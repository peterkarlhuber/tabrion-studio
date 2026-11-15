"""Studio's backup folder and the metadata of each backup (STU-XDM-2; STU-ADR-0008 section 4; TX-35, TX-36; GS4-07).

**Where.** ``backups/<area>/`` in the folder of ``write-areas.toml`` (never a folder an environment variable chose):
the same layout as the Schema Designer's backups before a run (STU-CR-5, ``backups/schema-designer``), so a user
finds every backup Studio made in one place. The Database Manager's own are in ``backups/database-manager``. Both
folders are 0700 (:func:`tabrion_studio.paths.ensure_private_dir`: no link, owned by the user); every file is 0600,
created exclusively by Studio and never through a link.

**Names.** Studio makes them: ``<UTC time>-<8 random hex>.dump`` (PostgreSQL custom format) or ``.sqlite``, and next to
each ``<name>.json``, its metadata. A name never holds a database, table or file name of the user. The backup's ID in
the API is its file name; only a name of that pattern is looked up, and only in this folder (an archive from
anywhere else is never restored, TX-38).

**Metadata** (:func:`metadata`; written exclusively, never over an existing file): the time, the source's safe URL
(``tabrion.safe_url``: never the password), profile and environment, the engine, the database or file name, size and
SHA-256, the format, the tool's name, version and SHA-256 (PostgreSQL), the server version and, where the server lets
the role read it, its ``system_identifier``, the level-2 fingerprint of the source, the number of extensions in the
archive's table of contents, and the scope notice: a ``pg_dump`` archive **is not a complete backup** (global roles,
role memberships, tablespaces and other cluster objects are not in it; TX-35). No secret, no value.

**Before a restore** (:meth:`BackupStore.load`): the metadata file and the backup file are regular files of this user,
0600, not links; the metadata names this file, its size and SHA-256 match the file (an edited archive or an edited
metadata file is refused), and its engine matches the target's. **Retention:** Studio never deletes a backup of its
own accord; the listing shows how many there are and how much space they take (review I-3 of STU-XDM-1; the owner
decides a cleanup).
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any

from tabrion_studio.paths import UnsafePathError, ensure_private_dir
from tabrion_studio.write.connection import WriteError

__all__ = [
    "BACKUP_ROOT",
    "SCOPE_NOTICE_POSTGRES",
    "SCOPE_NOTICE_SQLITE",
    "BackupStore",
    "Loaded",
    "backup_id_valid",
]

#: Below the folder of ``write-areas.toml``; the Schema Designer's backups are in ``backups/schema-designer``.
BACKUP_ROOT = "backups"
AREA_FOLDER = "database-manager"
METADATA_VERSION = 1
#: The scope notice of every PostgreSQL backup (E-X5, TX-35): in the metadata, the page and the guide.
SCOPE_NOTICE_POSTGRES = (
    "Not a complete backup: a pg_dump archive holds one database. Global roles, role memberships, tablespaces and "
    "other cluster objects are not in it; back them up outside Studio."
)
SCOPE_NOTICE_SQLITE = "A copy of one SQLite database file, made through SQLite's online backup."
SUFFIXES = {"postgresql": ".dump", "sqlite": ".sqlite"}
FORMATS = {"postgresql": "custom", "sqlite": "sqlite"}
_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}\.(?:dump|sqlite)")
_HEX = re.compile(r"[0-9a-f]{64}")
_POSIX = sys.platform != "win32"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
#: The most backups a listing reads (the newest first); the count and size cover all of them.
MAX_LISTING = 500
_MAX_METADATA = 64 * 1024


def backup_id_valid(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _invalid(reason: str) -> WriteError:
    return WriteError("studio.dbm_backup_invalid", [("backup.file", f"backup.{reason}")])


def _private_regular(info: os.stat_result) -> bool:
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return False
    return not _POSIX or (info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077)


def _read(path: Path) -> bytes:
    """A metadata file: opened without following a link, a private regular file, small."""
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | _CLOEXEC)
    except FileNotFoundError:
        raise _invalid("metadata_missing") from None
    except OSError:
        raise _invalid("link") from None
    with os.fdopen(descriptor, "rb") as handle:
        if not _private_regular(os.fstat(handle.fileno())):
            raise _invalid("not_private")
        data = handle.read(_MAX_METADATA + 1)
    if len(data) > _MAX_METADATA:
        raise _invalid("metadata_invalid")
    return data


class Loaded:
    """A backup of the folder, checked: its path and its metadata (the file's SHA-256 is checked by the caller on
    the bytes it restores)."""

    def __init__(self, path: Path, meta: dict[str, Any]) -> None:
        self.path = path
        self.meta = meta

    @property
    def engine(self) -> str:
        return str(self.meta["engine"])

    @property
    def sha256(self) -> str:
        return str(self.meta["sha256"])

    @property
    def size(self) -> int:
        return int(self.meta["size"])


class BackupStore:
    """The backups of one area below ``state`` (the folder of ``write-areas.toml``; None: no backup, fail closed)."""

    def __init__(
        self,
        state: Path | None,
        area: str = AREA_FOLDER,
        clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
    ) -> None:
        self.state = state
        self.area = area
        self.clock = clock

    @property
    def path(self) -> Path | None:
        return None if self.state is None else self.state / BACKUP_ROOT / self.area

    def folder(self) -> Path:
        """The backup folder, made private now (0700, no link); ``studio.dbm_backup_failed`` if it cannot be."""
        folder = self.path
        if folder is None:
            raise WriteError("studio.dbm_backup_failed")
        try:
            ensure_private_dir(folder.parent)
            ensure_private_dir(folder)
        except (OSError, UnsafePathError):
            raise WriteError("studio.dbm_backup_failed") from None
        return folder

    def new_name(self, engine: str) -> str:
        return f"{self.clock().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}{SUFFIXES[engine]}"

    def now(self) -> str:
        return self.clock().isoformat(timespec="seconds").replace("+00:00", "Z")

    def write_metadata(self, name: str, doc: dict[str, Any]) -> None:
        """``<name>.json`` next to the backup, created exclusively (0600, no link; never over an existing file)."""
        folder = self.folder()
        data = json.dumps(doc, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
        target = folder / f"{name}.json"
        temporary = folder / f".{name}.json.{secrets.token_hex(8)}.partial"
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target, follow_symlinks=False)
        except OSError:
            raise WriteError("studio.dbm_backup_failed") from None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temporary)

    def discard(self, name: str) -> None:
        """Remove a backup file Studio just made and could not describe (its metadata failed): a backup without
        metadata is never listed or restored, so it would only take space. Never called on a listed backup."""
        folder = self.path
        if folder is not None and backup_id_valid(name):
            with contextlib.suppress(OSError):
                os.unlink(folder / name)

    def load(self, backup_id: object, engine: str) -> Loaded:
        """The backup ``backup_id`` of this folder for a restore into ``engine``, with the checks of the module text.
        :class:`WriteError` ``studio.dbm_backup_unknown`` or ``studio.dbm_backup_invalid``."""
        folder = self.path
        if folder is None or not backup_id_valid(backup_id):
            raise WriteError("studio.dbm_backup_unknown")
        name = str(backup_id)
        path = folder / name
        try:
            info = os.lstat(path)
        except OSError:
            raise WriteError("studio.dbm_backup_unknown") from None
        if stat.S_ISLNK(info.st_mode):
            raise _invalid("link")
        if not _private_regular(info):
            raise _invalid("not_private")
        try:
            meta = json.loads(_read(folder / f"{name}.json"))
        except ValueError:
            raise _invalid("metadata_invalid") from None
        if not isinstance(meta, dict) or meta.get("version") != METADATA_VERSION or meta.get("file") != name:
            raise _invalid("metadata_invalid")
        if meta.get("engine") not in SUFFIXES or not name.endswith(SUFFIXES[meta["engine"]]):
            raise _invalid("metadata_invalid")
        if meta.get("format") != FORMATS[meta["engine"]]:
            raise _invalid("metadata_invalid")
        if meta["engine"] != engine:
            raise _invalid("engine_mismatch")
        if not isinstance(meta.get("sha256"), str) or not _HEX.fullmatch(meta["sha256"]):
            raise _invalid("metadata_invalid")
        if type(meta.get("size")) is not int or meta["size"] != info.st_size:
            raise _invalid("changed")
        return Loaded(path, meta)

    def listing(self) -> dict[str, Any]:
        """The backups of the folder (newest first, at most :data:`MAX_LISTING`) with their metadata, and the count
        and total size of every backup file there (the size notice, review I-3). A file without valid metadata is
        counted but not listed. Never creates the folder."""
        folder = self.path
        items: list[dict[str, Any]] = []
        count = 0
        total = 0
        if folder is None:
            return {"backups": items, "count": 0, "total_bytes": 0}
        try:
            names = sorted((n for n in os.listdir(folder) if backup_id_valid(n)), reverse=True)
        except OSError:
            names = []
        for name in names:
            try:
                info = os.lstat(folder / name)
            except OSError:
                continue
            count += 1
            total += info.st_size
            if len(items) >= MAX_LISTING:
                continue
            try:
                meta = json.loads(_read(folder / f"{name}.json"))
            except (WriteError, ValueError):
                continue
            if isinstance(meta, dict) and meta.get("file") == name:
                items.append(_public(meta))
        return {"backups": items, "count": count, "total_bytes": total}


#: The metadata keys the page may see (all of them today; listed so that a later key is a decision).
_PUBLIC_KEYS = (
    "file",
    "created",
    "engine",
    "format",
    "origin",
    "size",
    "sha256",
    "source",
    "server_version",
    "tool",
    "fingerprint",
    "extensions",
    "complete",
    "scope_notice",
)


def _public(meta: dict[str, Any]) -> dict[str, Any]:
    """What the page lists: never the source's address (host or file path), only its database, profile and
    environment."""
    public = {key: meta.get(key) for key in _PUBLIC_KEYS}
    source = meta.get("source")
    if isinstance(source, dict):
        public["source"] = {key: source.get(key) for key in ("database", "profile", "environment")}
    return public


def metadata(
    *,
    name: str,
    created: str,
    engine: str,
    origin: str,
    size: int,
    sha256: str,
    source: dict[str, Any],
    server_version: str | None,
    system_identifier: str | None,
    tool: dict[str, Any] | None,
    fingerprint: str | None,
    extensions: int | None,
) -> dict[str, Any]:
    """The metadata of one backup (see the module text)."""
    return {
        "version": METADATA_VERSION,
        "file": name,
        "created": created,
        "engine": engine,
        "format": FORMATS[engine],
        "origin": origin,
        "size": size,
        "sha256": sha256,
        "source": source,
        "server_version": server_version,
        "system_identifier": system_identifier,
        "tool": tool,
        "fingerprint": fingerprint,
        "extensions": extensions,
        "complete": engine != "postgresql",
        "scope_notice": SCOPE_NOTICE_POSTGRES if engine == "postgresql" else SCOPE_NOTICE_SQLITE,
    }
