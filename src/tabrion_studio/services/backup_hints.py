"""Backup hints for one open connection (03 section 2 G, STU-025). Everything here reads; nothing is written.

Studio does not make or restore backups (03 section 1: no backup execution; ``VACUUM INTO`` is not in 1.0, T-13).
It only says what it can see of them:

* **SQLite** (the last ``backup`` artifact): the files next to the database that look like its backups.
  :func:`tabrion.sqlite.backup` has no naming rule, so a backup is a regular file (no link) in the database's folder,
  or in its ``backups`` or ``backup`` subfolder (a real folder, not a link to one: L-2 of ``stu-025-review.md``),
  whose name is the database's name without its last suffix followed by a separator ``.``, ``-`` or ``_``
  (``shop.db`` → ``shop-v2.db``, ``shop.db.bak``, ``shop_2026-11-12.db``; ``shopping.db`` does not count), that is
  not the database itself, its ``-wal``, ``-shm`` or ``-journal`` file or one of tabrion's ``.tmp`` files, and that
  starts with SQLite's file header. Another database whose name starts the same way (``shop-orders.db``, or any
  ``a_*`` file next to ``a.db``) counts too (L-4: accepted, documented). At most :data:`MAX_ENTRIES` entries of each
  folder are looked at (more give ``STU-BK-005``), only the first 16 bytes of a candidate are read, and the scan
  stops between entries at the time limit or a cancel (L-3). The database's last change is the newer modification
  time of the file and its ``-wal`` file.
* **PostgreSQL** (``archive_mode`` and ``pg_stat_archiver``): whether the server archives WAL and whether the
  archiver fails. Both are readable by every role; ``archive_command`` is not read (it may hold a secret, and only
  a superuser may read it). Backups made with ``pg_dump`` or a tool that does not archive WAL are not visible.

The answer is ``{"hints": [{code, severity, title, detail, remedy, source}], "facts": {...}}`` with codes
``STU-BK-*``. Like every finding, no hint and no fact names a file path, a file name, a host, a user or a value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import decimal
import os
from pathlib import Path
import stat
import sys
from typing import Any

__all__ = [
    "ARCHIVER_SQL",
    "MAX_ENTRIES",
    "OLD_BACKUP_S",
    "ScanStopped",
    "postgres_hints",
    "sqlite_backups",
    "sqlite_hints",
]

#: The most directory entries looked at in each folder (a folder of many files stays fast).
MAX_ENTRIES = 10_000
#: A backup this much older than the database's last change is a warning, not only a hint (7 days).
OLD_BACKUP_S = 7 * 24 * 3600.0
SQLITE_HEADER = b"SQLite format 3\x00"
_SUBFOLDERS = ("backups", "backup")
_SIDE_SUFFIXES = ("-wal", "-shm", "-journal", ".tmp")
_SEPARATORS = (".", "-", "_")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_POSIX = sys.platform != "win32"
#: Scan a folder through a descriptor opened with ``O_NOFOLLOW`` (POSIX); elsewhere by path after an ``lstat``.
_BY_DESCRIPTOR = _POSIX and os.scandir in os.supports_fd and os.open in os.supports_dir_fd and bool(_NOFOLLOW)
#: "Readable by the group or by others": the read bits only (L-5 of ``stu-025-review.md``).
_READABLE_BY_OTHERS = 0o044

#: One row: the archive mode, whether the server is a standby, and the archiver's counts and times (epoch seconds).
ARCHIVER_SQL = (
    "SELECT pg_catalog.current_setting('archive_mode'), pg_catalog.pg_is_in_recovery(), "
    "a.archived_count, a.failed_count, "
    "EXTRACT(EPOCH FROM a.last_archived_time), EXTRACT(EPOCH FROM a.last_failed_time), "
    "EXTRACT(EPOCH FROM a.stats_reset) "
    "FROM pg_catalog.pg_stat_archiver a"
)


def _hint(code: str, severity: str, title: str, detail: str, remedy: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "remedy": remedy, "source": "studio"}


def _span(seconds: float) -> str:
    """``seconds`` in words: minutes below two hours, hours below two days, days above."""
    if seconds < 7200:
        return f"{max(int(seconds // 60), 1)} minute(s)"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)} hour(s)"
    return f"{int(seconds // 86400)} day(s)"


def _is_backup_name(name: str, database: str, stem: str) -> bool:
    if name == database or name.endswith(_SIDE_SUFFIXES):
        return False
    return any(name.startswith(stem + separator) for separator in _SEPARATORS)


def _has_sqlite_header(path: str, *, folder: int | None = None) -> bool:
    """Whether the regular file ``path`` (a name in the folder ``folder`` when given) starts with SQLite's header.
    Opened with ``O_NOFOLLOW``, so a link swapped in is refused; ``O_NONBLOCK``, so a FIFO does not block."""
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | _NONBLOCK, dir_fd=folder)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):  # swapped for something else after the lstat
            return False
        return os.read(descriptor, len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False
    finally:
        os.close(descriptor)


class ScanStopped(Exception):
    """The time limit or a cancel came during the scan (L-3 of ``stu-025-review.md``)."""


def _folders(folder: Path) -> list[Path]:
    """The database's folder and those of its backup subfolders that are real folders, not links (L-2)."""
    found = [folder]
    for name in _SUBFOLDERS:
        try:
            info = os.lstat(folder / name)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode):  # a link to a folder is S_ISLNK here and is skipped
            found.append(folder / name)
    return found


