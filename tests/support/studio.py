"""Start real ``tabrion-studio`` processes for the process-model tests (STU-002)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import IO
import urllib.request

from tabrion_studio.instance import InstanceInfo, InstancePaths

# No proxy for loopback requests (CI runners and developer machines may set HTTP_PROXY).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WINDOWS = sys.platform == "win32"


def studio_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "tabrion_studio", *args]


def get(url: str, timeout: float = 2.0) -> tuple[int, bytes, dict[str, str]]:
    with _OPENER.open(url, timeout=timeout) as response:
        return int(response.status), response.read(), dict(response.headers)


def get_json(url: str, timeout: float = 2.0) -> dict[str, object]:
    status, body, _ = get(url, timeout)
    assert status == 200
    doc = json.loads(body)
    assert isinstance(doc, dict)
    return doc


@dataclass
class Studio:
    """One started process and where to find it."""

    process: subprocess.Popen[bytes]
    project: Path
    state_dir: Path
    log_path: Path
    log: IO[bytes]
    started: float = field(default_factory=time.perf_counter)

    @property
    def paths(self) -> InstancePaths:
        return InstancePaths.for_project(self.state_dir, self.project)

    def info(self) -> InstanceInfo | None:
        try:
            return InstanceInfo.from_bytes(self.paths.info.read_bytes())
        except FileNotFoundError:
            return None

    def output(self) -> str:
        self.log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def wait_ready(self, timeout: float = 15.0) -> tuple[InstanceInfo, float]:
        """Wait until the info file exists and the health endpoint answers; returns (info, seconds since start)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f"tabrion-studio exited with {self.process.returncode}:\n{self.output()}")
            info = self.info()
            if info is not None:
                try:
                    doc = get_json(f"{info.url}api/health", timeout=1.0)
                except OSError:
                    doc = {}
                if doc.get("instance") == info.instance_id:
                    return info, time.perf_counter() - self.started
            time.sleep(0.02)
        raise AssertionError(f"tabrion-studio not ready within {timeout} s:\n{self.output()}")

    def wait_exit(self, timeout: float = 20.0) -> int:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"tabrion-studio still running after {timeout} s:\n{self.output()}") from None

    def interrupt(self) -> None:
        """Send the console stop signal: SIGINT on POSIX, CTRL_BREAK on Windows (own process group)."""
        self.send_stop_signal("SIGBREAK" if sys.platform == "win32" else "SIGINT")

    def send_stop_signal(self, name: str) -> None:
        """Send a stop signal by name; on Windows only SIGBREAK (CTRL_BREAK to the process group) exists."""
        if sys.platform == "win32":
            assert name == "SIGBREAK", name
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.process.send_signal(getattr(signal, name))

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)


def run_cli(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(studio_command(*args), capture_output=True, text=True, timeout=timeout, check=False)


@contextlib.contextmanager
def start_studio(project: Path, state_dir: Path, *extra: str, log_dir: Path | None = None) -> Iterator[Studio]:
    """Start ``tabrion-studio PROJECT --no-browser --state-dir STATE``; kill it on exit if it still runs."""
    log_dir = log_dir or state_dir.parent
    log_path = log_dir / f"studio-{time.monotonic_ns()}.log"
    log = log_path.open("wb")
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    args: Sequence[str] = studio_command(str(project), "--no-browser", "--state-dir", str(state_dir), *extra)
    started = time.perf_counter()
    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, **kwargs)  # type: ignore[call-overload]
    studio = Studio(process, project, state_dir, log_path, log, started)
    try:
        yield studio
    finally:
        studio.kill()
        log.close()
