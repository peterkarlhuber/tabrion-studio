"""SQL scripts in the project folder (03 section 2 C "SQL-Skripte öffnen/speichern (Projektordner, nur ``.sql``)",
08 sections 4.4 and 6 D-06, STU-020).

The page names a script by its path relative to the project folder, with ``/`` as the separator. Every path is judged
as text first and then on the file system, with the same helpers as a project's database path (threat T-08,
:mod:`tabrion_studio.config.locations`):

* only a relative path of plain names: no drive, no root, no ``\\``, no ``..``, no ``.`` component, no control or
  format character (bidi overrides, zero-width characters), no blank-looking character (a space other than U+0020
  and the ideographic space U+3000, the Hangul fillers, the braille blank, the combining grapheme joiner), no
  component that starts with ``.`` (hidden
  folders such as ``.git`` or ``.tabrion`` are not script folders), at most :data:`MAX_PATH_CHARS` characters;
* no name that Windows reads as something else, on every system alike (the same project is opened on all of them):
  a device name (``CON``, ``PRN``, ``AUX``, ``NUL``, ``COM0``-``COM9``, ``LPT0``-``LPT9``, also with superscript
  digits, ``CONIN$``, ``CONOUT$``; also with an extension, ``nul.sql``), a name that ends in ``.`` or a blank (Win32
  drops them, ``sub.`` names ``sub``) and a ``~`` followed by a digit (an 8.3 short name such as ``GIT~1`` can name
  ``.git``);
* the last name ends in ``.sql`` (any case) and the file is a regular file with one name: never a symbolic link and
  never a hard-linked file (its other name may lie outside the project);
* the path stays inside the project folder lexically *and* after following links one component at a time
  (``safe_realpath``, which never touches a network or device path); a folder that is a link is refused with
  ``studio.script_link``. On Windows that includes every reparse-point folder, also a OneDrive "Files On-Demand"
  placeholder (move the project out of the synced folder, or the scripts out of such a subfolder);
* a script has at most :data:`MAX_SCRIPT_BYTES` bytes and is UTF-8.
* files are read and written as bytes (``O_BINARY`` on Windows), never in text mode: content, size and digest are
  byte-exact on every system, and line endings are kept as they are. A UTF-8 byte order mark is left out of the
  content the page gets (its digest still covers it) and is written again when the page saves over that file.

**No check-then-use by name (07 TM-04).** On POSIX the file is reached from the project folder one name at a time
with ``openat``-style calls (``dir_fd``), each folder with ``O_DIRECTORY | O_NOFOLLOW`` and the file with
``O_NOFOLLOW``; the temporary file, the rename and the clean-up use the same folder descriptor. A folder swapped for a
link after the text checks is therefore never followed. Windows has no such calls in Python: there the folders are
checked with ``lstat`` (no link, no reparse point) and, after the file is opened, the open handle must be the file
that the checked path names (same volume and file index). What remains on Windows is a folder swapped between that
check and the rename of a save, which needs a program of the user's own running in the project folder.

**Listing** walks the project folder without following links, skips hidden folders, names Studio would refuse and the
usual tool folders (``node_modules``, virtual environments, caches), goes :data:`MAX_DEPTH` folders deep and stops
after :data:`MAX_SCRIPTS` scripts or :data:`MAX_VISITED` entries (``truncated`` says so); ``skipped_links`` counts the
folders and files left out because they are links (or reparse points), and ``skipped_invalid`` the folders and
``.sql`` files left out because Studio refuses their name (a hidden name, starting with ``.``, is not counted).

**Joiners and the ideographic space.** A ZWNJ (U+200C) or ZWJ (U+200D) is accepted only between two letters or
marks, so Persian, Indic and emoji names work; a ZWJ also between two symbols of an emoji sequence. It is never at
the start or end of a name, never doubled, never next to a space, a dot or a digit. U+3000 is accepted like U+0020,
never leading or trailing.

**Saving** is bound to what the page showed: ``digest`` is the SHA-256 of the file the page read (``null``: the page
expects no file there). A file that changed or appeared meanwhile is ``studio.script_stale`` or
``studio.script_exists`` unless the page sends ``overwrite: true`` after asking the user. The check and the replace run
under one lock, so two saves of one Studio never both pass the check. The folder must exist; Studio creates no
folders. The write is atomic: a temporary file with ``O_CREAT | O_EXCL | O_NOFOLLOW`` in the same folder, ``fsync``,
a rename over the script. A new script gets the usual mode of a project file (``0666`` less the umask, the files are
meant to be checked in); a replaced one keeps its mode. A refusal of the file system has its own problem:
``studio.script_permission_denied``, ``studio.script_locked`` (another program holds the file, Windows),
``studio.script_disk_full``, otherwise ``studio.script_write_failed``.

Nothing here runs a script, and nothing is logged with a path or a script's text.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import errno
import hashlib
import logging
import os
from pathlib import Path, PurePath
import re
import secrets
import stat
import sys
import threading
from typing import Any
import unicodedata

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
#: Device names of Windows, with or without an extension (``nul.sql`` names the device too).
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9¹²³]|LPT[0-9¹²³])", re.I)
#: Characters that show as nothing (or as a blank) but are not format characters: the combining grapheme joiner, the
#: Hangul fillers, the Khmer inherent vowels and the braille blank. With the format characters (category Cf) and every
#: space but U+0020 (category Zs) they would let two names look alike.
_BLANK = frozenset("\u034f\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0")
#: The spaces a name may hold (inside, never leading or trailing): U+0020 and the ideographic space of Japanese and
#: Chinese names (``売上　集計.sql``). Every other space (category Zs) is refused.
_SPACES = frozenset(" \u3000")
#: The zero-width non-joiner and joiner (Persian ``می‌خواهم``, Indic conjuncts, emoji sequences): accepted only
#: between two letters or marks (a ZWJ also inside an emoji sequence), see :func:`_joiner_in_place`. The page shows
#: them escaped (``\u200C``) wherever a script path is shown, so two names that differ only by one stay apart.
_ZWJ = "\u200d"
_JOINERS = frozenset("\u200c\u200d")
#: An 8.3 short name (``GIT~1``) can name another file or folder, a hidden one too.
_SHORT_NAME = re.compile(r"~[0-9]")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_POSIX = sys.platform != "win32"
#: Descriptor-anchored access (POSIX): every open, the rename and the clean-up go through a folder descriptor.
_ANCHORED = (
    _POSIX
    and bool(_NOFOLLOW and _DIRECTORY)
    and all(call in os.supports_dir_fd for call in (os.open, os.rename, os.unlink, os.stat))
)
_BOM = b"\xef\xbb\xbf"
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
#: Windows error numbers: a sharing or lock violation, a full disk.
_WIN_LOCKED = frozenset({32, 33})
_WIN_FULL = frozenset({39, 112})


class ScriptError(Exception):
    """A script request Studio refuses; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def script_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _letter_or_mark(char: str) -> bool:
    return unicodedata.category(char)[0] in "LM"