def _open_folder(directory: Path) -> int | None:
    """A descriptor of ``directory`` opened without following a link (POSIX), or None where that is not possible."""
    if not _BY_DESCRIPTOR:
        return None
    return os.open(directory, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)


def sqlite_backups(database_file: Path, *, stop: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """What the folders around ``database_file`` hold: ``{"found", "newest_at", "exposed", "truncated",
    "unreadable"}``; ``exposed`` counts backups that the group or others may read (POSIX, mode ``0o044``).
    ``stop`` is asked before each entry; when it says yes, :class:`ScanStopped` ends the scan (L-3)."""
    folder = database_file.parent
    stem = database_file.stem or database_file.name
    found = exposed = 0
    newest: float | None = None
    truncated = unreadable = False
    for directory in _folders(folder):
        descriptor: int | None = None
        try:
            descriptor = _open_folder(directory)
            with os.scandir(directory if descriptor is None else descriptor) as entries:
                for count, entry in enumerate(entries):
                    if stop():
                        raise ScanStopped
                    if count >= MAX_ENTRIES:
                        truncated = True
                        break
                    if not _is_backup_name(entry.name, database_file.name, stem):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    if descriptor is None:
                        header = _has_sqlite_header(entry.path)
                    else:
                        header = _has_sqlite_header(entry.name, folder=descriptor)
                    if not header:
                        continue
                    found += 1
                    newest = info.st_mtime if newest is None else max(newest, info.st_mtime)
                    if _POSIX and info.st_mode & _READABLE_BY_OTHERS:
                        exposed += 1
        except OSError:
            unreadable = True
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return {"found": found, "newest_at": newest, "exposed": exposed, "truncated": truncated, "unreadable": unreadable}


def _changed_at(database_file: Path) -> float | None:
    times: list[float] = []
    for path in (database_file, database_file.with_name(database_file.name + "-wal")):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else None


def sqlite_hints(
    database_file: Path | None, *, clock: Callable[[], float], stop: Callable[[], bool] = lambda: False
) -> dict[str, Any]:
    """The backup hints of a SQLite database file (see the module text); ``stop`` as for :func:`sqlite_backups`."""
    if database_file is None:
        return {"hints": [], "facts": {"engine": "sqlite", "searched": False}}
    seen = sqlite_backups(database_file, stop=stop)
    changed = _changed_at(database_file)
    newest = seen["newest_at"]
    facts: dict[str, Any] = {
        "engine": "sqlite",
        "searched": True,
        "folders": ["the database's folder", *(f"{name}/" for name in _SUBFOLDERS)],
        "backups_found": seen["found"],
        "newest_backup_at": newest,
        "newest_backup_age_s": None if newest is None else max(clock() - newest, 0.0),
        "database_changed_at": changed,
        "backups_readable_by_others": seen["exposed"],
        "search_truncated": seen["truncated"],
    }
    hints: list[dict[str, str]] = []
    if seen["unreadable"]:
        hints.append(_hint(
            "STU-BK-004", "info", "Backups could not be searched",
            "Studio could not list the database's folder or one of its backup subfolders.",
            "Check the folder's permissions, or look for backups yourself.",
        ))  # fmt: skip
    if seen["truncated"]:
        hints.append(_hint(
            "STU-BK-005", "info", "Backup search cut short",
            f"A folder holds more than {MAX_ENTRIES} entries; Studio looked at the first {MAX_ENTRIES} of each "
            "(in no particular order), so a backup, even the newest one, may not have been seen.",
            "Keep backups in a backups subfolder of their own, or fewer files next to the database.",
        ))  # fmt: skip
    if seen["found"] == 0:
        hints.append(_hint(
            "STU-BK-001", "warning", "No backup found",
            "Studio found no SQLite file next to the database, or in its backups or backup subfolder, whose name "
            "starts with the database's name. Backups kept elsewhere are not visible to Studio.",
            "Back the database up with tabrion.sqlite.backup (online, 0600, checked with quick_check) and keep the "
            "copy on another disk as well.",
        ))  # fmt: skip
    elif newest is not None and changed is not None and changed > newest:
        behind = changed - newest
        hints.append(_hint(
            "STU-BK-002", "warning" if behind > OLD_BACKUP_S else "info", "Newest backup is older than the data",
            f"The newest backup is {_span(behind)} older than the database's last change.",
            "Make a new backup with tabrion.sqlite.backup, for example before every migration.",
        ))  # fmt: skip
    if seen["exposed"]:
        hints.append(_hint(
            "STU-BK-003", "warning", "Backups readable by other users",
            f"{seen['exposed']} backup file(s) can be read by the group or by others; a backup holds all the data.",
            "chmod 600 the backup files; tabrion.sqlite.backup creates them with 0600.",
        ))  # fmt: skip
    return {"hints": hints, "facts": facts}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | decimal.Decimal):
        return float(value)
    return None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def postgres_hints(row: tuple[Any, ...] | None, *, prod: bool) -> dict[str, Any]:
    """The backup hints from the one row of :data:`ARCHIVER_SQL`, or ``None`` when it could not be read."""
    if row is None or len(row) != 7:
        return {
            "hints": [_hint(
                "STU-BK-014", "info", "Archiving state not readable",
                "Studio could not read archive_mode or pg_stat_archiver with this role.",
                "Ask the database administrator how the database is backed up.",
            )],
            "facts": {"engine": "postgresql", "archive_mode": None},
        }  # fmt: skip
    mode, standby = str(row[0]), bool(row[1])
    archived, failed = _count(row[2]), _count(row[3])
    last_archived, last_failed, reset = _number(row[4]), _number(row[5]), _number(row[6])
    facts: Mapping[str, Any] = {
        "engine": "postgresql",
        "archive_mode": mode,
        "standby": standby,
        "archived_count": archived,
        "failed_count": failed,
        "last_archived_at": last_archived,
        "last_failed_at": last_failed,
        "stats_reset_at": reset,
    }
    hints: list[dict[str, str]] = []
    if mode == "off":
        hints.append(_hint(
            "STU-BK-010", "warning" if prod else "info", "WAL archiving is off",
            "archive_mode is off: the server keeps no WAL archive, so the database cannot be restored to a point in "
            "time. Studio cannot see backups made with pg_dump or with a tool that does not archive WAL.",
            "Make sure the database is backed up (pg_dump, pg_basebackup or a backup tool); for point-in-time "
            "recovery set archive_mode and an archive_command or archive_library.",
        ))  # fmt: skip
    elif standby and mode == "on":
        hints.append(_hint(
            "STU-BK-013", "info", "This standby does not archive",
            "The server is a standby with archive_mode on, which archives only on the primary.",
            "Check the archiving on the primary, or set archive_mode = always to archive on the standby too.",
        ))  # fmt: skip
    elif failed and (last_archived is None or (last_failed is not None and last_failed >= last_archived)):
        hints.append(_hint(
            "STU-BK-011", "error" if prod else "warning", "WAL archiving fails",
            f"The archiver failed {failed} time(s) since its statistics were reset, and the last attempt failed: "
            "the WAL archive has a gap and the server keeps WAL until archiving works again.",
            "Look at the server log for the archive_command or archive_library error and fix it.",
        ))  # fmt: skip
    elif archived == 0:
        hints.append(_hint(
            "STU-BK-012", "info", "Nothing archived yet",
            "archive_mode is on, but the archiver has not archived a WAL file since its statistics were reset.",
            "Check that archive_command or archive_library is set; pg_switch_wal() forces a first file.",
        ))  # fmt: skip
    return {"hints": hints, "facts": dict(facts)}
