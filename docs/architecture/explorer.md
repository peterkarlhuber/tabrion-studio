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
catalog and Studio always quotes it as an identifier (`quoted_name(..., quote=True)`), keywords and lowercase names
included.

**Fixed catalog search path.** On PostgreSQL every catalog read of Studio's own (`fetch` with `catalog=True`, the
default, and the reflection of `tabrion_compat.inspector`) first sets `search_path` to `pg_catalog, pg_temp` for its
own read transaction (`set_config(..., true)`, `tabrion_compat.CATALOG_SEARCH_PATH`). This hardens Studio's fixed
catalog text against object resolution through user schemas: names in it resolve in `pg_catalog` only, and the
flagged queries also name `pg_catalog` operators and aggregates explicitly. The setting is transaction-local and is
not a write; the transaction stays read-only and the statement check is unchanged. User SQL, the viewer and the exports
never get it: they run under the session's `search_path`, so names in them mean what they mean to the user. Reading a
table's data runs code of the database's owner anyway (row-level security expressions and views run as the reader), so
this adds no new class of exposure. Alembic's version table is looked up with `catalog=False`, since it is found
through the session's `search_path`; its text is fully qualified. tabrion's diagnostics collectors are fully qualified
and stay on the session's path, since they report it. The queries made when a connection is opened (the SQLAlchemy
dialect's first-connect setup) run under the same fixed path, and the session's path is restored afterwards (TD-039,
[connections](connections.md)).

**PostgreSQL DDL** is rebuilt from the catalog with the server's own functions (`format_type`, `pg_get_expr`,
`pg_get_constraintdef`, `pg_get_indexdef`, `pg_get_triggerdef`, `pg_get_viewdef`, `pg_get_partkeydef`), in the form of
`pg_dump --schema-only --no-owner --no-privileges`, one statement per entry of `statements` (without the `;`):

| Object | Statements |
|---|---|
| Table | `CREATE [UNLOGGED] TABLE` with columns (type, default or generated expression, `NOT NULL`, collation) and the valid `CHECK` constraints, `INHERITS`, `PARTITION BY`, storage options; `FORCE ROW LEVEL SECURITY`; the identity column's `ADD GENERATED ... AS IDENTITY`; a default that uses a sequence (`serial`) or an inherited column's default as `SET DEFAULT`; `ATTACH PARTITION`; comments on the table, its columns, constraints and indexes; primary key, unique and exclusion constraints and `NOT VALID` checks; indexes and their `ATTACH PARTITION`; triggers (and `DISABLE TRIGGER`); foreign keys; `ENABLE ROW LEVEL SECURITY`; the policies; column settings (`SET NOT NULL` on a child only, `SET STATISTICS`, `SET (n_distinct ...)`, `SET STORAGE`, `SET COMPRESSION`); `REPLICA IDENTITY` (`FULL`, `NOTHING`, `USING INDEX`); `CLUSTER ON`; rules (`CREATE RULE`, and `DISABLE`/`ENABLE ALWAYS`/`ENABLE REPLICA RULE`) |
| View, materialized view | `CREATE VIEW` (options, `WITH ... CHECK OPTION`) and its column defaults (`SET DEFAULT`) or `CREATE MATERIALIZED VIEW` (its `WITH (...)` on a line of its own) `... WITH NO DATA`, its indexes, column settings (`SET STATISTICS`, `SET (...)`, `SET STORAGE`, `SET COMPRESSION`) and `CLUSTER ON`; comments; `INSTEAD OF` triggers; rules |
| Sequence | `CREATE SEQUENCE` with its options, `OWNED BY`, comment. An identity sequence has no statements of its own (a note names its column) |
| Foreign table | `CREATE FOREIGN TABLE` with `SERVER` and the table's `OPTIONS` (a note: column options are not shown) |

