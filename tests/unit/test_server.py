from __future__ import annotations

import asyncio
import errno
import io
from pathlib import Path
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


def test_bind_loopback_holds_both_loopback_addresses() -> None:
    sockets = server.bind_loopback(0)
    try:
        assert sockets.listener.getsockname()[0] == "127.0.0.1"
        if _ipv6_loopback_available():
            assert sockets.v6_guard is not None
            assert sockets.v6_guard.getsockname()[:2] == ("::1", sockets.port)
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as rival, pytest.raises(OSError):
                rival.bind(("::1", sockets.port))
        else:
            assert sockets.v6_guard is None
    finally:
        sockets.close()


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
