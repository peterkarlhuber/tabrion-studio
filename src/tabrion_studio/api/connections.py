"""Connection routes (03 section 6, STU-009). Every connection is read-only; Studio 1.0 never writes to a database.

* ``POST /api/connections`` with ``{"profile_id": "user:<name>" | "project:<name>", "confirmed_environment":
  "dev" | "test" | "prod"}``: 201 ``{connection_id,
  profile_id, engine, environment, read_only: true, url, opened_at}``. ``url`` comes from ``tabrion.safe_url`` (no
  password). The connection is checked before the answer, so a wrong password or an unreachable host is a
  ``db.<kind>`` problem here. An open connection of the same profile is returned again.
* ``GET /api/connections``: the open connections (each checked against its profile; a stale one is closed).
* ``GET /api/connections/{connection_id}/health`` (TD-034): whether the database still answers on the connection,
  ``{state: "open", latency_ms}`` or ``{state: "busy"}`` (every pooled session in use); ``db.<kind>`` when it does not,
  ``studio.connection_not_found`` or ``studio.connection_stale`` when the connection is gone. A read of ``SELECT 1``
  kind only (tabrion's health check); the page's "Connection lost" banner polls it for the header's connection.
  ``POST /api/connections`` does not return an open connection whose database no longer answers: it opens a new one.
* ``DELETE /api/connections`` with ``{"connection_id"}``: 204 (the id is in the body, like the profile routes).
* ``POST /api/connection-tests`` with ``{"profile_id", "confirmed_environment"}``: the connection test (TD-011):
  ``{ok, engine, environment, server_version, latency_ms, read_only, facts}``; the connection is closed again.
* D-02 on the server (TD-038): ``confirmed_environment`` is the environment the page confirmed for. A profile that is
  ``prod`` now opens (or is tested, or an open connection of it is returned) only with ``"prod"``; otherwise 409
  ``studio.prod_confirmation_required``, and the page reads the profile again, asks D-02 and sends the request again.
  A missing or null value means "not confirmed"; another value is ``access.bad_request``.

Refusals are problems with fixed texts: ``studio.secret_*`` (no password), ``studio.project_untrusted`` and
``studio.database_outside`` (review T-1), ``studio.database_*``, ``studio.connection_*`` and ``db.<kind>`` built from
``tabrion.errors.classify`` (never the server's message). Opening and closing run in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from tabrion.errors import TabrionError

from tabrion_studio.config.project import TrustError
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.problems import db_problem_response, is_problem_code, problem_response
from tabrion_studio.services.connections import (
    ENVIRONMENTS,
    ConnectionRegistry,
    HealthError,
    RegistryError,
    pool_busy,
)
from tabrion_studio.services.readonly import StatementRefused

logger = logging.getLogger("tabrion_studio.connections")
_MAX_BODY = 4096


def _body(raw: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(raw) if len(raw) <= _MAX_BODY else None
    except (ValueError, RecursionError):
        doc = None
    return doc if isinstance(doc, dict) else None


def _confirmation_ok(doc: dict[str, Any]) -> bool:
    """``confirmed_environment`` is absent, null or one of the environments (anything else is a bad request)."""
    value = doc.get("confirmed_environment")
    return value is None or (isinstance(value, str) and value in ENVIRONMENTS)


def connection_problem(exc: BaseException) -> Response | None:
    """The problem answer for an error of opening or using a connection; None for an unexpected one."""
    if isinstance(exc, SecretError | TrustError | RegistryError | StatementRefused):
        code = exc.code
        if is_problem_code(code):
            logger.info("connection refused", extra={"event": "studio.connection_refused", "code": code})
            return problem_response(code)
        return None
    if isinstance(exc, HealthError):
        logger.info(
            "connection failed", extra={"event": "studio.connection_failed", "error_kind": str(exc.classification.kind)}
        )
        return db_problem_response(exc.classification)
    if pool_busy(exc):
        # L-2 of stu-010-review.md: Studio's own pool had no free session within its timeout; the database is fine.
        logger.info("connection busy", extra={"event": "studio.connection_busy"})
        return problem_response("studio.connection_busy")
    if isinstance(exc, TabrionError):
        # DbErrorRedactionFilter (logs.py) rewrites the exception to tabrion's safe message in the log record.
        logger.info("database error", exc_info=exc, extra={"event": "studio.db_error", "code": exc.code})
        if exc.classification is not None:
            return db_problem_response(exc)
        return problem_response("studio.connection_config")
    return None


def add_connection_routes(
    app: FastAPI, registry: ConnectionRegistry, *, opened: Callable[[str], None] | None = None
) -> None:
    """``opened(profile_id)`` is told of every connection ``POST /api/connections`` answers (the recently opened
    profiles, STU-020); it must not raise."""

    async def run(call: Any, *args: Any) -> Any:
        return await asyncio.to_thread(call, *args)

    async def guarded(call: Any, *args: Any) -> Any:
        try:
            return await run(call, *args)
        except Exception as exc:
            answer = connection_problem(exc)
            if answer is None:
                raise
            return answer

    @app.post("/api/connections", status_code=201)
    async def post_connection(request: Request) -> Response:
        doc = _body(await request.body())
        if doc is None or not isinstance(doc.get("profile_id"), str) or not _confirmation_ok(doc):
            return problem_response("access.bad_request")
        result = await guarded(registry.open, doc["profile_id"], doc.get("confirmed_environment"))
        if isinstance(result, Response):
            return result
        if opened is not None:
            await run(opened, result.profile_id)
        return JSONResponse(result.to_dict(), status_code=201)

    @app.get("/api/connections")
    async def get_connections() -> Response:
        connections = await run(registry.list)
        return JSONResponse({"connections": [c.to_dict() for c in connections]})

    @app.get("/api/connections/{connection_id}/health")
    async def get_connection_health(connection_id: str) -> Response:
        result = await guarded(registry.check, connection_id)
        if isinstance(result, Response):
            return result
        return JSONResponse(result)

    @app.delete("/api/connections", status_code=204)
    async def delete_connection(request: Request) -> Response:
        doc = _body(await request.body())
        if doc is None or not isinstance(doc.get("connection_id"), str):
            return problem_response("access.bad_request")
        if not await run(registry.close, doc["connection_id"]):
            return problem_response("studio.connection_not_found")
        return Response(status_code=204)

    @app.post("/api/connection-tests")
    async def post_connection_test(request: Request) -> Response:
        doc = _body(await request.body())
        if doc is None or not isinstance(doc.get("profile_id"), str) or not _confirmation_ok(doc):
            return problem_response("access.bad_request")
        result = await guarded(registry.test, doc["profile_id"], doc.get("confirmed_environment"))
        if isinstance(result, Response):
            return result
        return JSONResponse(result)
