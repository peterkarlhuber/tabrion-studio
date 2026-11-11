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
  disjunction so that ascending, descending and ``NULLS LAST`` work on both engines. On SQLite, a primary key that
  may hold ``NULL`` (a rowid table) is ordered by the row id last (M-2 of ``stu-013-review.md``);
* ``rowid`` (SQLite table without a primary key) and ``ctid`` (PostgreSQL table or materialized view without one):
  the same keyset on the row's physical id, with ``hint: "no_primary_key"`` (03 section 5.3). On PostgreSQL the
  physical id is ``(tableoid, ctid)``: a ``ctid`` is unique only within one relation, and a partitioned table or an
  inheritance parent returns the rows of several (H-1);
* ``offset`` (a view or foreign table without a primary key): ``LIMIT`` and ``OFFSET`` (by the sort column, if
  any), with ``hint: "unstable_order"``. It is not ordered by every column (L-2 of ``stu-013-review.md``): that would
  make every page evaluate and sort the whole view, and a large view would only reach the timeout.

**Key values** stay in the backend: ``next`` is an opaque token, so a client never sends a key value that becomes
part of a statement. On PostgreSQL a key is read as the type's own text (``CAST(key AS text)``) and bound back as an
untyped parameter, which the server reads with the column's own input function: the comparison happens in the
column's type for every type (``real``, ``jsonb``, arrays, enums, domains; H-2 and M-1). A sort column whose type has
no ordering (``json``, ``xml``, geometric types) is ``studio.viewer_sort_unsupported``. Only the last row's key is
kept, and the identity of every row for the cell detail (L-1); a key longer than :data:`KEY_BYTES` is not kept, and
the next page then continues from the last kept key with an ``OFFSET``.

Tokens are bound to the session that made them (a digest of its CSRF value) and kept for the last 256 pages, at most
64 per session and connection; the pages of a closed connection are dropped (L-4).

**Bytes (M-4):** the length of every value that can be long is measured on the server (``octet_length``,
``length``) before it is fetched: a page cell loads at most 64 KB (longer ones are cut and listed in
``truncated_cells``), a page at most the ``max_result_mb`` setting (20 MB by default; the rest waits for the next
page, ``truncated: "bytes"``), and rows are fetched in batches of about 2 MB, as in the query engine. The cell detail
loads one value up to 10 MB of its bytes (text as UTF-8; 03 section 5.3: "Vollständig laden") and finds the row
again by its full primary key; by a physical id only if the row still shows the same values (M-3).

Each statement runs in its own ``Database.read()`` through ``OpenConnection.stream``, under the profile's timeout
(the same cancel as the query engine; past it, the answer is ``db.timeout``), and at most 4 at a time in Studio, 2
per connection (``studio.query_limit``, L-5). Nothing is logged but the event and the problem code; never a value.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import secrets
import threading
from typing import Any, TypeVar

import sqlalchemy
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import ColumnElement

from tabrion_studio.config.schema import Settings
from tabrion_studio.services.connections import OpenConnection
from tabrion_studio.services.explorer import Explorer, Reflected
from tabrion_studio.services.queries import (
    CANCEL_ATTEMPTS,
    CANCEL_INTERVAL_S,
    CELL_CHARS,
    FETCH_BUDGET_BYTES,
    MAX_PARALLEL,
    PER_CONNECTION,
    json_value,
)
from tabrion_studio.services.sqltext import BoundStatement, Dialect, driver_text
from tabrion_studio.services.tabrion_compat import Cancel, ResultCursor

__all__ = ["FULL_CELL_BYTES", "MAX_PAGE", "PAGE", "Viewer", "ViewerError", "ViewerTimeout"]

