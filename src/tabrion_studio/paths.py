"""Private files in the per-user state directory (03 section 4.3, threat T-04).

Directories are created with mode 0700 and files with mode 0600; symlinks are refused on every platform that
supports ``O_NOFOLLOW``.

An existing state directory is checked, never repaired behind the user's back (review F-10): one that another user
owns, or that the group or others may write to, is refused, because another user may already have placed files in
it. Read and search bits for group and others are removed, since nothing secret has been written yet.

On Windows the modes are not enforced by the OS. There the state directory and each of its parents up to the user's
profile must not be a symlink, junction or other reparse point, and a directory outside the profile is accepted only
if its access control list is private to the user (review F-07, :mod:`tabrion_studio._winsec`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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
LOG_DIR_ENV = "TABRION_STUDIO_LOG_DIR"
CONFIG_DIR_ENV = "TABRION_STUDIO_CONFIG_DIR"

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


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class UnsafePathError(RuntimeError):
    """A state path is a symlink, belongs to another user or is readable by others."""


def default_state_dir() -> Path:
    """``$TABRION_STUDIO_STATE_DIR`` if set, else the platform's per-user state directory."""
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override)
    return Path(platformdirs.user_state_dir(APP_NAME, appauthor=False))


def default_log_dir(state_dir: Path | None = None) -> Path:
    """``$TABRION_STUDIO_LOG_DIR`` if set; else ``<state_dir>/logs`` for a state directory given by option or
    environment; else the platform's per-user log directory (03 section 4.3)."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override)
    if state_dir is not None:
        return state_dir / "logs"
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def default_config_dir(state_dir: Path | None = None) -> Path:
    """``$TABRION_STUDIO_CONFIG_DIR`` if set; else ``<state_dir>/config`` for a state directory given by option or
    environment; else the platform's per-user configuration directory (03 section 4.3)."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    if state_dir is not None:
        return state_dir / "config"
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) with mode 0700 and check that only this user can use it.

    Raises :class:`UnsafePathError` for a symlink or junction, a directory owned by another user or one that the group
    or others may write to (POSIX), and on Windows for a directory outside the user's profile without a private ACL.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
        raise UnsafePathError(f"the state directory is not a plain directory: {path}")
    if _POSIX:
        if info.st_uid != os.getuid():
            raise UnsafePathError(f"the state directory belongs to another user: {path}")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o022:
            raise UnsafePathError(
                f"the state directory can be written by other users (mode {mode:o}): {path}; "
                "remove it or run chmod 700 on it after checking its content"
            )
        if mode != 0o700:
            os.chmod(path, 0o700)  # only read or search bits for others were set
    else:
        _check_windows_dir(path)
    return path


def check_private_dir(path: Path) -> None:
    """Check, without creating or changing it, that an existing ``path`` passes :func:`ensure_private_dir`'s rules.

    A missing ``path`` passes (nothing can be read from it). For a reader that must not change the folder, such as
    ``tabrion-studio diagnose`` reading the trust records (L-4 of the PR #85 review). Raises :class:`UnsafePathError`.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
        raise UnsafePathError(f"the state directory is not a plain directory: {path}")
    if _POSIX:
        if info.st_uid != os.getuid():
            raise UnsafePathError(f"the state directory belongs to another user: {path}")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o022:
            raise UnsafePathError(f"the state directory can be written by other users (mode {mode:o}): {path}")
    else:
        _check_windows_dir(path)


def _is_reparse_point(info: os.stat_result) -> bool:
    """A Windows junction, mount point or other reparse point (``os.lstat`` does not report junctions as links)."""
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def is_within(path: Path, roots: Iterable[Path]) -> bool:
    """Whether ``path`` is one of ``roots`` or inside one (compared case-normalized, as Windows does)."""
    target = os.path.normcase(str(path))
    for root in roots:
        base = os.path.normcase(str(root)).rstrip("\\/")
        if base and (target == base or target.startswith(base + os.sep)):
            return True
    return False


def profile_roots() -> list[Path]:
    """The user's profile folders (Windows): ``%USERPROFILE%`` and ``%LOCALAPPDATA%``, as given and resolved."""
    roots: list[Path] = []
    for name in ("USERPROFILE", "LOCALAPPDATA"):
        value = os.environ.get(name)
        if value:
            roots += [Path(os.path.abspath(value)), Path(value).resolve()]
    return roots


def _check_windows_dir(path: Path) -> None:
    roots = profile_roots()
    root_keys = {os.path.normcase(str(root)) for root in roots}
    absolute = Path(os.path.abspath(path))
    # The directory itself and every folder between it and the profile root must be plain directories: a junction
    # there could redirect the state into a folder that other users can read.
    for folder in (absolute, *absolute.parents):
        if folder != absolute and (os.path.normcase(str(folder)) in root_keys or not is_within(folder, roots)):
            break
        if _is_reparse_point(os.lstat(folder)):
            raise UnsafePathError(f"the state directory path contains a junction or link: {folder}")
    if is_within(path.resolve(), roots):
        return
    if sys.platform == "win32":
        from tabrion_studio._winsec import dacl_problem

        problem = dacl_problem(path)
        if problem is not None:
            raise UnsafePathError(
                f"the state directory is outside your user profile and not private: {path} ({problem}); "
                "use a folder in your profile or restrict its access to your account"
            )


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
