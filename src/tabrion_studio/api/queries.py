"""Query routes (03 section 6, STU-011). Every query is read-only; Studio 1.0 never writes to a database.

* ``POST /api/queries`` with ``{"connection_id", "sql", "params"?, "mode"?: "read", "max_rows"?}``: 202
  ``{query_id, state, ...}``. One statement; ``:name`` placeholders take their value from ``params`` (text, number,
  boolean or null). ``mode: "write"`` is refused (``studio.query_write_unsupported``; the Data Editor STU-026 stays
  locked). A refused statement or parameter set is answered here, before anything runs.
* ``GET /api/queries/{id}``: the state (``running``, ``done``, ``failed`` with ``problem``, ``canceled``), the
  columns (name and type), the row count and whether the result was cut (``truncated_by``: ``rows`` or ``bytes``).
* ``GET /api/queries/{id}/events``: the same state as server-sent events (``text/event-stream``): one ``state``
  event per change, a last event named after the final state, then the stream ends; a comment line every 15 s
  keeps it open. The access middleware needs the CSRF header on every API request, so the page reads the stream
  with ``fetch`` (``EventSource`` cannot send headers).
* ``GET /api/queries/{id}/rows?offset=&limit=``: ``{columns, rows, row_count, truncated, truncated_by,
  truncated_cells, offset, state}``; up to 5 000 rows per page, also while the query runs.
* ``POST /api/queries/{id}/cancel``: 202 with the state; the cancel reaches the database within a second.

Errors are problems with fixed texts: ``studio.statement_*``, ``studio.query_*``, ``studio.connection_*`` and
``db.<kind>`` (kind and SQLSTATE only). No route echoes the SQL text or a parameter value.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import logging
import time
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.problems import is_problem_code, problem_response
from tabrion_studio.services.queries import Query, QueryEngine, QueryError
from tabrion_studio.services.sqltext import ParameterError

logger = logging.getLogger("tabrion_studio.queries")
#: A statement of up to 512 KB plus its parameters.
MAX_BODY = 2 * 1024 * 1024
MAX_SQL_CHARS = 512 * 1024
EVENT_POLL_S = 0.05
KEEP_ALIVE_S = 15.0


def _refusal(exc: BaseException) -> Response | None:
    if isinstance(exc, QueryError | ParameterError):
        if is_problem_code(exc.code):
            logger.info("query refused", extra={"event": "studio.query_refused", "code": exc.code})
            return problem_response(exc.code)
        return None
    return connection_problem(exc)


def _event(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def add_query_routes(app: FastAPI, engine: QueryEngine) -> None:
    async def lookup(query_id: str) -> Query | Response:
        try:
            return engine.get(query_id)
        except QueryError as exc:
            return problem_response(exc.code)

    @app.post("/api/queries", status_code=202)
    async def post_query(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return problem_response("studio.query_too_large")
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError):
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("sql"), str):
            return problem_response("access.bad_request")
        if doc.get("mode", "read") != "read":
            return problem_response("studio.query_write_unsupported")
        if len(doc["sql"]) > MAX_SQL_CHARS:
            return problem_response("studio.query_too_large")
        try:
            query = await asyncio.to_thread(
                engine.start, doc.get("connection_id"), doc["sql"], doc.get("params"), doc.get("max_rows")
            )
        except Exception as exc:
            answer = _refusal(exc)
            if answer is None:
                raise
            return answer
        return JSONResponse(query.status(), status_code=202)

    @app.get("/api/queries/{query_id}")
    async def get_query(query_id: str) -> Response:
        query = await lookup(query_id)
        return query if isinstance(query, Response) else JSONResponse(query.status())

    @app.get("/api/queries/{query_id}/rows")
    async def get_rows(query_id: str, offset: int = 0, limit: int = 1_000) -> Response:
        try:
            return JSONResponse(engine.page(query_id, offset, limit))
        except QueryError as exc:
            return problem_response(exc.code)

    @app.post("/api/queries/{query_id}/cancel", status_code=202)
    async def post_cancel(query_id: str) -> Response:
        try:
            query = engine.cancel(query_id)
        except QueryError as exc:
            return problem_response(exc.code)
        return JSONResponse(query.status(), status_code=202)

    @app.get("/api/queries/{query_id}/events")
    async def get_events(query_id: str) -> Response:
        query = await lookup(query_id)
        if isinstance(query, Response):
            return query

        async def events() -> AsyncIterator[bytes]:
            seen = -1
            quiet_since = time.monotonic()
            while True:
                version, finished = query.version, query.finished
                if version != seen:
                    seen = version
                    status = query.status()
                    yield _event(status["state"] if finished else "state", status)
                    if finished:
                        return
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= KEEP_ALIVE_S:
                    yield b": keep-alive\n\n"
                    quiet_since = time.monotonic()
                await asyncio.sleep(EVENT_POLL_S)

        return StreamingResponse(events(), media_type="text/event-stream")