PAGE = 100
MAX_PAGE = 1_000
#: The cell detail loads at most this many bytes of one value (text as UTF-8, bytes as they are; M-4).
FULL_CELL_BYTES = 10 * 1024 * 1024
#: A key value (sort column or primary key) longer than this is not kept (L-1); see the module text.
KEY_BYTES = 64 * 1024
KEPT_PAGES = 256
KEPT_PER_SESSION = 64
MAX_FILTERS = 20
BATCH_ROWS = 500
OPERATORS = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "contains", "starts_with", "is_null", "not_null"})
_T = TypeVar("_T")
#: Both compile to ``:name`` placeholders, the form of a checked statement; ``sqltext.driver_text`` gives the driver's
#: text from it (psycopg ``%(name)s``), exactly as for user SQL, so ``OpenConnection.stream`` accepts it (L-1).
_DIALECTS: dict[str, sqlalchemy.Dialect] = {
    "postgresql": postgresql.psycopg.dialect(paramstyle="named"),  # type: ignore[no-untyped-call]
    "sqlite": sqlite.pysqlite.dialect(paramstyle="named"),
}
#: PostgreSQL types of a fixed, small size: selected as they are, without a length check.
_PG_SMALL = frozenset(
    {
        "int2", "int4", "int8", "float4", "float8", "bool", "date", "time", "timetz", "timestamp", "timestamptz",
        "interval", "uuid", "oid", "xid", "xid8", "cid", "tid", "money", "inet", "cidr", "macaddr", "macaddr8",
        "char", "pg_lsn", "regclass", "regtype", "regproc",
    }
)  # fmt: skip
#: PostgreSQL types without a default ordering (``could not identify an ordering operator``); geometric types (the
#: category ``G``) neither.
_PG_UNORDERED = frozenset({"json", "xml"})

_Found = tuple[list[Any], list[int], list[int | None], list[Any], list[bool]]


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


@dataclass(frozen=True)
class _Column:
    name: str
    type: str | None
    nullable: bool
    base: str | None
    element: str | None
    category: str | None

    def orderable(self, engine: str) -> bool:
        if engine != "postgresql":
            return True
        return self.category != "G" and (self.element or self.base) not in _PG_UNORDERED

    def small(self, engine: str) -> bool:
        return engine == "postgresql" and self.element is None and self.base in _PG_SMALL


@dataclass(frozen=True)
class _Part:
    """One selected part of a row: a column's value under a byte limit, or a key value."""

    engine: str
    width: int  # how many select expressions it takes
    limit: int
    binary: bool | None = None  # PostgreSQL: bytea; SQLite: decided per value


@dataclass
class _Row:
    ident: tuple[Any, ...] | None  # the identity parts' values (None: one is too long to keep)
    digest: bytes | None  # what the page showed of the row, when its identity is physical (M-3)


@dataclass
class _Page:
    connection_id: str
    owner: str | None
    schema: str
    name: str
    sort: str | None
    desc: bool
    filters: tuple[Filter, ...]
    limit: int
    paging: str
    identity: list[str]  # primary key columns, then "@tableoid", "@ctid", "@xmin" or "@rowid"
    rows: list[_Row]
    anchor: tuple[Any, ...] | None  # the keyset the next page starts after (None: from the start)
    skip: int  # the rows the next page skips after the anchor
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
    # NullType: no cast in the text. psycopg sends text as "unknown", so PostgreSQL reads it with the type of the
    # column it is compared with; that is how keys (kept as their text) come back typed. The value is always a
    # bound parameter.
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


# Byte-limited projections (M-4)


def _project(
    engine: str, expression: ColumnElement[Any], column: _Column | None, limit: int
) -> tuple[list[ColumnElement[Any]], _Part]:
    """The select expressions of one value under ``limit`` bytes: its size, the value itself if it fits, else its
    first ``limit`` bytes (and on SQLite its storage class); a small fixed-size PostgreSQL value as it is."""
    if engine == "postgresql":
        if column is not None and column.small(engine):
            return [expression], _Part(engine, 1, limit)
        binary = column is not None and column.base == "bytea" and column.element is None
        if binary:
            size = sqlalchemy.func.octet_length(expression)
            head = sqlalchemy.func.substr(expression, 1, limit)
        else:
            textual = column is not None and column.category == "S" and column.element is None
            text = expression if textual else sqlalchemy.cast(expression, sqlalchemy.Text)
            size = sqlalchemy.func.octet_length(text)
            head = sqlalchemy.func.substr(sqlalchemy.func.convert_to(text, "UTF8"), 1, limit)
        selected = [size, sqlalchemy.case((size <= limit, expression)), sqlalchemy.case((size > limit, head))]
        return selected, _Part(engine, 3, limit, binary)
    raw = sqlalchemy.cast(expression, sqlalchemy.LargeBinary)
    size = sqlalchemy.func.length(raw)
    selected = [
        size,
        sqlalchemy.case((size <= limit, expression)),
        sqlalchemy.case((size > limit, sqlalchemy.func.substr(raw, 1, limit))),
        sqlalchemy.case((size > limit, sqlalchemy.func.typeof(expression))),
    ]
    return selected, _Part(engine, 4, limit)


