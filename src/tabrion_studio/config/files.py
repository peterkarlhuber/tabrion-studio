"""Reading configuration files without following links (STU-006, T-08).

A project file comes from a repository that may be hostile (AT4). It is read only if it and the folder it lies in are
plain (no symlink, junction or other reparse point), it is a regular file of at most :data:`MAX_FILE_BYTES`, the file
opened is the file checked (same device and inode) and it is UTF-8. Every refusal is a :class:`ConfigFileError` with
a code; its message never contains the file's content.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys
import tomllib
from typing import Any

MAX_FILE_BYTES = 256 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_REPARSE = 0x400
_POSITION = re.compile(r"\(at line (\d+), column (\d+)\)")


class ConfigFileError(Exception):
    """A configuration file that cannot be used; ``code`` says why, ``line`` where (for a syntax error)."""

    def __init__(self, code: str, line: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.line = line


def _plain(info: os.stat_result) -> bool:
    return not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & _REPARSE)


def read_plain_file(path: Path, *, private: bool = False, folders: tuple[Path, ...] = ()) -> bytes | None:
    """The bytes of ``path``, or None if it does not exist.

    ``folders`` are checked to be plain directories as well (for example ``<project>/.tabrion``). With ``private``,
    the file must belong to this user and be inaccessible to others (POSIX; profiles.toml, 03 section 4.3).
    """
    for folder in folders:
        try:
            info = os.lstat(folder)
        except FileNotFoundError:
            return None
        if not _plain(info) or not stat.S_ISDIR(info.st_mode):
            raise ConfigFileError("config.unsafe_file")
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigFileError("config.unreadable") from exc
    if not _plain(before) or not stat.S_ISREG(before.st_mode):
        raise ConfigFileError("config.unsafe_file")
    if before.st_size > MAX_FILE_BYTES:
        raise ConfigFileError("config.too_large")
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY)
    except OSError as exc:
        raise ConfigFileError("config.unreadable") from exc
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(info.st_mode):
            raise ConfigFileError("config.unsafe_file")  # replaced between the check and the open
        if private and sys.platform != "win32" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ConfigFileError("config.not_private")
        data = fh.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ConfigFileError("config.too_large")
    return data


def parse_toml(data: bytes) -> dict[str, Any]:
    """Parse TOML; a syntax error keeps only its line number (tomllib's message is not passed on)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigFileError("config.encoding") from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        match = _POSITION.search(str(exc))
        raise ConfigFileError("config.syntax", int(match.group(1)) if match else None) from None
    except RecursionError:
        # 1.8 KB of nested arrays is enough (review T-3): invalid, never an HTTP 500 with a 160 KB traceback.
        raise ConfigFileError("config.too_deep") from None
