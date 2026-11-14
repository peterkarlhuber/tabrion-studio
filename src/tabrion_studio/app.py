"""The FastAPI application factory (05 section 3.1).

Routes: ``GET /api/health``, ``POST /api/session`` (launch token -> session cookie), ``GET /api/heartbeat``, the
control endpoints of the process model, ``GET /launch``, the frontend's own view paths (:data:`CLIENT_ROUTES`) and the
built frontend from ``static/``.

Every request first passes :class:`tabrion_studio.security.access.AccessMiddleware` (STU-003): the Host allowlist,
the Origin and fetch-metadata rules, JSON-only bodies, the session cookie plus CSRF header on every API route except
the health check, the launch exchange and the control endpoints (which need the control token instead), and the
security headers (CSP, ``no-store``) on every answer. Then :class:`tabrion_studio.security.body.BodyLimitMiddleware`
caps the request body while it streams in (:data:`BODY_LIMITS`, STU-016 review F-4).

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
import logging
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from tabrion_studio._version import __version__
from tabrion_studio.api.config import MAX_FORM_BODY, Configuration, add_config_routes
from tabrion_studio.api.connections import add_connection_routes
from tabrion_studio.api.diagnostics import add_diagnostics_routes
from tabrion_studio.api.drafts import MAX_BODY as MAX_DRAFTS_BODY
from tabrion_studio.api.drafts import add_draft_routes
from tabrion_studio.api.explorer import add_explorer_routes
from tabrion_studio.api.exports import add_export_routes
from tabrion_studio.api.history import add_history_routes
from tabrion_studio.api.migrations import add_migration_routes
from tabrion_studio.api.queries import MAX_BODY as MAX_QUERY_BODY
from tabrion_studio.api.queries import add_query_routes
from tabrion_studio.api.scripts import MAX_BODY as MAX_SCRIPT_BODY
from tabrion_studio.api.scripts import add_script_routes
from tabrion_studio.api.viewer import add_viewer_routes
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.instance import NONCE_PATTERN, control_proof
from tabrion_studio.logs import register_secret
from tabrion_studio.problems import code_for_status, problem_response
from tabrion_studio.request_log import RequestLogMiddleware
from tabrion_studio.security.access import AccessMiddleware
from tabrion_studio.security.body import BodyLimit, BodyLimitMiddleware
from tabrion_studio.security.launch import LAUNCH_PATH, LaunchTokens
from tabrion_studio.security.sessions import Sessions, cookie_name
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import Diagnostics
from tabrion_studio.services.drafts import Drafts
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.exports import Exports
from tabrion_studio.services.favorites import Favorites
from tabrion_studio.services.history import History
from tabrion_studio.services.migrations import Migrations
from tabrion_studio.services.queries import QueryEngine
from tabrion_studio.services.scripts import Scripts
from tabrion_studio.services.state import StateStore
from tabrion_studio.services.viewer import Viewer

_logger = logging.getLogger("tabrion_studio.app")

STATIC_DIR = Path(__file__).resolve().parent / "static"
#: Paths of the single-page app's own views (frontend/src/router.ts); a reload or a typed address on one of them gets
#: index.html. Every other unknown path stays a 404.
CLIENT_ROUTES = (
    "/connections",
    "/explorer",
    "/sql",
    "/history",
    "/migrations",
    "/diagnostics",
    "/vectors",
    "/settings",
)
#: How often expired launch tokens and their redirect files are removed.
SWEEP_SECONDS = 5.0
_MAX_BODY = 4096
#: The body cap of every request (STU-016 review F-4): enforced while the body streams in, before a route reads it
#: (:mod:`tabrion_studio.security.body`). The default is the largest cap of a JSON route's own check; each route keeps
#: its own, often smaller, check. The SQL editor and the export take a statement of up to 512 K characters, and a
#: script save (STU-020) a `.sql` file of up to 1 MB as JSON, and the editor's drafts (TD-033) 2 M characters of text.
DEFAULT_BODY_LIMIT = BodyLimit(MAX_FORM_BODY)
BODY_LIMITS: dict[str, BodyLimit] = {
    "/api/session": BodyLimit(_MAX_BODY),  # reached before a session exists
    "/api/control/launch": BodyLimit(_MAX_BODY),
    "/api/control/shutdown": BodyLimit(_MAX_BODY),
    "/api/queries": BodyLimit(MAX_QUERY_BODY, "studio.query_too_large"),
    "/api/exports": BodyLimit(MAX_QUERY_BODY, "studio.query_too_large"),
    "/api/scripts/file": BodyLimit(MAX_SCRIPT_BODY, "studio.script_too_large"),
    "/api/drafts": BodyLimit(MAX_DRAFTS_BODY, "studio.drafts_too_large"),
}

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
    #: Profiles, settings and project trust (STU-006); None serves no configuration routes.
    config: Configuration | None = None
    #: The open, read-only database connections (STU-009); None serves no connection routes.
    connections: ConnectionRegistry | None = None
    #: The query engine on those connections (STU-011); None serves no query routes.
    queries: QueryEngine | None = None
    #: Catalog and table detail on those connections (STU-010); None serves no explorer routes.
    explorer: Explorer | None = None
    #: The data viewer on the explorer's objects (STU-013); None serves no viewer routes.
    viewer: Viewer | None = None
    #: Diagnostics of those connections (STU-014); None serves no diagnostics route.
    diagnostics: Diagnostics | None = None
    #: Migration status and the project's code trust (STU-015); None serves neither.
    migrations: Migrations | None = None
    #: Exports of query results and tables (STU-021); None serves no export route.
    exports: Exports | None = None
    #: The state database (STU-020), closed on shutdown; the history and the favorites live in it.
    state: StateStore | None = None
    #: The query history and the favorite and recent profiles (STU-020); None serves neither.
    history: History | None = None
    favorites: Favorites | None = None
    #: The project's ``.sql`` scripts (STU-020); None serves no script route.
    scripts: Scripts | None = None
    #: The SQL editor's drafts in the state database (TD-033, review F-5); None serves no draft route.
    drafts: Drafts | None = None


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
        # Starlette reports a lifespan failure to Uvicorn as formatted traceback *text*, which the log redaction
        # cuts (review L-1 of stu-004-review.md); so the exception itself is logged here, with exc_info (frames
        # only), before it propagates.
        phase = "startup"
        try:
            tasks = [asyncio.create_task(_sweep_launches(runtime))]
            if runtime.idle.enabled:
                tasks.append(asyncio.create_task(_watch_idle(runtime)))
            try:
                phase = "running"
                yield
            finally:
                phase = "shutdown"
                for task in tasks:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if runtime.queries is not None:
                    # Running queries are canceled first (STU-011).
                    await asyncio.to_thread(runtime.queries.close_all)
                if runtime.connections is not None:
                    # Engines are disposed on shutdown (STU-009).
                    await asyncio.to_thread(runtime.connections.close_all)
                if runtime.state is not None:
                    await asyncio.to_thread(runtime.state.close)
        except Exception:
            _logger.exception("application %s failed", phase, extra={"event": "studio.lifespan_failed"})
            raise

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
    # Inside the access middleware: a request it refuses is never read (STU-016 review F-4).
    app.add_middleware(BodyLimitMiddleware, default=DEFAULT_BODY_LIMIT, routes=BODY_LIMITS)
    app.add_middleware(
        AccessMiddleware,
        port=runtime.port,
        sessions=runtime.sessions,
        control_token=runtime.control_token,
        on_activity=runtime.idle.touch,
    )
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

    if runtime.config is not None:
        # A new retention period applies to the history and the editor drafts at once (TD-033).
        expire = [store.purge for store in (runtime.history, runtime.drafts) if store is not None]

        def settings_saved() -> None:
            for purge in expire:
                purge()

        add_config_routes(app, runtime.config, settings_saved=settings_saved if expire else None)
    if runtime.connections is not None:
        opened = None if runtime.favorites is None else runtime.favorites.opened
        add_connection_routes(app, runtime.connections, opened=opened)
    if runtime.queries is not None:
        add_query_routes(app, runtime.queries)
    if runtime.explorer is not None:
        add_explorer_routes(app, runtime.explorer)
    if runtime.viewer is not None:
        add_viewer_routes(app, runtime.viewer)
    if runtime.diagnostics is not None:
        add_diagnostics_routes(app, runtime.diagnostics)
    if runtime.migrations is not None:
        add_migration_routes(app, runtime.migrations)
    if runtime.exports is not None:
        add_export_routes(app, runtime.exports)
    if runtime.history is not None and runtime.favorites is not None:
        add_history_routes(app, runtime.history, runtime.favorites)
    if runtime.scripts is not None:
        add_script_routes(app, runtime.scripts)
    if runtime.drafts is not None:
        add_draft_routes(app, runtime.drafts)

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
