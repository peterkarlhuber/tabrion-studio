"""Creating a database: a SQLite file (STU-CR-2) or a PostgreSQL database (STU-CR-3); threat TX-29, GS3-01..03,
GS3-20, GS3-21, GS3-27, STU-ADR-0007 sections 1 and 11, and since the G-S4 grant of 2026-11-18 the Database
Manager's admin area (STU-ADR-0008 section 10; STU-CR-ADMIN).

Imported only by a handler of :mod:`tabrion_studio.write.routes` after it checked that the ``database_manager`` flag
is on; with that flag off neither route imports this module (GS4-01). The Schema Designer's flag no longer opens
it: database creation and role management belong to the admin area with its own rights (owner, G-S4 grant).

**SQLite** (``POST /api/write/database-manager/create/sqlite``, ``{"path", "typed"}``). There is no connection to
unlock for a file that does not exist yet, and so no admin profile either (an admin profile belongs to the read
profile of an existing database, and a SQLite file has no role): the ``database_manager`` flag and the typed
confirmation, the new file's name (``typed`` equals the last part of ``path``), are what it needs. The rules, each
checked before anything is written:

* ``path`` is an absolute, normalized local path (no ``..``, no network, device or drive-relative form, no control
  character and no space other than U+0020), at most :data:`MAX_PATH` characters, whose file name ends in ``.db``,
  ``.sqlite`` or ``.sqlite3``;
* the platform is POSIX: on Windows Studio creates no SQLite file (``studio.schema_create_platform``), because it
  does not check a folder's access control list (open point 5 of the PR #128 review);
* the folder exists and is reached through **no link** at all (its real path is the path given), is not itself a
  link, belongs to this user and is not writable by group or others; every folder above it is a folder of root or
  this user that group and others may not write unless its sticky bit is set (like OpenSSH's ``StrictModes``);
* neither the folder nor any folder above it is one of Studio's own folders (configuration, state, audit, log, the
  project's ``.tabrion`` folder), compared by **device and inode**, so a second name of one of them is refused too;
* neither the file nor a side file (``-wal``, ``-shm``, ``-journal``) exists, as a file or as a link.

Then ``run.start`` is written to the audit log (no record, no file). Studio opens the checked folder without
following a link, compares it by device and inode with the folder it checked, and creates the file **relative to
that open folder** (``O_CREAT|O_EXCL|O_NOFOLLOW``, exactly ``0600`` whatever the umask), so the file lands in the
checked folder even if a folder on the path is replaced meanwhile; an existing name is ``studio.schema_file_exists``.
Before ``tabrion.sqlite.connect(create=False)`` with the default ``SQLiteProfile`` (WAL, foreign keys on) writes the
header, and again afterwards, Studio checks that the path still leads to the folder it checked (same device and
inode, same real path, safe folders above it) and that the name, through the path and in the open folder, is still
the file it created (device and inode, private regular file of this user). A failure removes that file from the open
folder (only if the name is still that file). ``run.result`` follows (``committed`` or the problem code). The answer
is the path and the mode; the page offers to save a connection profile for it (through the normal profile route,
with the environment the user picks).

**PostgreSQL** (``POST /api/write/database-manager/create/postgresql``, ``{"connection_id", "name", "owner",
"template", "encoding", "locale", "typed"}``). It runs on the admin connection of this session's unlock of
``connection_id`` for the Database Manager (:meth:`Unlocks.require_unlock`: the flag, an **admin** profile
(``kind="admin"``; a write profile never serves this area), the unlock and its 15-minute expiry, the coupling, the
stored environment, never the request's), so PROD is refused (``studio.write_prod_locked``,
E-X7) and so is a connection whose environment is not ``dev`` or ``test``. ``typed`` must equal ``name``. Studio
checks the fields first (``studio.schema_create_invalid`` with ``create.<field>`` issues), records ``run.start``,
looks for a database of that name (``studio.schema_database_exists``, no server text) and calls
``tabrion.postgres.create_database`` on an idle session of the admin connection in autocommit mode
(:meth:`WriteConnection.idle_autocommit`). tabrion checks ``CREATEDB``, that the owner exists and that the role may
``SET ROLE`` to it (PostgreSQL 16 and later; a member before), and builds the statement with ``psycopg.sql``; Studio
builds no DDL. Its refusals are fixed problems (``studio.schema_createdb_required``, ``studio.schema_owner_missing``,
``studio.schema_owner_not_allowed``); a duplicate found by the server is ``studio.schema_database_exists``; any other
driver error is a ``db.<kind>`` problem from ``tabrion.errors.classify`` (the SQLSTATE only), logged with
``safe_message`` only (I-191 b, TX-46). ``run.result`` follows.

**The admin role (STU-ADR-0008 section 6).** At the start of each run the privilege check runs again on the session
the statement uses (review L-1 of PR #127): superuser, BYPASSRLS, REPLICATION, a server-file role or CREATEROLE before
PostgreSQL 16 refuse the run with ``405 studio.admin_over_privileged`` and end the unlock, as every other action of
the area does. The admin role needs CREATEDB; a write role may no longer hold it (``open_write_connection`` refuses
it, :func:`~tabrion_studio.write.connection.write_refusals`).

**Audit (GS3-27, GS4-22).** ``run.start`` and ``run.result`` with the area (``database_manager``), the action
(``dbm.create_sqlite`` or ``dbm.create_postgresql``), the engine and the object's name (the file name, never its
folder; the database name), plus the profile and environment for PostgreSQL; never an owner, a locale, a path or a
secret.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import contextlib
from dataclasses import dataclass
import logging
import ntpath
import os
from pathlib import Path
import posixpath
import re
import secrets
import stat
import sys
from typing import Any

from tabrion.errors import PermissionDenied, RoleMissing, TabrionError, classify, safe_message
from tabrion.postgres import create_database
from tabrion.sqlite import SQLiteProfile
from tabrion.sqlite import connect as sqlite_connect

from tabrion_studio.config.locations import network_or_device, safe_realpath
from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import (
    DriverFailure,
    WriteError,
    admin_refusing_findings,
    has_hidden_character,
    privileges_now,
)
from tabrion_studio.write.folders import ancestor_is_safe as ancestor_is_safe  # re-exported (tests)
from tabrion_studio.write.folders import folders_up
from tabrion_studio.write.folders import reserved_identities as reserved_identities  # re-exported (tests)
from tabrion_studio.write.unlock import Unlock, Unlocks

logger = logging.getLogger("tabrion_studio.write")

#: The longest path the SQLite wizard takes (characters).
MAX_PATH = 4096
#: The file name endings the SQLite wizard creates (a safeguard against a typo that names a file of another kind).
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SIDE_FILES = ("-wal", "-shm", "-journal")
#: PostgreSQL's identifier limit (NAMEDATALEN - 1), in bytes of UTF-8 (tabrion I-190).
MAX_NAME_BYTES = 63
TEMPLATES = ("template1", "template0")
#: tabrion I-190's patterns for the encoding and the locale, checked here first so the page gets the field.
_ENCODING = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
_LOCALE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,62}")
#: The environments a database may be created for (E-X4 for the area; never prod, never unset).
CREATE_ENVIRONMENTS = frozenset({"dev", "test"})
_POSIX = sys.platform != "win32"
_GROUP_OTHER_WRITE = 0o022
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

ACTION_SQLITE = "dbm.create_sqlite"
ACTION_POSTGRES = "dbm.create_postgresql"
__all__ = ["DriverFailure", "create_postgres_database", "create_sqlite_file"]


def _invalid(field: str, code: str) -> WriteError:
    return WriteError("studio.schema_create_invalid", [(f"create.{field}", f"create.{code}")])


_has_control = has_hidden_character


# --- SQLite (STU-CR-2) -------------------------------------------------------------------------------------------


def _check_path(raw: object) -> tuple[str, str, str]:
    """``(path, folder, file name)`` of a valid request, else :class:`WriteError` with the ``create.path`` issue."""
    if not isinstance(raw, str) or not raw:
        raise _invalid("path", "required")
    if len(raw) > MAX_PATH:
        raise _invalid("path", "too_long")
    if _has_control(raw):
        raise _invalid("path", "control_character")
    if network_or_device(raw):
        raise _invalid("path", "network_path")
    module: Any = posixpath if _POSIX else ntpath
    if not module.isabs(raw):
        raise _invalid("path", "not_absolute")
    if module.normpath(raw) != raw or ".." in raw.replace("\\", "/").split("/"):
        raise _invalid("path", "not_normalized")
    folder, name = module.split(raw)
    if not name or name in (".", ".."):
        raise _invalid("path", "no_file_name")
    if not name.lower().endswith(SQLITE_SUFFIXES):
        raise _invalid("path", "suffix")
    return raw, folder, name


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool((getattr(info, "st_file_attributes", 0) or 0) & _REPARSE)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _unsafe() -> WriteError:
    return WriteError("studio.schema_folder_unsafe")


def _safe_ancestors(folder: str, uid: int) -> list[os.stat_result]:
    """The ``lstat`` of every folder above ``folder`` if each passes :func:`ancestor_is_safe`; else refused."""
    found = []
    for current in list(folders_up(folder))[1:]:
        try:
            info = os.lstat(current)
        except OSError:
            raise _unsafe() from None
        if not ancestor_is_safe(info, uid):
            raise _unsafe()
        found.append(info)
    return found


def _check_folder(folder: str, reserved: frozenset[tuple[int, int]]) -> os.stat_result:
    """The folder's ``lstat`` if Studio may create a file in it, else :class:`WriteError` (POSIX only).

    The folder is reached through no link (its real path is the path given), belongs to this user and neither group
    nor others may write it; every folder above it passes :func:`ancestor_is_safe`. Neither it nor any folder above
    it is one of Studio's own folders, compared by device and inode, so a second name of the same folder (a bind
    mount, another spelling on a case-insensitive file system) is refused too (review P128-02)."""
    real = safe_realpath(Path(folder))
    if real is None or str(real) != folder:
        raise _unsafe()  # a link somewhere on the way: use the real path
    try:
        info = os.lstat(folder)
    except OSError:
        raise _unsafe() from None
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise _unsafe()
    uid = os.geteuid()
    if info.st_uid != uid or info.st_mode & _GROUP_OTHER_WRITE:
        raise _unsafe()
    above = _safe_ancestors(folder, uid)
    if any(_identity(each) in reserved for each in (info, *above)):
        raise WriteError("studio.schema_folder_reserved")
    return info


@contextlib.contextmanager
def _folder_held(folder: str, checked: os.stat_result) -> Iterator[int]:
    """The checked folder, opened without following a link; refused if it is not the folder checked."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(folder, flags)
    except OSError:
        raise _unsafe() from None
    try:
        if _identity(os.fstat(descriptor)) != _identity(checked):
            raise _unsafe()
        yield descriptor
    finally:
        os.close(descriptor)


