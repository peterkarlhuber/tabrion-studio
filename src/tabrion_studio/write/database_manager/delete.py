"""Deleting a database in the Database Manager (STU-XDM-3; STU-ADR-0008 sections 2, 5, 7 and 9; TX-37, TX-39;
GS4-03, GS4-10, GS4-22).

Imported only by :mod:`tabrion_studio.write.routes` after it checked the ``database_manager`` flag (GS4-01).

**Who and where.** As every action of the area: this session's unlock of the connection with an **admin** profile,
re-checked at every use, and on PostgreSQL the admin role's privileges read again (section 6,
``studio.admin_over_privileged``). Only when the unlock's stored environment is ``dev`` or ``test`` (any other is
refused; PROD has no unlock at all, E-X7). A target that **any** PROD profile of the user's
store names is refused, whichever profile the request came through (the checks of STU-XDM-2, review L-1).

**What.**

* **PostgreSQL:** another database of the unlocked server, by its name, through ``tabrion.postgres.drop_database``
  on the admin session in autocommit. Never the maintenance databases (``postgres``, ``template0``, ``template1``,
  any template) and never the database the admin connection is connected to (``delete.maintenance``,
  ``delete.connected``; tabrion refuses them too). Studio's own read connections to the target are closed first.
* **SQLite:** the profile's own file (the unlocked connection's), with its ``-wal``, ``-shm`` and ``-journal``
  through ``tabrion.sqlite.delete_database_file`` (its exclusive-lock check, links never followed). The file's folder
  follows the folder rules of STU-CR-2 and STU-XDM-2 (the user's own, not writable by group or others, every folder
  above safe, none of them one of Studio's own folders by device and inode). Studio's own connections to the file
  are closed first, and with them the unlock. **Windows is refused** (``delete.platform``), as STU-CR-2 refuses
  creating a SQLite file there: its owner and mode rules cannot be read.

**Confirmations (D-13).** The exact name typed (``typed``; compared byte for byte, a name with a character that cannot
be seen is refused). ``force`` (PostgreSQL ``WITH (FORCE)``, which ends the other sessions) is a separate boolean,
never a default: it is accepted only when the server is PostgreSQL 13 or later and other sessions are on the target
right now, and only with its own typed confirmation (``force_typed``, the name again in a field of its own). It is
never retried after ``postgres.database_in_use``, and audited as its own field.

**A verified backup first (fail closed).** Before anything is deleted, :func:`.backups` makes a backup of the target
into Studio's backup folder (origin ``pre_delete``, its own ``dbm.backup`` records): PostgreSQL through the verified
``pg_dump`` runner (kept only after ``pg_restore --list`` read it), SQLite through ``tabrion.sqlite.backup`` and then
compared: the level-2 fingerprint of the copy must equal the source's. Any failure stops the delete
(``studio.dbm_delete_backup_unverified`` or the backup's own problem). There is no opt-out here (an owner decision:
STU-ADR-0008 section 5 would allow one on DEV).

**Binding.** PostgreSQL: the target's OID is read before the backup and again right before the drop; a different
database under the same name refuses (``studio.dbm_delete_target_changed``). SQLite: the file's device and inode
must be the unlock's at the check, after the backup and right before the delete (tabrion compares them once more
before its unlink).

**Errors (section 7).** Tabrion's refusals become fixed problems with fixed reasons; a driver error is a
``db.<kind>`` problem from ``classify`` with only ``safe_message`` in the log. Never ``str()`` of an exception, a path
or a server message.

**Audit (section 9).** ``run.start`` (no record, no delete) and ``run.result`` with the action ``dbm.delete``, the
target's name, the backup's file name and SHA-256, ``force`` and, on PostgreSQL, the number of other sessions seen.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import secrets
import sys
from typing import Any

import sqlalchemy
from tabrion.errors import DatabaseInUse, TabrionError, UnsafePath, UsageError, classify, safe_message

from tabrion_studio.config.schema import Profile
from tabrion_studio.write.connection import WriteError, admin_refusing_findings, file_identity, privileges_now
from tabrion_studio.write.database_manager import backups
from tabrion_studio.write.database_manager.backups import DriverFailure, Manager
from tabrion_studio.write.unlock import Unlock

logger = logging.getLogger("tabrion_studio.write")

ACTION_DELETE = "dbm.delete"
#: The stored environments a delete may act on (STU-ADR-0008 section 5): nothing else.
DELETE_ENVIRONMENTS = frozenset({"dev", "test"})
#: PostgreSQL's maintenance and template databases: never deleted (tabrion protects them too).
MAINTENANCE_DATABASES = frozenset({"postgres", "template0", "template1"})
#: PostgreSQL 13: ``DROP DATABASE ... WITH (FORCE)``.
FORCE_MIN_VERSION = (13,)
_WINDOWS = sys.platform == "win32"
_SESSIONS = (
    "SELECT pg_catalog.count(*)::pg_catalog.int8 FROM pg_catalog.pg_stat_activity a"
    " WHERE a.datname OPERATOR(pg_catalog.=) CAST(:name AS pg_catalog.name)"
    " AND a.pid OPERATOR(pg_catalog.<>) pg_catalog.pg_backend_pid()"
)
_TEMPLATE = (
    "SELECT d.datistemplate FROM pg_catalog.pg_database d"
    " WHERE d.datname OPERATOR(pg_catalog.=) CAST(:name AS pg_catalog.name)"
)


def _not_allowed(reason: str) -> WriteError:
    return WriteError("studio.dbm_delete_not_allowed", [("delete.target", f"delete.{reason}")])


def _unlock(manager: Manager, session: str, doc: dict[str, Any]) -> Unlock:
    unlock = backups._unlock(manager, session, doc.get("connection_id"))
    if unlock.environment not in DELETE_ENVIRONMENTS:
        raise _not_allowed("environment")
    return unlock


def _typed(raw: object, expected: str) -> None:
    if not isinstance(raw, str) or not secrets.compare_digest(raw.encode("utf-8"), expected.encode("utf-8")):
        raise WriteError("studio.dbm_delete_name_mismatch")


def _database_name(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise WriteError("studio.dbm_delete_name_invalid")
    if len(raw.encode("utf-8")) > 63 or raw != raw.strip() or backups._has_hidden(raw):
        raise WriteError("studio.dbm_delete_name_invalid")
    return raw


def _force(doc: dict[str, Any]) -> bool:
    value = doc.get("force", False)
    if type(value) is not bool:
        raise WriteError("studio.dbm_delete_name_invalid", [("delete.force", "delete.force_not_a_flag")])
    return value


# --- PostgreSQL -------------------------------------------------------------------------------------------------------


def _read_profile(manager: Manager, unlock: Unlock) -> Profile:
    _write, read, _password = manager.unlocks.credentials(unlock.write_name)
    return read


def _check_postgres_name(manager: Manager, unlock: Unlock, name: str) -> Profile:
    """The refusals before anything is read about the target: maintenance, connected, PROD."""
    if name in MAINTENANCE_DATABASES:
        raise _not_allowed("maintenance")
    read = _read_profile(manager, unlock)
    if name == read.database:
        raise _not_allowed("connected")
    try:
        backups._check_postgres_target(manager, unlock, read, name)
    except WriteError:
        raise _not_allowed("prod") from None
    return read


def _oid(unlock: Unlock, name: str) -> int | None:
    rows = unlock.connection.fetch(backups._DATABASE_OID, {"name": name})
    value = rows[0][0] if rows and rows[0] else None
    return value if type(value) is int else None


def _target_oid(unlock: Unlock, name: str) -> int:
    """The OID of an existing, non-template database ``name``; refuses a missing one or a template."""
    oid = _oid(unlock, name)
    if oid is None:
        raise WriteError("studio.dbm_delete_target_missing")
    rows = unlock.connection.fetch(_TEMPLATE, {"name": name})
    if rows and rows[0] and rows[0][0] is True:
        raise _not_allowed("maintenance")
    return oid


def _sessions(unlock: Unlock, name: str) -> int:
    rows = unlock.connection.fetch(_SESSIONS, {"name": name})
    value = rows[0][0] if rows and rows[0] else 0
    return value if type(value) is int else 0


def _force_offered(unlock: Unlock, sessions: int) -> bool:
    return sessions > 0 and unlock.connection.engine_version() >= FORCE_MIN_VERSION


def _close_studio_connections(manager: Manager, read: Profile, name: str) -> int:
    """Close Studio's own read connections to the database ``name`` of this server (their unlocks end with them), so
    that Studio itself is not a session on the target (and never one that ``force`` would end)."""
    here = backups._addresses(read.host, read.port)
    port = read.port or 5432
    closed = 0
    for connection in manager.registry.list():
        if connection.engine != "postgresql":
            continue
        try:
            url = sqlalchemy.make_url(connection.url)
        except Exception:  # noqa: S112 - a URL Studio cannot parse is not the target's
            continue
        if url.database != name or (url.port or 5432) != port:
            continue
        if backups._addresses(url.host, url.port) & here:
            manager.registry.close(connection.id)
            closed += 1
    return closed


def _drop(unlock: Unlock, name: str, oid: int, *, force: bool) -> None:
    """``tabrion.postgres.drop_database`` on the admin session in autocommit, after section 6 and the OID again on
    that very session. Errors as fixed problems or ``db.<kind>`` only."""
    import psycopg
    from psycopg.rows import tuple_row
    from tabrion.postgres import drop_database

    try:
        with unlock.connection.idle_autocommit() as connection:

            def fetch(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
                with psycopg.Cursor(connection, row_factory=tuple_row) as reading:
                    reading.execute(sql, params)
                    return reading.fetchall()

            _facts, findings = privileges_now(fetch)
            if admin_refusing_findings(findings):
                raise WriteError("studio.admin_over_privileged")
            rows = fetch(
                "SELECT d.oid::pg_catalog.int8 FROM pg_catalog.pg_database d"
                " WHERE d.datname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                (name,),
            )
            if not rows or rows[0][0] != oid:
                raise WriteError("studio.dbm_delete_target_changed")
            drop_database(connection, name, force=force)
    except WriteError:
        raise
    except DatabaseInUse:
        raise WriteError("studio.dbm_delete_in_use", [("delete.target", "delete.in_use")]) from None
    except UsageError as exc:
        reason = {
            "postgres.database_protected": "maintenance",
            "postgres.database_missing": "missing",
            "postgres.force_requires_13": "force_unsupported",
        }.get(exc.code if isinstance(exc.code, str) else "", "refused")
        if reason == "missing":
            raise WriteError("studio.dbm_delete_target_changed") from None
        raise _not_allowed(reason) from None
    except TabrionError as exc:
        if exc.classification is None:
            raise WriteError("studio.dbm_delete_failed", [("delete.target", "delete.refused")]) from None
        logger.info("database not dropped", extra={"event": "studio.dbm_delete_failed", "detail": safe_message(exc)})
        raise DriverFailure(exc.classification) from None
    except psycopg.Error as exc:
        classification = classify(exc)
        logger.info(
            "database not dropped",
            extra={"event": "studio.dbm_delete_failed", "detail": safe_message(classification)},
        )
        raise DriverFailure(classification) from None


def _preview_postgres(manager: Manager, unlock: Unlock, doc: dict[str, Any]) -> dict[str, Any]:
    name = _database_name(doc.get("name"))
    backups._check_admin(manager, unlock)
    _check_postgres_name(manager, unlock, name)
    _target_oid(unlock, name)
    sessions = _sessions(unlock, name)
    return {
        "engine": "postgresql",
        "target": name,
        "environment": unlock.environment,
        "sessions": sessions,
        "force_offered": _force_offered(unlock, sessions),
        "backup_required": True,
    }


def _delete_postgres(manager: Manager, unlock: Unlock, doc: dict[str, Any]) -> dict[str, Any]:
    name = _database_name(doc.get("name"))
    force = _force(doc)
    _typed(doc.get("typed"), name)
    backups._check_admin(manager, unlock)
    read = _check_postgres_name(manager, unlock, name)
    oid = _target_oid(unlock, name)
    if force:
        _typed(doc.get("force_typed"), name)
    closed = _close_studio_connections(manager, read, name)
    sessions = _sessions(unlock, name)
    if force and not _force_offered(unlock, sessions):
        raise _not_allowed("force_not_offered")
    if sessions and not force:
        # Fail early, before the backup; tabrion checks again right before the statement.
        raise WriteError("studio.dbm_delete_in_use", [("delete.target", "delete.in_use")])
    # The verified backup first (fail closed): pg_dump, then pg_restore --list of the archive.
    made = backups._backup_postgres(manager, unlock, database=name, origin="pre_delete")["backup"]
    if _oid(unlock, name) != oid:
        raise WriteError("studio.dbm_delete_target_changed")
    start = {
        "objects": [name],
        "target": name,
        "pre_backup": made["file"],
        "sha256": made["sha256"],
        "force": force,
        "sessions": sessions,
    }
    with backups._audited(manager, unlock, ACTION_DELETE, start) as outcome:
        _drop(unlock, name, oid, force=force)
        outcome.update(result="deleted")
    logger.info("database deleted", extra={"event": "studio.dbm_delete"})
    return {
        "engine": "postgresql",
        "target": name,
        "pre_backup": made["file"],
        "force": force,
        "connections_closed": closed,
        "audit_pending": outcome["audit_pending"],
    }


# --- SQLite ---------------------------------------------------------------------------------------------------------


def _sqlite_file(manager: Manager, unlock: Unlock) -> tuple[Path, tuple[int, int]]:
    """The unlocked connection's file, checked: still the unlock's, not a PROD profile's, in a folder that passes
    the folder rules and is none of Studio's own. Windows is refused (STU-CR-2)."""
    if _WINDOWS:
        raise _not_allowed("platform")
    file = unlock.connection.database_file
    if file is None:  # pragma: no cover - a SQLite write connection always has its file
        raise WriteError("studio.write_target_changed")
    identity = file_identity(file)
    if unlock.connection.identity != identity:
        raise WriteError("studio.dbm_delete_target_changed")
    try:
        backups._check_sqlite_target(manager, identity)
    except WriteError:
        raise _not_allowed("prod") from None
    reserved = (*manager.reserved, *([] if manager.store.path is None else [manager.store.path]))
    try:
        backups._check_new_file_folder(file.parent, reserved)
    except WriteError as exc:
        reason = exc.issues[0][1].split(".", 1)[1] if exc.issues else "folder_unsafe"
        raise _not_allowed(reason) from None
    return file, identity


