"""The connection registry: Studio's open database connections, always read-only (03 sections 5.1 and 6, STU-009).

Studio 1.0 never writes to a database. Every connection is opened with ``tabrion.Profile(read_only=True)``:

* **SQLite:** ``file:<path>?mode=ro`` and ``PRAGMA query_only=ON``, no attached database (tabrion), plus Studio's
  authorizer (:func:`tabrion_studio.services.readonly.sqlite_authorizer`) on every DBAPI connection of the pool. A
  project file is opened only through ``ProjectConfig.database_file`` (review T-1 of ``stu-006-review.md``), at the
  real path it returns.
* **PostgreSQL:** ``default_transaction_read_only=on`` for the session (tabrion), every statement in
  ``Database.read()`` (``BEGIN READ ONLY``, re-checked before ``COMMIT``), one statement per transaction and no
  transaction control (:func:`tabrion_studio.services.readonly.check_statement`).

A handle (:class:`OpenConnection`) exposes only reading. The engine and ``Database.transaction()`` never leave this
module.

**Where a password goes (C-1 of ``stu-007-review.md``, L-1 of ``stu-008-review.md``):** the driver gets the fields of
:data:`tabrion_studio.config.schema.TARGET_FIELDS` and the password, nothing else (:func:`connection_url`), plus the
constant options of :func:`driver_options`. The password is resolved with ``Configuration.resolve_entry_secret`` from
the same entry the URL comes from (L-1 of ``stu-009-review.md``) when the connection opens, and a stored one is bound
to the fingerprint of those fields. Without a password, libpq gets none of its own (``PGPASSWORD``, ``.pgpass``:
M-1). Session settings (timeouts, ``application_name``) are not connection targets. A handle is checked again on
every use: if its profile is gone, no longer trusted, points somewhere else or changed its environment, the
connection is closed (``studio.connection_stale``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Any, Protocol, TypeVar
import weakref

import sqlalchemy
from sqlalchemy import event
from sqlalchemy.pool import QueuePool
import tabrion
from tabrion.check import Finding
from tabrion.errors import ConfigurationError, Kind, UnsafePath
from tabrion.postgres import SessionSettings

from tabrion_studio.config.schema import TARGET_FIELDS, Profile, Settings, target_fingerprint
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.logs import register_secret
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.readonly import StatementRefused, check_statement, first_word, sqlite_authorizer
from tabrion_studio.services.sqltext import BoundStatement, Dialect, bind, is_bound
from tabrion_studio.services.tabrion_compat import Cancel, ResultCursor

__all__ = [
    "MAX_OPEN",
    "ConnectionRegistry",
    "OpenConnection",
    "ProfileSource",
    "RegistryError",
    "connection_facts",
    "connection_url",
    "read_only_database",
    "studio_profile",
]

logger = logging.getLogger("tabrion_studio.connections")
_T = TypeVar("_T")
#: Statements a PostgreSQL cursor (``DECLARE ... CURSOR FOR``) can hold; others (``EXPLAIN``, ``SHOW``, ...) use a
#: plain cursor. The first word comes from the same lexer as in ``check_statement`` (R-H1 of the delta re-review).
_CURSOR_WORDS = frozenset({"select", "with", "values", "table"})


def _cursor_query(statement: str) -> bool:
    return first_word(statement) in _CURSOR_WORDS


MAX_OPEN = 8
#: How long a statement waits for one of the connection's two sessions (then ``studio.connection_busy``).
POOL_TIMEOUT_S = 10.0
APPLICATION_NAME = "tabrion-studio"
#: 03 section 5.3: statement timeout 30 s, 15 s for production profiles; lock timeout 5 s.
PROD_STATEMENT_TIMEOUT_MS = 15_000
LOCK_TIMEOUT_MS = 5_000
_TLS = {
    # The parser always sets sslmode for PostgreSQL (prefer for loopback, verify-full otherwise; review I-1), so None
    # only reaches here for a profile built in code; AUTO lets tabrion require TLS for a remote host.
    None: tabrion.TLSMode.AUTO,
    "verify-full": tabrion.TLSMode.VERIFY_FULL,
    "verify-ca": tabrion.TLSMode.REQUIRE,
    "require": tabrion.TLSMode.REQUIRE,
    # Weaker modes are the user's explicit choice; D-01 marks them (weak_tls) and diagnostics (STU-014) report them.
    "prefer": tabrion.TLSMode.INSECURE,
    "allow": tabrion.TLSMode.INSECURE,
    "disable": tabrion.TLSMode.INSECURE,
}


class RegistryError(Exception):
    """A connection that cannot be opened or used; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


