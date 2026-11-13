# Export

STU-021 (03 sections 2 D and 6) exports the result of a query, or a whole table or view, as CSV or JSON. The file is
streamed to the page, and nothing is collected in memory. The export only reads. It runs one checked, read-only
statement through `OpenConnection.stream` in its own `Database.read()`, like every other Studio statement (see
[Connections and read-only enforcement](connections.md)).

## Route

| Route | Answer |
|---|---|
| `POST /api/exports` | `{"connection_id", "format": "csv" \| "json", "sql"?, "params"?, "table"?: {"schema", "name"}, "max_rows"?, "escape_formulas"?}`: `200` with the file as an attachment (`Content-Disposition`), streamed |

- **One source.** A request names exactly one of `sql` and `table`.
  - **`sql`:** the statement passes `check_statement` (the same first-word allowlist as the query engine), and its
    `:name` parameters are bound by the driver.
  - **`table`:** the object must be in the connection's catalog ([Database explorer](explorer.md)). The statement is
    `SELECT <reflected columns> FROM <schema>.<name>`, with every name quoted, so no request text becomes statement
    text.
- **Refusals come first.** The statement check, the parameters, the object check and the first fetch run before the
  response starts. So a refusal is a problem document:
  - `studio.export_invalid`, `studio.export_busy`;
  - `studio.statement_*`, `studio.parameter_*`, `studio.object_not_found`;
  - `db.<kind>`;
  - the problems of the connection routes.

  An error after the first byte ends the download early, and the log records it with the error's type but never
  the SQL text or a value.
- **Access.** The route sits behind the session and the CSRF and Origin checks of the access middleware, like every
  changing request. The page downloads the file with `fetch`.

## Formats

- **CSV** (`text/csv; charset=utf-8`):
  - The first line is a header row of column names, and fields are quoted as RFC 4180 requires.
  - `NULL` is the empty field. Bytes are base64; dates and times are ISO 8601; JSON and array values are written as
    JSON text.
  - **CSV injection:** a text that a spreadsheet would read as a formula starts with `=`, `+`, `-`, `@`, a tab, a
    carriage return, or one of the fullwidth forms `＝`, `＋`, `－`, `＠`. Such a text gets a leading `'`, and so does
    base64 (which can start with `+`). This applies to column names too. Numbers, dates and JSON values are never
    changed. `"escape_formulas": false` turns this off.
  - CSV cannot mark a cut export. The row count tells.
- **In both formats:**
  - A value that is not a finite number is spelled the way PostgreSQL spells it: `NaN`, `Infinity` or `-Infinity`.
    This holds for floats, decimals and numbers inside a JSON document.
  - An interval is an ISO 8601 duration in PostgreSQL's `iso_8601` style: `P428DT4H5M6S`, `P-1D`, `PT0S`. Every
    part carries its sign, so the text never starts with `-`. The driver already folds months into days.
- **JSON** (`application/json`): `{"columns": [...], "rows": [[...], ...], "truncated": null | "rows"}`.
  - Rows are arrays, so two columns with the same name stay apart.
  - Bytes are `{"base64": "..."}`. Decimals, UUIDs, dates, times and intervals are text. Integers beyond
    JavaScript's safe range (2^53 - 1) are text too, as in the query engine; inside a JSON document numbers stay as
    they are.
  - That rule is for column values only (R-2 of the `stu-021-review.md` delta check). Integers inside an array column
    stay numbers, so a `bigint[]` value above 2^53 keeps its digits but a JavaScript reader rounds it. A `json` or
    `jsonb` value that is a single number above 2^53 is a column value, so it becomes text, and that document's
    type changes from number to string.
  - The file is strict JSON: there is never a bare `NaN` or `Infinity`.
  - Formula-like text is not changed, because JSON is data.

## Limits

| Limit | Value |
|---|---|
| Rows | `max_rows`, 1 to 1 000 000 (the default is 1 000 000); a JSON export over it says `"truncated": "rows"` |
| Time | 10 minutes; then the export is canceled like a query (PostgreSQL cancel, SQLite interrupt) |
| Parallel | One export per connection and 2 at a time (`studio.export_busy`). Each holds one of the connection's sessions while it runs; the slot is freed when its transaction has ended, before the client sees the end or the error (TD-057) |
| Memory | Bounded (see below); the acceptance of STU-021 is 1 million rows with a peak under 100 MB, and so are wide rows of up to 1 MB after narrow ones (wider rows: see below) |

## Streaming and memory

- **A producer thread** runs the statement. PostgreSQL reads through a server-side cursor.
- **Fetch size.** The first fetch is one row. Each next one is at most twice the one before, at most as many rows as
  fit about 2 MB at the largest row so far, and never more than 32 rows (`next_export_batch`). A `FETCH n` holds all
  n rows at once in libpq and in Python, and an export has no per-value cap. So the row ceiling is what bounds a
  batch of wide rows after a run of narrow ones (F-1 of `stu-021-review.md`). Measured as resident memory: rows of
  400 KB after 1 011 empty rows peak at about 15 MB (they were 388 MB with batches of 500), and rows of 1 MB peak at
  about 36 MB. As a rule, a batch of the widest rows can hold about 32 times the widest row, and twice that on
  PostgreSQL (libpq's copy and Python's), so the bound grows with the row width and is not a fixed 100 MB: rows of
  4 MB after narrow ones peaked at about 138 MB (R-1 of the `stu-021-review.md` delta check).
