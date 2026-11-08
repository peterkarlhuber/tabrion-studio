"""The FastAPI application factory (05 section 3.1).

Routes: ``GET /api/health``, ``POST /api/session`` (launch token -> session cookie), ``GET /api/heartbeat``, the
control endpoints of the process model, ``GET /launch``, the frontend's own view paths (:data:`CLIENT_ROUTES`) and the
built frontend from ``static/``.

Every request first passes :class:`tabrion_studio.security.access.AccessMiddleware` (STU-003): the Host allowlist,
the Origin and fetch-metadata rules, JSON-only bodies, the session cookie plus CSRF header on every API route except
the health check, the launch exchange and the control endpoints, and the security headers (CSP, ``no-store``) on
every answer.

Only two things count as activity for the idle timeout (review F-06): a control request with a valid token and any
request with a valid session (the open tab's heartbeat among them). Health checks, static files, failed requests and
requests from other sites never postpone the idle exit.

Every error answer is a problem document (STU-004, :mod:`tabrion_studio.problems`): the refusals of the access
middleware, the framework's own errors (404, 405, a request that fails validation; never with the request's values)
and unexpected exceptions (``studio.internal_error`` with a log ID, :mod:`tabrion_studio.request_log`). The request
log middleware is the outermost layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
from dataclasses import dataclass, field
import hmac
import json
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from tabrion_studio._version import __version__
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.instance import NONCE_PATTERN, control_proof
from tabrion_studio.logs import register_secret
from tabrion_studio.problems import code_for_status, problem_response
from tabrion_studio.request_log import RequestLogMiddleware
from tabrion_studio.security.access import AccessMiddleware
from tabrion_studio.security.launch import LAUNCH_PATH, LaunchTokens
from tabrion_studio.security.sessions import Sessions, cookie_name

STATIC_DIR = Path(__file__).resolve().parent / "static"
#: Paths of the single-page app's own views (frontend/src/router.ts); a reload or a typed address on one of them gets
#: index.html. Every other unknown path stays a 404.
CLIENT_ROUTES = ("/connections", "/settings")
#: How often expired launch tokens and their redirect files are removed.
SWEEP_SECONDS = 5.0
_MAX_BODY = 4096

_NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Tabrion Studio</title></head>
<body><h1>Tabrion Studio</h1>
<p>The frontend is not built. Run <code>python scripts/build_frontend.py</code> in the repository.</p>
</body></html>
"""


@dataclass
class Runtime:
    """What the app needs from the process that serves it."""

    instance_id: str
    control_token: str
    idle: IdleMonitor
    port: int
    launches: LaunchTokens
    request_shutdown: Callable[[str], None]
    sessions: Sessions = field(default_factory=Sessions)


def _problem(code: str) -> Response:
    return problem_response(code)


async def _http_error(_request: Request, exc: Exception) -> Response:
    # A missing file, a wrong method, ...: the catalog code for the status, never the exception's own detail text.
    status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
    headers = getattr(exc, "headers", None) or {}
    allow = {key: value for key, value in headers.items() if key.lower() == "allow"}
    return problem_response(code_for_status(status), headers=allow or None)


async def _validation_error(_request: Request, _exc: Exception) -> Response:
    # FastAPI's default answer echoes the offending input values; this one names no value at all.
    return problem_response("studio.invalid_request")


