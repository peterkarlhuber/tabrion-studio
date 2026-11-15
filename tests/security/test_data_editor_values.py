"""STU-XDE-1: the Data Editor's column types and value checks without a database (``write/values.py``).

The checks run the vectors the page's checks run too (``frontend/src/api/cellValues.vectors.json``, read by
``cellValues.test.ts``), so the page and the server agree on every case; the server is the authority. A refusal is a
problem whose issues are positions and ``value.*`` codes only (TX-14).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabrion_studio.problems import problem
from tabrion_studio.write.plan import _sqlite_table_options
from tabrion_studio.write.values import (
    KINDS,
    ColumnType,
    check_value,
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
