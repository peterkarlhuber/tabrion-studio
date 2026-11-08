# Database explorer

The explorer is the backend of the tree and the detail tabs (03 section 2 B and 6, work package STU-010). Every route
only reads. Each request gets its connection through `ConnectionRegistry.get` and runs in one `Database.read()`
(see [Connections and read-only enforcement](connections.md)).

## Routes

| Route | Answer |
|---|---|
| `GET /api/connections/{id}/catalog[?refresh=true]` | `{connection_id, engine, schemas, objects: [{schema, name, kind}], built_at, cached}`, flat and sorted; `kind` is `table`, `view`, `materialized_view`, `sequence` or `foreign_table` |
| `GET /api/connections/{id}/tables/{schema}/{name}` | `columns` (name, type, nullable, default, comment), `primary_key`, `foreign_keys` (out), `referenced_by` (in), `unique_constraints`, `check_constraints`, `indexes`, `row_estimate` (PostgreSQL `pg_class.reltuples`) |
| `GET /api/connections/{id}/table?schema=&name=` | the same detail, for a name with `/` (the router reads `%2F` in a path as a separator) |
| `GET /api/connections/{id}/extensions` | PostgreSQL: name, version, schema, the default available version, `update_available` |
| `GET /api/connections/{id}/sqlite-info` | SQLite: `journal_mode`, `user_version`, `page_size`, `page_count`, `foreign_keys`, `file_size`, `file_mode`, and per table `strict` and `without_rowid` (`PRAGMA table_list`) |

The catalog is cached per connection until `refresh=true`. PostgreSQL's `pg_*` schemas and `information_schema` are
left out, and so a detail of their objects is refused too. SQLite has the single schema `main`. A cache entry goes
away when its connection closes (the registry tells the explorer).

A table detail is answered only for an object in the catalog. If the object is missing and the cached catalog is at
least 5 seconds old, the catalog is rebuilt once before Studio refuses with `studio.object_not_found`; a younger
catalog is not rebuilt, so unknown names cannot keep the connection busy with rebuilds. An object dropped after the
catalog was built is `studio.object_not_found` too, and drops the cached catalog. The schema and name from the path are only compared with the
catalog and passed as bound parameters. They never become part of a statement's text. Engine-specific routes on the
other engine give `studio.explorer_engine`.

**Foreign keys on PostgreSQL** come from one fixed query on `pg_constraint` with bound values (keys out of and into
the table, with their columns in order). SQLAlchemy's reflection takes the referenced schema from
`pg_get_constraintdef()`, which leaves it out for a table on the search path, so a key into `public` looked like one
into the referencing table's own schema (M-1 of `stu-010-review.md`). SQLite has one schema, so its reflection is
exact.

**Visibility:** like `psql \d`, the catalog and the detail show every schema, table, column and comment that
PostgreSQL's catalogs show the role, also objects it has no `USAGE` or `SELECT` privilege on. Reading their rows
still fails in the database.

**A busy connection:** a connection has two database sessions, shared by its queries (at most two), the explorer and
the health check. When both are in use, a request waits up to 10 seconds and then gets `studio.connection_busy` (503),
not a database error (TD-013).

**SQLite facts** use `PRAGMA table_list`, which needs SQLite 3.37 or later; with an older library Studio lists the
tables from `sqlite_master` without the `STRICT` and `WITHOUT ROWID` flags.

## Reflection on tabrion 0.1

The spec uses `Database.inspector()` (tabrion 0.2, TAB-033). Until then, `services/tabrion_compat.inspector` builds
a SQLAlchemy `Inspector` on the connection of a `Database.read()` transaction. The move to 0.2 changes that function
only (TODO TD-013). The extensions and the row estimate are fixed statements of Studio's own, with bound parameters,
sent through `OpenConnection.fetch`. The SQLite facts are reading pragmas, which Studio's authorizer allows.

Database values such as defaults, check texts and comments are the database's own text. The page shows them as text.
None of this is logged.

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_explorer.py` (SQLite) | the catalog of 503 tables in under a second (STU-010 acceptance: 500 tables < 1 s), the cache and `refresh`, the table detail (columns, default, PK, FK out and in, check, unique, index), unknown and hostile names refused, the SQLite facts (`STRICT`, `WITHOUT ROWID`, file mode), the registry check with a cached catalog, the routes behind session and CSRF |
| `tests/security/test_explorer.py` (PostgreSQL) | the catalog of 502 tables in a schema in under a second, materialized views and sequences, no system schemas; comments, FKs in and out, the row estimate, extensions |
