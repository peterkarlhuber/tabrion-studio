"""Private files in the per-user state directory (03 section 4.3, threat T-04).

Directories are created with mode 0700 and files with mode 0600; symlinks are refused on every platform that
supports ``O_NOFOLLOW``. On Windows the modes are not enforced by the OS: the state directory lives in the user's
profile (``%LOCALAPPDATA%``), whose ACL already limits access to that user.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import os
from pathlib import Path
import stat
import sys
import time
from typing import TypeVar

import platformdirs

T = TypeVar("T")

APP_NAME = "tabrion-studio"
STATE_DIR_ENV = "TABRION_STUDIO_STATE_DIR"

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_POSIX = sys.platform != "win32"


def _retry_on_sharing_violation(action: Callable[[], T]) -> T:
    """Run ``action``; on Windows retry briefly while another process has the file open (sharing violation)."""
    for _ in range(20):
        try:
            return action()
        except PermissionError:
            if _POSIX:
                raise
            time.sleep(0.05)
    return action()


class UnsafePathError(RuntimeError):
    """A state path is a symlink, belongs to another user or is readable by others."""


def default_state_dir() -> Path:
    """``$TABRION_STUDIO_STATE_DIR`` if set, else the platform's per-user state directory."""
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override)
    return Path(platformdirs.user_state_dir(APP_NAME, appauthor=False))


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) with mode 0700 and check that it is a real directory owned by this user."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafePathError(f"the state directory is not a plain directory: {path}")
    if _POSIX:
        if info.st_uid != os.getuid():
            raise UnsafePathError(f"the state directory belongs to another user: {path}")
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(path, 0o700)
    return path


def write_private_file(path: Path, data: bytes) -> None:
    """Write ``data`` atomically to ``path`` with mode 0600 (temporary file in the same directory, then replace)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _retry_on_sharing_violation(lambda: os.replace(tmp, path))
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def read_private_file(path: Path) -> bytes | None:
    """The content of ``path``, or None if it is missing. Raises :class:`UnsafePathError` for an unsafe file."""
    if not _NOFOLLOW and path.is_symlink():
        # Windows has no O_NOFOLLOW; the state directory is per-user, so the check before opening suffices there.
        raise UnsafePathError(f"refusing to follow a symlink: {path}")
    try:
        fd = _retry_on_sharing_violation(lambda: os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if path.is_symlink():
            raise UnsafePathError(f"refusing to follow a symlink: {path}") from exc
        raise
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePathError(f"not a regular file: {path}")
        if _POSIX and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise UnsafePathError(f"the file belongs to another user or is accessible by others: {path}")
        return fh.read()


def remove_file(path: Path) -> None:
    """Remove ``path`` if it exists."""
    with contextlib.suppress(FileNotFoundError):
        _retry_on_sharing_violation(path.unlink)
