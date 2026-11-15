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
#: The deepest nesting a configuration file may have: arrays and inline tables, plus the extra levels a dotted key or
#: table header makes (``a.b.c`` is two below ``a``). No Studio file needs more than a few
#: levels; a deeper file is refused before ``tomllib`` (a recursive parser) runs, so that it never recurses to the
#: interpreter's limit. A finalizer the garbage collector runs there (an event loop's ``__del__``, say) would fail
#: with its own ``RecursionError``, outside any ``except``.
MAX_NESTING = 64
#: Where the nesting scan looks next: a comment, a string or a bracket.
_NESTING_MARK = re.compile(r"[#\"'\[\]{}.=,\n]")
_BASIC_STRING_MARK = re.compile(r'["\\\n]')


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
    if _nesting(text) > MAX_NESTING:
        raise ConfigFileError("config.too_deep")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        match = _POSITION.search(str(exc))
        raise ConfigFileError("config.syntax", int(match.group(1)) if match else None) from None
    except RecursionError:
        # 1.8 KB of nested arrays is enough (review T-3): invalid, never an HTTP 500 with a 160 KB traceback. The scan
        # above refuses such a file first; this stays as the last line.
        raise ConfigFileError("config.too_deep") from None


def _nesting(text: str) -> int:
    """How deep tomllib would nest the file: the brackets and braces open around a point, plus the dots of the keys
    that lead there (the current table header's, the key whose value holds each open bracket or brace, and the key
    being read). A table header counts one level (two for ``[[...]]``) and its dots. Comments and strings are skipped,
    so a dot or bracket inside a quoted key or a string does not count, nor does a dot in a value (``1.5``).

    One pass, each step moving forward: linear in the text. It stops as soon as the depth passes ``MAX_NESTING`` (and
    returns it), and at a string that is not closed (tomllib refuses the file there, before any deeper nesting)."""
    owners: list[tuple[str, int]] = []  # each open bracket or brace, with the dots of the key that holds it
    header = 0  # levels of the current table header
    header_open = 0  # 1 or 2 while inside "[...]" or "[[...]]"
    in_key = True  # reading a key: at a line's start at the top level, or after "{" or "," in an inline table
    dots = 0  # the dots of the key being read, or of the key whose value follows
    deepest = at = 0
    while (found := _NESTING_MARK.search(text, at)) is not None:
        mark, start = found.group(), found.start()
        at = start + 1
        if mark == "#":
            end = text.find("\n", start)
            at = len(text) if end < 0 else end  # the newline is read next
        elif mark in "\"'":
            at = _string_end(text, start, mark)
            if at < 0:
                break
        elif header_open:
            if mark == ".":
                header += 1
            elif mark == "]":
                at += header_open - 1
                header_open = 0
                in_key = False
        elif mark == "[" and in_key and not owners:
            header_open = 2 if text.startswith("[[", start) else 1
            header = header_open
            at += header_open - 1
        elif mark == "." and in_key:
            dots += 1
        elif mark == "=":
            in_key = False
        elif mark in "[{":
            owners.append((mark, dots))
            in_key, dots = mark == "{", 0
        elif mark in "]}":
            if owners:
                owners.pop()
            in_key, dots = False, 0
        elif mark == ",":
            in_key, dots = bool(owners) and owners[-1][0] == "{", 0
        elif mark == "\n" and not owners:
            in_key, dots = True, 0
        deepest = max(deepest, header + len(owners) + sum(held for _, held in owners) + dots)
        if deepest > MAX_NESTING:
            break
    return deepest


def _string_end(text: str, start: int, quote: str) -> int:
    """Where the string that starts at ``start`` ends (after its closing quotes), or -1 when it is not closed."""
    if text.startswith(quote * 3, start):
        at = start + 3
        while True:
            end = text.find(quote * 3, at)
            if end < 0:
                return -1
            escapes = 0
            while quote == '"' and text[end - 1 - escapes] == "\\":
                escapes += 1
            if escapes % 2 == 0:
                end += 3
                for _ in range(2):  # up to two more quotes belong to the text
                    if text.startswith(quote, end):
                        end += 1
                return end
            at = end + 1
    if quote == "'":
        end = text.find("'", start + 1)
        line = text.find("\n", start + 1)
        return -1 if end < 0 or 0 <= line < end else end + 1
    at = start + 1
    while (found := _BASIC_STRING_MARK.search(text, at)) is not None:
        if found.group() == "\n":
            return -1
        if found.group() == "\\":
            at = found.start() + 2
            continue
        return found.start() + 1
    return -1
