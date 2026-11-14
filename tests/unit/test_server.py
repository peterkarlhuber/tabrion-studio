from __future__ import annotations

import asyncio
import errno
import io
import ipaddress
from pathlib import Path
import re
import signal
import socket
import sys

import pytest
import uvicorn

from tabrion_studio import server


def _ipv6_loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


def test_stop_signals_cover_the_platform() -> None:
    # Review F-04: a closed terminal (SIGHUP) or console window (CTRL_CLOSE -> SIGBREAK) also ends Studio cleanly.
    names = {sig.name for sig in server.STOP_SIGNALS}
    if sys.platform == "win32":
        assert names == {"SIGINT", "SIGTERM", "SIGBREAK"}
    else:
        assert names == {"SIGINT", "SIGTERM", "SIGHUP"}
        assert signal.SIGHUP in server.STOP_SIGNALS


def _random_loopback_available() -> bool:
    """Linux and Windows route all of 127.0.0.0/8 to loopback; macOS configures only 127.0.0.1 by default."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.83.41.29", 0))
    except OSError:
        return False
    return True


def test_bind_loopback_holds_both_loopback_addresses() -> None:
    sockets = server.bind_loopback(0)
    try:
        host = sockets.listener.getsockname()[0]
        assert host == sockets.host
        assert ipaddress.IPv4Address(host) in ipaddress.IPv4Network("127.0.0.0/8")
        if _ipv6_loopback_available():
            assert sockets.v6_guard is not None
            assert sockets.v6_guard.getsockname()[:2] == ("::1", sockets.port)
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as rival, pytest.raises(OSError):
                rival.bind(("::1", sockets.port))
        else:
            assert sockets.v6_guard is None
    finally:
        sockets.close()


def test_every_start_gets_a_fresh_loopback_address() -> None:
    """A-01: the origin of an instance is not predictable and not the one of an earlier instance."""
    if sys.platform.startswith("linux") or sys.platform == "win32":
        assert _random_loopback_available()
    if not _random_loopback_available():
        # macOS by default: only 127.0.0.1 exists, the documented fallback (STU-ADR-0002 residual risk).
        sockets = server.bind_loopback(0)
        try:
            assert sockets.host == "127.0.0.1"
        finally:
            sockets.close()
        return
    hosts = set()
    for _ in range(4):
        sockets = server.bind_loopback(0)
        try:
            hosts.add(sockets.host)
            assert sockets.host != "127.0.0.1"
        finally:
            sockets.close()
    assert len(hosts) == 4


def test_random_loopback_addresses() -> None:
    seen = {server.random_loopback() for _ in range(500)}
    assert "127.0.0.1" not in seen
    for address in seen:
        a, b, c = (int(part) for part in address.removeprefix("127.").split("."))
        assert 0 <= a <= 255 and 0 <= b <= 255 and 1 <= c <= 254
    assert len(seen) > 490  # 2**24 possibilities: practically no repeats


def test_a_fixed_port_keeps_its_port_on_a_new_address() -> None:
    if not _random_loopback_available():
        return  # the fallback is covered by test_without_other_loopback_addresses_studio_uses_127_0_0_1
    first = server.bind_loopback(0)
    port, host = first.port, first.host
    first.close()
    again = server.bind_loopback(port)
    try:
        assert again.port == port
        assert again.host != host
    finally:
        again.close()


def test_a_fixed_port_taken_on_one_random_address_tries_another() -> None:
    if not _random_loopback_available():
        return  # needs a second loopback address; the fallback is tested below
    with socket.create_server(("127.61.7.9", 0)) as busy:
        port = busy.getsockname()[1]
        choices = iter(["127.61.7.9", "127.61.7.10"])
        sockets = server.bind_loopback(port, choose=lambda: next(choices))
        try:
            assert (sockets.host, sockets.port) == ("127.61.7.10", port)
        finally:
            sockets.close()


def test_without_other_loopback_addresses_studio_uses_127_0_0_1(monkeypatch: pytest.MonkeyPatch) -> None:
    # macOS: binding an address that is not configured fails with EADDRNOTAVAIL; Studio then uses 127.0.0.1.
    real_bind = server._bind_v4

    def bind(host: str, port: int) -> socket.socket:
        if host != "127.0.0.1":
            raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")
        return real_bind(host, port)

    monkeypatch.setattr(server, "_bind_v4", bind)
    sockets = server.bind_loopback(0)
    try:
        assert sockets.host == "127.0.0.1"
    finally:
        sockets.close()


def test_a_busy_fixed_port_is_reported_on_the_fallback_address(monkeypatch: pytest.MonkeyPatch) -> None:
    real_bind = server._bind_v4

    def bind(host: str, port: int) -> socket.socket:
        if host != "127.0.0.1":
            raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")
        return real_bind(host, port)

    monkeypatch.setattr(server, "_bind_v4", bind)
    with socket.create_server(("127.0.0.1", 0)) as busy:
        port = busy.getsockname()[1]
        with pytest.raises(server.PortUnavailableError, match=f"cannot listen on 127.0.0.1:{port}"):
            server.bind_loopback(port)


def test_fixed_port_taken_on_ipv6_is_refused() -> None:
    if not _ipv6_loopback_available():
        # Without an IPv6 loopback there is nothing to take over: the IPv4 listener alone is used.
        sockets = server.bind_loopback(0)
        assert sockets.v6_guard is None
        sockets.close()
        return
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as rival:
        rival.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        rival.bind(("::1", 0))
        rival.listen()
        port = rival.getsockname()[1]
        with pytest.raises(server.PortUnavailableError, match=r"\[::1\] is used by another program"):
            server.bind_loopback(port)
        # The IPv4 port is free again afterwards.
        with socket.create_server(("127.0.0.1", port)):
            pass


def test_info_file_is_withdrawn_before_the_shutdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Review F-01/F-04: the info file goes first, then Uvicorn closes the sockets and the lifespan.
    events: list[str] = []

    async def fake_shutdown(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        events.append("uvicorn shutdown")

    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_shutdown)
    studio = server._StudioServer(
        # log_config=None as in serve(): the default would dictConfig Uvicorn's loggers for the whole process (TD-055).
        uvicorn.Config(app=lambda: None, log_config=None),
        on_ready=lambda: None,
        on_stopping=lambda: events.append("info removed"),
    )
    asyncio.run(studio.shutdown())
    assert events == ["info removed", "uvicorn shutdown"]


# JR-1 of joint-regression-2026-11-12.md: the last line of a run, when its reader is gone.


class _Out(io.StringIO):
    """A stream whose writes raise ``error``."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def write(self, text: str) -> int:
        raise self.error