Owner and privileges are left out, as with pg_dump's options (the answer has `owner`), and so are functions (03:
release S): a trigger's function is not part of the table's DDL. Types and domains are not objects of the catalog
page and are not listed. Expressions are printed under the fixed catalog search path, so names outside `pg_catalog`
are schema-qualified as pg_dump prints them. The other gaps are TD-035.

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
its own, and once both together in `public`; the sorted statements (a multiset: duplicates count) of Studio's DDL for
every object of the schema must equal the statements of pg_dump's output for that schema (`CREATE SCHEMA`, `SET`
lines, functions, types and domains left out). The test compares with the
recorded output in `tests/security/ddl_fixtures/` (pg_dump 16.15 against PostgreSQL 16.15) on every run, and with a live
`pg_dump` too when one on `PATH` can dump the server. A missing pg_dump or one that refuses the server's version
skips only the live comparison; any other pg_dump failure fails the test, and `TABRION_STUDIO_TEST_LIVE_PG_DUMP=1`
makes the live comparison required. pg_dump refuses a server of a newer major version. A newer pg_dump writes some
objects in a form only its own major version accepts: Studio writes the DDL for the server it reads, as that server's
pg_dump does, so the test rewrites the one such form in these schemas (pg_dump 18's `NOT NULL col` entry of a child
table, which PostgreSQL 17 rejects, as `ALTER TABLE ONLY ... SET NOT NULL`) when the dump's header shows a pg_dump
18 or later against an older server (`server_statements`); everything else is compared as written. The CI job runs
PostgreSQL 17 with the runner's own client (a pg_dump 18 or later on `ubuntu-26.04`), so CI
compares with both the recorded output and that live dump. Locally the comparison was also run against PostgreSQL 17.10
with pg_dump 17.10 (no rewrite) and 18.4.

## Reflection on tabrion 0.1

The spec uses `Database.inspector()` (tabrion 0.2, TAB-033). Until then, `services/tabrion_compat.inspector` builds
a SQLAlchemy `Inspector` on the connection of a `Database.read()` transaction. The move to 0.2 changes that function
only (TODO TD-013). The extensions and the row estimate are fixed statements of Studio's own, with bound parameters,
sent through `OpenConnection.fetch`. The SQLite facts are reading pragmas, which Studio's authorizer allows.

Database values such as defaults, check texts and comments are the database's own text. The page shows them as text.
None of this is logged.

## The page

`/explorer` (`frontend/src/views/explorer/`) shows the catalog as a tree and the selected object's details. Until the
connection workspace (`/c/:conn/...`, 08 section 2) exists, the page has its own profile picker, like the migration and
diagnostics pages (TD-041). "Open" reads the profile list again (so the PROD check uses the profile as it is now) and
opens the profile's read-only connection (`POST /api/connections`; D-02 first for a PROD profile the user has not
confirmed in this page session, the confirmation STU-008 shares between the pages) and reads the catalog; the opened
connection becomes the header's connection. "Refresh" reads the catalog again with `refresh=true` on the same connection
and never opens one. A connection that is gone (`connection_not_found`, `connection_stale`) is forgotten, by the page
and by the header.

- **Tree:** a WAI-ARIA tree (`role="tree"`, flat rows with `aria-level`, `aria-posinset`, `aria-setsize`,
  `aria-expanded`; one tab stop). PostgreSQL: schema → type group → object, then the extensions. SQLite: the
  database file (its facts), then the type groups of `main`. Keys: ↑ ↓ Home End, → opens or goes to the first child,
  ← closes or goes to the parent (while a filter keeps the groups open, it goes to the parent), Enter or Space
  selects (selection does not follow the focus, so moving through 500 tables sends no detail request), a letter
  jumps to the next row starting with it. A group renders at most 500 objects (no virtualized list yet, TD-041);
  from 2 000 objects on the page says that it loads names only.
- **Filter:** a name prefix (case-insensitive; with a dot, a prefix of `schema.name`) and an object type (03 section
  2 B). Matching groups open; a status line counts the matches. ⌘P / Ctrl+P focuses the filter on this page.
- **Details:** tabs Columns, Constraints, Indexes, Relationships and, on SQLite, the table's `STRICT` and
  `WITHOUT ROWID`. A related table is a button that selects it and moves the focus to its tree row. PostgreSQL
  shows the row estimate; the extensions node lists versions and updates; the SQLite file node lists the pragmas,
  the file size and its permission bits.
