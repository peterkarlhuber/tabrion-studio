"""The tabrion 0.1 workarounds of the query engine, in one place (STU-011; replaced with TAB-033 / tabrion 0.2).

Studio's query engine needs three things that tabrion 0.1 does not offer yet (03 section 9, doc 04 sections 5.3 to
5.6, backlog TAB-042): ``Transaction.stream`` (rows in batches from a server-side cursor), ``Transaction.cancel``
(from another thread) and the column metadata of ``tabrion.Result``. Until tabrion 0.2, this module builds them from
the public 0.1 surface: ``Database.read()`` and ``Transaction.connection`` (the SQLAlchemy connection, and through
it the driver's own connection). Nothing else in Studio touches a DBAPI connection, so the move to 0.2 changes this
file only (TODO TD-013).

What each part replaces:

* :func:`stream` -> ``Transaction.stream(statement, params, batch_size=...)``. PostgreSQL: a psycopg server-side
  cursor (``DECLARE ... CURSOR``, ``FETCH``) for a query, so the row cap stops the transfer, not only the delivery
  (03 section 5.3); a plain cursor for a statement a cursor cannot hold (``EXPLAIN``, ``SHOW``). SQLite: sqlite3's
  cursor, which steps the statement as rows are fetched. The statement goes to the driver's cursor as it is, never
  through SQLAlchemy ``text()`` (:mod:`tabrion_studio.services.sqltext`).
* :class:`Cancel` -> ``Transaction.cancel()``: psycopg's ``cancel_safe()`` and ``sqlite3.Connection.interrupt()``.
  A cancel is sent only while the statement's connection is attached, never after: the lock makes sure that a late
  cancel cannot reach the next user of the pooled connection. A connection that got a cancel is invalidated (the
  pool opens a new one), since PostgreSQL may act on a cancel packet after the statement ended (H-1 of
  ``tab-033-review.md``); a SQLite connection too, for the same guarantee.
* :func:`reset_session`: ``RESET ALL`` when a PostgreSQL connection goes back to the pool (M-2 of
  ``stu-011-review.md``).
* :func:`pin_setup_path` and :func:`restore_setup_path`: the queries SQLAlchemy's dialect sends when a PostgreSQL
  connection is opened run under :data:`CATALOG_SEARCH_PATH`, and the session's own path is restored before the
  connection is used (TD-039).
* :class:`Column` -> ``tabrion.Column`` (name and type name).
* :func:`check` -> ``tabrion.check(db)`` (STU-014): the 0.1 collectors on the driver connection.
* :func:`extension_status` (STU-055): tabrion's public ``tabrion.postgres.extension_status`` on the driver connection
  of a ``Database.read()``, like :func:`check`.
* :func:`inspector` -> ``Database.inspector()`` (STU-010): a SQLAlchemy ``Inspector`` on the connection of a
  ``Database.read()`` transaction, so reflection runs read-only like every other Studio statement.
* :data:`CATALOG_SEARCH_PATH` (STU-022 review H-1): Studio's own catalog reads on PostgreSQL (``stream(...,
  catalog=True)`` and :func:`inspector`) first set a fixed ``search_path`` for their transaction only
  (``set_config(..., true)``), as pg_dump does, so no object in a user's schema takes part in resolving their
  functions and operators. This is Studio's fixed text, never a user statement (the statement check still refuses
  ``set_config`` in user SQL), and it is not a write. User SQL and the viewer's and exports' statements keep the
  session's ``search_path``.

Every statement still runs inside ``Database.read()``: PostgreSQL ``BEGIN READ ONLY`` on a read-only session,
SQLite ``query_only`` and Studio's authorizer, and every PostgreSQL statement is a prepared statement
(``prepare_threshold=0``), the server cursor's ``DECLARE`` too. The one exception is :func:`check`: on PostgreSQL,
tabrion's collectors run on the read-only session in their own read-only transactions, before SQLAlchemy has sent
``BEGIN`` (I-1 of ``stu-014-review.md``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from dataclasses import dataclass
import secrets
import sqlite3
import threading
from typing import Any

import sqlalchemy
import tabrion
from tabrion import check as tabrion_check
from tabrion import postgres as tabrion_postgres

__all__ = [
    "CATALOG_SEARCH_PATH",
    "Cancel",
    "Cancelled",
    "Column",
    "ResultCursor",
    "check",
    "extension_status",
    "inspector",
    "pin_setup_path",
    "reset_session",
    "restore_setup_path",
    "stream",
]

#: The ``search_path`` of Studio's own catalog reads and of a new connection's setup (PostgreSQL): the system catalog,
#: temporary relations last (never searched for functions and operators). A catalog read sets it transaction-locally,
#: so it ends with the read; the setup sets it for the session and :func:`restore_setup_path` takes it back (TD-039).
CATALOG_SEARCH_PATH = "pg_catalog, pg_temp"
_PIN_SQL = "SELECT pg_catalog.set_config('search_path', %s, true)"


@dataclass(frozen=True)
class Column:
    """A result column (tabrion 0.2: ``tabrion.Column``). ``type_name`` is ``None`` where the driver gives none."""

    name: str
    type_name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type_name}


class Cancelled(Exception):
    """The statement was canceled (also when it finished before the cancel reached it)."""


class Cancel:
    """The cancel hook of one statement; :meth:`cancel` may come from any thread (0.2: ``Transaction.cancel``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dbapi: Any = None
        self._requested = False
        self._sent = False

    @property
    def requested(self) -> bool:
        return self._requested

    def attach(self, dbapi_connection: Any) -> None:
        """The running statement's driver connection (called in the statement's thread, before it starts)."""
        with self._lock:
            if self._requested:
                raise Cancelled
            self._dbapi = dbapi_connection

    def detach(self) -> bool:
        """No more cancels for this statement; whether one was sent."""
        with self._lock:
            self._dbapi = None
            return self._sent

    def cancel(self) -> None:
        """Ask the database to stop the statement; repeat it while the statement runs (a cancel before it starts
        does nothing in either database)."""
        with self._lock:
            self._requested = True
            dbapi = self._dbapi
            if dbapi is None:
                return
            self._sent = True
            if isinstance(dbapi, sqlite3.Connection):
                dbapi.interrupt()
            else:
                # psycopg >= 3.2: does not block the event loop or leave the connection in a bad state.
                with contextlib.suppress(Exception):
                    dbapi.cancel_safe(timeout=2.0)


class ResultCursor:
    """The rows of one running statement, fetched in batches (tabrion 0.2: the iterator of ``Transaction.stream``)."""

    def __init__(self, cursor: Any, columns: tuple[Column, ...]) -> None:
        self._cursor = cursor
        self.columns = columns

    def fetch(self, size: int) -> list[tuple[Any, ...]]:
        if not self.columns or size <= 0:
            return []
        return [tuple(row) for row in self._cursor.fetchmany(size)]


def _postgres_type(dbapi: Any, oid: object) -> str | None:
    if not isinstance(oid, int):
        return None
    info = dbapi.adapters.types.get(oid)
    if info is None:
        return None
    return f"{info.name}[]" if oid == info.array_oid else str(info.name)


def _columns(cursor: Any, dbapi: Any, postgres: bool) -> tuple[Column, ...]:
    description = cursor.description
    if not description:
        return ()
    if postgres:
        return tuple(Column(str(item.name), _postgres_type(dbapi, item.type_code)) for item in description)
    return tuple(Column(str(item[0]), None) for item in description)


def _discard(connection: sqlalchemy.Connection, dbapi: Any) -> None:
    """A connection that got a cancel never goes back to the pool (H-1 of ``tab-033-review.md``, L-2 of
    ``stu-011-review.md``): PostgreSQL may act on a cancel packet after the statement ended and abort the next user's
    statement; SQLite's interrupt flag is cleared only when no statement runs. The driver connection is closed in
    any case (after ``invalidate()`` too), so a traceback that keeps it alive cannot keep it open until garbage
    collection (the ResourceWarning lead of ``stu-013-review.md``)."""
    with contextlib.suppress(Exception):
        connection.invalidate()
    with contextlib.suppress(Exception):
        dbapi.close()


#: ``RESET ALL`` leaves ``role`` and ``session_authorization`` alone (both are ``GUC_NO_RESET_ALL``), so a ``SET ROLE``
#: that a database function made is taken back first (R2-I1 of delta re-review 2 of ``stu-011-review.md``).
#: ``DISCARD TEMP`` drops the session's temporary tables (F-1 of ``stu-016-security-review.md``), so none outlives
#: the statement that made it.
_RESET_STATEMENTS = (
    "CLOSE ALL",
    "SELECT pg_catalog.pg_advisory_unlock_all()",
    "SET SESSION AUTHORIZATION DEFAULT",
    "RESET ROLE",
    "DISCARD TEMP",
    "RESET ALL",
)
#: The schema of the dblink extension, quoted, if the database has it (B-1 of the security assessment).
_DBLINK_SCHEMA = (
    "SELECT pg_catalog.quote_ident(n.nspname) FROM pg_catalog.pg_extension e"
    " JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'dblink'"
)
#: dblink's error for "no unnamed connection" (``connection_does_not_exist``).
_NO_CONNECTION = "08003"


def _close_dblink_connections(cursor: Any) -> None:
    """Closes every dblink connection of the session, named and unnamed (B-1 of the security assessment).

    dblink keeps its connections in the server process, outside any transaction, so one would outlive the statement
    that opened it and serve the next user of the pooled connection; the statement check refuses every ``dblink*``
    call, so this is defense in depth. Nothing happens on a database without dblink. dblink's functions are called
    qualified with the extension's schema. A failure other than "no unnamed connection" (for example a role that may
    not run them) raises, so :func:`reset_session` drops the connection, which closes its dblink connections too.
    """
    cursor.execute(_DBLINK_SCHEMA)
    row = cursor.fetchone()
    if row is None:
        return
    schema = row[0]
    cursor.execute(
        f"SELECT {schema}.dblink_disconnect(name) FROM pg_catalog.unnest({schema}.dblink_get_connections()) AS name"  # noqa: S608 - quote_ident
    )
    cursor.execute("SAVEPOINT studio_dblink_reset")
    try:
        cursor.execute(f"SELECT {schema}.dblink_disconnect()")
    except Exception as exc:
        if getattr(exc, "sqlstate", None) != _NO_CONNECTION:
            raise
        cursor.execute("ROLLBACK TO SAVEPOINT studio_dblink_reset")
    cursor.execute("RELEASE SAVEPOINT studio_dblink_reset")


def reset_session(dbapi_connection: Any, connection_record: Any) -> None:
    """Pool ``checkin`` listener (PostgreSQL): ``RESET ALL``, back to the startup values (M-2 of ``stu-011-review.md``).

    Studio gives its session settings as startup options too (``connections.startup_options``), so this restores
    the profile's timeouts and ``default_transaction_read_only`` and drops any other setting a statement changed.
    ``CLOSE ALL`` and ``pg_advisory_unlock_all()`` drop held cursors and session-level advisory locks too (defense in
    depth for R-H1 of the delta re-review: the statement check refuses both), ``SET SESSION AUTHORIZATION
    DEFAULT`` and ``RESET ROLE`` take back a role change, which ``RESET ALL`` keeps (R2-I1), and ``DISCARD TEMP``
    drops temporary tables (F-1 of ``stu-016-security-review.md``; the check refuses the statements that make them).
    ``DISCARD ALL`` would cover them, but it cannot run in a transaction block; ``DISCARD TEMP`` can, also in a
    read-only one. The engine is Studio's own (tabrion's rule of no ``RESET ALL``/``DISCARD`` is
    for a host's shared pool).
    Last, the session's dblink connections are closed (:func:`_close_dblink_connections`).
    A connection that cannot be reset leaves the pool. tabrion 0.2: a session reset of tabrion's own (TD-013).
    """
    if dbapi_connection is None or isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        dbapi_connection.rollback()  # whatever a statement left open
        with dbapi_connection.cursor() as cursor:
            for statement in _RESET_STATEMENTS:
                cursor.execute(statement)
            _close_dblink_connections(cursor)
        dbapi_connection.commit()
    except Exception as exc:
        connection_record.invalidate(exc)


def pin_setup_path(dbapi_connection: Any, _connection_record: Any) -> None:
    """Pool ``connect`` listener, the first one (PostgreSQL): a fixed ``search_path`` for the connection's setup.

    When a connection is opened, SQLAlchemy's dialect sends its own setup queries (``current_schema()`` among them,
    unqualified) before Studio or tabrion send anything. A database or role setting can put a schema that another
    role may write ahead of ``pg_catalog``; under the fixed path the setup resolves every name in ``pg_catalog``
    (hardening against object resolution through user schemas). :func:`restore_setup_path` gives the session its own
    path back. ``set_config`` is committed, so the dialect's rollback after its setup keeps it; it is not a write.
    """
    if dbapi_connection is None or isinstance(dbapi_connection, sqlite3.Connection):
        return
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SELECT pg_catalog.set_config('search_path', %s, false)", (CATALOG_SEARCH_PATH,))
    dbapi_connection.commit()


def restore_setup_path(dialect: sqlalchemy.Dialect) -> Callable[[Any, Any], None]:
    """Pool ``connect`` listener, after the dialect's setup and tabrion's (PostgreSQL): the session's own path.

    ``RESET search_path`` gives back the value of the database, role or startup settings (Studio sets no
    ``search_path`` of its own, :func:`tabrion_studio.services.connections.startup_options`), so user SQL, the viewer
    and the exports see the path the user configured. The dialect learned its default schema under the fixed path
    (``pg_catalog``); it gets the session's, read with a qualified ``pg_catalog.current_schema()``.
    """

    def restore(dbapi_connection: Any, _connection_record: Any) -> None:
        if dbapi_connection is None or isinstance(dbapi_connection, sqlite3.Connection):
            return
        with dbapi_connection.cursor() as cursor:
            cursor.execute("RESET search_path")
            cursor.execute("SELECT pg_catalog.current_schema()")
            row = cursor.fetchone()
        dbapi_connection.commit()
        dialect.default_schema_name = None if row is None else row[0]

    return restore


@contextlib.contextmanager
def stream(
    transaction: tabrion.Transaction,
    sql: str,
    values: Mapping[str, Any],
    *,
    cancel: Cancel,
    server_side: bool,
    catalog: bool = False,
) -> Iterator[ResultCursor]:
    """Run ``sql`` (the driver's text, one statement) in ``transaction`` and yield its cursor.

    ``catalog``: one of Studio's own catalog reads; on PostgreSQL it runs under :data:`CATALOG_SEARCH_PATH`.

    Raises :class:`Cancelled` after a canceled statement that ended without a database error. A database error
    propagates and ``Database.read()`` turns it into a ``TabrionError``.
    """
    connection = transaction.connection
    dbapi: Any = connection.connection.dbapi_connection
    postgres = not isinstance(dbapi, sqlite3.Connection)
    cancel.attach(dbapi)
    sent = False
    cursor: Any = None
    try:
        if postgres and catalog:
            _pin_catalog_path(dbapi)
        # A named psycopg cursor is a server-side cursor (DECLARE ... CURSOR, FETCH).
        cursor = dbapi.cursor(name=f"studio_{secrets.token_hex(8)}") if postgres and server_side else dbapi.cursor()
        cursor.execute(sql, dict(values))
        yield ResultCursor(cursor, _columns(cursor, dbapi, postgres))
    finally:
        # Detached before the cursor closes (F-4 of stu-011-leak-delta-review.md): a cancel that came while the
        # cursor's CLOSE ran made psycopg's ServerCursor.close() raise before it marked the cursor closed, and the
        # cursor warned "deleted while still open" at garbage collection.
        sent = cancel.detach()
        if sent:
            _discard(connection, dbapi)  # first: a cancel packet still on its way cannot reach the CLOSE either
        if cursor is not None:
            _close_cursor(cursor, connection, dbapi, discarded=sent)
    if sent:
        raise Cancelled


def _close_cursor(cursor: Any, connection: sqlalchemy.Connection, dbapi: Any, *, discarded: bool = False) -> None:
    """Close ``cursor``; one whose close fails (a broken connection) is closed without the database: the
    connection leaves the pool (:func:`_discard`), and on a closed driver connection psycopg marks a server-side
    cursor closed without sending ``CLOSE``. ``discarded``: the connection already left the pool after a cancel
    (:func:`_discard` also closes the driver connection, so SQLite's ``cursor.close()`` raises); it is not
    discarded a second time."""
    try:
        cursor.close()
    except Exception:
        if not discarded:
            _discard(connection, dbapi)
        with contextlib.suppress(Exception):
            cursor.close()


def check(transaction: tabrion.Transaction, *, cancel: Cancel) -> list[tabrion_check.Finding]:
    """``tabrion.check`` on the driver connection of ``transaction`` (STU-014).

    tabrion 0.1's ``check(Database)`` is not there yet (layer B); its SQLite and psycopg collectors are, and they
    read only (pragmas and catalog queries). They run on the driver connection of Studio's ``Database.read()``,
    which is read-only (SQLite ``mode=ro``, ``query_only`` and the authorizer; PostgreSQL a read-only session), but
    on PostgreSQL not inside its transaction: SQLAlchemy has sent no ``BEGIN`` yet, so tabrion opens and rolls back
    its own read-only transactions (I-1 of ``stu-014-review.md``). The session's startup ``statement_timeout``
    bounds each of them. tabrion 0.2: ``tabrion.check(db)`` (TD-013).

    ``cancel`` stops the checks from another thread (M-1 of ``stu-014-review.md``):

    * **SQLite:** only the integrity check (``PRAGMA quick_check``) can take long; tabrion's other checks are a few
      pragmas and file modes. So the cancel stops that statement alone (N-1 of the delta review): a progress handler
      ends it when a cancel comes while it runs, and a cancel requested before it started interrupts it as it starts
      (from the trace callback, while it is the only running statement, so SQLite clears the flag when it ends).
      Every other statement runs to its end. tabrion maps the stopped ``quick_check`` to "not checked"
      (``TAB-SQ-006``, info) and keeps every other finding (``TAB-SQ-003``, say). No interrupt can reach a later
      statement, so the connection may go back to the pool.
    * **PostgreSQL:** the psycopg cancel is attached for the whole check, as for :func:`stream`; a connection that
      got a cancel leaves the pool.

    The caller asks ``cancel.requested``.
    """
    connection = transaction.connection
    dbapi: Any = connection.connection.dbapi_connection
    if isinstance(dbapi, sqlite3.Connection):
        return _check_sqlite(dbapi, cancel)
    cancel.attach(dbapi)
    try:
        return tabrion_check.check(dbapi)
    finally:
        if cancel.detach():
            _discard(connection, dbapi)


def extension_status(
    transaction: tabrion.Transaction, name: str, *, cancel: Cancel
) -> tabrion_postgres.ExtensionStatus:
    """``tabrion.postgres.extension_status(connection, name)`` on the driver connection of ``transaction`` (STU-055).

    It reads ``pg_extension``, ``pg_available_extensions`` and ``current_schemas`` in one statement. As for
    :func:`check`, SQLAlchemy has sent no ``BEGIN`` yet, so tabrion opens and rolls back its own implicit
    transaction on the read-only session; the session's ``statement_timeout`` bounds it and ``cancel`` stops it.
    """
    connection = transaction.connection
    dbapi: Any = connection.connection.dbapi_connection
    cancel.attach(dbapi)
    try:
        return tabrion_postgres.extension_status(dbapi, name)
    finally:
        if cancel.detach():
            _discard(connection, dbapi)


#: The statements the cancel stops in tabrion's SQLite checks (upper case, without blanks).
_LONG_SQLITE_CHECKS = ("PRAGMAQUICK_CHECK", "PRAGMAINTEGRITY_CHECK")
#: How many SQLite VM steps between two looks at the cancel while the integrity check runs (as tabrion's deadline).
_CHECK_PROGRESS_STEPS = 1_000


def _check_sqlite(dbapi: sqlite3.Connection, cancel: Cancel) -> list[tabrion_check.Finding]:
    long = [False]  # whether the running statement is the integrity check

    def trace(statement: str) -> None:
        # Called as a statement starts running (inside its first step). A cancel requested before the integrity
        # check started stops it here: the interrupt reaches only the statements running now, this one, and SQLite
        # clears it when no statement runs any more.
        long[0] = "".join(statement.split()).upper().startswith(_LONG_SQLITE_CHECKS)
        if long[0] and cancel.requested:
            dbapi.interrupt()

    def progress() -> int:
        return int(long[0] and cancel.requested)  # a cancel requested while it runs

    dbapi.set_trace_callback(trace)
    dbapi.set_progress_handler(progress, _CHECK_PROGRESS_STEPS)
    try:
        return tabrion_check.check(dbapi)
    finally:
        dbapi.set_progress_handler(None, _CHECK_PROGRESS_STEPS)
        dbapi.set_trace_callback(None)


def _pin_catalog_path(dbapi: Any) -> None:
    """:data:`CATALOG_SEARCH_PATH` for the rest of the running transaction (PostgreSQL)."""
    with dbapi.cursor() as cursor:
        cursor.execute(_PIN_SQL, (CATALOG_SEARCH_PATH,))


@contextlib.contextmanager
def inspector(transaction: tabrion.Transaction) -> Iterator[sqlalchemy.Inspector]:
    """A SQLAlchemy inspector on ``transaction`` (one ``Database.read()``; tabrion 0.2: ``Database.inspector``).

    On PostgreSQL the reflection's catalog queries run under :data:`CATALOG_SEARCH_PATH` (STU-022 review H-1).
    """
    connection = transaction.connection
    dbapi: Any = connection.connection.dbapi_connection
    if not isinstance(dbapi, sqlite3.Connection):
        _pin_catalog_path(dbapi)
    yield sqlalchemy.inspect(connection)
