"""Write and admin connections with Studio's safeguards (STU-XW-1; STU-ADR-0006 "hostile database objects", K-2,
threats TX-11 and TX-16).

A write or admin connection is opened only by an unlock (:mod:`tabrion_studio.write.unlock`), never by a read path,
and never for a PROD profile (E-X7; the caller checks the environment from the stored profile, and
:func:`open_write_connection` refuses ``prod`` once more).

**PostgreSQL.** The write role's own session (its own user and secret, E-X8) with the read profile's target and TLS
mode. The session is not read-only, and its ``search_path`` is fixed to ``pg_catalog,pg_temp`` as a startup option
(:data:`WRITE_SEARCH_PATH`), which wins over a role or database setting and which the check-in reset (``RESET ALL``)
gives back, so no function or operator in a user schema takes part in resolving the names of a write statement
(K-2, TX-09). The path is read back after the connection opened; any other value refuses the connection. Every name
in a write statement is still schema-qualified by the work packages that build statements (STU-XW-3, STU-026).
Triggers and rules on a target table run with the write role: that is the residual risk "the role is the boundary".

**SQLite.** The read profile's file, and it is **never created** (review L-3 of PR #111). Each connection of the pool
is opened by :func:`_open_existing`: first with the URI ``file:<path>?mode=rw`` (SQLite fails on a missing file and
creates nothing), then through ``tabrion.sqlite.connect(..., create=False)``, the connection Studio uses (no link,
owner and mode checks; the descriptor SQLite holds is the checked file; a missing name is refused, never created).
tabrion's engine from a URL would create a missing file, and it takes no URI, so Studio builds the engine itself
(``tabrion.Database.from_engine``). Each connection of the pool then gets, in this order:

* ``PRAGMA trusted_schema = OFF``: a view or trigger of the file cannot call a function that is not innocuous;
* ``SQLITE_DBCONFIG_DEFENSIVE`` (``sqlite3.Connection.setconfig``, Python 3.12 and newer): no statement can corrupt
  the schema (``writable_schema``, shadow tables of virtual tables, ``journal_mode=OFF`` and the like). **Python 3.11
  cannot set it**; there Studio refuses a SQLite write connection (``studio.write_unsupported``) rather than open one
  without it (fail closed; :func:`defensive_available`);
* Studio's write authorizer (:func:`write_authorizer`): reading as on a read connection, plus ``INSERT``,
  ``UPDATE`` and ``DELETE`` of the file's own tables; no ``ATTACH`` or ``DETACH``, no schema change, no write to the
  schema tables, no ``load_extension`` or any function outside the read allowlist, and no pragma that writes, except
  ``query_only`` (tabrion's ``read()`` turns it on and off). A trigger of a hostile file runs inside these limits.

The journal mode is left as the file has it (only ``wal``, ``delete`` and ``truncate`` are accepted; anything else,
``off`` or ``memory`` among them, refuses the connection): opening a write connection changes nothing in the file.
The file's identity (device and inode; on Windows the volume serial number and file ID that ``os.stat`` reports) is
taken at unlock and compared again after the connection opened and before each use (:meth:`WriteConnection.verify`),
so a file swapped behind the unlock stops it (TX-16; STU-XW-3 calls it again before each run).

**Authentication (STU-XW-4, review L-2 of PR #111, I-109 j, TX-07).** Every PostgreSQL write and admin connection, and
with it the privilege check that runs on it, sets libpq's ``require_auth``, whatever the profile or its owner:
``!password,!md5`` (:data:`REQUIRE_AUTH`), so libpq never answers a server that asks for the password in clear text
(``password``, and LDAP, PAM, RADIUS and BSD authentication, which use the same exchange) or as an MD5 hash; a
connection that sends a password is ``scram-sha-256`` (:data:`REQUIRE_AUTH_SCRAM`) for every host, a loopback address
and a socket folder included (review N-2 of PR #116), so a process that took over a local port or socket cannot make
libpq skip authentication or use another method. A write profile may name the server's operating system user
(``requirepeer``, PostgreSQL over a Unix socket only): libpq then refuses a socket whose process runs as another user
before any authentication exchange. SCRAM over loopback TCP without TLS remains open to a process that took over the
port first: it learns no password, but can try to guess it offline from the exchange (threat model, TX-07 residual
risk). The option exists from libpq 16 on; with an older libpq (``psycopg.pq.version()``) Studio refuses the write
connection and the unlock before any secret is read (``studio.require_auth_unavailable``, fail closed), where a read
connection of a project profile only logs a warning.

**Admin privileges (TX-11, E-X6).** On every connect Studio reads the role's attributes, every role reachable from
it through membership (``SET ROLE``, inheritance or the ADMIN option: every row of ``pg_auth_members``), the databases
they own and the server version, and reports (:func:`privilege_findings`), each held directly or through a reachable
role:

* ``admin.superuser``, ``admin.bypassrls``, ``admin.createrole``, ``admin.replication``: the attributes (``warning``);
* ``admin.createrole_pre16`` (``dangerous``): ``CREATEROLE`` on PostgreSQL before 16, where it can grant membership
  in almost any role that is not a superuser (tabrion ADR 0015 D-1, review I-1 of PR #111);
* ``admin.broad:<role>``: membership in a predefined role (a ``pg_*`` role **and** an oid below
  ``FirstNormalObjectId``, both checked by the query and again by :func:`privilege_findings`; a role a user created
  under a ``pg_`` name is not one), as tabrion's ``role_privileges`` reports ``broad:<role>`` (ADR 0015 D-2).
  ``pg_execute_server_program``, ``pg_read_server_files`` and ``pg_write_server_files`` are superuser-equivalent
  (``dangerous``); every other one, ``pg_read_all_data`` and ``pg_write_all_data`` among them, is a ``warning``.

``admin.createdb`` (``warning``, :func:`createdb_findings`): CREATEDB, held directly or through a reachable role, on
an unlock that does not need it: a ``kind="write"`` unlock while the Data Editor's flag is on (the same role then
edits rows too), and any unlock outside DEV and TEST. The Schema Designer on DEV or TEST needs CREATEDB, so there it
is the fact ``createdb`` only (open point 1 of the PR #128 review: accepted, with a separate role recommended).

The only role names a finding carries are those of predefined roles (fixed names of PostgreSQL); a role of the
database's own is never named.

**What a finding refuses (owner decision 1A at G-S2, review I-3 of PR #111).** For ``kind="write"``,
:func:`open_write_connection` refuses the connection, and with it the unlock, with
``405 studio.write_role_over_privileged`` when the role holds, directly or through a reachable role, any of
:data:`WRITE_REFUSING_FINDINGS`: ``admin.superuser``, ``admin.bypassrls``, ``admin.replication`` or membership in a
server-file role (``admin.broad:pg_read_server_files``, ``admin.broad:pg_write_server_files``,
``admin.broad:pg_execute_server_program``). The connection is closed before anything is unlocked. Every other finding
(``admin.createrole``, ``admin.createrole_pre16``, every other ``admin.broad:<role>``, ``pg_read_all_data`` and
``pg_write_all_data`` among them) stays a warning shown in the write state. For ``admin`` nothing refuses yet:
STU-XDM-1 decides it at G-S4. Studio never grants or widens its own rights.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass, field
import functools
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any

import sqlalchemy
from sqlalchemy import event
from sqlalchemy.pool import QueuePool
import tabrion
from tabrion.errors import ConfigurationError, UnsafePath
from tabrion.postgres import SessionSettings
from tabrion.sqlite import JournalMode, SQLiteProfile
from tabrion.sqlite import connect as tabrion_sqlite_connect

from tabrion_studio.config.schema import Profile, Settings
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import (
    _TLS,
    APPLICATION_NAME,
    LOCK_TIMEOUT_MS,
    REQUIRE_AUTH_LIBPQ,
    WRITE_STARTUP_SEARCH_PATH,
    RegistryError,
    connection_url,
    driver_options,
    libpq_version,
    pin_libpq_environment,
)
from tabrion_studio.services.readonly import ACTING_PRAGMAS, READING_PRAGMAS, SQLITE_FUNCTIONS

__all__ = [
    "REQUIRE_AUTH",
    "REQUIRE_AUTH_SCRAM",
    "WRITE_REFUSING_FINDINGS",
    "WRITE_SEARCH_PATH",
    "WriteConnection",
    "WriteError",
    "createdb_findings",
    "defensive_available",
    "file_identity",
    "open_write_connection",
    "privilege_findings",
    "refusing_findings",
    "refusing_findings_now",
    "require_auth",
    "require_auth_supported",
    "write_authorizer",
]

#: The fixed ``search_path`` of every write and admin session (K-2), as PostgreSQL reports it back (the startup
#: option's text: the catalog first, then the session's temporary schema; the same path as
#: ``tabrion_compat.CATALOG_SEARCH_PATH``).
WRITE_SEARCH_PATH = WRITE_STARTUP_SEARCH_PATH
#: libpq ``require_auth`` of every write and admin connection (review L-2 of PR #111): no clear-text, no MD5 password.
REQUIRE_AUTH = "!password,!md5"
#: libpq ``require_auth`` of a write or admin connection that sends a password, to any host (I-109 j; N-2 of PR #116:
#: also on loopback and over a Unix socket).
REQUIRE_AUTH_SCRAM = "scram-sha-256"
#: The journal modes a write connection accepts; it never changes the file's own.
ACCEPTED_JOURNAL_MODES = frozenset({"wal", "delete", "truncate"})
#: Schema tables a statement may never write, also not with ``writable_schema`` (defensive mode refuses that too).
_SCHEMA_TABLES = frozenset({"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema"})
_ROW_CHANGES = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})
_READING = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_RECURSIVE,
    }
)
#: Attributes that make a role over-privileged for Studio (E-X6): a finding when held directly or by a reachable role.
DANGEROUS_ATTRIBUTES = ("superuser", "bypassrls", "createrole", "replication")
#: Predefined roles that are superuser-equivalent (read or write any server file, run a server program): ``dangerous``.
SERVER_FILE_ROLES = frozenset({"pg_execute_server_program", "pg_read_server_files", "pg_write_server_files"})
#: The findings that refuse a ``kind="write"`` connection and its unlock (owner decision 1A at G-S2): superuser,
#: BYPASSRLS and REPLICATION, and membership in a server-file role, each held directly or through a reachable role.
#: CREATEROLE (also before PostgreSQL 16) and every other predefined role stay warnings.
WRITE_REFUSING_FINDINGS = frozenset(
    {"admin.superuser", "admin.bypassrls", "admin.replication"} | {f"admin.broad:{role}" for role in SERVER_FILE_ROLES}
)
#: The environments where the Schema Designer creates databases, so a write role with CREATEDB there is expected.
CREATEDB_ENVIRONMENTS = frozenset({"dev", "test"})
#: PostgreSQL ``FirstNormalObjectId``: every predefined role has a smaller oid.
FIRST_NORMAL_OBJECT_ID = 16384
#: The first PostgreSQL version (``server_version_num``) where CREATEROLE no longer reaches almost every role (D-1).
PG16 = 160000
logger = logging.getLogger("tabrion_studio.write")
_PREDEFINED_NAME = re.compile(r"pg_[a-z0-9_]{1,59}")


class WriteError(Exception):
    """A write connection or unlock that Studio refuses; ``code`` is a problem code."""

    def __init__(self, code: str, issues: list[tuple[str, str]] | None = None) -> None:
        super().__init__(code)
        self.code = code
        #: Issues of the problem (pointer, code): fixed words and numbers only (``problems.problem`` checks them).
        self.issues = issues


#: ``sqlite3.SQLITE_DBCONFIG_DEFENSIVE`` (Python 3.12+); None where this Python does not have it.
_DEFENSIVE: int | None = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)


def defensive_available() -> bool:
    """Whether this Python can set ``SQLITE_DBCONFIG_DEFENSIVE`` (``Connection.setconfig``, Python 3.12+)."""
    return _DEFENSIVE is not None and hasattr(sqlite3.Connection, "setconfig")


def write_authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    """``SQLITE_OK`` for what a SQLite write connection may do (reading and row changes), ``SQLITE_DENY`` else."""
    if action in _READING:
        return sqlite3.SQLITE_OK
    if action in _ROW_CHANGES:
        table = (arg1 or "").lower()
        if table in _SCHEMA_TABLES or table.startswith("sqlite_stat"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (arg2 or "").lower() in SQLITE_FUNCTIONS else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        name = (arg1 or "").lower()
        if name in ACTING_PRAGMAS:
            return sqlite3.SQLITE_DENY
        if arg2 is None or name in READING_PRAGMAS:
            return sqlite3.SQLITE_OK
        # The one pragma write on the allowlist: tabrion's read() turns query_only on and off around a read.
        return sqlite3.SQLITE_OK if name == "query_only" else sqlite3.SQLITE_DENY
    # ATTACH, DETACH, every CREATE, DROP and ALTER, ANALYZE, REINDEX, virtual tables: denied.
    return sqlite3.SQLITE_DENY


def require_auth_supported() -> None:
    """Refuse (``studio.require_auth_unavailable``) when the loaded libpq is older than 16 and has no
    ``require_auth``: a write or admin connection is never opened without it (fail closed)."""
    if libpq_version() < REQUIRE_AUTH_LIBPQ:
        logger.warning(
            "libpq %d cannot refuse clear-text or MD5 password requests; write connections need libpq 16 or newer",
            libpq_version(),
            extra={"event": "studio.require_auth_unavailable"},
        )
        raise WriteError("studio.require_auth_unavailable")


def require_auth(password: str | None) -> str:
    """The ``require_auth`` value of a write or admin connection (see the module text): SCRAM whenever a password is
    sent, whatever the host (N-2 of PR #116); otherwise no clear-text and no MD5 exchange."""
    return REQUIRE_AUTH_SCRAM if password is not None else REQUIRE_AUTH


def _install_write_guards(dbapi_connection: Any, _record: Any) -> None:
    """Pool ``connect`` listener (SQLite): ``trusted_schema`` off, defensive mode on, then the write authorizer.

    It runs after tabrion's own pragmas (they are set when tabrion opens the file); the authorizer comes last, since
    it denies setting these pragmas.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    dbapi_connection.execute("PRAGMA trusted_schema = OFF")
    configure = getattr(dbapi_connection, "setconfig", None)
    if configure is None or _DEFENSIVE is None:  # pragma: no cover - refused before the engine is made (3.11)
        raise WriteError("studio.write_unsupported")
    configure(_DEFENSIVE, True)
    dbapi_connection.set_authorizer(write_authorizer)


def file_identity(path: Path) -> tuple[int, int]:
    """``(device, inode)`` of the regular file ``path``, not through a link (on Windows: volume serial, file ID)."""
    try:
        info = os.lstat(path)
    except OSError:
        raise WriteError("studio.write_target_changed") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise WriteError("studio.write_target_changed")
    return info.st_dev, info.st_ino


_PRIVILEGES = (
    "WITH RECURSIVE reach(oid) AS ("
    " SELECT r.oid FROM pg_catalog.pg_roles r WHERE r.rolname OPERATOR(pg_catalog.=) pg_catalog.current_user()"
    " UNION"
    " SELECT m.roleid FROM pg_catalog.pg_auth_members m JOIN reach ON m.member OPERATOR(pg_catalog.=) reach.oid"
    ")"
    " SELECT r.rolname OPERATOR(pg_catalog.=) pg_catalog.current_user() AS own,"
    " r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolreplication, r.rolcreatedb,"
    " (SELECT pg_catalog.count(*) FROM pg_catalog.pg_database d WHERE d.datdba OPERATOR(pg_catalog.=) r.oid),"
    # The name only of a predefined role (an oid below FirstNormalObjectId, a pg_ name); never a database's own role.
    " CASE WHEN r.oid OPERATOR(pg_catalog.<) 16384::pg_catalog.oid"
    " AND pg_catalog.starts_with(r.rolname::pg_catalog.text, 'pg_') THEN r.rolname::pg_catalog.text END,"
    # Whether the session's role is a member of it itself (direct) or only through another role (inherited).
    " EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles s"
    " ON s.oid OPERATOR(pg_catalog.=) m.member"
    " WHERE m.roleid OPERATOR(pg_catalog.=) r.oid AND s.rolname OPERATOR(pg_catalog.=) pg_catalog.current_user()),"
    # The oid, so the findings check both conditions of a predefined role themselves (tabrion ADR 0015 D-2).
    " r.oid::pg_catalog.int8"
    " FROM reach JOIN pg_catalog.pg_roles r ON r.oid OPERATOR(pg_catalog.=) reach.oid"
)
_SERVER_VERSION = "SELECT pg_catalog.current_setting('server_version_num')"


def _predefined(name: object, oid: object) -> str | None:
    """``name`` if it is a predefined role: a ``pg_`` name as PostgreSQL spells them **and** an oid below
    :data:`FIRST_NORMAL_OBJECT_ID` (tabrion ADR 0015 D-2), else None. A role a user created is never predefined, also
    when it is called ``pg_something`` (a superuser can rename one so through the catalog), and an unknown oid counts
    as not predefined (never a free name)."""
    if type(oid) is not int or not 0 < oid < FIRST_NORMAL_OBJECT_ID:
        return None
    return name if isinstance(name, str) and _PREDEFINED_NAME.fullmatch(name) else None


def privilege_findings(
    rows: list[tuple[Any, ...]], server_version: int | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The facts and findings of the privilege check (TX-11) from the rows of :data:`_PRIVILEGES`.

    One row per role reachable from the session's role (itself included, ``own``), its oid last. A finding names the
    attribute or the predefined role and whether the role holds it ``direct`` or through a reachable role
    (``inherited``); never the name of a role that is not predefined. ``server_version`` is ``server_version_num``
    (None: not known, then CREATEROLE counts as before 16: fail closed).
    """
    reachable = 0
    owned = 0
    createdb: str | None = None
    held: dict[str, str] = {}
    broad: dict[str, str] = {}
    for own, superuser, bypassrls, createrole, replication, can_createdb, databases, predefined, member, oid in rows:
        reachable += 0 if own else 1
        owned += int(databases or 0)
        if can_createdb and createdb != "direct":
            createdb = "direct" if own else "inherited"
        for name, value in zip(DANGEROUS_ATTRIBUTES, (superuser, bypassrls, createrole, replication), strict=True):
            if value:
                if own:
                    held[name] = "direct"
                else:
                    held.setdefault(name, "inherited")
        role = None if own else _predefined(predefined, oid)
        if role is not None:
            if member:
                broad[role] = "direct"
            else:
                broad.setdefault(role, "inherited")
    facts = {
        "reachable_roles": reachable,
        "owned_databases": owned,
        "createdb": createdb is not None,
        "createdb_via": createdb,
        "server_version": server_version,
    }
    findings = [
        {"code": f"admin.{name}", "via": held[name], "severity": "warning"}
        for name in DANGEROUS_ATTRIBUTES
        if name in held
    ]
    if "createrole" in held and (server_version is None or server_version < PG16):
        # tabrion ADR 0015 D-1: before PostgreSQL 16, CREATEROLE can grant membership in almost any role.
        findings.append({"code": "admin.createrole_pre16", "via": held["createrole"], "severity": "dangerous"})
    for role in sorted(broad):
        # tabrion ADR 0015 D-2: membership in a predefined role is reported as broad:<role>.
        severity = "dangerous" if role in SERVER_FILE_ROLES else "warning"
        findings.append({"code": f"admin.broad:{role}", "via": broad[role], "severity": severity})
    return facts, findings


def createdb_findings(
    facts: dict[str, Any], *, kind: str, environment: str, data_editor_on: bool
) -> list[dict[str, str]]:
    """``admin.createdb`` (``warning``, never refusing) for a role with CREATEDB, held directly or through a reachable
    role, where Studio does not need it: a ``kind="write"`` unlock that the Data Editor may use too (its flag is on),
    and any unlock outside DEV and TEST (open point 1 of the PR #128 review, condition b). The Schema Designer on
    DEV or TEST needs CREATEDB, so there it is a fact only; a separate role for it is recommended (security guide)."""
    via = facts.get("createdb_via")
    if not facts.get("createdb") or not isinstance(via, str):
        return []
    if environment in CREATEDB_ENVIRONMENTS and not (kind == "write" and data_editor_on):
        return []
    return [{"code": "admin.createdb", "via": via, "severity": "warning"}]


def refusing_findings(findings: list[dict[str, str]]) -> list[str]:
    """The codes of ``findings`` that refuse a ``kind="write"`` unlock (:data:`WRITE_REFUSING_FINDINGS`), sorted."""
    return sorted({f["code"] for f in findings if f.get("code") in WRITE_REFUSING_FINDINGS})


def refusing_findings_now(fetch: Callable[[str], list[tuple[Any, ...]]]) -> list[str]:
    """Decision 1A again at the start of a run (review L-1 of PR #127): the privilege check read through ``fetch``,
    which runs one of Studio's fixed statements on the very session the run uses, and the codes of
    :data:`WRITE_REFUSING_FINDINGS` among its findings. A role that gained such a right after its unlock is caught
    before the run's statement."""
    _, findings = privilege_findings(fetch(_PRIVILEGES), _server_version(fetch(_SERVER_VERSION)))
    return refusing_findings(findings)


def _server_version(rows: list[tuple[Any, ...]]) -> int | None:
    try:
        return int(rows[0][0])
    except (IndexError, TypeError, ValueError):
        return None


@dataclass
class WriteConnection:
    """One open write or admin connection. Kept by its unlock only; never registered with the read registry."""

    kind: str
    engine: str
    environment: str
    role: str | None
    database_file: Path | None
    identity: tuple[int, int] | None
    timeout_s: float
    _db: tabrion.Database = field(repr=False)
    facts: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    #: The engine Studio built itself (SQLite, ``Database.from_engine``): disposed on :meth:`close`.
    _owned_engine: sqlalchemy.Engine | None = field(default=None, repr=False)

    read_only = False

    def verify(self) -> None:
        """The SQLite file is still the one checked at unlock (TX-16); raises :class:`WriteError` otherwise."""
        if self.database_file is not None and file_identity(self.database_file) != self.identity:
            raise WriteError("studio.write_target_changed")

    def fetch(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        """The rows of one of Studio's own fixed reading statements on this connection (the privilege check)."""
        rows = self._db.fetch_all(sqlalchemy.text(sql), params, timeout=self.timeout_s)
        return [tuple(row) for row in rows]

    @contextlib.contextmanager
    def transaction(self) -> Iterator[tabrion.Transaction]:
        """One write transaction, for the work packages that run a confirmed plan (STU-XW-3, STU-026).

        No route of STU-XW-1 calls it. The file identity is checked first (TX-16)."""
        self.verify()
        with self._db.transaction(timeout=self.timeout_s) as transaction:
            yield transaction

    @contextlib.contextmanager
    def idle_autocommit(self) -> Iterator[Any]:
        """One session of this PostgreSQL write connection as an idle ``psycopg.Connection`` in autocommit mode.

        For the statements that cannot run in a transaction and that tabrion runs only on such a connection
        (``tabrion.postgres.create_database``, contract section 4.8, I-190; STU-CR-3). An open transaction of the
        pooled session is rolled back first; afterwards the session is switched back and returned to the pool, where
        the check-in reset (``RESET ALL``) restores the fixed ``search_path`` (K-2). A session that cannot be switched
        back is discarded, never reused. Raises :class:`WriteError` (``studio.write_unsupported``) on SQLite."""
        if self.engine != "postgresql":
            raise WriteError("studio.write_unsupported")
        self.verify()
        import psycopg

        pooled = self._db.engine.raw_connection()
        try:
            driver = pooled.driver_connection
            if not isinstance(driver, psycopg.Connection):  # pragma: no cover - the engine is psycopg's
                raise WriteError("studio.write_unsupported")
            if driver.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                driver.rollback()
            driver.autocommit = True
            try:
                yield driver
            finally:
                try:
                    driver.autocommit = False
                except Exception:
                    pooled.invalidate()
                    raise
        finally:
            pooled.close()

    def inspect(self, consume: Callable[[sqlalchemy.Inspector], Any]) -> Any:
        """``consume(inspector)`` with tabrion's ``Database.inspector()`` on this write connection, inside one of its
        read-only transactions (the Schema Designer's level-2 snapshot, STU-CR-4). The file identity is checked
        first (TX-16); on PostgreSQL the session's ``search_path`` is already fixed (K-2) and pinned again for the
        catalog queries, as on a read connection."""
        self.verify()
        with self._db.inspector() as found:
            tabrion_compat.pin_catalog_path(found)
            return consume(found)

    def engine_version(self) -> tuple[int, ...]:
        """The engine version a schema change plan is made for (STU-ADR-0007 section 9, I-124 e, GS3-13): on SQLite
        ``sqlite_version_info`` of the library this connection's engine loaded, on PostgreSQL the server's
        ``server_version_num`` read on this connection (``160004`` is ``(16, 4)``, ``90624`` is ``(9, 6, 24)``).
        Never None: an unreadable version raises :class:`WriteError` (``studio.write_unsupported``)."""
        if self.engine == "sqlite":
            info = getattr(self._db.engine.dialect.dbapi, "sqlite_version_info", None)
            if not isinstance(info, tuple) or not info or not all(type(p) is int and p >= 0 for p in info):
                raise WriteError("studio.write_unsupported")
            return tuple(info)
        number = _server_version(self.fetch(_SERVER_VERSION))
        if number is None or number <= 0:
            raise WriteError("studio.write_unsupported")
        if number >= 100000:
            return (number // 10000, number % 10000)
        return (number // 10000, number // 100 % 100, number % 100)

    def check_privileges(self) -> None:
        """Read the role's privileges (TX-11) into :attr:`facts` and :attr:`findings`; nothing on SQLite."""
        if self.engine != "postgresql":
            return
        version = _server_version(self.fetch(_SERVER_VERSION))
        self.facts, self.findings = privilege_findings(self.fetch(_PRIVILEGES), version)

    def close(self) -> None:
        self._db.close()
        if self._owned_engine is not None:
            self._owned_engine.dispose()


def _settings(profile: Profile, limits: Settings) -> tuple[tabrion.Profile, int]:
    timeout = profile.statement_timeout_ms or limits.statement_timeout_ms
    session = SessionSettings(
        statement_timeout_ms=timeout, lock_timeout_ms=LOCK_TIMEOUT_MS, application_name=APPLICATION_NAME
    )
    return (
        tabrion.Profile(
            postgres=session,
            application_name=APPLICATION_NAME,
            pool_size=1,
            max_overflow=0,
            pool_timeout=10.0,
            tls=_TLS[profile.sslmode],
            max_rows=limits.max_rows,
            read_only=False,
        ),
        timeout,
    )


def _open_existing(path: str, profile: SQLiteProfile) -> sqlite3.Connection:
    """The pool's creator (SQLite): open the existing file ``path``, never create it (review L-3 of PR #111).

    The URI ``file:<path>?mode=rw`` fails on a missing file without creating one (and on a file that cannot be
    written); no statement runs on it, so it changes nothing. The connection Studio uses is then tabrion's checked one
    with ``create=False``, which refuses a missing name too. A missing file is ``studio.write_target_changed``."""
    try:
        probe = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=rw", uri=True, isolation_level=None)
    except sqlite3.Error:
        if not os.path.lexists(path):
            raise WriteError("studio.write_target_changed") from None
        raise WriteError("studio.write_unsupported") from None
    try:
        return tabrion_sqlite_connect(path, profile=profile, create=False, check_same_thread=False)
    except FileNotFoundError:
        raise WriteError("studio.write_target_changed") from None
    finally:
        probe.close()


def _sqlite_database(
    database_file: Path, journal_mode: str | None, settings: tabrion.Profile
) -> tuple[tabrion.Database, sqlalchemy.Engine]:
    if not defensive_available():
        raise WriteError("studio.write_unsupported")  # fail closed on Python 3.11 (see the module text)
    mode = (journal_mode or "").lower()
    if mode not in ACCEPTED_JOURNAL_MODES:
        raise WriteError("studio.write_unsupported")
    sqlite_profile = SQLiteProfile(journal_mode=JournalMode(mode))
    profile = tabrion.Profile(
        sqlite=sqlite_profile,
        pool_size=1,
        max_overflow=0,
        pool_timeout=settings.pool_timeout,
        max_rows=settings.max_rows,
        read_only=False,
    )
    # The engine is Studio's own (tabrion's from_url would create a missing file): the creator opens the existing
    # file only, tabrion's profile is applied by tabrion.sqlite.connect, and the pool is the one from_url would make.
    engine = sqlalchemy.create_engine(
        sqlalchemy.URL.create("sqlite+pysqlite", database=str(database_file)),
        creator=functools.partial(_open_existing, str(database_file), sqlite_profile),
        poolclass=QueuePool,
        pool_size=profile.pool_size,
        max_overflow=profile.max_overflow,
        pool_timeout=profile.pool_timeout,
        pool_pre_ping=profile.pool_pre_ping,
        pool_recycle=profile.pool_recycle,
        hide_parameters=True,
    )
    event.listen(engine, "connect", _install_write_guards)
    try:
        # install_listeners=False: the profile is already applied by the creator, and a checkout listener would
        # run after the write authorizer, which refuses its pragmas. tabrion's begin listener is installed either way.
        db = tabrion.Database.from_engine(engine, profile=profile, install_listeners=False)
    except BaseException:
        engine.dispose()
        raise
    return db, engine


def _postgres_database(
    profile: Profile,
    password: str | None,
    settings: tabrion.Profile,
    *,
    refuse_cleartext: bool,
    requirepeer: str | None = None,
) -> tabrion.Database:
    # Before the URL with the password is built: no write connection without require_auth (L-2 of PR #111).
    require_auth_supported()
    if requirepeer is not None and not (profile.host or "").startswith("/"):
        # libpq checks the peer only on a Unix socket; never silently drop the check (fail closed, N-2 of PR #116).
        raise WriteError("studio.write_coupling_invalid")
    url = connection_url(profile, password)
    connect_args = driver_options(password, settings.postgres, refuse_cleartext=refuse_cleartext, write=True)
    # Always, whatever the profile's owner (refuse_cleartext is only the read path's project-profile rule).
    connect_args["require_auth"] = require_auth(password)
    if requirepeer is not None:
        connect_args["requirepeer"] = requirepeer
    # G2-01: no libpq environment variable (PGHOSTADDR, PGSERVICE, ...) turns the socket or loopback target into
    # another server, past requirepeer and the loopback rules of check_transport.
    connect_args.update(pin_libpq_environment(url, connect_args))
    options = {"connect_args": connect_args, "use_native_hstore": False}
    db = tabrion.Database.from_url(url, profile=settings, engine_options=options)
    event.listen(db.engine, "checkin", tabrion_compat.reset_session)
    event.listen(db.engine, "connect", tabrion_compat.pin_setup_path, insert=True)
    event.listen(db.engine, "connect", tabrion_compat.restore_setup_path(db.engine.dialect))
    return db


def open_write_connection(
    kind: str,
    profile: Profile,
    limits: Settings,
    *,
    password: str | None = None,
    database_file: Path | None = None,
    journal_mode: str | None = None,
    identity: tuple[int, int] | None = None,
    refuse_cleartext: bool = False,
    requirepeer: str | None = None,
) -> WriteConnection:
    """Open and check a write (``kind="write"``) or admin (``"admin"``) connection for ``profile``, the read
    profile's target with the write profile's role and secret (``WriteProfile.connection_profile``).

    SQLite needs ``database_file`` (the read connection's real path), its ``identity`` taken at unlock and its current
    ``journal_mode``. ``requirepeer`` (PostgreSQL over a Unix socket) is the server's OS user that libpq checks.
    A ``kind="write"`` role with a finding of :data:`WRITE_REFUSING_FINDINGS` is refused
    (``studio.write_role_over_privileged``) and its connection closed (owner decision 1A at G-S2).
    Raises :class:`WriteError`, :class:`RegistryError` or a tabrion error (``db.<kind>``).
    """
    if profile.environment == "prod":
        raise WriteError("studio.write_prod_locked")  # E-X7: no code path writes to PROD
    settings, timeout = _settings(profile, limits)
    owned: sqlalchemy.Engine | None = None
    with _registry_errors():
        if profile.engine == "sqlite":
            if database_file is None or identity is None:
                raise WriteError("studio.write_target_changed")
            # Before anything opens it: a missing or swapped file is refused here, and nothing is created (L-3).
            if file_identity(database_file) != identity:
                raise WriteError("studio.write_target_changed")
            db, owned = _sqlite_database(database_file, journal_mode, settings)
        else:
            db = _postgres_database(
                profile, password, settings, refuse_cleartext=refuse_cleartext, requirepeer=requirepeer
            )
    connection = WriteConnection(
        kind=kind,
        engine=profile.engine,
        environment=profile.environment,
        role=profile.user,
        database_file=database_file,
        identity=identity,
        timeout_s=timeout / 1000,
        _db=db,
        _owned_engine=owned,
    )
    try:
        with _registry_errors():
            connection.verify()
            _check_session(connection)
            connection.verify()
            connection.check_privileges()
        if kind == "write" and refusing_findings(connection.findings):
            # Owner decision 1A (G-S2): an over-privileged write role is never unlocked. Fixed codes only, no role name.
            logger.warning(
                "the write role has superuser, BYPASSRLS, REPLICATION or server-file rights; the unlock is refused",
                extra={"event": "studio.write_role_over_privileged", "code": "studio.write_role_over_privileged"},
            )
            raise WriteError("studio.write_role_over_privileged")
    except BaseException:
        connection.close()
        raise
    return connection


@contextlib.contextmanager
def _registry_errors() -> Iterator[None]:
    """tabrion's errors of opening a database as Studio's problems (the SQLite pool opens on first use)."""
    try:
        yield
    except FileNotFoundError:
        raise RegistryError("studio.database_missing") from None
    except UnsafePath:
        raise RegistryError("studio.database_unsafe") from None
    except ConfigurationError as exc:
        raise RegistryError(
            "studio.connection_tls" if exc.code == "config.tls" else "studio.connection_config"
        ) from None


def _check_session(connection: WriteConnection) -> None:
    """Connect now and read back what the safeguards set; a session without them is refused."""
    if connection.engine == "sqlite":
        rows = connection.fetch("PRAGMA trusted_schema")
        if not rows or rows[0][0] not in (0, False):
            raise WriteError("studio.write_unsupported")
        return
    rows = connection.fetch(
        "SELECT pg_catalog.current_setting('search_path'), pg_catalog.current_setting('default_transaction_read_only')"
    )
    if not rows or rows[0][0] != WRITE_SEARCH_PATH:
        raise WriteError("studio.write_unsupported")
