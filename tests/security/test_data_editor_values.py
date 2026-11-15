"""STU-XDE-1: the Data Editor's column types and value checks without a database (``write/values.py``).

The checks run the vectors the page's checks run too (``frontend/src/api/cellValues.vectors.json``, read by
``cellValues.test.ts``), so the page and the server agree on every case; the server is the authority. A refusal is a
problem whose issues are positions and ``value.*`` codes only (TX-14).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from tabrion_studio.problems import problem
from tabrion_studio.write.plan import _sqlite_table_options
from tabrion_studio.write.values import (
    KINDS,
    MAX_BYTES_VALUE,
    ColumnType,
    bytes_size,
    check_compared_value,
    check_value,
    is_hour_24,
    pg_column_type,
    sqlite_column_type,
)

pytestmark = pytest.mark.security

VECTORS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "cellValues.vectors.json"


def _column(doc: dict[str, Any]) -> ColumnType:
    return ColumnType(
        name=doc["name"],
        kind=doc["kind"],
        nullable=doc["nullable"],
        optional=doc["optional"],
        read_only=doc["read_only"],
        length=doc.get("length"),
        precision=doc.get("precision"),
        scale=doc.get("scale"),
        array=doc["array"],
        values=tuple(doc.get("values", ())),
    )


def _cases() -> list[Any]:
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    return [pytest.param(case, id=f"{index}-{case['column']['kind']}") for index, case in enumerate(cases)]


@pytest.mark.parametrize("case", _cases())
def test_the_shared_vectors(case: dict[str, Any]) -> None:
    column = _column(case["column"])
    assert column.kind in KINDS
    assert column.to_dict() == {key: value for key, value in case["column"].items() if key != "values" or value}
    assert check_value(column, case["value"]) == case["issue"]


def test_the_vectors_cover_every_kind_and_every_issue() -> None:
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    assert {case["column"]["kind"] for case in cases} == KINDS
    assert {case["issue"] for case in cases} == {
        None,
        "value.read_only",
        "value.not_null",
        "value.type",
        "value.format",
        "value.too_long",
        "value.out_of_range",
        "value.not_allowed",
    }


def _pg(
    scalar: str,
    *,
    typmod: int = -1,
    array: bool = False,
    schema: str = "pg_catalog",
    typtype: str = "b",
    labels: list[str] | None = None,
    not_null: bool = False,
    default: bool = False,
    identity: str = "",
    generated: str = "",
) -> ColumnType:
    row = ("c", not_null, default, identity, generated, typmod, array, scalar, schema, typtype, labels or [])
    return pg_column_type(row)


def test_postgres_types_and_modifiers() -> None:
    assert _pg("varchar", typmod=24).length == 20
    assert _pg("bpchar", typmod=5).length == 1
    assert _pg("text").length is None
    numeric = _pg("numeric", typmod=(10 << 16) + 2 + 4)
    assert (numeric.kind, numeric.precision, numeric.scale) == ("numeric", 10, 2)
    negative = _pg("numeric", typmod=(3 << 16) + (0x800 - 2) + 4)  # numeric(3, -2), PostgreSQL 15+
    assert (negative.precision, negative.scale) == (3, -2)
    assert _pg("numeric").precision is None
    assert _pg("jsonb").kind == "json" and _pg("bytea").kind == "bytes" and _pg("bool").kind == "boolean"
    assert _pg("int4", array=True).to_dict()["array"] is True
    # Kinds without element checks make the array untyped; so do types outside pg_catalog and unknown built-ins.
    assert (_pg("jsonb", array=True).kind, _pg("jsonb", array=True).array) == ("other", False)
    assert _pg("citext", schema="public").kind == "other"
    assert _pg("interval").kind == "other"
    mood = _pg("mood", schema="app", typtype="e", labels=["ok", "bad"])
    assert (mood.kind, mood.values) == ("enum", ("ok", "bad"))
    assert mood.to_dict()["values"] == ["ok", "bad"]
    assert _pg("mood", schema="app", typtype="e", labels=["ok"], array=True).kind == "other"
    assert _pg("mood", schema="app", typtype="e", labels=[str(n) for n in range(1001)]).kind == "other"


def test_postgres_nullability_defaults_identity_and_generated_columns() -> None:
    required = _pg("text", not_null=True)
    assert (required.nullable, required.optional, required.read_only) == (False, False, False)
    assert _pg("text", not_null=True, default=True).optional is True
    by_default = _pg("int4", not_null=True, identity="d")
    assert (by_default.optional, by_default.read_only) == (True, False)
    always = _pg("int4", not_null=True, identity="a")
    assert (always.optional, always.read_only) == (True, True)
    generated = _pg("int4", generated="s")
    assert (generated.optional, generated.read_only) == (True, True)
    assert check_value(generated, None) == "value.read_only"


def test_an_sqlite_without_pragma_table_list_reads_every_table_as_untyped() -> None:
    """Before SQLite 3.37 there are no STRICT tables; the table is read as a lenient rowid table, unasked."""
    asked: list[str] = []

    def fetch(sql: str, params: dict[str, Any] | None) -> list[tuple[Any, ...]]:
        asked.append(sql)
        return [("3.36.9",)] if "sqlite_version" in sql else [(1, 1)]

    assert _sqlite_table_options(fetch, "t") == (False, False)
    assert len(asked) == 1
    assert _sqlite_table_options(lambda sql, params: [("3.37.0",)] if "version" in sql else [(1, 0)], "t") == (
        True,
        False,
    )


def test_sqlite_columns() -> None:
    plain = sqlite_column_type("a", "INTEGER", 1, None, 0, strict=False, rowid=False)
    assert (plain.kind, plain.nullable, plain.optional) == ("other", False, False)
    assert sqlite_column_type("a", "integer", 0, None, 0, strict=True, rowid=False).kind == "int8"
    assert sqlite_column_type("a", "REAL", 0, None, 0, strict=True, rowid=False).kind == "float8"
    assert sqlite_column_type("a", "TEXT", 0, None, 0, strict=True, rowid=False).kind == "text"
    assert sqlite_column_type("a", "BLOB", 0, None, 0, strict=True, rowid=False).kind == "bytes"
    assert sqlite_column_type("a", "ANY", 0, None, 0, strict=True, rowid=False).kind == "other"
    alias = sqlite_column_type("id", "INTEGER", 1, None, 0, strict=True, rowid=True)
    assert (alias.nullable, alias.optional) == (True, True)
    assert sqlite_column_type("a", "TEXT", 1, "'x'", 0, strict=False, rowid=False).optional is True
    generated = sqlite_column_type("g", "INT", 0, None, 3, strict=False, rowid=False)
    assert (generated.read_only, generated.optional) == (True, True)


def test_value_issues_are_positions_and_fixed_codes_only() -> None:
    doc = problem("studio.data_editor_value_invalid", issues=[("changes.3.12", "value.too_long")]).to_dict()
    assert doc["issues"] == [{"pointer": "changes.3.12", "code": "value.too_long"}]
    for pointer, code in [
        ("changes.3.material", "value.too_long"),
        ("changes.3", "value.too_long"),
        ("changes.3.1", "value.too long"),
        ("changes.3.1", "db.unique"),
        ("changes.12345.1", "value.format"),
    ]:
        with pytest.raises(ValueError, match="malformed issues"):
            problem("studio.data_editor_value_invalid", issues=[(pointer, code)])


def test_constraint_issues_are_positions_and_fixed_codes_only() -> None:
    """G-S2 6A: a constraint refusal names the change, and the column's position where the database named it."""
    issues = [("changes.3", "constraint.unique"), ("changes.3.1", "constraint.foreign_key")]
    doc = problem("studio.data_editor_constraint", issues=issues).to_dict()
    assert doc["status"] == 400 and doc["issues"] == [
        {"pointer": "changes.3", "code": "constraint.unique"},
        {"pointer": "changes.3.1", "code": "constraint.foreign_key"},
    ]
    for pointer, code in [
        ("changes.3.qty", "constraint.check"),
        ("profile.host", "constraint.check"),
        ("changes.3", "constraint.other"),
        ("changes.3", "constraint.unique x"),
        ("changes.12345", "constraint.not_null"),
    ]:
        with pytest.raises(ValueError, match="malformed issues"):
            problem("studio.data_editor_constraint", issues=[(pointer, code)])


