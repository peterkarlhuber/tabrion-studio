from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from tabrion_studio import instance
from tabrion_studio.instance import InstanceInfo, InstanceLock, InstancePaths

INFO = InstanceInfo(
    pid=1234,
    port=50123,
    project="/work/project",
    instance_id="0123456789abcdef",
    control_token="secret-token",
    version="0.1.0.dev0",
    started_at=1.5,
)


def test_project_key_is_stable_and_distinct(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert instance.project_key(a) == instance.project_key(a / ".")
    assert instance.project_key(a) != instance.project_key(b)
    assert len(instance.project_key(a)) == 16


def test_paths_are_named_after_the_key(tmp_path: Path) -> None:
    paths = InstancePaths.for_project(tmp_path / "state", tmp_path)
    key = instance.project_key(tmp_path)
    assert paths.lock.name == f"studio-{key}.lock"
    assert paths.info.name == f"studio-{key}.json"


def test_info_round_trip(tmp_path: Path) -> None:
    assert InstanceInfo.from_bytes(INFO.to_bytes()) == INFO
    assert INFO.url == "http://127.0.0.1:50123/"
    paths = InstancePaths.for_project(tmp_path / "state", tmp_path)
    instance.write_info(paths, INFO)
    assert instance.read_info(paths) == INFO


def test_info_carries_the_loopback_address(tmp_path: Path) -> None:
    # A-01: the info file names the instance's own random 127.a.b.c; the control client connects there.
    info = replace(INFO, host="127.9.200.17")
    assert info.url == "http://127.9.200.17:50123/"
    assert json.loads(info.to_bytes())["host"] == "127.9.200.17"
    assert InstanceInfo.from_bytes(info.to_bytes()) == info
    # A file without an address was written by a server from before A-01, which listened on 127.0.0.1.
    raw = json.loads(INFO.to_bytes())
    del raw["host"]
    old = InstanceInfo.from_bytes(json.dumps(raw).encode())
    assert old is not None and old.host == "127.0.0.1"


@pytest.mark.parametrize(
    "host",
    [
        "10.0.0.1",
        "0.0.0.0",
        "128.0.0.1",
        "::1",
        "localhost",
        "127.0.0.1.evil.example",
        "127.1",
        "127.000.0.1",
        "127.0.0.256",
        " 127.0.0.1",
        "",
        127,
        None,
        True,
    ],
)
def test_info_with_a_host_outside_the_loopback_net_is_ignored(host: object) -> None:
    # The control token only ever travels to an IPv4 loopback address written in plain dotted form.
    raw = {**json.loads(INFO.to_bytes()), "host": host}
    assert InstanceInfo.from_bytes(json.dumps(raw).encode()) is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff",
        b"[]",
        json.dumps({"format": 2}).encode(),
        json.dumps({"format": 1, "pid": 1}).encode(),
        json.dumps({**json.loads(INFO.to_bytes()), "port": "80"}).encode(),
        json.dumps({**json.loads(INFO.to_bytes()), "port": True}).encode(),
        json.dumps({**json.loads(INFO.to_bytes()), "port": 0}).encode(),
        json.dumps({**json.loads(INFO.to_bytes()), "port": 70000}).encode(),
    ],
)
def test_malformed_info_is_ignored(data: bytes) -> None:
    assert InstanceInfo.from_bytes(data) is None


def test_remove_info_only_if_ours(tmp_path: Path) -> None:
    paths = InstancePaths.for_project(tmp_path / "state", tmp_path)
    instance.write_info(paths, INFO)
    instance.remove_info_if_ours(paths, "someone-else")
    assert paths.info.exists()
    instance.remove_info_if_ours(paths, INFO.instance_id)
    assert not paths.info.exists()
    instance.remove_info_if_ours(paths, INFO.instance_id)  # missing: no error


def test_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "state" / "studio-x.lock"
    first, second = InstanceLock(path), InstanceLock(path)
    assert first.acquire()
    assert first.acquire()  # re-entrant for the holder
    assert first.held
    assert not second.acquire()
    assert not second.held
    first.release()
    assert second.acquire()
    second.release()
    first.release()  # releasing twice is harmless


def test_health_of_a_closed_port_is_none() -> None:
    import socket

    with socket.create_server(("127.0.0.1", 0)) as sock:
        port = sock.getsockname()[1]
    assert instance.health(port, timeout=0.5) is None


def test_control_requests_never_use_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # The control client connects straight to 127.0.0.1; proxy settings in the environment are ignored.
    from tests.support.fake_server import fake_studio

    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    token = "t0ken"
    with fake_studio("abc", lambda nonce: instance.control_proof(token, nonce)) as fake:
        info = instance.InstanceInfo(1, fake.port, "/p", "abc", token, "0", 1.0)
        assert instance.health(fake.port) == {"status": "ok", "version": "0.1.0.dev0", "instance": "abc"}
        assert instance.control(info, "shutdown") == {"status": "stopping"}
        assert {r.headers.get("Host") for r in fake.seen} == {f"127.0.0.1:{fake.port}"}


def test_control_client_talks_to_the_instance_address(monkeypatch: pytest.MonkeyPatch) -> None:
    # A-01: a Studio on a random 127.a.b.c is reached there, with exactly that Host, and never on 127.0.0.1.
    import socket

    from tests.support.fake_server import fake_studio

    try:
        with socket.create_server(("127.77.3.5", 0)):
            pass
    except OSError:
        host = "127.0.0.1"  # macOS by default: only 127.0.0.1 is configured
    else:
        host = "127.77.3.5"
    token = "t0ken"
    with fake_studio("abc", lambda nonce: instance.control_proof(token, nonce), host=host) as fake:
        info = instance.InstanceInfo(1, fake.port, "/p", "abc", token, "0", 1.0, host=host)
        assert instance.health(fake.port, host=host) == {"status": "ok", "version": "0.1.0.dev0", "instance": "abc"}
        instance.verify_server(info)
        assert instance.control(info, "shutdown") == {"status": "stopping"}
        assert {r.headers.get("Host") for r in fake.seen} == {f"{host}:{fake.port}"}
        if host != "127.0.0.1":
            assert instance.health(fake.port, timeout=0.5) is None  # nothing on 127.0.0.1:<port>


def test_control_against_nothing_raises() -> None:
    import socket

    with socket.create_server(("127.0.0.1", 0)) as sock:
        port = sock.getsockname()[1]
    with pytest.raises(instance.ControlError):
        instance.control(replace(INFO, port=port), "launch", timeout=0.5)


def test_find_running_gives_up(tmp_path: Path) -> None:
    paths = InstancePaths.for_project(tmp_path / "state", tmp_path)
    assert instance.find_running(paths, wait=0.2) is None
    instance.write_info(paths, replace(INFO, port=1))  # nothing healthy on port 1
    assert instance.find_running(paths, wait=0.2) is None