#: The environments a page may name as the one it confirmed (``confirmed_environment``).
ENVIRONMENTS = frozenset({"dev", "test", "prod"})


def require_prod_confirmation(environment: str, confirmed_environment: str | None) -> None:
    """D-02 on the server (TD-038): a PROD connection opens only when the caller says it confirmed PROD.

    The page asks D-02 from the profile list it loaded; that list may be stale (the profile was changed to ``prod`` in
    another tab or in ``profiles.toml``). So the caller sends the environment it confirmed for, and the server compares
    it with the profile as it reads it now. Nothing sent counts as "not confirmed" (fail closed).
    """
    if environment == "prod" and confirmed_environment != "prod":
        raise RegistryError("studio.prod_confirmation_required")


class ProfileEntryLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def source(self) -> str: ...
    @property
    def profile(self) -> Profile: ...


class ProfileSource(Protocol):
    """What the registry needs from the configuration (``tabrion_studio.api.config.Configuration``)."""

    def entry(self, profile_id: str) -> ProfileEntryLike: ...
    def resolve_entry_secret(self, entry: Any) -> str: ...
    def database_file(self, entry: Any) -> Path: ...
    def limits(self) -> Settings: ...


def connection_url(profile: Profile, password: str | None, database_file: Path | None = None) -> sqlalchemy.URL:
    """The URL the driver gets: only :data:`TARGET_FIELDS` of ``profile`` and ``password`` (binding for STU-009)."""
    target = {key: getattr(profile, key) for key in TARGET_FIELDS}
    if target["engine"] == "sqlite":
        if database_file is None:
            raise RegistryError("studio.database_missing")
        return sqlalchemy.URL.create("sqlite", database=str(database_file))
    query = {} if target["sslmode"] is None else {"sslmode": target["sslmode"]}
    return sqlalchemy.URL.create(
        "postgresql",
        username=target["user"],
        password=password,
        host=target["host"],
        port=target["port"] or 5432,
        database=target["database"],
        query=query,
    )


#: A path that cannot exist (below a regular file), for libpq's ``passfile``.
_NO_PASSFILE = str(Path(__file__) / "no-passfile")


#: The session settings tabrion sets on a new connection (``SessionSettings`` field -> setting), also given to the
#: server as startup options, so that ``RESET ALL`` on check-in restores them (M-2 of ``stu-011-review.md``).
_STARTUP_SETTINGS = {
    "statement_timeout_ms": "statement_timeout",
    "lock_timeout_ms": "lock_timeout",
    "idle_in_transaction_timeout_ms": "idle_in_transaction_session_timeout",
    "timezone": "TimeZone",
}
#: Settings that cannot go into startup options as they are (R-I1 of the delta re-review): tabrion quotes
#: ``search_path`` as identifiers, and sets ``transaction_timeout`` only on servers that know it (PostgreSQL 17),
#: while a server refuses an unknown startup parameter. Studio sets neither; a session with one is refused until the
#: startup options can match tabrion (TD-013).
_NOT_AT_STARTUP = ("search_path", "transaction_timeout_ms")
_STARTUP_VALUE = re.compile(r"[A-Za-z0-9_/+-]{1,64}")


def startup_options(session: SessionSettings) -> str:
    """libpq ``options`` for ``session``, ``default_transaction_read_only=on`` and ``extra_float_digits=3``: the values
    ``RESET ALL`` gives back.

    Only plain values (numbers, names like ``UTC``): anything else would need libpq's escaping, so it is refused.
    """
    if any(getattr(session, name) is not None for name in _NOT_AT_STARTUP):
        raise RegistryError("studio.connection_config")
    # extra_float_digits=3: float text is exact (shortest round-trip), whatever the role or database sets, so the
    # viewer's keyset (keys kept as their text, H-2 of stu-013-review.md) compares the same value it showed.
    pairs = [("default_transaction_read_only", "on"), ("extra_float_digits", "3")]
    for name, setting in _STARTUP_SETTINGS.items():
        value = getattr(session, name)
        if value is None:
            continue
        text = str(value)
        if _STARTUP_VALUE.fullmatch(text) is None:
            raise RegistryError("studio.connection_config")
        pairs.append((setting, text))
    return " ".join(f"-c {setting}={value}" for setting, value in pairs)


