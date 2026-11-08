"""One Studio instance per project folder (03 section 4.1, STU-002).

Each project folder has two files in the state directory, named after a hash of the folder's resolved path:

* ``studio-<key>.lock``: held with an exclusive OS lock (``flock`` or ``msvcrt.locking``) for as long as the server
  process runs. The OS releases it when the process ends, even after a crash, so a stale lock never blocks a start.
* ``studio-<key>.json`` (mode 0600): PID, port, instance ID and the control token of the running server. A second
  ``tabrion-studio`` start for the same folder reads it and asks the running server, over the control endpoint
  ``POST /api/control/{launch,shutdown}`` with ``Authorization: Bearer <control token>``, for a new launch URL instead
  of starting a second server. A web page cannot read the file, and it cannot send the header cross-origin without a
  CORS preflight, which the server never grants.

The control client never uses a proxy: requests go straight to the loopback address.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import IO, Any
import urllib.error
import urllib.request

from tabrion_studio.paths import ensure_private_dir, read_private_file, remove_file, write_private_file

LOOPBACK = "127.0.0.1"
INFO_VERSION = 1


def project_key(project: Path) -> str:
    """A short, stable key for a project folder (hash of its resolved, case-normalized path)."""
    normalized = os.path.normcase(str(project.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class InstancePaths:
    lock: Path
    info: Path

    @classmethod
    def for_project(cls, state_dir: Path, project: Path) -> InstancePaths:
        key = project_key(project)
        return cls(lock=state_dir / f"studio-{key}.lock", info=state_dir / f"studio-{key}.json")


@dataclass(frozen=True)
class InstanceInfo:
    """What a running server publishes about itself in the 0600 info file."""

    pid: int
    port: int
    project: str
    instance_id: str
    control_token: str
    version: str
    started_at: float

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/"

    def to_bytes(self) -> bytes:
        return json.dumps({"format": INFO_VERSION, **asdict(self)}, indent=2).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> InstanceInfo | None:
        """Parse an info file; None for anything malformed (a half-written or foreign file)."""
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("format") != INFO_VERSION:
            return None
        try:
            info = cls(
                pid=_typed(raw, "pid", int),
                port=_typed(raw, "port", int),
                project=_typed(raw, "project", str),
                instance_id=_typed(raw, "instance_id", str),
                control_token=_typed(raw, "control_token", str),
                version=_typed(raw, "version", str),
                started_at=float(_typed(raw, "started_at", (int, float))),
            )
        except (KeyError, TypeError):
            return None
        return info if 0 < info.port < 65536 else None


def _typed(raw: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise TypeError(key)
    return value


def read_info(paths: InstancePaths) -> InstanceInfo | None:
    data = read_private_file(paths.info)
    return None if data is None else InstanceInfo.from_bytes(data)


def write_info(paths: InstancePaths, info: InstanceInfo) -> None:
    ensure_private_dir(paths.info.parent)
    write_private_file(paths.info, info.to_bytes())


def remove_info_if_ours(paths: InstancePaths, instance_id: str) -> None:
    """Remove the info file unless a newer instance has replaced it."""
    try:
        current = read_info(paths)
    except OSError:
        return
    if current is not None and current.instance_id == instance_id:
        remove_file(paths.info)


class InstanceLock:
    """The exclusive, non-blocking per-project lock; released by the OS when the process ends."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> bool:
        """Take the lock; False if another process holds it."""
        if self._fh is not None:
            return True
        ensure_private_dir(self.path.parent)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fh = os.fdopen(os.open(self.path, flags, 0o600), "r+b")
        try:
            if not _try_lock(fh):
                fh.close()
                return False
        except BaseException:
            fh.close()
            raise
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            _unlock(fh)
            fh.close()


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fh: IO[bytes]) -> bool:
        # msvcrt locks byte ranges; the file needs one byte to lock.
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fh: IO[bytes]) -> None:
        fh.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fh: IO[bytes]) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------------------------------------------------
# Control client (used by a second start and by `tabrion-studio --stop`)
# ---------------------------------------------------------------------------------------------------------------------

# No proxy, ever: the control token must only travel to the loopback address.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ControlError(RuntimeError):
    """The running instance did not answer as expected."""


def _request(url: str, *, method: str = "GET", token: str | None = None, timeout: float = 5.0) -> Any:
    if not url.startswith(f"http://{LOOPBACK}:"):
        raise ControlError("control requests go to the loopback address only")
    headers = {"Accept": "application/json"}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = b"{}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310 (loopback only)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ControlError(f"{method} {url} failed: {exc}") from exc


def health(port: int, timeout: float = 2.0) -> dict[str, Any] | None:
    """The health document of the server on ``port``, or None if nothing answers."""
    try:
        doc = _request(f"http://{LOOPBACK}:{port}/api/health", timeout=timeout)
    except ControlError:
        return None
    return doc if isinstance(doc, dict) else None


def control(info: InstanceInfo, action: str, timeout: float = 5.0) -> dict[str, Any]:
    """Call ``POST /api/control/<action>`` on the running instance."""
    doc = _request(
        f"http://{LOOPBACK}:{info.port}/api/control/{action}", method="POST", token=info.control_token, timeout=timeout
    )
    if not isinstance(doc, dict):
        raise ControlError(f"unexpected answer to {action}")
    return doc


def find_running(paths: InstancePaths, wait: float = 5.0) -> InstanceInfo | None:
    """The running instance whose info file and health answer agree, waiting up to ``wait`` seconds for it.

    The caller has failed to take the lock, so a server process exists; it may still be starting (no info file yet)
    or shutting down.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            info = read_info(paths)
        except OSError:  # replaced or removed while reading; try again
            info = None
        if info is not None:
            doc = health(info.port)
            if doc is not None and doc.get("instance") == info.instance_id:
                return info
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)
