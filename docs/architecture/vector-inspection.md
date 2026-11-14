# Vector data inspection (STU-055)

The **Vector data** page shows, read-only, what a SQLite or PostgreSQL database holds for vector search: the vector
columns and their types, the vector indexes with their method and state, the embedding dimensions, the embedding
model identities that the database's own metadata names, how many embeddings are stored, and whether pgvector or
sqlite-vec is installed. It never embeds, indexes, searches or changes anything, and it adds no dependency: Studio
does not import Orkexa or any other Levuvo platform (import contract `no-levuvo-platform`), and it recognizes Orkexa's
tables by their documented structure only.

## Route

`GET /api/connections/{id}/vectors` (service `services/vectors.py`, registered with the explorer routes). It is a
`GET` of the same family as `/catalog` and `/extensions`, so every guard of the other routes applies unchanged: the
Host and Origin checks, the session cookie and the CSRF header of the access middleware, `no-store` and the CSP, the
connection check of `ConnectionRegistry.get` (a stale, closed or unknown connection is refused) and the problem
documents of the connection routes, whose database errors are redacted by tabrion's classifier. Other methods are
refused with 405 like every route. The answer is:

```text
{connection_id, engine, extension: {name, state, installed_version, available_version, schema, update_available},
 columns: [{schema, table, column, type, kind, layout, dimensions, dimensions_source, search, rows, indexes}],
 identities: [{layout, schema, table, tenant, namespace, space, state, provider, model, digest, dimensions,
               normalized, complete, embeddings}],
 hints: [{code, severity, params}], limits: {exact_count_max, max_columns}}
```

`hints` carry facts only (counts, names of database objects, fixed words); the page words them in English and German.

## The read-only path