def _entry(descriptor: int, name: str) -> os.stat_result | None:
    """The ``lstat`` of ``name`` in the held folder, None if there is none."""
    try:
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _exists(path: str) -> bool:
    return any(os.path.lexists(path + suffix) for suffix in ("", *_SIDE_FILES))


def _create_in(descriptor: int, name: str) -> tuple[int, int]:
    """Create ``name`` in the held folder (``O_CREAT|O_EXCL|O_NOFOLLOW``, exactly ``0600``); its identity.

    Relative to the folder's descriptor, so the file is made in the folder that was checked whatever happens to the
    path's folders meanwhile (review P128-01). An existing name, a dangling link included, is left alone."""
    for suffix in _SIDE_FILES:
        if _entry(descriptor, name + suffix) is not None:
            raise WriteError("studio.schema_file_exists")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        handle = os.open(name, flags, 0o600, dir_fd=descriptor)
    except FileExistsError:
        raise WriteError("studio.schema_file_exists") from None
    try:
        os.fchmod(handle, 0o600)  # whatever the umask
        info = os.fstat(handle)
    except BaseException:
        os.close(handle)
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=descriptor)
        raise
    os.close(handle)
    return _identity(info)


def _still_there(folder: str, checked: os.stat_result, descriptor: int, path: str, identity: tuple[int, int]) -> None:
    """The path still leads to the file Studio created in the folder it checked; :class:`WriteError` otherwise.

    The folder at the path is still the checked one, reached through no link and through safe folders only, and the
    name in it, by the path and in the held folder, is still the new private regular file (device and inode)."""
    try:
        at_path = os.lstat(folder)
    except OSError:
        raise _unsafe() from None
    if _identity(at_path) != _identity(checked):
        raise _unsafe()
    real = safe_realpath(Path(folder))
    if real is None or str(real) != folder:
        raise _unsafe()
    uid = os.geteuid()
    _safe_ancestors(folder, uid)
    name = posixpath.basename(path)
    for info in (_entry(descriptor, name), _lstat_or_none(path)):
        if info is None or _identity(info) != identity or _is_link(info) or not stat.S_ISREG(info.st_mode):
            raise WriteError("studio.schema_file_exists")
        if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
            raise WriteError("studio.schema_file_exists")


