"""The Data Editor (STU-026; 03 sections 2 D and 5.2, 08 section 4.6; STU-ADR-0006; threats TX-08, TX-09, TX-13,
TX-14, TX-16; reviews N-3 of PR #109, L-1 and I-1 of tabrion PR #78).

Part of the write package: imported only while the Data Editor flag is on (TX-02), never by a read path. Gate G-S2
is not granted: the flag stays off by default and nothing here turns it on.

**A changeset is a plan.** The page collects the user's changes to the rows of **one table** and sends them as the
request of the executor :class:`DataEditorExecutor` (``data_editor.changeset``) of the plan framework
(:mod:`tabrion_studio.write.plan`): preview, typed confirmation and one execution through
:meth:`Unlocks.audited_transaction <tabrion_studio.write.unlock.Unlocks.audited_transaction>`. Each change is one
step (``row.update``, ``row.insert``, ``row.delete``; a delete is destructive, so its plan is confirmed with the
table's name, D-10). Request::

    {"schema": "main", "table": "parts", "columns": ["id", "material"],
     "changes": [{"op": "update", "key": {"id": 1}, "old": {"id": 1, "material": "Steel"}, "new": {"material": "Iron"}},
                 {"op": "insert", "values": {"material": "Titan"}},
                 {"op": "delete", "key": {"id": 7}, "old": {"id": 7, "material": "Copper"}}]}

* ``columns`` are the columns the page **showed**; an update or a delete passes **every one of them** as ``old``
  (review L-1 of tabrion #78), so a concurrent change of any shown value is a conflict. ``key`` is exactly the
  table's primary key (never ``rowid`` or ``ctid``); its values are those of ``old``. A row is changed at most once
  per changeset.
* Values are JSON as the viewer sends them: text, numbers, booleans, ``null`` (SQL ``NULL``, never the empty text),
  ``{"base64": ...}`` for bytes, and on PostgreSQL JSON objects and arrays. A value of the database's own type
  (dates, decimals, UUIDs) is text and converted for the column's reflected type when the plan runs.

**Refused targets.** A view, a foreign table, a partitioned table or a partition (the framework's catalog read,
``studio.plan_target_invalid``, ``studio.plan_target_partitioned``), a table without a primary key
(``studio.plan_target_no_primary_key``, :attr:`DataEditorExecutor.requires_primary_key`) and PROD (no unlock of a
PROD profile exists, E-X7; re-checked from the stored profile on every use).

**Running.** Inside the run's transaction, after the framework's drift check (the table's catalog, its triggers,
rules and the functions they call are those of the preview), the table is reflected on the write connection and each
step runs as one statement of :mod:`tabrion.rowedit` with ``Transaction.execute_checked(expect_rowcount=1)``:
identifiers quoted by SQLAlchemy, every value a bound parameter, the optimistic predicate NULL-safe and exact. A
statement that changes another number of rows than 1, an update or a delete whose row changed or is gone (0 rows)
included, rolls the **whole changeset** back (03 section 5.2): ``studio.data_editor_conflict`` (0 rows of an update or
delete) or ``studio.data_editor_rowcount`` (any other count). Rows that triggers or PostgreSQL rules change are not in
the count (review I-1 of tabrion #78); the preview names them (TX-16).

**No values anywhere but the database (TX-13, TX-14, N-3).** The answers, the problems, the log records and the audit
records name codes, counts, the table and the plan hash only. A changeset never reaches the query history or the
draft queue: nothing here calls the query engine, the history or the drafts.

**Hooks for STU-XDE-1** (validation from the reflection, type editors, the change overview): :func:`bind_value` is
the one place that turns a page value into a bound value, and :func:`table_info` the one answer the page builds its
grid from.
"""

from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
from collections.abc import Mapping
import datetime
import decimal
import json
import logging
import math
import re
import threading
from typing import Any
import uuid

import sqlalchemy
import tabrion
from tabrion.errors import RowCountMismatch, UsageError
from tabrion.rowedit import delete_row, insert_row, update_row

