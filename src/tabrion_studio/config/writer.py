"""Writing Studio's own files: the user's ``profiles.toml`` and ``settings.toml`` (STU-008).

Studio 1.0 is read-only towards databases; the only files it writes are its own configuration files, and only in the
user's configuration folder (never the project's ``.tabrion/studio.toml``). Every write:

* serializes schema version 1 with a small, strict TOML writer (basic strings with every control character escaped;
  no dependency), then parses the result back and validates it, so Studio never writes a file it would not read;
* checks the configuration folder like the state folder (0700, owned by the user, no link; ``ensure_private_dir``);
* is bound to the bytes the page showed (``digest``): a file changed by hand meanwhile gives ``studio.config_stale``;
* is atomic: a temporary file created with ``O_CREAT | O_EXCL | O_NOFOLLOW`` and mode 0600 in the same folder,
  ``fsync``, then ``os.replace`` (which replaces a link at the target instead of following it), then ``fsync`` of
  the folder (POSIX).

Comments and unknown keys of a hand-written file are not kept; a file with errors is never rewritten
(``studio.config_invalid``), so nothing the user wrote is dropped silently.
"""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import math
import os
from pathlib import Path
import re
import sys
from typing import Any

from tabrion_studio.config.files import MAX_FILE_BYTES, ConfigFileError, parse_toml
from tabrion_studio.config.schema import SCHEMA_VERSION, Profile, Settings, validate_profiles, validate_settings
from tabrion_studio.paths import write_private_file

HEADER = "# Written by Tabrion Studio. Comments are not kept when Studio saves this file.\n"
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
PROFILE_KEYS = (
    "name",
    "engine",
    "environment",
    "path",
    "host",
    "port",
    "database",
    "user",
    "password",
    "sslmode",
    "read_only_role",
    "statement_timeout_ms",
)


class SaveError(Exception):
    """A save that Studio refuses; ``code`` is a problem code of the catalog."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def digest(data: bytes | None) -> str | None:
    """The SHA-256 of a file's bytes as the page sees it; None for a missing file."""
    return None if data is None else hashlib.sha256(data).hexdigest()


def toml_string(value: str) -> str:
    """A TOML basic string: quotes, backslashes and every control character escaped (no injection of keys)."""
    out = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F or char in "\u2028\u2029":
            out.append(f"\\u{code:04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return toml_string(value)
    raise TypeError("unsupported TOML value")


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else toml_string(key)


def _profile_items(profile: Profile) -> Iterable[tuple[str, Any]]:
    for key in PROFILE_KEYS:
        if key == "password":
            if profile.password is not None:
                ref = profile.password
                yield key, "keyring:" if ref.source == "keyring" else f"{ref.source}:{ref.target}"
            continue
        value = getattr(profile, key)
        # A derived sslmode (verify-full for a remote host) is written as well: the file then says what is used.
        if value is not None:
            yield key, value


def dump_profiles(profiles: list[Profile]) -> bytes:
    lines = [HEADER, f"version = {SCHEMA_VERSION}\n"]
    for profile in profiles:
        lines.append("\n[[profile]]\n")
        lines += [f"{_key(key)} = {_value(value)}\n" for key, value in _profile_items(profile)]
    return "".join(lines).encode("utf-8")


def dump_settings(settings: Settings) -> bytes:
    defaults = Settings()
    lines = [HEADER, f"version = {SCHEMA_VERSION}\n"]
    for key in ("language", "theme", "density", "log_sql"):
        value = getattr(settings, key)
        if value != getattr(defaults, key):
            lines.append(f"{key} = {_value(value)}\n")
    limits = [
        (key, getattr(settings, key))
        for key in ("max_rows", "statement_timeout_ms")
        if getattr(settings, key) != getattr(defaults, key)
    ]
    if limits:
        lines.append("\n[limits]\n")
        lines += [f"{key} = {_value(value)}\n" for key, value in limits]
    return "".join(lines).encode("utf-8")


def checked_profiles(profiles: list[Profile]) -> bytes:
    """``dump_profiles``, parsed back: the file must give exactly these profiles and no issue (else ValueError)."""
    data = dump_profiles(profiles)
    if len(data) > MAX_FILE_BYTES:
        raise SaveError("studio.config_too_large")  # Studio would not read it back
    try:
        parsed = validate_profiles(parse_toml(data))
    except ConfigFileError as exc:
        raise ValueError("the written profiles do not parse") from exc
    if parsed.issues or parsed.profiles != profiles:
        raise ValueError("the written profiles do not read back")
    return data


def checked_settings(settings: Settings) -> bytes:
    """``dump_settings``, parsed back: the file must give exactly these settings and no issue (else ValueError)."""
    data = dump_settings(settings)
    try:
        parsed, issues = validate_settings(parse_toml(data))
    except ConfigFileError as exc:
        raise ValueError("the written settings do not parse") from exc
    if issues or parsed != settings:
        raise ValueError("the written settings do not read back")
    return data


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_POSIX = sys.platform != "win32"


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically with mode 0600; the folder must already be checked (private)."""
    write_private_file(path, data)
    if _POSIX:
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW)
        try:
            os.fsync(fd)  # the rename itself survives a crash
        finally:
            os.close(fd)
