"""Serving one project folder (03 section 4.1, STU-002).

:func:`serve` takes the per-project lock, binds a loopback address (the port given or a free one chosen by the OS;
there is no option for another address, STU-T-025), runs Uvicorn with one worker in this process and publishes the
instance info file once the server accepts connections. It returns when the server stops: Ctrl+C (SIGINT), SIGTERM,
SIGHUP (the terminal closed) on POSIX, CTRL_BREAK and CTRL_CLOSE on Windows, ``tabrion-studio --stop`` or the idle
timeout. The info file is removed as soon as the shutdown starts and again on every way out (review F-04).

It also binds, without listening, ``[::1]`` on the same port (review F-08): no other program can then serve
``http://localhost:<port>/`` over IPv6 while Studio runs. Studio itself never prints or opens a ``localhost`` URL,
and the Host allowlist (:mod:`tabrion_studio.security.access`) answers ``<address>:<port>`` of this instance only.

**A fresh origin for every start (finding A-01).** The IPv4 address is a random one of ``127.0.0.0/8``
(``127.a.b.c``), chosen anew for every server, so the origin ``http://127.a.b.c:<port>`` of one instance is not
predictable before it starts and is not reused by later instances. Whatever another program left in the browser for
an earlier origin (storage, a service worker) therefore never applies to a later Studio, also with a fixed
``--port``. Where only ``127.0.0.1`` is configured (macOS by default), binding a random address fails with
``EADDRNOTAVAIL`` and Studio uses ``127.0.0.1``; the residual risk of that case is in STU-ADR-0002.

The browser is opened with the ``file://`` path of a 0600 redirect file that holds the one-time launch URL, never
with the URL itself, so no process list shows the token (review F-02, :mod:`tabrion_studio.security.launch`).
``--print-url`` prints a launch URL instead. Redirect files are deleted when their token is used or expires, when
the server stops and, left behind by a crash, when the next server for the folder starts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass, field
import errno
import logging
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

# h11 is Uvicorn's own HTTP/1.1 parser (a required dependency of uvicorn, which Studio runs with http="h11").
import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from tabrion_studio._version import __version__
from tabrion_studio.api.config import Configuration
from tabrion_studio.app import Runtime, create_app
from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.user import UserConfig
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.instance import (
    LOOPBACK,
    InstanceInfo,
    InstanceLock,
    InstancePaths,
    project_key,
    remove_info_if_ours,
    write_info,
)
from tabrion_studio.logs import configure_logging
from tabrion_studio.paths import UnsafePathError, default_config_dir, remove_file
from tabrion_studio.security.access import loopback_origin
from tabrion_studio.security.launch import LaunchTokens, redirect_file_pattern
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


class PortUnavailableError(RuntimeError):
    """The requested port cannot be bound on the loopback address."""


@dataclass
class ServeOptions:
    project: Path
    state_dir: Path
    port: int = 0
    idle_timeout_seconds: float = 30 * 60
    open_browser: Callable[[str], None] | None = None
    print_url: bool = False
    #: Where the rotating log file goes (03 section 4.5); None logs to the console only.
    log_dir: Path | None = None
    #: The user's configuration folder (settings.toml, profiles.toml); None uses the default (STU-006).
    config_dir: Path | None = None
    started: float = field(default_factory=time.perf_counter)


STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)
if sys.platform == "win32":
    # The C runtime delivers CTRL_BREAK and CTRL_CLOSE (console window closed) as SIGBREAK.
    STOP_SIGNALS += (signal.SIGBREAK,)
else:
    STOP_SIGNALS += (signal.SIGHUP,)

LOOPBACK_V6 = "::1"
_PORT_ATTEMPTS = 20
#: How many random ``127.a.b.c`` addresses are tried when one is taken with a fixed port (A-01).
_ADDRESS_ATTEMPTS = 8
#: "Cannot assign requested address": the address is not configured on this host (macOS: only 127.0.0.1).
_NOT_CONFIGURED_ERRNOS = frozenset({errno.EADDRNOTAVAIL, 10049})  # 10049 = WSAEADDRNOTAVAIL


def random_loopback() -> str:
    """A random IPv4 loopback address ``127.a.b.c`` other than ``127.0.0.1``, with ``c`` in 1..254 (A-01)."""
    while True:
        a, b, c = secrets.randbelow(256), secrets.randbelow(256), 1 + secrets.randbelow(254)
        address = f"127.{a}.{b}.{c}"
        if address != LOOPBACK:
            return address


class _StudioServer(uvicorn.Server):
    """Uvicorn's server with a callback once it accepts connections and a clean exit on a stop signal."""

    def __init__(self, config: uvicorn.Config, on_ready: Callable[[], None], on_stopping: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_ready = on_ready
        self._on_stopping = on_stopping
        self.stop_reason = "stopped"

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            self._on_ready()

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # First withdraw the info file, so that no client reads a port that is about to be free (review F-01).
        self._on_stopping()
        await super().shutdown(sockets=sockets)

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


#: How long a connection may take to send one whole request (head and body), counted from when the server waits for
#: one: after the connection opened, or after the previous response (A-02).
REQUEST_READ_TIMEOUT_S = 10.0


class RequestTimeoutProtocol(H11Protocol):
    """Uvicorn's h11 protocol that closes a connection which does not send a complete request in time (A-02).

    Uvicorn itself waits without limit for the first request on a new connection, and its keep-alive timer stops at
    the first byte of the next one, so a connection that sends nothing, or a request a byte at a time, stays open for
    good; any local program could hold all of the server's connections that way. Here, whenever the server waits for
    a request (the peer's h11 state is ``IDLE`` or ``SEND_BODY``), :data:`REQUEST_READ_TIMEOUT_S` runs from that
    moment, and data that arrives does not restart it. The timer stops once the request is complete, so a long query
    is never cut. Uvicorn's shorter keep-alive timeout for an idle connection after a response stays as it is.
    """

    _request_timer: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._watch_request()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self._watch_request()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        self._watch_request()

    def connection_lost(self, exc: Exception | None) -> None:
        self._stop_request_timer()
        super().connection_lost(exc)

    def _watch_request(self) -> None:
        if self.transport.is_closing() or self.conn.their_state not in (h11.IDLE, h11.SEND_BODY):
            self._stop_request_timer()
        elif self._request_timer is None:
            self._request_timer = self.loop.call_later(REQUEST_READ_TIMEOUT_S, self._request_timed_out)

    def _stop_request_timer(self) -> None:
        if self._request_timer is not None:
            self._request_timer.cancel()
            self._request_timer = None

    def _request_timed_out(self) -> None:
        self._request_timer = None
        if not self.transport.is_closing():
            self.transport.close()


@dataclass
class LoopbackSockets:
    """The listening IPv4 socket and, if IPv6 is available, the bound but not listening ``[::1]`` socket."""

    listener: socket.socket
    v6_guard: socket.socket | None

    @property
    def host(self) -> str:
        return str(self.listener.getsockname()[0])

    @property
    def port(self) -> int:
        return int(self.listener.getsockname()[1])

    def close(self) -> None:
        for sock in (self.listener, self.v6_guard):
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()


def _exclusive_socket(family: socket.AddressFamily) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.set_inheritable(False)
    if sys.platform == "win32":
        # Without it, another program could bind the same address with SO_REUSEADDR and take over connections.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    return sock


def _bind_v4(host: str, port: int) -> socket.socket:
    sock = _exclusive_socket(socket.AF_INET)
    try:
        if sys.platform != "win32":
            # POSIX only: lets a restart reuse a port in TIME_WAIT; never lets two listeners share it.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
    except BaseException:
        sock.close()
        raise
    return sock


_NO_IPV6 = {errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, getattr(errno, "EPROTONOSUPPORT", errno.EAFNOSUPPORT)}


def _guard_v6(port: int) -> socket.socket | None:
    """Bind ``[::1]:port`` without listening; None if this host has no IPv6 loopback. Raises if the port is taken."""
    if not socket.has_ipv6:
        return None
    try:
        sock = _exclusive_socket(socket.AF_INET6)
    except OSError as exc:
        if exc.errno in _NO_IPV6 or getattr(exc, "winerror", None) == 10047:  # WSAEAFNOSUPPORT
            return None
        raise
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((LOOPBACK_V6, port))
    except OSError as exc:
        sock.close()
        if exc.errno in _NO_IPV6 or getattr(exc, "winerror", None) in (10047, 10049):  # WSAEADDRNOTAVAIL
            return None
        raise
    return sock


def _not_configured(exc: OSError) -> bool:
    return exc.errno in _NOT_CONFIGURED_ERRNOS or getattr(exc, "winerror", None) == 10049


def _listen_v4(port: int, choose: Callable[[], str]) -> socket.socket:
    """Listen on a random loopback address (A-01), or on ``127.0.0.1`` where only that one is configured."""
    for attempt in range(1, _ADDRESS_ATTEMPTS + 1):
        try:
            return _bind_v4(choose(), port)
        except OSError as exc:
            if _not_configured(exc):
                break  # macOS and others: only 127.0.0.1 exists; fall back to it below
            if port and attempt < _ADDRESS_ATTEMPTS:
                continue  # the fixed port is taken on this random address only: try another address
            raise PortUnavailableError(
                f"cannot listen on 127.0.0.0/8 port {port or 'any'}: {exc.strerror or exc}"
            ) from exc
    try:
        listener = _bind_v4(LOOPBACK, port)
    except OSError as exc:
        raise PortUnavailableError(f"cannot listen on {LOOPBACK}:{port}: {exc.strerror or exc}") from exc
    logging.getLogger("tabrion_studio.server").info(
        "only %s is available as a loopback address", LOOPBACK, extra={"event": "studio.loopback_fallback"}
    )
    return listener


def bind_loopback(port: int, choose: Callable[[], str] = random_loopback) -> LoopbackSockets:
    """Listen on ``127.a.b.c:port`` and hold ``[::1]:port`` (``port`` 0 = a free port that is free on both).

    ``127.a.b.c`` is a fresh random loopback address (``choose``), or ``127.0.0.1`` where no other one is configured
    (finding A-01).
    """
    attempts = 1 if port else _PORT_ATTEMPTS
    last: OSError | None = None
    for _ in range(attempts):
        listener = _listen_v4(port, choose)
        chosen = int(listener.getsockname()[1])
        try:
            guard = _guard_v6(chosen)
        except OSError as exc:
            listener.close()
            last = exc
            continue  # [::1] has this port in use by another program: with port 0, try another free port
        return LoopbackSockets(listener, guard)
    detail = last.strerror if last is not None and last.strerror else last
    raise PortUnavailableError(
        f"port {port or 'any'}: [{LOOPBACK_V6}] is used by another program ({detail}); "
        "Studio holds both loopback addresses so that no other program can answer for localhost"
    )


def remove_redirect_files(paths: InstancePaths) -> None:
    """Delete the redirect files of this project's instance (left behind by a crash; the lock is held)."""
    pattern = redirect_file_pattern(paths.info.stem)
    with contextlib.suppress(OSError):
        for leftover in paths.info.parent.iterdir():
            if pattern.fullmatch(leftover.name):
                remove_file(leftover)


def serve(options: ServeOptions, lock: InstanceLock, out: TextIO) -> int:
    """Run the server for ``options.project`` while holding ``lock``; returns the exit code."""
    paths = InstancePaths.for_project(options.state_dir, options.project)
    remove_redirect_files(paths)
    sockets = bind_loopback(options.port)
    # One log file per project instance (the lock makes this process its only writer), named like the info file.
    logs = configure_logging(options.log_dir, paths.info.stem)
    port = sockets.port
    host = sockets.host
    info = InstanceInfo(
        pid=os.getpid(),
        port=port,
        host=host,
        project=str(options.project),
        instance_id=secrets.token_hex(8),
        control_token=secrets.token_urlsafe(32),
        version=__version__,
        started_at=time.time(),
    )
    server: _StudioServer | None = None
    # An absolute folder: the CLI of a second start accepts only absolute redirect file paths.
    redirect_dir = Path(os.path.abspath(paths.info.parent))
    launches = LaunchTokens(loopback_origin(port, host), redirect_dir=redirect_dir, prefix=paths.info.stem)

    def request_shutdown(reason: str) -> None:
        if server is not None:
            server.stop_reason = reason
            server.should_exit = True

    config_dir = options.config_dir if options.config_dir is not None else default_config_dir()
    configuration = Configuration(
        project=ProjectConfig(options.project, options.state_dir, project_key(options.project), config_dir),
        user=UserConfig(config_dir),
    )
    registry = ConnectionRegistry(configuration)
    explorer = Explorer(registry)
    key = project_key(options.project)
    state = StateStore(options.state_dir)
    history = History(state, key, configuration.limits)
    # Retention holds on disk also while nothing new is recorded (the history off, an idle project): F-1.
    history.purge()
    drafts = Drafts(state, key, configuration.limits)
    drafts.purge()
    favorites = Favorites(state, key, lambda: {entry.id for entry in configuration.entries()})
    runtime = Runtime(
        config=configuration,
        connections=registry,
        queries=QueryEngine(registry, configuration.limits, history=history),
        state=state,
        history=history,
        favorites=favorites,
        scripts=Scripts(options.project),
        drafts=drafts,
        explorer=explorer,
        viewer=Viewer(explorer, configuration.limits),
        diagnostics=Diagnostics(registry),
        migrations=Migrations(registry, CodeTrust(options.project, options.state_dir, project_key(options.project))),
        exports=Exports(registry, explorer),
        instance_id=info.instance_id,
        control_token=info.control_token,
        idle=IdleMonitor(options.idle_timeout_seconds),
        port=port,
        host=host,
        launches=launches,
        request_shutdown=request_shutdown,
    )

    def on_ready() -> None:
        write_info(paths, info)
        elapsed = time.perf_counter() - options.started
        print(f"Tabrion Studio {__version__} is running at {info.url} (ready in {elapsed:.2f} s)", file=out)
        print(f"Project: {options.project}", file=out)
        if logs.file is not None:
            print(f"Log: {logs.file}", file=out)
        logging.getLogger("tabrion_studio.server").info(
            "Tabrion Studio %s ready on port %d", __version__, port, extra={"event": "studio.ready"}
        )
        if host == LOOPBACK and options.port:
            # A-01: a fixed port on the one fixed address gives a predictable origin (STU-ADR-0002, residual risk).
            print(
                f"Note: only {LOOPBACK} is available here, so --port {port} gives the same address every time; "
                "on a computer shared with other users, leave --port out.",
                file=out,
            )
        if options.print_url:
            # Explicit opt-in (--print-url): the one-time link is now in the terminal and its scrollback.
            link = launches.issue(redirect=False).url
            print(f"One-time sign-in link (valid for {launches.ttl:g} s, do not share): {link}", file=out)
        print("Press Ctrl+C to stop.", file=out, flush=True)
        if options.open_browser is not None:
            try:
                redirect = launches.issue(redirect=True).redirect
            except (OSError, UnsafePathError) as exc:
                print(f"Cannot open the browser ({exc}); run tabrion-studio again or use --print-url.", file=out)
                return
            if redirect is not None:
                uri = redirect.resolve().as_uri()
                threading.Thread(target=options.open_browser, args=(uri,), daemon=True).start()

    def on_stopping() -> None:
        # First withdraw the info file and the launch tokens, then Uvicorn closes the sockets (review F-01).
        remove_info_if_ours(paths, info.instance_id)
        launches.close()

    config = uvicorn.Config(
        create_app(runtime),
        # Uvicorn's loggers go through the redacting handlers of configure_logging (STU-004), not its own config.
        log_config=None,
        log_level="info",
        # 03 section 4.5: no access log (paths carry IDs); tabrion_studio.request_log logs routes without queries.
        access_log=False,
        # Forwarded headers are never trusted (T-01); the access middleware refuses requests that carry them.
        proxy_headers=False,
        server_header=False,
        lifespan="on",
        # h11 with a time limit for each request (A-02). No limit_concurrency: Uvicorn would answer the owner's own
        # requests with 503 while that many idle connections are open, which makes blocking Studio easier.
        http=RequestTimeoutProtocol,
        ws="none",
        loop="asyncio",
        workers=1,
        timeout_graceful_shutdown=5,
    )
    server = _StudioServer(config, on_ready, on_stopping)
    try:
        server.run(sockets=[sockets.listener])
    finally:
        launches.close()
        remove_info_if_ours(paths, info.instance_id)
        sockets.close()
        lock.release()
        logging.getLogger("tabrion_studio.server").info(
            "Tabrion Studio stopped (%s)", server.stop_reason, extra={"event": "studio.stopped"}
        )
        logs.close()
    if not server.started:
        say_last("Tabrion Studio could not start.", out)
        return 1
    say_last(f"Tabrion Studio stopped ({server.stop_reason}).", out)
    return 0


def say_last(text: str, out: TextIO) -> None:
    """Print the last line of a run; a reader that is already gone does not turn a clean stop into a traceback.

    The shutdown has finished when this runs (info file, launch tokens, sockets, lock and log are closed), so a
    closed pipe (the terminal or the parent process stopped reading, e.g. ``tabrion-studio | head``) or a closed
    ``out`` only loses this line. ``sys.stdout`` is then pointed at ``os.devnull``, so the interpreter's own flush at
    exit does not report the broken pipe either (the CPython guidance for SIGPIPE). Every other error is raised.
    """
    try:
        print(text, file=out, flush=True)
    except BrokenPipeError:
        _drop_stdout(out)
    except ValueError:
        if not getattr(out, "closed", False):
            raise
    except OSError as exc:
        # Windows reports a write to a pipe whose reader is gone as EINVAL, not as a broken pipe.
        if not (sys.platform == "win32" and exc.errno == errno.EINVAL):
            raise
        _drop_stdout(out)


def _drop_stdout(out: TextIO) -> None:
    if out is not sys.stdout:
        return
    with contextlib.suppress(OSError, ValueError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
