"""Vector data inspection (STU-055, ``docs/architecture/vector-inspection.md``): read-only facts about vector columns,
vector indexes, embedding dimensions and model identities, stored embeddings and pgvector / sqlite-vec.

Everything here reads, on the connection's read-only path (``OpenConnection.fetch``: the statement check, one
``Database.read()`` per statement, the fixed catalog ``search_path`` on PostgreSQL) and always through
``ConnectionRegistry.get``. The statements are fixed texts with bound values; only a counting statement names a
table, and then the name of a table the catalog listed, as a quoted identifier. No statement scans a table without a
``LIMIT`` (:data:`EXACT_COUNT_MAX`), every count runs under a timer, and on PostgreSQL a table whose estimate is
past the bound is not counted at all.

Nothing is embedded, indexed, searched or changed, and no extension is loaded. Orkexa's tables are recognized by
their documented structure (schema version 1 of its PostgreSQL backend: ``<prefix>vector_spaces`` and
``<prefix>chunk_vectors``; its SQLite index: ``index_spaces``, ``index_vectors``, ``index_meta`` and the older
``index_vec`` ``vec0`` table); Studio does not import Orkexa.

**ANN or exact (the rule of the page):** a column is ``ann`` only when an index on it uses an approximate method
(:data:`ANN_METHODS`) and is valid, ready and live. Everything else is ``exact``: no such index, an invalid one,
Orkexa's ``real[]`` and BLOB vectors, and sqlite-vec's ``vec0`` tables (a full scan of their chunks).

Values of the database (names, the identity's model name) go to the page as data, which renders them as text. Nothing
of this is logged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import re
import threading
import time
from typing import Any

from tabrion import TabrionError
from tabrion.errors import Kind

from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.definitions import COUNT_TIMEOUT_S
from tabrion_studio.services.queries import CANCEL_ATTEMPTS, CANCEL_INTERVAL_S
from tabrion_studio.services.tabrion_compat import Cancel

__all__ = ["ANN_METHODS", "EXACT_COUNT_MAX", "MAX_COLUMNS", "MAX_COUNTS", "MAX_IDENTITIES", "VectorInspection"]

#: A count reads at most this many rows plus one; past it the answer is "more than" (and on PostgreSQL a table whose
#: estimate is larger is not counted, its estimate is shown).
EXACT_COUNT_MAX = 100_000
#: At most this many counts per inspection; the rest are skipped with a hint.
MAX_COUNTS = 50
#: At most this many vector columns (and identities) per answer.
MAX_COLUMNS = 200
MAX_IDENTITIES = 200
#: Index access methods that answer a nearest-neighbor query approximately (pgvector, pgvectorscale).
ANN_METHODS = frozenset({"hnsw", "ivfflat", "diskann"})
_PGVECTOR_EXTENSION = "vector"
_STATES = {"active": "installed", "other_schema": "other_schema", "installable": "available", "unavailable": "missing"}

#: pgvector's types, by name and by membership of the extension ``vector`` (a type of that name from elsewhere is not
#: one); the dimensions from the type modifier (a domain's own), -1 when not declared.
_PG_COLUMNS_SQL = (
    "SELECT n.nspname, c.relname, c.relkind, a.attname, t.typname, "
    "pg_catalog.format_type(a.atttypid, a.atttypmod), "
    "CASE WHEN t0.typtype = 'd' THEN t0.typtypmod ELSE a.atttypmod END, "
    "c.reltuples::pg_catalog.int8, pg_catalog.has_table_privilege(c.oid, 'SELECT') "
    "FROM pg_catalog.pg_attribute a "
    "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_type t0 ON t0.oid = a.atttypid "
    "JOIN pg_catalog.pg_type t ON t.oid = CASE WHEN t0.typtype = 'd' THEN t0.typbasetype ELSE t0.oid END "
    "WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'm', 'f') AND NOT c.relispartition "
    "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
    "AND t.typname IN ('vector', 'halfvec', 'sparsevec') "
    "AND EXISTS (SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid = d.refobjid "
    "WHERE d.classid = 'pg_catalog.pg_type'::pg_catalog.regclass AND d.objid = t.oid AND d.deptype = 'e' "
    "AND d.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass AND e.extname = 'vector') "
    "ORDER BY n.nspname, c.relname, a.attnum "
    "LIMIT 201"  # MAX_COLUMNS + 1
)
#: Tables that may be Orkexa's (``<prefix>vector_spaces``, ``<prefix>chunk_vectors``), with their columns.
_PG_ORKEXA_SQL = (
    "SELECT n.nspname, c.relname, c.reltuples::pg_catalog.int8, pg_catalog.has_table_privilege(c.oid, 'SELECT'), "
    "ARRAY(SELECT a.attname::pg_catalog.text FROM pg_catalog.pg_attribute a "
    "WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum), "
    "ARRAY(SELECT pg_catalog.format_type(a.atttypid, a.atttypmod) FROM pg_catalog.pg_attribute a "
    "WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum) "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
    "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
    "AND (c.relname LIKE '%vector!_spaces' ESCAPE '!' OR c.relname LIKE '%chunk!_vectors' ESCAPE '!') "
    "ORDER BY n.nspname, c.relname LIMIT 200"
)
#: The indexes of one table, with the columns each depends on (plain and expression indexes alike).
_PG_INDEXES_SQL = (
    "SELECT ic.relname, am.amname, i.indisvalid, i.indisready, i.indislive, "
    "pg_catalog.pg_get_indexdef(i.indexrelid), "
    "ARRAY(SELECT o.opcname::pg_catalog.text FROM pg_catalog.unnest(i.indclass::pg_catalog.oid[]) "
    "WITH ORDINALITY k(opc, n) JOIN pg_catalog.pg_opclass o ON o.oid = k.opc ORDER BY k.n), "
    "ARRAY(SELECT a.attname::pg_catalog.text FROM pg_catalog.pg_attribute a WHERE a.attrelid = i.indrelid "
    "AND a.attnum > 0 AND EXISTS (SELECT 1 FROM pg_catalog.pg_depend d "
    "WHERE d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass AND d.objid = i.indexrelid "
    "AND d.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass AND d.refobjid = i.indrelid "
    "AND d.refobjsubid = a.attnum)), "
    "ic.reloptions, pg_catalog.pg_relation_size(i.indexrelid) "
    "FROM pg_catalog.pg_index i "
    "JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid "
    "JOIN pg_catalog.pg_am am ON am.oid = ic.relam "
    "WHERE i.indrelid = (SELECT c.oid FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema AND c.relname = :name) "
    "ORDER BY ic.relname LIMIT 200"
)
_ORKEXA_PG_SPACES = {
    "tenant_id": "text",
    "namespace": "text",
    "space": "text",
    "identity": "jsonb",
    "dimensions": "integer",
    "state": "text",
}
_ORKEXA_PG_VECTORS = {
    "tenant_id": "text",
    "namespace": "text",
    "space": "text",
    "chunk_id": "bigint",
    "embedding": "real[]",
}
_ORKEXA_SQLITE_SPACES = ("namespace", "space", "identity", "dimensions", "state")
_ORKEXA_SQLITE_VECTORS = ("space", "chunk_id", "embedding")
_SQLITE_TABLES_SQL = "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name LIMIT 10001"
_VEC0 = re.compile(r"^\s*CREATE\s+VIRTUAL\s+TABLE\s+.*?\bUSING\s+vec0\s*\((?P<body>.*)\)\s*;?\s*$", re.I | re.S)
_VEC0_COLUMN = re.compile(
    r'^\s*(?P<name>"(?:[^"]|"")+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)\s+'
    r"(?P<element>float|f32|int8|i8|bit)\s*\[\s*(?P<dimensions>\d+)\s*\](?P<options>.*)$",
    re.I | re.S,
)
_DISTANCE = re.compile(r"\bdistance_metric\s*=\s*([A-Za-z0-9_]+)", re.I)


def _ident(name: str) -> str:
    """A name as a quoted identifier (``"`` doubled): the same rule in PostgreSQL and SQLite."""
    return '"' + name.replace('"', '""') + '"'


def _unquote(name: str) -> str:
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        return name[1:-1].replace('""', '"')
    if len(name) >= 2 and name[0] in "`[":
        return name[1:-1]
    return name


def _hint(code: str, severity: str = "info", **params: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "params": params}


def _expire(hook: Cancel, finished: threading.Event) -> None:
    for _ in range(CANCEL_ATTEMPTS):
        hook.cancel()
        if finished.wait(CANCEL_INTERVAL_S):
            return


def _lost(exc: BaseException) -> bool:
    """Whether a database error means the connection is gone (then it is the route's error, not a hint)."""
    classification = getattr(exc, "classification", None)
    return classification is not None and classification.kind is Kind.CONNECTION


def split_top_level(text: str) -> list[str]:
    """``text`` split at the commas outside brackets, parentheses and quotes (a ``vec0`` column list)."""
    parts: list[str] = []
    depth, quote, start = 0, "", 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def vec0_columns(sql: str | None) -> list[dict[str, Any]] | None:
    """The vector columns of a ``CREATE VIRTUAL TABLE ... USING vec0(...)`` text, or None for another table."""
    found = _VEC0.match(sql or "")
    if found is None:
        return None
    columns: list[dict[str, Any]] = []
    for part in split_top_level(found.group("body")):
        column = _VEC0_COLUMN.match(part)
        if column is None:
            continue  # a partition key, metadata or auxiliary column, or a table option
        distance = _DISTANCE.search(column.group("options"))
        element = column.group("element").lower()
        columns.append(
            {
                "name": _unquote(column.group("name")),
                "element": {"f32": "float", "i8": "int8"}.get(element, element),
                "dimensions": int(column.group("dimensions")),
                "distance_metric": distance.group(1).lower() if distance else None,
            }
        )
    return columns


def parse_identity(text: object) -> dict[str, Any] | None:
    """The fields of an Orkexa ``EmbeddingIdentity`` (``to_dict``) the page shows, or None for text that is no JSON
    object. Instructions and prefixes are left out: they are prompts, not identity facts the page needs."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    if isinstance(text, dict):
        data: Any = text
    else:
        try:
            data = json.loads(str(text))
        except (ValueError, RecursionError):
            return None
    if not isinstance(data, dict):
        return None

    def word(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) and value != "" else None

    dimensions = data.get("dimensions")
    normalized = data.get("normalized")
    return {
        "provider": word("provider"),
        "model": word("model"),
        "digest": word("digest"),
        "dimensions": dimensions if isinstance(dimensions, int) and not isinstance(dimensions, bool) else None,
        "normalized": normalized if isinstance(normalized, bool) else None,
    }


@dataclass
class _Run:
    """One inspection: its connection, the counts it may still make, and the hints it found."""

    connection: OpenConnection
    clock: Callable[[], float]
    count_timeout_s: float
    started: float = 0.0
    counts: int = 0
    skipped: int = 0
    hints: list[dict[str, Any]] = field(default_factory=list)

    def hint(self, code: str, severity: str = "info", **params: Any) -> None:
        entry = _hint(code, severity, **params)
        if entry not in self.hints:
            self.hints.append(entry)

    def read(self, sql: str, params: Mapping[str, object] | None = None) -> list[tuple[Any, ...]] | None:
        """The rows of a catalog or metadata read, or None when the database refused it (a permission, a missing
        module); a lost connection is raised."""
        try:
            return self.connection.fetch(sql, params)
        except TabrionError as exc:
            if _lost(exc):
                raise
            return None

    def count(self, table_sql: str, where: str = "", params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """A bounded count: ``{"value", "kind": "exact" | "more_than"}``, or ``{"value": None, "kind": "timeout" |
        "unreadable" | "skipped"}``."""
        elapsed = self.clock() - self.started
        if self.counts >= MAX_COUNTS or elapsed >= self.connection.timeout_s:
            self.skipped += 1
            return {"value": None, "kind": "skipped"}
        self.counts += 1
        # A catalog-listed name as a quoted identifier, a fixed condition with bound values, and a fixed bound.
        filtered = f"{table_sql} WHERE {where}" if where else table_sql
        sql = f"SELECT count(*) FROM (SELECT 1 FROM {filtered} LIMIT {EXACT_COUNT_MAX + 1}) AS bounded"  # noqa: S608
        hook, finished = Cancel(), threading.Event()
        timer = threading.Timer(min(self.count_timeout_s, self.connection.timeout_s), _expire, args=(hook, finished))
        timer.daemon = True
        timer.start()
        try:
            rows = self.connection.fetch(sql, params, cancel=hook)
        except Exception as exc:
            if hook.requested:
                return {"value": None, "kind": "timeout"}
            if isinstance(exc, TabrionError) and not _lost(exc):
                return {"value": None, "kind": "unreadable"}
            raise
        finally:
            finished.set()
            timer.cancel()
        value = int(rows[0][0])
        if value > EXACT_COUNT_MAX:
            return {"value": EXACT_COUNT_MAX, "kind": "more_than"}
        return {"value": value, "kind": "exact"}

    def counted(self, rows: dict[str, Any], schema: str, table: str) -> dict[str, Any]:
        """``rows`` with the hint its kind needs."""
        if rows["kind"] == "timeout":
            self.hint("count_timeout", "warning", schema=schema, table=table)
        elif rows["kind"] == "unreadable":
            self.hint("table_unreadable", "warning", schema=schema, table=table)
        return rows


@dataclass
class VectorInspection:
    """Reads the vector facts of one open connection. Blocking (call it off the event loop)."""

    registry: ConnectionRegistry
    clock: Callable[[], float] = time.monotonic
    count_timeout_s: float = COUNT_TIMEOUT_S

    def inspect(self, connection_id: object) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        run = _Run(connection, self.clock, self.count_timeout_s, started=self.clock())
        if connection.engine == "postgresql":
            extension, columns, identities = self._postgres(run)
        else:
            extension, columns, identities = self._sqlite(run)
        if len(columns) > MAX_COLUMNS:
            run.hint("columns_capped", count=MAX_COLUMNS)
            columns = columns[:MAX_COLUMNS]
        if not columns and not identities:
            run.hint("no_vector_data")
        for column in columns:
            if column["search"] == "exact":
                run.hint("exact_search", schema=column["schema"], table=column["table"], column=column["column"])
        if run.skipped:
            run.hint("counts_skipped", count=run.skipped)
        order = {"warning": 0, "info": 1}
        hints = sorted(run.hints, key=lambda item: order.get(item["severity"], 2))
        return {
            "connection_id": connection.id,
            "engine": connection.engine,
            "extension": extension,
            "columns": columns,
            "identities": identities,
            "hints": hints,
            "limits": {"exact_count_max": EXACT_COUNT_MAX, "max_columns": MAX_COLUMNS, "max_counts": MAX_COUNTS},
        }

    # PostgreSQL

    def _postgres(self, run: _Run) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        extension = self._pgvector(run)
        indexes: dict[tuple[str, str], list[dict[str, Any]] | None] = {}

        def table_indexes(schema: str, table: str) -> list[dict[str, Any]] | None:
            key = (schema, table)
            if key not in indexes:
                rows = run.read(_PG_INDEXES_SQL, {"schema": schema, "name": table})
                indexes[key] = None if rows is None else [self._pg_index(row) for row in rows]
            return indexes[key]

        columns: list[dict[str, Any]] = []
        rows = run.read(_PG_COLUMNS_SQL)
        if rows is None:
            run.hint("catalog_unreadable", "warning")
            rows = []
        for schema, table, relkind, name, base, shown, typmod, estimate, readable in rows:
            dimensions = int(typmod) if isinstance(typmod, int) and typmod > 0 else None
            if dimensions is None:
                run.hint("dimensions_undeclared", "warning", schema=schema, table=table, column=name)
            column = self._pg_column(run, table_indexes, schema, table, name, str(shown), str(base))
            column["dimensions"], column["dimensions_source"] = dimensions, "type" if dimensions else None
            if not readable:
                run.hint("table_unreadable", "warning", schema=schema, table=table)
                column["rows"] = None
            elif relkind == "f":
                column["rows"] = None  # a foreign table is not counted: the count would run on the remote server
            elif isinstance(estimate, int) and estimate > EXACT_COUNT_MAX:
                column["rows"] = {"value": estimate, "kind": "estimate"}
                run.hint("count_estimated", schema=schema, table=table)
            else:
                column["rows"] = run.counted(
                    run.count(f"{_ident(schema)}.{_ident(table)}", f"{_ident(name)} IS NOT NULL"), schema, table
                )
            columns.append(column)
        identities: list[dict[str, Any]] = []
        for layout in self._pg_orkexa_layouts(run):
            column, found = self._pg_orkexa(run, table_indexes, layout)
            columns.append(column)
            identities += found
        if extension["state"] in ("available", "missing") and not any(c["kind"] == "pgvector" for c in columns):
            code = "pgvector_available" if extension["state"] == "available" else "pgvector_missing"
            run.hint(code, version=extension["available_version"])
        return extension, columns, identities[:MAX_IDENTITIES]

    @staticmethod
    def _pgvector(run: _Run) -> dict[str, Any]:
        try:
            status = run.connection.extension_status(_PGVECTOR_EXTENSION)
        except Exception as exc:
            if _lost(exc):
                raise
            run.hint("extension_unknown", "warning")
            return {
                "name": "pgvector",
                "state": "unknown",
                "installed_version": None,
                "available_version": None,
                "schema": None,
                "update_available": False,
                "determinable": False,
            }
        state = _STATES.get(status.state, "unknown")
        update = (
            status.installed_version is not None
            and status.available_version is not None
            and status.installed_version != status.available_version
        )
        if state == "other_schema":
            run.hint("pgvector_other_schema", "warning", schema=status.extension_schema)
        if update:
            run.hint("pgvector_update", installed=status.installed_version, available=status.available_version)
        return {
            "name": "pgvector",
            "state": state,
            "installed_version": status.installed_version,
            "available_version": status.available_version,
            "schema": status.extension_schema,
            "update_available": update,
            "determinable": True,
        }

    @staticmethod
    def _pg_index(row: tuple[Any, ...]) -> dict[str, Any]:
        name, method, valid, ready, live, definition, opclasses, columns, options, size = row
        usable = bool(valid) and bool(ready) and bool(live)
        return {
            "name": name,
            "method": method,
            "valid": bool(valid),
            "ready": bool(ready),
            "live": bool(live),
            "state": "valid" if usable else ("building" if not ready else "invalid"),
            "ann": method in ANN_METHODS,
            "operator_classes": list(opclasses or []),
            "options": list(options or []),
            "size_bytes": size if isinstance(size, int) else None,
            "definition": definition,
            "columns": list(columns or []),
        }

    @staticmethod
    def _pg_column(
        run: _Run,
        table_indexes: Callable[[str, str], list[dict[str, Any]] | None],
        schema: str,
        table: str,
        name: str,
        shown: str,
        base: str,
        *,
        kind: str = "pgvector",
        layout: str = "generic",
    ) -> dict[str, Any]:
        found = table_indexes(schema, table)
        if found is None:
            run.hint("indexes_unreadable", "warning", schema=schema, table=table)
        on_column = [
            {key: value for key, value in index.items() if key != "columns"}
            for index in found or []
            if name in index["columns"]
        ]
        for index in on_column:
            if index["ann"] and index["state"] != "valid":
                run.hint("index_invalid", "warning", schema=schema, table=table, index=index["name"])
        ann = any(index["ann"] and index["state"] == "valid" for index in on_column)
        return {
            "schema": schema,
            "table": table,
            "column": name,
            "type": shown,
            "base_type": base,
            "kind": kind,
            "layout": layout,
            "dimensions": None,
            "dimensions_source": None,
            "search": "ann" if ann else "exact",
            "rows": None,
            "indexes": on_column,
        }

    @staticmethod
    def _pg_orkexa_layouts(run: _Run) -> list[dict[str, Any]]:
        rows = run.read(_PG_ORKEXA_SQL)
        if rows is None:
            return []
        tables = {
            (schema, name): {
                "estimate": estimate,
                "readable": bool(readable),
                "columns": dict(zip(names, types, strict=False)),
            }
            for schema, name, estimate, readable, names, types in rows
        }
        layouts: list[dict[str, Any]] = []
        for (schema, name), spaces in sorted(tables.items()):
            if not name.endswith("vector_spaces"):
                continue
            prefix = name[: -len("vector_spaces")]
            vectors = tables.get((schema, prefix + "chunk_vectors"))
            if vectors is None:
                continue
            if any(spaces["columns"].get(key) != kind for key, kind in _ORKEXA_PG_SPACES.items()):
                continue
            if any(vectors["columns"].get(key) != kind for key, kind in _ORKEXA_PG_VECTORS.items()):
                continue
            layouts.append({"schema": schema, "prefix": prefix, "spaces": (name, spaces), "vectors": vectors})
        return layouts

    def _pg_orkexa(
        self,
        run: _Run,
        table_indexes: Callable[[str, str], list[dict[str, Any]] | None],
        layout: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        schema, prefix = layout["schema"], layout["prefix"]
        spaces_name, spaces = layout["spaces"]
        vectors_name, vectors = prefix + "chunk_vectors", layout["vectors"]
        column = self._pg_column(
            run, table_indexes, schema, vectors_name, "embedding", "real[]", "float4[]",
            kind="float_array", layout="orkexa",
        )  # fmt: skip
        vectors_table = f"{_ident(schema)}.{_ident(vectors_name)}"
        if not vectors["readable"]:
            run.hint("table_unreadable", "warning", schema=schema, table=vectors_name)
        elif isinstance(vectors["estimate"], int) and vectors["estimate"] > EXACT_COUNT_MAX:
            column["rows"] = {"value": vectors["estimate"], "kind": "estimate"}
            run.hint("count_estimated", schema=schema, table=vectors_name)
        else:
            column["rows"] = run.counted(run.count(vectors_table), schema, vectors_name)
        identities: list[dict[str, Any]] = []
        if not spaces["readable"]:
            run.hint("metadata_unreadable", "warning", schema=schema, table=spaces_name)
        else:
            table = f"{_ident(schema)}.{_ident(spaces_name)}"  # a catalog-listed name, quoted
            columns = "tenant_id, namespace, space, identity::pg_catalog.text, dimensions, state"
            rows = run.read(f"SELECT {columns} FROM {table} ORDER BY 1, 2, 3 LIMIT 201")  # noqa: S608
            if rows is None:
                run.hint("metadata_unreadable", "warning", schema=schema, table=spaces_name)
                rows = []
            for tenant, namespace, space, identity, dimensions, state in rows[:MAX_IDENTITIES]:
                embeddings: dict[str, Any] | None = None
                if vectors["readable"]:
                    embeddings = run.counted(
                        run.count(vectors_table, "tenant_id = :tenant AND space = :space",
                                  {"tenant": tenant, "space": space}),
                        schema, vectors_name,
                    )  # fmt: skip
                identities.append(
                    self._identity(run, "orkexa", schema, spaces_name, tenant, namespace, space, identity,
                                   dimensions, state, embeddings)
                )  # fmt: skip
        dims = {item["dimensions"] for item in identities if item["dimensions"] is not None}
        if len(dims) == 1:
            column["dimensions"], column["dimensions_source"] = dims.pop(), "metadata"
        return column, identities

    @staticmethod
    def _identity(
        run: _Run,
        layout: str,
        schema: str,
        table: str,
        tenant: object,
        namespace: object,
        space: object,
        identity: object,
        dimensions: object,
        state: object,
        embeddings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        parsed = parse_identity(identity)
        dims = dimensions if isinstance(dimensions, int) and not isinstance(dimensions, bool) else None
        complete = parsed is not None and bool(parsed["provider"]) and bool(parsed["model"]) and dims is not None
        if not complete:
            run.hint("identity_incomplete", "warning", space=str(space))
        if (
            parsed is not None
            and parsed["dimensions"] is not None
            and dims is not None
            and parsed["dimensions"] != dims
        ):
            run.hint("dimension_mismatch", "warning", space=str(space), identity=parsed["dimensions"], stored=dims)
        if state == "building":
            run.hint("space_building", space=str(space))
        return {
            "layout": layout,
            "schema": schema,
            "table": table,
            "tenant": None if tenant is None else str(tenant),
            "namespace": None if namespace is None else str(namespace),
            "space": str(space),
            "state": None if state is None else str(state),
            "provider": parsed["provider"] if parsed else None,
            "model": parsed["model"] if parsed else None,
            "digest": parsed["digest"] if parsed else None,
            "normalized": parsed["normalized"] if parsed else None,
            "dimensions": dims,
            "complete": complete,
            "embeddings": embeddings,
        }

    # SQLite

    def _sqlite(self, run: _Run) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        extension = {
            "name": "sqlite-vec",
            "state": "not_determinable",
            "installed_version": None,
            "available_version": None,
            "schema": None,
            "update_available": False,
            "determinable": False,
        }
        run.hint("sqlite_vec_undeterminable")
        rows = run.read(_SQLITE_TABLES_SQL)
        if rows is None:
            run.hint("catalog_unreadable", "warning")
            return extension, [], []
        tables = {str(name): (str(sql) if sql is not None else None) for name, sql in rows}
        columns: list[dict[str, Any]] = []
        for name, sql in tables.items():
            found = vec0_columns(sql)
            if found is None:
                continue
            columns += self._vec0(run, tables, name, found)
        identities: list[dict[str, Any]] = []
        if "index_spaces" in tables and "index_vectors" in tables:
            column, identities = self._sqlite_orkexa(run)
            if column is not None:
                columns.append(column)
        if "index_meta" in tables:
            meta = run.read("SELECT value FROM index_meta WHERE key = 'dimensions'")
            if meta and "index_vec" not in tables:
                run.hint("legacy_table_missing", "warning", table="index_vec")
            elif meta:
                for column in columns:
                    if column["table"] == "index_vec":
                        column["layout"] = "orkexa"
                        if str(meta[0][0]) != str(column["dimensions"]):
                            run.hint("legacy_dimension_mismatch", "warning", table="index_vec",
                                     meta=str(meta[0][0]), declared=column["dimensions"])  # fmt: skip
        return extension, columns, identities[:MAX_IDENTITIES]

    @staticmethod
    def _vec0(run: _Run, tables: dict[str, str | None], name: str, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
        run.hint("vec0_module", table=name)
        rows: dict[str, Any] | None = None
        if f"{name}_rowids" in tables:
            rows = run.counted(run.count(_ident(f"{name}_rowids")), "main", name)
        else:
            run.hint("vec0_shadow_missing", "warning", table=name)
        created_with = None
        if f"{name}_info" in tables:
            shadow = _ident(name + "_info")  # a name sqlite_master listed, quoted
            info = run.read(f"SELECT value FROM {shadow} WHERE key = 'CREATE_VERSION'")  # noqa: S608
            if info:
                created_with = str(info[0][0])
        return [
            {
                "schema": "main",
                "table": name,
                "column": column["name"],
                "type": f"{column['element']}[{column['dimensions']}]",
                "base_type": column["element"],
                "kind": "vec0",
                "layout": "generic",
                "dimensions": column["dimensions"],
                "dimensions_source": "declaration",
                "search": "exact",
                "rows": rows,
                "indexes": [
                    {
                        "name": name,
                        "method": "vec0",
                        "valid": None,
                        "ready": None,
                        "live": None,
                        "state": "unknown",
                        "ann": False,
                        "operator_classes": [],
                        "options": [f"distance_metric={column['distance_metric']}"]
                        if column["distance_metric"]
                        else [],
                        "size_bytes": None,
                        "definition": None,
                        "created_with": created_with,
                    }
                ],
            }
            for column in found
        ]

    def _sqlite_orkexa(self, run: _Run) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        def names(table: str) -> set[str]:
            rows = run.read("SELECT name FROM pragma_table_info(:name)", {"name": table})
            return {str(row[0]) for row in rows or []}

        if not set(_ORKEXA_SQLITE_SPACES) <= names("index_spaces"):
            return None, []
        if not set(_ORKEXA_SQLITE_VECTORS) <= names("index_vectors"):
            return None, []
        column: dict[str, Any] = {
            "schema": "main",
            "table": "index_vectors",
            "column": "embedding",
            "type": "BLOB",
            "base_type": "blob",
            "kind": "blob",
            "layout": "orkexa",
            "dimensions": None,
            "dimensions_source": None,
            "search": "exact",
            "rows": run.counted(run.count(_ident("index_vectors")), "main", "index_vectors"),
            "indexes": [],
        }
        rows = run.read(
            "SELECT namespace, space, identity, dimensions, state FROM index_spaces "
            "ORDER BY namespace, space LIMIT 201"  # MAX_IDENTITIES + 1
        )
        if rows is None:
            run.hint("metadata_unreadable", "warning", schema="main", table="index_spaces")
            rows = []
        identities = [
            self._identity(
                run, "orkexa", "main", "index_spaces", None, namespace, space, identity, dimensions, state,
                run.counted(
                    run.count(_ident("index_vectors"), "space = :space", {"space": space}), "main", "index_vectors"
                ),
            )
            for namespace, space, identity, dimensions, state in rows[:MAX_IDENTITIES]
        ]  # fmt: skip
        dims = {item["dimensions"] for item in identities if item["dimensions"] is not None}
        if len(dims) == 1:
            column["dimensions"], column["dimensions_source"] = dims.pop(), "metadata"
        return column, identities
