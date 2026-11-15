"""Creating a database: a SQLite file (STU-CR-2) or a PostgreSQL database (STU-CR-3); threat TX-29, GS3-01..03,
GS3-20, GS3-21, GS3-27, STU-ADR-0007 sections 1 and 11.

Imported only by a handler of :mod:`tabrion_studio.write.routes` after it checked that the ``schema_designer`` flag
is on; with that flag off neither route imports this module (STU-ADR-0006 proof item 2, GS3-01).

**SQLite** (``POST /api/write/schema-designer/create/sqlite``, ``{"path", "typed"}``). There is no connection to
unlock for a file that does not exist yet, so the typed confirmation is the new file's name (``typed`` equals the
last part of ``path``). The rules, each checked before anything is written:

* ``path`` is an absolute, normalized local path (no ``..``, no network, device or drive-relative form, no control
  character), at most :data:`MAX_PATH` characters, whose file name ends in ``.db``, ``.sqlite`` or ``.sqlite3``;
* the folder exists and is reached through **no link** at all (its real path is the path given; on Windows compared
  case-folded), is not itself a link (opened with ``O_NOFOLLOW`` where the platform has it and compared by device and
  inode with the checked folder), belongs to this user and is not writable by group or others (POSIX; the
  checks of Studio's own folders), and is not inside Studio's configuration, state or audit folders;
* neither the file nor a side file (``-wal``, ``-shm``, ``-journal``) exists, as a file or as a link.

Then ``run.start`` is written to the audit log (no record, no file), tabrion's ``create_private_file`` creates the
file (``O_CREAT|O_EXCL|O_NOFOLLOW``, exactly ``0600``), Studio checks that the name is now its own new, empty,
private regular file (an existing name is left alone by tabrion, so a file that appeared in between is
``studio.schema_file_exists``), and ``tabrion.sqlite.connect(create=False)`` with the default ``SQLiteProfile``
(WAL, foreign keys on) writes the header. A failure removes the file Studio created (only if the name is still that
file). ``run.result`` follows (``committed`` or the problem code). The answer is the path and the mode; the page
offers to save a connection profile for it (through the normal profile route, with the environment the user picks).

**PostgreSQL** (``POST /api/write/schema-designer/create/postgresql``, ``{"connection_id", "name", "owner",
"template", "encoding", "locale", "typed"}``). It runs on the write connection of this session's unlock of
``connection_id`` for the Schema Designer (:meth:`Unlocks.require_unlock`: the flag, the unlock and its 15-minute
expiry, the coupling, the stored environment, never the request's), so PROD is refused (``studio.write_prod_locked``,
E-X7) and so is a connection whose environment is not ``dev`` or ``test``. ``typed`` must equal ``name``. Studio
checks the fields first (``studio.schema_create_invalid`` with ``create.<field>`` issues), records ``run.start``,
looks for a database of that name (``studio.schema_database_exists``, no server text) and calls
``tabrion.postgres.create_database`` on an idle session of the write connection in autocommit mode
(:meth:`WriteConnection.idle_autocommit`). tabrion checks ``CREATEDB``, that the owner exists and that the role may
``SET ROLE`` to it (PostgreSQL 16 and later; a member before), and builds the statement with ``psycopg.sql``; Studio
builds no DDL. Its refusals are fixed problems (``studio.schema_createdb_required``, ``studio.schema_owner_missing``,
``studio.schema_owner_not_allowed``); a duplicate found by the server is ``studio.schema_database_exists``; any other
driver error is a ``db.<kind>`` problem from ``tabrion.errors.classify`` (the SQLSTATE only), logged with
``safe_message`` only (I-191 b, TX-46). ``run.result`` follows.

**Audit (GS3-27).** ``run.start`` and ``run.result`` with the area, the action (``schema_designer.create_sqlite`` or
``schema_designer.create_postgresql``), the engine and the object's name (the file name, never its folder; the
database name), plus the profile and environment for PostgreSQL; never an owner, a locale, a path or a secret.
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
import unicodedata

from tabrion.errors import Classification, PermissionDenied, RoleMissing, TabrionError, classify, safe_message
from tabrion.postgres import create_database
from tabrion.sqlite import SQLiteProfile, create_private_file
from tabrion.sqlite import connect as sqlite_connect

from tabrion_studio.config.locations import network_or_device, safe_realpath
from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import WriteError
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

ACTION_SQLITE = "schema_designer.create_sqlite"
ACTION_POSTGRES = "schema_designer.create_postgresql"


class DriverFailure(Exception):
    """A driver error of ``CREATE DATABASE`` that is not one of the fixed refusals: answered as ``db.<kind>``."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("database error")
        self.classification = classification


def _invalid(field: str, code: str) -> WriteError:
    return WriteError("studio.schema_create_invalid", [(f"create.{field}", f"create.{code}")])


def _has_control(text: str) -> bool:
    return any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn") for char in text)


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


def _same_path(left: str, right: str) -> bool:
    module: Any = posixpath if _POSIX else ntpath
    return bool(module.normcase(module.normpath(left)) == module.normcase(module.normpath(right)))


