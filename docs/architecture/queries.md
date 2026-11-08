# Query engine

The query engine runs user SQL on an open, read-only connection (03 sections 5.3 and 6, work package STU-011,
threat T-09). It sits on the connection registry of [Connections and read-only enforcement](connections.md) and is
what the SQL editor (STU-012) and the data viewer (STU-013) call.

## Flow

1. `POST /api/queries` with `{connection_id, sql, params?, mode?: "read", max_rows?}`.
   - The connection comes from `ConnectionRegistry.get`, which checks the handle against its profile again.
   - `OpenConnection.prepare` checks the statement (`check_statement`: one statement whose first word is `SELECT`,
     `WITH`, `VALUES`, `TABLE`, `EXPLAIN` of a query, `SHOW` or SQLite's `PRAGMA`) and binds its parameters (`services/sqltext.py`). A refusal is the answer to this request.
   - The answer is 202 with the query's state; a worker thread runs the statement.
2. The worker runs `OpenConnection.stream`: one `Database.read()` per statement, the statement checked once more
   (also the driver text it executes: it must be what binding the checked text gives), and the rows read in
   batches until a limit is reached. The first fetch is one row; the next ones as many rows as fit about 2 MB at
   the size so far, at most 500.
3. The page follows `GET /api/queries/{id}/events` (server-sent events) and reads `GET /api/queries/{id}/rows`
   in pages of up to 5 000 rows, also while the query runs.
4. `POST /api/queries/{id}/cancel` stops it.

`mode: "write"` is refused (`studio.query_write_unsupported`): Studio 1.0 does not write, and the Data Editor
(STU-026) stays locked.

## Parameters

A `:name` placeholder takes its value from `params`: text, a number, `true`, `false` or `null`. JSON goes as text
with a cast (`:doc::jsonb`). Studio never sends user SQL through SQLAlchemy `text()`, which reads `:name` inside a
string literal as a placeholder too (I-2 of `stu-009-review.md`).

- PostgreSQL: Studio scans the statement (literals, quoted identifiers, dollar quotes and comments skipped; `::` is a
  cast) and sends psycopg `%(name)s` placeholders, with every other `%` doubled. The values travel separately; the
  statement is a prepared statement (`prepare_threshold=0`).
- SQLite: the text goes unchanged to sqlite3, which reads `:name` itself.

A missing value is `studio.query_param_missing`. Where Studio's scan and the server disagree (a server with
`standard_conforming_strings = off`), the server refuses the statement: values are never part of the text. An array
slice `a[1:n]` reads `:n` as a placeholder; write `a[1 : n]`.

## Limits

| Limit | Value | Where |
|---|---|---|
| Rows per result | 1 000 by default; `max_rows` up to the `max_rows` setting (10 000 by default, at most 1 000 000) | `truncated_by: "rows"` |
| Bytes per result | the `max_result_mb` setting: 20 MB of JSON values by default, at most 200 MB | `truncated_by: "bytes"` |
| One fetch | about 2 MB (one value larger than that still loads in full: the database's own limit bounds a single value) | |
| Cell | 64 KB of text, 32 KB of bytes (as base64) | `truncated_cells: [[row, column], ...]` |
| Time | the profile's timeout (30 s, 15 s for `prod`) for the whole query; PostgreSQL also `statement_timeout` per statement and `lock_timeout` 5 s | `db.timeout` |
| Parallel queries | 4 in Studio, 2 per connection | `studio.query_limit` (429) |
| Kept results | the last 16 queries; finished ones are dropped, oldest first, above 200 MB | `studio.query_not_found` |

A PostgreSQL query (`SELECT`, `WITH`, `VALUES`, `TABLE`) is read through a server-side cursor (`DECLARE ... CURSOR`,
`FETCH`), so a cap stops the transfer and not only the delivery. Other statements (`EXPLAIN`, `SHOW`) use a plain
cursor. A SQLite cursor steps the statement as rows are fetched.

## Cancel and timeout

`POST /api/queries/{id}/cancel` returns at once. A worker sends the database's own cancel (psycopg `cancel_safe()`,
`sqlite3.Connection.interrupt()`) and repeats it every 100 ms until the statement stops: a cancel that arrives before
the statement starts does nothing in either database. The target is under one second (STU-T-020). After 3 s the
worker keeps asking once a second until the statement stops; a query has one cancel worker at most, so repeated
cancel requests start nothing new. The timeout uses the same cancel and reports `db.timeout`.