from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.plan import (
    MAX_NAME,
    MAX_STEPS,
    Plan,
    PlanContent,
    Step,
    Target,
    TargetCatalog,
    read_catalog,
)
from tabrion_studio.write.unlock import AuditedRun, Unlocks

__all__ = ["EXECUTOR_NAME", "DataEditorExecutor", "bind_value", "table_info"]

logger = logging.getLogger("tabrion_studio.write.data_editor")

EXECUTOR_NAME = "data_editor.changeset"
#: At most this many columns shown (PostgreSQL's limit per table).
MAX_COLUMNS = 1600
_OPS = {"update": ("key", "old", "new"), "insert": ("values",), "delete": ("key", "old")}
_ACTIONS = {"update": "row.update", "insert": "row.insert", "delete": "row.delete"}
_COUNTS = {"row.update": "updated", "row.insert": "inserted", "row.delete": "deleted"}
_REQUEST_KEYS = frozenset({"schema", "table", "columns", "changes"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
#: Reflected tables of the runs in progress (one per run and target), the oldest dropped first.
_KEPT_TABLES = 16


def _invalid() -> WriteError:
    return WriteError("studio.plan_invalid")


def _name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_NAME
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise _invalid()
    return value


def _is_bytes(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"base64"} or not isinstance(value["base64"], str):
        return False
    try:
        base64.b64decode(value["base64"], validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _value(value: object, engine: str) -> None:
    """A page value: JSON scalars, ``{"base64": ...}``, and on PostgreSQL JSON objects and arrays (JSON, arrays)."""
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _invalid()
        return
    if _is_bytes(value):
        return
    if engine == "postgresql" and isinstance(value, Mapping | list):
        return  # nested values are canonical JSON (the framework checked the request)
    raise _invalid()


def _values(raw: object, columns: frozenset[str], engine: str, *, empty: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or (not raw and not empty):
        raise _invalid()
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in columns:
            raise _invalid()
        _value(value, engine)
        out[key] = value
    return out


def _same(first: object, second: object) -> bool:
    """JSON equality that tells ``1``, ``1.0`` and ``true`` apart."""
    return json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


class DataEditorExecutor:
    """The Data Editor's plan executor (see the module text). Thread-safe; one per server."""

    name = EXECUTOR_NAME
    area = Area.DATA_EDITOR
    #: Every target needs a primary key (the framework refuses one without, ``studio.plan_target_no_primary_key``).
    requires_primary_key = True

    def __init__(self) -> None:
        self._tables: OrderedDict[tuple[str, int], sqlalchemy.Table] = OrderedDict()
        self._lock = threading.Lock()

    # --- build: the request, checked without the database ---------------------------------------------------------

    def build(self, request: Mapping[str, Any], engine: str) -> PlanContent:
        if set(request) - _REQUEST_KEYS:
            raise _invalid()
        schema = _name(request.get("schema", "main" if engine == "sqlite" else None))
        table = _name(request.get("table"))
        raw_columns = request.get("columns")
        if not isinstance(raw_columns, list) or not 1 <= len(raw_columns) <= MAX_COLUMNS:
            raise _invalid()
        shown = [_name(column) for column in raw_columns]
        columns = frozenset(shown)
        if len(columns) != len(shown):
            raise _invalid()
        changes = request.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_STEPS:
            raise _invalid()
        steps: list[Step] = []
        keys: set[str] = set()
        for change in changes:
            if not isinstance(change, Mapping):
                raise _invalid()
            op = change.get("op")
            if not isinstance(op, str) or op not in _OPS or set(change) != {"op", *_OPS[op]}:
                raise _invalid()
            data: dict[str, Any] = {"op": op}
            if op == "insert":
                data["values"] = _values(change["values"], columns, engine, empty=True)
            else:
                old = _values(change["old"], columns, engine, empty=False)
                if set(old) != columns:
                    raise _invalid()  # every column the page showed (review L-1 of tabrion #78)
                key = _values(change["key"], columns, engine, empty=False)
                if any(value is None or not _same(value, old[name]) for name, value in key.items()):
                    raise _invalid()
                identity = json.dumps(key, sort_keys=True)
                if identity in keys:
                    raise _invalid()  # one change per row and changeset
                keys.add(identity)
                data["key"], data["old"] = key, old
                if op == "update":
                    data["new"] = _values(change["new"], columns, engine, empty=False)
            steps.append(Step(_ACTIONS[op], 0, destructive=op == "delete", rows=1, data=data))
        return PlanContent((Target(schema, table),), tuple(steps))

    # --- check: the request against the table's catalog -----------------------------------------------------------

    def check(self, content: PlanContent, catalogs: tuple[TargetCatalog, ...]) -> None:
        """Every named column is a column of the table, the primary key is shown, and every key is exactly it."""
        catalog = catalogs[0]
        known = set(catalog.columns)
        primary = set(catalog.primary_key)
        for step in content.steps:
            data = step.data
            named = set(data.get("values", {})) | set(data.get("old", {})) | set(data.get("new", {}))
            if not named <= known:
                raise _invalid()
            if "key" in data and (set(data["key"]) != primary or not primary <= set(data["old"])):
                raise _invalid()

    # --- run ------------------------------------------------------------------------------------------------------

    def _table(self, transaction: tabrion.Transaction, plan: Plan, step: Step, run: AuditedRun) -> sqlalchemy.Table:
        key = (run.run_id, step.target)
        with self._lock:
            table = self._tables.get(key)
        if table is not None:
            return table
        target = plan.content.targets[step.target]
        reflected = sqlalchemy.Table(
            target.table,
            sqlalchemy.MetaData(),
            schema=target.schema,
            autoload_with=transaction.connection,
            resolve_fks=False,
        )
        table = _bindable(reflected, plan.engine)
        with self._lock:
            self._tables[key] = table
            while len(self._tables) > _KEPT_TABLES:
                self._tables.popitem(last=False)
        return table

    def run_step(self, transaction: tabrion.Transaction, plan: Plan, step: Step, run: AuditedRun) -> None:
        table = self._table(transaction, plan, step, run)
        data = step.data

        def bound(values: Mapping[str, Any]) -> dict[str, Any]:
            return {name: bind_value(table.c[name], value, plan.engine) for name, value in values.items()}

        try:
            if step.action == "row.insert":
                statement: Any = insert_row(table, bound(data["values"]))
            elif step.action == "row.update":
                statement = update_row(table, bound(data["key"]), bound(data["old"]), bound(data["new"]))
            elif step.action == "row.delete":
                statement = delete_row(table, bound(data["key"]), bound(data["old"]))
            else:  # pragma: no cover - build makes no other action
                raise _invalid()
        except (KeyError, ValueError, TypeError, UsageError):
            # A column the reflection does not have (the drift check makes that unlikely), or a key that is not the
            # primary key: nothing ran yet; the run rolls back. The message names no value.
            raise WriteError("studio.data_editor_value_invalid") from None
        try:
            transaction.execute_checked(statement, expect_rowcount=1)
        except RowCountMismatch as exc:
            actual = exc.details.get("actual")
            conflict = actual == 0 and step.action != "row.insert"
            code = "studio.data_editor_conflict" if conflict else "studio.data_editor_rowcount"
            # The whole changeset rolls back: the transaction is failed, and the error leaves the block (03 5.2).
            logger.warning("data editor changeset rolled back", extra={"event": code})
            raise WriteError(code) from None
        run.count(_COUNTS[step.action], 1)


def _bindable(reflected: sqlalchemy.Table, engine: str) -> sqlalchemy.Table:
    """The table the statements are built on: the reflected columns, primary key and names.

    PostgreSQL keeps every reflected type (the driver binds each value in its column's type). SQLite keeps text
    columns as reflected (so ``tabrion.rowedit`` compares them under ``BINARY``) and leaves every other column
    untyped, so a value is bound as the driver reads it back (the viewer's value) and the column's affinity applies,
    never a SQLAlchemy conversion (a ``DATE`` column holds text, a ``NUMERIC`` one numbers). Every column is
    nullable here: the database checks ``NOT NULL`` and defaults; SQLAlchemy only builds the statement."""
    columns = [
        sqlalchemy.Column(
            column.name,
            column.type if engine != "sqlite" or _text(column.type) else sqlalchemy.types.NullType(),
            key=column.key,
            primary_key=column.primary_key,
            nullable=True,
        )
        for column in reflected.columns
    ]
    return sqlalchemy.Table(reflected.name, sqlalchemy.MetaData(), *columns, schema=reflected.schema)


def _text(kind: sqlalchemy.types.TypeEngine[Any]) -> bool:
    return isinstance(kind, sqlalchemy.String) and not isinstance(kind, sqlalchemy.Enum)


def _python_type(column: sqlalchemy.Column[Any]) -> type | None:
    try:
        found: type = column.type.python_type
    except NotImplementedError:
        return None
    return found


def bind_value(column: sqlalchemy.Column[Any], value: Any, engine: str) -> Any:
    """The bound value of a page value for ``column`` (see the module text). Raises :class:`WriteError`
    ``studio.data_editor_value_invalid`` for a value the column's type cannot take (the message names no value).

    The hook of STU-XDE-1's type editors: the same conversion for ``old`` (as the viewer showed it) and new values."""
    if value is None:
        return None
    if _is_bytes(value):
        return base64.b64decode(value["base64"], validate=True)
    if engine == "sqlite":
        return value  # untyped: the driver's own value (_bindable)
    kind = _python_type(column)
    try:
        if kind is bool:
            return _boolean(value)
        if isinstance(value, bool):
            return value if kind is None else _refuse()
        if kind is int:
            return int(value) if isinstance(value, str) else _number(value, int)
        if kind is float:
            return float(value) if isinstance(value, str | int | float) else _refuse()
        if kind is decimal.Decimal:
            return decimal.Decimal(str(value)) if isinstance(value, str | int | float) else _refuse()
        if isinstance(value, str):
            if kind is datetime.datetime:
                return datetime.datetime.fromisoformat(value)
            if kind is datetime.date:
                return datetime.date.fromisoformat(value)
            if kind is datetime.time:
                return datetime.time.fromisoformat(value)
            if kind is uuid.UUID:
                return uuid.UUID(value)
    except (ValueError, TypeError, ArithmeticError):
        raise WriteError("studio.data_editor_value_invalid") from None
    # Text, JSON, arrays, intervals, network addresses, enums, domains: the server reads the value in the column's
    # type (SQLAlchemy's psycopg dialect casts each parameter to it).
    return value


def _refuse() -> Any:
    raise ValueError("value")


def _number(value: Any, kind: type) -> Any:
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and kind is int):
        return int(value)
    return _refuse()


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "t", "1", "false", "f", "0"):
        return value.strip().lower() in ("true", "t", "1")
    return bool(_refuse())


# --- the page's table information --------------------------------------------------------------------------------


def table_info(unlocks: Unlocks, session: str, connection_id: object, schema: object, table: object) -> dict[str, Any]:
    """What the page builds its grid from: the table's columns and primary key, its triggers, rules and row security
    (TX-16), read on the unlock's write connection. Refuses what the executor refuses (a view, a partitioned table,
    a table without a primary key) before the user edits anything. Names only, never a value."""
    unlock = unlocks.require_unlock(session, connection_id, Area.DATA_EDITOR)
    engine = unlock.connection.engine
    target = Target(_name("main" if schema is None and engine == "sqlite" else schema), _name(table))
    if engine == "sqlite" and target.schema != "main":
        raise _invalid()
    _fingerprint, catalogs = read_catalog(unlock.connection.fetch, engine, (target,), unlock.connection.database_file)
    catalog = catalogs[0]
    if not catalog.primary_key:
        raise WriteError("studio.plan_target_no_primary_key")
    return {
        "connection_id": unlock.connection_id,
        "schema": target.schema,
        "table": target.table,
        "columns": list(catalog.columns),
        "primary_key": list(catalog.primary_key),
        "triggers": [trigger.to_dict() for trigger in catalog.triggers],
        "rules": [rule.to_dict() for rule in catalog.rules],
        "row_security": catalog.row_security,
        "max_changes": MAX_STEPS,
    }