def pool_busy(exc: BaseException) -> bool:
    """Whether ``exc`` is tabrion's error for a pool without a free session within its timeout
    (``studio.connection_busy``, L-2 of ``stu-010-review.md``): the database is fine, Studio's sessions are in use."""
    return isinstance(exc, tabrion.TabrionError) and isinstance(exc.original, sqlalchemy.exc.TimeoutError)


def driver_options(password: str | None, session: SessionSettings | None = None) -> dict[str, Any]:
    """psycopg connection options of every Studio PostgreSQL connection; constants, not connection targets.

    * ``prepare_threshold=0``: every statement goes through the extended query protocol (a prepared statement), so the
      server refuses a text with several commands (``42601``), whatever a text split thinks the text holds (H-1 of
      ``stu-009-review.md``, ``standard_conforming_strings = off``).
    * ``passfile`` points to a path that cannot exist, and without a password ``password`` is the empty string, which
      keeps libpq from reading ``PGPASSWORD``, ``PGPASSFILE``, ``~/.pgpass`` or a service file's password (M-1).
    * ``options`` and ``application_name``: Studio's session settings as startup values (:func:`startup_options`),
      which the check-in reset (``tabrion_compat.reset_session``) restores.
    """
    options: dict[str, Any] = {"prepare_threshold": 0, "passfile": _NO_PASSFILE}
    if session is not None:
        options["options"] = startup_options(session)
        options["application_name"] = APPLICATION_NAME
    if password is None:
        options["password"] = ""
    return options


def studio_profile(profile: Profile, limits: Settings) -> tabrion.Profile:
    """The tabrion profile of a Studio connection: read-only, small pool, Studio's timeouts (03 section 5.3)."""
    timeout = profile.statement_timeout_ms
    if timeout is None:
        timeout = PROD_STATEMENT_TIMEOUT_MS if profile.environment == "prod" else limits.statement_timeout_ms
    session = SessionSettings(
        statement_timeout_ms=timeout, lock_timeout_ms=LOCK_TIMEOUT_MS, application_name=APPLICATION_NAME
    )
    return tabrion.Profile(
        postgres=session,
        application_name=APPLICATION_NAME,
        pool_size=2,
        max_overflow=0,
        pool_timeout=POOL_TIMEOUT_S,
        tls=_TLS[profile.sslmode],
        max_rows=limits.max_rows,
        read_only=True,
    )


def _install_authorizer(dbapi_connection: Any, _record: Any) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.set_authorizer(sqlite_authorizer)


def read_only_database(
    profile: Profile, limits: Settings, password: str | None, database_file: Path | None
) -> tabrion.Database:
    """A read-only ``tabrion.Database`` for ``profile`` with Studio's guards: the connection registry's, and the
    migration worker's (STU-015). Not connected yet. Raises :class:`RegistryError`."""
    url = connection_url(profile, password, database_file)
    settings = studio_profile(profile, limits)
    options: dict[str, Any] = {}
    if profile.engine != "sqlite":
        # use_native_hstore=False: no type lookup of the driver's when a connection is opened (TD-039); hstore values
        # are the server's text, as for every type the driver does not know.
        options = {"connect_args": driver_options(password, settings.postgres), "use_native_hstore": False}
    try:
        db = tabrion.Database.from_url(url, profile=settings, engine_options=options)
    except FileNotFoundError:
        raise RegistryError("studio.database_missing") from None
    except UnsafePath:
        raise RegistryError("studio.database_unsafe") from None
    except ConfigurationError as exc:
        raise RegistryError(
            "studio.connection_tls" if exc.code == "config.tls" else "studio.connection_config"
        ) from None
    if db.dialect == "sqlite":
        event.listen(db.engine, "connect", _install_authorizer)
    else:
        # M-2 of stu-011-review.md: no session setting outlives the statement that made it (after tabrion's own
        # check-in listener, which gives psycopg's read_only back to the session default).
        event.listen(db.engine, "checkin", tabrion_compat.reset_session)
        # TD-039: the connection's setup (the dialect's first-connect queries, tabrion's session settings) runs
        # under a fixed search_path; the session's own path is back before Studio uses the connection.
        event.listen(db.engine, "connect", tabrion_compat.pin_setup_path, insert=True)
        event.listen(db.engine, "connect", tabrion_compat.restore_setup_path(db.engine.dialect))
    return db


