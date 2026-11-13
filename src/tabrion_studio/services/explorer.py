"""The database explorer: catalog, table detail, PostgreSQL extensions, SQLite facts (03 section 2 B and 6, STU-010).

Everything here reads, on the connection's read-only path (``OpenConnection.inspect`` and ``OpenConnection.fetch``,
each one ``Database.read()``), and always through ``ConnectionRegistry.get``. Reflection uses SQLAlchemy's
``Inspector`` (tabrion 0.2: ``Database.inspector()``, :mod:`.tabrion_compat`); the few queries of Studio's own are
fixed texts with bound parameters (names from the request are values, never part of the text).

* **Catalog:** schemas and their objects (tables, views, materialized views, sequences, foreign tables), flat and
  sorted, cached per connection until ``refresh`` (03 section 6: "flach, gecacht"). PostgreSQL's ``pg_*`` and
  ``information_schema`` schemas are left out. SQLite has the one schema ``main``.
* **Table detail:** columns (type, nullable, default, comment), primary key, foreign keys out and in, unique and
  check constraints, indexes and, for PostgreSQL, the row estimate (``pg_class.reltuples``). An object that is not in
  the catalog is ``studio.object_not_found``.
* **Extensions (PostgreSQL):** name, version, schema, the default version available, and whether an update exists.
* **Diagram (STU-023):** the tables of one schema with their columns (name, type, nullable), primary key and outgoing
  foreign keys, in one answer for the ER diagram of the page; at most :data:`MAX_DIAGRAM_TABLES` tables.
* **SQLite facts:** ``journal_mode``, ``user_version``, ``page_size``, ``page_count``, ``foreign_keys``, the file's
  size and permission bits, and per table whether it is ``STRICT`` or ``WITHOUT ROWID`` (``PRAGMA table_list``).

Values of the database (defaults, check texts, comments) are the database's own text and go to the page as data; the
page renders them as text, never as HTML. Nothing of this is logged.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from dataclasses import dataclass, field
import os
import sqlite3
import stat
import threading
import time
from typing import Any, TypeVar
import warnings

import sqlalchemy

from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection

__all__ = ["KINDS", "MAX_DIAGRAM_TABLES", "Explorer", "ExplorerError", "Reflected"]

_T = TypeVar("_T")

KINDS = ("table", "view", "materialized_view", "sequence", "foreign_table")
#: Schemas of the server itself, with ``pg_*``: the catalog leaves them out, and so a table detail of them is refused
#: too (``known`` answers only objects of the catalog; I-1 of ``stu-010-review.md``).
_SYSTEM_SCHEMAS = frozenset({"information_schema"})
#: L-3 of stu-010-review.md: a name missing from a catalog at least this old rebuilds it once; a younger one does not.
REBUILD_AFTER_S = 5.0
#: ``PRAGMA table_list`` (STRICT, WITHOUT ROWID) needs SQLite 3.37; a module value, so a test can force the fallback.
_HAS_TABLE_LIST = sqlite3.sqlite_version_info >= (3, 37)
#: M-1 of stu-010-review.md: PostgreSQL foreign keys out of and into one table, from the catalog itself. SQLAlchemy's
#: reflection reads the referenced schema from ``pg_get_constraintdef()``, which leaves it out for a table on the
#: search path, so a key into ``public`` looked like one into the referencing table's own schema. ``conparentid = 0``
#: leaves out the copies PostgreSQL keeps for each partition of a partitioned table (N-1 of the delta re-review): the
#: key is shown once, on the partitioned table.
_PG_FOREIGN_KEYS_SQL = (
    "SELECT con.conname, sn.nspname, sc.relname, tn.nspname, tc.relname, "
    "ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY k(n, i) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i), "
    "ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY k(n, i) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class sc ON sc.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace sn ON sn.oid = sc.relnamespace "
    "JOIN pg_catalog.pg_class tc ON tc.oid = con.confrelid "
    "JOIN pg_catalog.pg_namespace tn ON tn.oid = tc.relnamespace "
    "WHERE con.contype = 'f' AND con.conparentid = 0 AND ((sn.nspname = :schema AND sc.relname = :name) "
    "OR (tn.nspname = :schema AND tc.relname = :name)) "
    "ORDER BY sn.nspname, sc.relname, con.conname"
)
#: STU-023: the foreign keys out of every table of one schema, from the catalog like ``_PG_FOREIGN_KEYS_SQL`` (M-1):
#: the referenced schema is always named, and a partitioned table's key is listed once (``conparentid = 0``).
_PG_SCHEMA_FOREIGN_KEYS_SQL = (
    "SELECT con.conname, sc.relname, tn.nspname, tc.relname, "
    "ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY k(n, i) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i), "
    "ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY k(n, i) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class sc ON sc.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace sn ON sn.oid = sc.relnamespace "
    "JOIN pg_catalog.pg_class tc ON tc.oid = con.confrelid "
    "JOIN pg_catalog.pg_namespace tn ON tn.oid = tc.relnamespace "
    "WHERE con.contype = 'f' AND con.conparentid = 0 AND sn.nspname = :schema "
    "ORDER BY sc.relname, con.conname"
)
#: STU-023: the most tables one diagram answer holds. The page draws at most 150 of them (03 section 2 E); a schema
#: with more is listed for the page's selection, up to this bound, so that one answer stays a few megabytes at most.
MAX_DIAGRAM_TABLES = 1000
#: The kinds a diagram shows: the ones that can have keys.
_DIAGRAM_KINDS = ("table", "foreign_table")
#: STU-022: ``pg_class.reltuples``, ``-1`` before the first ``VACUUM``/``ANALYZE`` (``None``); a partitioned table
#: without an estimate of its own gets the sum of its partitions' when each of them has one.
_ESTIMATE_SQL = (
    "SELECT CASE WHEN c.reltuples OPERATOR(pg_catalog.>=) 0 THEN c.reltuples::pg_catalog.int8 "
    "WHEN c.relkind OPERATOR(pg_catalog.=) 'p' THEN (SELECT CASE WHEN pg_catalog.bool_and(l.reltuples "
    "OPERATOR(pg_catalog.>=) 0) THEN pg_catalog.sum(l.reltuples)::pg_catalog.int8 END "
    "FROM pg_catalog.pg_partition_tree(c.oid) t JOIN pg_catalog.pg_class l ON l.oid = t.relid WHERE t.isleaf) END "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name"
)
_EXTENSIONS_SQL = (
    "SELECT e.extname, e.extversion, n.nspname, a.default_version "
    "FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
    "LEFT JOIN pg_catalog.pg_available_extensions a ON a.name = e.extname ORDER BY e.extname"
)
#: STU-013: the PostgreSQL type facts of a relation's columns (base type of a domain, array element, category), which
#: SQLAlchemy's reflection leaves out or loses (an unknown type reflects as ``NullType``).
_PG_COLUMN_TYPES_SQL = (
    "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), b.typname, e.typname, "
    "COALESCE(e.typcategory, b.typcategory) "
    "FROM pg_catalog.pg_attribute a "
    "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
    "JOIN pg_catalog.pg_type b ON b.oid = CASE WHEN t.typtype = 'd' THEN t.typbasetype ELSE t.oid END "
    "LEFT JOIN pg_catalog.pg_type e ON e.oid = b.typelem AND b.typcategory = 'A' "
    "WHERE n.nspname = :schema AND c.relname = :name AND a.attnum > 0 AND NOT a.attisdropped "
    "LIMIT 1601"  # a relation has at most 1600 columns; the viewer's statements all have a LIMIT
)
#: L-5 of stu-013-review.md: how long the viewer's reflection of one object is reused (two catalog round trips less
#: per page); a refresh of the catalog or a closed connection drops it at once.
COLUMNS_TTL_S = 10.0
_SQLITE_PRAGMAS = ("journal_mode", "user_version", "page_size", "page_count", "foreign_keys")


class ExplorerError(Exception):
    """An object or request the explorer cannot answer; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _optional(call: Callable[[], list[_T]]) -> list[_T]:
    """``call()``, or nothing where the dialect does not have it (SQLite: materialized views, sequences, ...)."""
    try:
        return list(call())
    except NotImplementedError:
        return []