def test_say_last_prints_the_line() -> None:
    out = io.StringIO()
    server.say_last("Tabrion Studio stopped (SIGTERM).", out)
    assert out.getvalue() == "Tabrion Studio stopped (SIGTERM).\n"


def test_say_last_ignores_a_reader_that_is_gone() -> None:
    server.say_last("lost", _Out(BrokenPipeError(errno.EPIPE, "Broken pipe")))
    closed = io.StringIO()
    closed.close()
    server.say_last("lost", closed)


@pytest.mark.parametrize(
    "error",
    [OSError(errno.EIO, "I/O error"), OSError(errno.ENOSPC, "No space left"), ValueError("not about a closed file")],
    ids=["EIO", "ENOSPC", "ValueError-open"],
)
def test_say_last_raises_every_other_error(error: BaseException) -> None:
    with pytest.raises(type(error)):
        server.say_last("line", _Out(error))


@pytest.mark.skipif(sys.platform == "win32", reason="EINVAL is the closed pipe only on Windows")
def test_say_last_raises_einval_off_windows() -> None:
    with pytest.raises(OSError):
        server.say_last("line", _Out(OSError(errno.EINVAL, "Invalid argument")))


# --- A-02: a time limit for each request ------------------------------------------------------------------------


async def _slow_app(scope: dict[str, object], receive: object, send: object) -> None:
    """Answers ``/slow`` after twice the request timeout and everything else at once."""
    if scope["type"] != "http":
        return
    if scope["path"] == "/slow":
        await asyncio.sleep(2 * server.REQUEST_READ_TIMEOUT_S)
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})  # type: ignore[operator]
    await send({"type": "http.response.body", "body": b"ok"})  # type: ignore[operator]


async def _serve(app: object) -> tuple[uvicorn.Server, asyncio.Task[None], int]:
    config = uvicorn.Config(
        app,  # type: ignore[arg-type]
        host="127.0.0.1",
        port=0,
        log_config=None,
        lifespan="off",
        http=server.RequestTimeoutProtocol,
        ws="none",
    )
    instance = uvicorn.Server(config)
    task = asyncio.create_task(instance.serve())
    while not instance.started:
        await asyncio.sleep(0.01)
    port = instance.servers[0].sockets[0].getsockname()[1]
    return instance, task, int(port)


async def _closed_within(reader: asyncio.StreamReader, seconds: float) -> bytes:
    """What the server sent before it closed the connection; fails if it is still open after ``seconds``."""
    return await asyncio.wait_for(reader.read(), seconds)


def test_a_connection_that_sends_no_request_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "REQUEST_READ_TIMEOUT_S", 0.3)

    async def scenario() -> None:
        instance, task, port = await _serve(_slow_app)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await _closed_within(reader, 3) == b""
            writer.close()
        finally:
            instance.should_exit = True
            await task

    asyncio.run(scenario())


def test_a_request_that_trickles_in_is_closed_although_bytes_arrive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "REQUEST_READ_TIMEOUT_S", 0.5)

    async def scenario() -> None:
        instance, task, port = await _serve(_slow_app)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            closed = asyncio.create_task(_closed_within(reader, 5))
            for byte in b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Pad: " + b"a" * 200:
                if closed.done():
                    break
                writer.write(bytes([byte]))
                await asyncio.sleep(0.05)  # far below the timeout between two bytes
            assert await closed == b""
            writer.close()
        finally:
            instance.should_exit = True
            await task

    asyncio.run(scenario())


def test_requests_and_a_long_answer_are_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "REQUEST_READ_TIMEOUT_S", 0.3)

    async def scenario() -> None:
        instance, task, port = await _serve(_slow_app)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            # The answer takes twice the timeout: the timer stops once the request is complete.
            for path in ("/slow", "/", "/slow"):
                writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                assert head.startswith(b"HTTP/1.1 200")
                assert await reader.readexactly(2) == b"ok"
            writer.close()
        finally:
            instance.should_exit = True
            await task

    asyncio.run(scenario())


def test_studio_runs_uvicorn_with_the_request_time_limit() -> None:
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "http=RequestTimeoutProtocol" in source
    # No limit_concurrency: with it, idle connections would make Uvicorn answer the owner with 503.
    assert re.search(r"limit_concurrency\s*=", source) is None