def _close_after_dispose(db: tabrion.Database) -> Callable[[Any, Any], None]:
    """Pool ``checkin`` listener: a session that comes back after ``db.close()`` is closed, not pooled.

    ``Database.close()`` is ``engine.dispose()``, which closes only the idle sessions. One that a running statement
    still holds goes back, at check-in, into the disposed pool, which nothing closes: its driver connection stays
    open until garbage collection (``ResourceWarning: unclosed database`` in whichever test happens to collect it).
    Closing it at check-in releases it as soon as the statement ends.

    A weak reference (F-2 of ``stu-011-leak-delta-review.md``): the engine's pool holds this listener, so a strong
    one would make ``db -> engine -> pool -> listener -> db`` a cycle that only the cyclic garbage collector frees. A
    Database that is gone counts as closed.
    """
    database = weakref.ref(db)

    def close_if_disposed(dbapi_connection: Any, connection_record: Any) -> None:
        current = database()
        if dbapi_connection is not None and (current is None or current.closed):
            connection_record.invalidate()

    return close_if_disposed


@dataclass(frozen=True)
class ConnectionCheck:
    """The outcome of :meth:`OpenConnection.check`: ``open``, ``busy``, ``lost`` or ``failed`` (the last two with the
    failed health)."""

    state: str
    latency_ms: float | None
    failed: tabrion.Health | None


def _no_free_session(engine: sqlalchemy.Engine) -> bool:
    """Whether every session of the engine's pool is lent out now (a long query, an export), read without a checkout.

    Read before the check (TD-034, L-2 of td-034-review.md): read after a failed check, the counts would include the
    check's own session, and a dead database would pass for a busy pool.
    """
    pool = engine.pool
    if not isinstance(pool, QueuePool):
        return False
    try:
        size = pool.size() + max(pool.overflow(), 0)
        return size > 0 and pool.checkedout() >= size
    except Exception:  # pool status is a hint; without it the check runs
        return False


