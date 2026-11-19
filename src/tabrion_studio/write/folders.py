"""Folder rules shared by the write areas (POSIX): which folders on the way to a file Studio uses are safe.

A folder that someone else may write lets them replace what lies below it by something of theirs. So a folder above
a file Studio creates or runs must be a real folder (no link) of root or this user that neither group nor others may
write, unless its sticky bit is set (like ``/tmp``; OpenSSH's ``StrictModes``). Used by the Schema Designer's
database creation (STU-CR-2, review P128-01) and the Database Manager's tool runner and restore targets (STU-XDM-2,
review L-1 and I-2 of PR #136). Studio's own folders are compared by device and inode, never by name (P128-02).

:func:`check_new_file_path`, :func:`check_folder`, :func:`held_folder` and :func:`create_new_file` are the rules for a
**new file Studio writes for the user** in a folder the user names (the profile export of STU-CR-6, shared with the
profile export of STU-XDM-5): an absolute, normalized local path; a folder of this user that only this user may write,
reached through no link, below safe folders only and outside Studio's own folders (by device and inode); the file
created relative to the held folder with ``O_CREAT|O_EXCL|O_NOFOLLOW`` and mode ``0600``, so an existing file or link
is never overwritten. They are the rules of the SQLite file of STU-CR-2 (``database_manager/create.py``), raising
:class:`FolderRefused` with a reason instead of an area's problem, so each caller names its own problems. POSIX only:
the caller refuses Windows first (no ``dir_fd``, no check of a folder's access list).

:func:`remove_stale_runs` clears the tool runner's run folder when an area that uses the runner starts (GS4-06): it
lives here, not in :mod:`tabrion_studio.write.pgtools`, so that starting the Schema Designer loads no part of the
runner until a backup is made (GS4-01: with the Database Manager's flag off its runner and backup modules are not
loaded at start).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import contextlib
import errno
import logging
import os
from pathlib import Path
import posixpath
import shutil
import stat
import sys
from typing import Literal

from tabrion_studio.config.locations import network_or_device, safe_realpath
from tabrion_studio.paths import UnsafePathError, check_private_dir

__all__ = [
    "GROUP_OTHER_WRITE",
    "MAX_PATH",
    "RUN_FOLDER",
    "FolderRefused",
    "ancestor_is_safe",
    "check_folder",
    "check_new_file_path",
    "create_new_file",
    "folders_up",
    "held_folder",
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


# --- a new file for the user (STU-CR-6, STU-XDM-5) ------------------------------------------------------------------

#: The longest path of a new file (characters), as for the SQLite file of STU-CR-2.
MAX_PATH = 4096
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

Reason = Literal["path", "unsafe", "reserved", "exists"]


class FolderRefused(Exception):
    """Why Studio does not create the file: ``path`` (with a ``code``: the path text is not valid), ``unsafe`` (the
    folder or one above it), ``reserved`` (one of Studio's own folders) or ``exists`` (a file or link of that name).
    Holds no path."""

    def __init__(self, reason: Reason, code: str | None = None) -> None:
        super().__init__(reason if code is None else f"{reason}:{code}")
        self.reason: Reason = reason
        self.code = code


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool((getattr(info, "st_file_attributes", 0) or 0) & _REPARSE)


def _hidden(text: str) -> bool:
    from tabrion_studio.write.connection import has_hidden_character

    return has_hidden_character(text)


def check_new_file_path(raw: object, suffixes: tuple[str, ...]) -> tuple[str, str, str]:
    """``(path, folder, file name)`` of an absolute, normalized POSIX path whose name ends in one of ``suffixes``.

    Refused (:class:`FolderRefused` ``path`` with a code): not a text or empty (``required``), longer than
    :data:`MAX_PATH` (``too_long``), a control, format or other hidden character or a space other than U+0020
    (``control_character``), a network, device or drive-relative form (``network_path``), not absolute
    (``not_absolute``), not normalized or with ``..`` (``not_normalized``), no file name (``no_file_name``), another
    ending (``suffix``)."""
    if not isinstance(raw, str) or not raw:
        raise FolderRefused("path", "required")
    if len(raw) > MAX_PATH:
        raise FolderRefused("path", "too_long")
    if _hidden(raw):
        raise FolderRefused("path", "control_character")
    if network_or_device(raw):
        raise FolderRefused("path", "network_path")
    if not posixpath.isabs(raw):
        raise FolderRefused("path", "not_absolute")
    if posixpath.normpath(raw) != raw or ".." in raw.split("/"):
        raise FolderRefused("path", "not_normalized")
    folder, name = posixpath.split(raw)
    if not name or name in (".", ".."):
        raise FolderRefused("path", "no_file_name")
    if not name.lower().endswith(suffixes) or name.lower() in suffixes:
        raise FolderRefused("path", "suffix")
    return raw, folder, name


def _safe_above(folder: str, uid: int) -> None:
    for current in list(folders_up(folder))[1:]:
        try:
            info = os.lstat(current)
        except OSError:
            raise FolderRefused("unsafe") from None
        if not ancestor_is_safe(info, uid):
            raise FolderRefused("unsafe")


def check_folder(folder: str, reserved: frozenset[tuple[int, int]]) -> os.stat_result:
    """The folder's ``lstat`` if Studio may create a file in it (POSIX), else :class:`FolderRefused`.

    The folder is reached through no link (its real path is the path given), is a folder of this user that neither
    group nor others may write; every folder above it passes :func:`ancestor_is_safe`; neither it nor a folder above
    it is one of ``reserved`` (:func:`reserved_identities`), compared by device and inode."""
    real = safe_realpath(Path(folder))
    if real is None or str(real) != folder:
        raise FolderRefused("unsafe")
    try:
        info = os.lstat(folder)
    except OSError:
        raise FolderRefused("unsafe") from None
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise FolderRefused("unsafe")
    uid = os.geteuid()
    if info.st_uid != uid or info.st_mode & GROUP_OTHER_WRITE:
        raise FolderRefused("unsafe")
    _safe_above(folder, uid)
    for current in folders_up(folder):
        with contextlib.suppress(OSError):
            if identity(os.lstat(current)) in reserved:
                raise FolderRefused("reserved")
    return info


@contextlib.contextmanager
def held_folder(folder: str, checked: os.stat_result) -> Iterator[int]:
    """The checked folder, opened without following a link; :class:`FolderRefused` if it is not the folder checked."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(folder, flags)
    except OSError:
        raise FolderRefused("unsafe") from None
    try:
        if identity(os.fstat(descriptor)) != identity(checked):
            raise FolderRefused("unsafe")
        yield descriptor
    finally:
        os.close(descriptor)


def create_new_file(descriptor: int, name: str, data: bytes) -> tuple[int, int]:
    """Create ``name`` in the held folder with ``data`` and return its identity.

    ``O_CREAT|O_EXCL|O_NOFOLLOW`` relative to the folder's descriptor, exactly ``0600`` whatever the umask, written in
    full and synced. An existing name, a dangling link included, is :class:`FolderRefused` ``exists`` and is left
    alone; a failure after the create removes the new file (only if the name is still that file)."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        handle = os.open(name, flags, 0o600, dir_fd=descriptor)
    except FileExistsError:
        raise FolderRefused("exists") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # O_NOFOLLOW met a link of that name
            raise FolderRefused("exists") from None
        raise
    created: tuple[int, int] | None = None
    try:
        os.fchmod(handle, 0o600)
        created = identity(os.fstat(handle))
        view = memoryview(data)
        while view:
            written = os.write(handle, view)
            view = view[written:]
        os.fsync(handle)
    except BaseException:
        os.close(handle)
        with contextlib.suppress(OSError):
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if created is not None and identity(info) == created and stat.S_ISREG(info.st_mode):
                os.unlink(name, dir_fd=descriptor)
        raise
    os.close(handle)
    return created