def _utf8(raw: bytes) -> str:
    """The text of ``raw``, the first bytes of a UTF-8 text: a character cut at the end is left out."""
    end = len(raw)
    for back in range(1, min(4, end) + 1):
        byte = raw[end - back]
        if byte & 0xC0 != 0x80:  # the first byte of the last character
            need = 1 if byte < 0x80 else 2 if byte >> 5 == 0b110 else 3 if byte >> 4 == 0b1110 else 4
            if need > back:
                end -= back
            break
    return str(memoryview(raw)[:end], "utf-8", "replace")  # no copy of the bytes


def _read(part: _Part, values: Sequence[Any]) -> tuple[Any, int | None, bool]:
    """A projected value: the value (or its first bytes), its size in bytes on the server, and whether it was cut."""
    if part.width == 1:
        return values[0], None, False
    size, whole, head = values[0], values[1], values[2]
    if size is None:
        return None, None, False
    if size <= part.limit:
        return whole, int(size), False
    binary = part.binary if part.engine == "postgresql" else values[3] == "blob"
    raw = bytes(head)
    return (raw if binary else _utf8(raw)), int(size), True


def _key(engine: str, expression: ColumnElement[Any], small: bool) -> tuple[list[ColumnElement[Any]], _Part]:
    """The select expressions of a key value: on PostgreSQL its text (bound back untyped, read in the column's
    type), on SQLite the value; a long one only as its size."""
    value = sqlalchemy.cast(expression, sqlalchemy.Text) if engine == "postgresql" else expression
    if small:
        return [value], _Part(engine, 1, KEY_BYTES)
    if engine == "postgresql":
        size = sqlalchemy.func.octet_length(value)
    else:
        size = sqlalchemy.func.length(sqlalchemy.cast(expression, sqlalchemy.LargeBinary))
    return [size, sqlalchemy.case((size <= KEY_BYTES, value))], _Part(engine, 2, KEY_BYTES)


def _read_key(part: _Part, values: Sequence[Any]) -> tuple[Any, bool]:
    """A key value and whether it was kept (False: longer than :data:`KEY_BYTES`)."""
    if part.width == 1:
        return values[0], True
    if values[0] is None:
        return None, True
    return (values[1], True) if values[0] <= KEY_BYTES else (None, False)


def _raw_size(row: Sequence[Any]) -> int:
    return sum(len(value) if isinstance(value, str | bytes | bytearray | memoryview) else 16 for value in row)