@dataclass
class OpenConnection:
    """One open, read-only connection. The engine is private; only reading is offered."""

    id: str
    profile_id: str
    engine: str
    environment: str
    url: str  # tabrion.safe_url: never the password
    fingerprint: str
    database_file: Path | None
    opened_at: float
    timeout_s: float
    _db: tabrion.Database = field(repr=False)

    read_only = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.id,
            "profile_id": self.profile_id,
            "engine": self.engine,
            "environment": self.environment,
            "read_only": True,
            "url": self.url,
            "opened_at": self.opened_at,
        }

    @contextlib.contextmanager
    def _read(self) -> Iterator[Any]:
        with self._db.read(timeout=self.timeout_s) as transaction:
            yield transaction

    def prepare(self, sql: str, params: Mapping[str, object] | None = None) -> BoundStatement:
        """The one checked statement of ``sql`` with its parameters in the driver's style.

        Raises ``StatementRefused`` (``check_statement``) or ``ParameterError`` (``sqltext.bind``). Never through
        SQLAlchemy ``text()``, which reads ``:name`` inside literals too (I-2 of ``stu-009-review.md``).
        """
        statement = check_statement(sql)
        return bind(statement, params, self._dialect)

    @property
    def _dialect(self) -> Dialect:
        return "sqlite" if self.engine == "sqlite" else "postgresql"

    def stream(
        self,
        statement: BoundStatement,
        consume: Callable[[ResultCursor], _T],
        *,
        cancel: Cancel | None = None,
        catalog: bool = False,
    ) -> _T:
        """Run ``statement`` in its own ``Database.read()`` and return ``consume(cursor)`` (STU-011).

        One statement per transaction (03 section 5.1); the text is checked again here, so no caller can skip
        :meth:`prepare`'s check. ``cancel`` stops it from another thread (:mod:`.tabrion_compat`).
        """
        # L-1 of stu-011-review.md: check what is executed, the driver text, not only the text it came from.
        if not is_bound(statement, check_statement(statement.sql), self._dialect):
            raise StatementRefused("studio.statement_refused")
        hook = cancel if cancel is not None else Cancel()
        server_side = self.engine != "sqlite" and _cursor_query(statement.sql)
        with (
            self._read() as transaction,
            tabrion_compat.stream(
                transaction,
                statement.driver_sql,
                statement.values,
                cancel=hook,
                server_side=server_side,
                catalog=catalog,
            ) as cursor,
        ):
            return consume(cursor)

    def inspect(self, consume: Callable[[sqlalchemy.Inspector], _T]) -> _T:
        """``consume(inspector)`` with a SQLAlchemy inspector inside one ``Database.read()`` (STU-010).

        Reflection runs SQLAlchemy's own catalog queries, never user SQL; read-only like everything else.
        """
        with self._read() as transaction, tabrion_compat.inspector(transaction) as found:
            return consume(found)

    def tabrion_findings(self, *, cancel: Cancel | None = None) -> list[Finding]:
        """``tabrion.check`` on the driver connection of one ``Database.read()`` (STU-014; it only reads, see
        :func:`.tabrion_compat.check`). ``cancel`` stops it from another thread."""
        with self._read() as transaction:
            return tabrion_compat.check(transaction, cancel=cancel if cancel is not None else Cancel())

    def fetch(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
        *,
        cancel: Cancel | None = None,
        catalog: bool = True,
    ) -> list[tuple[Any, ...]]:
        """All rows of one read-only statement, for Studio's own small queries; a refused one raises
        ``StatementRefused``. User SQL goes through :class:`tabrion_studio.services.queries.QueryEngine` (caps).
        ``cancel`` stops it from another thread.

        ``catalog`` (the default): on PostgreSQL the statement runs under the fixed ``search_path`` of
        ``tabrion_compat.CATALOG_SEARCH_PATH``, so objects in user schemas cannot take part in resolving its functions
        and operators (STU-022 review H-1). Only a read that must find a table on the session's ``search_path`` (the
        Alembic version table) passes ``False``."""

        def every_row(cursor: ResultCursor) -> list[tuple[Any, ...]]:
            rows: list[tuple[Any, ...]] = []
            while batch := cursor.fetch(500):
                rows += batch
            return rows

        return self.stream(self.prepare(sql, params), every_row, cancel=cancel, catalog=catalog)

    def health(self) -> tabrion.Health:
        return self._db.health(timeout=min(self.timeout_s, 5.0))

    def _no_free_session(self) -> bool:
        return _no_free_session(self._db.engine)

    def check(self) -> ConnectionCheck:
        """Whether the database still answers on this connection (TD-034, the page's "Connection lost" banner).

        When every session of the pool is lent out (a long query, an export) the check does not run and the answer is
        ``busy``: it would only wait for a session (up to the pool timeout) and learn nothing about the database.
        Only a failure of the connection kind (the database does not answer) is ``lost``; any other failure (the
        check's statement timeout on a loaded server, a permission or authentication problem) is ``failed``:
        reported, but the connection is not replaced, so the queries and exports other views run on it go on.
        """
        if self._no_free_session():
            return ConnectionCheck("busy", None, None)
        health = self.health()
        if health.ok:
            return ConnectionCheck("open", health.latency_ms, None)
        classification = health.classification
        if classification is not None and classification.kind is Kind.CONNECTION:
            return ConnectionCheck("lost", None, health)
        return ConnectionCheck("failed", None, health)

    def close(self) -> None:
        self._db.close()