@pytest.mark.parametrize("kind", ["bytes", "other"])
def test_a_new_bytes_value_has_at_most_the_limit(kind: str) -> None:
    """G-S2 6A: a file that replaces a bytes value has at most MAX_BYTES_VALUE bytes, whatever the column's kind; a
    compared (old) value is what the viewer showed and is not limited here."""
    column = ColumnType(name="c", kind=kind, nullable=True, optional=True, read_only=False)
    for size, issue in (
        (0, None),
        (1, None),
        (2, None),
        (MAX_BYTES_VALUE, None),
        (MAX_BYTES_VALUE + 1, "value.too_large"),
    ):
        value = {"base64": base64.b64encode(b"\x07" * size).decode("ascii")}
        assert bytes_size(value) == size
        assert check_value(column, value) == issue, size
    big = {"base64": base64.b64encode(b"\x07" * (MAX_BYTES_VALUE + 1)).decode("ascii")}
    assert check_compared_value(column, big) is None


# --- G2-02 and G2-03 of the G-S2 gate review ------------------------------------------------------------------------


@pytest.mark.parametrize("strict", [False, True])
def test_a_sqlite_integer_must_fit_in_64_bits_whatever_the_kind(strict: bool) -> None:
    """G2-02: SQLite and its driver take no integer wider than 64 bits; refused before the plan, as a new value and
    as a key or old value, for an untyped column too. PostgreSQL's untyped columns keep any JSON number."""
    for declared in ("INTEGER", "TEXT", "ANY"):
        column = sqlite_column_type("a", declared, 0, None, 0, strict=strict, rowid=False)
        assert "sqlite" not in column.to_dict()
        for fits in (0, (1 << 63) - 1, -(1 << 63), True, 1.5, "99999999999999999999999"):
            assert check_compared_value(column, fits) is None
        for wide in (1 << 63, -(1 << 63) - 1, 10**40):
            assert check_value(column, wide) == "value.out_of_range"
            assert check_compared_value(column, wide) == "value.out_of_range"
    assert check_value(_pg("interval"), 10**40) is None
    assert check_compared_value(_pg("int8"), 10**40) is None


