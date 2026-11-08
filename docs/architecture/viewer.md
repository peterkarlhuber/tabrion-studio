# Data viewer

The data viewer is the backend of the grid tab (03 sections 2 D, 5.3 and 6, work package STU-013). It shows one
table, view or materialized view page by page, sorted and filtered, and loads single values in full. Every route
only reads. The object must be in the connection's catalog ([Database explorer](explorer.md)); each statement runs
in its own `Database.read()` through `OpenConnection.stream` (see [Connections and read-only enforcement](connections.md)).

## Routes

| Route | Answer |
|---|---|
| `GET /api/connections/{id}/tables/{schema}/{name}/rows` | `{columns, rows, truncated_cells, page, next, paging, hint, sort, desc, limit}`. Query parameters: `limit` (1 to 1000, default 100), `sort` (a column), `desc=true`, `filter` (JSON, below), `after` (the `next` token of the page before) |
| `GET /api/connections/{id}/tables/{schema}/{name}/cell?page=&row=&column=` | `{value, size, truncated}`: one value of a page in full, up to 10 MB |

A filter is a JSON list of at most 20 entries `{"column", "op", "value"}`. `op` is `eq`, `ne`, `lt`, `le`, `gt`,
`ge`, `contains`, `starts_with`, `is_null` or `not_null`; the value is text, a number, `true`, `false` or `null`.
`contains` and `starts_with` compare the column as text, and `%` and `_` in their value are literal characters.
All entries must hold (AND).

## Statements

**Every statement has a `LIMIT`** (03 section 5.3, full-table protection). The viewer builds its statements with
SQLAlchemy Core `select()` from the reflected columns. Request text never becomes statement text:

- the sort column, every filter column and the cell's column must be columns of the reflected object, or the request
  is `studio.viewer_column_unknown`; names are quoted by the dialect;
- filter values are bound parameters, compared by the database's own rules (psycopg sends text as `unknown`, so
  PostgreSQL resolves it against the column's type);
- a page is `LIMIT page + 1`, to know whether a next page exists.

The compiled text still passes `check_statement` again in `OpenConnection.stream`.

## Paging

| `paging` | When | Order and next page | `hint` |
|---|---|---|---|
| `primary_key` | the object has a primary key | `ORDER BY [sort,] primary key`, keyset `(sort, key) > last row` | none |
| `rowid` | SQLite table without a primary key | the same keyset on `rowid` (or `_rowid_`, `oid` if a column hides it) | `no_primary_key` |
| `ctid` | PostgreSQL table or materialized view without a primary key | the same keyset on `ctid` | `no_primary_key` |
| `offset` | a view or foreign table without a primary key | `LIMIT` and `OFFSET` | `unstable_order` |

The keyset is written as a disjunction (`a > x OR (a = x AND b > y) ...`), so mixed directions and `NULLS LAST` work
on both engines; `NULL` sort values come last in both directions. The key values of a page stay in the backend: the
answer carries an opaque token (`page`, and `next` when more rows exist), kept for the last 256 pages. A client never
sends a key value that becomes part of a statement. A `next` token keeps the sort, filters and limit of its page; an
unknown, evicted or foreign token is `studio.viewer_page_unknown`.

## Cells, timeout, errors

Cells are encoded as in the query engine: 64 KB of text per cell, longer ones listed in `truncated_cells` as
`[row, column]`. The cell detail finds the row again by its key from the page and loads the value in full up to
10 MB (`studio.viewer_row_gone` if the row is gone; `studio.viewer_cell_unavailable` for `offset` pages, whose rows
cannot be found again).

Each statement runs under the profile's statement timeout. Past it, the statement is canceled (the query engine's
cancel, repeated every 0.1 s) and the answer is `db.timeout`, the same as for a query. Nothing is logged but events
and problem codes; never a value.

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_viewer.py` (SQLite) | a spy on `tabrion_compat.stream`: every statement has a `LIMIT` and no literal; keyset pages walk 250 rows exactly once, ascending and descending, by primary key, a nullable `REAL` and repeated text; `rowid` paging with a column called `rowid`; `offset` paging of a view; filters with hostile column names and `%`/`_` values; malformed filters and unknown columns refused before any statement; page tokens (unknown, foreign, last page, eviction); the cell detail and a deleted row; the timeout on an endless view; the routes behind the session check |
| `tests/security/test_viewer.py` (PostgreSQL) | keyset pages over `numeric` with `NULL`s in both directions, a composite primary key, `ctid` paging of a table and a materialized view, `offset` for a view, filters with a quoted schema and a hostile column name, the statement timeout |
