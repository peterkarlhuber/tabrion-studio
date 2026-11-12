"""DDL, row-level security policies and row estimates of the explorer's objects (03 section 2 B, STU-022).

Everything here reads, through ``OpenConnection.fetch`` (one checked statement in one ``Database.read()``), and only
for an object of the connection's catalog (``Explorer.known``, so ``studio.object_not_found`` otherwise). The
statements are fixed catalog queries with the schema and name as bound values; the one exception is SQLite's
``count(*)``, whose table name comes from the catalog and is quoted as an identifier.

* **DDL, PostgreSQL:** rebuilt from the catalog with the server's own ``pg_get_*def`` functions, in the form
  ``pg_dump --schema-only --no-owner --no-privileges`` writes it, statement by statement: ``CREATE TABLE`` (columns
  with type, default or generated expression, ``NOT NULL``, collation; the valid ``CHECK`` constraints; ``INHERITS``,
  ``PARTITION BY``, storage options), the identity sequence, ``ATTACH PARTITION``, comments, primary key, unique and
  exclusion constraints, ``NOT VALID`` checks, indexes (and their ``ATTACH PARTITION``), triggers, foreign keys,
  ``ENABLE``/``FORCE ROW LEVEL SECURITY`` and the policies. Views, materialized views and sequences likewise. Not
  included (as with pg_dump's own options left out): owner, privileges, tablespace, a table access method, foreign
  tables' column options, security labels, and functions (03: release S). Expressions are printed by the server
  under the session's ``search_path`` (Studio does not change it): a name on it is not schema-qualified, where
  pg_dump (``search_path = ''``) qualifies every name.
* **DDL, SQLite:** the stored text of ``sqlite_master`` (exactly what created the object), then its indexes and
  triggers.
* **Policies (PostgreSQL):** whether row-level security is on and forced, each policy (permissive or restrictive,
  command, roles, ``USING`` and ``WITH CHECK``), and whether the policies apply to the connection's own role (not
  for a superuser, a ``BYPASSRLS`` role, or the owner while RLS is not forced).
* **Row estimates:** PostgreSQL ``pg_class.reltuples`` (``None`` before the first ``VACUUM``/``ANALYZE``; a
  partitioned table sums its partitions). SQLite keeps no estimate; it counts on request (``count(*)``), under the
  timeout of :data:`COUNT_TIMEOUT_S` or the profile's, whichever is shorter.

Database text (definitions, expressions, comments) goes to the page as data and is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

import sqlalchemy
from sqlalchemy.dialects import sqlite as sqlite_dialect

from tabrion_studio.services.connections import OpenConnection
from tabrion_studio.services.explorer import Explorer, ExplorerError
from tabrion_studio.services.tabrion_compat import Cancel

__all__ = ["COUNT_TIMEOUT_S", "CountTimeout", "Definitions"]

#: The longest a SQLite ``count(*)`` may run (03 section 2 B: "nur auf Anfrage count(*) mit Timeout").
COUNT_TIMEOUT_S = 10.0
_CANCEL_ATTEMPTS = 30
_SQLITE = sqlite_dialect.dialect()
_CANCEL_INTERVAL_S = 0.1

_RELATION_SQL = (
    "SELECT c.relkind, c.relpersistence, c.relispartition, c.relrowsecurity, c.relforcerowsecurity, "
    "pg_catalog.pg_get_userbyid(c.relowner), "
    "CASE WHEN c.relkind = 'p' THEN pg_catalog.pg_get_partkeydef(c.oid) END, "
    "CASE WHEN c.relispartition THEN pg_catalog.pg_get_expr(c.relpartbound, c.oid) END, "
    "pg_catalog.obj_description(c.oid, 'pg_class'), "
    "ARRAY(SELECT o.option_name FROM pg_catalog.pg_options_to_table(c.reloptions) o), "
    "ARRAY(SELECT o.option_value FROM pg_catalog.pg_options_to_table(c.reloptions) o), "
    "ARRAY(SELECT pg_catalog.quote_ident(pn.nspname) || '.' || pg_catalog.quote_ident(p.relname) "
    "FROM pg_catalog.pg_inherits i JOIN pg_catalog.pg_class p ON p.oid = i.inhparent "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace WHERE i.inhrelid = c.oid ORDER BY i.inhseqno), "
    "pg_catalog.pg_has_role(c.relowner, 'USAGE'), "
    "pg_catalog.quote_ident(n.nspname) || '.' || pg_catalog.quote_ident(c.relname), c.relreplident "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name"
)
#: Besides the definition: the statistics target, storage (when not the type's), compression and options of each
#: column, and whether an inherited column's ``NOT NULL`` comes from a parent (pg_dump's per-column ``ALTER``s).
_COLUMNS_SQL = (
    "SELECT pg_catalog.quote_ident(a.attname), pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull, "
    "a.attidentity, a.attgenerated, pg_catalog.pg_get_expr(d.adbin, d.adrelid), a.attislocal, "
    "CASE WHEN a.attcollation <> t.typcollation "
    "THEN pg_catalog.quote_ident(cn.nspname) || '.' || pg_catalog.quote_ident(co.collname) END, "
    "pg_catalog.col_description(c.oid, a.attnum), "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_depend dep JOIN pg_catalog.pg_class s ON s.oid = dep.refobjid "
    "WHERE dep.classid = 'pg_catalog.pg_attrdef'::pg_catalog.regclass AND dep.objid = d.oid AND s.relkind = 'S'), "
    "a.attstattarget, CASE WHEN a.attstorage <> t.typstorage THEN a.attstorage END, a.attcompression, "
    "pg_catalog.array_to_string(a.attoptions, ', '), "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h JOIN pg_catalog.pg_attribute pa ON pa.attrelid = h.inhparent "
    "AND pa.attname = a.attname WHERE h.inhrelid = c.oid AND pa.attnotnull) "
    "FROM pg_catalog.pg_attribute a "
    "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
    "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
    "LEFT JOIN pg_catalog.pg_collation co ON co.oid = a.attcollation "
    "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid = co.collnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name AND a.attnum > 0 AND NOT a.attisdropped "
    "ORDER BY a.attnum"
)
#: The sequences a table owns: identity (``deptype = 'i'``) and ``serial``/``OWNED BY`` (``'a'``).
_OWNED_SEQUENCES_SQL = (
    "SELECT pg_catalog.quote_ident(a.attname), dep.deptype, "
    "pg_catalog.quote_ident(sn.nspname) || '.' || pg_catalog.quote_ident(s.relname), "
    "pg_catalog.format_type(q.seqtypid, NULL), q.seqstart, q.seqincrement, q.seqmin, q.seqmax, q.seqcache, "
    "q.seqcycle "
    "FROM pg_catalog.pg_depend dep "
    "JOIN pg_catalog.pg_class s ON s.oid = dep.objid AND s.relkind = 'S' "
    "JOIN pg_catalog.pg_namespace sn ON sn.oid = s.relnamespace "
    "JOIN pg_catalog.pg_sequence q ON q.seqrelid = s.oid "
    "JOIN pg_catalog.pg_class c ON c.oid = dep.refobjid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = dep.refobjsubid "
    "WHERE dep.classid = 'pg_catalog.pg_class'::pg_catalog.regclass "
    "AND dep.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass AND dep.deptype IN ('i', 'a') "
    "AND n.nspname = :schema AND c.relname = :name ORDER BY a.attnum"
)
_SEQUENCE_SQL = (
    "SELECT pg_catalog.format_type(q.seqtypid, NULL), q.seqstart, q.seqincrement, q.seqmin, q.seqmax, "
    "q.seqcache, q.seqcycle, "
    "(SELECT pg_catalog.quote_ident(tn.nspname) || '.' || pg_catalog.quote_ident(t.relname) || '.' "
    "|| pg_catalog.quote_ident(a.attname) || '|' || dep.deptype::text "
    "FROM pg_catalog.pg_depend dep JOIN pg_catalog.pg_class t ON t.oid = dep.refobjid "
    "JOIN pg_catalog.pg_namespace tn ON tn.oid = t.relnamespace "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attnum = dep.refobjsubid "
    "WHERE dep.classid = 'pg_catalog.pg_class'::pg_catalog.regclass AND dep.objid = s.oid "
    "AND dep.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass AND dep.deptype IN ('i', 'a') LIMIT 1) "
    "FROM pg_catalog.pg_class s JOIN pg_catalog.pg_namespace n ON n.oid = s.relnamespace "
    "JOIN pg_catalog.pg_sequence q ON q.seqrelid = s.oid "
    "WHERE n.nspname = :schema AND s.relname = :name"
)
_CONSTRAINTS_SQL = (
    "SELECT pg_catalog.quote_ident(con.conname), con.contype, pg_catalog.pg_get_constraintdef(con.oid), "
    "con.convalidated, con.conislocal, con.conparentid OPERATOR(pg_catalog.<>) 0, "
    "pg_catalog.obj_description(con.oid, 'pg_constraint') "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class c ON c.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name AND con.contype IN ('c', 'p', 'u', 'x', 'f') "
    "ORDER BY con.conname"
)
_INDEXES_SQL = (
    "SELECT pg_catalog.pg_get_indexdef(i.indexrelid), "
    "pg_catalog.quote_ident(n.nspname) || '.' || pg_catalog.quote_ident(ic.relname), "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_constraint con WHERE con.conindid = i.indexrelid "
    "AND con.conrelid = i.indrelid AND con.contype IN ('p', 'u', 'x')), "
    "(SELECT pg_catalog.quote_ident(pn.nspname) || '.' || pg_catalog.quote_ident(p.relname) "
    "FROM pg_catalog.pg_inherits h JOIN pg_catalog.pg_class p ON p.oid = h.inhparent "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace WHERE h.inhrelid = i.indexrelid LIMIT 1), "
    "pg_catalog.obj_description(i.indexrelid, 'pg_class'), pg_catalog.quote_ident(ic.relname), i.indisclustered, "
    "i.indisreplident "
    "FROM pg_catalog.pg_index i "
    "JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid "
    "JOIN pg_catalog.pg_class c ON c.oid = i.indrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name ORDER BY ic.relname"
)
_TRIGGERS_SQL = (
    "SELECT pg_catalog.pg_get_triggerdef(t.oid), pg_catalog.quote_ident(t.tgname), t.tgenabled "
    "FROM pg_catalog.pg_trigger t "
    "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name AND NOT t.tgisinternal AND t.tgparentid OPERATOR(pg_catalog.=) 0 "
    "ORDER BY t.tgname"
)
#: ``roles``: ``NULL`` for ``PUBLIC`` (``{0}``); the names as identifiers (DDL) and as they are (the policy list).
_POLICIES_SQL = (
    "SELECT pg_catalog.quote_ident(p.polname), p.polname, p.polpermissive, p.polcmd, "
    "CASE WHEN p.polroles OPERATOR(pg_catalog.=) '{0}' THEN NULL ELSE ARRAY(SELECT pg_catalog.quote_ident(r.rolname) "
    "FROM pg_catalog.pg_roles r WHERE r.oid = ANY (p.polroles)) END, "
    "CASE WHEN p.polroles OPERATOR(pg_catalog.=) '{0}' THEN NULL ELSE ARRAY(SELECT r.rolname::text "
    "FROM pg_catalog.pg_roles r WHERE r.oid = ANY (p.polroles) ORDER BY r.rolname) END, "
    "pg_catalog.pg_get_expr(p.polqual, p.polrelid), pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) "
    "FROM pg_catalog.pg_policy p "
    "JOIN pg_catalog.pg_class c ON c.oid = p.polrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name ORDER BY p.polname"
)
#: Rules other than a view's own ``_RETURN`` rule, with whether each is enabled.
_RULES_SQL = (
    "SELECT pg_catalog.pg_get_ruledef(r.oid), pg_catalog.quote_ident(r.rulename), r.ev_enabled "
    "FROM pg_catalog.pg_rewrite r "
    "JOIN pg_catalog.pg_class c ON c.oid = r.ev_class "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = :name AND r.rulename <> '_RETURN' ORDER BY r.rulename"
)
_VIEW_SQL = (
    "SELECT pg_catalog.pg_get_viewdef(c.oid) FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema AND c.relname = :name"
)
_FOREIGN_SQL = (
    "SELECT pg_catalog.quote_ident(s.srvname), "
    "ARRAY(SELECT pg_catalog.quote_ident(o.option_name) || ' ' || pg_catalog.quote_literal(o.option_value) "
    "FROM pg_catalog.pg_options_to_table(f.ftoptions) o) "
    "FROM pg_catalog.pg_foreign_table f "
    "JOIN pg_catalog.pg_foreign_server s ON s.oid = f.ftserver "
    "JOIN pg_catalog.pg_class c ON c.oid = f.ftrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema AND c.relname = :name"
)
_ROLE_SQL = (
    "SELECT r.rolname::text, r.rolsuper, r.rolbypassrls FROM pg_catalog.pg_roles r WHERE r.rolname = current_user"
)
#: A partitioned table has no estimate of its own (``-1``) unless it was analyzed itself; it gets the sum of its
#: partitions' estimates when each of them has one.
_ESTIMATES_SQL = (
    "SELECT n.nspname::text, c.relname::text, "
    "CASE WHEN c.reltuples OPERATOR(pg_catalog.>=) 0 THEN c.reltuples::pg_catalog.int8 "
    "WHEN c.relkind OPERATOR(pg_catalog.=) 'p' THEN (SELECT CASE WHEN pg_catalog.bool_and(l.reltuples "
    "OPERATOR(pg_catalog.>=) 0) THEN pg_catalog.sum(l.reltuples)::pg_catalog.int8 END "
    "FROM pg_catalog.pg_partition_tree(c.oid) t JOIN pg_catalog.pg_class l ON l.oid = t.relid WHERE t.isleaf) END "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'm') AND NOT c.relispartition AND n.nspname !~ '^pg_' "
    "AND n.nspname <> 'information_schema' AND (CAST(:schema AS text) IS NULL OR n.nspname = :schema) "
    "ORDER BY n.nspname, c.relname"
)
_SQLITE_OBJECTS_SQL = (
    "SELECT type, name, sql FROM sqlite_master WHERE (name = :name OR tbl_name = :name) AND sql IS NOT NULL "
    "ORDER BY CASE WHEN name = :name THEN 0 WHEN type = 'index' THEN 1 ELSE 2 END, name"
)

_COMMANDS = {"r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE", "*": "ALL"}
_TRIGGER_STATES = {"D": "DISABLE TRIGGER", "R": "ENABLE REPLICA TRIGGER", "A": "ENABLE ALWAYS TRIGGER"}
_RULE_STATES = {"D": "DISABLE RULE", "R": "ENABLE REPLICA RULE", "A": "ENABLE ALWAYS RULE"}
_STORAGE = {"p": "PLAIN", "e": "EXTERNAL", "m": "MAIN", "x": "EXTENDED"}
_COMPRESSION = {"p": "pglz", "l": "lz4"}
_REPLICA_IDENTITY = {"n": "NOTHING", "f": "FULL"}
_COMMENT_KINDS = {
    "table": "TABLE",
    "view": "VIEW",
    "materialized_view": "MATERIALIZED VIEW",
    "sequence": "SEQUENCE",
    "foreign_table": "FOREIGN TABLE",
}
_TYPE_BOUNDS = {
    "smallint": (-(2**15), 2**15 - 1),
    "integer": (-(2**31), 2**31 - 1),
    "bigint": (-(2**63), 2**63 - 1),
}


class CountTimeout(Exception):
    """A ``count(*)`` ran past its timeout and was canceled (the answer is ``db.timeout``)."""


def _count_sql(name: str) -> str:
    """``SELECT count(*)`` of a SQLite table or view of the catalog, its name always a quoted identifier (also a name
    SQLAlchemy does not take for a keyword, such as ``nothing``; L-1 of the review)."""
    table = sqlalchemy.table(sqlalchemy.sql.quoted_name(name, quote=True))
    statement = sqlalchemy.select(sqlalchemy.func.count()).select_from(table)
    return str(statement.compile(dialect=_SQLITE))


def _literal(text: str) -> str:
    """A string literal as pg_dump writes it (``standard_conforming_strings = on``)."""
    return "'" + text.replace("'", "''") + "'"


@dataclass
class _Sequence:
    type_name: str
    start: int
    increment: int
    minimum: int
    maximum: int
    cache: int
    cycle: bool

    def options(self, *, identity: bool) -> list[str]:
        """The option lines of ``CREATE SEQUENCE`` (and of an identity), as pg_dump writes them."""
        low, high = _TYPE_BOUNDS.get(self.type_name, _TYPE_BOUNDS["bigint"])
        default_min, default_max = (1, high) if self.increment > 0 else (low, -1)
        lines = [] if identity or self.type_name == "bigint" else [f"AS {self.type_name}"]
        lines += [f"START WITH {self.start}", f"INCREMENT BY {self.increment}"]
        lines.append("NO MINVALUE" if self.minimum == default_min else f"MINVALUE {self.minimum}")
        lines.append("NO MAXVALUE" if self.maximum == default_max else f"MAXVALUE {self.maximum}")
        lines.append(f"CACHE {self.cache}")
        if self.cycle:
            lines.append("CYCLE")
        return lines


def _sequence(row: tuple[Any, ...]) -> _Sequence:
    type_name, start, increment, minimum, maximum, cache, cycle = row
    return _Sequence(str(type_name), int(start), int(increment), int(minimum), int(maximum), int(cache), bool(cycle))


@dataclass
class Definitions:
    """DDL, policies and row estimates of catalog objects. Blocking (call it off the event loop); thread-safe."""

    explorer: Explorer
    count_timeout_s: float = COUNT_TIMEOUT_S

    # DDL

    def ddl(self, connection_id: object, schema: str, name: str) -> dict[str, Any]:
        connection, kind = self.explorer.known(connection_id, schema, name)
        notes: list[str] = []
        owner: str | None = None
        if connection.engine == "sqlite":
            statements = self._sqlite_ddl(connection, name)
        else:
            statements, owner = self._pg_ddl(connection, kind, schema, name, notes)
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "kind": kind,
            "engine": connection.engine,
            "owner": owner,
            "statements": statements,
            "ddl": "".join(f"{statement};\n\n" for statement in statements).rstrip("\n") + "\n" if statements else "",
            "notes": notes,
        }

    @staticmethod
    def _sqlite_ddl(connection: OpenConnection, name: str) -> list[str]:
        rows = connection.fetch(_SQLITE_OBJECTS_SQL, {"name": name})
        return [str(sql).strip().rstrip(";") for _, _, sql in rows]

    def _pg_ddl(
        self, connection: OpenConnection, kind: str, schema: str, name: str, notes: list[str]
    ) -> tuple[list[str], str | None]:
        values = {"schema": schema, "name": name}
        relation = connection.fetch(_RELATION_SQL, values)
        if not relation:
            self.explorer.forget(connection.id)
            raise ExplorerError("studio.object_not_found")
        (
            relkind,
            persistence,
            is_partition,
            rls,
            rls_forced,
            owner,
            partition_key,
            bound,
            comment,
            option_names,
            option_values,
            parents,
            _mine,
            target,
            replica_identity,
        ) = relation[0]
        options = dict(zip(option_names or [], option_values or [], strict=False))
        if relkind == "S":
            return self._pg_sequence(connection, target, values, comment, notes), owner
        if relkind in ("v", "m"):
            return self._pg_view(connection, relkind, target, values, options, comment), owner
        statements: list[str] = []
        columns = connection.fetch(_COLUMNS_SQL, values)
        constraints = connection.fetch(_CONSTRAINTS_SQL, values)
        foreign = relkind == "f"
        alter = "ALTER FOREIGN TABLE" if foreign else "ALTER TABLE"
        lines: list[str] = []
        separate_defaults: list[tuple[str, str]] = []
        column_alters: list[str] = []
        for row in columns:
            column, type_name, not_null, _identity, generated, default, local, collation, _, from_sequence = row[:10]
            column_alters += self._column_alters(target, row, is_partition=bool(is_partition))
            if not (local or is_partition):
                # An inherited column is the parent's (pg_dump leaves it out of the child too); its default is set
                # on the child after the table, as pg_dump does.
                if default is not None and not generated:
                    separate_defaults.append((column, str(default)))
                continue
            line = f"    {column} {type_name}"
            if default is not None and generated:
                # PostgreSQL 18: "v" is a virtual generated column, written without STORED (TD-035: untested).
                line += f" GENERATED ALWAYS AS ({default})" + (" STORED" if generated == "s" else "")
            elif default is not None and from_sequence:
                separate_defaults.append((column, str(default)))  # after the sequence, as pg_dump does
            elif default is not None:
                line += f" DEFAULT {default}"
            if not_null:
                line += " NOT NULL"
            if collation is not None:
                line += f" COLLATE {collation}"
            lines.append(line)
        for con_name, contype, definition, validated, local, _, _ in constraints:
            if contype == "c" and validated and (local or is_partition):
                lines.append(f"    CONSTRAINT {con_name} {definition}")
        unlogged = "UNLOGGED " if persistence == "u" else ""
        create = f"CREATE {'FOREIGN ' if foreign else unlogged}TABLE {target} (\n" + ",\n".join(lines) + "\n)"
        if parents and not is_partition:
            create += f"\nINHERITS ({', '.join(parents)})"
        if partition_key is not None:
            create += f"\nPARTITION BY {partition_key}"
        if foreign:
            server = connection.fetch(_FOREIGN_SQL, values)
            if server:
                create += f"\nSERVER {server[0][0]}"
                if server[0][1]:
                    create += "\nOPTIONS (\n    " + ",\n    ".join(server[0][1]) + "\n)"
            notes.append("Column options of a foreign table are not shown.")
        if options:
            create += f"\nWITH ({self._options(options)})"
        statements.append(create)
        if rls_forced:
            statements.append(f"ALTER TABLE ONLY {target} FORCE ROW LEVEL SECURITY")
        for column, deptype, sequence, *facts in connection.fetch(_OWNED_SEQUENCES_SQL, values):
            found = _sequence(tuple(facts))
            if deptype == "i":
                identity = next(row[3] for row in columns if row[0] == column)
                how = "ALWAYS" if identity == "a" else "BY DEFAULT"
                statements.append(
                    f"ALTER TABLE {target} ALTER COLUMN {column} ADD GENERATED {how} AS IDENTITY (\n"
                    f"    SEQUENCE NAME {sequence}\n    " + "\n    ".join(found.options(identity=True)) + "\n)"
                )
            else:
                notes.append(f"Column {column} owns the sequence {sequence}; its DDL is the sequence's.")
        for column, default in separate_defaults:
            statements.append(f"ALTER TABLE ONLY {target} ALTER COLUMN {column} SET DEFAULT {default}")
        statements += column_alters
        if relkind in ("r", "p") and replica_identity in _REPLICA_IDENTITY:
            statements.append(f"ALTER TABLE ONLY {target} REPLICA IDENTITY {_REPLICA_IDENTITY[replica_identity]}")
        if is_partition and parents and bound is not None:
            statements.append(f"ALTER TABLE ONLY {parents[0]} ATTACH PARTITION {target} {bound}")
        statements += self._comments(target, _COMMENT_KINDS.get(kind, "TABLE"), comment, columns)
        for con_name, contype, definition, validated, _, _, con_comment in constraints:
            if contype in ("p", "u", "x"):
                statements.append(f"{alter} ONLY {target}\n    ADD CONSTRAINT {con_name} {definition}")
            elif contype == "c" and not validated:
                statements.append(f"{alter} {target}\n    ADD CONSTRAINT {con_name} {definition}")
            if con_comment is not None and contype != "f":
                statements.append(f"COMMENT ON CONSTRAINT {con_name} ON {target} IS {_literal(con_comment)}")
        attached: list[str] = []
        for row in connection.fetch(_INDEXES_SQL, values):
            definition, index, backs_constraint, parent, index_comment, index_name, clustered, replica = row
            if not backs_constraint:
                statements.append(str(definition))
            if clustered:
                statements.append(f"ALTER TABLE {target} CLUSTER ON {index_name}")
            if replica and replica_identity == "i":
                statements.append(f"ALTER TABLE ONLY {target} REPLICA IDENTITY USING INDEX {index_name}")
            if parent is not None:
                attached.append(f"ALTER INDEX {parent} ATTACH PARTITION {index}")
            if index_comment is not None:
                statements.append(f"COMMENT ON INDEX {index} IS {_literal(index_comment)}")
        statements += attached
        for definition, trigger, enabled in connection.fetch(_TRIGGERS_SQL, values):
            statements.append(str(definition))
            if enabled in _TRIGGER_STATES:
                statements.append(f"ALTER TABLE {target} {_TRIGGER_STATES[enabled]} {trigger}")
        for con_name, contype, definition, _, _, inherited, con_comment in constraints:
            if contype == "f" and not inherited:
                only = "" if relkind == "p" else "ONLY "
                statements.append(f"ALTER TABLE {only}{target}\n    ADD CONSTRAINT {con_name} {definition}")
                if con_comment is not None:
                    statements.append(f"COMMENT ON CONSTRAINT {con_name} ON {target} IS {_literal(con_comment)}")
        statements += self._rules(connection, target, values)
        if rls:
            statements.append(f"ALTER TABLE {target} ENABLE ROW LEVEL SECURITY")
        for quoted, _, permissive, command, roles, _, using, check in connection.fetch(_POLICIES_SQL, values):
            statements.append(self._policy(target, quoted, permissive, command, roles, using, check))
        return statements, owner

    @staticmethod
    def _column_alters(target: str, row: tuple[Any, ...], *, is_partition: bool) -> list[str]:
        """pg_dump's per-column ``ALTER``s: a child's own ``NOT NULL`` on an inherited column, the statistics target,
        options, storage and compression."""
        column, not_null, local = row[0], row[2], row[6]
        statistics, storage, compression, options, parent_not_null = row[10:15]
        prefix = f"ALTER TABLE ONLY {target} ALTER COLUMN {column}"
        alters = []
        if not local and not is_partition and not_null and not parent_not_null:
            alters.append(f"{prefix} SET NOT NULL")
        if isinstance(statistics, int) and statistics >= 0:
            alters.append(f"{prefix} SET STATISTICS {statistics}")
        if options:
            alters.append(f"{prefix} SET ({options})")
        if storage in _STORAGE:
            alters.append(f"{prefix} SET STORAGE {_STORAGE[storage]}")
        if compression in _COMPRESSION:
            alters.append(f"{prefix} SET COMPRESSION {_COMPRESSION[compression]}")
        return alters

    @staticmethod
    def _rules(connection: OpenConnection, target: str, values: dict[str, str]) -> list[str]:
        """``CREATE RULE`` (they change what DML does) and a rule's state when it is not the default."""
        statements = []
        for definition, rule, enabled in connection.fetch(_RULES_SQL, values):
            statements.append(str(definition).rstrip().rstrip(";"))
            if enabled in _RULE_STATES:
                statements.append(f"ALTER TABLE {target} {_RULE_STATES[enabled]} {rule}")
        return statements

    @staticmethod
    def _options(options: dict[str, str]) -> str:
        return ", ".join(f"{name}={_literal(str(value))}" for name, value in options.items())

    @staticmethod
    def _comments(
        target: str, keyword: str, comment: object, columns: list[tuple[Any, ...]] | None = None
    ) -> list[str]:
        statements = [] if comment is None else [f"COMMENT ON {keyword} {target} IS {_literal(str(comment))}"]
        for row in columns or []:
            if row[8] is not None:
                statements.append(f"COMMENT ON COLUMN {target}.{row[0]} IS {_literal(str(row[8]))}")
        return statements

    @staticmethod
    def _policy(
        target: str, name: str, permissive: bool, command: str, roles: list[str] | None, using: Any, check: Any
    ) -> str:
        text = f"CREATE POLICY {name} ON {target}"
        if not permissive:
            text += " AS RESTRICTIVE"
        if command != "*":
            text += f" FOR {_COMMANDS.get(command, 'ALL')}"
        if roles:
            text += f" TO {', '.join(roles)}"
        if using is not None:
            text += f" USING ({using})"
        if check is not None:
            text += f" WITH CHECK ({check})"
        return text

    def _pg_view(
        self,
        connection: OpenConnection,
        relkind: str,
        target: str,
        values: dict[str, str],
        options: dict[str, str],
        comment: object,
    ) -> list[str]:
        rows = connection.fetch(_VIEW_SQL, values)
        definition = str(rows[0][0]).rstrip().rstrip(";") if rows and rows[0][0] is not None else ""
        check_option = options.pop("check_option", None)
        with_options = f" WITH ({self._options(options)})" if options else ""
        if relkind == "m":
            # pg_dump puts a materialized view's options on a line of their own (a view's on the first line).
            create = f"CREATE MATERIALIZED VIEW {target}{with_options.replace(' WITH', chr(10) + 'WITH', 1)} AS\n"
            create += f"{definition}\n  WITH NO DATA"
            keyword = "MATERIALIZED VIEW"
        else:
            create = f"CREATE VIEW {target}{with_options} AS\n{definition}"
            if check_option is not None:
                create += f"\n  WITH {str(check_option).upper()} CHECK OPTION"
            keyword = "VIEW"
        columns = connection.fetch(_COLUMNS_SQL, values)
        statements = [create, *self._comments(target, keyword, comment, columns)]
        if relkind == "v":
            # A view's column default (what an INSERT through the view stores), as pg_dump writes it (E-1).
            for row in columns:
                if row[5] is not None:
                    statements.append(f"ALTER TABLE ONLY {target} ALTER COLUMN {row[0]} SET DEFAULT {row[5]}")
        if relkind == "m":
            # A materialized view keeps per-column settings and a clustering index, as a table does.
            for row in columns:
                statements += self._column_alters(target, row, is_partition=False)
            for row in connection.fetch(_INDEXES_SQL, values):
                index_definition, index, backs, _, index_comment, index_name, clustered, _ = row
                if not backs:
                    statements.append(str(index_definition))
                if index_comment is not None:
                    statements.append(f"COMMENT ON INDEX {index} IS {_literal(index_comment)}")
                if clustered:
                    statements.append(f"ALTER TABLE {target} CLUSTER ON {index_name}")
        for trigger_definition, trigger, enabled in connection.fetch(_TRIGGERS_SQL, values):
            statements.append(str(trigger_definition))
            if enabled in _TRIGGER_STATES:
                statements.append(f"ALTER TABLE {target} {_TRIGGER_STATES[enabled]} {trigger}")
        statements += self._rules(connection, target, values)
        return statements

    @staticmethod
    def _pg_sequence(
        connection: OpenConnection, target: str, values: dict[str, str], comment: object, notes: list[str]
    ) -> list[str]:
        rows = connection.fetch(_SEQUENCE_SQL, values)
        if not rows:
            return []
        *facts, owned = rows[0]
        found = _sequence(tuple(facts))
        if owned is not None:
            column, _, deptype = str(owned).rpartition("|")
            if deptype == "i":
                notes.append(f"The identity sequence of {column}: its DDL is part of the table's.")
                return []
        statements = [f"CREATE SEQUENCE {target}\n    " + "\n    ".join(found.options(identity=False))]
        if owned is not None:
            statements.append(f"ALTER SEQUENCE {target} OWNED BY {str(owned).rpartition('|')[0]}")
        if comment is not None:
            statements.append(f"COMMENT ON SEQUENCE {target} IS {_literal(str(comment))}")
        return statements

    # Policies

    def policies(self, connection_id: object, schema: str, name: str) -> dict[str, Any]:
        connection, kind = self.explorer.known(connection_id, schema, name)
        if connection.engine != "postgresql":
            raise ExplorerError("studio.explorer_engine")
        values = {"schema": schema, "name": name}
        relation = connection.fetch(_RELATION_SQL, values)
        if not relation:
            self.explorer.forget(connection.id)
            raise ExplorerError("studio.object_not_found")
        rls, forced, owner, mine = relation[0][3], relation[0][4], relation[0][5], relation[0][12]
        role_rows = connection.fetch(_ROLE_SQL)
        role, superuser, bypass = role_rows[0] if role_rows else (None, False, False)
        policies = [
            {
                "name": raw,
                "permissive": bool(permissive),
                "command": _COMMANDS.get(command, "ALL"),
                "roles": list(roles) if roles is not None else ["public"],
                "using": using,
                "with_check": check,
            }
            for _, raw, permissive, command, _, roles, using, check in connection.fetch(_POLICIES_SQL, values)
        ]
        exempt = bool(superuser) or bool(bypass) or (bool(mine) and not forced)
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "kind": kind,
            "owner": owner,
            "rls_enabled": bool(rls),
            "rls_forced": bool(forced),
            "current_role": {
                "name": role,
                "superuser": bool(superuser),
                "bypass_rls": bool(bypass),
                "owner": bool(mine),
            },
            "rls_applies": bool(rls) and not exempt,
            "policies": policies,
        }

    # Row estimates and counts

    def row_estimates(self, connection_id: object, schema: str | None = None) -> dict[str, Any]:
        """PostgreSQL: ``pg_class.reltuples`` of every table and materialized view (of ``schema``); SQLite: none."""
        connection = self.explorer.registry.get(connection_id)
        if connection.engine != "postgresql":
            raise ExplorerError("studio.explorer_engine")
        rows = connection.fetch(_ESTIMATES_SQL, {"schema": schema})
        return {
            "connection_id": connection.id,
            "schema": schema,
            "estimates": [{"schema": s, "name": n, "estimate": e} for s, n, e in rows],
        }

    def row_count(self, connection_id: object, schema: str, name: str) -> dict[str, Any]:
        """SQLite: ``count(*)`` of a table or view, canceled after the timeout (:class:`CountTimeout`)."""
        connection, kind = self.explorer.known(connection_id, schema, name)
        if connection.engine != "sqlite":
            raise ExplorerError("studio.explorer_engine")
        if kind not in ("table", "view"):
            raise ExplorerError("studio.object_not_found")
        timeout = min(self.count_timeout_s, connection.timeout_s)
        hook, finished = Cancel(), threading.Event()
        timer = threading.Timer(timeout, _expire, args=(hook, finished))
        timer.daemon = True
        started = time.monotonic()
        timer.start()
        try:
            rows = connection.fetch(_count_sql(name), cancel=hook)
        except Exception:
            if hook.requested:
                raise CountTimeout from None
            raise
        finally:
            finished.set()
            timer.cancel()
        return {
            "connection_id": connection.id,
            "schema": schema,
            "name": name,
            "kind": kind,
            "count": int(rows[0][0]),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }


def _expire(hook: Cancel, finished: threading.Event) -> None:
    """The timeout: cancel, and repeat it every 0.1 s (for 3 s) until the statement stops."""
    for _ in range(_CANCEL_ATTEMPTS):
        hook.cancel()
        if finished.wait(_CANCEL_INTERVAL_S):
            return