def _preview_sqlite(manager: Manager, unlock: Unlock) -> dict[str, Any]:
    file, _identity = _sqlite_file(manager, unlock)
    return {
        "engine": "sqlite",
        "target": file.name,
        "environment": unlock.environment,
        "sessions": None,
        "force_offered": False,
        "backup_required": True,
    }


def _verified_sqlite_backup(manager: Manager, unlock: Unlock) -> dict[str, Any]:
    """The backup of the file, then its check: the copy's level-2 fingerprint equals the source's (which did not
    change while it was copied). No such fingerprint, no delete."""
    made: dict[str, Any] = backups._backup_sqlite(manager, unlock, origin="pre_delete")["backup"]
    expected = made.get("fingerprint")
    folder = manager.store.path
    copy = (
        None
        if folder is None
        else backups._sqlite_file_fingerprint(folder / made["file"], manager.unlocks.config.limits())
    )
    if not isinstance(expected, str) or copy != expected:
        logger.warning("delete refused: the backup could not be verified", extra={"event": "studio.dbm_delete_backup"})
        raise WriteError("studio.dbm_delete_backup_unverified")
    return made


def _delete_sqlite(manager: Manager, unlock: Unlock, doc: dict[str, Any]) -> dict[str, Any]:
    if doc.get("force", False) is not False:
        raise _not_allowed("force_not_offered")
    file, identity = _sqlite_file(manager, unlock)
    _typed(doc.get("typed"), file.name)
    made = _verified_sqlite_backup(manager, unlock)
    start = {
        "objects": [file.name],
        "target": file.name,
        "pre_backup": made["file"],
        "sha256": made["sha256"],
        "force": False,
    }
    from tabrion.sqlite import delete_database_file

    with backups._audited(manager, unlock, ACTION_DELETE, start) as outcome:
        if file_identity(file) != identity:
            raise WriteError("studio.dbm_delete_target_changed")
        # Studio's own pools on this file are closed first (and the unlock with its connection).
        closed = backups._close_connections_to(manager, identity)
        unlock.connection.close()
        manager.unlocks.lock(unlock.session, unlock.connection_id)
        try:
            delete_database_file(file)
        except DatabaseInUse:
            raise WriteError("studio.dbm_delete_in_use", [("delete.target", "delete.in_use")]) from None
        except (UnsafePath, UsageError):
            raise WriteError("studio.dbm_delete_failed", [("delete.target", "delete.refused")]) from None
        except FileNotFoundError:
            raise WriteError("studio.dbm_delete_target_changed") from None
        except Exception:
            logger.warning("delete failed", extra={"event": "studio.dbm_delete_failed"})
            raise WriteError("studio.dbm_delete_failed", [("delete.target", "delete.failed")]) from None
        if os.path.lexists(file):  # pragma: no cover - tabrion deletes it or raises
            raise WriteError("studio.dbm_delete_failed", [("delete.target", "delete.failed")])
        outcome.update(result="deleted")
    logger.info("database file deleted", extra={"event": "studio.dbm_delete"})
    return {
        "engine": "sqlite",
        "target": file.name,
        "pre_backup": made["file"],
        "force": False,
        "connections_closed": closed,
        "audit_pending": outcome["audit_pending"],
    }


# --- the actions ----------------------------------------------------------------------------------------------------


def preview(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """What a delete through this unlock would do, checked as far as it can be without changing anything: the
    target's name to type, the other sessions on it (PostgreSQL; a hint that can change) and whether ``force`` is
    offered. Changes nothing."""
    unlock = _unlock(manager, session, doc)
    if unlock.connection.engine == "postgresql":
        return _preview_postgres(manager, unlock, doc)
    if doc.get("name") is not None:
        raise _not_allowed("target")  # SQLite: only the profile's own file, never a name from the request
    return _preview_sqlite(manager, unlock)


def delete(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Delete the database (see the module text). Raises :class:`WriteError` or :class:`DriverFailure`."""
    unlock = _unlock(manager, session, doc)
    if unlock.connection.engine == "postgresql":
        return _delete_postgres(manager, unlock, doc)
    if doc.get("name") is not None:
        raise _not_allowed("target")
    return _delete_sqlite(manager, unlock, doc)
