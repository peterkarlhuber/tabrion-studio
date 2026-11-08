"""STU-002 acceptance tests with real processes: start < 2 s, second start reuses the instance, idle exit, shutdown."""

from __future__ import annotations

import contextlib
from pathlib import Path
import socket
import sys
import time

import pytest

from tabrion_studio.instance import InstanceInfo, InstanceLock, InstancePaths
from tests.support.session import heartbeat, sign_in
from tests.support.studio import get, get_json, run_cli, start_studio

pytestmark = pytest.mark.process

START_BUDGET_SECONDS = 2.0
# Every stop signal of the platform (review F-04): Ctrl+C, kill and a closed terminal on POSIX; CTRL_BREAK (and
# CTRL_CLOSE, which the C runtime also delivers as SIGBREAK) on Windows.
STOP_SIGNALS = ["SIGBREAK"] if sys.platform == "win32" else ["SIGINT", "SIGTERM", "SIGHUP"]


def _stop(project: Path, state_dir: Path) -> None:
    result = run_cli("--stop", str(project), "--state-dir", str(state_dir))
    assert result.returncode == 0, result.stdout + result.stderr


def test_start_is_ready_within_two_seconds(project: Path, state_dir: Path) -> None:
    # STU-002: "Start < 2 s". A first start compiles the bytecode caches (as any first run after installation);
    # the measured start is the next one: from spawning the process to the first health answer.
    with start_studio(project, state_dir) as warmup:
        warmup.wait_ready()
        _stop(project, state_dir)
        assert warmup.wait_exit() == 0
    timings = []
    for _ in range(2):
        with start_studio(project, state_dir) as studio:
            _, seconds = studio.wait_ready()
            timings.append(seconds)
            _stop(project, state_dir)
            assert studio.wait_exit() == 0
            assert "ready in" in studio.output()
    assert min(timings) < START_BUDGET_SECONDS, f"start took {timings} s (budget {START_BUDGET_SECONDS} s)"


def test_binds_loopback_only(project: Path, state_dir: Path) -> None:
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        assert info.url == f"http://127.0.0.1:{info.port}/"
        # Nothing answers on the same port on a non-loopback address of this machine (if it has one).
        try:
            other = socket.gethostbyname(socket.gethostname())
        except OSError:
            other = "127.0.0.1"
        if not other.startswith("127."):
            with pytest.raises(OSError), socket.create_connection((other, info.port), timeout=1):
                pass
        _stop(project, state_dir)
        assert studio.wait_exit() == 0


def test_second_start_reuses_the_running_instance(project: Path, state_dir: Path) -> None:
    with start_studio(project, state_dir) as first:
        info, _ = first.wait_ready()
        began = time.perf_counter()
        second = run_cli(str(project), "--no-browser", "--state-dir", str(state_dir))
        elapsed = time.perf_counter() - began
        assert second.returncode == 0, second.stdout + second.stderr
        assert f"already running for {project.resolve()}" in second.stdout
        assert info.url in second.stdout
        assert elapsed < 5
        # Still the same single server: same instance in the info file and on the port.
        assert first.process.poll() is None
        assert first.info() == info
        assert get_json(f"{info.url}api/health")["instance"] == info.instance_id
        _stop(project, state_dir)
        assert first.wait_exit() == 0


