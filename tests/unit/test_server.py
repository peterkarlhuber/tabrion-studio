from __future__ import annotations

import asyncio
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
        uvicorn.Config(app=lambda: None), on_ready=lambda: None, on_stopping=lambda: events.append("info removed")
    )
    asyncio.run(studio.shutdown())
    assert events == ["info removed", "uvicorn shutdown"]