def _inside(path: str, root: str) -> bool:
    module: Any = posixpath if _POSIX else ntpath
    name = module.normcase(module.normpath(path))
    base = module.normcase(module.normpath(root)).rstrip("\\/")
    sep = "/" if _POSIX else "\\"
    return bool(base) and (name == base or name.startswith(base + sep))


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _check_folder(folder: str, reserved: Iterable[Path]) -> os.stat_result:
    """The folder's ``lstat`` if Studio may create a file in it, else :class:`WriteError`."""
    real = safe_realpath(Path(folder))
    if real is None or not _same_path(str(real), folder):
        raise WriteError("studio.schema_folder_unsafe")  # a link somewhere on the way: use the real path
    for root in reserved:
        root_real = safe_realpath(Path(os.path.abspath(root)))
        if root_real is not None and _inside(folder, str(root_real)):
            raise WriteError("studio.schema_folder_reserved")
    try:
        info = os.lstat(folder)
    except OSError:
        raise WriteError("studio.schema_folder_unsafe") from None
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise WriteError("studio.schema_folder_unsafe")
    if _POSIX and (info.st_uid != os.geteuid() or info.st_mode & _GROUP_OTHER_WRITE):
        raise WriteError("studio.schema_folder_unsafe")
    return info


@contextlib.contextmanager
def _folder_held(folder: str, checked: os.stat_result) -> Iterator[None]:
    """Hold the checked folder open (``O_NOFOLLOW`` where available) and refuse if it is not the folder checked."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not _POSIX:
        yield  # Windows cannot open a folder like this; the lstat checks above and below stand
        return
    try:
        descriptor = os.open(folder, flags)
    except OSError:
        raise WriteError("studio.schema_folder_unsafe") from None
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (checked.st_dev, checked.st_ino):
            raise WriteError("studio.schema_folder_unsafe")
        yield
    finally:
        os.close(descriptor)


def _exists(path: str) -> bool:
    return any(os.path.lexists(path + suffix) for suffix in ("", *_SIDE_FILES))


def _remove_created(path: str, identity: tuple[int, int]) -> None:
    """Remove the file Studio created, and its side files, if the name is still that file (never another one)."""
    with contextlib.suppress(OSError):
        info = os.lstat(path)
        if (info.st_dev, info.st_ino) != identity or not stat.S_ISREG(info.st_mode):
            return
        for suffix in _SIDE_FILES:
            side = path + suffix
            with contextlib.suppress(OSError):
                side_info = os.lstat(side)
                if stat.S_ISREG(side_info.st_mode) and (not _POSIX or side_info.st_uid == os.geteuid()):
                    os.unlink(side)
        os.unlink(path)


def _created_identity(path: str) -> tuple[int, int]:
    """The new file's identity if the name is now an empty private regular file of this user; else refused."""
    try:
        info = os.lstat(path)
    except OSError:
        raise WriteError("studio.schema_file_exists") from None
    if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_size != 0:
        raise WriteError("studio.schema_file_exists")
    if _POSIX and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise WriteError("studio.schema_file_exists")
    return info.st_dev, info.st_ino


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
            "mode": "0600" if _POSIX else None,
            "audit_pending": self.audit_pending,
        }


def create_sqlite_file(audit: AuditLog, reserved: Iterable[Path], raw_path: object, typed: object) -> CreatedFile:
    """Create a new, private SQLite database file (see the module text). Raises :class:`WriteError`."""
    path, folder, name = _check_path(raw_path)
    if not isinstance(typed, str) or typed != name:
        raise WriteError("studio.schema_create_unconfirmed")
    checked = _check_folder(folder, reserved)
    if _exists(path):
        raise WriteError("studio.schema_file_exists")
    run = secrets.token_hex(8)
    common: dict[str, object] = {"run": run, "area": Area.SCHEMA_DESIGNER.value, "action": ACTION_SQLITE}
    try:
        audit.append("run.start", **common, engine="sqlite", objects=[name])
    except AuditError:
        logger.warning("database not created: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    result = "failed"
    try:
        with _folder_held(folder, checked):
            create_private_file(path)
            identity = _created_identity(path)
            try:
                connection = sqlite_connect(path, profile=SQLiteProfile(), create=False)
                try:
                    connection.execute("PRAGMA user_version").fetchone()
                finally:
                    connection.close()
                after = os.lstat(path)
                if (after.st_dev, after.st_ino) != identity or (_POSIX and stat.S_IMODE(after.st_mode) != 0o600):
                    raise WriteError("studio.schema_file_exists")
            except BaseException:
                _remove_created(path, identity)
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
    unlock = unlocks.require_unlock(session, connection_id, Area.SCHEMA_DESIGNER)
    # E-X7 again, from the stored profile as the unlock re-read it now: dev or test only, never prod or unset.
    if unlock.environment not in CREATE_ENVIRONMENTS:
        raise WriteError("studio.write_prod_locked")
    if unlock.connection.engine != "postgresql":
        raise WriteError("studio.schema_create_engine")
    return unlock


def create_postgres_database(unlocks: Unlocks, session: str, doc: dict[str, Any]) -> CreatedDatabase:
    """Create a PostgreSQL database on the unlocked write connection (see the module text). Raises
    :class:`WriteError` or :class:`DriverFailure`."""
    request = check_database_request(doc)
    if not isinstance(doc.get("typed"), str) or doc.get("typed") != request.name:
        raise WriteError("studio.schema_create_unconfirmed")
    unlock = _target(unlocks, session, doc.get("connection_id"))
    run = secrets.token_hex(8)
    common: dict[str, object] = {
        "run": run,
        "area": Area.SCHEMA_DESIGNER.value,
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
    logger.info("PostgreSQL database created", extra={"event": "studio.schema_database_created"})
    return CreatedDatabase(request.name, request.owner, pending)


def _create(unlock: Unlock, request: DatabaseRequest) -> None:
    import psycopg
    from psycopg.rows import tuple_row

    try:
        with unlock.connection.idle_autocommit() as connection:
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