- **Safety:** the page sends only GET requests and the connection open; it offers no action that changes the
  database (STU-026 stays locked). Every database text is escaped (`visible()`: control, separator and bidi
  characters as `\uXXXX`) and cut with a visible "…" (names 64, types 64, defaults 160, comments and check texts
  300 characters); lists stop after 500 entries with "and N more".
- **Fail closed:** a catalog without its object list, or with an object without a schema or name, is
  `studio.answer_invalid`, never an empty tree. An unknown object kind is shown under "Other objects". A count
  (row estimate, page size, file size) is used only when it is a safe non-negative integer, a time only when `Date`
  can show it, the file mode only as four octal digits. Nothing is cut while reading: the catalog keeps every object,
  however many, and only the rendering is capped (and says so). A table detail for another schema or name than the
  one asked for, or a catalog for another connection, is `studio.answer_invalid` too.
- **Stale answers:** a profile switch, an open or a refresh counts up one request counter, a selection a second one;
  only the latest answer of each is shown. A refresh keeps the selection of the moment it answers.

Open: "Open in editor" and "SELECT … LIMIT 100" (with the SQL editor, #41), the DDL and policies tab (with STU-022,
#38), the data tab (STU-013 page), the virtualized tree and following the header's connection; see TD-041.

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_explorer.py` (SQLite) | the catalog of 503 tables in under a second (STU-010 acceptance: 500 tables < 1 s), the cache and `refresh`, the table detail (columns, default, PK, FK out and in, check, unique, index), unknown and hostile names refused, the SQLite facts (`STRICT`, `WITHOUT ROWID`, file mode), the registry check with a cached catalog, the routes behind session and CSRF |
| `tests/security/test_explorer.py` (PostgreSQL) | the catalog of 502 tables in a schema in under a second, materialized views and sequences, no system schemas; comments, FKs in and out, the row estimate, extensions |
| `tests/security/test_definitions.py` (SQLite) | the DDL of the measurements device file equals its migrations and `sqlite_master`, the parts schema likewise; `count(*)` on request, every name quoted (keywords, a broken view reports `db.*`), unknown names refused, the timeout cancels a count that would run for minutes (`db.timeout`) within seconds and repeats its cancel; only checked `SELECT` statements reach the database (spy); the routes with both name forms behind session and CSRF |
| `tests/security/test_definitions.py` (PostgreSQL) | STU-022 acceptance: the DDL of the parts, measurements and `extras` schemas, and of both apps in `public`, equals `pg_dump --schema-only` statement by statement (recorded and live); `extras` includes rules, child-only `NOT NULL`, replica identity, `CLUSTER ON`, column statistics, storage and compression, a materialized view with options, a statistics target and `CLUSTER ON`, a view column default, a type and a domain; identity and owned sequences, policies and `rls_applies` (forced, not forced, owner, member, `BYPASSRLS`, superuser), row estimates with partitions (an unanalyzed partition gives none), a foreign table (superuser), the spy, the routes |
| `tests/security/test_definitions.py` (PostgreSQL superuser) | the fixed catalog search path: objects another role plants in `public`, armed before the connection is opened (so the connection's setup is covered too, TD-039), are never called by Studio's catalog reads (DDL, policies, estimates, the catalog, table detail, extensions, the migration lookups), with `pg_catalog` implicit and with `public` placed before it in the role's `search_path` |
| `frontend/src/views/explorer/ExplorerView.test.tsx`, `tree.test.ts`, `src/api/explorer.test.ts` | the page: D-02, the tree keyboard, the filter, the detail tabs, U-1 escaping and caps, stale answers, focus, fail-closed parsing, no write |
| `frontend/e2e/explorer.spec.ts` | the page in Chromium with axe (light/dark, en/de) and keyboard-only; `e2e/explorer-reference.spec.ts` against the parts and measurements reference apps (skipped without its variables, TD-041) |
