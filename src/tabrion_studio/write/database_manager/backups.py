"""Backup and restore in the Database Manager (STU-XDM-2; STU-ADR-0008 sections 2 to 4 and 9; TX-32..TX-38;
GS4-03..09, GS4-22).

Imported only by :mod:`tabrion_studio.write.routes` after it checked the ``database_manager`` flag (GS4-01).

**Who acts.** Every action needs this session's unlock of the connection with an **admin** profile (``kind =
"admin"``, which only the ``database_manager`` flag opens; :meth:`Unlocks.require_unlock`), re-checked at every use
(flag, expiry, the stored environment, the coupling, the confirmed pair). PROD has no unlock (E-X7), so no action of
this package reaches a PROD profile; the backup of a PROD database with the read profile (STU-ADR-0008 section 2) is
not built yet (an open point: refused, fail closed). On PostgreSQL the admin role's privileges are read again on its
session before each action: SUPERUSER, BYPASSRLS, REPLICATION, a server-file role or CREATEROLE before 16
(:data:`ADMIN_REFUSING_FINDINGS`, directly or through a reachable role) refuse with
``405 studio.admin_over_privileged`` (section 6; the unlock refusal itself is STU-XDM-4).

**Backup** (:func:`backup`). PostgreSQL: ``pg_dump`` and ``pg_restore`` verified now (:mod:`..pgtools`; the client
major at least the server's), the admin profile's credentials with every check of an unlock (the password only in the
tool's passfile), ``run.start`` (no record, no backup), the level-2 fingerprint of every user schema before and after
the dump (a schema that changed in between leaves the fingerprint out: the restore cannot be compared, and says so),
``pg_dump -Fc`` into a temporary file, ``pg_restore --list`` of it, the move to its final name and the metadata
(:mod:`..backup_store`), ``run.result``. SQLite: ``tabrion.sqlite.backup`` through the checked write connection
(STU-CR-5's way), then the same metadata.

**Restore** (:func:`preview`, :func:`restore`). Only a backup of Studio's own folder whose metadata matches the file
and the target's engine; only on an unlock whose stored environment is ``dev`` or ``test``; the user types the
target's name (D-12).

* **PostgreSQL** restores only into a **new** database (no ``--clean``): ``tabrion.postgres.create_database`` on the
  admin connection (``template0``, the owner the restore role), its OID read right after it is created and compared
  again after the tool ran (a database swapped in between is refused), then ``pg_restore --single-transaction
  --exit-on-error --no-owner --no-privileges`` **as a separate restore role** (review M-1 of STU-XDM-1): the
  archive's functions (check constraints, index expressions, materialized views) run as the role that restores. So
  the restore connects, as its own login and not through a role switch, with a second stored admin profile of the
  same read profile whose role is lean: no SUPERUSER, BYPASSRLS, REPLICATION, CREATEROLE or CREATEDB and no
  predefined role, directly or through a reachable role (:func:`restore_role_refusals`, read on its own checked
  connection before the run). Without one the restore is refused (``studio.dbm_restore_role_unsafe``). The admin role
  must be allowed to make it the owner (``SET`` on its membership; provisioned outside Studio, like every right). A
  failed restore leaves the new database (empty: one transaction) for the user to inspect.
* **SQLite** restores into a new file next to the profile's file, or over the profile's own file (``overwrite``;
  tabrion I-170). An existing target gets a backup of its own first (mandatory here: the DEV opt-out of section 4 is
  not offered). Before the copy Studio closes its own connections to that file (the read connections in the
  registry, by device and inode, and with them every unlock of them; review I-4), so no pool keeps working on the
  replaced content; the user connects again.

**PROD targets (L-1 of STU-XDM-1).** A target that a PROD profile of the user's store names is refused, whichever
profile the request came through: a SQLite file by device and inode; a PostgreSQL database by its name on the same
server, where the same server means the same ``system_identifier`` (``pg_control_system()``, read on the admin
connection and on any open read connection of the PROD profile, where the role may read it), or the same port and
an overlapping set of resolved addresses (``localhost``, every loopback address and a socket folder are all "this
computer"). A name that resolves differently later, or a server behind a port forward, is a residual risk.

**After the restore** the level-2 fingerprint of the target (SQLite: tabrion's ``integrity_check`` ran in
``restore``) is compared with the backup's; a difference, or a backup without a fingerprint, is answered as a restore
that could not be verified (``verified: false``) and the target is left for the user to inspect.

**Audit (section 9, GS4-22).** ``run.start`` before (``dbm.backup`` or ``dbm.restore``; no record, no action) and
``run.result`` after, with the backup's file name, SHA-256 and tool version, the target's name and the restore
profile; never a value, a password, a path or a server message.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import contextlib
from dataclasses import dataclass
import hashlib
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import stat
from typing import Any
import unicodedata

from tabrion.errors import (
    DatabaseInUse,
    PermissionDenied,
    RoleMissing,
    TabrionError,
    UnsafePath,
    UsageError,
    classify,
    safe_message,
)
from tabrion.schema import fingerprint, snapshot
from tabrion.sqlite import backup as sqlite_backup
from tabrion.sqlite import restore as sqlite_restore

from tabrion_studio.config.schema import Profile, WriteProfile
from tabrion_studio.config.write_areas import Area
from tabrion_studio.services.connections import ConnectionRegistry, read_only_database
from tabrion_studio.write.audit import AuditError
from tabrion_studio.write.backup_store import BackupStore, Loaded, metadata
from tabrion_studio.write.connection import (
    WriteConnection,
    WriteError,
    admin_refusing_findings,
    file_identity,
    privileges_now,
    restore_role_refusals,
)
from tabrion_studio.write.folders import GROUP_OTHER_WRITE, ancestor_is_safe, folders_up, reserved_identities
from tabrion_studio.write.pgtools import Connection, Runner, Tool, open_archive, verify_tool
from tabrion_studio.write.unlock import WRITE_ID_PREFIX, Unlock, Unlocks

logger = logging.getLogger("tabrion_studio.write")

AREA = Area.DATABASE_MANAGER
ACTION_BACKUP = "dbm.backup"
ACTION_RESTORE = "dbm.restore"
#: The stored environments a restore may write to (STU-ADR-0008 section 4): never STAGE, PROD or unset.
RESTORE_ENVIRONMENTS = frozenset({"dev", "test"})
#: The file name endings of a new SQLite restore target.
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SQLITE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_SYSTEM_IDENTIFIER = "SELECT pg_catalog.pg_control_system().system_identifier::pg_catalog.text"
_DATABASE_OID = (
    "SELECT d.oid::pg_catalog.int8 FROM pg_catalog.pg_database d"
    " WHERE d.datname OPERATOR(pg_catalog.=) CAST(:name AS pg_catalog.name)"
)
_SERVER_VERSION_TEXT = "SELECT pg_catalog.current_setting('server_version')"


class DriverFailure(Exception):
    """A driver error of ``CREATE DATABASE`` (tabrion lets it through, I-191 b): answered as ``db.<kind>`` only."""

    def __init__(self, classification: Any) -> None:
        super().__init__("database error")
        self.classification = classification


@dataclass
class Manager:
    """What the routes give each action: the unlocks, the tool runner, the backup folder, the read registry and the
    folders no database file may be created in (Studio's own, by device and inode)."""

    unlocks: Unlocks
    runner: Runner
    store: BackupStore
    registry: ConnectionRegistry
    reserved: tuple[Path, ...] = ()


# --- small checks ---------------------------------------------------------------------------------------------------


def _unlock(manager: Manager, session: str, connection_id: object) -> Unlock:
    return manager.unlocks.require_unlock(session, connection_id, AREA)


def _check_admin(manager: Manager, unlock: Unlock) -> None:
    """Section 6 at every action: an admin role that has or reaches what refuses the area (PostgreSQL only). The
    refusal ends the unlock: the role must be fixed outside Studio and unlocked again."""
    if unlock.connection.engine != "postgresql":
        return
    _facts, findings = privileges_now(unlock.connection.fetch)
    if admin_refusing_findings(findings):
        manager.unlocks.lock(unlock.session, unlock.connection_id)
        logger.warning(
            "the admin role has superuser, BYPASSRLS, REPLICATION, server-file or pre-16 CREATEROLE rights",
            extra={"event": "studio.admin_over_privileged", "code": "studio.admin_over_privileged"},
        )
        raise WriteError("studio.admin_over_privileged")


def _not_allowed(reason: str) -> WriteError:
    return WriteError("studio.dbm_restore_not_allowed", [("restore.target", f"restore.{reason}")])


def _has_hidden(text: str) -> bool:
    """A control, format, surrogate, private-use or unassigned character, a separator or a space other than U+0020:
    a name the user cannot confirm by typing what they see (P90-05)."""
    return any(
        unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
        or (unicodedata.category(char) == "Zs" and char != " ")
        for char in text
    )


def _database_name(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise WriteError("studio.dbm_restore_name_invalid")
    if len(raw.encode("utf-8")) > 63 or raw != raw.strip() or _has_hidden(raw):
        raise WriteError("studio.dbm_restore_name_invalid")
    return raw


def _sqlite_name(raw: object) -> str:
    if not isinstance(raw, str) or _SQLITE_NAME.fullmatch(raw) is None or not raw.lower().endswith(SQLITE_SUFFIXES):
        raise WriteError("studio.dbm_restore_name_invalid")
    return raw


def _typed(doc: dict[str, Any], expected: str) -> None:
    typed = doc.get("typed")
    if not isinstance(typed, str) or not secrets.compare_digest(typed.encode("utf-8"), expected.encode("utf-8")):
        raise WriteError("studio.dbm_restore_name_mismatch")


def _sha256_file(path: Path) -> tuple[int, str, tuple[int, int]]:
    """Size, SHA-256 and identity of the regular file ``path``, opened without following a link."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise WriteError("studio.dbm_backup_invalid", [("backup.file", "backup.not_a_file")])
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return info.st_size, digest.hexdigest(), (info.st_dev, info.st_ino)


def _user_schemas(inspector: Any) -> list[str]:
    if inspector.dialect.name == "sqlite":
        return ["main"]
    names = inspector.get_schema_names()
    return sorted(n for n in names if isinstance(n, str) and not n.startswith("pg_") and n != "information_schema")


def _fingerprint_of(inspect: Callable[[Callable[[Any], str]], str]) -> str | None:
    """The level-2 fingerprint of every user schema (``main`` on SQLite); None where tabrion cannot read it (a name
    it refuses at level 2, too many tables): the restore then cannot be compared and says so."""
    try:
        return inspect(lambda inspector: fingerprint(snapshot(inspector, schemas=_user_schemas(inspector), level=2)))
    except WriteError:
        raise
    except Exception:
        logger.info("schema fingerprint not available", extra={"event": "studio.dbm_fingerprint_unavailable"})
        return None


def schema_fingerprint(connection: WriteConnection) -> str | None:
    return _fingerprint_of(connection.inspect)


def _sqlite_file_fingerprint(path: Path, limits: Any) -> str | None:
    """The fingerprint of a SQLite file read through Studio's guarded read-only connection (``trusted_schema`` off,
    the read authorizer)."""
    profile = Profile(name="restored", engine="sqlite", environment="dev", path=str(path))
    try:
        db = read_only_database(profile, limits, None, path)
    except Exception:
        return None
    try:

        def inspect(consume: Callable[[Any], str]) -> str:
            with db.inspector() as found:
                return consume(found)

        return _fingerprint_of(inspect)
    finally:
        db.close()


# --- audit ----------------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def _audited(manager: Manager, unlock: Unlock, action: str, start: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """``run.start`` before (no record, nothing runs) and ``run.result`` after, whatever happened. The caller adds
    fields for the result record to the yielded dict (``result`` among them)."""
    common: dict[str, object] = {
        "run": secrets.token_hex(8),
        "area": AREA.value,
        "action": action,
        "profile": unlock.write_name,
    }
    audit = manager.unlocks.audit
    try:
        audit.append(
            "run.start",
            **common,
            kind=unlock.kind,
            environment=unlock.environment,
            engine=unlock.connection.engine,
            connection=unlock.connection_id,
            **start,
        )
    except AuditError:
        logger.warning("database manager action not run: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    except ValueError:
        logger.warning("action not run: audit record not valid", extra={"event": "studio.audit_record_invalid"})
        raise WriteError("studio.audit_record_invalid") from None
    outcome: dict[str, Any] = {"result": "failed"}
    try:
        yield outcome
    except WriteError as exc:
        outcome["result"] = exc.code
        raise
    except DriverFailure as exc:
        outcome["result"] = f"db.{exc.classification.kind.value}"
        raise
    finally:
        fields = {key: value for key, value in outcome.items() if key != "audit_pending"}
        outcome["audit_pending"] = not audit.record("run.result", **common, **fields)
        if outcome["result"] == "studio.admin_over_privileged":
            manager.unlocks.lock(unlock.session, unlock.connection_id)


# --- the tools --------------------------------------------------------------------------------------------------------


def _tools(manager: Manager, server_major: int, archive_major: int = 0) -> tuple[Tool, Tool]:
    """``pg_dump`` and ``pg_restore``, verified now for a server of ``server_major`` (and an archive of
    ``archive_major``): the configuration is read again, every check runs again."""
    tools = manager.runner.tools()
    with manager.runner.private_folder() as (scratch, _passfile):
        pg_dump = verify_tool("pg_dump", tools, scratch, at_least=server_major)
        pg_restore = verify_tool("pg_restore", tools, scratch, at_least=max(server_major, archive_major))
    return pg_dump, pg_restore


def tools_status(manager: Manager) -> dict[str, Any]:
    """For the page: whether each tool is configured and passes the checks that need no server (its reason
    otherwise). Never the path."""
    status: dict[str, Any] = {}
    tools = manager.runner.tools()
    for name in ("pg_dump", "pg_restore"):
        try:
            with manager.runner.private_folder() as (scratch, _passfile):
                tool = verify_tool(name, tools, scratch)
        except WriteError as exc:
            reason = exc.issues[0][1] if exc.issues else "tool.unavailable"
            status[name] = {"ok": False, "reason": reason, "version": None}
        else:
            status[name] = {"ok": True, "reason": None, "version": tool.version}
    return status


def _connection(write: WriteProfile, read: Profile, password: str | None, database: str | None = None) -> Connection:
    if read.database is None or write.user is None:
        raise WriteError("studio.write_coupling_invalid")
    return Connection(
        host=read.host,
        port=read.port,
        dbname=database if database is not None else read.database,
        user=write.user,
        sslmode=read.sslmode,
        password=password,
        requirepeer=write.requirepeer,
    )


def _system_identifier(fetch: Callable[[str], list[tuple[Any, ...]]]) -> str | None:
    try:
        rows = fetch(_SYSTEM_IDENTIFIER)
    except Exception:
        return None  # the role may not read pg_control_system(): the addresses decide
    value = rows[0][0] if rows and rows[0] else None
    return value if isinstance(value, str) and value.isdigit() else None


# --- backup ---------------------------------------------------------------------------------------------------------


def _source(manager: Manager, unlock: Unlock) -> dict[str, Any]:
    url = manager.registry.get(unlock.connection_id).url
    return {"url": url, "profile": unlock.read_name, "environment": unlock.environment}


def backup(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Back up the database of this session's unlocked admin connection (see the module text)."""
    unlock = _unlock(manager, session, doc.get("connection_id"))
    if unlock.connection.engine == "sqlite":
        return _backup_sqlite(manager, unlock, origin="database_manager")
    return _backup_postgres(manager, unlock)


def _backup_postgres(manager: Manager, unlock: Unlock) -> dict[str, Any]:
    _check_admin(manager, unlock)
    server = unlock.connection.engine_version()
    pg_dump, pg_restore = _tools(manager, server[0])
    write, read, password = manager.unlocks.credentials(unlock.write_name)
    target = _connection(write, read, password)
    folder = manager.store.folder()
    name = manager.store.new_name("postgresql")
    version_rows = unlock.connection.fetch(_SERVER_VERSION_TEXT)
    server_version = str(version_rows[0][0]) if version_rows and version_rows[0] else None
    start = {"objects": [target.dbname], "backup_file": name, "tool_version": pg_dump.version}
    with _audited(manager, unlock, ACTION_BACKUP, start) as outcome:
        before = schema_fingerprint(unlock.connection)
        archive = manager.runner.dump(pg_dump, pg_restore, target, folder / name)
        after = schema_fingerprint(unlock.connection)
        meta = metadata(
            name=name,
            created=manager.store.now(),
            engine="postgresql",
            origin="database_manager",
            size=archive.size,
            sha256=archive.sha256,
            source={**_source(manager, unlock), "database": target.dbname},
            server_version=server_version,
            system_identifier=_system_identifier(unlock.connection.fetch),
            tool=pg_dump.metadata(),
            fingerprint=before if before is not None and before == after else None,
            extensions=archive.extensions,
        )
        try:
            manager.store.write_metadata(name, meta)
        except WriteError:
            manager.store.discard(name)
            raise
        outcome.update(result="ok", sha256=archive.sha256, backup_file=name)
    logger.info("database backup made", extra={"event": "studio.dbm_backup"})
    return {"backup": _answer(meta), "audit_pending": outcome["audit_pending"]}


def _backup_sqlite(manager: Manager, unlock: Unlock, *, origin: str) -> dict[str, Any]:
    connection = unlock.connection
    if connection.database_file is None:  # pragma: no cover - a SQLite write connection always has its file
        raise WriteError("studio.write_target_changed")
    folder = manager.store.folder()
    name = manager.store.new_name("sqlite")
    start = {"objects": [connection.database_file.name], "backup_file": name}
    with _audited(manager, unlock, ACTION_BACKUP, start) as outcome:
        before = schema_fingerprint(connection)
        try:
            with connection.raw_sqlite() as source:
                sqlite_backup(source, folder / name, overwrite=False)
            connection.verify()  # still the file the unlock checked
        except WriteError:
            raise
        except Exception:
            logger.warning("database backup failed", extra={"event": "studio.dbm_backup_failed"})
            raise WriteError("studio.dbm_backup_failed") from None
        size, digest, _identity = _sha256_file(folder / name)
        after = schema_fingerprint(connection)
        version = ".".join(str(part) for part in connection.engine_version())
        meta = metadata(
            name=name,
            created=manager.store.now(),
            engine="sqlite",
            origin=origin,
            size=size,
            sha256=digest,
            source={**_source(manager, unlock), "database": connection.database_file.name},
            server_version=version,
            system_identifier=None,
            tool=None,
            fingerprint=before if before is not None and before == after else None,
            extensions=None,
        )
        try:
            manager.store.write_metadata(name, meta)
        except WriteError:
            manager.store.discard(name)
            raise
        outcome.update(result="ok", sha256=digest, backup_file=name)
    logger.info("database backup made", extra={"event": "studio.dbm_backup"})
    return {"backup": _answer(meta), "audit_pending": outcome["audit_pending"]}


def _answer(meta: dict[str, Any]) -> dict[str, Any]:
    keys = ("file", "created", "engine", "size", "sha256", "fingerprint", "extensions", "complete", "scope_notice")
    return {key: meta.get(key) for key in keys}


def listing(manager: Manager) -> dict[str, Any]:
    """The backups of the folder (newest first), their count and size, and the tools' state. Read only."""
    found = manager.store.listing()
    found["tools"] = tools_status(manager)
    return found


# --- PROD targets (L-1) -----------------------------------------------------------------------------------------------


def _addresses(host: str | None, port: int | None) -> frozenset[str]:
    """``host`` as a set of addresses: ``local`` for this computer (no host, a socket folder, ``localhost``, a
    loopback address or a name that resolves to one), else the resolved addresses (the name itself if it does not
    resolve, compared as text)."""
    if host is None or host.startswith("/") or host.lower().rstrip(".") == "localhost":
        return frozenset({"local"})
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port or 5432, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return frozenset({f"name:{host.lower().rstrip('.')}"})
        found = set()
        for info in infos:
            text = str(info[4][0]).split("%", 1)[0]
            try:
                parsed = ipaddress.ip_address(text)
            except ValueError:
                continue
            found.add("local" if parsed.is_loopback else str(parsed))
        return frozenset(found) or frozenset({f"name:{host.lower().rstrip('.')}"})
    return frozenset({"local" if address.is_loopback else str(address)})


def _prod_profiles(manager: Manager, engine: str) -> list[Profile]:
    profiles = manager.unlocks.config.user.profiles().profiles
    return [p for p in profiles if p.environment == "prod" and p.engine == engine]


def _check_postgres_target(manager: Manager, unlock: Unlock, read: Profile, name: str) -> None:
    """Refuse a database ``name`` on this server that a PROD profile of the user's store names (L-1)."""
    here = _addresses(read.host, read.port)
    port = read.port or 5432
    identifier = _system_identifier(unlock.connection.fetch)
    open_ids = {c.profile_id: c for c in manager.registry.list()}
    for profile in _prod_profiles(manager, "postgresql"):
        if profile.database != name:
            continue
        same = (profile.port or 5432) == port and bool(_addresses(profile.host, profile.port) & here)
        opened = open_ids.get(f"user:{profile.name}")
        if not same and identifier is not None and opened is not None:
            same = _system_identifier(opened.fetch) == identifier
        if same:
            logger.warning("restore refused: a PROD profile names the target", extra={"event": "studio.dbm_prod"})
            raise _not_allowed("prod")


def _check_sqlite_target(manager: Manager, identity: tuple[int, int]) -> None:
    for profile in _prod_profiles(manager, "sqlite"):
        if profile.path is None:
            continue
        try:
            info = os.stat(Path(profile.path).expanduser())
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == identity:
            logger.warning("restore refused: a PROD profile names the target", extra={"event": "studio.dbm_prod"})
            raise _not_allowed("prod")


# --- restore --------------------------------------------------------------------------------------------------------


def _restore_unlock(manager: Manager, session: str, doc: dict[str, Any]) -> Unlock:
    unlock = _unlock(manager, session, doc.get("connection_id"))
    if unlock.environment not in RESTORE_ENVIRONMENTS:
        raise _not_allowed("environment")
    return unlock


def _restore_profile(manager: Manager, unlock: Unlock, raw: object) -> WriteProfile:
    """The stored admin profile the restore runs as (M-1): another admin profile of the same read profile."""
    if not isinstance(raw, str) or not raw.startswith(WRITE_ID_PREFIX):
        raise WriteError("studio.dbm_restore_role_unsafe", [("restore.role", "restore.role_required")])
    name = raw[len(WRITE_ID_PREFIX) :]
    user = manager.unlocks.config.user.profiles()
    write = next((w for w in user.write_profiles if w.name == name), None)
    admin = next((w for w in user.write_profiles if w.name == unlock.write_name), None)
    if write is None or write.kind != "admin" or write.read_profile != unlock.read_name or admin is None:
        raise WriteError("studio.dbm_restore_role_unsafe", [("restore.role", "restore.role_unknown")])
    if write.name == unlock.write_name or write.user == admin.user:
        raise WriteError("studio.dbm_restore_role_unsafe", [("restore.role", "restore.role_is_admin")])
    return write


def _check_restore_role(manager: Manager, restore_profile: WriteProfile) -> None:
    """The restore role's privileges, read on its own checked connection now (M-1): it must be lean."""
    connection = manager.unlocks.open_checked(restore_profile.name)
    try:
        facts, findings = privileges_now(connection.fetch)
    finally:
        connection.close()
    refusals = restore_role_refusals(facts, findings)
    if refusals:
        logger.warning("restore refused: the restore role is not lean", extra={"event": "studio.dbm_restore_role"})
        issues = [("restore.role", "restore." + code.split(":", 1)[0].replace("admin.", "")) for code in refusals]
        raise WriteError("studio.dbm_restore_role_unsafe", sorted(set(issues))[:8])


def preview(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """What a restore of ``backup_id`` through this unlock would do, checked as far as it can be without running:
    the backup's metadata (file, size and SHA-256 checked), the targets offered, the text to type, and on PostgreSQL
    the number of extensions in the archive's table of contents (review I-2). Changes nothing."""
    unlock = _restore_unlock(manager, session, doc)
    loaded = manager.store.load(doc.get("backup_id"), unlock.connection.engine)
    answer: dict[str, Any] = {"backup": _answer(loaded.meta), "environment": unlock.environment}
    if unlock.connection.engine == "postgresql":
        _check_admin(manager, unlock)
        server = unlock.connection.engine_version()
        _pg_dump, pg_restore = _tools(manager, server[0], _archive_major(loaded))
        with open_archive(loaded.path) as (archive, size, digest):
            _same_backup(loaded, size, digest)
            extensions = manager.runner.table_of_contents(pg_restore, archive)
        answer.update(targets=["new"], extensions=extensions, restore_profile_required=True)
    else:
        size, digest, _identity = _sha256_file(loaded.path)
        _same_backup(loaded, size, digest)
        file = unlock.connection.database_file
        answer.update(targets=["new", "existing"], existing_name=None if file is None else file.name)
    return answer


def _archive_major(loaded: Loaded) -> int:
    tool = loaded.meta.get("tool")
    version = tool.get("version") if isinstance(tool, dict) else None
    head = version.split(".", 1)[0] if isinstance(version, str) else ""
    if not head.isdigit():
        raise WriteError("studio.dbm_backup_invalid", [("backup.file", "backup.metadata_invalid")])
    return int(head)


def _same_backup(loaded: Loaded, size: int, digest: str) -> None:
    if size != loaded.size or not secrets.compare_digest(digest, loaded.sha256):
        logger.warning("restore refused: the backup changed", extra={"event": "studio.dbm_backup_changed"})
        raise WriteError("studio.dbm_backup_invalid", [("backup.file", "backup.changed")])


def restore(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Restore ``backup_id`` (see the module text). Raises :class:`WriteError` or :class:`DriverFailure`."""
    unlock = _restore_unlock(manager, session, doc)
    loaded = manager.store.load(doc.get("backup_id"), unlock.connection.engine)
    target = doc.get("target")
    if unlock.connection.engine == "postgresql":
        if target != "new":
            raise _not_allowed("existing_postgres")
        return _restore_postgres(manager, unlock, loaded, doc)
    if target not in ("new", "existing"):
        raise _not_allowed("target")
    return _restore_sqlite(manager, unlock, loaded, doc, existing=target == "existing")


def _restore_postgres(manager: Manager, unlock: Unlock, loaded: Loaded, doc: dict[str, Any]) -> dict[str, Any]:
    name = _database_name(doc.get("name"))
    _typed(doc, name)
    _check_admin(manager, unlock)
    _admin, read, _password = manager.unlocks.credentials(unlock.write_name)
    _check_postgres_target(manager, unlock, read, name)
    restore_profile = _restore_profile(manager, unlock, doc.get("restore_profile_id"))
    _check_restore_role(manager, restore_profile)
    server = unlock.connection.engine_version()
    _pg_dump, pg_restore = _tools(manager, server[0], _archive_major(loaded))
    role_write, role_read, role_password = manager.unlocks.credentials(restore_profile.name)
    if role_write.user is None:  # pragma: no cover - a PostgreSQL write profile always has its role
        raise WriteError("studio.write_coupling_invalid")
    into = _connection(role_write, role_read, role_password, name)
    with open_archive(loaded.path) as (archive, size, digest):
        _same_backup(loaded, size, digest)
        manager.runner.table_of_contents(pg_restore, archive)
        start = {
            "objects": [name],
            "target": name,
            "backup_file": loaded.path.name,
            "sha256": digest,
            "tool_version": pg_restore.version,
            "restore_profile": restore_profile.name,
        }
        with _audited(manager, unlock, ACTION_RESTORE, start) as outcome:
            _create_database(unlock, name, role_write.user)
            oid = _database_oid(unlock, name)
            if oid is None:
                raise WriteError("studio.dbm_restore_target_changed")
            manager.runner.restore(pg_restore, into, archive)
            if _database_oid(unlock, name) != oid:
                raise WriteError("studio.dbm_restore_target_changed")
            restored = manager.unlocks.open_checked(restore_profile.name, name)
            try:
                after = schema_fingerprint(restored)
            finally:
                restored.close()
            expected = loaded.meta.get("fingerprint")
            verified = isinstance(expected, str) and after == expected
            outcome.update(result="restored" if verified else "not_verified", verified=verified)
    logger.info("database restored", extra={"event": "studio.dbm_restore", "code": outcome["result"]})
    return {
        "engine": "postgresql",
        "target": name,
        "verified": verified,
        "fingerprint_expected": expected if isinstance(expected, str) else None,
        "fingerprint_after": after,
        "audit_pending": outcome["audit_pending"],
    }


def _database_oid(unlock: Unlock, name: str) -> int | None:
    rows = unlock.connection.fetch(_DATABASE_OID, {"name": name})
    value = rows[0][0] if rows and rows[0] else None
    return value if type(value) is int else None


def _create_database(unlock: Unlock, name: str, owner: str) -> None:
    """``tabrion.postgres.create_database(name, owner=<restore role>, template="template0")`` on the admin session in
    autocommit, after section 6 again on that very session. Errors as fixed problems or ``db.<kind>`` only."""
    import psycopg
    from psycopg.rows import tuple_row
    from tabrion.postgres import create_database

    try:
        with unlock.connection.idle_autocommit() as connection:

            def fetch(sql: str) -> list[tuple[Any, ...]]:
                with psycopg.Cursor(connection, row_factory=tuple_row) as reading:
                    reading.execute(sql)
                    return reading.fetchall()

            _facts, findings = privileges_now(fetch)
            if admin_refusing_findings(findings):
                raise WriteError("studio.admin_over_privileged")
            create_database(connection, name, owner=owner, template="template0")
    except WriteError:
        raise
    except psycopg.errors.DuplicateDatabase:
        raise WriteError("studio.dbm_restore_target_exists") from None
    except TabrionError as exc:
        if isinstance(exc, RoleMissing | PermissionDenied | UsageError) or exc.classification is None:
            code = exc.code.rsplit(".", 1)[-1] if isinstance(exc.code, str) else "failed"
            reason = code if re.fullmatch(r"[a-z_]{1,48}", code) else "failed"
            raise WriteError("studio.dbm_restore_failed", [("restore.create", f"restore.{reason}")]) from None
        logger.info("database not created", extra={"event": "studio.dbm_create_failed", "detail": safe_message(exc)})
        raise DriverFailure(exc.classification) from None
    except psycopg.Error as exc:
        classification = classify(exc)
        logger.info(
            "database not created",
            extra={"event": "studio.dbm_create_failed", "detail": safe_message(classification)},
        )
        raise DriverFailure(classification) from None


def _identity_or_none(path: Path) -> tuple[int, int] | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _close_connections_to(manager: Manager, identity: tuple[int, int]) -> int:
    """Close Studio's read connections to the file of ``identity`` (and with them their unlocks; review I-4)."""
    closed = 0
    for connection in manager.registry.list():
        if connection.database_file is None:
            continue
        if (
            manager.registry.database_identity(connection) == identity
            or _identity_or_none(connection.database_file) == identity
        ):
            manager.registry.close(connection.id)
            closed += 1
    return closed


def _check_new_file_folder(folder: Path, reserved: Iterable[Path]) -> None:
    """The folder a new SQLite file is restored into (review I-1 and I-2 of PR #136): neither it nor a folder above it
    is one of Studio's own folders, compared by device and inode (a second name of one is refused too, P128-02). On
    POSIX the rules of the Schema Designer's database creation (STU-CR-2) apply to its real path, so nobody else can
    swap the restored file: the folder belongs to this user and neither group nor others may write it, and every
    folder above it passes :func:`ancestor_is_safe`. A link on the stored path (macOS ``/var``) must belong to root or
    this user, and the folder holding it is checked as a folder above. Raises ``studio.dbm_restore_not_allowed``."""
    ids = reserved_identities(reserved)
    real = Path(os.path.realpath(folder))
    try:
        # Path.parents: the folders above in the platform's own path syntax (folders_up is POSIX only).
        real_chain = [os.lstat(each) for each in (real, *real.parents)]
    except OSError:
        raise _not_allowed("folder_unsafe") from None
    if any((info.st_dev, info.st_ino) in ids for info in real_chain):
        raise _not_allowed("reserved_folder")
    if os.name != "posix":
        return  # Windows: no owner or mode rules (its access lists are not read), as STU-CR-2 notes
    try:
        given_chain = [os.lstat(each) for each in folders_up(str(folder))]
    except OSError:
        raise _not_allowed("folder_unsafe") from None
    uid = os.geteuid()
    here = real_chain[0]
    if not stat.S_ISDIR(here.st_mode) or here.st_uid != uid or here.st_mode & GROUP_OTHER_WRITE:
        raise _not_allowed("folder_unsafe")
    if not all(ancestor_is_safe(info, uid) for info in real_chain[1:]):
        raise _not_allowed("folder_unsafe")
    for index, info in enumerate(given_chain):
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid not in (0, uid):
                raise _not_allowed("folder_unsafe")
        elif index > 0 and not ancestor_is_safe(info, uid):
            raise _not_allowed("folder_unsafe")


def _restore_sqlite(
    manager: Manager, unlock: Unlock, loaded: Loaded, doc: dict[str, Any], *, existing: bool
) -> dict[str, Any]:
    file = unlock.connection.database_file
    if file is None:  # pragma: no cover - a SQLite write connection always has its file
        raise WriteError("studio.write_target_changed")
    if existing:
        target = file
        _typed(doc, file.name)
        identity = file_identity(file)
        if unlock.connection.identity != identity:
            raise WriteError("studio.dbm_restore_target_changed")
        _check_sqlite_target(manager, identity)
    else:
        name = _sqlite_name(doc.get("name"))
        _typed(doc, name)
        target = file.parent / name
        if os.path.lexists(target):
            raise WriteError("studio.dbm_restore_target_exists")
        _check_new_file_folder(
            target.parent, (*manager.reserved, *([] if manager.store.path is None else [manager.store.path]))
        )
    size, digest, _source_identity = _sha256_file(loaded.path)
    _same_backup(loaded, size, digest)
    pre_backup: str | None = None
    if existing:
        # The target's own backup first (section 4): no backup, no restore.
        made = _backup_sqlite(manager, unlock, origin="pre_restore")
        pre_backup = made["backup"]["file"]
    start = {
        "objects": [target.name],
        "target": target.name,
        "backup_file": loaded.path.name,
        "sha256": digest,
        "pre_backup": pre_backup,
    }
    with _audited(manager, unlock, ACTION_RESTORE, start) as outcome:
        closed = 0
        if existing:
            identity = file_identity(target)
            if identity != unlock.connection.identity:
                raise WriteError("studio.dbm_restore_target_changed")
            # Review I-4: Studio's own pools on this file are closed first (and the unlock with its connection).
            closed = _close_connections_to(manager, identity)
            unlock.connection.close()
        try:
            sqlite_restore(loaded.path, target, overwrite=existing)
        except DatabaseInUse:
            raise WriteError("studio.dbm_restore_failed", [("restore.target", "restore.in_use")]) from None
        except (UnsafePath, UsageError, FileExistsError, FileNotFoundError):
            raise WriteError("studio.dbm_restore_failed", [("restore.target", "restore.refused")]) from None
        except TabrionError:
            raise WriteError("studio.dbm_restore_failed", [("restore.target", "restore.integrity")]) from None
        except Exception:
            logger.warning("restore failed", extra={"event": "studio.dbm_restore_failed"})
            raise WriteError("studio.dbm_restore_failed", [("restore.target", "restore.failed")]) from None
        if existing and file_identity(target) != unlock.connection.identity:
            raise WriteError("studio.dbm_restore_target_changed")
        after = _sqlite_file_fingerprint(target, manager.unlocks.config.limits())
        expected = loaded.meta.get("fingerprint")
        verified = isinstance(expected, str) and after == expected
        outcome.update(result="restored" if verified else "not_verified", verified=verified)
    logger.info("database restored", extra={"event": "studio.dbm_restore", "code": outcome["result"]})
    return {
        "engine": "sqlite",
        "target": target.name,
        "verified": verified,
        "fingerprint_expected": expected if isinstance(expected, str) else None,
        "fingerprint_after": after,
        "pre_backup": pre_backup,
        "connections_closed": closed,
        "audit_pending": outcome["audit_pending"],
    }
