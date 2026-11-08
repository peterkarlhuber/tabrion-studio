"""One Studio instance per project folder (03 section 4.1, STU-002).

Each project folder has two files in the state directory, named after a hash of the folder's resolved path:

* ``studio-<key>.lock``: held with an exclusive OS lock (``flock`` or ``msvcrt.locking``) for as long as the server
  process runs. The OS releases it when the process ends, even after a crash, so a stale lock never blocks a start.
* ``studio-<key>.json`` (mode 0600): PID, port, instance ID and the control token of the running server. A second
  ``tabrion-studio`` start for the same folder reads it and asks the running server, over the control endpoint
  ``POST /api/control/{launch,shutdown}`` with ``Authorization: Bearer <control token>``, for a new launch URL instead
  of starting a second server. A web page cannot read the file, and it cannot send the header cross-origin without a
  CORS preflight, which the server never grants.

Before the client sends the token, the server proves that it knows it: the client sends a random nonce to
``GET /api/control/proof`` and checks ``HMAC-SHA256(token, "tabrion-studio-proof:" + nonce)``. Another program that
took over a port named in an info file (review F-01) therefore never receives the token, and the client accepts a
launch URL only if it is ``http://127.0.0.1:<port>/launch#<token>`` of that instance. The proof and the request with
the token travel over **one** keep-alive connection (review R-4): an established TCP connection cannot be taken
over, so the token only reaches the server that delivered the proof, even if that server stops in between and
another program binds the port.

The control client never uses a proxy: requests go straight to the loopback address.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import IO, Any

from tabrion_studio.paths import (
    UnsafePathError,
    ensure_private_dir,
    read_private_file,
    remove_file,
    write_private_file,
)
from tabrion_studio.security.launch import LAUNCH_PATH, TOKEN_PATTERN, redirect_file_pattern, redirect_page

LOOPBACK = "127.0.0.1"
INFO_VERSION = 1
PROOF_CONTEXT = b"tabrion-studio-proof:"
NONCE_PATTERN = re.compile(r"[0-9a-f]{32,128}")


def control_proof(token: str, nonce: str) -> str:
    """The server's answer to a proof request: HMAC-SHA256 over the client nonce, keyed with the control token."""
    return hmac.new(token.encode("utf-8"), PROOF_CONTEXT + nonce.encode("ascii"), hashlib.sha256).hexdigest()


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
            _check_lock_file(fh, self.path)
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


