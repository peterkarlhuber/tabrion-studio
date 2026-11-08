"""The data viewer: one table's rows in keyset pages, sorted and filtered (03 sections 2 D, 5.3 and 6, STU-013).

**Every statement has a ``LIMIT``** (03 section 5.3, "Volltabellen-Schutz"; the test spy in
``tests/security/test_viewer.py``). The viewer builds its statements with SQLAlchemy Core ``select()`` from the
reflected columns, never from text of the request:

* the table and every column named in a request (sort, filter, cell) must be in the reflected table, or the request
  is ``studio.viewer_column_unknown``; the object must be in the connection's catalog (``Explorer``);
* filter values are bound parameters (text, number, boolean or null), compared by the database's own rules;
* a page is ``LIMIT page + 1`` (to know whether there is a next page) after a keyset condition.

**Paging (``paging`` in the answer):**

* ``primary_key``: ``ORDER BY [sort column,] primary key`` and the keyset ``(sort, pk) > (last row)``, written as a
  disjunction so that ascending, descending and ``NULLS LAST`` work on both engines;
* ``rowid`` (SQLite table without a primary key) and ``ctid`` (PostgreSQL table or materialized view without one):
  the same keyset on the row's physical id, with ``hint: "no_primary_key"`` (03 section 5.3);
* ``offset`` (a view or foreign table without a primary key): ``LIMIT`` and ``OFFSET``, with
  ``hint: "unstable_order"``.

The key values of a page stay in the backend: ``next`` is an opaque token, so a client never sends a key value that
becomes part of a statement. Tokens are kept for the last 256 pages.

**Cells:** values as in the query engine (64 KB of text per cell, longer ones listed in ``truncated_cells``). The cell
detail loads one value in full, up to 10 MB (03 section 5.3: "Vollständig laden").

Each statement runs in its own ``Database.read()`` through ``OpenConnection.stream``, under the profile's timeout
(the same cancel as the query engine; past it, the answer is ``db.timeout``). Nothing is logged but the event and
the problem code; never a value.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import secrets
import threading
from typing import Any, TypeVar

import sqlalchemy
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import ColumnElement

from tabrion_studio.services.connections import OpenConnection
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.queries import CANCEL_ATTEMPTS, CANCEL_INTERVAL_S, CELL_CHARS, json_value
from tabrion_studio.services.sqltext import BoundStatement, Dialect, driver_text
from tabrion_studio.services.tabrion_compat import Cancel, ResultCursor

__all__ = ["FULL_CELL_CHARS", "MAX_PAGE", "PAGE", "Viewer", "ViewerError", "ViewerTimeout"]

PAGE = 100
MAX_PAGE = 1_000
FULL_CELL_CHARS = 10 * 1024 * 1024
KEPT_PAGES = 256
MAX_FILTERS = 20
OPERATORS = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "contains", "starts_with", "is_null", "not_null"})
_KEY = "__studio_key_"
_T = TypeVar("_T")
#: Both compile to ``:name`` placeholders, the form of a checked statement; ``sqltext.driver_text`` gives the driver's
#: text from it (psycopg ``%(name)s``), exactly as for user SQL, so ``OpenConnection.stream`` accepts it (L-1).
_DIALECTS: dict[str, sqlalchemy.Dialect] = {
    "postgresql": postgresql.psycopg.dialect(paramstyle="named"),  # type: ignore[no-untyped-call]
    "sqlite": sqlite.pysqlite.dialect(paramstyle="named"),
}


class ViewerError(Exception):
    """A request the viewer refuses; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ViewerTimeout(Exception):
    """The statement ran past the connection's timeout and was canceled (the answer is ``db.timeout``)."""


@dataclass(frozen=True)
class Filter:
    column: str
    op: str
    value: Any = None


@dataclass
class _Page:
    connection_id: str
    schema: str
    name: str
    sort: str | None
    desc: bool
    filters: tuple[Filter, ...]
    limit: int
    paging: str
    keys: list[tuple[Any, ...]]  # the key of every row of the page, in order
    offset: int
    more: bool


