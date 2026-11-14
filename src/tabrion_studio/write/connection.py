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

**SQLite.** The read profile's file, opened through tabrion (no link, owner and mode checks; the descriptor SQLite
holds is the checked file) and never created. Each connection of the pool then gets, in this order:

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

**Admin privileges (TX-11, E-X6).** On every connect Studio reads the role's attributes, every role reachable from
it through membership (``SET ROLE`` or inheritance) and the databases they own, and reports a finding for
``SUPERUSER``, ``BYPASSRLS``, ``CREATEROLE`` and ``REPLICATION``, held directly or through a reachable role
(:func:`privilege_findings`). What a finding blocks is fixed by STU-XDM-1 (G-S4); Studio never grants or widens its
own rights.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass, field
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

import sqlalchemy
from sqlalchemy import event
import tabrion
from tabrion.errors import ConfigurationError, UnsafePath
from tabrion.postgres import SessionSettings
from tabrion.sqlite import JournalMode, SQLiteProfile

from tabrion_studio.config.schema import Profile, Settings
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import (
    _TLS,
    APPLICATION_NAME,
    LOCK_TIMEOUT_MS,
    WRITE_STARTUP_SEARCH_PATH,
    RegistryError,
    connection_url,
    driver_options,
)
from tabrion_studio.services.readonly import ACTING_PRAGMAS, READING_PRAGMAS, SQLITE_FUNCTIONS

__all__ = [
    "WRITE_SEARCH_PATH",
    "WriteConnection",
    "WriteError",
    "defensive_available",
    "file_identity",
    "open_write_connection",
    "privilege_findings",
    "write_authorizer",
]

#: The fixed ``search_path`` of every write and admin session (K-2), as PostgreSQL reports it back (the startup
#: option's text: the catalog first, then the session's temporary schema; the same path as
#: ``tabrion_compat.CATALOG_SEARCH_PATH``).
WRITE_SEARCH_PATH = WRITE_STARTUP_SEARCH_PATH
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


class WriteError(Exception):
    """A write connection or unlock that Studio refuses; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
    " (SELECT pg_catalog.count(*) FROM pg_catalog.pg_database d WHERE d.datdba OPERATOR(pg_catalog.=) r.oid)"
    " FROM reach JOIN pg_catalog.pg_roles r ON r.oid OPERATOR(pg_catalog.=) reach.oid"
)


def privilege_findings(rows: list[tuple[Any, ...]]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The facts and findings of the privilege check (TX-11) from the rows of :data:`_PRIVILEGES`.

    One row per role reachable from the session's role (itself included, ``own``). A finding names the attribute and
    whether the role holds it ``direct`` or through a reachable role (``inherited``); never a role name.
    """
    reachable = 0
    owned = 0
    createdb = False
    held: dict[str, str] = {}
    for own, superuser, bypassrls, createrole, replication, can_createdb, databases in rows:
        reachable += 0 if own else 1
        owned += int(databases or 0)
        createdb = createdb or bool(can_createdb)
        for name, value in zip(DANGEROUS_ATTRIBUTES, (superuser, bypassrls, createrole, replication), strict=True):
            if value:
                if own:
                    held[name] = "direct"
                else:
                    held.setdefault(name, "inherited")
    facts = {"reachable_roles": reachable, "owned_databases": owned, "createdb": createdb}
    findings = [
        {"code": f"admin.{name}", "via": held[name], "severity": "warning"}
        for name in DANGEROUS_ATTRIBUTES
        if name in held
    ]
    return facts, findings


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

    def check_privileges(self) -> None:
        """Read the role's privileges (TX-11) into :attr:`facts` and :attr:`findings`; nothing on SQLite."""
        if self.engine != "postgresql":
            return
        self.facts, self.findings = privilege_findings(self.fetch(_PRIVILEGES))

    def close(self) -> None:
        self._db.close()


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


def _sqlite_database(database_file: Path, journal_mode: str | None, settings: tabrion.Profile) -> tabrion.Database:
    if not defensive_available():
        raise WriteError("studio.write_unsupported")  # fail closed on Python 3.11 (see the module text)
    mode = (journal_mode or "").lower()
    if mode not in ACCEPTED_JOURNAL_MODES:
        raise WriteError("studio.write_unsupported")
    profile = tabrion.Profile(
        sqlite=SQLiteProfile(journal_mode=JournalMode(mode)),
        pool_size=1,
        max_overflow=0,
        pool_timeout=settings.pool_timeout,
        max_rows=settings.max_rows,
        read_only=False,
    )
    url = sqlalchemy.URL.create("sqlite", database=str(database_file))
    db = tabrion.Database.from_url(url, profile=profile)
    event.listen(db.engine, "connect", _install_write_guards)
    return db


def _postgres_database(
    profile: Profile, password: str | None, settings: tabrion.Profile, *, refuse_cleartext: bool
) -> tabrion.Database:
    url = connection_url(profile, password)
    options = {
        "connect_args": driver_options(password, settings.postgres, refuse_cleartext=refuse_cleartext, write=True),
        "use_native_hstore": False,
    }
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
) -> WriteConnection:
    """Open and check a write (``kind="write"``) or admin (``"admin"``) connection for ``profile``, the read
    profile's target with the write profile's role and secret (``WriteProfile.connection_profile``).

    SQLite needs ``database_file`` (the read connection's real path), its ``identity`` taken at unlock and its current
    ``journal_mode``. Raises :class:`WriteError`, :class:`RegistryError` or a tabrion error (``db.<kind>``).
    """
    if profile.environment == "prod":
        raise WriteError("studio.write_prod_locked")  # E-X7: no code path writes to PROD
    settings, timeout = _settings(profile, limits)
    try:
        if profile.engine == "sqlite":
            if database_file is None or identity is None:
                raise WriteError("studio.write_target_changed")
            db = _sqlite_database(database_file, journal_mode, settings)
        else:
            db = _postgres_database(profile, password, settings, refuse_cleartext=refuse_cleartext)
    except FileNotFoundError:
        raise RegistryError("studio.database_missing") from None
    except UnsafePath:
        raise RegistryError("studio.database_unsafe") from None
    except ConfigurationError as exc:
        raise RegistryError(
            "studio.connection_tls" if exc.code == "config.tls" else "studio.connection_config"
        ) from None
    connection = WriteConnection(
        kind=kind,
        engine=profile.engine,
        environment=profile.environment,
        role=profile.user,
        database_file=database_file,
        identity=identity,
        timeout_s=timeout / 1000,
        _db=db,
    )
    try:
        connection.verify()
        _check_session(connection)
        connection.verify()
        connection.check_privileges()
    except BaseException:
        connection.close()
        raise
    return connection


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
