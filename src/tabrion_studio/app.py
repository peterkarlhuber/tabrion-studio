"""The FastAPI application factory (05 section 3.1).

Skeleton scope (STU-001, STU-002): ``GET /api/health``, the control endpoint for the process model and the built
frontend from ``static/``. Session, Host/Origin checks, CSP and ``no-store`` headers arrive with STU-003; problem JSON
and logging with STU-004.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from dataclasses import dataclass
import hmac
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tabrion_studio._version import __version__
from tabrion_studio.idle import IdleMonitor

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

    @app.middleware("http")
    async def count_activity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        runtime.idle.touch()
        return await call_next(request)

    @app.get("/api/health")
    async def get_health() -> Response:
        return _no_store(JSONResponse({"status": "ok", "version": __version__, "instance": runtime.instance_id}))

    @app.post("/api/control/{action}", include_in_schema=False)
    async def post_control(action: str, request: Request) -> Response:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        authorized = scheme.lower() == "bearer" and hmac.compare_digest(
            token.encode("utf-8"), runtime.control_token.encode("utf-8")
        )
        if not authorized:
            return _no_store(JSONResponse({"code": "access.control_denied"}, status_code=401))
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