def _lstat_or_none(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _remove_created(descriptor: int, name: str, identity: tuple[int, int]) -> None:
    """Remove the file Studio created, and its side files, from the held folder if the name is still that file
    (never another one)."""
    with contextlib.suppress(OSError):
        info = _entry(descriptor, name)
        if info is None or _identity(info) != identity or not stat.S_ISREG(info.st_mode):
            return
        for suffix in _SIDE_FILES:
            with contextlib.suppress(OSError):
                side = _entry(descriptor, name + suffix)
                if side is not None and stat.S_ISREG(side.st_mode) and side.st_uid == os.geteuid():
                    os.unlink(name + suffix, dir_fd=descriptor)
        os.unlink(name, dir_fd=descriptor)


@dataclass(frozen=True)
class CreatedFile:
    path: str
    name: str
    audit_pending: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "engine": "sqlite",
            "path": self.path,
            "file_name": self.name,
            "mode": "0600",
            "audit_pending": self.audit_pending,
        }


def create_sqlite_file(audit: AuditLog, reserved: Iterable[Path], raw_path: object, typed: object) -> CreatedFile:
    """Create a new, private SQLite database file (see the module text). Raises :class:`WriteError`."""
    path, folder, name = _check_path(raw_path)
    if not isinstance(typed, str) or typed != name:
        raise WriteError("studio.schema_create_unconfirmed")
    if not _POSIX:
        # Open point 5 of the PR #128 review: Windows has no owner and mode check of the folder here (its access
        # control lists are not checked), so Studio creates no SQLite file there.
        raise WriteError("studio.schema_create_platform")
    checked = _check_folder(folder, reserved_identities(reserved))
    if _exists(path):
        raise WriteError("studio.schema_file_exists")
    run = secrets.token_hex(8)
    common: dict[str, object] = {"run": run, "area": Area.DATABASE_MANAGER.value, "action": ACTION_SQLITE}
    try:
        audit.append("run.start", **common, engine="sqlite", objects=[name])
    except AuditError:
        logger.warning("database not created: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    result = "failed"
    try:
        with _folder_held(folder, checked) as descriptor:
            identity = _create_in(descriptor, name)
            try:
                _still_there(folder, checked, descriptor, path, identity)
                connection = sqlite_connect(path, profile=SQLiteProfile(), create=False)
                try:
                    connection.execute("PRAGMA user_version").fetchone()
                finally:
                    connection.close()
                _still_there(folder, checked, descriptor, path, identity)
            except BaseException:
                _remove_created(descriptor, name, identity)
                raise
        result = "committed"
    except WriteError as exc:
        result = exc.code
        raise
    except Exception:
        logger.warning("SQLite file not created", extra={"event": "studio.schema_create_failed"})
        raise WriteError("studio.schema_folder_unsafe") from None
    finally:
        pending = not audit.record("run.result", **common, result=result)
    logger.info("SQLite database created", extra={"event": "studio.schema_database_created"})
    return CreatedFile(path, name, pending)


# --- PostgreSQL (STU-CR-3) ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DatabaseRequest:
    name: str
    owner: str | None
    template: str
    encoding: str
    locale: str | None


def _name(raw: object, field: str, required: bool) -> str | None:
    if raw is None or raw == "":
        if required:
            raise _invalid(field, "required")
        return None
    if not isinstance(raw, str):
        raise _invalid(field, "type")
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_NAME_BYTES:
        raise _invalid(field, "too_long")
    if _has_control(raw):
        raise _invalid(field, "control_character")
    if raw != raw.strip():
        raise _invalid(field, "whitespace")
    return raw


def check_database_request(doc: dict[str, Any]) -> DatabaseRequest:
    """The fields of a PostgreSQL creation, checked as tabrion I-190 does and stricter (no control characters, no
    blank at either end); :class:`WriteError` ``studio.schema_create_invalid`` with the field otherwise."""
    name = _name(doc.get("name"), "name", True)
    if name is None:  # pragma: no cover - a required name is never None
        raise _invalid("name", "required")
    owner = _name(doc.get("owner"), "owner", False)
    template = doc.get("template", "template1")
    if template not in TEMPLATES:
        raise _invalid("template", "choice")
    encoding = doc.get("encoding", "UTF8")
    if not isinstance(encoding, str) or _ENCODING.fullmatch(encoding) is None:
        raise _invalid("encoding", "format")
    locale = doc.get("locale")
    if locale == "":
        locale = None
    if locale is not None and (not isinstance(locale, str) or _LOCALE.fullmatch(locale) is None):
        raise _invalid("locale", "format")
    return DatabaseRequest(name, owner, str(template), encoding, locale)


@dataclass(frozen=True)
class CreatedDatabase:
    name: str
    owner: str | None
    audit_pending: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "engine": "postgresql",
            "database": self.name,
            "owner": self.owner,
            "audit_pending": self.audit_pending,
        }


