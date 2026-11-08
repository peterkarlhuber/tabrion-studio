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
* **SQLite facts:** ``journal_mode``, ``user_version``, ``page_size``, ``page_count``, ``foreign_keys``, the file's
  size and permission bits, and per table whether it is ``STRICT`` or ``WITHOUT ROWID`` (``PRAGMA table_list``).

Values of the database (defaults, check texts, comments) are the database's own text and go to the page as data; the
page renders them as text, never as HTML. Nothing of this is logged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
import stat
import threading
import time
from typing import Any, TypeVar

import sqlalchemy

from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection

__all__ = ["KINDS", "Explorer", "ExplorerError"]

_T = TypeVar("_T")

KINDS = ("table", "view", "materialized_view", "sequence", "foreign_table")
#: Schemas of the server itself (the catalog leaves them out; a table detail of them stays possible by name).
_SYSTEM_SCHEMAS = frozenset({"information_schema"})
_ESTIMATE_SQL = (
    "SELECT c.reltuples::bigint FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name"
)
_EXTENSIONS_SQL = (
    "SELECT e.extname, e.extversion, n.nspname, a.default_version "
    "FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
    "LEFT JOIN pg_catalog.pg_available_extensions a ON a.name = e.extname ORDER BY e.extname"
)
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


@dataclass
class Explorer:
    """Catalog cache and readers. Blocking (call it off the event loop); thread-safe."""

    registry: ConnectionRegistry
    clock: Callable[[], float] = time.time
    _cache: dict[str, _Cached] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Catalog

    def catalog(self, connection_id: object, *, refresh: bool = False) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        with self._lock:
            # Entries of connections that are gone are dropped here, so the cache cannot grow without bound.
            open_ids = {c.id for c in self.registry.list()} | {connection.id}
            for stale in [key for key in self._cache if key not in open_ids]:
                del self._cache[stale]
            cached = None if refresh else self._cache.get(connection.id)
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

    def _known(self, connection_id: object, schema: str, name: str) -> tuple[OpenConnection, str]:
        doc = self.catalog(connection_id)
        for item in doc["objects"]:
            if item["schema"] == schema and item["name"] == name:
                return self.registry.get(connection_id), str(item["kind"])
        # Created since the catalog was built? One fresh look before refusing.
        doc = self.catalog(connection_id, refresh=True)
        for item in doc["objects"]:
            if item["schema"] == schema and item["name"] == name:
                return self.registry.get(connection_id), str(item["kind"])
        raise ExplorerError("studio.object_not_found")

    # Table detail

    def table(self, connection_id: object, schema: str, name: str) -> dict[str, Any]:
        connection, kind = self._known(connection_id, schema, name)
        if kind == "sequence":
            return {"connection_id": connection.id, "schema": schema, "name": name, "kind": kind}
        key = None if connection.engine == "sqlite" else schema
        detail = connection.inspect(lambda found: self._table(connection, found, key, schema, name))
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
        outgoing = [
            {
                "name": fk.get("name"),
                "columns": list(fk["constrained_columns"]),
                "referred_schema": fk.get("referred_schema") or schema,
                "referred_table": fk["referred_table"],
                "referred_columns": list(fk["referred_columns"]),
            }
            for fk in found.get_foreign_keys(name, schema=key)
        ]
        return {
            "columns": columns,
            "primary_key": {"name": primary.get("name"), "columns": list(primary.get("constrained_columns") or [])},
            "foreign_keys": outgoing,
            "referenced_by": self._incoming(connection, found, schema, name),
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
        tables = [
            {"name": row[1], "type": row[2], "strict": bool(row[5]), "without_rowid": bool(row[4])}
            for row in connection.fetch("PRAGMA table_list")
            if row[0] == "main" and not str(row[1]).startswith("sqlite_")
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
