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


def test_control_requests_never_leave_loopback() -> None:
    with pytest.raises(instance.ControlError, match="loopback"):
        instance._request("http://example.com:80/api/health")


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
