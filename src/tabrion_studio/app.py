"""The FastAPI application factory (05 section 3.1).

Skeleton scope (STU-001, STU-002): ``GET /api/health``, ``GET /api/heartbeat``, the control endpoints for the process
model and the built frontend from ``static/``.

Only two things count as activity for the idle timeout (review F-06): a control request with a valid token and a
heartbeat from a page of this origin (``Sec-Fetch-Site: same-origin``). Health checks, static files, failed control
requests and requests from other sites never postpone the idle exit.

Session, Host/Origin checks, CSP and ``no-store`` headers arrive with STU-003; problem JSON and logging with STU-004.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
from dataclasses import dataclass
import hmac
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tabrion_studio._version import __version__
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.instance import NONCE_PATTERN, control_proof

STATIC_DIR = Path(__file__).resolve().parent / "static"

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
    launch_url: Callable[[], str]
    request_shutdown: Callable[[str], None]


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def create_app(runtime: Runtime, static_dir: Path = STATIC_DIR) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        watcher = asyncio.create_task(_watch_idle(runtime)) if runtime.idle.enabled else None
        try:
            yield
        finally:
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

    app = FastAPI(
        title="Tabrion Studio",
        version=__version__,
        # No Swagger/ReDoc pages: they load scripts from a CDN (SQ-08).
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/api/health")
    async def get_health() -> Response:
        return _no_store(JSONResponse({"status": "ok", "version": __version__, "instance": runtime.instance_id}))

    @app.get("/api/heartbeat", include_in_schema=False)
    async def get_heartbeat(request: Request) -> Response:
        # A browser sets Sec-Fetch-Site itself and a page cannot forge it, so a page of another site cannot keep
        # Studio alive. Tools without the header do not count either.
        if request.headers.get("sec-fetch-site") == "same-origin":
            runtime.idle.touch()
        return _no_store(Response(status_code=204))

    @app.get("/api/control/proof", include_in_schema=False)
    async def get_control_proof(nonce: str = "") -> Response:
        # Proves to a second `tabrion-studio` start that this server knows the control token, before that client
        # sends the token (review F-01). The answer depends on the client's random nonce, so it cannot be replayed.
        if NONCE_PATTERN.fullmatch(nonce) is None:
            return _no_store(JSONResponse({"code": "studio.bad_nonce"}, status_code=400))
        return _no_store(JSONResponse({"proof": control_proof(runtime.control_token, nonce)}))

    @app.post("/api/control/{action}", include_in_schema=False)
    async def post_control(action: str, request: Request) -> Response:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        authorized = scheme.lower() == "bearer" and hmac.compare_digest(
            token.encode("utf-8"), runtime.control_token.encode("utf-8")
        )
        if not authorized:
            return _no_store(JSONResponse({"code": "access.control_denied"}, status_code=401))
        runtime.idle.touch()
        if action == "launch":
            return _no_store(JSONResponse({"url": runtime.launch_url()}))
        if action == "shutdown":
            runtime.request_shutdown("stop requested")
            return _no_store(JSONResponse({"status": "stopping"}, status_code=202))
        return _no_store(JSONResponse({"code": "studio.not_found"}, status_code=404))

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:

        @app.get("/", include_in_schema=False)
        async def not_built() -> Response:
            return HTMLResponse(_NOT_BUILT)

    return app


async def _watch_idle(runtime: Runtime) -> None:
    while True:
        await asyncio.sleep(runtime.idle.check_interval())
        if runtime.idle.expired():
            minutes = runtime.idle.timeout_seconds / 60
            runtime.request_shutdown(f"no activity for {minutes:g} min")
            return