def test_another_project_gets_its_own_instance(tmp_path: Path, project: Path, state_dir: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    with start_studio(project, state_dir) as a, start_studio(other, state_dir) as b:
        info_a, _ = a.wait_ready()
        info_b, _ = b.wait_ready()
        assert info_a.port != info_b.port
        assert info_a.instance_id != info_b.instance_id
        _stop(project, state_dir)
        _stop(other, state_dir)
        assert a.wait_exit() == 0
        assert b.wait_exit() == 0


def test_stop_shuts_down_and_cleans_up(project: Path, state_dir: Path) -> None:
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        _stop(project, state_dir)
        assert studio.wait_exit() == 0
        assert "stopped (stop requested)" in studio.output()
        assert not studio.paths.info.exists()
        with pytest.raises(OSError):
            get(f"{info.url}api/health", timeout=1)
        # The lock is free again.
        lock = InstanceLock(studio.paths.lock)
        assert lock.acquire()
        lock.release()


def test_stop_without_a_running_instance(project: Path, state_dir: Path) -> None:
    result = run_cli("--stop", str(project), "--state-dir", str(state_dir))
    assert result.returncode == 0
    assert "No Tabrion Studio is running" in result.stdout


@pytest.mark.parametrize("name", STOP_SIGNALS)
def test_stop_signal_shuts_down_cleanly(project: Path, state_dir: Path, name: str) -> None:
    with start_studio(project, state_dir) as studio:
        studio.wait_ready()
        studio.send_stop_signal(name)
        assert studio.wait_exit() == 0
        assert f"stopped ({name})" in studio.output()
        assert not studio.paths.info.exists()
        lock = InstanceLock(studio.paths.lock)
        assert lock.acquire()
        lock.release()


def test_idle_exit(project: Path, state_dir: Path) -> None:
    # 0.05 min = 3 s without any request.
    with start_studio(project, state_dir, "--idle-timeout", "0.05") as studio:
        studio.wait_ready()
        began = time.monotonic()
        assert studio.wait_exit(timeout=30) == 0
        assert time.monotonic() - began >= 2.5
        assert "stopped (no activity for 0.05 min)" in studio.output()
        assert not studio.paths.info.exists()


def test_activity_postpones_the_idle_exit(project: Path, state_dir: Path) -> None:
    with start_studio(project, state_dir, "--idle-timeout", "0.05") as studio:
        info, _ = studio.wait_ready()
        session = sign_in(info)
        until = time.monotonic() + 5
        while time.monotonic() < until:  # a heartbeat every second, as an open tab does every minute
            assert heartbeat(session) == 204
            assert studio.process.poll() is None
            time.sleep(1)
        assert studio.wait_exit(timeout=30) == 0


def test_health_checks_do_not_postpone_the_idle_exit(project: Path, state_dir: Path) -> None:
    # Review F-06: only requests with a valid session and authenticated control requests count as activity.
    with start_studio(project, state_dir, "--idle-timeout", "0.05") as studio:
        info, _ = studio.wait_ready()
        started = time.monotonic()
        while studio.process.poll() is None and time.monotonic() - started < 30:
            with contextlib.suppress(OSError):
                get_json(f"{info.url}api/health", timeout=0.5)
            time.sleep(0.25)
        assert studio.wait_exit(timeout=5) == 0
        assert time.monotonic() - started < 15
        assert "stopped (no activity" in studio.output()


def _ipv6_loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


def test_ipv6_loopback_port_is_held(project: Path, state_dir: Path) -> None:
    # Review F-08: while Studio runs, no other program can serve http://localhost:<port>/ over [::1].
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        if _ipv6_loopback_available():
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as rival, pytest.raises(OSError):
                rival.bind(("::1", info.port))
        assert "localhost" not in studio.output()
        _stop(project, state_dir)
        assert studio.wait_exit() == 0


def test_stale_info_file_after_a_crash_does_not_block_a_start(project: Path, state_dir: Path) -> None:
    with start_studio(project, state_dir) as crashed:
        stale, _ = crashed.wait_ready()
        crashed.kill()  # no cleanup: the info file stays, the OS releases the lock
    assert InstancePaths.for_project(state_dir, project).info.exists()
    with start_studio(project, state_dir) as studio:
        info, _ = studio.wait_ready()
        assert info.instance_id != stale.instance_id
        _stop(project, state_dir)
        assert studio.wait_exit() == 0


def test_busy_port_is_reported(project: Path, state_dir: Path) -> None:
    with socket.create_server(("127.0.0.1", 0)) as busy:
        port = busy.getsockname()[1]
        result = run_cli(str(project), "--no-browser", "--state-dir", str(state_dir), "--port", str(port))
    assert result.returncode == 1
    assert f"cannot listen on 127.0.0.1:{port}" in result.stderr
    lock = InstanceLock(InstancePaths.for_project(state_dir, project).lock)
    assert lock.acquire()
    lock.release()


def test_fixed_port(project: Path, state_dir: Path) -> None:
    with socket.create_server(("127.0.0.1", 0)) as probe:
        port = probe.getsockname()[1]
    with start_studio(project, state_dir, "--port", str(port)) as studio:
        info, _ = studio.wait_ready()
        assert info.port == port
        assert isinstance(info, InstanceInfo)
        _stop(project, state_dir)
        assert studio.wait_exit() == 0