_EXISTS = "SELECT 1 FROM pg_catalog.pg_database d WHERE d.datname::pg_catalog.text OPERATOR(pg_catalog.=) %s"
_PARAMETERS = {"name": "name", "owner": "owner", "template": "template", "encoding": "encoding", "locale": "locale"}


def _refusal(exc: TabrionError) -> WriteError:
    """tabrion's refusal of ``create_database`` as Studio's fixed problem (never its message)."""
    if isinstance(exc, RoleMissing):
        return WriteError("studio.schema_owner_missing")
    if isinstance(exc, PermissionDenied):
        if exc.code == "postgres.owner_not_allowed":
            return WriteError("studio.schema_owner_not_allowed")
        return WriteError("studio.schema_createdb_required")
    if exc.code == "postgres.invalid_argument":
        parameter = exc.details.get("parameter")
        field = _PARAMETERS.get(parameter) if isinstance(parameter, str) else None
        return _invalid(field or "name", "format")
    return WriteError("studio.write_unsupported")


def _target(unlocks: Unlocks, session: str, connection_id: object) -> Unlock:
    unlock = unlocks.require_unlock(session, connection_id, Area.DATABASE_MANAGER)
    # E-X7 again, from the stored profile as the unlock re-read it now: dev or test only, never prod or unset.
    if unlock.environment not in CREATE_ENVIRONMENTS:
        raise WriteError("studio.write_prod_locked")
    if unlock.connection.engine != "postgresql":
        raise WriteError("studio.schema_create_engine")
    return unlock


