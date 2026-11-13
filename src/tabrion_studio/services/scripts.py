"""SQL scripts in the project folder (03 section 2 C "SQL-Skripte öffnen/speichern (Projektordner, nur ``.sql``)",
08 sections 4.4 and 6 D-06, STU-020).

The page names a script by its path relative to the project folder, with ``/`` as the separator. Every path is judged
as text first and then on the file system, with the same helpers as a project's database path (threat T-08,
:mod:`tabrion_studio.config.locations`):

* only a relative path of plain names: no drive, no root, no ``\\``, no ``..``, no ``.`` component, no control
  character, no component that starts with ``.`` (hidden folders such as ``.git`` or ``.tabrion`` are not script
  folders), at most :data:`MAX_PATH_CHARS` characters;
* the last name ends in ``.sql`` (any case) and the file is a regular file, never a link;
* the path stays inside the project folder lexically *and* after following links one component at a time
  (``safe_realpath``, which never touches a network or device path); a folder that is a link out of the project is
  refused;
* a script has at most :data:`MAX_SCRIPT_BYTES` bytes and is UTF-8 (a byte order mark is dropped when reading).

**Listing** walks the project folder without following links, skips hidden folders and the usual tool folders
(``node_modules``, virtual environments, caches), goes :data:`MAX_DEPTH` folders deep and stops after
:data:`MAX_SCRIPTS` scripts or :data:`MAX_VISITED` entries (``truncated`` says so).

**Saving** is bound to what the page showed: ``digest`` is the SHA-256 of the file the page read (``null``: the page
expects no file there). A file that changed or appeared meanwhile is ``studio.script_stale`` or
``studio.script_exists`` unless the page sends ``overwrite: true`` after asking the user. The folder must exist; Studio
creates no folders. The write is atomic: a temporary file with ``O_CREAT | O_EXCL | O_NOFOLLOW`` in the same folder,
``fsync``, ``os.replace``. A new script gets the usual mode of a project file (``0666`` less the umask, the files are
meant to be checked in); a replaced one keeps its mode.

Nothing here runs a script, and nothing is logged with a path or a script's text.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path, PurePath
import re
import secrets
import stat
import sys
from typing import Any

from tabrion_studio.config.locations import lexical_target, lexically_within, network_or_device, safe_realpath

__all__ = [
    "MAX_DEPTH",
    "MAX_PATH_CHARS",
    "MAX_SCRIPTS",
    "MAX_SCRIPT_BYTES",
    "MAX_VISITED",
    "ScriptError",
    "Scripts",
    "script_digest",
]

logger = logging.getLogger("tabrion_studio.scripts")

MAX_SCRIPT_BYTES = 1024 * 1024
MAX_PATH_CHARS = 512
MAX_DEPTH = 8
MAX_SCRIPTS = 1_000
MAX_VISITED = 20_000
#: Folders that hold tools' files, never a project's scripts.
SKIPPED_FOLDERS = frozenset({"node_modules", "__pycache__", "venv", "env", "site-packages", "dist", "build"})
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]")
_FORBIDDEN = re.compile(r'[\\:*?"<>|]')
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_POSIX = sys.platform != "win32"
_BOM = b"\xef\xbb\xbf"


class ScriptError(Exception):
    """A script request Studio refuses; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def script_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parts(path: object) -> tuple[str, ...]:
    """The names of a script path as text; :class:`ScriptError` ``studio.script_invalid`` for any refused form."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
        raise ScriptError("studio.script_invalid")
    if _CONTROL.search(path) or _FORBIDDEN.search(path) or network_or_device(path) or path.startswith("/"):
        raise ScriptError("studio.script_invalid")
    parts = tuple(path.split("/"))
    if any(not part or part in (".", "..") or part.startswith(".") or part != part.strip() for part in parts):
        raise ScriptError("studio.script_invalid")
    if not parts[-1].lower().endswith(".sql") or len(parts[-1]) <= 4:
        raise ScriptError("studio.script_invalid")
    return parts


@dataclass
class Scripts:
    """Scripts of one project folder. Blocking (call it off the event loop)."""

    project: Path

    def _root(self) -> Path:
        root = Path(os.path.abspath(self.project))
        real = safe_realpath(root)
        if real is None:
            raise ScriptError("studio.script_invalid")
        return real

    def _target(self, path: object) -> tuple[Path, tuple[str, ...]]:
        """The real path of a script inside the project; refused when it leaves the project in any way."""
        parts = _parts(path)
        root = self._root()
        target = lexical_target("/".join(parts), PurePath(root))
        if target is None or not lexically_within(target, [PurePath(root)]):
            raise ScriptError("studio.script_invalid")
        real = safe_realpath(Path(target))
        if real is None or not lexically_within(PurePath(real), [PurePath(root)]) or real == root:
            raise ScriptError("studio.script_invalid")
        # The real path must be the path itself: a link anywhere on the way (also one that stays inside) is refused,
        # so what the page names is exactly what is read and written.
        if os.path.normcase(str(real)) != os.path.normcase(str(target)):
            raise ScriptError("studio.script_invalid")
        return real, parts

    # Listing

    def list(self) -> dict[str, Any]:
        root = self._root()
        found: list[dict[str, Any]] = []
        visited = 0
        truncated = False
        pending: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
        while pending and not truncated:
            folder, prefix = pending.pop(0)
            try:
                with os.scandir(folder) as entries:
                    children = sorted(entries, key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in children:
                visited += 1
                if visited > MAX_VISITED or len(found) >= MAX_SCRIPTS:
                    truncated = True
                    break
                name = entry.name
                if name.startswith(".") or _CONTROL.search(name) or _FORBIDDEN.search(name):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode) and not entry.is_symlink() and not _reparse(info):
                    if len(prefix) + 1 < MAX_DEPTH and name not in SKIPPED_FOLDERS and not name.endswith(".egg-info"):
                        pending.append((Path(entry.path), (*prefix, name)))
                    continue
                if not stat.S_ISREG(info.st_mode) or not name.lower().endswith(".sql") or len(name) <= 4:
                    continue
                relative = "/".join((*prefix, name))
                if len(relative) > MAX_PATH_CHARS:
                    continue
                found.append({"path": relative, "size": info.st_size, "modified_at": info.st_mtime})
        found.sort(key=lambda item: str(item["path"]).lower())
        return {"scripts": found, "truncated": truncated}

    # Reading

    def read(self, path: object) -> dict[str, Any]:
        real, parts = self._target(path)
        data = _read_regular(real)
        if data is None:
            raise ScriptError("studio.script_not_found")
        try:
            text = (data[len(_BOM) :] if data.startswith(_BOM) else data).decode("utf-8")
        except UnicodeDecodeError:
            raise ScriptError("studio.script_unreadable") from None
        return {"path": "/".join(parts), "content": text, "digest": script_digest(data)}

    # Saving

    def save(self, path: object, content: object, digest: object, *, overwrite: object = False) -> dict[str, Any]:
        if not isinstance(content, str) or (digest is not None and not isinstance(digest, str)):
            raise ScriptError("studio.invalid_request")
        if not isinstance(overwrite, bool):
            raise ScriptError("studio.invalid_request")
        real, parts = self._target(path)
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:  # a lone surrogate from the page
            raise ScriptError("studio.script_unreadable") from None
        if len(data) > MAX_SCRIPT_BYTES:
            raise ScriptError("studio.script_too_large")
        folder = real.parent
        try:
            folder_info = os.lstat(folder)
        except FileNotFoundError:
            raise ScriptError("studio.script_folder_missing") from None
        if not stat.S_ISDIR(folder_info.st_mode) or stat.S_ISLNK(folder_info.st_mode) or _reparse(folder_info):
            raise ScriptError("studio.script_invalid")
        current = _read_regular(real)
        if not overwrite:
            if current is not None and digest is None:
                raise ScriptError("studio.script_exists")
            if digest is not None and (current is None or script_digest(current) != digest):
                raise ScriptError("studio.script_stale")
        mode = None
        if current is not None:
            mode = stat.S_IMODE(os.lstat(real).st_mode)
        _write_atomic(real, data, mode)
        logger.info("script saved", extra={"event": "studio.script_saved"})
        return {"path": "/".join(parts), "digest": script_digest(data)}


def _reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _read_regular(path: Path) -> bytes | None:
    """The bytes of a regular file that is no link; None if it does not exist. Refuses anything else."""
    if not _NOFOLLOW and os.path.islink(path):
        raise ScriptError("studio.script_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError:
        # ELOOP for a link (O_NOFOLLOW), EISDIR, EACCES ...
        raise ScriptError("studio.script_invalid") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ScriptError("studio.script_invalid")
        if info.st_size > MAX_SCRIPT_BYTES:
            raise ScriptError("studio.script_too_large")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES:
        raise ScriptError("studio.script_too_large")
    return data


def _write_atomic(path: Path, data: bytes, mode: int | None) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            if mode is not None and _POSIX:
                os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    if _POSIX:
        folder = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
