"""The Data Editor's column types and value checks (STU-XDE-1; planning document 15 section 4; STU-ADR-0006; threats
TX-13, TX-14 and TX-17 of the write threat model).

Part of the write package: imported by the plan framework's catalog read (:mod:`tabrion_studio.write.plan`), never
by a read path. Gate G-S2 is not granted: nothing here turns a flag on.

**Column types.** The catalog read gives every column of a target a :class:`ColumnType`: its ``kind`` (one of
:data:`KINDS`), whether it takes NULL, whether an insert may leave it out (a default, an identity, SQLite's rowid
alias), whether it takes no value at all (a generated column, an identity ``ALWAYS``), the length of a
``varchar(n)``/``char(n)``, the precision and scale of a ``numeric(p, s)``, the labels of an enum and whether it is
an array. The table route sends them to the page, which picks a cell editor and checks a value with the same rules
(``frontend/src/api/cellValues.ts``) before the preview. **The server checks every new value again** when the plan is
made (:func:`check_value`, from the executor's ``check``), so the page's checks are help, never the authority; the
database stays the last instance (CHECK, UNIQUE, foreign keys, a domain's constraints, triggers).

* PostgreSQL: the kind comes from the column's type after unwrapping domains (a domain's own length or precision
  counts); an array of booleans, integers, floats or text is checked element by element (rectangular, NULL elements
  allowed, at most 6 dimensions).
  Types without a kind of their own (``interval``, ``inet``, ``money``, ranges, extension types such as ``citext``,
  arrays of enums or domains, which the viewer shows as array text) are ``other``: any JSON value, checked by the
  database only.
* SQLite: a column's declared type is advice, not a rule, so every column of an ordinary table is ``other``; a
  ``STRICT`` table's columns are ``int8`` (``INT``, ``INTEGER``), ``float8`` (``REAL``), ``text`` (``TEXT``),
  ``bytes`` (``BLOB``) or ``other`` (``ANY``).

The catalog fingerprint covers what the checks depend on (PostgreSQL: each column's type with its modifier,
nullability, default, identity and generation; SQLite: the table's SQL), so a check made at preview time cannot be
outrun by a schema change before the run (``studio.plan_drift``). A label added to an enum afterwards is not drift:
it only lets the database take a value the check refused.

**No values in answers (TX-14).** A refusal is ``studio.data_editor_value_invalid`` (400, W-9) with ``issues``:
``{"pointer": "changes.<change>.<column>", "code": "value.<check>"}``, the change's position in the request and the
column's position in the table. A pointer and a code are numbers and fixed words; no value, no column name and no
database text is in an answer, a log record or the audit log.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import datetime
import math
import re
from typing import Any

__all__ = [
    "KINDS",
    "PG_COLUMN_TYPES",
    "ColumnType",
    "check_value",
    "is_bytes",
    "pg_column_type",
    "sqlite_column_type",
    "sqlite_strict",
]

#: The kinds of a column (the page has a cell editor and the same checks for each).
KINDS = frozenset(
    {
        "boolean",
        "int2",
        "int4",
        "int8",
        "float4",
        "float8",
        "numeric",
        "text",
        "date",
        "time",
        "timetz",
        "timestamp",
        "timestamptz",
        "uuid",
        "json",
        "enum",
        "bytes",
        "other",
    }
)
#: The kinds an array may have elements of: those whose values the viewer sends as JSON (others: the array is
#: ``other``; the viewer cannot show an array of dates, decimals or UUIDs yet, docs/TODO.md).
_ARRAY_KINDS = frozenset({"boolean", "int2", "int4", "int8", "float4", "float8", "text"})
#: At most this many enum labels are sent and checked (PostgreSQL allows more; beyond it the kind is ``other``).
MAX_ENUM_VALUES = 1000

_INT_RANGES = {"int2": 1 << 15, "int4": 1 << 31, "int8": 1 << 63}
_FLOAT4_MAX = 3.4028234663852886e38
_INTEGER = re.compile(r"[+-]?\d+", re.ASCII)
_DECIMAL = re.compile(r"([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?", re.ASCII)
_SPECIAL_FLOAT = re.compile(r"[+-]?(?:inf|infinity)|nan", re.IGNORECASE | re.ASCII)
_DATE = r"\d{4}-\d{2}-\d{2}"
_TIME = r"\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
_OFFSET = r"(?:Z|[+-]\d{2}:\d{2}(?::\d{2})?)"
_FORMATS = {
    "date": re.compile(_DATE, re.ASCII),
    "time": re.compile(_TIME, re.ASCII),
    "timetz": re.compile(_TIME + _OFFSET, re.ASCII),
    "timestamp": re.compile(_DATE + "[T ]" + _TIME, re.ASCII),
    "timestamptz": re.compile(_DATE + "[T ]" + _TIME + _OFFSET, re.ASCII),
    "uuid": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
}
_BOOLEAN_TEXT = {"true": True, "t": True, "1": True, "false": False, "f": False, "0": False}

#: PostgreSQL's built-in types (schema ``pg_catalog``) that have a kind of their own.
_PG_KINDS = {
    "bool": "boolean",
    "int2": "int2",
    "int4": "int4",
    "int8": "int8",
    "float4": "float4",
    "float8": "float8",
    "numeric": "numeric",
    "text": "text",
    "varchar": "text",
    "bpchar": "text",
    "date": "date",
    "time": "time",
    "timetz": "timetz",
    "timestamp": "timestamp",
    "timestamptz": "timestamptz",
    "uuid": "uuid",
    "json": "json",
    "jsonb": "json",
    "bytea": "bytes",
}

#: One row per column of a table, in table order: name, NOT NULL, has a default, identity, generated, the type
#: modifier (a domain's own when the column has none), whether it is an array, the scalar type (an array's element
#: type) by name, schema and ``typtype``, and an enum's labels in their order. Domains are unwrapped (at most 32
#: levels, as ``plan._PG_INCOMPARABLE``). Fixed text, bound parameters, schema-qualified operators (K-2).
PG_COLUMN_TYPES = (
    "WITH RECURSIVE base(attnum, typ, typmod, depth) AS ("
    " SELECT a.attnum, a.atttypid, a.atttypmod, 0 FROM pg_catalog.pg_attribute a"
    " WHERE a.attrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid)"
    " AND a.attnum OPERATOR(pg_catalog.>) 0 AND NOT a.attisdropped"
    " UNION ALL SELECT b.attnum, t.typbasetype,"
    " CASE WHEN b.typmod OPERATOR(pg_catalog.<>) -1 THEN b.typmod ELSE t.typtypmod END,"
    " b.depth OPERATOR(pg_catalog.+) 1"
    " FROM base b JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) b.typ"
    " WHERE t.typtype OPERATOR(pg_catalog.=) 'd' AND b.depth OPERATOR(pg_catalog.<) 32)"
    " SELECT a.attname::pg_catalog.text, a.attnotnull, a.atthasdef, a.attidentity::pg_catalog.text,"
    " a.attgenerated::pg_catalog.text, b.typmod::pg_catalog.int4,"
    " (t.typcategory OPERATOR(pg_catalog.=) 'A' AND t.typelem OPERATOR(pg_catalog.<>) 0),"
    " s.typname::pg_catalog.text, n.nspname::pg_catalog.text, s.typtype::pg_catalog.text,"
    " ARRAY(SELECT e.enumlabel::pg_catalog.text FROM pg_catalog.pg_enum e"
    " WHERE e.enumtypid OPERATOR(pg_catalog.=) s.oid ORDER BY e.enumsortorder)"
    " FROM base b"
    " JOIN pg_catalog.pg_attribute a ON a.attrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid)"
    " AND a.attnum OPERATOR(pg_catalog.=) b.attnum"
    " JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) b.typ"
    " JOIN pg_catalog.pg_type s ON s.oid OPERATOR(pg_catalog.=) CASE"
    " WHEN t.typcategory OPERATOR(pg_catalog.=) 'A' AND t.typelem OPERATOR(pg_catalog.<>) 0 THEN t.typelem"
    " ELSE t.oid END"
    " JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) s.typnamespace"
    " WHERE t.typtype OPERATOR(pg_catalog.<>) 'd' ORDER BY b.attnum"
)


@dataclass(frozen=True)
class ColumnType:
    """One column as the Data Editor checks it (see the module text). Names and labels only, never a value."""

    name: str
    kind: str = "other"
    nullable: bool = True
    #: An insert may leave the column out: it has a default, it is an identity, or it is SQLite's rowid alias.
    optional: bool = True
    #: The column takes no value (generated, or identity ``ALWAYS``).
    read_only: bool = False
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    array: bool = False
    values: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "nullable": self.nullable,
            "optional": self.optional,
            "read_only": self.read_only,
            "array": self.array,
        }
        for key in ("length", "precision", "scale"):
            if getattr(self, key) is not None:
                doc[key] = getattr(self, key)
        if self.kind == "enum":
            doc["values"] = list(self.values)
        return doc


def pg_column_type(row: Sequence[Any]) -> ColumnType:
    """The :class:`ColumnType` of a row of :data:`PG_COLUMN_TYPES`."""
    name, not_null, has_default, identity, generated, typmod, is_array, scalar, schema, typtype, labels = row
    kind = "other"
    length = precision = scale = None
    if typtype == "e":
        kind = "enum" if len(labels) <= MAX_ENUM_VALUES else "other"
    elif schema == "pg_catalog":
        kind = _PG_KINDS.get(str(scalar), "other")
    modifier = int(typmod)
    if kind == "text" and scalar != "text" and modifier >= 4:
        length = modifier - 4
    if kind == "numeric" and modifier >= 4:
        precision = ((modifier - 4) >> 16) & 0xFFFF
        scale = (((modifier - 4) & 0x7FF) ^ 0x400) - 0x400  # PostgreSQL 15+ allows a negative scale
    array = bool(is_array)
    if array and kind not in _ARRAY_KINDS:
        kind, array, length, precision, scale = "other", False, None, None, None
    return ColumnType(
        name=str(name),
        kind=kind,
        nullable=not not_null,
        optional=bool(has_default) or identity in ("a", "d") or bool(generated),
        read_only=identity == "a" or bool(generated),
        length=length,
        precision=precision,
        scale=scale,
        array=array,
        values=tuple(str(label) for label in labels) if kind == "enum" else (),
    )


#: The table options at the end of a SQLite ``CREATE TABLE``: ``WITHOUT ROWID`` and ``STRICT``, in any order.
_SQLITE_OPTIONS = re.compile(
    r"\)\s*((?:WITHOUT\s+ROWID|STRICT)(?:\s*,\s*(?:WITHOUT\s+ROWID|STRICT))*)?\s*;?\s*\Z", re.IGNORECASE
)
_SQLITE_STRICT_KINDS = {"INT": "int8", "INTEGER": "int8", "REAL": "float8", "TEXT": "text", "BLOB": "bytes"}


def sqlite_strict(sql: str | None) -> tuple[bool, bool]:
    """Whether a SQLite table's SQL ends with ``STRICT`` and with ``WITHOUT ROWID``. Anything else after the closing
    parenthesis (a comment, say) counts as neither: the checks then stay lenient and the database decides."""
    found = _SQLITE_OPTIONS.search(sql or "")
    options = re.sub(r"\s+", " ", (found.group(1) or "") if found else "").upper()
    return "STRICT" in options, "WITHOUT ROWID" in options


def sqlite_column_type(
    name: str, declared: str | None, not_null: int, default: object, hidden: int, *, strict: bool, rowid: bool
) -> ColumnType:
    """The :class:`ColumnType` of a ``pragma_table_xinfo`` row. ``rowid`` is True for the table's rowid alias (an
    ``INTEGER PRIMARY KEY`` of a rowid table): an insert may leave it out, SQLite gives it the next number."""
    kind = _SQLITE_STRICT_KINDS.get((declared or "").strip().upper(), "other") if strict else "other"
    return ColumnType(
        name=name,
        kind=kind,
        nullable=not not_null or rowid,
        optional=default is not None or rowid or hidden in (2, 3),
        read_only=hidden in (2, 3),
    )


# --- the checks ----------------------------------------------------------------------------------------------------


def check_value(column: ColumnType, value: Any) -> str | None:
    """None if ``value`` (a page value: JSON, ``{"base64": ...}`` for bytes) fits ``column``, else the issue code
    (``value.<check>``). The code names the check, never the value."""
    if column.read_only:
        return "value.read_only"
    if value is None:
        return None if column.nullable else "value.not_null"
    if column.array and column.kind in _ARRAY_KINDS:
        return _check_array(column, value, 0)
    return _check_scalar(column, value)


def _check_array(column: ColumnType, value: Any, depth: int) -> str | None:
    if not isinstance(value, list) or depth > 5:  # PostgreSQL arrays have at most 6 dimensions
        return "value.type"
    if any(isinstance(item, list) for item in value):
        # A sub-array: every item one, all of the same length (PostgreSQL arrays are rectangular).
        if not all(isinstance(item, list) and len(item) == len(value[0]) for item in value):
            return "value.format"
        for item in value:
            issue = _check_array(column, item, depth + 1)
            if issue is not None:
                return issue
        return None
    for item in value:
        issue = None if item is None else _check_scalar(column, item)
        if issue is not None:
            return issue
    return None


def _check_scalar(column: ColumnType, value: Any) -> str | None:
    kind = column.kind
    if kind in ("other", "json"):
        return None
    if kind == "bytes":
        return None if is_bytes(value) else "value.type"
    if kind == "boolean":
        if isinstance(value, bool) or (isinstance(value, str) and value.strip().lower() in _BOOLEAN_TEXT):
            return None
        return "value.type"
    if isinstance(value, bool):
        return "value.type"
    if kind in _INT_RANGES:
        return _check_integer(kind, value)
    if kind in ("float4", "float8"):
        return _check_float(kind, value)
    if kind == "numeric":
        return _check_numeric(column, value)
    if not isinstance(value, str):
        return "value.type"
    if "\x00" in value:
        return "value.format"  # PostgreSQL text takes no NUL character
    if kind == "text":
        # Like PostgreSQL: a longer value is refused unless the excess is spaces, which are cut.
        return "value.too_long" if column.length is not None and len(value.rstrip(" ")) > column.length else None
    if kind == "enum":
        return None if value in column.values else "value.not_allowed"
    return _check_format(kind, value)


def _check_integer(kind: str, value: Any) -> str | None:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        value = int(value)
    if not isinstance(value, int):
        return "value.format" if isinstance(value, str) else "value.type"
    limit = _INT_RANGES[kind]
    return None if -limit <= value < limit else "value.out_of_range"


def _check_float(kind: str, value: Any) -> str | None:
    if isinstance(value, str):
        if _SPECIAL_FLOAT.fullmatch(value):
            return None
        if not _decimal_parts(value):
            return "value.format"
        value = float(value)
    if not isinstance(value, int | float):
        return "value.type"
    number = float(value)
    if not math.isfinite(number) or (kind == "float4" and abs(number) > _FLOAT4_MAX):
        return "value.out_of_range"
    return None


def _decimal_parts(text: str) -> tuple[str, str, int] | None:
    """``(sign, digits, exponent)`` of a decimal text (``value = digits * 10 ** exponent``), or None."""
    found = _DECIMAL.fullmatch(text)
    if found is None or not (found.group(2) or found.group(3)):
        return None
    sign, whole, fraction, exponent = found.group(1), found.group(2), found.group(3) or "", found.group(4) or "0"
    if len(exponent.lstrip("+-")) > 9:
        return sign, (whole + fraction).lstrip("0"), -(10**9) if exponent.startswith("-") else 10**9
    return sign, (whole + fraction).lstrip("0"), int(exponent) - len(fraction)


def _check_numeric(column: ColumnType, value: Any) -> str | None:
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return "value.out_of_range"
        value = repr(value) if isinstance(value, float) else str(value)
    if not isinstance(value, str):
        return "value.type"
    if value.lower() == "nan":
        return None
    if _SPECIAL_FLOAT.fullmatch(value):
        return None if column.precision is None else "value.out_of_range"  # infinity: unconstrained numeric only
    parts = _decimal_parts(value)
    if parts is None:
        return "value.format"
    if column.precision is None:
        return None
    return None if _fits(parts[1], parts[2], column.precision, column.scale or 0) else "value.out_of_range"


def _fits(digits: str, exponent: int, precision: int, scale: int) -> bool:
    """Whether ``digits * 10 ** exponent``, rounded half away from zero to ``scale`` places as PostgreSQL does, has
    at most ``precision`` digits. Only powers as large as the input are computed."""
    if not digits:
        return True
    shift = exponent + scale
    if shift >= 0:
        return len(digits) + shift <= precision
    if -shift > len(digits):
        return True  # below half of the last place: rounds to zero
    quotient, rest = divmod(int(digits), 10**-shift)
    if 2 * rest >= 10**-shift:
        quotient += 1
    return quotient == 0 or len(str(quotient)) <= precision


def _check_format(kind: str, value: str) -> str | None:
    pattern = _FORMATS.get(kind)
    if pattern is None or not pattern.fullmatch(value):
        return "value.format"
    if kind == "uuid":
        return None
    try:
        if kind == "date":
            datetime.date.fromisoformat(value)
        elif kind in ("time", "timetz"):
            datetime.time.fromisoformat(value)
        else:
            datetime.datetime.fromisoformat(value)
    except ValueError:
        return "value.format"
    return None


def is_bytes(value: object) -> bool:
    """Whether ``value`` is the page's form of bytes, ``{"base64": ...}`` with valid base64."""
    if not isinstance(value, Mapping) or set(value) != {"base64"} or not isinstance(value["base64"], str):
        return False
    try:
        base64.b64decode(value["base64"], validate=True)
    except (binascii.Error, ValueError):
        return False
    return True