def create_postgres_database(unlocks: Unlocks, session: str, doc: dict[str, Any]) -> CreatedDatabase:
    """Create a PostgreSQL database on the unlocked admin connection (see the module text). Raises
    :class:`WriteError` or :class:`DriverFailure`."""
    request = check_database_request(doc)
    if not isinstance(doc.get("typed"), str) or doc.get("typed") != request.name:
        raise WriteError("studio.schema_create_unconfirmed")
    unlock = _target(unlocks, session, doc.get("connection_id"))
    run = secrets.token_hex(8)
    common: dict[str, object] = {
        "run": run,
        "area": Area.DATABASE_MANAGER.value,
        "action": ACTION_POSTGRES,
        "profile": unlock.write_name,
    }
    try:
        unlocks.audit.append(
            "run.start",
            **common,
            kind=unlock.kind,
            environment=unlock.environment,
            engine="postgresql",
            connection=unlock.connection_id,
            objects=[request.name],
        )
    except AuditError:
        logger.warning("database not created: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    except ValueError:
        logger.warning("database not created: audit record not valid", extra={"event": "studio.audit_record_invalid"})
        raise WriteError("studio.audit_record_invalid") from None
    result = "failed"
    try:
        _create(unlock, request)
        result = "committed"
    except WriteError as exc:
        result = exc.code
        raise
    except DriverFailure as exc:
        result = f"db.{exc.classification.kind.value}"
        raise
    finally:
        pending = not unlocks.audit.record("run.result", **common, result=result)
        if result == "studio.admin_over_privileged":
            # The unlock ends with the refusal (section 6): the role must be fixed outside Studio and unlocked again.
            unlocks.lock(session, unlock.connection_id)
    logger.info("PostgreSQL database created", extra={"event": "studio.schema_database_created"})
    return CreatedDatabase(request.name, request.owner, pending)


def _create(unlock: Unlock, request: DatabaseRequest) -> None:
    import psycopg
    from psycopg.rows import tuple_row

    try:
        with unlock.connection.idle_autocommit() as connection:

            def fetch(sql: str) -> list[tuple[Any, ...]]:
                with psycopg.Cursor(connection, row_factory=tuple_row) as reading:
                    reading.execute(sql)
                    return reading.fetchall()

            # STU-ADR-0008 section 6 at the start of the run, on the session the statement uses (review L-1 of PR #127):
            # an admin role that gained superuser, BYPASSRLS, REPLICATION, a server-file role or (before 16)
            # CREATEROLE since its unlock is refused. CREATE DATABASE cannot run in a transaction; nothing runs between
            # this check and the statement but the look-up below.
            _facts, findings = privileges_now(fetch)
            if admin_refusing_findings(findings):
                logger.warning(
                    "the admin role has superuser, BYPASSRLS, REPLICATION, server-file or pre-16 CREATEROLE rights",
                    extra={"event": "studio.admin_over_privileged", "code": "studio.admin_over_privileged"},
                )
                raise WriteError("studio.admin_over_privileged")
            with psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
                cursor.execute(_EXISTS, (request.name,))
                if cursor.fetchone() is not None:
                    raise WriteError("studio.schema_database_exists")
            create_database(
                connection,
                request.name,
                owner=request.owner,
                template="template0" if request.template == "template0" else "template1",
                encoding=request.encoding,
                locale=request.locale,
            )
    except WriteError:
        raise
    except psycopg.errors.DuplicateDatabase:
        # Created by someone else between the look-up and the statement: the same answer, no server text.
        raise WriteError("studio.schema_database_exists") from None
    except TabrionError as exc:
        if exc.classification is None or isinstance(exc, PermissionDenied | RoleMissing):
            raise _refusal(exc) from None
        logger.info("database not created", extra={"event": "studio.schema_create_failed", "detail": safe_message(exc)})
        raise DriverFailure(exc.classification) from None
    except psycopg.Error as exc:
        # tabrion lets the statement's driver errors through unchanged (I-191 b): only the classification and
        # safe_message leave here, never str(exc) (TX-46).
        classification = classify(exc)
        logger.info(
            "database not created",
            extra={"event": "studio.schema_create_failed", "detail": safe_message(classification)},
        )
        raise DriverFailure(classification) from None
