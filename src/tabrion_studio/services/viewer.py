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
no ordering (``json``, ``xml``, geometric types) is ``studio.viewer_sort_unsupported``. Float text is exact because
every Studio session starts with ``extra_float_digits=3`` (``connections.startup_options``), whatever the role or
database sets; that pin is the fix. As a backstop only, a keyset page that ends where an earlier page of the walk did
(a key whose text does not read back as its value) is read again by ``OFFSET`` after the previous page's own anchor,
and the walk stays on ``OFFSET``: the walk always ends, but such a key can still repeat or skip rows before the guard
trips (H-2 residual of ``stu-013-review.md``, finding 3 of its re-review; ``docs/TODO.md``). Only the last row's key is
kept, and the identity of every row for the cell detail (L-1); a key longer than :data:`KEY_BYTES` is not kept, and
the next page then continues from the last kept key with an ``OFFSET``.

Tokens are bound to the session that made them (a digest of its CSRF value) and kept for the last 256 pages, at most
64 per session and connection; the pages of a closed connection are dropped (L-4).

**Bytes (M-4):** the length of every value that can be long is measured on the server (``octet_length``,
``length``) before it is fetched: a page cell loads at most 64 KB (longer ones are cut and listed in
``truncated_cells``), a page at most the ``max_result_mb`` setting (20 MB by default; the rest waits for the next
page, ``truncated: "bytes"``), and rows are fetched in batches of at most about 2 MB (as in the query engine): the
batch is sized by the widest row the caps allow (every capped value at its cap), never by the rows so far alone, so a
small first row cannot make the next batch large (M-4 residual); a row wider than 2 MB is fetched alone. A
PostgreSQL ``json``, ``jsonb`` or array value is decoded by the driver into Python objects that can take about 20
times the bytes of its text: the batch counts it at :data:`CONTAINER_FACTOR` times its cap, and the page cap counts
the memory of the decoded value, if that is more than its JSON size (R-1). One row is always shown, however large.
The cell detail loads one value up to 10 MB of its bytes (text as UTF-8; 03 section 5.3: "Vollständig laden") and
finds the row again by its full primary key; by a physical id only if the row still shows the same values (M-3). A
row's identity is a list of typed parts, ``("column", name)`` or a physical id (``rowid``, ``tableoid``, ``ctid``,
``xmin``): a key column is always a quoted identifier, whatever its name (``@a`` included; N-1).

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
import sys
import threading
from typing import Any, Literal, TypeVar

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
#: A page keeps the digests of this many anchors of the pages before it, to see a keyset that goes in a circle.
KEPT_ANCHORS = 32
MAX_FILTERS = 20
BATCH_ROWS = 500
#: How many times the bytes of its text a decoded json, jsonb or array value may take in Python, at most (about 21
#: for ``[[], [], ...]``: 3 bytes of text become a 56-byte list and its 8-byte pointer); sizes the fetch batch (R-1).
CONTAINER_FACTOR = 32
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

    def container(self, engine: str) -> bool:
        """psycopg decodes it into lists and dicts, which take many times the bytes of its text (R-1)."""
        return engine == "postgresql" and (self.element is not None or self.base in ("json", "jsonb"))


@dataclass(frozen=True)
class _Part:
    """One selected part of a row: a column's value under a byte limit, or a key value."""

    engine: str
    width: int  # how many select expressions it takes
    limit: int
    binary: bool | None = None  # PostgreSQL: bytea; SQLite: decided per value
    container: bool = False  # PostgreSQL json, jsonb or an array: the driver decodes it into Python objects (R-1)


#: The parts of a row's identity (N-1 of the delta re-review): a column of the primary key, or one of the row's
#: physical ids. A column part names its column, which is quoted like any other identifier; a physical part is one of
#: the fixed names below and never a column name, so no column name (one that starts with ``@``, say) can become one.
_IdentityKind = Literal["column", "rowid", "ctid", "tableoid", "xmin"]
_IdentityPart = tuple[_IdentityKind, str]  # (kind, column name); the name is "" for a physical id
#: The SQL of the physical ids (PostgreSQL system columns; SQLite's row id is named by :func:`_rowid_name`).
_SYSTEM_COLUMNS: dict[str, str] = {"ctid": "ctid", "tableoid": "tableoid", "xmin": "xmin"}


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
    identity: list[_IdentityPart]  # ("column", name) of the primary key, then the physical ids
    rows: list[_Row]
    anchor: tuple[Any, ...] | None  # the keyset the next page starts after (None: from the start)
    skip: int  # the rows the next page skips after the anchor
    more: bool
    base_anchor: tuple[Any, ...] | None  # the anchor and skip this page's own statement used
    base_skip: int
    stuck: bool  # the keyset made no progress: the walk goes on by OFFSET (H-2 residual)
    seen: tuple[bytes, ...]  # digests of the anchors of the pages before (the last KEPT_ANCHORS)