@dataclass
class ConnectionRegistry:
    """Open connections by id. Thread-safe; opening and closing block (call them off the event loop)."""

    source: ProfileSource
    max_open: int = MAX_OPEN
    clock: Callable[[], float] = time.time
    _open: dict[str, OpenConnection] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed_listeners: list[Callable[[str], None]] = field(default_factory=list)

    def on_close(self, listener: Callable[[str], None]) -> None:
        """Call ``listener(connection_id)`` after a connection closed (the explorer drops its cache entry)."""
        self._closed_listeners.append(listener)

    def _closed(self, connection_id: str) -> None:
        for listener in self._closed_listeners:
            with contextlib.suppress(Exception):
                listener(connection_id)

    def _credentials(self, profile_id: str, entry: Any) -> tuple[Path | None, str | None]:
        """The database file (SQLite) or the password (PostgreSQL) of ``entry``, read now."""
        profile = entry.profile
        database_file: Path | None = None
        password: str | None = None
        if profile.engine == "sqlite":
            # T-1: a project file only through database_file, which re-checks the trust and the containment now.
            # C-1 of the #48 review: the path of this very entry, the read that passed the D-02 check.
            database_file = self.source.database_file(entry)
        else:
            try:
                # L-1 of stu-009-review.md: the secret of this very entry, so the URL and the password come from
                # the same read of the profile file.
                password = self.source.resolve_entry_secret(entry)
            except SecretError as exc:
                # No reference and nothing entered: connect without a password (trust, peer or certificate
                # authentication). libpq's own fallbacks are off (driver_options), so no PGPASSWORD or .pgpass
                # password goes to a host the profile names (M-1).
                if profile.password is not None or exc.code != "studio.secret_missing":
                    raise
            if password is not None:
                register_secret(password)
        return database_file, password

    def worker_target(self, connection_id: object) -> dict[str, Any]:
        """What a migration worker needs to open the same read-only connection as ``connection_id`` (STU-015): the
        target fields, the timeouts, the database file or the password, read again now, and nothing else. The
        connection must be open and still valid (``get``)."""
        connection = self.get(connection_id)
        entry = self.source.entry(connection.profile_id)
        # I-2 of the #48 delta review: this read must still be the connection's target *and* environment (a profile
        # that became PROD after ``get`` must not hand a DEV connection's target to a worker).
        if (
            target_fingerprint(entry.profile.to_dict()) != connection.fingerprint
            or entry.profile.environment != connection.environment
        ):
            raise RegistryError("studio.connection_stale")
        database_file, password = self._credentials(connection.profile_id, entry)
        profile = entry.profile
        limits = self.source.limits()
        return {
            "profile": {
                **{key: getattr(profile, key) for key in TARGET_FIELDS},
                "name": profile.name,
                "environment": profile.environment,
                "statement_timeout_ms": profile.statement_timeout_ms,
            },
            "limits": {"statement_timeout_ms": limits.statement_timeout_ms, "max_rows": limits.max_rows},
            "database_file": None if database_file is None else str(database_file),
            "password": password,
        }

    def _connect(self, profile_id: str, confirmed_environment: str | None) -> OpenConnection:
        entry = self.source.entry(profile_id)
        profile = entry.profile
        # The same read of the profile as the connection's target: no window for a change in between.
        require_prod_confirmation(profile.environment, confirmed_environment)
        limits = self.source.limits()
        database_file, password = self._credentials(profile_id, entry)
        db = read_only_database(profile, limits, password, database_file)
        # Last of the check-in listeners: a session that a statement returns after close() leaves with the pool.
        event.listen(db.engine, "checkin", _close_after_dispose(db))
        timeout = profile.statement_timeout_ms or (
            PROD_STATEMENT_TIMEOUT_MS if profile.environment == "prod" else limits.statement_timeout_ms
        )
        connection = OpenConnection(
            id=f"c_{secrets.token_urlsafe(12)}",
            profile_id=entry.id,
            engine=profile.engine,
            environment=profile.environment,
            url=tabrion.safe_url(db.safe_url),
            fingerprint=target_fingerprint(profile.to_dict()),
            database_file=database_file,
            opened_at=self.clock(),
            timeout_s=timeout / 1000,
            _db=db,
        )
        try:
            # Connect now, so that a wrong password or an unreachable host is the answer to "open".
            health = connection.health()
        except BaseException:
            connection.close()
            raise
        if not health.ok:
            connection.close()
            raise _health_error(health)
        return connection

    def open(self, profile_id: str, confirmed_environment: str | None = None) -> OpenConnection:
        """Open (or return the open) connection of ``profile_id``.

        ``confirmed_environment`` is the environment the caller confirmed for (D-02); a PROD profile needs ``"prod"``,
        also to get back a connection that is already open (:func:`require_prod_confirmation`).
        """
        with self._lock:
            for connection in self._open.values():
                if connection.profile_id == profile_id:
                    break
            else:
                connection = None
            if connection is None and len(self._open) >= self.max_open:
                raise RegistryError("studio.connection_limit")
        if connection is not None:
            existing = self.get(connection.id)
            require_prod_confirmation(existing.environment, confirmed_environment)
            # TD-034: a connection whose database no longer answers (it restarted, or ended Studio's sessions) is not
            # returned again; "Reconnect" opens a new one in its place.
            if existing.check().state != "lost":
                return existing
            logger.info("connection lost", extra={"event": "studio.connection_lost", "profile_id": profile_id})
            self.close(existing.id)
        connection = self._connect(profile_id, confirmed_environment)
        with self._lock:
            # Another request (a "Reconnect" and another view's open at once) may have registered a connection of
            # the profile meanwhile: that one is returned and this one closed, so none is left orphaned.
            raced = next((c for c in self._open.values() if c.profile_id == profile_id), None)
            if raced is None and len(self._open) >= self.max_open:
                connection.close()
                raise RegistryError("studio.connection_limit")
            if raced is None:
                self._open[connection.id] = connection
        if raced is not None:
            connection.close()
            # D-02 as for any connection already open (D-1 of td-034-review.md): the profile may have become PROD
            # meanwhile, and the request that registered first confirmed it; this one may not have.
            require_prod_confirmation(raced.environment, confirmed_environment)
            return raced
        logger.info(
            "connection opened", extra={"event": "studio.connection_opened", "profile_id": connection.profile_id}
        )
        return connection

    def get(self, connection_id: object) -> OpenConnection:
        """The open connection ``connection_id``, checked against its profile now (else closed and refused)."""
        with self._lock:
            connection = self._open.get(connection_id) if isinstance(connection_id, str) else None
        if connection is None:
            raise RegistryError("studio.connection_not_found")
        if not self._still_valid(connection):
            self.close(connection.id)
            raise RegistryError("studio.connection_stale")
        return connection

    def _still_valid(self, connection: OpenConnection) -> bool:
        try:
            entry = self.source.entry(connection.profile_id)
            profile = entry.profile
            if target_fingerprint(profile.to_dict()) != connection.fingerprint:
                return False
            if profile.environment != connection.environment:
                return False
            if connection.database_file is not None:
                return self.source.database_file(entry) == connection.database_file
        except Exception:  # gone, no longer trusted, damaged: the connection must not be used
            return False
        return True

    def check(self, connection_id: object) -> dict[str, Any]:
        """``GET /api/connections/{id}/health``: ``{"state": "open", "latency_ms"}`` or ``{"state": "busy"}``.

        Raises :class:`RegistryError` (gone or stale, as :meth:`get`) or :class:`HealthError` when the database does
        not answer (``db.connection`` and the like). The connection stays registered: the page offers "Reconnect".
        """
        outcome = self.get(connection_id).check()
        if outcome.failed is not None:
            raise _health_error(outcome.failed)
        latency = None if outcome.latency_ms is None else round(outcome.latency_ms, 1)
        return {"state": outcome.state, "latency_ms": latency}

    def list(self) -> list[OpenConnection]:
        with self._lock:
            connections = list(self._open.values())
        return [c for c in connections if self._valid_or_closed(c)]

    def _valid_or_closed(self, connection: OpenConnection) -> bool:
        if self._still_valid(connection):
            return True
        self.close(connection.id)
        return False

    def close(self, connection_id: object) -> bool:
        with self._lock:
            connection = self._open.pop(connection_id, None) if isinstance(connection_id, str) else None
        if connection is None:
            return False
        connection.close()
        self._closed(connection.id)
        logger.info("connection closed", extra={"event": "studio.connection_closed"})
        return True

    def close_all(self) -> None:
        """Dispose every engine (on shutdown)."""
        with self._lock:
            connections = list(self._open.values())
            self._open.clear()
        for connection in connections:
            with contextlib.suppress(Exception):
                connection.close()
            self._closed(connection.id)

    def test(self, profile_id: str, confirmed_environment: str | None = None) -> dict[str, Any]:
        """The connection test (STU-008, TD-011): open, check, read the facts of :func:`connection_facts`, close;
        never keeps the connection. It connects, so a PROD profile needs ``confirmed_environment="prod"`` like
        :meth:`open` (C-2 of ``stu-008-connect-review.md``)."""
        with self._lock:
            # A test shares the limit of open connections (review I-4).
            if len(self._open) >= self.max_open:
                raise RegistryError("studio.connection_limit")
        connection = self._connect(profile_id, confirmed_environment)
        try:
            health = connection.health()
            facts = connection_facts(connection)
            # PostgreSQL: health() gives server_version_num ("160015"); the facts read the version as people write
            # it ("16.15", C-3 of stu-008-connect-review.md). SQLite's health() version is already "3.45.1".
            version = facts.pop("server_version", None) or health.server_version
            return {
                "ok": health.ok,
                "engine": connection.engine,
                "environment": connection.environment,
                "server_version": version,
                "latency_ms": None if health.latency_ms is None else round(health.latency_ms, 1),
                "read_only": True,
                "facts": facts,
            }
        finally:
            connection.close()


