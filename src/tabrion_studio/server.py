"""Serving one project folder (03 section 4.1, STU-002).

:func:`serve` takes the per-project lock, binds ``127.0.0.1`` (the port given or a free one chosen by the OS; there
is no option for another address, STU-T-025), runs Uvicorn with one worker in this process and publishes the
instance info file once the server accepts connections. It returns when the server stops: Ctrl+C (SIGINT), SIGTERM,
CTRL_BREAK on Windows, ``tabrion-studio --stop`` or the idle timeout. On the way out it removes its info file and
releases the lock.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import signal
import socket
import sys
import threading
import time
from types import FrameType
from typing import TextIO

import uvicorn

from tabrion_studio._version import __version__
from tabrion_studio.app import Runtime, create_app
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.instance import (
    LOOPBACK,
    InstanceInfo,
    InstanceLock,
    InstancePaths,
    remove_info_if_ours,
    write_info,
)


class PortUnavailableError(RuntimeError):
    """The requested port cannot be bound on the loopback address."""


@dataclass
class ServeOptions:
    project: Path
    state_dir: Path
    port: int = 0
    idle_timeout_seconds: float = 30 * 60
    open_browser: Callable[[str], None] | None = None
    started: float = field(default_factory=time.perf_counter)


STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)
if sys.platform == "win32":
    STOP_SIGNALS += (signal.SIGBREAK,)


class _StudioServer(uvicorn.Server):
    """Uvicorn's server with a callback once it accepts connections and a clean exit on a stop signal."""

    def __init__(self, config: uvicorn.Config, on_ready: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_ready = on_ready
        self.stop_reason = "stopped"

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            self._on_ready()

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        # Unlike Uvicorn's own version, this does not raise the signal again after the graceful shutdown: a stop
        # signal is the normal way to end Studio, so the process cleans up and exits with code 0.
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        original = {sig: signal.signal(sig, self._on_signal) for sig in STOP_SIGNALS}
        try:
            yield
        finally:
            for sig, handler in original.items():
                signal.signal(sig, handler)

    def _on_signal(self, sig: int, frame: FrameType | None) -> None:
        if not self.should_exit:
            self.stop_reason = signal.Signals(sig).name
        self.handle_exit(sig, frame)


def bind_loopback(port: int) -> socket.socket:
    """A listening TCP socket on 127.0.0.1 (``port`` 0 = a free port chosen by the OS)."""
    try:
        sock = socket.create_server((LOOPBACK, port), family=socket.AF_INET, backlog=128)
    except OSError as exc:
        raise PortUnavailableError(f"cannot listen on {LOOPBACK}:{port}: {exc.strerror or exc}") from exc
    sock.set_inheritable(False)
    return sock


def serve(options: ServeOptions, lock: InstanceLock, out: TextIO) -> int:
    """Run the server for ``options.project`` while holding ``lock``; returns the exit code."""
    paths = InstancePaths.for_project(options.state_dir, options.project)
    sock = bind_loopback(options.port)
    port = int(sock.getsockname()[1])
    info = InstanceInfo(
        pid=os.getpid(),
        port=port,
        project=str(options.project),
        instance_id=secrets.token_hex(8),
        control_token=secrets.token_urlsafe(32),
        version=__version__,
        started_at=time.time(),
    )
    server: _StudioServer | None = None

    def request_shutdown(reason: str) -> None:
        if server is not None:
            server.stop_reason = reason
            server.should_exit = True

    runtime = Runtime(
        instance_id=info.instance_id,
        control_token=info.control_token,
        idle=IdleMonitor(options.idle_timeout_seconds),
        # STU-003 adds the one-time launch token in the URL fragment.
        launch_url=lambda: info.url,
        request_shutdown=request_shutdown,
    )

    def on_ready() -> None:
        write_info(paths, info)
        elapsed = time.perf_counter() - options.started
        print(f"Tabrion Studio {__version__} is running at {info.url} (ready in {elapsed:.2f} s)", file=out)
        print(f"Project: {options.project}", file=out)
        print("Press Ctrl+C to stop.", file=out, flush=True)
        if options.open_browser is not None:
            threading.Thread(target=options.open_browser, args=(info.url,), daemon=True).start()

    config = uvicorn.Config(
        create_app(runtime),
        log_level="warning",
        # 03 section 4.5: no access log (paths carry IDs); a request log without query strings comes with STU-004.
        access_log=False,
        # Forwarded headers are never trusted (T-01).
        proxy_headers=False,
        server_header=False,
        lifespan="on",
        http="h11",
        ws="none",
        loop="asyncio",
        workers=1,
        timeout_graceful_shutdown=5,
    )
    server = _StudioServer(config, on_ready)
    try:
        server.run(sockets=[sock])
    finally:
        remove_info_if_ours(paths, info.instance_id)
        with contextlib.suppress(OSError):
            sock.close()
        lock.release()
    if not server.started:
        print("Tabrion Studio could not start.", file=out, flush=True)
        return 1
    print(f"Tabrion Studio stopped ({server.stop_reason}).", file=out, flush=True)
    return 0
