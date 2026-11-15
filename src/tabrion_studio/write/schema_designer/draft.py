"""The Schema Designer's draft and its edit operations (STU-CR-4; STU-ADR-0007 sections 2, 3 and 9; TX-21, TX-26,
TX-31; GS3-09, GS3-13).

A **draft** is the target model the user designs: a ``tabrion.schema`` level-2 :class:`SchemaSnapshot` of one schema
of the connected database, held only in this server process (:class:`Drafts`), for one session and one connection,
and bound to the unlock it was started under. It is never taken whole from the page, a file, a project folder, a URL
or a database, never written to disk, and it ends with the unlock (lock, expiry, reconnect, a changed profile) or
when the user discards it. ``MigrationPlan.from_json`` and ``SchemaSnapshot.from_json`` are never called here.

The page sends one **edit operation** at a time (:data:`OPERATIONS`): add, drop or rename a table or a column;
change a column's type, nullability or default; add or drop a primary key, unique, foreign key or check constraint;
add or drop an index. :func:`apply_edit` checks it against the draft (names, the objects it names, what still uses a
column or table, the engine's naming rules and the limits below) and builds the next model with tabrion's own shape
classes; then :func:`validate` asks tabrion to plan from the draft's starting snapshot to that model, with the
``server_version`` read on the write connection when the draft started (GS3-13), so a model tabrion cannot plan
(an unknown type, schema text that could leave its place, a change tabrion refuses) is refused at the edit, with a
fixed catalog problem (:func:`refusal`). A refused edit leaves the draft as it was. Studio builds no SQL: the plan
of the validation is thrown away, and the preview plans again from a fresh snapshot (:mod:`.designer`).

**A rename is not a rename in the plan.** tabrion matches tables and columns by name, so a renamed table or column is
planned as a drop and an add (its data is not carried over); the preview shows that, and the page says it.

**Limits (review info I-1 of STU-CR-1, STU-ADR-0007 Consequences).** :data:`MAX_TABLES` tables per draft (also the
most a draft can start from), :data:`MAX_COLUMNS` columns per table, :data:`MAX_TABLE_ELEMENTS` constraints and
indexes per table, :data:`MAX_KEY_COLUMNS` columns in one key or index, :data:`MAX_EDITS` edits per draft,
:data:`MAX_DRAFT_BYTES` of the model's canonical JSON, :data:`MAX_SQL_TEXT` characters of a default, check or
partial-index text, :data:`MAX_TYPE` characters of a type, :data:`MAX_NAME_BYTES` bytes (UTF-8) of a name,
:data:`MAX_EDIT_BODY` bytes of one edit request and :data:`MAX_DRAFTS` drafts in the process. A limit refuses with
``studio.schema_draft_limit``.

**Problems.** ``studio.schema_edit_invalid`` with ``edit.<field>`` issues and ``edit.*`` codes (never a value or a
name), ``studio.schema_draft_limit``, ``studio.schema_plan_refused`` (tabrion's ``PlanRefused``: ``plan.reason`` and,
for an unsupported feature, the feature's kind as ``plan.feature``) and ``studio.schema_model_invalid`` (tabrion's
``UsageError`` ``plan.*``, ``ddl.*`` and ``schema.*``: the code, never its details).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING, Any

from tabrion.errors import PlanRefused, UsageError
from tabrion.plan import plan
from tabrion.schema import (
    CheckShape,
    ColumnShape,
    ForeignKeyShape,
    IndexShape,
    SchemaSnapshot,
    TableShape,
    UniqueShape,
    fingerprint,
)

from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.schema_designer.create import _has_control

if TYPE_CHECKING:
    from tabrion_studio.write.schema_designer.execute import RunPlan

#: The limits of a draft (I-1). Each is far above a hand-designed schema and keeps one draft's memory and the
#: validation plan of every edit small.
MAX_TABLES = 500
MAX_COLUMNS = 250
MAX_TABLE_ELEMENTS = 100
MAX_KEY_COLUMNS = 32
MAX_EDITS = 500
MAX_DRAFT_BYTES = 2 * 1024 * 1024
MAX_SQL_TEXT = 4000
MAX_TYPE = 128
MAX_NAME_BYTES = 63
MAX_DRAFTS = 16
#: The body of one edit request: the default cap of every request body (``MAX_FORM_BODY``, 16 KiB); a larger table is
#: built in several edits.
MAX_EDIT_BODY = 16 * 1024

OPERATIONS = frozenset(
    {
        "add_table",
        "drop_table",
        "rename_table",
        "add_column",
        "drop_column",
        "rename_column",
        "alter_column",
        "add_constraint",
        "drop_constraint",
        "add_index",
        "drop_index",
    }
)
#: The keys each operation takes besides ``op``; any other key is refused (``edit.unknown_key``).
_KEYS: dict[str, frozenset[str]] = {
    "add_table": frozenset({"table", "columns", "primary_key"}),
    "drop_table": frozenset({"table"}),
    "rename_table": frozenset({"table", "new_name"}),
    "add_column": frozenset({"table", "column"}),
    "drop_column": frozenset({"table", "column"}),
    "rename_column": frozenset({"table", "column", "new_name"}),
    "alter_column": frozenset({"table", "column", "type", "nullable", "default"}),
    "add_constraint": frozenset({"table", "constraint"}),
    "drop_constraint": frozenset({"table", "kind", "position"}),
    "add_index": frozenset({"table", "index"}),
    "drop_index": frozenset({"table", "name"}),
}
_COLUMN_KEYS = frozenset({"name", "type", "nullable", "default"})
_CONSTRAINT_KEYS = frozenset(
    {"kind", "name", "columns", "referred_table", "referred_columns", "on_delete", "on_update", "expression"}
)
_INDEX_KEYS = frozenset({"name", "columns", "unique", "where"})
CONSTRAINT_KINDS = ("primary_key", "unique", "foreign_key", "check")
#: ``None`` is the engine default ``NO ACTION`` (tabrion ``ForeignKeyShape``).
REFERENTIAL_ACTIONS = frozenset({"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT"})
#: Schemas a draft never starts from (PostgreSQL's own).
_SYSTEM_SCHEMA = re.compile(r"pg_.*|information_schema", re.IGNORECASE | re.DOTALL)
#: An identifier-like token of schema text (a check, a partial-index predicate, an index expression, a generated
#: column): used only to refuse dropping or renaming a column such a text may name (fail closed), never to change it.
# A bare identifier follows the engines' lexer rule: an ASCII letter, ``_`` or any non-ASCII character first, then
# ASCII letters, digits, ``_``, ``$`` or any non-ASCII character (PostgreSQL takes every byte from 0x80 as an
# identifier character, SQLite every non-ASCII character); quoted, backtick and bracket names are taken as written
# (review P132-01).
_TOKEN = re.compile(
    r'"((?:[^"]|"")*)"|`([^`]*)`|\[([^\]]*)\]|((?:[A-Za-z_]|[^\x00-\x7f])(?:[A-Za-z0-9_$]|[^\x00-\x7f])*)'
)
# A PostgreSQL Unicode-escape name (``U&"d\0061ta"``) cannot be compared by its text: a text with one may name any
# column, so the check fails closed.
_UNICODE_ESCAPE = re.compile(r'(?<![\w$])[Uu]&"')
_ISSUE_TOKEN = re.compile(r"[a-z_]{1,48}")


def edit_invalid(field_name: str, code: str) -> WriteError:
    return WriteError("studio.schema_edit_invalid", [(f"edit.{field_name}", f"edit.{code}")])


def draft_limit(what: str) -> WriteError:
    return WriteError("studio.schema_draft_limit", [(f"edit.{what}", "edit.too_many")])


# --- tabrion's refusals ----------------------------------------------------------------------------------------------


def refusal(exc: PlanRefused | UsageError, locate: Callable[[str], str | None] | None = None) -> WriteError:
    """A fixed catalog problem for a refusal of tabrion (STU-ADR-0007 section 2, GS3-20): the reason or code, and
    for an unsupported feature the feature's kind (``rule``, ``partitioned``, ``view`` ...), never a name, a text or
    any other detail. With ``locate`` (the preview and the run, STU-CR-5, GS3-12) the table tabrion names is given by
    its position in what the page shows (``tables.<n>`` of the draft's tables, ``dropped.<n>`` of the dropped ones),
    so the page can name it while the problem itself carries no name."""
    if isinstance(exc, PlanRefused):
        issues: list[tuple[str, str]] = []
        reason = exc.details.get("reason")
        if isinstance(reason, str) and _ISSUE_TOKEN.fullmatch(reason):
            issues.append(("plan.reason", f"plan.{reason}"))
        feature = exc.details.get("feature")
        if isinstance(feature, str):
            kind = feature.partition(":")[0]
            if _ISSUE_TOKEN.fullmatch(kind):
                issues.append(("plan.feature", f"plan.{kind}"))
        table = exc.details.get("table")
        pointer = locate(table) if locate is not None and isinstance(table, str) else None
        if pointer is not None and issues:
            issues.append((pointer, issues[0][1]))
        return WriteError("studio.schema_plan_refused", issues)
    family, _, name = exc.code.partition(".")
    if family in ("plan", "ddl", "schema") and _ISSUE_TOKEN.fullmatch(name):
        return WriteError("studio.schema_model_invalid", [("plan.code", f"{family}.{name}")])
    return WriteError("studio.schema_model_invalid")


def locator(model: SchemaSnapshot, base: SchemaSnapshot) -> Callable[[str], str | None]:
    """Where the page shows a table tabrion names in a refusal: ``tables.<n>`` (the model's tables, in its order) or
    ``dropped.<n>`` (the tables of the draft's start that the model drops, in name order: the draft view's
    ``dropped``), so a problem names no table (GS3-12). A table the page does not show gets no pointer."""
    names = [table.name for table in model.tables]
    dropped = sorted({table.name for table in base.tables} - set(names))

    def locate(table: str) -> str | None:
        if table in names:
            return f"tables.{names.index(table)}"
        if table in dropped:
            return f"dropped.{dropped.index(table)}"
        return None

    return locate


def plan_for(
    current: SchemaSnapshot,
    target: SchemaSnapshot,
    server_version: tuple[int, ...],
    locate: Callable[[str], str | None] | None = None,
) -> Any:
    """``tabrion.plan.plan`` with ``server_version`` always passed (GS3-13, never None) and direct-execution options
    (``concurrent_indexes=False``); tabrion's refusals become catalog problems (``locate``: see :func:`refusal`).
    tabrion's own checks of every default, check and predicate text (one complete expression, the statement-head
    check) run in each call, so also in the re-plan before a run and in the executor's plan inside its transaction."""
    if not isinstance(server_version, tuple) or not server_version:
        raise WriteError("studio.write_unsupported")  # pragma: no cover - the draft always has one
    try:
        return plan(current, target, server_version=server_version, concurrent_indexes=False)
    except (PlanRefused, UsageError) as exc:
        raise refusal(exc, locate) from None


# --- the draft and its store -----------------------------------------------------------------------------------------


@dataclass
class Draft:
    """One draft: the starting snapshot (``base``), the model the user designs (``model``) and what it is bound to.
    ``revision`` counts the accepted edits; an edit names the revision it was made on, so an edit from a page that
    did not see the latest state is refused (``studio.schema_draft_stale``)."""

    session: str
    connection_id: str
    unlock_id: str
    engine: str
    schema: str
    base: SchemaSnapshot
    model: SchemaSnapshot
    server_version: tuple[int, ...]
    revision: int = 0
    edits: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: The plan of the last preview (STU-CR-5), until it is run once, a new preview or an edit replaces it, or the
    #: draft ends; never sent whole to the page.
    plan: RunPlan | None = field(default=None, repr=False)

    def qualified(self, name: str) -> str:
        return f"{self.schema}.{name}"


class Drafts:
    """The drafts of this process, by session and connection; each bound to its unlock. Thread-safe.

    ``project`` is the project folder (STU-CR-5: an ``alembic.ini`` there means revisions only) and ``write_state``
    the folder of ``write-areas.toml`` (the backups go below it); None refuses a run that needs them."""

    def __init__(self, project: Path | None = None, write_state: Path | None = None) -> None:
        self._drafts: dict[tuple[str, str], Draft] = {}
        self._lock = threading.Lock()
        self.project = project
        self.write_state = write_state

    def put(self, draft: Draft) -> None:
        key = (draft.session, draft.connection_id)
        with self._lock:
            if key not in self._drafts and len(self._drafts) >= MAX_DRAFTS:
                raise draft_limit("drafts")
            self._drafts[key] = draft

    def get(self, session: str, connection_id: str, unlock_id: str) -> Draft:
        """The draft of this session and connection made under ``unlock_id``; a draft of an earlier unlock is
        dropped (``studio.schema_draft_missing``)."""
        key = (session, connection_id)
        with self._lock:
            draft = self._drafts.get(key)
            if draft is not None and draft.unlock_id != unlock_id:
                del self._drafts[key]
                draft = None
        if draft is None:
            raise WriteError("studio.schema_draft_missing")
        return draft

    def discard(self, session: str, connection_id: str) -> bool:
        with self._lock:
            return self._drafts.pop((session, connection_id), None) is not None

    def unlock_ended(self, unlock_id: str) -> None:
        """:attr:`Unlocks.end_listeners`: the drafts of an unlock that ended go with it."""
        with self._lock:
            for key in [key for key, draft in self._drafts.items() if draft.unlock_id == unlock_id]:
                del self._drafts[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._drafts)


# --- checks of the request's parts -----------------------------------------------------------------------------------


def check_name(raw: object, field_name: str) -> str:
    """A table, column, constraint or index name: a non-empty string of at most :data:`MAX_NAME_BYTES` UTF-8 bytes,
    no control, format or unusual space character, no blank at either end and no dot (tabrion's ``schema.table`` is
    unambiguous only without one)."""
    if raw is None or raw == "":
        raise edit_invalid(field_name, "required")
    if not isinstance(raw, str):
        raise edit_invalid(field_name, "type")
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_NAME_BYTES:
        raise edit_invalid(field_name, "too_long")
    if _has_control(raw):
        raise edit_invalid(field_name, "control_character")
    if raw != raw.strip(" "):
        raise edit_invalid(field_name, "whitespace")
    if "." in raw:
        raise edit_invalid(field_name, "dot")
    return raw


def check_schema(raw: object, engine: str) -> str:
    """The schema a draft starts from: ``main`` on SQLite (no attached database), on PostgreSQL a valid name that is
    not one of PostgreSQL's own schemas (the caller checks that it exists)."""
    if raw is None or raw == "":
        return "main" if engine == "sqlite" else "public"
    name = check_name(raw, "schema")
    if engine == "sqlite" and name != "main":
        raise WriteError("studio.schema_draft_schema")
    if engine != "sqlite" and _SYSTEM_SCHEMA.fullmatch(name):
        raise WriteError("studio.schema_draft_schema")
    return name


def _text(raw: object, field_name: str, limit: int, *, optional: bool) -> str | None:
    if raw is None and optional:
        return None
    if raw is None or raw == "":
        raise edit_invalid(field_name, "required")
    if not isinstance(raw, str):
        raise edit_invalid(field_name, "type")
    if len(raw) > limit:
        raise edit_invalid(field_name, "too_long")
    if "\x00" in raw:
        raise edit_invalid(field_name, "control_character")
    return raw


def _type(raw: object, field_name: str) -> str:
    text = _text(raw, field_name, MAX_TYPE, optional=False)
    assert text is not None  # noqa: S101 - not optional
    if _has_control(text) or text != text.strip():
        raise edit_invalid(field_name, "control_character")
    return text


def _bool(raw: object, field_name: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise edit_invalid(field_name, "type")
    return raw


def _dict(raw: object, field_name: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise edit_invalid(field_name, "type")
    if set(raw) - keys:
        raise edit_invalid(field_name, "unknown_key")
    return raw


def _names(raw: object, field_name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(raw, list):
        raise edit_invalid(field_name, "type")
    if not raw and not allow_empty:
        raise edit_invalid(field_name, "required")
    if len(raw) > MAX_KEY_COLUMNS:
        raise draft_limit(field_name)
    return [check_name(item, field_name) for item in raw]


# --- helpers over the model ------------------------------------------------------------------------------------------


class _Model:
    """The draft's tables by name while an edit is applied; names compared as the engine compares them (SQLite
    ignores case for ASCII letters, PostgreSQL compares quoted names exactly)."""

    def __init__(self, draft: Draft) -> None:
        self.draft = draft
        self.sqlite = draft.engine == "sqlite"
        self.tables: dict[str, TableShape] = {table.name: table for table in draft.model.tables}

    def same(self, left: str, right: str) -> bool:
        return left.casefold() == right.casefold() if self.sqlite else left == right

    def find_table(self, name: str) -> str | None:
        qualified = self.draft.qualified(name)
        return next((key for key in self.tables if self.same(key, qualified)), None)

    def table(self, raw: object, field_name: str = "table") -> TableShape:
        found = self.find_table(check_name(raw, field_name))
        if found is None:
            raise edit_invalid(field_name, "table_missing")
        return self.tables[found]

    def column_index(self, table: TableShape, name: str) -> int | None:
        return next((i for i, column in enumerate(table.columns) if self.same(column.name, name)), None)

    def column(self, table: TableShape, raw: object, field_name: str = "column") -> ColumnShape:
        index = self.column_index(table, check_name(raw, field_name))
        if index is None:
            raise edit_invalid(field_name, "column_missing")
        return table.columns[index]

    def columns_exist(self, table: TableShape, names: list[str], field_name: str) -> tuple[str, ...]:
        out: list[str] = []
        for name in names:
            index = self.column_index(table, name)
            if index is None:
                raise edit_invalid(field_name, "column_missing")
            stored = table.columns[index].name
            if any(self.same(stored, seen) for seen in out):
                raise edit_invalid(field_name, "duplicate")
            out.append(stored)
        return tuple(out)

    def index_names(self) -> list[str]:
        return [index.name for table in self.tables.values() for index in table.indexes]

    def referrers(self, qualified: str) -> list[tuple[TableShape, ForeignKeyShape]]:
        return [
            (table, key)
            for table in self.tables.values()
            for key in table.foreign_keys
            if self.same(key.referred_table, qualified)
        ]

    def put(self, table: TableShape) -> None:
        self.tables[table.name] = table

    def snapshot(self) -> SchemaSnapshot:
        tables = tuple(self.tables[name] for name in sorted(self.tables))
        return replace(self.draft.model, tables=tables)


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    out: set[str] = set()
    for match in _TOKEN.finditer(text):
        quoted, backtick, bracket, bare = match.groups()
        token = quoted.replace('""', '"') if quoted is not None else (backtick or bracket or bare or "")
        out.add(token.casefold())
    return out


def _texts_naming(table: TableShape, column: str) -> bool:
    """Whether a check, a partial-index predicate, an index expression or a generated column's expression of the
    table may name ``column`` (a token match, so a refusal can be too careful but never misses a plain name)."""
    wanted = column.casefold()
    texts: list[str | None] = [check.sqltext for check in table.check_constraints]
    for index in table.indexes:
        texts.append(index.where)
        texts.extend(index.columns[position] for position in index.expressions if position < len(index.columns))
    texts.extend(c.generation_expression for c in table.columns)
    return any(text_may_name(text, wanted) for text in texts)


def text_may_name(text: str | None, wanted: str) -> bool:
    """Whether ``text`` may name the column ``wanted`` (case-folded): a token match, or any column at all when the text
    holds a Unicode-escape name it cannot read (fails closed)."""
    if text and _UNICODE_ESCAPE.search(text):
        return True
    return wanted in _tokens(text)


def _uses_column(model: _Model, table: TableShape, column: str) -> bool:
    plain = [
        *table.primary_key,
        *(name for unique in table.unique_constraints for name in (*unique.columns, *unique.include)),
        *(name for key in table.foreign_keys for name in key.columns),
        *(
            name
            for index in table.indexes
            for position, name in enumerate(index.columns)
            if position not in index.expressions
        ),
        *(name for index in table.indexes for name in index.include),
    ]
    if any(model.same(name, column) for name in plain):
        return True
    return any(
        any(model.same(name, column) for name in key.referred_columns) for _t, key in model.referrers(table.name)
    )


def _rename_in(names: tuple[str, ...], old: str, new: str, same: Callable[[str, str], bool]) -> tuple[str, ...]:
    return tuple(new if same(name, old) else name for name in names)


# --- the operations --------------------------------------------------------------------------------------------------


def _column_shape(raw: object, field_name: str) -> ColumnShape:
    doc = _dict(raw, field_name, _COLUMN_KEYS)
    return ColumnShape(
        name=check_name(doc.get("name"), f"{field_name}_name"),
        type=_type(doc.get("type"), f"{field_name}_type"),
        nullable=_bool(doc.get("nullable"), f"{field_name}_nullable", True),
        default=_text(doc.get("default"), f"{field_name}_default", MAX_SQL_TEXT, optional=True),
    )


def _add_table(model: _Model, edit: Mapping[str, Any]) -> None:
    name = check_name(edit.get("table"), "table")
    if model.find_table(name) is not None:
        raise edit_invalid("table", "table_exists")
    raw_columns = edit.get("columns")
    if not isinstance(raw_columns, list):
        raise edit_invalid("columns", "type")
    if not raw_columns:
        raise edit_invalid("columns", "required")
    if len(raw_columns) > MAX_COLUMNS:
        raise draft_limit("columns")
    columns: list[ColumnShape] = []
    for raw in raw_columns:
        column = _column_shape(raw, "column")
        if any(model.same(column.name, other.name) for other in columns):
            raise edit_invalid("column_name", "column_exists")
        columns.append(column)
    table = TableShape(name=model.draft.qualified(name), columns=tuple(columns), primary_key=())
    key = edit.get("primary_key")
    if key is not None and key != []:
        table = replace(table, primary_key=model.columns_exist(table, _names(key, "primary_key"), "primary_key"))
    model.put(table)


def _drop_table(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    if any(other.name != table.name for other, _key in model.referrers(table.name)):
        raise edit_invalid("table", "table_referenced")
    del model.tables[table.name]


def _rename_table(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    new = check_name(edit.get("new_name"), "new_name")
    found = model.find_table(new)
    if found is not None and found != table.name:
        raise edit_invalid("new_name", "table_exists")
    target = model.draft.qualified(new)
    if target == table.name:
        raise edit_invalid("new_name", "no_change")
    del model.tables[table.name]
    for name, other in list(model.tables.items()):
        keys = tuple(
            replace(key, referred_table=target) if model.same(key.referred_table, table.name) else key
            for key in other.foreign_keys
        )
        if keys != other.foreign_keys:
            model.tables[name] = replace(other, foreign_keys=keys)
    keys = tuple(
        replace(key, referred_table=target) if model.same(key.referred_table, table.name) else key
        for key in table.foreign_keys
    )
    model.put(replace(table, name=target, foreign_keys=keys))


def _add_column(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    column = _column_shape(edit.get("column"), "column")
    if model.column_index(table, column.name) is not None:
        raise edit_invalid("column_name", "column_exists")
    if len(table.columns) >= MAX_COLUMNS:
        raise draft_limit("columns")
    model.put(replace(table, columns=(*table.columns, column)))


def _drop_column(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    column = model.column(table, edit.get("column"))
    if len(table.columns) == 1:
        raise edit_invalid("column", "last_column")
    if _uses_column(model, table, column.name):
        raise edit_invalid("column", "column_in_use")
    if _texts_naming(table, column.name):
        raise edit_invalid("column", "column_named_in_text")
    model.put(replace(table, columns=tuple(c for c in table.columns if c.name != column.name)))


def _rename_column(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    column = model.column(table, edit.get("column"))
    new = check_name(edit.get("new_name"), "new_name")
    if new == column.name:
        raise edit_invalid("new_name", "no_change")
    found = model.column_index(table, new)
    if found is not None and table.columns[found].name != column.name:
        raise edit_invalid("new_name", "column_exists")
    if _texts_naming(table, column.name):
        raise edit_invalid("column", "column_named_in_text")
    old = column.name
    same = model.same

    def rename(names: tuple[str, ...]) -> tuple[str, ...]:
        return _rename_in(names, old, new, same)

    renamed = replace(
        table,
        columns=tuple(replace(c, name=new) if c.name == old else c for c in table.columns),
        primary_key=rename(table.primary_key),
        unique_constraints=tuple(
            replace(u, columns=rename(u.columns), include=rename(u.include)) for u in table.unique_constraints
        ),
        foreign_keys=tuple(replace(k, columns=rename(k.columns)) for k in table.foreign_keys),
        indexes=tuple(
            replace(
                index,
                columns=tuple(
                    name if position in index.expressions else (new if same(name, old) else name)
                    for position, name in enumerate(index.columns)
                ),
                include=rename(index.include),
            )
            for index in table.indexes
        ),
    )
    model.put(renamed)
    for name, other in list(model.tables.items()):
        keys = tuple(
            replace(key, referred_columns=rename(key.referred_columns)) if same(key.referred_table, table.name) else key
            for key in other.foreign_keys
        )
        if keys != other.foreign_keys:
            model.tables[name] = replace(other, foreign_keys=keys)


def _alter_column(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    column = model.column(table, edit.get("column"))
    if not any(key in edit for key in ("type", "nullable", "default")):
        raise edit_invalid("column", "no_change")
    changed = column
    if "type" in edit:
        changed = replace(changed, type=_type(edit.get("type"), "type"))
    if "nullable" in edit:
        if not isinstance(edit.get("nullable"), bool):
            raise edit_invalid("nullable", "type")
        changed = replace(changed, nullable=edit["nullable"])
    if "default" in edit:
        changed = replace(changed, default=_text(edit.get("default"), "default", MAX_SQL_TEXT, optional=True))
    if changed == column:
        raise edit_invalid("column", "no_change")
    model.put(replace(table, columns=tuple(changed if c.name == column.name else c for c in table.columns)))


def _constraint_name(model: _Model, raw: object, *, sqlite_allowed: bool) -> str | None:
    """PostgreSQL: a constraint needs a name (an unnamed one gets the server's name, which the model would not
    know). SQLite keeps no name of a unique constraint (tabrion ``UniqueShape``), so none may be given there."""
    if raw is None or raw == "":
        if model.sqlite:
            return None
        raise edit_invalid("constraint_name", "name_required")
    if model.sqlite and not sqlite_allowed:
        raise edit_invalid("constraint_name", "name_not_supported")
    return check_name(raw, "constraint_name")


def _element_names(table: TableShape) -> list[str]:
    return [
        name
        for name in (
            *(u.name for u in table.unique_constraints),
            *(k.name for k in table.foreign_keys),
            *(c.name for c in table.check_constraints),
            *(e.name for e in table.exclusion_constraints),
        )
        if name is not None
    ]


def _elements(table: TableShape) -> int:
    return (
        len(table.unique_constraints)
        + len(table.foreign_keys)
        + len(table.check_constraints)
        + len(table.exclusion_constraints)
        + len(table.indexes)
        + (1 if table.primary_key else 0)
    )


def _action(raw: object, field_name: str) -> str | None:
    if raw is None or raw == "" or raw == "NO ACTION":
        return None
    if not isinstance(raw, str) or raw not in REFERENTIAL_ACTIONS:
        raise edit_invalid(field_name, "choice")
    return raw


def _add_constraint(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    doc = _dict(edit.get("constraint"), "constraint", _CONSTRAINT_KEYS)
    kind = doc.get("kind")
    if kind not in CONSTRAINT_KINDS:
        raise edit_invalid("constraint_kind", "choice")
    allowed = {
        "primary_key": {"kind", "columns"},
        "unique": {"kind", "name", "columns"},
        "foreign_key": {"kind", "name", "columns", "referred_table", "referred_columns", "on_delete", "on_update"},
        "check": {"kind", "name", "expression"},
    }[kind]
    if set(doc) - allowed:
        raise edit_invalid("constraint", "unknown_key")
    if _elements(table) >= MAX_TABLE_ELEMENTS:
        raise draft_limit("elements")
    name: str | None = None
    if kind != "primary_key":
        name = _constraint_name(model, doc.get("name"), sqlite_allowed=kind != "unique")
        if name is not None and any(model.same(name, other) for other in _element_names(table)):
            raise edit_invalid("constraint_name", "name_exists")
    if kind == "primary_key":
        if table.primary_key:
            raise edit_invalid("constraint_kind", "primary_key_exists")
        columns = model.columns_exist(table, _names(doc.get("columns"), "constraint_columns"), "constraint_columns")
        model.put(replace(table, primary_key=columns))
    elif kind == "unique":
        columns = model.columns_exist(table, _names(doc.get("columns"), "constraint_columns"), "constraint_columns")
        unique = UniqueShape(name=name, columns=columns)
        model.put(replace(table, unique_constraints=(*table.unique_constraints, unique)))
    elif kind == "foreign_key":
        columns = model.columns_exist(table, _names(doc.get("columns"), "constraint_columns"), "constraint_columns")
        referred = model.table(doc.get("referred_table"), "referred_table")
        if referred.name == table.name:
            referred = table
        referred_columns = model.columns_exist(
            referred, _names(doc.get("referred_columns"), "referred_columns"), "referred_columns"
        )
        if len(referred_columns) != len(columns):
            raise edit_invalid("referred_columns", "count_mismatch")
        key = ForeignKeyShape(
            name=name,
            columns=columns,
            referred_table=referred.name,
            referred_columns=referred_columns,
            on_delete=_action(doc.get("on_delete"), "on_delete"),
            on_update=_action(doc.get("on_update"), "on_update"),
        )
        model.put(replace(table, foreign_keys=(*table.foreign_keys, key)))
    else:
        expression = _text(doc.get("expression"), "expression", MAX_SQL_TEXT, optional=False)
        assert expression is not None  # noqa: S101 - not optional
        check = CheckShape(name=name, sqltext=expression)
        model.put(replace(table, check_constraints=(*table.check_constraints, check)))


def _position(raw: object, size: int) -> int:
    if type(raw) is not int:
        raise edit_invalid("position", "type")
    if not 0 <= raw < size:
        raise edit_invalid("position", "constraint_missing")
    return raw


def _drop_constraint(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    kind = edit.get("kind")
    if kind not in CONSTRAINT_KINDS:
        raise edit_invalid("kind", "choice")
    if kind == "primary_key":
        if edit.get("position") is not None:
            raise edit_invalid("position", "type")
        if not table.primary_key:
            raise edit_invalid("kind", "constraint_missing")
        if any(other.name != table.name for other, _key in model.referrers(table.name)):
            raise edit_invalid("kind", "table_referenced")
        model.put(replace(table, primary_key=()))
    elif kind == "unique":
        at = _position(edit.get("position"), len(table.unique_constraints))
        kept = tuple(u for i, u in enumerate(table.unique_constraints) if i != at)
        model.put(replace(table, unique_constraints=kept))
    elif kind == "foreign_key":
        at = _position(edit.get("position"), len(table.foreign_keys))
        model.put(replace(table, foreign_keys=tuple(k for i, k in enumerate(table.foreign_keys) if i != at)))
    else:
        at = _position(edit.get("position"), len(table.check_constraints))
        kept_checks = tuple(c for i, c in enumerate(table.check_constraints) if i != at)
        model.put(replace(table, check_constraints=kept_checks))


def _add_index(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    doc = _dict(edit.get("index"), "index", _INDEX_KEYS)
    name = check_name(doc.get("name"), "index_name")
    # Index names are per schema on PostgreSQL and per database on SQLite.
    if any(model.same(name, other) for other in model.index_names()) or any(
        model.same(name, other) for other in _element_names(table)
    ):
        raise edit_invalid("index_name", "name_exists")
    if _elements(table) >= MAX_TABLE_ELEMENTS:
        raise draft_limit("elements")
    columns = model.columns_exist(table, _names(doc.get("columns"), "index_columns"), "index_columns")
    index = IndexShape(
        name=name,
        columns=columns,
        unique=_bool(doc.get("unique"), "index_unique", False),
        where=_text(doc.get("where"), "index_where", MAX_SQL_TEXT, optional=True) or None,
    )
    model.put(replace(table, indexes=(*table.indexes, index)))


def _drop_index(model: _Model, edit: Mapping[str, Any]) -> None:
    table = model.table(edit.get("table"))
    name = check_name(edit.get("name"), "name")
    kept = tuple(index for index in table.indexes if not model.same(index.name, name))
    if len(kept) == len(table.indexes):
        raise edit_invalid("name", "index_missing")
    model.put(replace(table, indexes=kept))


_APPLY: dict[str, Callable[[_Model, Mapping[str, Any]], None]] = {
    "add_table": _add_table,
    "drop_table": _drop_table,
    "rename_table": _rename_table,
    "add_column": _add_column,
    "drop_column": _drop_column,
    "rename_column": _rename_column,
    "alter_column": _alter_column,
    "add_constraint": _add_constraint,
    "drop_constraint": _drop_constraint,
    "add_index": _add_index,
    "drop_index": _drop_index,
}


def check_limits(model: SchemaSnapshot) -> None:
    """The size limits of a model (I-1): tables, columns and elements per table, the canonical JSON's size."""
    if len(model.tables) > MAX_TABLES:
        raise draft_limit("tables")
    for table in model.tables:
        if len(table.columns) > MAX_COLUMNS:
            raise draft_limit("columns")
        if _elements(table) > MAX_TABLE_ELEMENTS:
            raise draft_limit("elements")
    if len(model.to_json()) > MAX_DRAFT_BYTES:
        raise draft_limit("size")


def apply_edit(draft: Draft, edit: object) -> SchemaSnapshot:
    """The model after ``edit``, checked (see the module text); the draft itself is not changed. Raises
    :class:`WriteError`."""
    if not isinstance(edit, dict):
        raise edit_invalid("op", "type")
    op = edit.get("op")
    if not isinstance(op, str) or op not in OPERATIONS:
        raise edit_invalid("op", "choice")
    if set(edit) - _KEYS[op] - {"op"}:
        raise edit_invalid("op", "unknown_key")
    if draft.edits >= MAX_EDITS:
        raise draft_limit("edits")
    model = _Model(draft)
    _APPLY[op](model, edit)
    try:
        result = model.snapshot()
    except UsageError as exc:
        raise refusal(exc) from None
    check_limits(result)
    return result


def validate(draft: Draft, model: SchemaSnapshot) -> int:
    """tabrion plans from the draft's starting snapshot to ``model`` (``server_version`` passed, GS3-13); a model it
    cannot plan is refused with its catalog problem. The plan is not kept: the number of its steps is returned."""
    return len(plan_for(draft.base, model, draft.server_version, locator(model, draft.base)).steps)


# --- what the page sees ----------------------------------------------------------------------------------------------


def _bare(draft: Draft, qualified: str) -> str:
    prefix = f"{draft.schema}."
    return qualified[len(prefix) :] if qualified.startswith(prefix) else qualified


def _table_view(draft: Draft, table: TableShape, base: TableShape | None) -> dict[str, Any]:
    status = "new" if base is None else ("unchanged" if base == table else "changed")
    return {
        "name": _bare(draft, table.name),
        "status": status,
        "columns": [
            {
                "name": c.name,
                "type": c.type,
                "nullable": c.nullable,
                "default": c.default,
                "identity": c.identity,
                "generated": c.generated,
            }
            for c in table.columns
        ],
        "primary_key": list(table.primary_key),
        "unique": [{"name": u.name, "columns": list(u.columns)} for u in table.unique_constraints],
        "foreign_keys": [
            {
                "name": k.name,
                "columns": list(k.columns),
                "referred_table": _bare(draft, k.referred_table),
                "referred_columns": list(k.referred_columns),
                "on_delete": k.on_delete,
                "on_update": k.on_update,
            }
            for k in table.foreign_keys
        ],
        "checks": [{"name": c.name, "expression": c.sqltext} for c in table.check_constraints],
        "indexes": [
            {
                "name": i.name,
                "columns": list(i.columns),
                "unique": i.unique,
                "where": i.where,
                "expressions": list(i.expressions),
            }
            for i in table.indexes
        ],
        # What tabrion's plan refuses to change (ADR 0015 D-5) or what a rebuild must keep: shown, never edited here.
        "triggers": len(table.triggers),
        "policies": len(table.policies),
        "features": sorted({feature.partition(":")[0] for feature in table.features}),
    }


def draft_view(draft: Draft) -> dict[str, Any]:
    """The draft as the page shows it: its tables with their columns, keys, constraints and indexes, each marked
    new, changed or unchanged against the starting snapshot, and the tables the draft drops. Schema text (defaults,
    checks, predicates) is the user's own design or the database's schema, shown only to this session; never logged."""
    base = {table.name: table for table in draft.base.tables}
    names = {table.name for table in draft.model.tables}
    return {
        "connection_id": draft.connection_id,
        "engine": draft.engine,
        "schema": draft.schema,
        "revision": draft.revision,
        "edits": draft.edits,
        "base_fingerprint": fingerprint(draft.base),
        "draft_fingerprint": fingerprint(draft.model),
        "changed": fingerprint(draft.base) != fingerprint(draft.model),
        "tables": [_table_view(draft, table, base.get(table.name)) for table in draft.model.tables],
        "dropped": sorted(_bare(draft, name) for name in base if name not in names),
        "limits": {
            "tables": MAX_TABLES,
            "columns": MAX_COLUMNS,
            "elements": MAX_TABLE_ELEMENTS,
            "edits": MAX_EDITS,
        },
    }


def table_names(tables: Iterable[str]) -> list[str]:
    """The names of a schema's tables, refused above :data:`MAX_TABLES` before any table is read in full."""
    names = list(tables)
    if len(names) > MAX_TABLES:
        raise draft_limit("tables")
    return names