#: What the connection test reads besides ``Database.health()`` (03 section 2 A: role, superuser, encoding, time zone,
#: TLS). Fixed catalog queries of Studio's own, each one statement through ``OpenConnection.fetch`` (read-only, the
#: statement check, a prepared statement on PostgreSQL); none reads a table of the user.
_POSTGRES_FACTS = (
    "SELECT current_user::text, r.rolsuper, r.rolbypassrls, current_setting('server_encoding'),"
    " current_setting('TimeZone'), (SELECT s.ssl FROM pg_catalog.pg_stat_ssl s WHERE s.pid = pg_backend_pid()),"
    " current_setting('server_version')"
    " FROM pg_catalog.pg_roles r WHERE r.rolname = current_user"
)
_SQLITE_FACTS = (
    ("foreign_keys", "PRAGMA foreign_keys"),
    ("journal_mode", "PRAGMA journal_mode"),
    ("encoding", "PRAGMA encoding"),
)
_FACT_TEXT = re.compile(r"[\x20-\x7e]{0,64}")


def _fact_text(value: object) -> str | None:
    """A short printable text of a server setting, else None (the page shows its own text for a missing fact)."""
    text = value if isinstance(value, str) else None
    return text if text is not None and _FACT_TEXT.fullmatch(text) else None