def _joiner_in_place(name: str, index: int) -> bool:
    """Whether the ZWNJ or ZWJ at ``index`` joins two letters or marks (a ZWJ also two symbols, as in an emoji
    sequence): never at the start or end of a name, never doubled, never next to a space, a dot or a digit."""
    if index == 0 or index == len(name) - 1:
        return False
    before, after = name[index - 1], name[index + 1]
    if _letter_or_mark(before) and _letter_or_mark(after):
        return True
    if name[index] != _ZWJ:
        return False
    # An emoji sequence: a symbol (or its variation selector or skin tone, which are marks or symbols) on both sides.
    pictured = ("So", "Sk", "Mn")
    return unicodedata.category(before) in pictured and unicodedata.category(after) == "So"


def _invisible(name: str, index: int) -> bool:
    char = name[index]
    if char in _BLANK:
        return True
    if char in _JOINERS:
        return not _joiner_in_place(name, index)
    category = unicodedata.category(char)
    return category == "Cf" or (category == "Zs" and char not in _SPACES)


def _plain_name(name: str) -> bool:
    """Whether one path component is a name Studio accepts on every system."""
    # ``strip()`` also removes U+3000, so the ideographic space has the rules of U+0020: never leading or trailing.
    if not name or name in (".", "..") or name.startswith(".") or name != name.strip() or name.endswith("."):
        return False
    if _CONTROL.search(name) or _FORBIDDEN.search(name) or _SHORT_NAME.search(name):
        return False
    if any(_invisible(name, index) for index in range(len(name))):
        return False  # bidi overrides and isolates, zero-width and blank characters: a name must read as what it is
    return _DEVICE.fullmatch(name.split(".", 1)[0].rstrip(" ")) is None