async def _json_body(request: Request) -> dict[str, object] | None:
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        return None
    if not raw:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def create_app(runtime: Runtime, static_dir: Path = STATIC_DIR) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks = [asyncio.create_task(_sweep_launches(runtime))]
        if runtime.idle.enabled:
            tasks.append(asyncio.create_task(_watch_idle(runtime)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="Tabrion Studio",
        version=__version__,
        # No Swagger/ReDoc pages: they load scripts from a CDN (SQ-08).
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        exception_handlers={StarletteHTTPException: _http_error, RequestValidationError: _validation_error},
    )
    register_secret(runtime.control_token)  # never in a log record (STU-004)
    app.add_middleware(AccessMiddleware, port=runtime.port, sessions=runtime.sessions, on_activity=runtime.idle.touch)
    # Added last, so it is the outermost middleware: it sees every refusal and catches every unexpected error.
    app.add_middleware(RequestLogMiddleware)

    @app.get("/api/health")
    async def get_health() -> Response:
        return JSONResponse({"status": "ok", "version": __version__, "instance": runtime.instance_id})

    @app.post("/api/session", include_in_schema=False)
    async def post_session(request: Request) -> Response:
        # The launch page sends the one-time token from its URL fragment (03 section 4.1, T-03).
        doc = await _json_body(request)
        if doc is None:
            return _problem("access.bad_request")
        result = runtime.launches.redeem(doc.get("token"))
        if result != "ok":
            return _problem(f"access.launch_{result}")
        session_id, csrf = runtime.sessions.create()
        runtime.idle.touch()
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            cookie_name(runtime.port), session_id, path="/", httponly=True, samesite="strict", secure=False
        )
        return response

    @app.get("/api/heartbeat", include_in_schema=False)
    async def get_heartbeat() -> Response:
        # Reached only with a valid session and CSRF header; the middleware counts it as activity (F-06, R-5).
        return Response(status_code=204)

    @app.get("/api/control/proof", include_in_schema=False)
    async def get_control_proof(nonce: str = "") -> Response:
        # Proves to a second `tabrion-studio` start that this server knows the control token, before that client
        # sends the token (review F-01). The answer depends on the client's random nonce, so it cannot be replayed.
        if NONCE_PATTERN.fullmatch(nonce) is None:
            return _problem("studio.bad_nonce")
        return JSONResponse({"proof": control_proof(runtime.control_token, nonce)})

    @app.post("/api/control/{action}", include_in_schema=False)
    async def post_control(action: str, request: Request) -> Response:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        authorized = scheme.lower() == "bearer" and hmac.compare_digest(
            token.encode("utf-8"), runtime.control_token.encode("utf-8")
        )
        if not authorized:
            return _problem("access.control_denied")
        runtime.idle.touch()
        if action == "launch":
            doc = await _json_body(request)
            if doc is None:
                return _problem("access.bad_request")
            launch = runtime.launches.issue(redirect=doc.get("redirect") is True)
            redirect = None if launch.redirect is None else str(launch.redirect)
            return JSONResponse({"url": launch.url, "redirect": redirect})
        if action == "shutdown":
            runtime.request_shutdown("stop requested")
            return JSONResponse({"status": "stopping"}, status_code=202)
        return _problem("studio.not_found")

    index = static_dir / "index.html"
    if index.is_file():

        @app.get(LAUNCH_PATH, include_in_schema=False)
        async def launch_page() -> Response:
            # The single-page app reads the token from the fragment and exchanges it (frontend/src/session.ts).
            return FileResponse(index, media_type="text/html")

        async def client_route() -> Response:
            return FileResponse(index, media_type="text/html")

        # GET and HEAD, like the static files (review U-4: link checkers use HEAD).
        for route in CLIENT_ROUTES:
            app.add_api_route(route, client_route, methods=["GET", "HEAD"], include_in_schema=False)

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:

        @app.get("/", include_in_schema=False)
        @app.get(LAUNCH_PATH, include_in_schema=False)
        async def not_built() -> Response:
            return HTMLResponse(_NOT_BUILT)

    return app


async def _sweep_launches(runtime: Runtime) -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        runtime.launches.sweep()


async def _watch_idle(runtime: Runtime) -> None:
    while True:
        await asyncio.sleep(runtime.idle.check_interval())
        if runtime.idle.expired():
            minutes = runtime.idle.timeout_seconds / 60
            runtime.request_shutdown(f"no activity for {minutes:g} min")
            return
