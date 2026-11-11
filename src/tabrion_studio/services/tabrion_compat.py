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
* :class:`Column` -> ``tabrion.Column`` (name and type name).
* :func:`check` -> ``tabrion.check(db)`` (STU-014): the 0.1 collectors on the driver connection.
* :func:`inspector` -> ``Database.inspector()`` (STU-010): a SQLAlchemy ``Inspector`` on the connection of a
  ``Database.read()`` transaction, so reflection runs read-only like every other Studio statement.

Every statement still runs inside ``Database.read()``: PostgreSQL ``BEGIN READ ONLY`` on a read-only session,
SQLite ``query_only`` and Studio's authorizer, and every PostgreSQL statement is a prepared statement
(``prepare_threshold=0``), the server cursor's ``DECLARE`` too.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import contextlib
from dataclasses import dataclass
import secrets
import sqlite3
import threading
from typing import Any

import sqlalchemy
import tabrion
from tabrion import check as tabrion_check

__all__ = ["Cancel", "Cancelled", "Column", "ResultCursor", "check", "inspector", "reset_session", "stream"]


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
    statement; SQLite's interrupt flag is cleared only when no statement runs. If SQLAlchemy cannot invalidate it,
    the driver connection is closed, so the pool can only throw it away."""
    try:
        connection.invalidate()
    except Exception:
        with contextlib.suppress(Exception):
            dbapi.close()


_RESET_STATEMENTS = ("CLOSE ALL", "SELECT pg_catalog.pg_advisory_unlock_all()", "RESET ALL")


def reset_session(dbapi_connection: Any, connection_record: Any) -> None:
    """Pool ``checkin`` listener (PostgreSQL): ``RESET ALL``, back to the startup values (M-2 of ``stu-011-review.md``).

    Studio gives its session settings as startup options too (``connections.startup_options``), so this restores
    the profile's timeouts and ``default_transaction_read_only`` and drops any other setting a statement changed.
    ``CLOSE ALL`` and ``pg_advisory_unlock_all()`` drop held cursors and session-level advisory locks too (defense in
    depth for R-H1 of the delta re-review: the statement check refuses both). ``DISCARD ALL`` would cover them, but it
    cannot run in a transaction block.
    A connection that cannot be reset leaves the pool. tabrion 0.2: a session reset of tabrion's own (TD-013).
    """
    if dbapi_connection is None or isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        dbapi_connection.rollback()  # whatever a statement left open
        with dbapi_connection.cursor() as cursor:
            for statement in _RESET_STATEMENTS:
                cursor.execute(statement)
        dbapi_connection.commit()
    except Exception as exc:
        connection_record.invalidate(exc)


@contextlib.contextmanager
def stream(
    transaction: tabrion.Transaction,
    sql: str,
    values: Mapping[str, Any],
    *,
    cancel: Cancel,
    server_side: bool,
) -> Iterator[ResultCursor]:
    """Run ``sql`` (the driver's text, one statement) in ``transaction`` and yield its cursor.

    Raises :class:`Cancelled` after a canceled statement that ended without a database error. A database error
    propagates and ``Database.read()`` turns it into a ``TabrionError``.
    """
    connection = transaction.connection
    dbapi: Any = connection.connection.dbapi_connection
    postgres = not isinstance(dbapi, sqlite3.Connection)
    cancel.attach(dbapi)
    sent = False
    try:
        # A named psycopg cursor is a server-side cursor (DECLARE ... CURSOR, FETCH).
        cursor = dbapi.cursor(name=f"studio_{secrets.token_hex(8)}") if postgres and server_side else dbapi.cursor()
        try:
            cursor.execute(sql, dict(values))
            yield ResultCursor(cursor, _columns(cursor, dbapi, postgres))
        finally:
            with contextlib.suppress(Exception):
                cursor.close()
    finally:
        sent = cancel.detach()
        if sent:
            _discard(connection, dbapi)
    if sent:
        raise Cancelled


def check(transaction: tabrion.Transaction) -> list[tabrion_check.Finding]:
    """``tabrion.check`` on the driver connection of ``transaction`` (STU-014).

    tabrion 0.1's ``check(Database)`` is not there yet (layer B); its SQLite and psycopg collectors are, and they
    read only (pragmas and catalog queries), so they run inside Studio's ``Database.read()``. tabrion 0.2:
    ``tabrion.check(db)`` (TD-013).
    """
    dbapi: Any = transaction.connection.connection.dbapi_connection
    return tabrion_check.check(dbapi)


@contextlib.contextmanager
def inspector(transaction: tabrion.Transaction) -> Iterator[sqlalchemy.Inspector]:
    """A SQLAlchemy inspector on ``transaction`` (one ``Database.read()``; tabrion 0.2: ``Database.inspector``)."""
    yield sqlalchemy.inspect(transaction.connection)