def _parts(path: object) -> tuple[str, ...]:
    """The names of a script path as text; :class:`ScriptError` ``studio.script_invalid`` for any refused form."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
        raise ScriptError("studio.script_invalid")
    if network_or_device(path) or path.startswith("/"):
        raise ScriptError("studio.script_invalid")
    parts = tuple(path.split("/"))
    if not all(_plain_name(part) for part in parts):
        raise ScriptError("studio.script_invalid")
    if not parts[-1].lower().endswith(".sql") or len(parts[-1]) <= 4:
        raise ScriptError("studio.script_invalid")
    return parts


def _os_code(exc: OSError) -> str:
    """The problem code of a refusal of the file system (the message, which may name a path, is never used)."""
    winerror = getattr(exc, "winerror", None)
    if winerror in _WIN_LOCKED:
        return "studio.script_locked"
    if winerror in _WIN_FULL or exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
        return "studio.script_disk_full"
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "studio.script_permission_denied"
    return "studio.script_write_failed"


def _link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


class _Folder:
    """The folder of a script: an open descriptor (POSIX) or its checked path (Windows)."""

    def __init__(self, path: Path, fd: int | None) -> None:
        self.path = path
        self.fd = fd

    def open(self, name: str, flags: int, mode: int = 0o666) -> int:
        if self.fd is not None:
            return os.open(name, flags | _CLOEXEC, mode, dir_fd=self.fd)
        return os.open(self.path / name, flags, mode)

    def lstat(self, name: str) -> os.stat_result:
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return os.lstat(self.path / name)

    def rename(self, source: str, target: str) -> None:
        if self.fd is not None:
            # rename(2) replaces the target atomically, like os.replace (which takes no folder descriptors).
            os.rename(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.path / source, self.path / target)

    def unlink(self, name: str) -> None:
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            os.unlink(self.path / name)

    def sync(self) -> None:
        if self.fd is not None:
            os.fsync(self.fd)

    def close(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None


def _open_folder(root: Path, names: tuple[str, ...]) -> _Folder:
    """The folder ``root/names``, reached one name at a time without following a link."""
    if not _ANCHORED:
        folder = root
        for name in names:
            folder = folder / name
            try:
                info = os.lstat(folder)
            except FileNotFoundError:
                raise ScriptError("studio.script_folder_missing") from None
            except OSError as exc:
                raise ScriptError(_os_code(exc)) from None
            if _link(info):
                raise ScriptError("studio.script_link")
            if not stat.S_ISDIR(info.st_mode):
                raise ScriptError("studio.script_invalid")
        return _Folder(folder, None)
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC
    try:
        fd = os.open(root, flags)
    except OSError:
        raise ScriptError("studio.script_invalid") from None
    for name in names:
        try:
            child = os.open(name, flags, dir_fd=fd)
        except FileNotFoundError:
            os.close(fd)
            raise ScriptError("studio.script_folder_missing") from None
        except OSError as exc:
            code = _os_code(exc) if isinstance(exc, PermissionError) else "studio.script_invalid"
            with contextlib.suppress(OSError):
                if stat.S_ISLNK(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                    code = "studio.script_link"
            os.close(fd)
            raise ScriptError(code) from None
        os.close(fd)
        fd = child
    return _Folder(root.joinpath(*names), fd)


def _read_file(folder: _Folder, name: str) -> tuple[bytes, os.stat_result] | None:
    """The bytes and the status of a regular file with one name, never through a link; None if there is none."""
    try:
        before = folder.lstat(name)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ScriptError(_os_code(exc)) from None
    if _link(before):
        raise ScriptError("studio.script_link")
    if not stat.S_ISREG(before.st_mode):
        raise ScriptError("studio.script_invalid")  # a folder, a FIFO, a device
    try:
        fd = folder.open(name, os.O_RDONLY | _NOFOLLOW | _BINARY | _NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ScriptError("studio.script_link") from None
        raise ScriptError(_os_code(exc) if isinstance(exc, PermissionError) else "studio.script_invalid") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ScriptError("studio.script_invalid")
        # Without folder descriptors (Windows) the open handle must be the file the checked name named.
        if folder.fd is None and (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise ScriptError("studio.script_link")
        if info.st_nlink > 1:
            raise ScriptError("studio.script_link")  # a hard link: its other name may lie outside the project
        if info.st_size > MAX_SCRIPT_BYTES:
            raise ScriptError("studio.script_too_large")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES:
        raise ScriptError("studio.script_too_large")
    return data, info


def _write_atomic(folder: _Folder, name: str, data: bytes, mode: int | None) -> None:
    temporary = f".{name}.{secrets.token_hex(6)}.tmp"
    fd = folder.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            if mode is not None and _POSIX:
                os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        folder.rename(temporary, name)
    except BaseException:
        with contextlib.suppress(OSError):
            folder.unlink(temporary)
        raise
    folder.sync()


@dataclass
class Scripts:
    """Scripts of one project folder. Blocking (call it off the event loop)."""

    project: Path
    #: Held from a save's digest check to its rename.
    _save_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def _root(self) -> Path:
        root = Path(os.path.abspath(self.project))
        real = safe_realpath(root)
        if real is None:
            raise ScriptError("studio.script_invalid")
        return real

    def _target(self, path: object) -> tuple[Path, tuple[str, ...]]:
        """The project folder and the names of a script inside it; refused when it leaves the project in any way."""
        parts = _parts(path)
        root = self._root()
        target = lexical_target("/".join(parts), PurePath(root))
        if target is None or not lexically_within(target, [PurePath(root)]):
            raise ScriptError("studio.script_invalid")
        real = safe_realpath(Path(target))
        if real is None or not lexically_within(PurePath(real), [PurePath(root)]) or real == root:
            raise ScriptError("studio.script_link")
        # The real path must be the path itself: a link anywhere on the way (also one that stays inside) is refused,
        # so what the page names is exactly what is read and written.
        if os.path.normcase(str(real)) != os.path.normcase(str(target)):
            raise ScriptError("studio.script_link")
        return root, parts

    # Listing

    def list(self) -> dict[str, Any]:
        root = self._root()
        found: list[dict[str, Any]] = []
        visited = 0
        links = 0
        invalid = 0
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
                if name.startswith("."):
                    continue  # a hidden name (.git, .tabrion): not a script folder, and not counted
                plain = _plain_name(name)
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not plain:
                    # Counted so the page can say why a script is missing; a link with such a name stays uncounted.
                    script = stat.S_ISREG(info.st_mode) and name.lower().endswith(".sql")
                    if not _link(info) and (stat.S_ISDIR(info.st_mode) or script):
                        invalid += 1
                    continue
                if _link(info):
                    links += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if len(prefix) + 1 < MAX_DEPTH and name not in SKIPPED_FOLDERS and not name.endswith(".egg-info"):
                        pending.append((Path(entry.path), (*prefix, name)))
                    continue
                if not stat.S_ISREG(info.st_mode) or not name.lower().endswith(".sql") or len(name) <= 4:
                    continue
                relative = "/".join((*prefix, name))
                if len(relative) > MAX_PATH_CHARS:
                    invalid += 1
                    continue
                found.append({"path": relative, "size": info.st_size, "modified_at": info.st_mtime})
        found.sort(key=lambda item: str(item["path"]).lower())
        return {"scripts": found, "truncated": truncated, "skipped_links": links, "skipped_invalid": invalid}

    # Reading

    def read(self, path: object) -> dict[str, Any]:
        root, parts = self._target(path)
        try:
            folder = _open_folder(root, parts[:-1])
        except ScriptError as exc:
            if exc.code == "studio.script_folder_missing":
                raise ScriptError("studio.script_not_found") from None
            raise
        try:
            current = _read_file(folder, parts[-1])
        finally:
            folder.close()
        if current is None:
            raise ScriptError("studio.script_not_found")
        data = current[0]
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
        root, parts = self._target(path)
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:  # a lone surrogate from the page
            raise ScriptError("studio.script_unreadable") from None
        if len(data) > MAX_SCRIPT_BYTES:
            raise ScriptError("studio.script_too_large")
        with self._save_lock:
            folder = _open_folder(root, parts[:-1])
            try:
                current = _read_file(folder, parts[-1])
                if not overwrite:
                    if current is not None and digest is None:
                        raise ScriptError("studio.script_exists")
                    if digest is not None and (current is None or script_digest(current[0]) != digest):
                        raise ScriptError("studio.script_stale")
                mode = None
                if current is not None:
                    mode = stat.S_IMODE(current[1].st_mode)
                    # The page never sees a byte order mark: the file keeps the one it had.
                    if current[0].startswith(_BOM) and not data.startswith(_BOM):
                        data = _BOM + data
                _write_atomic(folder, parts[-1], data, mode)
            except OSError as exc:
                raise ScriptError(_os_code(exc)) from None
            finally:
                folder.close()
        logger.info("script saved", extra={"event": "studio.script_saved"})
        return {"path": "/".join(parts), "digest": script_digest(data)}
