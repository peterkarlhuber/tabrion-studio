# Data viewer

The data viewer is the backend of the grid tab (03 sections 2 D, 5.3 and 6, work package STU-013). It shows one
table, view or materialized view page by page, sorted and filtered, and loads single values in full. Every route
only reads. The object must be in the connection's catalog ([Database explorer](explorer.md)); each statement runs
in its own `Database.read()` through `OpenConnection.stream` (see [Connections and read-only enforcement](connections.md)).

## Routes

| Route | Answer |
|---|---|
| `GET /api/connections/{id}/tables/{schema}/{name}/rows` | `{columns, rows, truncated_cells, truncated, page, next, paging, hint, sort, desc, limit}`. Query parameters: `limit` (1 to 1000, default 100), `sort` (a column), `desc=true`, `filter` (JSON, below), `after` (the `next` token of the page before) |
| `GET /api/connections/{id}/tables/{schema}/{name}/cell?page=&row=&column=` | `{value, size, truncated}`: one value of a page in full, up to 10 MB of its bytes; `size` is the value's whole size in bytes |
| `GET /api/connections/{id}/rows?schema=&name=...`, `GET /api/connections/{id}/cell?schema=&name=...` | the same, for a name with `/` |

A filter is a JSON list of at most 20 entries `{"column", "op", "value"}`. `op` is `eq`, `ne`, `lt`, `le`, `gt`,
`ge`, `contains`, `starts_with`, `is_null` or `not_null`; the value is text, a number, `true`, `false` or `null`.
`contains` and `starts_with` compare the column as text, and `%` and `_` in their value are literal characters.
All entries must hold (AND).

The engines differ (I-3 of `stu-013-review.md`; the grid tab says so): `contains` and `starts_with` use `LIKE`,
which ignores the case of ASCII letters on SQLite and respects case on PostgreSQL. A value of the wrong type for
its column (`id eq "abc"`, `id eq true` on PostgreSQL) is the database's classified problem (`db.data`,
`db.undefined_object`), with no SQL and no values in it.

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
| `primary_key` | the object has a primary key | `ORDER BY [sort,] primary key [, rowid]`, keyset `(sort, key) > last row`; on a SQLite rowid table the row id comes last, because SQLite lets a primary key hold `NULL` in several rows | none |
| `rowid` | SQLite table without a primary key | the same keyset on `rowid` (or `_rowid_`, `oid` if a column hides it) | `no_primary_key` |
| `ctid` | PostgreSQL table or materialized view without a primary key | the same keyset on `(tableoid, ctid)`: a `ctid` is unique only within one relation, and a partitioned table or an inheritance parent reads several | `no_primary_key` |
| `offset` | a view or foreign table without a primary key | `LIMIT` and `OFFSET`, by the sort column if any | `unstable_order` |

The keyset is written as a disjunction (`a > x OR (a = x AND b > y) ...`), so mixed directions and `NULLS LAST` work
on both engines; `NULL` sort values come last in both directions.