@pytest.mark.parametrize(
    "text",
    [
        "24:00",
        "24:00:00",
        "24:00:00.000",
        "24:00:00.000000",
        "24:00:00+02:00",
        "24:00:00+02",
        "24:00Z",
        "2024-12-31 24:00",
        "2024-12-31T24:00:00.0Z",
        "2024-12-31 24:00:00+00",
    ],
)
def test_hour_24_is_recognized_as_postgresql_writes_it(text: str) -> None:
    assert is_hour_24(text)


@pytest.mark.parametrize(
    "text", ["24:00:01", "24:01", "24:00:00.000001", "00:00", "23:59:59", "2024-12-31", "x24:00", "24:00 ", 2400]
)
def test_other_times_are_not_hour_24(text: object) -> None:
    assert not is_hour_24(text)


@pytest.mark.parametrize("kind", ["time", "timetz", "timestamp", "timestamptz"])
def test_hour_24_passes_the_check_and_binds_as_text_on_every_python_version(kind: str) -> None:
    """G2-03: the check takes 24:00 (as PostgreSQL does), and bind_value hands the text to the database instead of
    ``fromisoformat``, which refuses it before Python 3.14 and turns it into the next midnight from 3.14 on."""
    import sqlalchemy
    from sqlalchemy.dialects import postgresql

    from tabrion_studio.write.data_editor import bind_value

    text = {"time": "24:00", "timetz": "24:00:00+02:00", "timestamp": "2024-12-31 24:00"}.get(
        kind, "2024-12-31 24:00:00Z"
    )
    assert check_value(ColumnType("c", kind=kind), text) is None
    types: dict[str, sqlalchemy.types.TypeEngine[Any]] = {
        "time": sqlalchemy.Time(),
        "timetz": sqlalchemy.Time(timezone=True),
        "timestamp": sqlalchemy.DateTime(),
        "timestamptz": postgresql.TIMESTAMP(timezone=True),
    }
    column: sqlalchemy.Column[Any] = sqlalchemy.Column("c", types[kind])
    assert bind_value(column, text, "postgresql") == text
    # Any other time still goes through fromisoformat.
    assert not isinstance(bind_value(column, text.replace("24:00", "23:00"), "postgresql"), str)
