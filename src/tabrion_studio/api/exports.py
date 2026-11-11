"""Export route (03 section 6 ``POST /exports``, STU-021). It reads; nothing here changes a database.

``POST /api/exports`` with ``{"connection_id", "format": "csv" | "json", "sql"?, "params"?, "table"?: {"schema",
"name"}, "max_rows"?, "escape_formulas"?}`` (``sql`` or ``table``, not both) answers ``200`` with the file as an
attachment, streamed (:class:`tabrion_studio.services.exports.Exports`). A refusal before the first byte is a problem:
``studio.export_*``, ``studio.statement_*``, ``studio.parameter_*``, ``studio.object_not_found``, ``db.<kind>`` and
the problems of the connection routes. An error after the first byte ends the download early (the log has it).

The route sits behind the session and the CSRF check of the access middleware, like every changing request; the page
downloads with ``fetch``. No route echoes the SQL text or a parameter value.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
import json
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from tabrion.errors import TabrionError

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.api.queries import MAX_BODY, MAX_SQL_CHARS
from tabrion_studio.problems import db_problem_response, is_problem_code, problem_response
from tabrion_studio.services.exports import Export, ExportError, Exports

logger = logging.getLogger("tabrion_studio.exports")


def _refusal(exc: BaseException) -> Response | None:
    code = getattr(exc, "code", None)
    if isinstance(exc, TabrionError):
        return db_problem_response(exc)
    if isinstance(code, str) and code.startswith("studio.") and is_problem_code(code):
        return problem_response(code)
    return connection_problem(exc)


def _next(chunks: Iterator[bytes]) -> bytes | None:
    return next(chunks, None)


def add_export_routes(app: FastAPI, exports: Exports) -> None:
    @app.post("/api/exports")
    async def post_export(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return problem_response("studio.query_too_large")
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError):
            doc = None
        if not isinstance(doc, dict):
            return problem_response("access.bad_request")
        sql = doc.get("sql")
        if isinstance(sql, str) and len(sql) > MAX_SQL_CHARS:
            return problem_response("studio.query_too_large")
        params = doc.get("params")
        if params is not None and not isinstance(params, dict):
            return problem_response("studio.export_invalid")
        export: Export | None = None
        try:
            export = await asyncio.to_thread(
                exports.start,
                doc.get("connection_id"),
                doc.get("format"),
                sql=sql,
                params=params,
                table=doc.get("table"),
                max_rows=doc.get("max_rows"),
                escape_formulas=doc.get("escape_formulas", True),
            )
            chunks = export.chunks()
            first = await asyncio.to_thread(_next, chunks)
        except Exception as exc:
            if export is not None:
                export.close()
            answer = _refusal(exc)
            if answer is None:
                raise
            if isinstance(exc, ExportError):
                logger.info("export refused", extra={"event": "studio.export_refused", "code": exc.code})
            return answer

        running = export

        async def body() -> AsyncIterator[bytes]:
            try:
                if first is not None:
                    yield first
                while (chunk := await asyncio.to_thread(_next, chunks)) is not None:
                    yield chunk
            finally:
                running.close()  # the client went away, or the export ended: stop the producer either way

        return StreamingResponse(
            body(),
            media_type=export.media_type,
            headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
        )
