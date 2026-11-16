"""Folder rules shared by the write areas (POSIX): which folders on the way to a file Studio uses are safe.

A folder that someone else may write lets them replace what lies below it by something of theirs. So a folder above
a file Studio creates or runs must be a real folder (no link) of root or this user that neither group nor others may
write, unless its sticky bit is set (like ``/tmp``; OpenSSH's ``StrictModes``). Used by the Schema Designer's
database creation (STU-CR-2, review P128-01) and the Database Manager's tool runner and restore targets (STU-XDM-2,
review L-1 and I-2 of PR #136). Studio's own folders are compared by device and inode, never by name (P128-02).

:func:`remove_stale_runs` clears the tool runner's run folder when an area that uses the runner starts (GS4-06): it
lives here, not in :mod:`tabrion_studio.write.pgtools`, so that starting the Schema Designer loads no part of the
runner until a backup is made (GS4-01: with the Database Manager's flag off its runner and backup modules are not
loaded at start).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import contextlib
import logging
import os
from pathlib import Path
import posixpath
import shutil
import stat
import sys

from tabrion_studio.paths import UnsafePathError, check_private_dir

__all__ = [
    "GROUP_OTHER_WRITE",
    "RUN_FOLDER",
    "ancestor_is_safe",
    "folders_up",
    "identity",
    "remove_stale_runs",
    "reserved_identities",
]

logger = logging.getLogger("tabrion_studio.write")

GROUP_OTHER_WRITE = 0o022
#: The tool runner's folder of private run folders, in the folder of ``write-areas.toml``.
RUN_FOLDER = "tool-runs"


def identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def ancestor_is_safe(info: os.stat_result, uid: int) -> bool:
    """Whether a folder above a file Studio uses is safe to go through: a real folder (no link) of root or ``uid``
    that neither group nor others may write, unless its sticky bit is set (``/tmp``)."""
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    if info.st_uid not in (0, uid):
        return False
    return not (info.st_mode & GROUP_OTHER_WRITE) or bool(info.st_mode & stat.S_ISVTX)


def folders_up(folder: str) -> Iterator[str]:
    """``folder`` and every folder above it, up to the root."""
    current = folder
    while True:
        yield current
        parent = posixpath.dirname(current)
        if parent == current:
            return
        current = parent


def reserved_identities(reserved: Iterable[Path]) -> frozenset[tuple[int, int]]:
    """``(st_dev, st_ino)`` of each of Studio's own folders that exists (a link to one counts as the folder)."""
    found: set[tuple[int, int]] = set()
    for root in reserved:
        with contextlib.suppress(OSError, ValueError):
            found.add(identity(os.stat(root)))
    return frozenset(found)


def remove_stale_runs(state: Path | None) -> int:
    """Remove what a crashed tool run left in :data:`RUN_FOLDER` below ``state`` (passfiles among it); the number
    removed. Called once when an area that uses the runner starts, before any run."""
    if state is None or sys.platform == "win32":
        return 0
    folder = state / RUN_FOLDER
    try:
        check_private_dir(folder)
        names = os.listdir(folder)
    except (OSError, UnsafePathError):
        return 0
    removed = 0
    for name in names:
        path = folder / name
        with contextlib.suppress(OSError):
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
            removed += 1
    if removed:
        logger.warning("left-over tool run files removed", extra={"event": "studio.backup_tool_leftovers"})
    return removed