def _check_lock_file(fh: IO[bytes], path: Path) -> None:
    """Refuse a lock file that is not a regular file or that another user owns (review F-10)."""
    info = os.fstat(fh.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise UnsafePathError(f"the lock file is not a regular file: {path}")
    if sys.platform != "win32" and info.st_uid != os.getuid():
        raise UnsafePathError(f"the lock file belongs to another user: {path}")


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

# No proxy, ever: the control token must only travel to the loopback address. http.client connects directly to the
# given host and never reads proxy settings from the environment.


class ControlError(RuntimeError):
    """The running instance did not answer as expected."""


class ImpostorError(ControlError):
    """The program on the instance's port could not prove that it knows the control token."""


def health(port: int, timeout: float = 2.0) -> dict[str, Any] | None:
    """The health document of the server on ``port``, or None if nothing answers."""
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
    try:
        status, doc, _ = _exchange(conn, "GET", "/api/health")
    except ControlError:
        return None
    finally:
        conn.close()
    return doc if status == 200 and isinstance(doc, dict) else None


def _exchange(
    conn: http.client.HTTPConnection, method: str, path: str, token: str | None = None, body: object = None
) -> tuple[int, Any, bool]:
    """One request on ``conn``: (status, JSON document or None, whether the server closes the connection)."""
    headers = {"Accept": "application/json", "Host": f"{LOOPBACK}:{conn.port}"}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = json.dumps({} if body is None else body).encode("utf-8")
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read()
    except (http.client.HTTPException, OSError) as exc:
        raise ControlError(f"{method} {path} failed: {exc}") from exc
    try:
        doc = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        doc = None
    return response.status, doc, bool(response.will_close)


def _prove(conn: http.client.HTTPConnection, info: InstanceInfo) -> None:
    """Make the server prove on ``conn`` that it knows the control token, without sending the token."""
    nonce = secrets.token_hex(16)
    try:
        status, doc, closing = _exchange(conn, "GET", f"/api/control/proof?nonce={nonce}")
    except ControlError as exc:
        raise ImpostorError(f"the program on port {info.port} gave no proof: {exc}") from exc
    proof = doc.get("proof") if status == 200 and isinstance(doc, dict) else None
    expected = control_proof(info.control_token, nonce)
    if not isinstance(proof, str) or not hmac.compare_digest(proof.encode("utf-8"), expected.encode("utf-8")):
        raise ImpostorError(
            f"the program on port {info.port} is not the Tabrion Studio of this folder (wrong proof); "
            "the control token was not sent"
        )
    if closing or conn.sock is None:
        raise ControlError(
            f"the server on port {info.port} closed the connection after its proof; the control token was not sent"
        )


def verify_server(info: InstanceInfo, timeout: float = 5.0) -> None:
    """Make the server on ``info.port`` prove that it knows the control token, without sending the token."""
    conn = http.client.HTTPConnection(LOOPBACK, info.port, timeout=timeout)
    try:
        _prove(conn, info)
    finally:
        conn.close()


def control(info: InstanceInfo, action: str, timeout: float = 5.0, body: object = None) -> dict[str, Any]:
    """Call ``POST /api/control/<action>`` on the running instance, after it has proved that it holds the token.

    Both requests use one connection (review R-4); the client never reconnects in between.
    """
    conn = http.client.HTTPConnection(LOOPBACK, info.port, timeout=timeout)
    try:
        _prove(conn, info)
        conn.auto_open = 0  # a closed connection raises instead of silently opening a new one
        status, doc, _ = _exchange(conn, "POST", f"/api/control/{action}", token=info.control_token, body=body)
    finally:
        conn.close()
    if not 200 <= status < 300:
        raise ControlError(f"POST /api/control/{action} failed: HTTP {status}")
    if not isinstance(doc, dict):
        raise ControlError(f"unexpected answer to {action}")
    return doc


def validate_launch_url(info: InstanceInfo, url: object) -> str:
    """The launch URL returned by the instance, if it is ``http://127.0.0.1:<port>/launch#<token>`` of that instance."""
    prefix = f"{info.url.rstrip('/')}{LAUNCH_PATH}#"
    if not isinstance(url, str) or not url.startswith(prefix) or TOKEN_PATTERN.fullmatch(url[len(prefix) :]) is None:
        raise ControlError(f"refusing a launch URL that does not point to {info.url}")
    return url


def validate_redirect_file(paths: InstancePaths, url: str, value: object) -> Path:
    """The redirect file named by the instance, if it is this instance's private file that opens ``url`` (F-02).

    It must lie in the state directory, have the instance's redirect file name, be a private regular file of this
    user and contain exactly the redirect page for ``url``. Anything else raises :class:`ControlError`.
    """
    if not isinstance(value, str):
        raise ControlError("the running Tabrion Studio returned no redirect file")
    path = Path(value)
    folder = paths.info.parent
    if (
        not path.is_absolute()
        or os.path.normpath(value) != value  # no "..", "." or doubled separators
        or redirect_file_pattern(paths.info.stem).fullmatch(path.name) is None
        or os.path.normcase(os.path.abspath(path.parent)) != os.path.normcase(os.path.abspath(folder))
    ):
        raise ControlError(f"refusing a redirect file outside the state directory: {value}")
    try:
        content = read_private_file(path)
    except (OSError, UnsafePathError) as exc:
        raise ControlError(f"refusing the redirect file {path}: {exc}") from exc
    if content is None or not hmac.compare_digest(content, redirect_page(url)):
        raise ControlError(f"refusing the redirect file {path}: unexpected content")
    return path


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
