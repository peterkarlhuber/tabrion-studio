"""The query engine: user SQL with caps, timeouts and cancel (03 sections 5.3 and 6, STU-011, threat T-09).

A query is one statement on one open, read-only connection. :meth:`QueryEngine.start` checks it, binds its
parameters and runs it in a worker thread; the page follows its state over SSE and reads its rows in pages.

**Binding rules (TD-012, ``docs/architecture/connections.md``):** the connection comes from
``ConnectionRegistry.get`` (which checks the handle against its profile again), the statement from
``OpenConnection.prepare`` (``check_statement`` and ``sqltext.bind``: never SQLAlchemy ``text()``), and it runs
through ``OpenConnection.stream``: one ``Database.read()`` per statement.

**Limits (03 section 5.3):**

* rows: ``max_rows`` of the request, 1 000 by default, at most the ``max_rows`` setting (10 000 by default, at most
  1 000 000); the engine fetches one row more to tell whether the result was cut;
* bytes: the ``max_result_mb`` setting, 20 MB per result by default, at most 200 MB (the size of the JSON values,
  measured as rows arrive); the rest is cut. A fetch loads at most about 2 MB (one row first, then as many rows as
  fit at the size so far), so memory stays near the caps; one value larger than that still loads in full (M-3 of
  ``stu-011-review.md``);
* cells: 64 KB of text (or 32 KB of bytes) per cell to the page; a longer value is cut and listed in
  ``truncated_cells`` ("load in full" comes with STU-012/013);
* time: the profile's statement timeout (30 s, 15 s for ``prod``, or the profile's own) for the whole query, fetching
  included; PostgreSQL also has it as ``statement_timeout`` per statement, and ``lock_timeout`` 5 s;
* 4 queries at a time in Studio, 2 per connection (its pool); more are refused (``studio.query_limit``);
* memory: the last 16 queries are kept, and finished ones are dropped, oldest first, while the kept rows exceed
  200 MB.

**Cancel:** :meth:`QueryEngine.cancel` returns at once; a worker sends the database's own cancel
(:class:`.tabrion_compat.Cancel`) and repeats it until the statement stops (target < 1 s): every 100 ms for 3 s, then
every second. A query has one cancel worker at most; more cancel requests change nothing (L-3). The timeout uses the
same cancel.

**What is logged:** one ``studio.query_finished`` record per query with its state, row count, duration, engine and
problem code. Never the SQL text, a parameter value or a row (03 section 4.5, T-05).

**History (STU-020):** a finished query is handed to :class:`.history.History` with its text and the *names* of its
placeholders; the values stay in the bound statement and are never passed on.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import datetime
import decimal
import enum
import json
import logging
import math
import secrets
import threading
import time
from typing import Any
import uuid

from tabrion.errors import Kind, TabrionError

from tabrion_studio.config.schema import Settings
from tabrion_studio.problems import Problem, db_kind_problem, db_problem, new_log_id, problem
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection, pool_busy
from tabrion_studio.services.history import History
from tabrion_studio.services.sqltext import BoundStatement, placeholders
from tabrion_studio.services.tabrion_compat import Cancel, Cancelled, Column, ResultCursor

__all__ = [
    "CELL_CHARS",
    "MAX_BYTES",
    "MAX_PARALLEL",
    "PER_CONNECTION",
    "PREVIEW_ROWS",
    "Query",
    "QueryEngine",
    "QueryError",
    "json_value",
    "next_batch_rows",
    "raw_size",
]

logger = logging.getLogger("tabrion_studio.queries")

PREVIEW_ROWS = 1_000
MAX_BYTES = 20 * 1024 * 1024
CELL_CHARS = 64 * 1024
CELL_BYTES = 32 * 1024  # a bytes value is sent as base64, which grows it by a third
MAX_PARALLEL = 4
PER_CONNECTION = 2
KEPT_QUERIES = 16
KEPT_BYTES = 200 * 1024 * 1024
BATCH_ROWS = 500
#: M-3 of stu-011-review.md and R-M1 of its delta re-review: what one fetch may load before the caps apply. The first
#: fetch is one row; each next one at most twice the one before and as many rows as fit this budget at the size of the
#: largest row so far (at most BATCH_ROWS). Text, binary and nested values (arrays, json) count in full. A run of small
#: rows followed by large ones can still load one batch of that many large rows; one value larger than the budget
#: loads in full: the database's own limit (1 GB in PostgreSQL) is the only bound for a single value.
FETCH_BUDGET_BYTES = 2 * 1024 * 1024
PAGE_ROWS = 1_000
MAX_PAGE_ROWS = 5_000
#: How often and how long a cancel is repeated until the statement stops.
CANCEL_INTERVAL_S = 0.1
CANCEL_ATTEMPTS = 30
#: After the fast attempts, the one cancel worker of a query keeps asking at this pace until the statement stops
#: (L-3 of stu-011-review.md): neither driver can close a connection safely from another thread while it runs.
CANCEL_SLOW_INTERVAL_S = 1.0
_JS_SAFE_INTEGER = 2**53 - 1


def _raw_size(row: tuple[Any, ...]) -> int:
    """About what the driver loaded for ``row``: the length of text and binary values, nested values (psycopg's lists
    for arrays, dicts and lists for json) element by element, 16 bytes for the others. Iterative, so a deep json
    document cannot reach the recursion limit."""
    size = 0
    pending: list[Any] = list(row)
    while pending:
        value = pending.pop()
        if isinstance(value, str | bytes | bytearray | memoryview):
            size += len(value) + 16
        elif isinstance(value, list | tuple):
            size += 16
            pending.extend(value)
        elif isinstance(value, dict):
            size += 16
            pending.extend(value.keys())
            pending.extend(value.values())
        else:
            size += 16
    return size


#: The public name of the row size the fetch budget uses (STU-021's export sizes its fetches the same way).
raw_size = _raw_size


def next_batch_rows(fetched: int, largest: int) -> int:
    """The size of the next fetch after one of ``fetched`` rows, with ``largest`` the largest row so far (R-M1)."""
    return max(1, min(BATCH_ROWS, 2 * fetched, FETCH_BUDGET_BYTES // max(1, largest)))


class QueryError(Exception):
    """A query that cannot be started or found; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class State(enum.StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


def json_value(value: object, cell_chars: int = CELL_CHARS) -> tuple[Any, int, bool]:
    """A database value as a JSON value for the page, its approximate size in bytes and whether it was cut.

    Numbers beyond JavaScript's safe integers, decimals, dates and times, UUIDs and anything else without a JSON
    form become text; bytes become ``{"base64": ...}``; JSON (PostgreSQL ``json``/``jsonb``) stays a JSON value,
    measured and cut as text when it is long.
    """
    if value is None:
        return None, 4, False
    if isinstance(value, bool):
        return value, 5, False
    if isinstance(value, int):
        if -_JS_SAFE_INTEGER <= value <= _JS_SAFE_INTEGER:
            return value, 20, False
        return _text(str(value), cell_chars)
    if isinstance(value, float):
        return (value, 24, False) if math.isfinite(value) else (str(value), 10, False)
    if isinstance(value, str):
        return _text(value, cell_chars)
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        limit = cell_chars // 2
        encoded = base64.b64encode(raw[:limit]).decode("ascii")
        return {"base64": encoded}, len(encoded) + 16, len(raw) > limit
    if isinstance(value, decimal.Decimal | uuid.UUID):
        return _text(str(value), cell_chars)
    if isinstance(value, datetime.date | datetime.time):
        return _text(value.isoformat(), cell_chars)
    if isinstance(value, datetime.timedelta):
        return _text(str(value), cell_chars)
    if isinstance(value, dict | list | tuple):
        text = json.dumps(value, default=str, ensure_ascii=False)
        if len(text) > cell_chars:
            return _text(text, cell_chars)
        return value, len(text), False
    return _text(str(value), cell_chars)


def _text(text: str, cell_chars: int) -> tuple[str, int, bool]:
    if len(text) > cell_chars:
        return text[:cell_chars], cell_chars + 2, True
    return text, len(text) + 2, False


@dataclass
class Query:
    """One query's state and rows. Changed only by its worker (and :meth:`QueryEngine.cancel`), under ``_lock``."""

    id: str
    connection_id: str
    engine: str
    max_rows: int
    max_bytes: int
    started: float
    state: State = State.RUNNING
    columns: tuple[Column, ...] = ()
    rows: list[list[Any]] = field(default_factory=list)
    truncated_cells: list[tuple[int, int]] = field(default_factory=list)
    truncated: str | None = None  # "rows" or "bytes"
    size: int = 0
    elapsed_ms: float | None = None
    problem: Problem | None = None
    version: int = 0
    timed_out: bool = False
    hook: Cancel = field(default_factory=Cancel)
    done: threading.Event = field(default_factory=threading.Event)
    cancelling: bool = False  # a cancel worker runs (one per query, L-3)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def finished(self) -> bool:
        return self.state is not State.RUNNING

    def status(self) -> dict[str, Any]:
        """The state the page sees (the ``data`` of an SSE event); never SQL, parameters or rows."""
        with self._lock:
            doc: dict[str, Any] = {
                "query_id": self.id,
                "connection_id": self.connection_id,
                "state": self.state.value,
                "row_count": len(self.rows),
                "max_rows": self.max_rows,
                "truncated": self.truncated is not None,
                "truncated_by": self.truncated,
                "elapsed_ms": self.elapsed_ms,
                "columns": [column.to_dict() for column in self.columns],
            }
            if self.problem is not None:
                doc["problem"] = self.problem.to_dict()
            return doc

    def page(self, offset: int, limit: int) -> dict[str, Any]:
        with self._lock:
            rows = self.rows[offset : offset + limit]
            cells = [[row - offset, column] for row, column in self.truncated_cells if offset <= row < offset + limit]
            return {
                "query_id": self.id,
                "state": self.state.value,
                "columns": [column.to_dict() for column in self.columns],
                "offset": offset,
                "rows": rows,
                "row_count": len(self.rows),
                "truncated": self.truncated is not None,
                "truncated_by": self.truncated,
                "truncated_cells": cells,
            }

    def _changed(self) -> None:
        self.version += 1


@dataclass
class QueryEngine:
    """Running and kept queries. Thread-safe; :meth:`start` and :meth:`cancel` do not wait for the database."""

    registry: ConnectionRegistry
    limits: Callable[[], Settings] = Settings
    clock: Callable[[], float] = time.monotonic
    max_parallel: int = MAX_PARALLEL
    per_connection: int = PER_CONNECTION
    #: The byte cap of a result; ``None``: the ``max_result_mb`` setting (20 MB by default, at most 200 MB).
    max_bytes: int | None = None
    #: The query history (STU-020): each finished query's text and placeholder *names*, never its values.
    history: History | None = None
    _queries: dict[str, Query] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False

    # Starting

    def start(self, connection_id: object, sql: object, params: object = None, max_rows: object = None) -> Query:
        """Check and start one query (blocking for the registry's checks; call it off the event loop).

        Raises ``RegistryError`` (connection), ``StatementRefused``, ``ParameterError`` or :class:`QueryError`.
        """
        if not isinstance(sql, str) or (params is not None and not isinstance(params, Mapping)):
            raise QueryError("studio.invalid_request")
        if self._closed:
            raise QueryError("studio.query_limit")
        rows = self._max_rows(max_rows)
        connection = self.registry.get(connection_id)
        statement = connection.prepare(sql, params)
        query = Query(
            id=f"q_{secrets.token_urlsafe(12)}",
            connection_id=connection.id,
            engine=connection.engine,
            max_rows=rows,
            max_bytes=self.limits().max_result_mb * 1024 * 1024 if self.max_bytes is None else self.max_bytes,
            started=self.clock(),
        )
        with self._lock:
            if self._closed:
                raise QueryError("studio.query_limit")
            running = [q for q in self._queries.values() if not q.finished]
            if len(running) >= self.max_parallel or (
                sum(q.connection_id == connection.id for q in running) >= self.per_connection
            ):
                raise QueryError("studio.query_limit")
            self._queries[query.id] = query
            self._evict()
        worker = threading.Thread(
            target=self._run, args=(query, connection, statement), name=f"studio-query-{query.id}", daemon=True
        )
        try:
            worker.start()
        except BaseException:
            # TD-057 review F-1: a query whose worker never ran must not keep its slot (it counts while RUNNING).
            with self._lock:
                self._queries.pop(query.id, None)
            raise
        return query

    def _max_rows(self, value: object) -> int:
        ceiling = self.limits().max_rows
        if value is None:
            return min(PREVIEW_ROWS, ceiling)
        if type(value) is not int or value < 1:
            raise QueryError("studio.invalid_request")
        return min(value, ceiling)

    def _evict(self) -> None:
        """Drop finished queries, oldest first, beyond :data:`KEPT_QUERIES` or :data:`KEPT_BYTES` (under the lock)."""
        finished = [q for q in self._queries.values() if q.finished]
        total = sum(q.size for q in self._queries.values())
        while finished and (len(self._queries) > KEPT_QUERIES or total > KEPT_BYTES):
            oldest = finished.pop(0)
            total -= oldest.size
            del self._queries[oldest.id]

    # Running

    def _run(self, query: Query, connection: OpenConnection, statement: BoundStatement) -> None:
        timer = threading.Timer(connection.timeout_s, self._expire, args=(query,))
        timer.daemon = True
        state, found = State.DONE, None
        try:
            timer.start()  # inside the try (TD-057 review F-1): if it fails, the query still ends as failed
            connection.stream(statement, lambda cursor: self._collect(query, cursor), cancel=query.hook)
        except Cancelled:
            state, found = self._stopped(query)
        except TabrionError as exc:
            if query.hook.requested:
                state, found = self._stopped(query)
            elif pool_busy(exc):  # I-9 of the stu-010 delta re-review: no free session, the database is fine
                state, found = State.FAILED, problem("studio.connection_busy")
            else:
                # DbErrorRedactionFilter (logs.py) rewrites the exception to tabrion's safe message in the record.
                logger.info("query failed", exc_info=exc, extra={"event": "studio.db_error", "code": exc.code})
                state, found = State.FAILED, db_problem(exc)
        except Exception as exc:
            if query.hook.requested:
                state, found = self._stopped(query)
            else:
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code.startswith("studio."):
                    state, found = State.FAILED, problem(code)
                else:
                    log_id = new_log_id()
                    logger.exception("query failed", extra={"event": "studio.query_error", "log_id": log_id})
                    state, found = State.FAILED, problem("studio.internal_error", log_id=log_id)
        finally:
            timer.cancel()
        with query._lock:
            query.state = state
            query.problem = found
            query.elapsed_ms = round((self.clock() - query.started) * 1000, 1)
            query._changed()
        logger.info(
            "query finished",
            extra={
                "event": "studio.query_finished",
                "state": state.value,
                "row_count": len(query.rows),
                "elapsed_ms": query.elapsed_ms,
                "engine": query.engine,
                "code": None if found is None else found.code,
            },
        )
        self._remember(query, connection, statement)
        query.done.set()  # last: whoever waits for it may close the log

    def _remember(self, query: Query, connection: OpenConnection, statement: BoundStatement) -> None:
        """One history entry (STU-020): the statement as written and its placeholder names. ``statement.values``
        is never read here: the history has no place for a parameter value (08 section 5)."""
        if self.history is None:
            return
        try:
            self.history.record(
                profile_id=connection.profile_id,
                environment=connection.environment,
                engine=connection.engine,
                sql=statement.sql,
                param_names=placeholders(statement.sql, "sqlite" if connection.engine == "sqlite" else "postgresql"),
                state=query.state.value,
                row_count=len(query.rows),
                elapsed_ms=query.elapsed_ms,
                code=None if query.problem is None else query.problem.code,
            )
        except Exception:
            logger.warning("history entry not kept", exc_info=True, extra={"event": "studio.history_error"})

    @staticmethod
    def _stopped(query: Query) -> tuple[State, Problem]:
        if query.timed_out:
            return State.FAILED, db_kind_problem(Kind.TIMEOUT)
        return State.CANCELED, problem("studio.query_canceled")

    def _collect(self, query: Query, cursor: ResultCursor) -> None:
        with query._lock:
            query.columns = cursor.columns
            query._changed()
        batch_rows, largest = 1, 1
        while not query.hook.requested:
            wanted = min(batch_rows, query.max_rows + 1 - len(query.rows))
            batch = cursor.fetch(wanted)
            if not batch:
                return
            largest = max(largest, *(_raw_size(row) for row in batch))
            batch_rows = next_batch_rows(len(batch), largest)
            with query._lock:
                for row in batch:
                    if len(query.rows) >= query.max_rows:
                        query.truncated = "rows"
                        break
                    values, size, cut = self._row(row)
                    if query.size + size > query.max_bytes:
                        query.truncated = "bytes"
                        break
                    index = len(query.rows)
                    query.rows.append(values)
                    query.size += size
                    query.truncated_cells += [(index, column) for column in cut]
                query._changed()
                if query.truncated is not None:
                    return

    @staticmethod
    def _row(row: tuple[Any, ...]) -> tuple[list[Any], int, list[int]]:
        values: list[Any] = []
        size = 2
        cut: list[int] = []
        for column, value in enumerate(row):
            encoded, length, truncated = json_value(value)
            values.append(encoded)
            size += length + 1
            if truncated:
                cut.append(column)
        return values, size, cut

    def _expire(self, query: Query) -> None:
        query.timed_out = True
        self._start_cancel(query)

    # Reading, canceling, closing

    def get(self, query_id: object) -> Query:
        with self._lock:
            query = self._queries.get(query_id) if isinstance(query_id, str) else None
        if query is None:
            raise QueryError("studio.query_not_found")
        return query

    def page(self, query_id: object, offset: object = 0, limit: object = PAGE_ROWS) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE_ROWS:
            raise QueryError("studio.invalid_request")
        return self.get(query_id).page(offset, limit)

    def cancel(self, query_id: object) -> Query:
        """Ask the database to stop the query; returns at once (a worker repeats the cancel until it stops)."""
        query = self.get(query_id)
        self._start_cancel(query)
        return query

    @classmethod
    def _start_cancel(cls, query: Query) -> None:
        """Start the query's cancel worker, unless it is finished or one runs already (L-3 of the review)."""
        with query._lock:
            if query.finished or query.cancelling:
                return
            query.cancelling = True
        threading.Thread(target=cls._send_cancel, args=(query,), name="studio-cancel", daemon=True).start()

    @staticmethod
    def _send_cancel(query: Query) -> None:
        attempt = 0
        while not query.done.is_set():
            query.hook.cancel()
            attempt += 1
            if query.done.wait(CANCEL_INTERVAL_S if attempt < CANCEL_ATTEMPTS else CANCEL_SLOW_INTERVAL_S):
                return

    def close_all(self, wait_s: float = 2.0) -> None:
        """Cancel every running query (on shutdown) and wait a little for them to stop."""
        with self._lock:
            self._closed = True
            running = [q for q in self._queries.values() if not q.finished]
        for query in running:
            self._start_cancel(query)
        deadline = time.monotonic() + wait_s
        for query in running:
            query.done.wait(max(0.0, deadline - time.monotonic()))