#: The inspector method that lists each kind (``get_foreign_table_names`` exists on PostgreSQL's inspector only).
_LISTINGS = {
    "table": "get_table_names",
    "view": "get_view_names",
    "materialized_view": "get_materialized_view_names",
    "sequence": "get_sequence_names",
    "foreign_table": "get_foreign_table_names",
}


def _listed(found: sqlalchemy.Inspector, method: str, schema: str | None) -> list[str]:
    call = getattr(found, method, None)
    if call is None:
        return []
    return [str(name) for name in _optional(lambda: call(schema=schema))]


def _type_name(value: object) -> str | None:
    try:
        return str(value)
    except Exception:  # a type SQLAlchemy cannot render (an unknown PostgreSQL type)
        return None


def _text(value: object) -> str | None:
    return None if value is None else str(value)


@dataclass
class _Cached:
    built: float
    doc: dict[str, Any]


@dataclass(frozen=True)
class Reflected:
    """What :meth:`Explorer.columns` reflects of one object (``rowid``: SQLite tables only, whether it has one)."""

    connection: OpenConnection
    kind: str
    columns: list[dict[str, Any]]
    primary: list[str]
    rowid: bool | None


@dataclass
class Explorer:
    """Catalog cache and readers. Blocking (call it off the event loop); thread-safe."""

    registry: ConnectionRegistry
    clock: Callable[[], float] = time.time
    _cache: dict[str, _Cached] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _columns: dict[tuple[str, str, str], tuple[float, Reflected]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.registry.on_close(self.forget)

    def forget(self, connection_id: str) -> None:
        """Drop the cached catalog of ``connection_id`` (its connection closed, or an object of it is gone)."""
        with self._lock:
            self._cache.pop(connection_id, None)
            for key in [key for key in self._columns if key[0] == connection_id]:
                del self._columns[key]

    def _reflect(self, connection: OpenConnection, consume: Callable[[sqlalchemy.Inspector], _T]) -> _T:
        """``connection.inspect(consume)``; an object dropped since the catalog was built is ``object_not_found``
        and drops the cached catalog (L-1 of ``stu-010-review.md``)."""
        try:
            return connection.inspect(consume)
        except Exception as exc:
            if isinstance(exc, sqlalchemy.exc.NoSuchTableError) or isinstance(
                getattr(exc, "original", None), sqlalchemy.exc.NoSuchTableError
            ):
                self.forget(connection.id)
                raise ExplorerError("studio.object_not_found") from None
            raise

    # Catalog

    def catalog(self, connection_id: object, *, refresh: bool = False) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        with self._lock:
            # Entries of connections that are gone are dropped here, so the cache cannot grow without bound.
            open_ids = {c.id for c in self.registry.list()} | {connection.id}
            for stale in [key for key in self._cache if key not in open_ids]:
                del self._cache[stale]
            for gone in [key for key in self._columns if key[0] not in open_ids]:
                del self._columns[gone]
            cached = None if refresh else self._cache.get(connection.id)
            if refresh:
                for key in [key for key in self._columns if key[0] == connection.id]:
                    del self._columns[key]
        if cached is not None:
            return {**cached.doc, "cached": True}
        doc = {"connection_id": connection.id, **connection.inspect(lambda found: self._catalog(connection, found))}
        doc["built_at"] = self.clock()
        with self._lock:
            self._cache[connection.id] = _Cached(doc["built_at"], doc)
        return {**doc, "cached": False}

    @staticmethod
    def _schemas(connection: OpenConnection, found: sqlalchemy.Inspector) -> list[str]:
        if connection.engine == "sqlite":
            return ["main"]
        return sorted(
            name for name in found.get_schema_names() if not name.startswith("pg_") and name not in _SYSTEM_SCHEMAS
        )

    def _catalog(self, connection: OpenConnection, found: sqlalchemy.Inspector) -> dict[str, Any]:
        objects: list[dict[str, str]] = []
        schemas = self._schemas(connection, found)
        for schema in schemas:
            key = None if connection.engine == "sqlite" else schema
            listing = {kind: _listed(found, method, key) for kind, method in _LISTINGS.items()}
            if connection.engine == "postgresql":
                # get_table_names lists foreign tables too; they are shown once, as foreign tables.
                foreign = set(listing["foreign_table"])
                listing["table"] = [name for name in listing["table"] if name not in foreign]
            for kind in KINDS:
                objects += [{"schema": schema, "name": name, "kind": kind} for name in sorted(listing[kind])]
        return {"engine": connection.engine, "schemas": schemas, "objects": objects}

    def known(self, connection_id: object, schema: str, name: str) -> tuple[OpenConnection, str]:
        """The connection and kind of a catalog object; ``studio.object_not_found`` for anything else (the check of
        every per-object route, also STU-022's DDL, policies and row count)."""
        doc = self.catalog(connection_id)
        kind = self._kind(doc, schema, name)
        if kind is None and doc["cached"] and self.clock() - doc["built_at"] >= REBUILD_AFTER_S:
            # Created since the catalog was built? One fresh look before refusing, at most every few seconds per
            # connection, so that unknown names cannot keep the pool busy with rebuilds (L-3).
            doc = self.catalog(connection_id, refresh=True)
            kind = self._kind(doc, schema, name)
        if kind is None:
            raise ExplorerError("studio.object_not_found")
        return self.registry.get(connection_id), kind

    @staticmethod
    def _kind(doc: dict[str, Any], schema: str, name: str) -> str | None:
        for item in doc["objects"]:
            if item["schema"] == schema and item["name"] == name:
                return str(item["kind"])
        return None

    def columns(self, connection_id: object, schema: str, name: str) -> Reflected:
        """The connection, kind, columns and primary key of a catalog object (STU-013's data viewer; the table
        detail without constraints, indexes and incoming keys).

        Each column has ``name``, ``type`` (display), ``nullable`` and the facts the viewer pages by: for PostgreSQL
        ``base`` (the type's name, a domain resolved to its base type), ``element`` (an array's element type) and
        ``category`` (``pg_type.typcategory`` of the element or base type). Kept for :data:`COLUMNS_TTL_S`, keyed
        like the catalog (L-5 of ``stu-013-review.md``), and dropped with it.
        """
        connection, kind = self.known(connection_id, schema, name)
        cache_key = (connection.id, schema, name)
        with self._lock:
            cached = self._columns.get(cache_key)
        if cached is not None and self.clock() - cached[0] < COLUMNS_TTL_S and cached[1].kind == kind:
            return dataclasses.replace(cached[1], connection=connection)
        if kind == "sequence":
            return Reflected(connection, kind, [], [], None)
        key = None if connection.engine == "sqlite" else schema

        def read(found: sqlalchemy.Inspector) -> tuple[list[dict[str, Any]], list[str], bool | None]:
            with warnings.catch_warnings():
                # A type SQLAlchemy does not know (point, a type of an extension) reflects as NullType with a warning;
                # its name comes from the catalog below.
                warnings.simplefilter("ignore", sqlalchemy.exc.SAWarning)
                reflected = found.get_columns(name, schema=key)
            columns = [
                {
                    "name": str(column["name"]),
                    "type": _type_name(column["type"]),
                    "nullable": bool(column.get("nullable", True)),
                    "base": None,
                    "element": None,
                    "category": None,
                }
                for column in reflected
            ]
            primary = found.get_pk_constraint(name, schema=key)
            rowid: bool | None = None
            if connection.engine == "sqlite" and kind == "table":
                # M-2: a rowid table (not WITHOUT ROWID) can hold NULL in a primary key that is not the row id.
                rowid = bool(found.get_table_options(name).get("sqlite_with_rowid", True))
            return columns, [str(n) for n in primary.get("constrained_columns") or []], rowid

        found_columns, primary, rowid = self._reflect(connection, read)
        if connection.engine == "postgresql":
            facts = {
                str(row[0]): row[1:] for row in connection.fetch(_PG_COLUMN_TYPES_SQL, {"schema": schema, "name": name})
            }
            for column in found_columns:
                fact = facts.get(column["name"])
                if fact is not None:
                    if column["type"] in (None, "NULL"):  # a type SQLAlchemy does not know
                        column["type"] = _text(fact[0])
                    column["base"], column["element"], column["category"] = (
                        _text(fact[1]),
                        _text(fact[2]),
                        _text(fact[3]),
                    )
        reflected = Reflected(connection, kind, found_columns, primary, rowid)
        with self._lock:
            self._columns[cache_key] = (self.clock(), reflected)
        return reflected

    # Table detail

    def table(self, connection_id: object, schema: str, name: str) -> dict[str, Any]:
        connection, kind = self.known(connection_id, schema, name)
        if kind == "sequence":
            return {"connection_id": connection.id, "schema": schema, "name": name, "kind": kind}
        key = None if connection.engine == "sqlite" else schema
        detail = self._reflect(connection, lambda found: self._table(connection, found, key, schema, name))
        if connection.engine == "postgresql":
            detail["foreign_keys"], detail["referenced_by"] = self._pg_foreign_keys(connection, schema, name)
        estimate = None
        if connection.engine == "postgresql" and kind in ("table", "materialized_view"):
            rows = connection.fetch(_ESTIMATE_SQL, {"schema": schema, "name": name})
            if rows and isinstance(rows[0][0], int) and rows[0][0] >= 0:
                estimate = rows[0][0]
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "kind": kind,
            **detail,
            "row_estimate": estimate,
        }

    def _table(
        self, connection: OpenConnection, found: sqlalchemy.Inspector, key: str | None, schema: str, name: str
    ) -> dict[str, Any]:
        columns = [
            {
                "name": column["name"],
                "type": _type_name(column["type"]),
                "nullable": bool(column.get("nullable", True)),
                "default": _text(column.get("default")),
                "comment": _text(column.get("comment")),
            }
            for column in found.get_columns(name, schema=key)
        ]
        primary = found.get_pk_constraint(name, schema=key)
        # PostgreSQL's keys come from _pg_foreign_keys (M-1); SQLite has one schema, so its reflection is exact.
        sqlite = connection.engine == "sqlite"
        outgoing = [
            {
                "name": fk.get("name"),
                "columns": list(fk["constrained_columns"]),
                "referred_schema": fk.get("referred_schema") or schema,
                "referred_table": fk["referred_table"],
                "referred_columns": list(fk["referred_columns"]),
            }
            for fk in (found.get_foreign_keys(name, schema=key) if sqlite else [])
        ]
        return {
            "columns": columns,
            "primary_key": {"name": primary.get("name"), "columns": list(primary.get("constrained_columns") or [])},
            "foreign_keys": outgoing,
            "referenced_by": self._incoming(connection, found, schema, name) if sqlite else [],
            "unique_constraints": [
                {"name": u.get("name"), "columns": list(u["column_names"])}
                for u in _optional(lambda: found.get_unique_constraints(name, schema=key))
            ],
            "check_constraints": [
                {"name": c.get("name"), "sqltext": _text(c.get("sqltext"))}
                for c in _optional(lambda: found.get_check_constraints(name, schema=key))
            ],
            "indexes": [
                {
                    "name": i.get("name"),
                    "columns": [c for c in i["column_names"] if c is not None],
                    "unique": i["unique"],
                }
                for i in found.get_indexes(name, schema=key)
            ],
        }

    def _incoming(
        self, connection: OpenConnection, found: sqlalchemy.Inspector, schema: str, name: str
    ) -> list[dict[str, Any]]:
        incoming: list[dict[str, Any]] = []
        for other in self._schemas(connection, found):
            key = None if connection.engine == "sqlite" else other
            for (_, table), keys in found.get_multi_foreign_keys(schema=key).items():
                for fk in keys:
                    if fk["referred_table"] == name and (fk.get("referred_schema") or other) == schema:
                        incoming.append(
                            {
                                "name": fk.get("name"),
                                "schema": other,
                                "table": table,
                                "columns": list(fk["constrained_columns"]),
                                "referred_columns": list(fk["referred_columns"]),
                            }
                        )
        return sorted(incoming, key=lambda item: (item["schema"], item["table"], str(item["name"])))

    @staticmethod
    def _pg_foreign_keys(
        connection: OpenConnection, schema: str, name: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        outgoing: list[dict[str, Any]] = []
        incoming: list[dict[str, Any]] = []
        rows = connection.fetch(_PG_FOREIGN_KEYS_SQL, {"schema": schema, "name": name})
        for key_name, from_schema, from_table, to_schema, to_table, columns, referred in rows:
            if (from_schema, from_table) == (schema, name):
                outgoing.append(
                    {
                        "name": key_name,
                        "columns": list(columns),
                        "referred_schema": to_schema,
                        "referred_table": to_table,
                        "referred_columns": list(referred),
                    }
                )
            if (to_schema, to_table) == (schema, name):
                incoming.append(
                    {
                        "name": key_name,
                        "schema": from_schema,
                        "table": from_table,
                        "columns": list(columns),
                        "referred_columns": list(referred),
                    }
                )
        return outgoing, incoming

    # Diagram (STU-023)

    def diagram(self, connection_id: object, schema: str) -> dict[str, Any]:
        """The tables of ``schema`` for the ER diagram: ``{connection_id, engine, schema, tables: [{name, kind,
        columns: [{name, type, nullable}], primary_key: [column], foreign_keys: [{name, columns, referred_schema,
        referred_table, referred_columns}]}]}``, sorted by name. A schema outside the catalog is
        ``studio.object_not_found``, one with more than :data:`MAX_DIAGRAM_TABLES` tables ``studio.diagram_too_large``.
        Three catalog reads for the whole schema (columns, primary keys, foreign keys), never one per table."""
        doc = self.catalog(connection_id)
        if schema not in doc["schemas"]:
            raise ExplorerError("studio.object_not_found")
        kinds = {
            str(item["name"]): str(item["kind"])
            for item in doc["objects"]
            if item["schema"] == schema and item["kind"] in _DIAGRAM_KINDS
        }
        if len(kinds) > MAX_DIAGRAM_TABLES:
            raise ExplorerError("studio.diagram_too_large")
        connection = self.registry.get(connection_id)
        sqlite = connection.engine == "sqlite"
        key = None if sqlite else schema
        names = sorted(kinds)

        def read(
            found: sqlalchemy.Inspector,
        ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
            if not names:
                return {}, {}, {}
            with warnings.catch_warnings():
                # A type SQLAlchemy does not know reflects as NullType with a warning (see ``columns``).
                warnings.simplefilter("ignore", sqlalchemy.exc.SAWarning)
                reflected = found.get_multi_columns(schema=key, filter_names=names)
            columns = {
                str(table): [
                    {
                        "name": str(column["name"]),
                        "type": _type_name(column["type"]),
                        "nullable": bool(column.get("nullable", True)),
                    }
                    for column in found_columns
                ]
                for (_, table), found_columns in reflected.items()
            }
            primary = {
                str(table): [str(n) for n in (pk or {}).get("constrained_columns") or []]
                for (_, table), pk in found.get_multi_pk_constraint(schema=key, filter_names=names).items()
            }
            outgoing: dict[str, list[dict[str, Any]]] = {}
            if sqlite:  # SQLite has one schema, so its reflection is exact; PostgreSQL's keys come from the catalog
                for (_, table), keys in found.get_multi_foreign_keys(schema=key, filter_names=names).items():
                    outgoing[str(table)] = [
                        {
                            "name": fk.get("name"),
                            "columns": list(fk["constrained_columns"]),
                            "referred_schema": fk.get("referred_schema") or schema,
                            "referred_table": fk["referred_table"],
                            "referred_columns": list(fk["referred_columns"]),
                        }
                        for fk in keys
                    ]
            return columns, primary, outgoing

        columns, primary, outgoing = self._reflect(connection, read)
        if not sqlite and names:
            for key_name, table, to_schema, to_table, key_columns, referred in connection.fetch(
                _PG_SCHEMA_FOREIGN_KEYS_SQL, {"schema": schema}
            ):
                outgoing.setdefault(str(table), []).append(
                    {
                        "name": key_name,
                        "columns": list(key_columns),
                        "referred_schema": to_schema,
                        "referred_table": to_table,
                        "referred_columns": list(referred),
                    }
                )
        return {
            "connection_id": connection.id,
            "engine": connection.engine,
            "schema": schema,
            "tables": [
                {
                    "name": name,
                    "kind": kinds[name],
                    "columns": columns.get(name, []),
                    "primary_key": primary.get(name, []),
                    "foreign_keys": outgoing.get(name, []),
                }
                for name in names
            ],
        }

    # Engine specifics

    def extensions(self, connection_id: object) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        if connection.engine != "postgresql":
            raise ExplorerError("studio.explorer_engine")
        rows = connection.fetch(_EXTENSIONS_SQL)
        return {
            "connection_id": connection.id,
            "extensions": [
                {
                    "name": name,
                    "version": version,
                    "schema": schema,
                    "default_version": default,
                    "update_available": default is not None and default != version,
                }
                for name, version, schema, default in rows
            ],
        }

    def sqlite_info(self, connection_id: object) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        if connection.engine != "sqlite" or connection.database_file is None:
            raise ExplorerError("studio.explorer_engine")
        pragmas = {name: connection.fetch(f"PRAGMA {name}")[0][0] for name in _SQLITE_PRAGMAS}
        if _HAS_TABLE_LIST:
            tables = [
                {"name": row[1], "type": row[2], "strict": bool(row[5]), "without_rowid": bool(row[4])}
                for row in connection.fetch("PRAGMA table_list")
                if row[0] == "main" and not str(row[1]).startswith("sqlite_")
            ]
        else:
            # PRAGMA table_list needs SQLite 3.37 (I-7); older ones do not know STRICT. index_info of a WITHOUT ROWID
            # table lists its primary key, of a rowid table nothing.
            tables = [
                {
                    "name": name,
                    "type": kind,
                    "strict": False,
                    "without_rowid": kind == "table"
                    and connection.fetch("SELECT count(*) FROM pragma_index_info(:name)", {"name": name})[0][0] > 0,
                }
                for name, kind in connection.fetch(
                    "SELECT name, type FROM sqlite_master "
                    "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite!_%' ESCAPE '!'"
                )
            ]
        info = os.stat(connection.database_file)
        return {
            "connection_id": connection.id,
            **pragmas,
            "foreign_keys": bool(pragmas["foreign_keys"]),
            "file_size": info.st_size,
            "file_mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "tables": sorted(tables, key=lambda item: str(item["name"])),
        }