Every statement is a fixed text of Studio's run through `OpenConnection.fetch`: the statement check
(`check_statement`), one `Database.read()` per statement (PostgreSQL `BEGIN READ ONLY` in a
`default_transaction_read_only` session; SQLite `mode=ro`, `query_only` and Studio's authorizer), and on PostgreSQL the
fixed `search_path` of catalog reads (`pg_catalog, pg_temp`). Values are bound parameters. A table name is never a
parameter in SQL, so the few counting statements put the name of a table that the catalog listed (never a name from
the request) into the text as a quoted identifier (`"` doubled). The pgvector status comes from Tabrion's public
`tabrion.postgres.extension_status` (stable API), run on the driver connection of a `Database.read()` through
`tabrion_compat.extension_status`, as `tabrion.check` is for the diagnostics. Nothing loads an extension: SQLite's
authorizer denies `load_extension`, and Studio does not change that.

## Queries

**PostgreSQL** (all `pg_catalog`-qualified):

- vector columns: `pg_attribute` of tables, partitioned tables, materialized views and foreign tables (no partitions,
  no system schemas) whose type, or the base type of their domain, is `vector`, `halfvec` or `sparsevec` **and**
  belongs to the extension `vector` (`pg_depend` with `deptype = 'e'`); the dimensions come from the type modifier
  (`atttypmod`, or the domain's `typtypmod`), `-1` meaning "not declared"; with `pg_class.reltuples` and
  `has_table_privilege(oid, 'SELECT')`;
- Orkexa layout: tables named `<prefix>vector_spaces` and `<prefix>chunk_vectors` in one schema, with the columns of
  Orkexa's schema version 1 (`tenant_id`, `namespace`, `space`, `identity jsonb`, `dimensions`, `state`;
  `embedding real[]`). Only then is a `real[]` column listed, as a "float array" vector column: a float array without
  that metadata is never called a vector column;
- the identities: `SELECT tenant_id, namespace, space, identity::text, dimensions, state FROM <spaces> ... LIMIT 201`;
  the JSON is parsed in Python (`provider`, `model`, `digest`, `dimensions`, `normalized`);
- indexes per listed table: `pg_index` with `pg_class`, `pg_am.amname`, `indisvalid`, `indisready`, `indislive`, the
  operator classes (`pg_opclass`), `reloptions`, `pg_relation_size` and `pg_get_indexdef`; an index belongs to a
  column when `pg_depend` records the column for it (this covers plain and expression indexes such as
  `USING hnsw ((embedding::vector(384)) vector_cosine_ops)`);
- the extension: `extension_status(connection, "vector")` (`pg_extension.extversion`, the default version of
  `pg_available_extensions`, the schema).

**SQLite:** `sqlite_master` only for the structure (a `vec0` virtual table is read from its `CREATE VIRTUAL TABLE`
text, which needs no module); `index_meta` (`dimensions`), `index_spaces` and `index_vectors` of Orkexa's SQLite
index; a `vec0` table's shadow tables `<name>_rowids` (one row per vector) and `<name>_info` (`CREATE_VERSION`).

## Values are capped, parsing is linear

`fetch` has no byte cap, so every text that can be large in a hostile database is cut in SQL before it is read:
`substr(sql, 1, 65537)` of `sqlite_master` and `left(identity::text, 65537)` / `substr(identity, 1, 65537)` of the
identities. A value longer than 64 KiB is never parsed: a virtual table's text gives the hint
`definition_too_long`, an identity counts as incomplete. A `vec0` text is recognized without a backtracking pattern
(PR #97 review M-1): the head `CREATE VIRTUAL TABLE [IF NOT EXISTS]` and `USING <module> (` are matched at fixed
places after a scan of the table name, and the column list runs from that `(` to the last `)`.

The Orkexa spaces table is read with `ORDER BY 1, 2, 3 LIMIT 201` under the session's statement timeout, not under
the count timer; spaces tables are small by design (review I-1).

## Counts are bounded

No statement scans a table without a bound. A count is
`SELECT count(*) FROM (SELECT 1 FROM <table> [WHERE ...] LIMIT 100001) AS bounded`, so it reads at most
100,001 rows and answers "more than 100,000" past that. On PostgreSQL a table whose estimate (`reltuples`) is above
100,000 is not counted at all: its estimate is shown, marked as an estimate. Every count also runs under a timer of at
most 10 seconds (the explorer's `count(*)` limit, never more than the connection's statement timeout), which cancels
it like a query; a canceled count is shown as unknown with a hint, and the other facts stay. At most 50 counts run per
inspection. Under row-level security a count is the rows the role sees.

## ANN index or exact search

A vector column is shown as **ANN-indexed** only when at least one index on it uses an approximate-nearest-neighbor
access method (`hnsw`, `ivfflat`, or `diskann` of pgvectorscale) and is valid, ready and live (`indisvalid`,
`indisready`, `indislive`). Everything else is **exact search (no ANN index)**: a `vector` column without such an
index, one whose only ANN index is invalid (a failed `CREATE INDEX CONCURRENTLY`; listed with its state and a
warning), a B-tree on a vector column, Orkexa's `real[]` vectors (ranked by an exact cosine distance) and Orkexa's
SQLite `index_vectors` BLOBs (`vec_distance_cosine`).

Caveats the page states: an index on an expression of the column (`USING hnsw ((embedding::vector(384)) ...)`) or a
partial index (`WHERE ...`) counts, but a query uses it only with the same expression or a matching condition, so
the page marks such an index "on an expression" or "partial". The operator class decides the distance an index
serves (`vector_cosine_ops` serves cosine distance, not L2); the page lists it next to the index. A schema, table or
column named like a function the statement check refuses (after a `.`, since PR #92) cannot be counted: the count
shows "unknown (not readable)" with a hint, on the safe side. A sqlite-vec `vec0` table is listed as a vector index of the
method `vec0`, and as exact search: the released sqlite-vec versions answer a `vec0` KNN query by a full scan of its
chunks, not by an approximate index.

## Install status

- **pgvector:** installed with its version, available with the server's default version (and "update available"
  when the two differ), installed in a schema outside the search path, or not on the server. These come from the
  catalog and are reliable.
- **sqlite-vec:** **not determinable**. sqlite-vec is a loadable module of the application's process, not part of the
  database file, and Studio does not load extensions. The page says so, and lists what the file shows: the `vec0`
  tables and the version that created each one (`<name>_info.CREATE_VERSION`, sqlite-vec 0.1.6 and later).

## Failures that are not errors

A table the role may not read (no `SELECT`, `permission denied`) gives a hint and no count or identity; an empty
database, a database without vector data or without the extension gives an empty list with a hint; malformed
identity JSON, a dimension that disagrees with the identity, a space in state `building`, a `dimensions` row of
`index_meta` without its `vec0` table and a vector column without declared dimensions each give a hint. Only a lost
or refused connection is an error of the route.