def _value(value: object) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return value
    raise ViewerError("studio.viewer_filter_invalid")


def parse_filters(raw: object) -> tuple[Filter, ...]:
    """``[{"column", "op", "value"?}]`` (a list, or its JSON text) as filters; anything else is refused."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            raise ViewerError("studio.viewer_filter_invalid") from None
    if not isinstance(raw, list) or len(raw) > MAX_FILTERS:
        raise ViewerError("studio.viewer_filter_invalid")
    found = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("column"), str) or item.get("op") not in OPERATORS:
            raise ViewerError("studio.viewer_filter_invalid")
        if set(item) - {"column", "op", "value"}:
            raise ViewerError("studio.viewer_filter_invalid")
        value = None if item["op"] in ("is_null", "not_null") else _value(item.get("value"))
        if item["op"] in ("contains", "starts_with") and not isinstance(value, str):
            raise ViewerError("studio.viewer_filter_invalid")
        found.append(Filter(item["column"], item["op"], value))
    return tuple(found)


def _bind(value: Any) -> ColumnElement[Any]:
    # NullType: no cast in the text (psycopg sends the value with its own type, or text as "unknown" for the server
    # to resolve against the column); the value is always a bound parameter.
    return sqlalchemy.bindparam(None, value, type_=sqlalchemy.types.NullType())


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _condition(column: ColumnElement[Any], item: Filter) -> ColumnElement[bool]:
    if item.op == "is_null":
        return column.is_(None)
    if item.op == "not_null":
        return column.is_not(None)
    if item.value is None:
        return column.is_(None) if item.op == "eq" else column.is_not(None)
    text = sqlalchemy.cast(column, sqlalchemy.Text)
    if item.op == "contains":
        return text.like(_bind(f"%{_like(item.value)}%"), escape="\\")
    if item.op == "starts_with":
        return text.like(_bind(f"{_like(item.value)}%"), escape="\\")
    value = _bind(item.value)
    return {
        "eq": column == value,
        "ne": column != value,
        "lt": column < value,
        "le": column <= value,
        "gt": column > value,
        "ge": column >= value,
    }[item.op]


def _after(keys: Sequence[tuple[ColumnElement[Any], bool, bool]], last: Sequence[Any]) -> ColumnElement[bool]:
    """Rows after ``last`` for ``ORDER BY key [DESC] NULLS LAST, ...`` (``keys``: expression, descending, nullable)."""
    branches: list[ColumnElement[bool]] = []
    equal: list[ColumnElement[bool]] = []
    for (expression, descending, nullable), value in zip(keys, last, strict=True):
        if value is None:
            # Nothing sorts after NULL (NULLS LAST) but more NULLs, which the next keys order.
            equal.append(expression.is_(None))
            continue
        later = expression < _bind(value) if descending else expression > _bind(value)
        if nullable:
            later = sqlalchemy.or_(later, expression.is_(None))
        branches.append(sqlalchemy.and_(*equal, later))
        equal.append(expression == _bind(value))
    return sqlalchemy.or_(*branches) if branches else sqlalchemy.false()


@dataclass
class Viewer:
    """Rows of one table at a time. Blocking (call it off the event loop); thread-safe."""

    explorer: Explorer
    _pages: OrderedDict[str, _Page] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _table(self, connection_id: object, schema: str, name: str) -> tuple[OpenConnection, str, dict[str, Any]]:
        connection, kind, columns, primary = self.explorer.columns(connection_id, schema, name)
        if kind == "sequence":
            raise ViewerError("studio.object_not_found")
        return connection, kind, {"columns": columns, "primary": primary}

    def rows(
        self,
        connection_id: object,
        schema: str,
        name: str,
        *,
        limit: int = PAGE,
        sort: str | None = None,
        desc: bool = False,
        filters: object = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        """One page; ``after`` is the ``next`` token of the page before (its sort and filters are kept)."""
        if after is not None:
            previous = self._page(after, connection_id, schema, name)
            if not previous.more:
                raise ViewerError("studio.viewer_page_unknown")
            sort, desc, parsed, limit = previous.sort, previous.desc, previous.filters, previous.limit
        else:
            previous = None
            parsed = parse_filters(filters)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ViewerError("studio.invalid_request")
        connection, kind, meta = self._table(connection_id, schema, name)
        names = [column["name"] for column in meta["columns"]]
        if (sort is not None and sort not in names) or any(item.column not in names for item in parsed):
            raise ViewerError("studio.viewer_column_unknown")
        table = sqlalchemy.table(name, *(sqlalchemy.column(n) for n in names), schema=self._schema(connection, schema))
        nullable = {column["name"]: column["nullable"] for column in meta["columns"]}
        primary = [n for n in meta["primary"] if n in names]
        physical: ColumnElement[Any] | None
        if primary:
            paging, physical = "primary_key", None
        elif connection.engine == "sqlite" and kind == "table" and _rowid_name(names) is not None:
            paging, physical = "rowid", sqlalchemy.literal_column(str(_rowid_name(names)))
        elif connection.engine == "postgresql" and kind in ("table", "materialized_view"):
            paging, physical = "ctid", sqlalchemy.literal_column("ctid")
        else:
            paging, physical = "offset", None
        keys: list[tuple[ColumnElement[Any], bool, bool]] = []
        if sort is not None and sort not in primary[:1]:
            keys.append((table.c[sort], desc, bool(nullable.get(sort, True))))
        keys += [(table.c[n], desc if n == sort else False, False) for n in primary]
        if physical is not None:
            keys.append((physical, False, False))
        key_labels = [expression.label(f"{_KEY}{index}") for index, (expression, _, _) in enumerate(keys)]
        statement = sqlalchemy.select(*(table.c[n] for n in names), *key_labels)
        for item in parsed:
            statement = statement.where(_condition(table.c[item.column], item))
        if previous is not None and paging != "offset":
            statement = statement.where(_after(keys, previous.keys[-1]))
        order: list[Any] = [(e.desc() if d else e.asc()).nulls_last() for e, d, _ in keys]
        if not order and sort is not None:
            order = [(table.c[sort].desc() if desc else table.c[sort].asc()).nulls_last()]
        statement = statement.order_by(*order).limit(limit + 1)
        offset = 0
        if paging == "offset":
            offset = 0 if previous is None else previous.offset + len(previous.keys)
            statement = statement.offset(offset)
        width = len(names)
        fetched = self._run(connection, statement, lambda cursor: cursor.fetch(limit + 1))
        more = len(fetched) > limit
        fetched = fetched[:limit]
        rows: list[list[Any]] = []
        cells: list[list[int]] = []
        for index, row in enumerate(fetched):
            values = []
            for column, value in enumerate(row[:width]):
                encoded, _, cut = json_value(value, CELL_CHARS)
                values.append(encoded)
                if cut:
                    cells.append([index, column])
            rows.append(values)
        page = _Page(
            connection.id,
            schema,
            name,
            sort,
            desc,
            parsed,
            limit,
            paging,
            [tuple(row[width:]) for row in fetched] if paging != "offset" else [() for _ in fetched],
            offset,
            more,
        )
        token = self._keep(page)
        types = {column["name"]: column["type"] for column in meta["columns"]}
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "columns": [{"name": n, "type": types.get(n)} for n in names],
            "rows": rows,
            "truncated_cells": cells,
            "page": token,
            "next": token if more else None,
            "paging": paging,
            "hint": {"primary_key": None, "offset": "unstable_order"}.get(paging, "no_primary_key"),
            "sort": sort,
            "desc": desc,
            "limit": limit,
        }

    def cell(self, connection_id: object, schema: str, name: str, page: str, row: int, column: str) -> dict[str, Any]:
        """One value of a page in full (up to 10 MB), found again by the row's key."""
        state = self._page(page, connection_id, schema, name)
        if state.paging == "offset":
            raise ViewerError("studio.viewer_cell_unavailable")
        if type(row) is not int or not 0 <= row < len(state.keys):
            raise ViewerError("studio.invalid_request")
        connection, _, meta = self._table(connection_id, schema, name)
        names = [c["name"] for c in meta["columns"]]
        if column not in names:
            raise ViewerError("studio.viewer_column_unknown")
        table = sqlalchemy.table(name, *(sqlalchemy.column(n) for n in names), schema=self._schema(connection, schema))
        primary = [n for n in meta["primary"] if n in names]
        if state.paging == "primary_key":
            expressions: list[ColumnElement[Any]] = [table.c[n] for n in primary]
            values = state.keys[row][-len(primary) :]
        else:
            physical = _rowid_name(names) if state.paging == "rowid" else "ctid"
            if physical is None:  # pragma: no cover - the table changed its columns since the page
                raise ViewerError("studio.viewer_row_gone")
            expressions = [sqlalchemy.literal_column(physical)]
            values = state.keys[row][-1:]
        statement = sqlalchemy.select(table.c[column])
        for expression, value in zip(expressions, values, strict=True):
            statement = statement.where(expression == _bind(value))
        statement = statement.limit(2)
        found = self._run(connection, statement, lambda cursor: cursor.fetch(2))
        if len(found) != 1:
            raise ViewerError("studio.viewer_row_gone")
        value, size, cut = json_value(found[0][0], FULL_CELL_CHARS)
        return {"value": value, "size": size, "truncated": cut}

    # Helpers

    @staticmethod
    def _schema(connection: OpenConnection, schema: str) -> str | None:
        return None if connection.engine == "sqlite" else schema

    @staticmethod
    def _run(
        connection: OpenConnection, statement: sqlalchemy.Select[Any], consume: Callable[[ResultCursor], _T]
    ) -> _T:
        compiled = statement.compile(dialect=_DIALECTS[connection.engine])
        text = str(compiled)
        if " LIMIT " not in f" {text.upper()} ".replace("\n", " "):  # pragma: no cover - every statement above limits
            raise ViewerError("studio.internal_error")
        dialect: Dialect = "sqlite" if connection.engine == "sqlite" else "postgresql"
        driver_sql, names = driver_text(text, dialect)
        params = dict(compiled.params)
        # Key values from a page may be any type the driver returned (dates, UUIDs, decimals): they go back to the
        # driver as they came, so they skip sqltext's checks for user values (text, numbers, booleans, null).
        bound = BoundStatement(text, driver_sql, {name: params[name] for name in names})
        hook, finished = Cancel(), threading.Event()
        timer = threading.Timer(connection.timeout_s, _expire, args=(hook, finished))
        timer.daemon = True
        timer.start()
        try:
            return connection.stream(bound, consume, cancel=hook)
        except Exception:
            if hook.requested:
                raise ViewerTimeout from None
            raise
        finally:
            finished.set()
            timer.cancel()

    def _keep(self, page: _Page) -> str:
        token = f"p_{secrets.token_urlsafe(12)}"
        with self._lock:
            self._pages[token] = page
            while len(self._pages) > KEPT_PAGES:
                self._pages.popitem(last=False)
        return token

    def _page(self, token: object, connection_id: object, schema: str, name: str) -> _Page:
        with self._lock:
            page = self._pages.get(token) if isinstance(token, str) else None
        if page is None or (page.connection_id, page.schema, page.name) != (connection_id, schema, name):
            raise ViewerError("studio.viewer_page_unknown")
        return page


def _rowid_name(names: Sequence[str]) -> str | None:
    """SQLite's name of the row id that no column of the table hides (a column may be called ``rowid``)."""
    taken = {n.lower() for n in names}
    return next((alias for alias in ("rowid", "_rowid_", "oid") if alias not in taken), None)


def _expire(hook: Cancel, finished: threading.Event) -> None:
    """The timeout: cancel, and repeat it every 0.1 s (for 3 s) until the statement stops."""
    for _ in range(CANCEL_ATTEMPTS):
        hook.cancel()
        if finished.wait(CANCEL_INTERVAL_S):
            return