def _digest(values: list[Any], sizes: list[int | None]) -> bytes:
    text = json.dumps([values, sizes], default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


@dataclass
class _Shape:
    """One object's columns as the viewer selects them."""

    engine: str
    table: Any  # sqlalchemy.TableClause
    names: list[str]
    columns: dict[str, _Column]
    parts: list[_Part]
    selected: list[ColumnElement[Any]]

    def encode(self, row: Sequence[Any]) -> tuple[list[Any], list[int | None], int, list[int]]:
        """The page values of one row, their sizes on the server, the row's JSON size and its cut columns."""
        values: list[Any] = []
        sizes: list[int | None] = []
        total, at = 2, 0
        cut: list[int] = []
        for index, part in enumerate(self.parts):
            value, size, truncated = _read(part, row[at : at + part.width])
            at += part.width
            encoded, length, shortened = json_value(value, CELL_CHARS)
            values.append(encoded)
            sizes.append(size)
            total += length + 1
            if truncated or shortened:
                cut.append(index)
        return values, sizes, total, cut


def _encode_cell(value: Any, size: int | None, cut: bool) -> dict[str, Any]:
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        encoded: Any = {"base64": base64.b64encode(raw).decode("ascii")}
        return {"value": encoded, "size": len(raw) if size is None else size, "truncated": cut}
    if isinstance(value, str):
        return {"value": value, "size": len(value.encode("utf-8")) if size is None else size, "truncated": cut}
    encoded, length, _ = json_value(value, 4 * FULL_CELL_BYTES)
    return {"value": encoded, "size": length if size is None else size, "truncated": cut}


@dataclass
class Viewer:
    """Rows of one table at a time. Blocking (call it off the event loop); thread-safe."""

    explorer: Explorer
    #: The ``max_result_mb`` setting bounds a page (M-4), as it bounds a query result.
    limits: Callable[[], Settings] = Settings
    max_parallel: int = MAX_PARALLEL
    per_connection: int = PER_CONNECTION
    _pages: OrderedDict[str, _Page] = field(default_factory=OrderedDict)
    _running: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.explorer.registry.on_close(self.forget)

    def forget(self, connection_id: str) -> None:
        """Drop the pages of ``connection_id`` (its connection closed; L-4)."""
        with self._lock:
            for token in [t for t, page in self._pages.items() if page.connection_id == connection_id]:
                del self._pages[token]

    def _reflected(self, connection_id: object, schema: str, name: str) -> Reflected:
        reflected = self.explorer.columns(connection_id, schema, name)
        if reflected.kind == "sequence":
            raise ViewerError("studio.object_not_found")
        return reflected

    def _shape(self, reflected: Reflected, schema: str, name: str) -> _Shape:
        connection = reflected.connection
        columns = {
            c["name"]: _Column(
                c["name"], c["type"], bool(c["nullable"]), c.get("base"), c.get("element"), c.get("category")
            )
            for c in reflected.columns
        }
        names = list(columns)
        table = sqlalchemy.table(name, *(sqlalchemy.column(n) for n in names), schema=self._schema(connection, schema))
        parts: list[_Part] = []
        selected: list[ColumnElement[Any]] = []
        for n in names:
            expressions, part = _project(connection.engine, table.c[n], columns[n], CELL_CHARS)
            selected += expressions
            parts.append(part)
        return _Shape(connection.engine, table, names, columns, parts, selected)

    @staticmethod
    def _physical(names: Sequence[str], part: str) -> ColumnElement[Any]:
        if part == "@rowid":
            alias = _rowid_name(names)
            if alias is None:  # pragma: no cover - the table changed its columns since the page
                raise ViewerError("studio.viewer_row_gone")
            return sqlalchemy.literal_column(alias)
        return sqlalchemy.literal_column(part[1:])

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
        owner: str | None = None,
    ) -> dict[str, Any]:
        """One page; ``after`` is the ``next`` token of the page before (its sort and filters are kept).

        ``owner`` names the session (a digest of its CSRF value): a token is accepted only from the session that got
        it (L-4)."""
        if after is not None:
            previous = self._page(after, connection_id, schema, name, owner)
            if not previous.more:
                raise ViewerError("studio.viewer_page_unknown")
            sort, desc, parsed, limit = previous.sort, previous.desc, previous.filters, previous.limit
        else:
            previous = None
            parsed = parse_filters(filters)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ViewerError("studio.invalid_request")
        reflected = self._reflected(connection_id, schema, name)
        connection, engine = reflected.connection, reflected.connection.engine
        shape = self._shape(reflected, schema, name)
        names, columns, table = shape.names, shape.columns, shape.table
        if (sort is not None and sort not in names) or any(item.column not in names for item in parsed):
            raise ViewerError("studio.viewer_column_unknown")
        if sort is not None and not columns[sort].orderable(engine):
            raise ViewerError("studio.viewer_sort_unsupported")
        primary = [n for n in reflected.primary if n in names]
        rowid_table = engine == "sqlite" and reflected.kind == "table" and bool(reflected.rowid)
        rowid_free = _rowid_name(names) is not None
        physical: list[str] = []
        if primary:
            paging = "primary_key"
            if rowid_table and rowid_free and any(columns[n].nullable for n in primary):
                physical = ["@rowid"]  # M-2: NULL keys are told apart by the row id
        elif rowid_table and rowid_free:
            paging, physical = "rowid", ["@rowid"]
        elif engine == "postgresql" and reflected.kind in ("table", "materialized_view"):
            paging, physical = "ctid", ["@tableoid", "@ctid"]  # H-1: a ctid is unique per relation only
        else:
            paging = "offset"
        # The order: (expression, descending, nullable, small).
        keys: list[tuple[ColumnElement[Any], bool, bool, bool]] = []
        sort_key = sort is not None and (paging == "offset" or sort not in primary[:1])
        if sort is not None and sort_key:
            keys.append((table.c[sort], desc, columns[sort].nullable, columns[sort].small(engine)))
        pk_nullable = engine == "sqlite"  # PostgreSQL enforces NOT NULL on a primary key; SQLite does not (M-2)
        keys += [
            (table.c[n], desc if n == sort else False, pk_nullable and columns[n].nullable, columns[n].small(engine))
            for n in primary
        ]
        keys += [(self._physical(names, p), False, False, True) for p in physical]
        # The identity of a row for the cell detail: the primary key, the physical id and, on PostgreSQL, xmin (M-3).
        identity = primary + physical + (["@xmin"] if paging == "ctid" else [])
        key_parts: list[_Part] = []
        key_selected: list[ColumnElement[Any]] = []
        if paging != "offset":
            extra = [(self._physical(names, "@xmin"), False, False, True)] if paging == "ctid" else []
            for expression, _, _, small in keys + extra:
                expressions, part = _key(engine, expression, small)
                # Labeled apart: "CAST(ctid AS TEXT) AS ctid" would make ORDER BY ctid order the text.
                key_selected += [e.label(f"__studio_key_{len(key_selected) + i}") for i, e in enumerate(expressions)]
                key_parts.append(part)
        order_keys = [(e, d, n) for e, d, n, _ in keys]
        statement = sqlalchemy.select(*shape.selected, *key_selected)
        for item in parsed:
            statement = statement.where(_condition(table.c[item.column], item))
        anchor = previous.anchor if previous is not None else None
        skip = previous.skip if previous is not None else 0
        if anchor is not None:
            if len(anchor) != len(order_keys):  # the table changed its key since the page before
                raise ViewerError("studio.viewer_page_unknown")
            statement = statement.where(_after(order_keys, anchor))
        order = [(e.desc() if d else e.asc()).nulls_last() for e, d, _ in order_keys]
        statement = statement.order_by(*order).limit(limit + 1)
        if skip:
            statement = statement.offset(skip)
        width = len(shape.selected)
        page_bytes = self.limits().max_result_mb * 1024 * 1024

        def consume(cursor: ResultCursor) -> tuple[list[_Found], bool]:
            # M-4: the first fetch is one row, the next ones as many rows as fit about 2 MB at the size so far; the
            # page stops at the byte cap (one row at least).
            found: list[_Found] = []
            total, batch = 0, 1
            while len(found) <= limit:
                fetched = cursor.fetch(min(batch, limit + 1 - len(found)))
                if not fetched:
                    return found, False
                for row in fetched:
                    values, sizes, size, cut = shape.encode(row)
                    kept: list[Any] = []
                    whole: list[bool] = []
                    at = width
                    for part in key_parts:
                        value, complete = _read_key(part, row[at : at + part.width])
                        at += part.width
                        kept.append(value)
                        whole.append(complete)
                    if found and total + size > page_bytes:
                        return found, True
                    total += size
                    found.append((values, cut, sizes, kept, whole))
                loaded = sum(_raw_size(row) for row in fetched)
                batch = max(1, min(BATCH_ROWS, FETCH_BUDGET_BYTES * len(fetched) // max(1, loaded)))
            return found, False

        fetched, cut_bytes = self._run(connection, statement, consume)
        more = cut_bytes or len(fetched) > limit
        fetched = fetched[:limit]
        rows: list[list[Any]] = []
        cells: list[list[int]] = []
        kept_rows: list[_Row] = []
        sort_offset = 1 if sort_key else 0
        last_key: tuple[Any, ...] | None = None
        for index, (values, cut, sizes, kept, whole) in enumerate(fetched):
            rows.append(values)
            cells += [[index, column] for column in cut]
            if paging == "offset":
                kept_rows.append(_Row(None, None))
                continue
            last_key = tuple(kept[: len(keys)]) if all(whole[: len(keys)]) else None
            ident = tuple(kept[sort_offset:]) if all(whole[sort_offset:]) else None
            kept_rows.append(_Row(ident, _digest(values, sizes) if physical else None))
        next_anchor: tuple[Any, ...] | None
        next_skip: int
        if paging != "offset" and fetched and last_key is not None:
            next_anchor, next_skip = last_key, 0
        else:
            next_anchor, next_skip = anchor, skip + len(fetched)
        page = _Page(
            connection.id,
            owner,
            schema,
            name,
            sort,
            desc,
            parsed,
            limit,
            paging,
            identity,
            kept_rows,
            next_anchor,
            next_skip,
            more,
        )
        token = self._keep(page)
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "columns": [{"name": n, "type": columns[n].type} for n in names],
            "rows": rows,
            "truncated_cells": cells,
            "truncated": "bytes" if cut_bytes else None,
            "page": token,
            "next": token if more else None,
            "paging": paging,
            "hint": {"primary_key": None, "offset": "unstable_order"}.get(paging, "no_primary_key"),
            "sort": sort,
            "desc": desc,
            "limit": limit,
        }

    def cell(
        self,
        connection_id: object,
        schema: str,
        name: str,
        page: str,
        row: int,
        column: str,
        *,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """One value of a page in full (up to 10 MB of its bytes), found again by the row's identity."""
        state = self._page(page, connection_id, schema, name, owner)
        if state.paging == "offset":
            raise ViewerError("studio.viewer_cell_unavailable")
        if type(row) is not int or not 0 <= row < len(state.rows):
            raise ViewerError("studio.invalid_request")
        reflected = self._reflected(connection_id, schema, name)
        connection, engine = reflected.connection, reflected.connection.engine
        shape = self._shape(reflected, schema, name)
        if column not in shape.names:
            raise ViewerError("studio.viewer_column_unknown")
        kept = state.rows[row]
        if kept.ident is None:
            raise ViewerError("studio.viewer_cell_unavailable")  # a key too long to keep
        if any(p not in shape.names for p in state.identity if not p.startswith("@")):
            raise ViewerError("studio.viewer_row_gone")  # the table changed its key since the page
        parts = list(zip(state.identity, kept.ident, strict=True))
        primary = [(p, v) for p, v in parts if not p.startswith("@")]
        # M-3: the full primary key identifies a row. A physical id (ctid, rowid) can be reused by another row, so it
        # identifies one only together with what the page showed of the row.
        by_key = state.paging == "primary_key" and all(v is not None for _, v in primary)
        used = primary if by_key else parts
        target, target_part = _project(engine, shape.table.c[column], shape.columns[column], FULL_CELL_BYTES)
        statement = sqlalchemy.select(*target, *([] if by_key else shape.selected))
        for part, value in used:
            expression = shape.table.c[part] if not part.startswith("@") else self._physical(shape.names, part)
            statement = statement.where(expression.is_(None) if value is None else expression == _bind(value))
        statement = statement.limit(2)
        found = self._run(connection, statement, lambda cursor: cursor.fetch(2))
        if len(found) != 1:
            raise ViewerError("studio.viewer_row_gone")
        if not by_key:
            values, sizes, _, _ = shape.encode(found[0][target_part.width :])
            if kept.digest is None or _digest(values, sizes) != kept.digest:
                raise ViewerError("studio.viewer_row_gone")
        value, size, cut = _read(target_part, found[0][: target_part.width])
        return _encode_cell(value, size, cut)

    # Helpers

    @staticmethod
    def _schema(connection: OpenConnection, schema: str) -> str | None:
        return None if connection.engine == "sqlite" else schema

    def _run(
        self, connection: OpenConnection, statement: sqlalchemy.Select[Any], consume: Callable[[ResultCursor], _T]
    ) -> _T:
        # I-2: the statement itself has a LIMIT; the text check below is a second look.
        if statement._limit_clause is None:  # pragma: no cover - every statement above limits
            raise ViewerError("studio.internal_error")
        compiled = statement.compile(dialect=_DIALECTS[connection.engine])
        text = str(compiled)
        if " LIMIT " not in f" {text.upper()} ".replace("\n", " "):  # pragma: no cover - as above
            raise ViewerError("studio.internal_error")
        dialect: Dialect = "sqlite" if connection.engine == "sqlite" else "postgresql"
        driver_sql, names = driver_text(text, dialect)
        params = dict(compiled.params)
        # Key values from a page are the driver's own values (SQLite) or their text (PostgreSQL): they go back to the
        # driver as they are, so they skip sqltext's checks for user values (text, numbers, booleans, null).
        bound = BoundStatement(text, driver_sql, {name: params[name] for name in names})
        with self._lock:
            # L-5: as many statements at a time as the query engine allows queries.
            if (
                sum(self._running.values()) >= self.max_parallel
                or self._running.get(connection.id, 0) >= self.per_connection
            ):
                raise ViewerError("studio.query_limit")
            self._running[connection.id] = self._running.get(connection.id, 0) + 1
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
            with self._lock:
                self._running[connection.id] -= 1
                if not self._running[connection.id]:
                    del self._running[connection.id]

    def _keep(self, page: _Page) -> str:
        token = f"p_{secrets.token_urlsafe(12)}"
        with self._lock:
            self._pages[token] = page
            mine = [t for t, p in self._pages.items() if (p.owner, p.connection_id) == (page.owner, page.connection_id)]
            for old in mine[: max(0, len(mine) - KEPT_PER_SESSION)]:
                del self._pages[old]
            while len(self._pages) > KEPT_PAGES:
                self._pages.popitem(last=False)
        return token

    def _page(self, token: object, connection_id: object, schema: str, name: str, owner: str | None) -> _Page:
        with self._lock:
            page = self._pages.get(token) if isinstance(token, str) else None
        if page is None or (page.connection_id, page.owner, page.schema, page.name) != (
            connection_id,
            owner,
            schema,
            name,
        ):
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