def connection_facts(connection: OpenConnection) -> dict[str, Any]:
    """The facts of the connection test: on PostgreSQL ``server_version`` (``current_setting``, the first word),
    ``role``, ``superuser``, ``bypass_rls``, ``encoding``, ``timezone`` and ``tls`` (``pg_stat_ssl`` of Studio's own
    session); on SQLite ``foreign_keys``, ``journal_mode``
    and ``encoding``. A fact Studio could not read is ``None``: the test's answer is the health check, the facts are
    what the page shows next to it. A database error while reading them is logged with its kind only."""
    try:
        if connection.engine == "sqlite":
            facts: dict[str, Any] = {}
            for name, statement in _SQLITE_FACTS:
                rows = connection.fetch(statement)
                value = rows[0][0] if rows and rows[0] else None
                facts[name] = bool(value) if name == "foreign_keys" and isinstance(value, int) else _fact_text(value)
            return facts
        rows = connection.fetch(_POSTGRES_FACTS)
    except (tabrion.TabrionError, StatementRefused) as exc:
        logger.info(
            "connection facts not read", extra={"event": "studio.connection_facts", "error": type(exc).__name__}
        )
        return {}
    row = rows[0] if rows else (None,) * 7
    role, superuser, bypass_rls, encoding, timezone, tls, version = row
    # "16.15 (Debian 16.15-1.pgdg120+1)": the version without the packager's note (C-3 of the connect review).
    version_text = version.split()[0] if isinstance(version, str) and version.strip() else None
    return {
        "server_version": _fact_text(version_text),
        "role": _fact_text(role),
        "superuser": superuser if isinstance(superuser, bool) else None,
        "bypass_rls": bypass_rls if isinstance(bypass_rls, bool) else None,
        "encoding": _fact_text(encoding),
        "timezone": _fact_text(timezone),
        "tls": tls if isinstance(tls, bool) else None,
    }


class HealthError(Exception):
    """A failed health check: carries tabrion's classification (for the ``db.<kind>`` problem)."""

    def __init__(self, classification: Any) -> None:
        super().__init__("health check failed")
        self.classification = classification


def _health_error(health: tabrion.Health) -> Exception:
    if health.classification is None:
        return RegistryError("studio.connection_failed")
    return HealthError(health.classification)