- **One huge value** still costs about three times its size while it is written (the driver's copy, the base64 or
  text form and the encoded bytes). The database's own limit is the only bound for a single value.
- **Encoding.** Each row is encoded at once. The bytes go into a queue of at most 8 chunks of about 256 KB, which
  the response reads.
- **Backpressure.** A slow client makes the producer wait, and the database cursor waits with it.
- **Stopping.** When the client goes away, or the response ends for any reason, the export is closed. Closing cancels
  the running statement and frees the export's slot. The response closes it itself when it ends, and does not rely
  on the body generator being finalized (F-8).

`tests/security/test_exports.py` covers three memory cases:

- It exports 1 million rows as CSV and as JSON under `tracemalloc` and checks the peak.
- It measures wide rows after narrow ones by resident memory in a separate process, on SQLite and PostgreSQL. That
  counts libpq's buffers, which `tracemalloc` does not see.
- It covers 300 000 rows through a PostgreSQL server-side cursor.

## Known gaps

- **Infinite and out-of-range dates (F-7).** psycopg cannot load PostgreSQL `infinity`/`-infinity` dates and
  timestamps, BC dates or years above 9999. The query engine and the viewer have the same limit. The export fails
  with `db.data`: before the first byte as a problem, after it as a cut download (TD-026).
- **File names (F-9).** A table export's file name keeps only `[A-Za-z0-9_.-]`, with leading dots removed, so
  `Ümlaut` becomes `_mlaut.csv` (TD-026).
- **Data-modifying CTEs (F-10).** On PostgreSQL, `WITH d AS (DELETE ...) SELECT ...` is refused by the server's
  `DECLARE CURSOR` as an unsupported feature (`db.unknown`), not as `db.read_only`. It is blocked either way.

## The page

The export dialog (`frontend/src/components/export/ExportDialog.tsx`, client `frontend/src/api/exports.ts`, TD-043)
implements 08 section 5 "Export": result → ⤓ CSV/JSON → dialog (the fetched rows, or query again up to a limit) →
download, with a hint when the export is cut.

- **Where:** the SQL editor offers "Export…" on a finished result. The export is that statement with the parameters
  it ran with, on the connection the result came from. The data viewer (STU-013) mounts the same dialog with a table
  as its source.
- **D-02:** the caller asks it through the shared guard (`useConnect().guard`) before it opens the dialog; for a
  result of the editor it was confirmed when the connection opened. The dialog never opens a connection.
- **Choices:** CSV or JSON; "the fetched rows of the result" (`max_rows` = the rows the page holds) or "up to" 1 to
  1 000 000 rows, checked in the page before anything is sent; for CSV the formula protection, on by default. The
  dialog says that the statement runs again (so the file holds the rows as they are now), that a cut result's export
  is cut too, and that a CSV file cannot mark a cut while a JSON file does.
- **Download:** `fetch` with the session's CSRF header; the stream is read chunk by chunk and the bytes received are
  announced. The file is kept only when its media type is the one asked for, and a JSON file only when it ends with
  the server's closing `"truncated"` member (an export that ended early is not saved). It is saved under the
  `Content-Disposition` name only when that has the server's form (`[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}` plus the
  format's suffix, no `..`), else as `export.csv` or `export.json`. A JSON file the server cut says so after the save.
- **Cancel:** the button, Escape, closing the dialog and leaving the page abort the request (`AbortController`); the
  server closes the export when the request goes away, and nothing is saved. A second press before the next render
  starts nothing.
- **Problems** are the page's en/de texts (`studio.export_invalid`, `studio.export_busy`, `studio.export_stopped`,
  a closed connection, and in the editor the statement's own wording). A stream that breaks after some bytes have
  arrived is `studio.export_incomplete` ("did not arrive whole"), not "the server does not answer".
- **Memory in the browser:** the file is held until it is saved (TD-043), at most `MAX_EXPORT_BYTES` (1 GiB): past it
  the page aborts the request (the server closes the export), drops the bytes, saves nothing and answers
  `studio.export_too_large` ("export fewer rows or columns"). The server bounds rows and time, not bytes.

Tests: `src/api/exports.test.ts`, `src/components/export/ExportDialog.test.tsx`, the export part of
`EditorView.test.tsx`, axe in jsdom (`a11y.test.tsx`); in Chromium `e2e/export.spec.ts` (axe in light and dark, English
and German, the keyboard flow and the download, cancel while streaming) and, against a real `tabrion-studio`, the
export test of `e2e/editor-backend.spec.ts` (CSV of the fetched rows with a parameter, JSON cut at 10 rows).

## Not in this package

- **Other formats** (Excel, Parquet, SQL `INSERT`s) are not in 03 section 2 D for 1.0.