A cancel is sent only while the statement holds the connection, never after (a lock in `tabrion_compat.Cancel`).
A connection that received a cancel is invalidated, on both engines, so it never goes back to the pool: PostgreSQL
may act on a cancel packet after the statement ended and abort the next user's statement (H-1 of
`tab-033-review.md`). If SQLAlchemy cannot invalidate it, the driver connection is closed.

## Statements and session state

`check_statement` allows a statement only by its first word (M-1, M-2, I-2 of `stu-011-review.md`): `SELECT`,
`WITH`, `VALUES`, `TABLE`, `EXPLAIN` whose statement is one of the first four, `SHOW`, and `PRAGMA` (SQLite; the
authorizer allows only reading pragmas). An allowlist instead of a denylist, because a denylist missed two session
effects that outlive Studio's one-statement transaction:

- `DECLARE ... CURSOR WITH HOLD` is materialized at `COMMIT`, after the cancel hook is detached, so neither the
  timeout nor a cancel stops it. `DECLARE`, `FETCH`, `MOVE` and `CLOSE` are refused; the engine makes its own cursor.
- `SET` (also `SET LOCAL`, which does nothing useful in a one-statement transaction) is refused: a plain `SET`
  stays on the pooled connection (`SET statement_timeout = 0`).

Writes (`INSERT`, `CREATE`, ...) are refused by the same rule; the read-only session stays the boundary for them,
which the STU-T-010 tests prove by sending them below the check. As a second layer, every PostgreSQL connection
gets Studio's session settings (`statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`,
`TimeZone`, `default_transaction_read_only=on`, `application_name`) as startup options too, and runs `RESET ALL`
when it goes back to the pool: whatever a statement changed (for example through a function of the database), the
next statement gets the profile's values. A connection that cannot be reset leaves the pool.

## Events and states

`GET /api/queries/{id}/events` is a `text/event-stream`. Each change sends an event `state` with the same document
as `GET /api/queries/{id}`; the last event is named after the final state (`done`, `failed`, `canceled`), then the
stream ends. A comment line every 15 seconds keeps the stream open. The access middleware needs the CSRF header on
every API request, so the page reads the stream with `fetch`: `EventSource` cannot send headers.

The state document: `{query_id, connection_id, state, row_count, max_rows, truncated, truncated_by, elapsed_ms,
columns: [{name, type}], problem?}`. `type` is the PostgreSQL type name (`int4`, `jsonb`, `int4[]`); SQLite gives
none. Values: numbers beyond JavaScript's safe integers, decimals, dates, times and UUIDs are text; bytes are
`{"base64": ...}`; JSON stays JSON.

## Errors and logs

Errors are problems with fixed texts: `studio.statement_*`, `studio.query_*`, `studio.connection_*` and
`db.<kind>` with the SQLSTATE (STU-009). The log has one `studio.query_finished` record per query (state, row count,
duration, engine, problem code) and never the SQL text, a parameter value or a row; a database error goes through
tabrion's `DbErrorRedactionFilter`.

## tabrion 0.1 workarounds

tabrion 0.1 has no `Transaction.stream`, `Transaction.cancel` or `tabrion.Column` yet (TAB-033, released with
TAB-042). `services/tabrion_compat.py` builds them from `Database.read()` and `Transaction.connection`. It is the only
module that touches the driver's connection (`tests/architecture/test_compat_boundary.py`), so the move to 0.2 changes
that file only (TODO TD-013).

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_queries.py` (SQLite, always) | STU-T-018: the default of 1 000 rows, `max_rows`, the setting as the ceiling, the byte cap, the cell cap; STU-T-019: an endless recursive query stops at the profile's timeout; STU-T-020: cancel stops it within a second and the connection works afterwards; a cancel before the start; writes and several statements refused; the registry check; the limits on parallel queries; kept results; no SQL text or parameter value in the log; shutdown cancels; the routes, the SSE stream and the refusals |
| `tests/security/test_queries.py` (PostgreSQL) | types and parameters, `:name` and `%` inside literals; STU-T-018: a 50-million-row series with `max_rows=10` ends at once (server-side cursor); `EXPLAIN` on a plain cursor; STU-T-019: `statement_timeout`; STU-T-020: cancel of `pg_sleep` and of a long scan within a second, the session still read-only afterwards; a write fails; several commands past the text check are refused for the cursor's `DECLARE` too (`42601`) |
| `tests/architecture/test_compat_boundary.py` | only `tabrion_compat.py` reaches the driver |