**Key types.** On PostgreSQL a key is selected as the type's own text (`CAST(key AS text)`) and bound back as an
untyped parameter: the server reads it with the column's input function and compares in the column's type. So a
`real` key is compared as `real` (not as the driver's `float8`, which made the next page repeat forever), and
`jsonb`, arrays, enums, ranges and domains work on every page. A sort column of a type without an ordering (`json`,
`xml`, geometric types such as `point`) is refused before any statement: `studio.viewer_sort_unsupported`. On SQLite
keys go back as the driver returned them.

**Float text and the progress guard (H-2 residual).** The text of a `float4`/`float8` key is exact only with
`extra_float_digits` above 0 (`0.30000000000000004` reads as `0.3` with 0, the default up to PostgreSQL 11 and a
common role or database setting). Every Studio session starts with `extra_float_digits=3` in its startup options
(`connections.startup_options`), which `RESET ALL` keeps and which wins over a role or database setting. As a guard
for any key text that does not read back as its value, a keyset page whose last key equals an anchor of the walk
(the last 32 are kept as digests) is not shown: the page is read again by `OFFSET` after the previous page's own
anchor, and the walk stays on `OFFSET` to its end. Every row then comes exactly once, and `next` always ends.

**Row identity (N-1).** The identity of a row for the cell detail is a list of typed parts: `("column", name)` for
each primary key column, then the physical ids (`rowid`, `tableoid`, `ctid`, `xmin`). A key column is always a quoted
identifier, whatever its name (one called `@a` or `@1=1 OR 1` included); a physical id is one of the fixed names and
never comes from a column name.

**What is kept.** The key values stay in the backend: the answer carries an opaque token (`page`, and `next` when
more rows exist). A page keeps only its last row's key, and for every row its identity for the cell detail. A key
longer than 64 KB is not kept; the next page then starts after the last key kept, with an `OFFSET` for the rows
since. Tokens are bound to the session (a digest of its CSRF value) and kept for the last 256 pages, at most 64 per
session and connection; a closed connection's pages are dropped. A `next` token keeps the sort, filters and limit of
its page; an unknown, evicted, foreign or other session's token is `studio.viewer_page_unknown`.

**Concurrent writes (L-3).** Each page is its own statement and its own snapshot. Pages by primary key can miss or
repeat a row whose sort value changes between pages (the usual keyset trade-off). Pages by `ctid` or `rowid`
(`hint: no_primary_key`) can also miss or repeat rows under concurrent writes: an `UPDATE` moves a PostgreSQL row to
a new `ctid`, `VACUUM FULL` and `CLUSTER` rewrite all of them, and SQLite's `VACUUM` may renumber row ids. `OFFSET`
pages of a view without a sort have no stable order at all (L-2, kept open in `docs/TODO.md`).

## Cells, timeout, errors

**Byte caps (M-4).** The length of every value that can be long (text, `bytea`, JSON, arrays, any type without a
small fixed size; every SQLite value) is measured on the server (`octet_length`, `length(CAST(x AS BLOB))`) in the
same statement. A value that fits is fetched as it is; a longer one only as its first bytes (`substr`, text as
UTF-8). So a page cell loads at most 64 KB (cut ones are listed in `truncated_cells` as `[row, column]`; bytes go out
as base64 of at most 32 KB, as in the query engine). A page stops at the `max_result_mb` setting (20 MB by default,
the query engine's cap; at least one row) with `truncated: "bytes"` and a `next` token, and rows are fetched one
first, then in batches of about 2 MB at the size so far (the query engine's fetch budget), never more rows than 2 MB
holds of the widest row the caps allow (every capped value at its cap, M-4 residual): a small first row cannot make
the next batch large. With 11 text columns that is 2 rows per fetch; a row wider than 2 MB is fetched alone.

The cell detail loads at most 10 MB of the value's bytes (text as UTF-8; a character cut at the end is left out;
bytes as base64 of up to 10 MB); `truncated` says whether it was cut, and `size` is the whole value's size in bytes.

**Finding the row again (M-3).** The cell detail finds the row by its full primary key. A physical id (`ctid`,
`rowid`, or the row id of a SQLite row whose key holds `NULL`) can be reused by another row after a delete, so the
row must also still show what the page showed (a digest of its page values and sizes, and on PostgreSQL its
`xmin`); otherwise the answer is `studio.viewer_row_gone`. `studio.viewer_cell_unavailable` is for `offset` pages,
whose rows cannot be found again, and for a row whose primary key is longer than 64 KB.

The statements are compiled with `:name` placeholders and go through the same driver-text derivation as user SQL
(`sqltext.driver_text`), so `OpenConnection.stream` checks them like any other statement; key values go back to the
driver as their text (PostgreSQL) or as the driver returned them (SQLite). Every statement is checked for its
`LIMIT` on the statement itself before it is compiled (and on the text as a second look).

At most 4 viewer statements run at a time in Studio and 2 per connection, the query engine's numbers; more are
`studio.query_limit` (429). The reflection of an object (columns, primary key and, on PostgreSQL, the type facts from
`pg_attribute`) is kept for 10 seconds and dropped with a catalog refresh or a closed connection.

Each statement runs under the profile's statement timeout. Past it, the statement is canceled (the query engine's
cancel, repeated every 0.1 s) and the answer is `db.timeout`, the same as for a query. Nothing is logged but events
and problem codes; never a value.

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_viewer.py` (SQLite) | a spy on `tabrion_compat.stream`: every statement has a `LIMIT` and no literal; keyset pages walk 250 rows exactly once, ascending and descending, by primary key, a nullable `REAL` and repeated text; `rowid` paging with a column called `rowid`; `offset` paging of a view; filters with hostile column names and `%`/`_` values; malformed filters and unknown columns refused before any statement; page tokens (unknown, foreign, last page, eviction); the cell detail and a deleted row; the timeout on an endless view; the routes behind the session check |
| `tests/security/test_viewer.py` (PostgreSQL) | keyset pages over `numeric` with `NULL`s in both directions, a composite primary key, `ctid` paging of a table and a materialized view, `offset` for a view, filters with a quoted schema and a hostile column name, the statement timeout |
| `tests/security/test_viewer.py` (fix round of `stu-013-review.md`) | the reviewer's probes: a partitioned table and an inheritance parent without a primary key walk all 20 rows (H-1); `real`, `jsonb` and `int[]` sorts with repeated values walk to the end, `json`, `xml` and `point` are refused (H-2, M-1); NULLs in a SQLite `TEXT PRIMARY KEY` (M-2); a reused `rowid` and a reused `ctid` after `VACUUM` answer `viewer_row_gone` (M-3); a 30 MB blob or `bytea` and a 12 MB text stay in the database for a page (tracemalloc), the cell detail loads 10 MB of bytes, a page stops at `max_result_mb` (M-4); long sort keys (L-1); tokens per session and connection (L-4); the reflection cache and the statement limit (L-5); every route form without a session or from a foreign origin (I-1) |
| `tests/security/test_viewer.py` (delta re-review of `4ad445a`) | N-1: primary keys with columns named `@1=1 OR 1`, `@a` and `@rowid` on SQLite and PostgreSQL: the statements quote them, the cell of a deleted row whose key came back with another `@a` value is `viewer_row_gone`; H-2 residual: with `extra_float_digits = 0` on the database, a `float8` sort over 30 equal `0.1 + 0.2` walks 30 rows in both directions, and without the startup pin the progress guard still walks every row exactly once; M-4 residual: 10 text columns of 60 KB after a tiny first row fetch at most 2 rows at a time, under 8 MB peak |