@dataclass
class _Loaded:
    """One page's statement, read."""

    more: bool
    cut_bytes: bool
    rows: list[list[Any]] = field(default_factory=list)
    cells: list[list[int]] = field(default_factory=list)
    kept: list[_Row] = field(default_factory=list)
    last_key: tuple[Any, ...] | None = None


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
        return selected, _Part(engine, 3, limit, binary, column is not None and column.container(engine))
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


def _footprint(value: Any) -> int:
    """About how many bytes of memory a decoded JSON or array value takes (every object counted, shared ones too)."""
    total, stack = 0, [value]
    while stack:
        item = stack.pop()
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            stack += item.keys()
            stack += item.values()
        elif isinstance(item, list | tuple):
            stack += item
    return total


def _raw_size(row: Sequence[Any]) -> int:
    return sum(len(value) if isinstance(value, str | bytes | bytearray | memoryview) else 16 for value in row)


def _anchor_digest(anchor: tuple[Any, ...] | None) -> bytes:
    text = json.dumps(anchor, default=repr, ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


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
        """The page values of one row, their sizes on the server, the row's size and its cut columns. The size is the
        JSON size, or for a decoded json, jsonb or array value the memory its Python objects take, if that is more
        (R-1)."""
        values: list[Any] = []
        sizes: list[int | None] = []
        total, at = 2, 0
        cut: list[int] = []
        for index, part in enumerate(self.parts):
            value, size, truncated = _read(part, row[at : at + part.width])
            at += part.width
            encoded, length, shortened = json_value(value, CELL_CHARS)
            if part.container and isinstance(encoded, list | dict):
                length = max(length, _footprint(encoded))
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
    def _physical(names: Sequence[str], kind: _IdentityKind) -> ColumnElement[Any]:
        """A physical id's expression: a fixed name, never a column name (N-1)."""
        if kind == "rowid":
            alias = _rowid_name(names)
            if alias is None:  # pragma: no cover - the table changed its columns since the page
                raise ViewerError("studio.viewer_row_gone")
            return sqlalchemy.literal_column(alias)
        return sqlalchemy.literal_column(_SYSTEM_COLUMNS[kind])

    @classmethod
    def _identity_expression(cls, shape: _Shape, part: _IdentityPart) -> ColumnElement[Any]:
        kind, name = part
        return shape.table.c[name] if kind == "column" else cls._physical(shape.names, kind)

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
        physical: list[_IdentityKind] = []
        if primary:
            paging = "primary_key"
            if rowid_table and rowid_free and any(columns[n].nullable for n in primary):
                physical = ["rowid"]  # M-2: NULL keys are told apart by the row id
        elif rowid_table and rowid_free:
            paging, physical = "rowid", ["rowid"]
        elif engine == "postgresql" and reflected.kind in ("table", "materialized_view"):
            paging, physical = "ctid", ["tableoid", "ctid"]  # H-1: a ctid is unique per relation only
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
        identity: list[_IdentityPart] = [("column", n) for n in primary]
        versions: list[_IdentityKind] = ["xmin"] if paging == "ctid" else []
        identity += [(kind, "") for kind in [*physical, *versions]]
        key_parts: list[_Part] = []
        key_selected: list[ColumnElement[Any]] = []
        if paging != "offset":
            extra = [(self._physical(names, "xmin"), False, False, True)] if paging == "ctid" else []
            for expression, _, _, small in keys + extra:
                expressions, part = _key(engine, expression, small)
                # Labeled apart: "CAST(ctid AS TEXT) AS ctid" would make ORDER BY ctid order the text.
                key_selected += [e.label(f"__studio_key_{len(key_selected) + i}") for i, e in enumerate(expressions)]
                key_parts.append(part)
        order_keys = [(e, d, n) for e, d, n, _ in keys]
        base = sqlalchemy.select(*shape.selected, *key_selected)
        for item in parsed:
            base = base.where(_condition(table.c[item.column], item))
        order = [(e.desc() if d else e.asc()).nulls_last() for e, d, _ in order_keys]

        def statement(anchor: tuple[Any, ...] | None, skip: int) -> sqlalchemy.Select[Any]:
            query = base
            if anchor is not None:
                if len(anchor) != len(order_keys):  # the table changed its key since the page before
                    raise ViewerError("studio.viewer_page_unknown")
                query = query.where(_after(order_keys, anchor))
            query = query.order_by(*order).limit(limit + 1)
            return query.offset(skip) if skip else query

        width = len(shape.selected)
        page_bytes = self.limits().max_result_mb * 1024 * 1024
        # M-4 residual: a fetch holds at most about FETCH_BUDGET_BYTES, by the largest row the byte caps allow (every
        # capped value at its cap), so a small first row cannot make the next batch large; a row of more than the
        # budget is fetched alone.
        # R-1: a decoded json, jsonb or array value takes up to about CONTAINER_FACTOR times the bytes of its text.
        widest = sum(
            part.limit * (CONTAINER_FACTOR if part.container else 1) if part.width > 1 else 16
            for part in [*shape.parts, *key_parts]
        )
        batch_cap = max(1, min(BATCH_ROWS, FETCH_BUDGET_BYTES // max(1, widest)))

        def consume(cursor: ResultCursor) -> tuple[list[_Found], bool]:
            # The first fetch is one row, the next ones as many rows as fit the budget at the size so far, never more
            # than batch_cap; the page stops at the byte cap (one row at least).
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
                batch = max(1, min(batch_cap, FETCH_BUDGET_BYTES * len(fetched) // max(1, loaded)))
            return found, False

        sort_offset = 1 if sort_key else 0

        def load(anchor: tuple[Any, ...] | None, skip: int) -> _Loaded:
            fetched, cut_bytes = self._run(connection, statement(anchor, skip), consume)
            loaded = _Loaded(more=cut_bytes or len(fetched) > limit, cut_bytes=cut_bytes)
            for index, (values, cut, sizes, kept, whole) in enumerate(fetched[:limit]):
                loaded.rows.append(values)
                loaded.cells += [[index, column] for column in cut]
                if paging == "offset":
                    loaded.kept.append(_Row(None, None))
                    continue
                loaded.last_key = tuple(kept[: len(keys)]) if all(whole[: len(keys)]) else None
                ident = tuple(kept[sort_offset:]) if all(whole[sort_offset:]) else None
                loaded.kept.append(_Row(ident, _digest(values, sizes) if physical else None))
            return loaded

        anchor = previous.anchor if previous is not None else None
        skip = previous.skip if previous is not None else 0
        stuck = previous is not None and previous.stuck
        seen = previous.seen if previous is not None else ()
        loaded = load(anchor, skip)
        if (
            previous is not None
            and paging != "offset"
            and not stuck
            and loaded.rows
            and loaded.last_key is not None
            and _anchor_digest(loaded.last_key) in {*seen, _anchor_digest(anchor)}
        ):
            # H-2 residual (progress guard): a key that does not come back as the value it was read from (float text
            # with a low extra_float_digits, say) can make a keyset page end where an earlier one did, and following
            # ``next`` would never end. The keyset cannot be trusted then: the walk goes on by OFFSET after the
            # previous page's own anchor, which returned exactly that page's rows, and stays on it to the end.
            stuck = True
            anchor, skip = previous.base_anchor, previous.base_skip + len(previous.rows)
            loaded = load(anchor, skip)
        base_anchor, base_skip = anchor, skip
        next_anchor: tuple[Any, ...] | None
        next_skip: int
        if paging != "offset" and not stuck and loaded.rows and loaded.last_key is not None:
            next_anchor, next_skip = loaded.last_key, 0
        else:
            next_anchor, next_skip = anchor, skip + len(loaded.rows)
        if anchor is not None:
            seen = (*seen, _anchor_digest(anchor))[-KEPT_ANCHORS:]
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
            loaded.kept,
            next_anchor,
            next_skip,
            loaded.more,
            base_anchor,
            base_skip,
            stuck,
            seen,
        )
        token = self._keep(page)
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "columns": [{"name": n, "type": columns[n].type} for n in names],
            "rows": loaded.rows,
            "truncated_cells": loaded.cells,
            "truncated": "bytes" if loaded.cut_bytes else None,
            "page": token,
            "next": token if loaded.more else None,
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
        if any(kind == "column" and n not in shape.names for kind, n in state.identity):
            raise ViewerError("studio.viewer_row_gone")  # the table changed its key since the page
        parts = list(zip(state.identity, kept.ident, strict=True))
        primary = [(p, v) for p, v in parts if p[0] == "column"]
        # M-3: the full primary key identifies a row. A physical id (ctid, rowid) can be reused by another row, so it
        # identifies one only together with what the page showed of the row.
        by_key = state.paging == "primary_key" and all(v is not None for _, v in primary)
        used = primary if by_key else parts
        target, target_part = _project(engine, shape.table.c[column], shape.columns[column], FULL_CELL_BYTES)
        statement = sqlalchemy.select(*target, *([] if by_key else shape.selected))
        for part, value in used:
            expression = self._identity_expression(shape, part)
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
