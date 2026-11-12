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
| `GET /api/connections/{id}/tables/{schema}/{name}/ddl` | `statements`, `ddl` (the statements joined with `;`), `kind`, `engine`, `owner` (PostgreSQL), `notes` (STU-022, below) |
| `GET /api/connections/{id}/tables/{schema}/{name}/policies` | PostgreSQL: `rls_enabled`, `rls_forced`, `owner`, `current_role` (`name`, `superuser`, `bypass_rls`, `owner`), `rls_applies`, `policies` (`name`, `permissive`, `command`, `roles`, `using`, `with_check`) |
| `GET /api/connections/{id}/tables/{schema}/{name}/row-count` | SQLite: `count` and `elapsed_ms` of `count(*)` on a table or view, on request; `db.timeout` after 10 seconds or the profile's timeout, whichever is shorter |
| `GET /api/connections/{id}/row-estimates[?schema=]` | PostgreSQL: `estimates` (`schema`, `name`, `estimate`) of every table and materialized view, partitions counted in their table |

The `ddl`, `policies` and `row-count` routes also take `/api/connections/{id}/<route>?schema=&name=`, for a name with
`/`. The other engine gets `studio.explorer_engine` from the engine-specific ones.

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
into the referencing table's own schema (M-1 of `stu-010-review.md`). The copies PostgreSQL keeps of a partitioned
table's key for each partition (`conparentid` set) are left out, so the key shows once, on the partitioned table
(N-1). SQLite has one schema, so its reflection is exact.

**Visibility:** like `psql \d`, the catalog and the detail show every schema, table, column and comment that
PostgreSQL's catalogs show the role, also objects it has no `USAGE` or `SELECT` privilege on. Reading their rows
still fails in the database.

**A busy connection:** a connection has two database sessions, shared by its queries (at most two), the explorer and
the health check. When both are in use, a request waits up to 10 seconds and then gets `studio.connection_busy` (503),
not a database error (TD-013).

**SQLite facts** use `PRAGMA table_list`, which needs SQLite 3.37 or later; with an older library Studio lists the
tables from `sqlite_master` without the `STRICT` and `WITHOUT ROWID` flags.

## DDL, policies and row estimates (STU-022)

`services/definitions.py` answers these for an object of the catalog only (`Explorer.known`, the same check as the
table detail). Every statement is a fixed catalog query with the schema and name as bound values, sent through
`OpenConnection.fetch`: the statement check of STU-009 (`check_statement`, only `SELECT` here) and one
`Database.read()` each. SQLite's `count(*)` is the one statement with a name in its text: the name comes from the
catalog and SQLAlchemy's SQLite dialect quotes it as an identifier.

**PostgreSQL DDL** is rebuilt from the catalog with the server's own functions (`format_type`, `pg_get_expr`,
`pg_get_constraintdef`, `pg_get_indexdef`, `pg_get_triggerdef`, `pg_get_viewdef`, `pg_get_partkeydef`), in the form of
`pg_dump --schema-only --no-owner --no-privileges`, one statement per entry of `statements` (without the `;`):

| Object | Statements |
|---|---|
| Table | `CREATE [UNLOGGED] TABLE` with columns (type, default or generated expression, `NOT NULL`, collation) and the valid `CHECK` constraints, `INHERITS`, `PARTITION BY`, storage options; `FORCE ROW LEVEL SECURITY`; the identity column's `ADD GENERATED ... AS IDENTITY`; a default that uses a sequence (`serial`) or an inherited column's default as `SET DEFAULT`; `ATTACH PARTITION`; comments on the table, its columns, constraints and indexes; primary key, unique and exclusion constraints and `NOT VALID` checks; indexes and their `ATTACH PARTITION`; triggers (and `DISABLE TRIGGER`); foreign keys; `ENABLE ROW LEVEL SECURITY`; the policies |
| View, materialized view | `CREATE VIEW` (options, `WITH ... CHECK OPTION`) or `CREATE MATERIALIZED VIEW ... WITH NO DATA` and its indexes; comments; `INSTEAD OF` triggers |
| Sequence | `CREATE SEQUENCE` with its options, `OWNED BY`, comment. An identity sequence has no statements of its own (a note names its column) |
| Foreign table | `CREATE FOREIGN TABLE` with `SERVER` and the table's `OPTIONS` (a note: column options are not shown) |

Owner and privileges are left out, as with pg_dump's options (the answer has `owner`), and so are functions (03:
release S): a trigger's function is not part of the table's DDL. Expressions are printed under the connection's
`search_path`, which Studio does not change, so a name on it is not schema-qualified where pg_dump qualifies every
name. The other gaps are TD-035.

**SQLite DDL** is the stored text of `sqlite_master` (exactly the statement that created the object), followed by the
object's indexes and triggers. An index SQLite made itself (for `UNIQUE` or a primary key) has no text and is not
listed.

**Policies** come from `pg_policy`: permissive or restrictive, the command (`ALL`, `SELECT`, `INSERT`, `UPDATE`,
`DELETE`), the roles (`public` for all), `USING` and `WITH CHECK` as the server prints them. `rls_applies` says whether
the policies bind the connection's own role: not for a superuser or a `BYPASSRLS` role, and not for the owner (or a
member of the owning role) unless RLS is forced. A view or a sequence has no policies.

**Row estimates:** PostgreSQL's `pg_class.reltuples`, `null` before the first `VACUUM` or `ANALYZE`. A partitioned table
has no estimate of its own unless it was analyzed itself; it gets the sum of its partitions' estimates when each of
them has one. The table detail uses the same rule. SQLite keeps no estimate: the page asks for `row-count`, which runs
`count(*)` and is canceled after 10 seconds or the profile's statement timeout, whichever is shorter (`db.timeout`).

**Acceptance (09 backlog STU-022):** the DDL equals `pg_dump --schema-only` for the reference apps. The reference apps
are tabrion's `examples/parts` (Alembic schema) and `examples/measurements` (the device file on SQLite and the
upstream table on PostgreSQL); `tests/support/reference_apps.py` copies their schemas. Each is created in a schema of
its own, and the sorted statements of Studio's DDL for every object of the schema must equal the statements of
pg_dump's output for that schema (`CREATE SCHEMA`, `SET` lines and functions left out). The test compares with the
recorded output in `tests/security/ddl_fixtures/` (pg_dump 16.15 against PostgreSQL 16.15) on every run, and with a live
`pg_dump` too when one on `PATH` can dump the server. pg_dump refuses a server of a newer major version: the CI job
runs PostgreSQL 17 with the runner's own client, so CI compares with the recorded output.

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
| `tests/security/test_definitions.py` (SQLite) | the DDL of the measurements device file equals its migrations and `sqlite_master`, the parts schema likewise; `count(*)` on request, a quoted name, unknown names refused, the timeout cancels a count that would run for minutes (`db.timeout`); only checked `SELECT` statements reach the database (spy); the routes with both name forms behind session and CSRF |
| `tests/security/test_definitions.py` (PostgreSQL) | STU-022 acceptance: the DDL of the parts, measurements and `extras` schemas equals `pg_dump --schema-only` statement by statement (recorded and live); identity and owned sequences, policies and `rls_applies` (forced, not forced, superuser), row estimates with partitions, a foreign table (superuser), the spy, the routes |
