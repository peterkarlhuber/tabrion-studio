"""The request log and the last line of defense for unexpected errors (03 section 4.5, STU-004).

:class:`RequestLogMiddleware` is the outermost Studio middleware. For every HTTP request it logs one record
(``http.request``) with the method, the **route** (the route's template such as ``/api/control/{action}``, ``static``
for files of the built frontend, ``<unmatched>`` when no route matched or the access middleware refused the request
before routing: the raw path is never logged, since it is chosen by the client), the status, the duration
and, for a problem answer, its code. It never logs the query string, a header, a cookie or a body. API requests are
logged at INFO (health checks and heartbeats at DEBUG), everything else at DEBUG; refusals at INFO.

An exception that escapes a route or a middleware is logged once at ERROR with a new log ID and the redacted
traceback (:mod:`tabrion_studio.logs`), and the client gets ``studio.internal_error`` with that log ID and the
security headers, never the exception text or a stack trace. The exception is not raised further, so the server
does not log it a second time without redaction.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tabrion_studio.logs import printable
from tabrion_studio.problems import PROBLEM_MEDIA_TYPE, is_problem_code, new_log_id, problem, send_problem
from tabrion_studio.security.access import with_security_headers

logger = logging.getLogger("tabrion_studio.request")
#: Routes polled by the page and the CLI; logged at DEBUG so that they do not fill the log.
QUIET_ROUTES = frozenset({"/api/health", "/api/heartbeat"})
_MAX_PROBLEM_BODY = 4096
UNMATCHED = "<unmatched>"


def route_label(scope: Scope) -> str:
    """The route template of the request, ``static`` or ``<unmatched>``; never the path the client sent."""
    route = scope.get("route")
    if isinstance(route, Mount) or isinstance(scope.get("endpoint"), StaticFiles):
        return "static"
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        state: dict[str, Any] = {"status": None, "problem": False, "body": b""}

        async def watch(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
                headers = dict(message.get("headers", []))
                state["problem"] = headers.get(b"content-type", b"").startswith(PROBLEM_MEDIA_TYPE.encode())
            elif message["type"] == "http.response.body" and state["problem"]:
                state["body"] = (state["body"] + message.get("body", b""))[:_MAX_PROBLEM_BODY]
            await send(message)

        log_id: str | None = None
        try:
            await self.app(scope, receive, watch)
        except Exception:
            log_id = new_log_id()
            logger.error(
                "unexpected error",
                exc_info=True,
                extra={"event": "studio.internal_error", "log_id": log_id, "route": route_label(scope)},
            )
            if state["status"] is None:
                await send_problem(with_security_headers(send), problem("studio.internal_error", log_id=log_id))
                state["status"] = 500
                state["code"] = "studio.internal_error"
        self._log(scope, state, started, log_id)

    @staticmethod
    def _log(scope: Scope, state: dict[str, Any], started: float, log_id: str | None) -> None:
        route = route_label(scope)
        status = state["status"]
        code = state.get("code")
        if code is None and state["problem"]:
            try:
                parsed = json.loads(state["body"]).get("code")
            except (ValueError, AttributeError):
                parsed = None
            code = parsed if is_problem_code(parsed) else "unknown"
        api = str(scope.get("path", "")).startswith("/api/")
        level = logging.INFO if api and route not in QUIET_ROUTES else logging.DEBUG
        if status is not None and status >= 400:
            level = max(level, logging.INFO)
        extra: dict[str, Any] = {
            "event": "http.request",
            "method": printable(str(scope.get("method", "")), 16),
            "route": route,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if code is not None:
            extra["code"] = code
        if log_id is not None:
            extra["log_id"] = log_id
        logger.log(level, "%s %s %s", extra["method"], route, status, extra=extra)
