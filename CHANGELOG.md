# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- STU-020 (page): **Query history** (`/history`, ⌘Y / Ctrl+H): the statements you ran with their profile,
  environment, state and parameter names, never the values; filter by profile, search, "Open in editor" (runs
  nothing; the values are asked again), delete one or all after a confirmation; history settings and "Delete history"
  in Settings. **Favorites**: a star in the connection manager; the start page lists favorite and recently opened
  connections with Connect (through the manager, D-02 for PROD). **Scripts** in the SQL editor: Open… lists the
  project's `.sql` files, Save (⌘S / Ctrl+S, and D-06's Save) writes back to the script or asks for a path in the
  project, asks before overwriting a script that changed on disk, keeps CRLF line endings; "Download instead" stays.
  **Format** (⌥⇧F / Alt+Shift+F, sql-formatter 15.9.0, MIT) of the selection or the whole text as one undoable
  change. **Completion** of schemas, tables and views from the catalog of the open connection and of columns after
  `table.` (DEV and TEST); on PROD only a catalog already read, or after "Load names". The completion list now closes
  when a missing parameter takes the focus (TD-046 B-6). A history step on the start page.
- STU-020 review fixes (page): a script with mixed line endings is saved byte for byte (only an all-CRLF script is
  edited with LF); the tab title escapes invisible and bidi characters; the save dialog checks the server's new path
  rules; the Open… dialog says how many links it left out; texts for the new script problems; a cut history entry
  says so in the editor; the delete-all dialog says it also clears the user's own profiles in every project; the
  settings say that retention deletes old statements also while the history is off.
- STU-020 delta review (page): `visible()` also escapes zero-width and blank-looking characters (Hangul fillers,
  the braille blank, U+034F), the editor's accessible name shows a script's name through it, and the save dialog
  refuses blank-looking characters like the server.
- STU-020 (backend): Studio's state database `state.sqlite` in the state folder (0600, opened through
  `tabrion.sqlite.connect`, links refused, `secure_delete`); the query history of every finished query, per profile
  and project, with the statement text and its placeholder names but never a parameter value (`GET /api/history`,
  `DELETE /api/history`; settings `[history] enabled` and `retention_days`, 30 days by default, at most 5,000
  entries); favorite and recently opened profiles (`GET`/`PUT /api/favorites`); `.sql` scripts in the project folder
  (`GET /api/scripts`, `GET`/`PUT /api/scripts/file`: only `.sql` files inside the project, no links, atomic,
  byte-exact with the line endings kept, and bound to what the page read); `?cached_only=true` on the catalog, so completion never reads a PROD catalog
  unasked. New problem codes `studio.state_unavailable`, `studio.favorites_full`, `studio.catalog_not_loaded` and
  `studio.script_*`.
- STU-020 review fixes (backend): the history's retention period now holds on disk while nothing is recorded (the
  history off, an idle project): expired entries are deleted, with a checkpoint, when Studio starts, when the history
  is read and when the settings are saved. Script paths refuse Windows device names (also with an extension), 8.3
  short names, names that end in a dot and format characters (bidi, zero-width) on every system; the file is reached
  through folder descriptors with `O_NOFOLLOW` on POSIX (07 TM-04), and on Windows the opened handle must be the
  checked file; hard-linked files are refused. A save keeps the file's byte order mark and holds one lock from the
  digest check to the replace. Links, hard links and reparse-point folders (OneDrive placeholders) answer the new
  `studio.script_link`, and the listing counts what it skipped (`skipped_links`); refusals of the file system answer
  `studio.script_permission_denied`, `studio.script_locked`, `studio.script_disk_full` or
  `studio.script_write_failed` instead of an internal error. Recording a history entry waits at most 250 ms for
  another instance's write lock; the checkpoint result is checked and retried; the 100 favorites count per project.
- STU-020 delta review fixes (backend): a checkpoint of the state database never waits for another connection
  (`busy_timeout = 0`), so a reader holds up neither a query's end nor the store; a checkpoint that finds the log in
  use is retried on every purge until it succeeds. The purge after a settings save runs off the event loop and, like
  recording, waits at most 250 ms for another instance's write lock. Script names refuse blank-looking characters
  (spaces other than U+0020, Hangul fillers, the braille blank, the combining grapheme joiner).
- STU-023, the ER diagram: **ER diagram** next to **Objects** in the explorer draws the tables of one schema (at most
  150; **Tables** chooses them) with their columns, keys and a line for each foreign key, laid out by dagre and shown
  with @xyflow/react 12 (pan, zoom, overview map); 150 tables are laid out in under 2 s, and the page says how long
  it took. A table under the diagram lists the same tables and keys as text; every box's name is a button in the
  Tab order that shows the table in the tree. **Save as SVG** and **Save as PNG** (twice the size) are drawn in the
  page from the diagram, without any remote resource. New read route `GET /api/connections/{id}/diagram?schema=`
  (tables, columns, primary keys and keys out of a schema in three catalog reads; `studio.diagram_too_large` past
  1000 tables). New frontend dependencies `@xyflow/react` 12.12.0 and `@dagrejs/dagre` 3.1.3 (MIT, named in the
  plan). `npm test` runs the timed acceptance tests (`*.perf.test.ts`) after the unit tests, alone in one worker.
  Open items: TD-052.
- TD-008 (review C-2): the required CI job `T-2 path checks (windows-latest)` runs the T-2 path tests by name on
  Windows (no `nt._getfinalpathname` for a UNC value from the project file) and fails when one of them is missing,
  skipped or failed.

- STU-017 docs (no tag, no release): the documentation checklist `docs/development/docs-checklist.md` maps every
  requirement of the package (installation, start, security model, troubleshooting, the feature pages of TD-023) to
  its page and check, and records the clean-install test: on Linux by hand from a locally built wheel (start without
  Node.js, sign-in, a SQLite profile connected read-only, explorer and data viewer, `--stop`; passed), and automated
  in `scripts/smoke_wheel.py`, which now installs with the Tabrion pin, signs in, connects a SQLite profile and reads
  its rows (CI `packaging` job on Linux, macOS and Windows). The guide covers installing from a wheel, the residual
  risks, and troubleshooting while installing, in the views and for `--stop`; the installation page names the
  minimum OS versions and has PowerShell commands for Windows.
- TD-034: the banner **Connection lost** with **Reconnect** (08 section 5 "error recovery") above every page that
  holds a connection (explorer with its data and definition tabs, SQL editor, diagnostics, migration status), when a
  request finds the database gone or the connection closed, or when the header connection's check (every 30 seconds
  while the tab is visible; new route `GET /api/connections/{id}/health`) fails. **Reconnect** reads the profile
  again and asks D-02 again for PROD; the page goes on with the new connection. The server now replaces a lost
  connection on open instead of returning it; a busy pool (checked without waiting for a session) or a check that
  fails otherwise (a timeout) keeps the connection. The SQL editor no longer reopens a PROD connection without D-02
  when the server says it is gone.
- STU-050, UX acceptance of the MVP: `docs/architecture/ux-acceptance.md` maps every criterion of 08 sections 3 to 6
  and of the plan's acceptance to its test or manual check, with the results of the run on 2026-11-13 (SQLite and
  PostgreSQL 16, Chromium). `e2e/ux-acceptance.spec.ts` runs 08 section 5's "First start" (launch link, D-01, the
  start page, the first table's rows in under 60 s) and "Connect" (a failed test says what to check) against a real
  server with axe. User guide page "Migration status"; the guide's index and security page cover the views that
  landed and PROD profiles (D-02). Open items are TD-046 and TD-047.
- STU-016 preparation (not the gate G-S1): `docs/architecture/security-review-prep.md`, a readiness checklist that
  maps SQ-01..SQ-08 and the threat model T-01..T-16 to their code and tests, lists the open security-relevant TD rows,
  the residual risks and the points for the human reviewer. Two security tests from the internal pre-review:
  `tests/security/test_every_route.py` (every API route of the full application, read from the app itself, needs the
  session and the CSRF header and refuses other sites, unsafe methods refuse non-JSON bodies, every refusal carries
  the security headers, and nothing on disk changes; the only other `/api/` route is `/api/openapi.json`) and
  `tests/security/test_canary_flows.py` (a canary in the database, parameters, SQL text, a viewer filter and export
  parameters reaches no log line and no problem answer of the editor, viewer, export and diagnosis routes; exports
  Studio refuses included). The security headers are pinned literally in the tests
  (`test_security_headers_are_pinned`), not compared with the code's own constant.
  `docs/TODO.md`: the package rows of STU-012, STU-022 and STU-025 are done with their PR numbers, and merged pages
  carry their PR numbers instead of "draft".
- STU-052 (rest of TD-020): F6 and Shift+F6 also step through the panes of the main area (08 section 7,
  "Explorer/Editor/Ergebnis"): in the explorer the tree (its current item) and the details (the selected tab), in the
  SQL editor the SQL text, the first parameter value and the result grid. On other pages F6 stops at the main area as
  before. Keyboard-only end-to-end tests for every main flow of the MVP from the page's start, without a click:
  connect with the test and D-02 for PROD, the explorer, and a query with a parameter, then a cancel with Escape in
  the result and with Ctrl+. in the SQL text (`e2e/keyboard.spec.ts`).
- Accessibility audit of the work pages (STU-053, second part; `docs/architecture/accessibility.md`): the SQL
  editor with its grid, cell detail and export dialog, the explorer's tree, Data, DDL and Policies tabs and Count
  rows, the diagnostics report and D-02 in the connection manager. For each, `e2e/a11y-audit.spec.ts` checks target
  size, reflow at 320 px and at 200 % zoom, every Tab stop's focus ring (an open dialog keeps the focus), text
  spacing and reduced motion, and that each action's result is said in a live region (WCAG 4.1.3).
- STU-022 in the explorer page: a **DDL** tab for every object (a sequence shows its DDL on its own) with **Copy
  DDL**, a **Policies** tab for a PostgreSQL table (row-level security on and forced, the owner and the connection's
  role, whether the policies bind that role and why not, a warning when RLS is on without a policy, every policy with
  its command, roles, `USING` and `WITH CHECK`), and **Count rows** for a SQLite table or view (on request; a count
  past the server's timeout says so). Read-only on the explorer's open connection; every text escaped (line breaks and
  tabs in the DDL kept), the DDL cut after 100,000 characters with a note; answers for another object or connection refused.
  New user-guide page "Explorer".
- STU-013 page: the data viewer as the explorer's **Data** tab (08 section 4.5), read-only. Pages of 50 to 1,000 rows
  through the server's keyset tokens (Next, Previous, Reload), sort by a column, up to 20 filters the server binds as
  parameters, a search in the current page, and the SQL editor's grid and cell detail with "Load in full" (10 MB). A
  page asks for no more rows than fit in 64 MB at its column types' widest values, and says so; the paging hint
  shows without a primary key. The tab reads on the explorer's connection (D-02 through the shared confirmation),
  is bound to its connection and object, drops stale answers and hands a connection that is gone back to the
  explorer. The explorer gets "Show data", "SELECT … LIMIT 100 in the SQL editor" (a new editor tab; nothing runs by
  itself) and "Copy name". User guide page "Data viewer". Open items are TD-042. After the review: filters too long
  for one request (12 KB, below the server's 16 KB head limit) are refused in the form; the memory estimate counts 4
  bytes a character (code points above U+FFFF); the first page is read 150 ms after the tab opens, so passing
  through it reads nothing; "Load in full" is aborted when its cell is left.
- D-02 on the server (TD-038): `POST /api/connections` and `POST /api/connection-tests` take `confirmed_environment`,
  the environment the page confirmed for. A profile that is PROD now opens, is tested or has its open connection
  returned only with `"prod"`; otherwise 409 `studio.prod_confirmation_required` (a missing value counts as not
  confirmed). So a page with a stale profile list (the profile became PROD in another tab or in `profiles.toml`) no
  longer opens PROD without D-02: the connection manager reads the profile again and asks D-02 (at most once), and
  the migration status, diagnostics and explorer views and the SQL editor read their list again and ask on the next
  try. The page's `openConnection` and `testConnection` require the environment, so no caller can leave it out. A
  SQLite file is opened from the same read of the profile that passed the check. English and German.
- TD-043 (STU-021 in the page): the export dialog. **Export…** on a finished result of the SQL editor saves it as
  CSV or JSON: the fetched rows or up to 1 000 000 rows, with the CSV formula protection on by default. The statement
  runs again with its parameters on the result's connection (D-02 through the shared guard; no new connection). The
  dialog shows the bytes received, Cancel or Esc aborts the request and saves nothing, a file is saved only when it
  arrived whole and of the asked type, under the server's file name only when that has the expected form, and a JSON
  export the server cut says so. The page holds the file until it is saved, at most 1 GiB: past it the export is
  aborted, nothing is saved, and the dialog says to export fewer rows or columns; a stream that breaks after some
  bytes says the file did not arrive whole (not that the server does not answer). en/de; the data viewer (STU-013)
  can mount the same dialog with a table.
- STU-022 backend: DDL, row-level security policies and row estimates in the explorer, read-only.
  `GET /api/connections/{id}/tables/{schema}/{name}/ddl` rebuilds a PostgreSQL table, view, materialized view or
  sequence from the catalog (`pg_get_*def`) in the form of `pg_dump --schema-only`, and gives SQLite's stored text.
  `.../policies` lists PostgreSQL's policies, whether RLS is on and forced, and whether it applies to the connection's
  role. `.../row-count` counts a SQLite table or view on request, stopped after 10 seconds (`db.timeout`), and
  `GET /api/connections/{id}/row-estimates` gives PostgreSQL's `reltuples` per table; a partitioned table's estimate
  is the sum of its partitions' (also in the table detail). Each route also takes `?schema=&name=`. For the
  reference apps parts and measurements, the DDL equals pg_dump's statement by statement, in their own schemas and in
  `public`. The DDL includes rules, column statistics, storage and compression settings, replica identity,
  `CLUSTER ON` and a view's column defaults.
- TD-036 (STU-025 in the page): the Diagnostics view lists the backup hints (`STU-BK-*`) under "Backups", with
  en/de titles, not counted as findings; a broken `backup` field says that the hints could not be read. **Download
  Markdown report** and **Download JSON report** save the report of the shown connection (no new connection, so no
  new D-02) under the server's file name; a refused report (`studio.report_unavailable`, `studio.report_invalid`) is
  the page's en/de text, and Cancel, a profile switch or leaving the page drops it. The view's D-02 now uses the
  shared gate of the connection manager, so a PROD profile confirmed in the page session is not asked again. The
  diagnostics page route now replaces tabrion's `TAB-PG-020` with `STU-BK-010` like the report. New user-guide page
  "Diagnostics". After the review: a run that did not check the backups says "Backups were not
  checked in this run" instead of "No backup hints"; a canceled report's request is aborted, and the page says
  "canceled" only when the server confirms it; a double press starts one report.
- STU-012 SQL editor (MVP, read-only): the page `/sql` with CodeMirror 6 (PostgreSQL or SQLite highlighting and
  completion), editor tabs whose drafts are kept in the browser, run (the selection or the statement at the cursor), run
  script (statement by statement, stopping at the first error), cancel within a second, `:name` parameters (text,
  number, boolean, null, JSON), and a virtualized result grid (WAI-ARIA grid, keyboard, copy, a detail panel that shows
  the whole value as plain text). D-02 before the first PROD connection, D-06 before closing a changed tab, "Save"
  downloads a `.sql` file. At most 10 000 rows and about 64 MB are in the browser at a time, so 100 000 rows, and wide
  rows at the largest result size, stay far below the 300 MB limit. After the review: a profile change clears the tab's
  results, a run cannot be started twice while it connects and can be canceled then, the durations show (they read as 0
  before), D-02 names the profile's own timeout, drafts are kept per project, and a failed row page is asked for again.
  Before a new connection the editor reads the profile again, so a profile changed to PROD elsewhere is never connected
  to without D-02, and an open answer that does not name that profile's environment is closed again. New shortcuts in
  the help and the palette. CodeMirror runs in a shadow root, so the strict CSP stays unchanged. Open items are TD-033
  and TD-037. The migrations and diagnostics pages now have their own window titles.
- STU-010 page: the explorer (`/explorer`). Open a profile's read-only connection (D-02 first for PROD, once per
  page session as in STU-008; it becomes the header's connection) and browse its catalog as a WAI-ARIA tree
  (PostgreSQL schemas, type groups and objects plus the extensions; SQLite's file facts and type groups), filter it
  by name prefix and type (⌘P / Ctrl+P), and read an object's columns, constraints, indexes, relationships (a
  related table is one click or Enter away) and, on SQLite, `STRICT` and `WITHOUT ROWID`. Refresh reads the catalog
  again on the open connection. The page only reads; every database text is escaped and capped, the answers are
  parsed fail-closed, and stale answers after a profile switch are dropped. E2E against the parts and measurements
  reference apps (`e2e/explorer-reference.spec.ts`; not in CI yet). Open parts: TD-041.
  Follow-up: Open fails closed when the profile list cannot be read again (the offline problem, no D-02 and no
  connection), and a profile deleted meanwhile moves the picker to a remaining profile and the focus to it.
- STU-025 backend: `POST /api/connections/{id}/diagnostics/report` with
  `{"format": "markdown" | "json"}` runs a diagnosis with its backup hints and answers it as a file
  (`Content-Disposition: attachment`). The report is built from an allowlist: no password, token, connection URL or
  DSN, host, port, user or database name, file path, SQL text or parameter value. Every text also passes the log
  redaction, and the Markdown escapes text from the database. Canary tests cover both formats and the log
  (`studio.report_invalid` is new). Every diagnosis now carries backup hints (`backup: {hints, facts}`,
  `STU-BK-001..014`): SQLite backups next to the database (none found, older than the data, readable by others), and
  PostgreSQL's `archive_mode` and `pg_stat_archiver` (off, failing, nothing archived, a standby). tabrion's
  `TAB-PG-020` gives way to `STU-BK-010` only when that hint is given. After the review, the report shows the login
  role and the database as `[role]` and `[database]`, also where tabrion names them as schemas; Markdown escapes `@`;
  a linked `backups` folder is not followed; the file scan stops at a cancel or the time limit; a cut search is the
  hint `STU-BK-005`; "readable by others" means the read bits. After the delta review: a report whose names cannot
  be read from the server is refused (`studio.report_unavailable`, new), and the diagnostics page route keeps
  `TAB-PG-020` until the page shows the backup hints.
- STU-008 connection test and Connect (the parts that waited for STU-009): in the connection manager, **Test
  connection** shows the time, the server version and, for PostgreSQL, the role, superuser, TLS, encoding and time
  zone (for SQLite foreign keys, journal mode and encoding), with a warning for a superuser role or one that bypasses
  row-level security. `POST /api/connection-tests` answers these `facts` and the profile's `environment`. **Connect**
  opens the read-only connection and the header, the PROD frame and the window title show it; the first PROD
  connection of a profile in a page session asks D-02, the connection test of a PROD profile included (shared with
  the migration status view; it belongs to the profile's target and is forgotten when the profile is saved or deleted
  or the project is trusted again); **Disconnect** closes it. Only the newest connect request sets the header, and it
  never stays "connecting…" when an older request answers before or after a newer one that failed, and a connection that was open before keeps the
  header when both fail; a list of the open connections
  read while a connect ran no longer clears the new connection from the header. A
  missing password opens the password dialog and the action runs again. Failures name the remedy (host, port, user,
  password and TLS mode; a file that is no SQLite database), never the server's text; the separate class-28 and TLS
  texts are reached only by mocks with tabrion 0.1 (TD-038). The server version is the one people read (`16.15`).
  The assistant takes a pasted `postgresql://` URL apart (its password goes to the next step, never into the file;
  a refused URL loses its password from the field, `password` and `sslpassword` query parameters included) and tests the profile
  after saving. English and German.
- STU-014 page: the view Diagnostics (`/diagnostics`) runs the diagnosis of a chosen profile on a click and lists the
  findings by severity, each with an icon and a word for its severity, its code, title, detail, remedy and source.
  A PROD profile asks D-02 before its connection opens; "Run again" uses the shown connection. Cancel stops a
  running diagnosis (`.../diagnostics/cancel`), and a result with `STU-DX-008` says that it is incomplete; a profile
  switch or leaving the page cancels too, and an answer that arrives after a profile switch is dropped. The titles of
  Studio's own findings are in English and German; the server's text is shown escaped and capped (U-1), at most 100
  findings, and an answer the page cannot read shows nothing (`studio.answer_invalid`). The activity bar and the
  command palette offer "Diagnostics". After the review: an out-of-range `ran_at` no longer blanks the page, the
  buttons stay focusable while a run starts and ends (`aria-disabled`), and a cancel that does not take says so
  and can be tried again.
- STU-021 backend (on tabrion 0.1 like STU-011): `POST /api/exports`
  exports a query result or a table as CSV or JSON, streamed with bounded memory (1 million rows under 100 MB). The
  statement and object checks run before the first byte, so refusals are problem documents. Limits: at most
  1 000 000 rows, 10 minutes, one export per connection and 2 at a time. CSV escapes formula-like text and base64
  with a leading `'` (`escape_formulas`); JSON keeps rows as arrays and marks a cut export (`truncated`).
  After the review: an export fetches at most 32 rows at a time, so wide rows stay under 100 MB too. The fullwidth
  formula starts are escaped. Non-finite numbers are spelled `NaN`/`Infinity`. Large integers are text in JSON, and
  intervals are ISO 8601 durations.
- STU-024 backend: `GET /api/connections/{id}/migrations` also reports the
  Levuvo formats on PostgreSQL. It compares Nodestro's and Axentra's version tables with the project's SQL migration
  files (checksums as their runners record them; pending steps with an SQL preview), and Orkexa's version table with
  `SCHEMA_VERSION`. Studio reads these files as text and runs nothing. A version table the role may not read, or
  one with unexpected columns or types, is reported as `state: unreadable` with its problem
  (`studio.migrations_table` is new) and does not fail the answer.
- STU-015 backend: `GET /api/connections/{id}/migrations` with SQLite's
  `user_version` and the Alembic status. The Alembic `current` comes from the version table; `heads` and `pending`
  need the project's revision files to be imported, so they are read only after the user trusted the project's code
  (`GET`/`POST`/`DELETE /api/project/code-trust`, off by default, bound to the digest of `alembic.ini` and the
  revision files), in a separate process that gets only the connection's target and is killed after 30 seconds.
  Otherwise they show as `unknown (project not trusted)`. Threat-model entry T-16. New dependency extra
  `tabrion[migrations]` (Alembic). After the security review, the worker runs exactly the revision bytes it hashed
  and reads no bytecode of the project, loads the database driver before the project's folders are on `sys.path`,
  reads its answer with a size and time bound, and only one worker runs at a time (`studio.migrations_busy`).
  On Windows the worker runs in a job object that ends its children, and the backend tests are green there; a test
  that hangs for 300 s now ends the pytest run after its stack dump, so the CI log is kept.
- STU-015 page: the view Migrations (`/migrations`) shows the trust state of the project's Alembic files and, for a
  chosen profile, the status of every migration format.
  The trust dialog lists `alembic.ini`, the `script_location` and every revision file (escaped, like D-01), opens on
  "Don't trust", sends the digest it showed and shows the new list when the files changed meanwhile; the trust can
  be taken back. A PROD profile asks D-02 before its connection opens. English and German.
  The command palette offers "Go to Migrations" like the activity bar.
  The Nodestro, Axentra and Orkexa version tables that STU-024 reports are shown with their state, current version
  and the steps not applied (at most 20, then "and N more"; names escaped and capped), and an unreadable table says
  why (no read permission, unexpected columns, statement refused, timeout); a format the page does not know is
  counted in a fixed line, so a database with only such tables never reads as "no migration format" (C-1 of
  `stu-015-ui-review.md`). A status that arrives after the profile was switched is dropped.
- STU-014 backend (`tabrion.check` on the driver connection until tabrion 0.2):
  `POST /api/connections/{id}/diagnostics` with tabrion's findings (without those about Studio's own
  session) and Studio's `STU-DX-001..005`: a prod profile without a verified certificate, weak TLS to a remote host
  (T-10), a read-only role that can write, no statement timeout for the role or database, SQLite rows written with
  foreign keys off. Review fix round (`stu-014-review.md`): the diagnosis stops at the connection's statement
  timeout and can be canceled (`POST .../diagnostics/cancel`; `STU-DX-008` names what did not run, also SQLite's
  `quick_check`); `STU-DX-003` covers column grants, `TRUNCATE`, views, foreign tables, sequences and every role the
  session can `SET ROLE` to; `STU-DX-006` finds an owner role in any schema (it replaces `TAB-PG-003`, which sees
  the current schema only); `STU-DX-007` default privileges; role settings follow PostgreSQL's precedence, and
  `STU-DX-010` covers `lock_timeout`, `idle_in_transaction_session_timeout` and `application_name`; a broken foreign
  key definition is `STU-DX-009` instead of a failed route; no TLS finding twice, none for a Unix socket.
  Delta review: on SQLite a time limit or cancel stops tabrion's `quick_check` alone, so tabrion's other findings
  (a 0644 file's `TAB-SQ-003`, say) stay and `TAB-SQ-006` says "not checked"; `STU-DX-009` calls only "foreign key
  mismatch" and "no such table" a broken definition (a lock or I/O error gets a neutral text). A deterministic test
  requests the limit or the cancel before tabrion's step starts on SQLite (finding 3 of
  `stu-014-delta-v010-review.md`).
- STU-013 backend (on tabrion 0.1 like STU-011):
  `GET /api/connections/{id}/tables/{schema}/{name}/rows` (pages of 1 to 1000 rows, sort, JSON filters, `next`
  token) and `GET .../cell` (one value in full, up to 10 MB). Every statement has a `LIMIT`; pages go by primary
  key, by `rowid` or `ctid` without one, and by `OFFSET` for views (`hint: unstable_order`). Column names must be
  reflected columns, filter values are bound parameters, and key values stay in the backend. Past the profile's
  timeout the answer is `db.timeout`.
  - Review `stu-013-review.md`: PostgreSQL pages without a primary key go by `(tableoid, ctid)`, so partitioned
    tables and inheritance parents lose no rows; keys are compared in the column's own type (a `real`, `jsonb` or
    array sort no longer loops or fails on page 2), and `json`, `xml` and geometric columns are refused for sorting
    (`studio.viewer_sort_unsupported`); NULLs in a SQLite primary key are ordered by the row id; the cell detail by
    `ctid` or `rowid` answers `studio.viewer_row_gone` when another row took the id; lengths are measured on the
    server before values are fetched (64 KB per cell, `max_result_mb` per page with `truncated: "bytes"`, 10 MB of
    bytes for the cell detail, fetches of about 2 MB); tokens are bound to the session, at most 64 per session and
    connection, and dropped with their connection; reflection is cached for 10 s; at most 4 viewer statements at a
    time, 2 per connection.
  - Delta re-review of `stu-013-review.md`: a primary key column whose name starts with `@` is a quoted column like
    any other (the row identity is typed, not marked by a prefix); every PostgreSQL session starts with
    `extra_float_digits=3`, and a keyset walk that makes no progress goes on by `OFFSET` instead of repeating pages;
    the fetch batch is capped by the widest row the byte caps allow, so a small first row cannot inflate it; a
    decoded `json`, `jsonb` or array value counts toward the batch and the page cap at the memory its Python objects
    take, not at its text bytes.
- STU-010 backend (reflection through `tabrion_compat.inspector` until tabrion 0.2):
  `GET /api/connections/{id}/catalog` (schemas and objects by kind, cached until `refresh`),
  `GET /api/connections/{id}/tables/{schema}/{name}` (columns, keys in and out, constraints, indexes, row estimate),
  `GET /api/connections/{id}/extensions` (PostgreSQL) and `GET /api/connections/{id}/sqlite-info` (pragmas, file
  size and mode, `STRICT`, `WITHOUT ROWID`). 500 tables in under a second.
  - Review `stu-010-review.md`: PostgreSQL foreign keys come from `pg_constraint` (a key into a search-path schema
    such as `public` kept the wrong schema); an object dropped after the catalog was built is
    `studio.object_not_found`; a busy pool is `studio.connection_busy` (503); unknown names rebuild the catalog at
    most every 5 seconds; a closed connection's catalog is dropped at once; `GET /api/connections/{id}/table?schema=&name=`
    for names with `/`; SQLite facts without `PRAGMA table_list` before SQLite 3.37.
  - Delta re-review of `stu-010-review.md`: a partitioned table's foreign key is shown once, without its
    partitions' copies (N-1); a query that finds no free session is `studio.connection_busy` too (I-9); SQLite
    before 3.37 still reports `without_rowid` as true or false.
- STU-011 (on tabrion 0.1 behind `services/tabrion_compat.py` until tabrion 0.2):
  - The query engine and the routes `POST /api/queries`, `GET /api/queries/{id}`, `GET /api/queries/{id}/events`
    (server-sent events), `GET /api/queries/{id}/rows` and `POST /api/queries/{id}/cancel`.
  - One checked statement per `Database.read()`, through `ConnectionRegistry.get`; `:name` parameters are bound by
    the driver (psycopg `%(name)s`, sqlite3 `:name`), never through SQLAlchemy `text()`, so a colon inside a literal
    stays text.
  - Limits of 03 section 5.3: 1 000 rows by default up to the `max_rows` setting, 20 MB per result, 64 KB per cell,
    the profile's timeout for the whole query, 4 queries at a time (2 per connection), the last 16 results kept.
    PostgreSQL queries are read through a server-side cursor.
  - Cancel within a second (PostgreSQL `cancel_safe()`, SQLite `interrupt()`); a canceled PostgreSQL connection
    leaves the pool.
  - No SQL text, parameter value or row in the log.
  - Review `stu-011-review.md` (and H-1 of `tab-033-review.md`):
    - M-1, M-2, I-2: `check_statement` allows a statement only by its first word (`SELECT`, `WITH`, `VALUES`,
      `TABLE`, `EXPLAIN` of a query, `SHOW`, `PRAGMA`), so `DECLARE ... WITH HOLD`, `FETCH`, `MOVE`, `CLOSE`, `SET`
      and every write are refused before the database. PostgreSQL connections get Studio's session settings as
      startup options and run `RESET ALL` on check-in.
    - M-3: a fetch loads about 2 MB at most (one row first, then as many as fit).
    - L-1: `OpenConnection.stream` checks the driver text it executes.
    - L-2, H-1: a connection that got a cancel is invalidated on both engines (closed if that fails), with tests of
      both cancel guards.
    - L-3: one cancel worker per query. I-3: the byte cap is the new `max_result_mb` setting (20 MB, up to 200 MB).
  - Delta re-review of `stu-011-review.md`:
    - R-H1: the first word of a statement comes from the lexer, read the way PostgreSQL (with and without backslash
      escapes) and SQLite lex it; the readings must agree. `-- x\r<statement> /*\nshow */` and a SQLite nested
      comment can no longer hide a statement. The check-in reset also closes held cursors and session-level advisory
      locks.
    - R-M1: the fetch budget follows the largest row so far, grows at most twice per fetch and counts arrays and json
      in full.
    - R-I1: a `search_path` or `transaction_timeout` session setting is refused at startup (Studio sets neither).
  - Delta re-review 2 of `stu-011-review.md` (head 70ca2f9):
    - R2-L1: white space and `--` comments are skipped with one regular expression, so checking a text with many line
      comments is linear (512 k characters of `--\n`: 10 s before, about 0.2 s now); the same for the placeholder
      scan.
    - R2-I2: a text with a NUL character is refused.
    - R2-I1: the check-in reset also takes back a role change (`SET SESSION AUTHORIZATION DEFAULT`, `RESET ROLE`),
      which `RESET ALL` keeps.
  - Delta review `stu-011-leak-delta-review.md`:
    - A session that a running statement returns after its connection was closed is closed at check-in, not left
      open in the disposed pool (the unclosed-database ResourceWarning); the listener holds its Database weakly.
    - F-4: the cancel hook is detached before the cursor closes, so a late cancel can no longer hit the server
      cursor's `CLOSE` and leave it open (the flaky `ServerCursor ... deleted while still open` warning); a cursor
      that cannot close is closed with its connection.
- STU-009 (tabrion v0.1.0 pinned by full SHA):
  - The connection registry and the routes `POST`/`GET`/`DELETE /api/connections` and `POST /api/connection-tests`.
  - Every connection is read-only. SQLite: `mode=ro`, `query_only` and an authorizer allowlist (no ATTACH, no
    VACUUM INTO, no `load_extension`, no write or schema change). PostgreSQL: a read-only session, `Database.read()`,
    one statement per transaction and no transaction control.
  - URLs only through `tabrion.safe_url`; the driver gets only the fingerprinted fields.
  - `db.<kind>` problems from `tabrion.errors.classify`, and tabrion's `DbErrorRedactionFilter` in the log handlers.
  - Engines are disposed on shutdown.
  - Review `stu-009-review.md`:
    - H-1: every PostgreSQL statement is a prepared statement (`prepare_threshold=0`), so the server refuses several
      commands, whatever a text split thinks (the `standard_conforming_strings = off` escape no longer writes).
      `SET standard_conforming_strings`, `SET backslash_quote` and `set_config(` are refused.
    - M-1: without a password, libpq gets none of its own (no `PGPASSWORD`, `.pgpass` or service-file password).
    - L-1: the password is resolved from the same entry as the URL.
    - Info items: a connection test shares the connection limit.
  - Delta re-review of `stu-009-review.md`:
    - R-L1: the function check reads the statement's tokens (comments and literals left out, quoted identifiers
      unquoted, `'...'` lexed with and without backslash escapes), so `"set_config"(` and `set_config/**/(` are
      refused; so are `U&"..."` identifiers.
    - I-R1: `pg_notify()` and the session-level advisory lock functions are refused like `NOTIFY` and `LOCK`.
    - R-L2: a requirements or constraints file, at any depth, holds only plain requirements and the full-SHA tabrion
      pin: pip options (`-e`, `-r`, `-c`, `-f`, index and host options), paths, wheel files, other URLs and
      fragments are refused.
    - I-R2: a test that fails when the secret comes from a second read of the profile.
  - Re-review 2 of `stu-009-review.md`:
    - R2-L1: `DO` and the functions that run a query given as text (`query_to_xml`, `cursor_to_xml` and their schema
      forms, `ts_stat`, `ts_rewrite`, `crosstab`, `dblink`) are refused.
    - R2-I2: tests for both ways of lexing `'...'` and for `E'...'` escapes.
    - R2-I1: a requirement's marker cannot carry a pip option (`--config-settings`, `--global-option`, `-e`).
    - CI: the PostgreSQL service port is published on `127.0.0.1` only.
  - The dependency rule follows the ADR 0009 amendment (no PyPI publication): a full-SHA tabrion pin is allowed in a
    constraints file, and `pyproject.toml` keeps the final range with no direct reference.
  - tabrion v0.1.0 (`d5821faa8742dea012abda3b663549c038d76543`, the merge commit of tabrion PR #56) is pinned in
    `constraints/tabrion-integration.txt`, and `pyproject.toml` declares `tabrion[postgres,sqlalchemy]>=0.1,<0.2`.
    CONTRIBUTING and the guide's installation page install with `-c constraints/tabrion-integration.txt`.
- TABRION QA-21 (AC-7): `scripts/check_tabrion_api.py` is now the check shared with axentra, orkexa and
  nodestro-runtime. Besides private Tabrion modules it refuses `_` attributes on Tabrion names, `getattr` and
  `import_module` with a private constant and star imports. Every used name must be in its module's `__all__` in the
  installed Tabrion (a used name without an installed Tabrion fails), and `--surface` compares with Tabrion's frozen
  API snapshot and refuses experimental (`X`) names. `--export` writes the used names to `docs/api-consumers.md` (in
  the docs under Development), and `tests/architecture/test_tabrion_api_export.py` fails when that file is stale or
  missing.
- Reproducible-wheel check (QA-8): the packaging tests build the wheel twice more and require the same SHA-256 as the
  CI build, with the build backend pinned exactly (`hatchling==1.33.0`, hashed in `build-constraints.txt`); the QA-8
  evidence (audits, SBOMs, hashes) is in `docs/development/ci.md`. A fixed `SOURCE_DATE_EPOCH` build is tested too, and
  `scripts/check_frontend_reproducible.py` checks by hand that two frontend builds match.
- Conventions audit (TABRION QA-22): `docs/development/conventions.md`, `tests/security/test_conventions.py` (license,
  security policy, community files, Dependabot policy, CI manifest), and the missing `CODE_OF_CONDUCT.md` and
  `SUPPORT.md`.
- Project skeleton: Python package `tabrion_studio` (hatchling, ruff, mypy strict, import-linter) and the React 19 /
  TypeScript 5.9 / Vite 8 frontend in `frontend/` (eslint, prettier, strict `tsc`, vitest) with a placeholder page
  (STU-001).
- `tabrion-studio` command and process model: loopback-only server on a free port, one instance per project folder
  (a second start reuses it), idle exit after 30 minutes, `--stop`, clean shutdown on Ctrl+C and SIGTERM (STU-002).
- CI (`ci.yml`, `ci-ok.yml` with the commit status `ci-ok`, `nightly.yml`) on Linux, macOS and Windows; the wheel
  build hook that requires the built frontend; a wheel smoke test without Node (STU-018).
- License policy check with negative probes and CycloneDX SBOMs for the Python runtime and the frontend (STU-031).
- Access protection (STU-003, `docs/architecture/access-protection.md`): one-time launch token in the URL fragment
  (`/launch#<token>`, 120 s, once), exchanged by the page through `POST /api/session` for an `HttpOnly;
  SameSite=Strict` session cookie (`tabrion_studio_session_<port>`) and a CSRF value kept in `sessionStorage` and sent
  as `X-Tabrion-CSRF`; `tabrion-studio --print-url` prints a one-time sign-in link. Every request passes the access
  middleware: Host allowlist `127.0.0.1:<port>` on every route, no forwarded headers, Origin and fetch-metadata
  checks, JSON-only API requests, session plus CSRF header on every API route except health and the launch exchange.
  Every answer carries a strict Content-Security-Policy, `Cache-Control: no-store`, `nosniff`, `no-referrer`,
  `X-Frame-Options: DENY` and same-origin opener and resource policies (STU-T-001..006, -014, -025, -026).

- App shell, design system and languages (STU-005, `docs/architecture/frontend.md`): header, activity bar, status
  bar and the views Start, Connections, Settings and Not found; `--tb-` design tokens with a complete light and dark
  theme (system default, selectable) and compact or normal density; components copied from axentra-studio with a
  provenance note; German and English (i18next), with a completeness test. Contrast test of the tokens in both themes
  and axe without serious or critical violations (jsdom in CI; Chromium with colour contrast in `frontend/e2e/`).
- Status displays and dialogs (STU-051): environment badge (DEV, TEST, PROD) in the header, on every tab and in the
  window title (`[PROD] name – Tabrion Studio`) with a red frame for PROD; access mode, transaction and connection
  displays; the dialogs D-01, D-02, D-06 and D-08 with tested focus rules (initial focus on the safe option, focus
  trap, Escape cancels, focus returns to the opener).
- Brand assets (`assets/branding/`): the Tabrion Studio lockup, marks and favicons derived from Peter Karlhuber's logo
  master with the shared brand kit (no redraw, no CSS filters), the social preview, and `TRADEMARKS.md` (the names and
  logos are not MIT). The committed masters carry no C2PA or eXIf metadata; a test proves the pixels unchanged.
- Keyboard and command palette (STU-052, first part; `docs/architecture/frontend.md`, "Keyboard"): ⌘K / Ctrl+K opens
  a command palette (go to a view, theme, density, language, help) with the combobox pattern; ⌘/ / Ctrl+/ shows the
  keyboard shortcuts; F6 and Shift+F6 move between header, activity bar, tabs, main area and status bar. Shortcuts are
  shown with the keys of the platform (⌘ on macOS, Ctrl or Strg elsewhere); the header has buttons for both dialogs.
  In German the theme commands read "Darstellung", like the settings view.
- Language, theme and density are saved in Studio's `settings.toml` as soon as they are chosen, and applied from it
  when the page opens. They were browser preferences only, which a browser keeps per port, so they were lost at every
  start (TD-021). `settings.toml` accepts `density = "compact" | "normal"`. A file with issues or one written by hand
  is not changed from the general settings; the choice then stays in the browser and the view says so. The command
  palette's theme, density and language commands are saved the same way.
- Accessibility audit of the shell (STU-053, first part; `docs/architecture/accessibility.md`): WCAG 2.2 AA
  checklist, and `e2e/a11y-audit.spec.ts` checks target size, reflow at 320 px, text spacing, focus visible and not
  obscured, reduced motion and the landmarks. Fixed on the way: a PROD workspace no longer scrolls sideways at 320 px
  (the header wraps; the tabs' screen-reader texts stay inside the tab list), the D-01 "Show full value" and "and N
  more" buttons are 24 px high, the tooltips can be hovered and hidden with Escape, and a click beside a focused
  link no longer follows it through its tooltip (WCAG 1.4.10, 2.5.8, 1.4.13). A long connection name or tab title
  is cut with "…" and shown in full as a title; below 720 px the logo link keeps its accessible name (4.1.2). After "Go to …" in the
  command palette the focus moves to the new page's main area.
- The command prints a hint when the default browser on Linux is a snap or a Flatpak, which cannot open the sign-in
  file in a hidden state directory (TD-002, review S-4 of `stu-003-review.md`).
- CI job `e2e` (mandatory check): the Playwright suites in Chromium on every pull request, namely axe with color
  contrast in light and dark and in English and German, rendered contrast, dialog focus rules, status displays, and
  `e2e/backend.spec.ts` against the built wheel in a real `tabrion-studio` under the CSP. It installs the Chromium build
  of the locked Playwright (`npm run e2e:install`) and fails if a required suite was skipped (review U-3 of
  `stu-005-051-review.md`). `npm run e2e` runs the suites locally.

- Problem JSON and logging (STU-004, `docs/architecture/errors-and-logging.md`): every error answer is
  `application/problem+json` with `code`, `kind`, `status` and fixed `title`, `detail` and `hint` texts from one
  catalog, including the access refusals, 404, 405 and validation errors (which no longer echo the input); an
  unexpected exception gives `studio.internal_error` with a log ID and is logged once, redacted. Logs go to a rotating
  JSON-lines file per project instance (5 x 5 MB, 0600 in a 0700 folder, symlinks refused) and WARNING and above to
  stderr; `--log-dir` and `TABRION_STUDIO_LOG_DIR` choose the folder (default: the per-user log directory, or
  `STATE_DIR/logs` with `--state-dir`). A request log names the method, route template, status, duration and problem
  code, never the query string. Uvicorn's loggers use the same handlers; its access log stays off. Canary tests
  (STU-T-008, -009) prove that secrets, parameter values, server `DETAIL` texts and tokens reach neither an answer nor
  a log line.

- Configuration, profiles and project trust (STU-006, `docs/architecture/configuration.md`): TOML schema version 1 for
  the project's `.tabrion/studio.toml`, the user's `profiles.toml` (must be 0600) and `settings.toml`, with key-path
  issues that never repeat a value; `--config-dir` and `TABRION_STUDIO_CONFIG_DIR`. `GET /api/project`,
  `POST /api/project/trust`, `GET /api/profiles` and `GET /api/settings`. Project profiles are used only after the
  user confirmed the file's exact bytes in D-01; a changed file needs a new confirmation and shows the diff. D-01 now
  lists each connection, host, file reference, password variable and unknown key as its own item with control and
  bidi characters escaped, long values and lists capped, and the real totals (binding requirement U-1 of
  `stu-005-051-review.md`). The German D-01 text says "definiert" (review wording nit).

- Secret management (STU-007, `docs/architecture/secrets.md`): `keyring:` references use the OS keyring (service
  `tabrion-studio`, one entry per profile and project folder) with no plain-text fallback (the `fail`, `null` and
  `keyrings.alt` backends are refused); `env:NAME` reads the process environment; `file:PATH` is read with the checks
  of review C-3 at read time (no `~` expansion, containment, no link on the path, owner, 0600, one link, no FIFO,
  4 KB); a password can be entered for the current session only. `PUT` and `DELETE /api/profiles/{id}/secret` store
  and remove an entered password (204, never echoed), `GET /api/secrets` says whether a keyring is available, and
  `GET /api/profiles` shows only `secret: {source, has_secret}`. Every resolved or entered secret is redacted from the
  logs. `file:` values are never URL-decoded or Unicode-normalized.
- Re-review of PR #9 (`stu-conditions-rereview.md`): R-1, the link walk refuses a step to `/net/<host>` or another
  network form before it `lstat`s it (a relative link that climbs to `/net` triggered the macOS automount); R-3, a
  leading `\??\` is refused as text.
- Connection manager and server settings (STU-008, `docs/architecture/configuration.md`): the Connections view lists
  the project's and the user's profiles with a filter and a detail panel; the three-step "New connection" assistant
  (database, password source, name and the required environment) creates and edits the user's profiles; a profile
  can be deleted (Cancel is the default) and a password entered for the keyring or the session. The Settings view
  saves the query limits and the SQL logging opt-in. `POST`, `PUT` and `DELETE /api/profiles` and `PUT /api/settings`
  write only Studio's own `profiles.toml` and `settings.toml`: into a private folder, atomically with 0600 and
  `O_NOFOLLOW`, bound to the digest the page read (`studio.config_stale`), never over a file with issues, and over a
  hand-written file only after a confirmation. Refused forms name fixed keys and codes, never values. The project's
  file is never written. Testing and connecting wait for STU-009.
- Review of PR #10 (`stu-007-review.md`): C-1, a stored keyring or session secret is bound to a fingerprint of the
  connection target (engine, host, port, database, user, path), so a project profile that keeps its name but points
  to another host no longer gets the saved password, and D-01 lists it under "Saved password not used"; I-1, secrets
  of 4 to 7 characters are redacted too; I-3, one leading BOM is removed from a `file:` secret; I-2, the FIFO test
  fails instead of hanging, a deterministic link-swap test, and test servers use the null keyring backend.

- Review of PR #11 (`stu-008-review.md`): the effective TLS mode is part of the saved-password fingerprint (a TLS
  downgrade asks for the password again); a lone surrogate in a field is a form issue and a file that would not read
  back is a 422, not a 500; a keyring that refuses to delete a saved password is logged and reported; a save reads
  the file again just before it replaces it; the e2e server uses the null keyring; an `env:` password reads "set in
  the environment".

- Network log check (QA-9, SQ-08): `e2e/network.spec.ts` visits every view, dialog and notice of the e2e harness, and
  `e2e/backend.spec.ts` the real server, and fails if the page requests any other host (no CDN font, telemetry,
  external image or script). It listens on the browser context, so popups, new pages and workers count too.
- User guide (STU-017, first part; `docs/guide/`): installation from a Git commit, starting and stopping with every
  option, connections and the trust dialog, passwords, settings and keyboard, how Studio protects your data, and
  troubleshooting with the messages of the page and the command. A test checks that every command-line option has a
  row in the guide's options table and that every guide page has an entry in the site navigation.

- Dependency policy (QA-17, AC-2): `dependency-policy.json` is this repository's copy of the dependency policy K-33
  from nodestro-studio (only its outgoing edges: `tabrion[postgres,sqlalchemy,migrations]` in the core), and
  `scripts/check_dependency_policy.py` is the unchanged checker from there; `scripts/DEPENDENCY_POLICY_SOURCE.md`
  records their origin and SHA-256. `python scripts/check_dependency_policy.py` checks `pyproject.toml` (core, every
  extra and dependency group) against the copy. `tests/architecture/test_dependency_policy.py` checks the checksums,
  runs the checker and probes forbidden requirements (Axentra, a platform, extras beyond the edge). Ruff skips the
  vendored script (it is linted in nodestro-studio). The sdist ships the policy copy.

- Import-contract mutation probe (QA-16, AC-1): `tests/architecture/test_forbidden_probe.py` runs `lint-imports` on
  the repository and on a copy of the package, and on copies with one forbidden import each, which must fail and name
  the broken contract (every contract has at least one probe). It also checks that the edge to `tabrion` stays
  allowed (K-33), that the K-33 contract names every known Levuvo platform, and scans for imports of any `orkexa*`,
  `axentra*`, `nodestro*` or `levuvo*` package by prefix.

### Security

- TD-055: Studio's log redaction no longer depends on Uvicorn's loggers being unconfigured. `configure_logging`
  takes over Uvicorn's and asyncio's loggers (their own handlers removed, propagation on, the redaction filters on the
  loggers themselves) and restores them on close, so a `uvicorn.Config` with the default `log_config` or another
  `dictConfig` in the same process cannot make Uvicorn print a failed lifespan's traceback text unredacted. The
  `tabrion-studio` command was not affected (it passes `log_config=None`). Tests restore the logging state after
  each one, which fixes the lifespan canary failing after `tests/unit/test_server.py`.

- Security review F-6 / TD-031: the Python runtime dependencies are installed from the hashed lock
  `constraints/runtime-lock.txt` (exact versions, SHA-256 of every file, `pip --require-hashes --no-deps`, wheels
  only) in every `ci.yml` job, in `scripts/smoke_wheel.py` and in the installation guide; Tabrion stays pinned by its full
  commit SHA. `scripts/runtime_lock.py check` keeps the lock inside the unchanged `pyproject.toml` ranges and in step
  with `build-constraints.txt`, `check-installed` fails a CI job whose runtime closure differs from the lock, and
  `update` regenerates it (`docs/development/ci.md`, "Hashed runtime pins"). The CI wheel builds now take the build
  backend from the hashed `build-constraints.txt`. `tests/security/test_workflows.py` now loads every workflow with PyYAML
  (a dev dependency), because an unparsable `ci.yml` ran no job and no check noticed (review of PR #76, B-1/M-1).

- STU-016: the independent security review was re-run on master `2a8a229` and accepted (F-1..F-4, F-8, F-9 fixed in
  PR #65 and PR #66). `security-review-prep.md` and `docs/TODO.md` record this and the owner decisions still open
  for G-S1 (F-5/TD-033, F-6/TD-031, F-7/T-16, TD-005, TD-002/TD-007, TD-003/TD-008, the PostgreSQL read-only model);
  G-S1 is not passed, and the human review before a public release (STU-028, G-R) stays required. New TD-053.
- STU-016 review F-7 (T-16, TD-054): the migration code trust also covers the project modules under the
  `prepend_sys_path` folders (every `*.py` in identifier-named folders, by path and bytes), so a changed or new
  module asks for trust again. The digest is versioned: a project trusted before needs trust again. Over the caps
  (5 000 modules, 32 MB, 256 KB per module) or with a link out of the project, the code cannot be trusted. The
  worker runs only the hashed module bytes, and the trust dialog lists the modules and says what is checked (en/de).
  Review C-1 of the fix: project folders on the interpreter's path are loaded through the hashed loader too. An
  import guard first on `sys.meta_path` holds every finder (also those of an editable install in Studio's
  environment) to the hashed bytes for any module in the project folder outside Studio's installation, and refuses
  the rest; the worker starts with `-S` and runs `site.main()` under the guard. Studio running from a source
  checkout inside the project does not read the revisions (fail closed). The page names the file when the trust
  state is invalid because of one file (`issue_file`, review I-1). Delta review: the guard goes back to the front of
  `sys.meta_path` after `site.main()`, and the worker fails closed when a finder is in front of it before the
  revisions are read (C-2); a namespace package from Python's path finder is no longer refused (N-1).
- STU-016 review, read-only path: the statement check refuses write-capable statement forms on the read-only path
  (any `INTO` outside literals, comments and quoted identifiers, and a number directly followed by a word character,
  so numbers must be written in decimal) in the editor, the
  export route and every other path; PostgreSQL sessions drop temporary tables when they go back to the pool
  (`DISCARD TEMP`); the diagnostic `STU-DX-003` also reports `CREATE` on a schema (warning, error on PROD) and `TEMP`
  on the database (an info on its own). (F-1)
- STU-016 review, SQLite: the authorizer's function branch is an allowlist of SQLite's built-in reading functions,
  so `fts3_tokenizer`, `fts5`, the FTS4 maintenance and R-tree debugging functions and anything a build adds are
  denied; every SQLite session sets `trusted_schema = OFF` before the authorizer. (F-2)
- STU-016 review, PostgreSQL side-effect functions: the statement check also refuses functions whose effect a
  read-only transaction does not stop (other sessions, the WAL, replication slots and origins, transaction IDs,
  server reloads and resets, server files, index maintenance, every `dblink` function, and `connectby` and
  `xpath_table`, which run a query given as text); the list and the reason for each are in
  `docs/architecture/connections.md`. (F-3)
- Request bodies are capped while they stream in, before any route reads them (STU-016 review F-4). A body over the
  route's cap gets 413 without any of it being kept: a chunked body is stopped one chunk past the cap, and a declared
  `Content-Length` over the cap is never read into memory. Before the 413, the rest of the body is read and discarded
  (at most 4 MiB and 2 seconds), so the client gets the 413 instead of a connection reset; a declared length more than
  that over the cap is answered at once. The answer closes the connection. Caps: 4 KB for `/api/session` and the
  control endpoints, 2 MB for `/api/queries` and `/api/exports` (`studio.query_too_large` as before), 16 KB for every
  other route (new problem code `access.body_too_large`). The routes' own, smaller caps are unchanged; an oversized
  `/api/session` or `/api/control/launch` body is now 413 `access.body_too_large` instead of 400
  `access.bad_request`.
- The access middleware checks the control token itself for every request under `/api/control/` except `GET` and
  `HEAD` of the proof, so a control route added later is never reachable without it, and an `/api/*` path with a `.`
  or `..` segment is 404 before routing (STU-016 review F-8).
- `docs/architecture/security-review-prep.md` records the outcome of the first independent security review (not
  accepted at `e186178`): SQ-05, SQ-07, T-06 and T-13 are not met until the read-only hardening PR is merged and
  re-reviewed, and F-5, F-6 and F-7 are owner decisions (STU-016 review F-9).
- Catalog reads now use a fixed search_path; hardening against object resolution through user schemas (STU-022
  review). User SQL, the viewer and the exports keep the session's `search_path`.
- Opening a PostgreSQL connection now runs the driver's and SQLAlchemy's setup queries under a fixed search_path;
  hardening against object resolution through user schemas (TD-039). The session's own `search_path` is restored
  before the connection is used, so user SQL, the viewer and the exports see the path the user configured.
- `hstore` values now come back as the server's text (`"a"=>"1"`) in query results, the data viewer and the exports,
  not as a mapping: the driver's native hstore adaptation is off, so no type lookup runs when a connection is opened
  (TD-039).
- D-01 shows where each PostgreSQL connection's password goes (TD-010 T-5, T-4 rest; `stu-007-review.md` L-1): the
  secret's source, `host:port` and the TLS mode, marked for a host outside loopback, TLS weaker than `verify-full`, a
  variable that looks like another service's credential and a password file outside the project. Projects trusted
  before this change show as changed once and must be confirmed again.
- Conditions of the reviews of PR #7 (`stu-004-review.md`) and PR #8 (`stu-006-review.md`):
  - L-1 (C-1): exception text that reaches a log record as its *message* is removed too. Records of the `asyncio`
    logger keep only a known fixed first line (no `repr` of a task, future or callback with its exception or
    arguments), a formatted traceback in a message (Starlette's lifespan failure, which Uvicorn logs as text) is cut,
    Studio's lifespan logs its own exception with frames only (`studio.lifespan_failed`), and an exception passed as a
    `%s`/`%r` argument formats as its type. L-2: the scrub also removes `db_password=`, `PGPASSWORD=`, `*_token=`,
    `sslpassword=`, `auth_secret:`, `privatekey=`, `Authorization: Basic ...` and URL passwords that contain `@` or
    `/`; every pattern runs in linear time. L-3: a traceback keeps the first and last 20 frames of each exception and
    is capped at 32 KB per record.
  - T-1 (C-1): a trusted project file stays trusted only while the review equals the one the user confirmed, so a
    database folder that later becomes a link out of the project makes it `changed`; `ProjectConfig.database_file`
    re-checks containment when a database file is opened (for STU-009).
  - T-2 (C-2): UNC (`//host`, `\\host`), device (`\\?\`, `\\.\`), drive-relative (`C:foo`), `file://host` and
    `/net/<host>` paths in a profile file are refused as `config.network_path`, and containment is decided as text
    before any file-system access; no value from a project file is passed to `Path.resolve()` (on Windows that opened
    an SMB connection to the named host).
  - T-3: deeply nested TOML is `config.too_deep`, a damaged trust record means untrusted, and the user's own profiles
    still load when the project file cannot be read (no more HTTP 500). T-4: only `localhost`, a loopback IP address
    and a socket folder default to `sslmode = "prefer"`; `127.evil.example` gets `verify-full`.
  - I-2: D-01 shows "Unknown keys removed" and says when it shows a file that changed after it was opened
    (`studio.trust_stale`); an invalid project file gets a notice with its issues (English and German).

- STU-004: access refusals no longer repeat request data in their text (`access.forwarded_not_allowed` named the
  forwarded headers); the process's control token, launch tokens, session IDs and CSRF values are registered for log
  redaction when they are created.

- Review of PR #4 (`stu-003-review.md`), S-1: when the server no longer knows the tab's session (a restart, or a
  program on another local port that replaced the session cookie), the page stops the heartbeat and says "Your session
  has ended. Run `tabrion-studio` again to open Studio." The cookie-tossing residual risk is documented.

- Review of PR #1 (`stu-002-018-review.md`): the control client makes the server prove the control token (HMAC over a
  client nonce) before it sends the token, and opens only launch URLs of that instance; stale info files are removed
  when the lock is taken and at the start of a shutdown (F-01). SIGHUP and Windows CTRL_CLOSE stop Studio cleanly
  (F-04). Only authenticated control requests and same-origin heartbeats (`GET /api/heartbeat`) reset the idle timer
  (F-06). Unsafe state directories are refused: group- or world-writable or foreign-owned on POSIX (F-10), outside
  the profile without a private ACL or behind a junction on Windows (F-07). `[::1]` on the same port is held (F-08).
  ci-ok reports NOT OK for unreviewed changes to `.github/workflows/**` or `.github/scripts/**` (label
  `workflow-change-reviewed`, F-03) and retries the status post, failing loudly (F-11).
- STU-003 binding requirements of the review (`stu-002-018-review.md`, condition C-3): the browser is opened with the
  `file://` path of a 0600 redirect file and never with the token URL, so no process argv shows the token; the file
  is deleted when the token is used or expires, at shutdown and at the next start (F-02). The Host allowlist covers
  every route, and the control endpoints refuse requests with `Origin` or `Sec-Fetch-*` (F-05). `localhost` and
  `[::1]` are not answered (F-08). A session cookie alone opens nothing without its CSRF value (F-09). Requests with
  a valid session count as activity; the heartbeat needs the session (F-06, R-5).
- Re-review recommendations: the control client sends the proof request and the request with the token over one
  keep-alive connection and never reconnects in between (R-4). The workflow-change gate guards every file under
  `.github/` except the issue and pull request templates (R-3), accepts the label `workflow-change-reviewed` only if
  it was set after the latest push (R-2), and the `ci-ok` status description names its run; the merging party checks
  the ci-ok run link before every merge (R-1).

### Changed

- STU-020 (E-1 and the Info item of the delta review): script names may hold the zero-width non-joiner U+200C and the
  zero-width joiner U+200D between two letters or marks (Persian `می‌خواهم.sql`, Indic conjuncts; a ZWJ also inside
  an emoji sequence) and the ideographic space U+3000 inside a name, like U+0020 (Japanese `売上　集計.sql`). A
  joiner at the start or end of a name, doubled or next to a space, a dot or a digit, the bidi controls and the other
  format and blank characters stay refused; the save dialog checks the same rule. The page keeps showing the joiners
  escaped (`\u200C`) in the Open… list and in tab titles, so look-alike names stay apart. `GET /api/scripts` counts
  the folders and `.sql` files it leaves out for their name in `skipped_invalid`, and the Open… dialog says how many
  (en/de). Another instance's write lock on the state database is logged at info level as `studio.state_busy`
  without a traceback; other database errors stay `studio.state_error` warnings.

- e2e (TD-050, joint regression JR-N2, PR #68): `backend.spec.ts` removes its `studio-e2e-*` temp directory after
  the server has exited, like the other real-server suites; before, every run left one behind.
- CI (STU-050, TD-047): the e2e suites also run in Firefox and WebKit, in the jobs `e2e (firefox)` and
  `e2e (webkit)` with the browsers of the locked Playwright version; advisory in `ci-manifest.json` until both are
  green. `STUDIO_E2E_BROWSERS` picks the browsers locally (Chromium by default); the heap measurements through CDP
  skip outside Chromium with a "chromium only:" reason, the only skip those jobs accept.
- e2e (TD-047, PR #63): the suites pass in Firefox and WebKit too, with no product change. The real-server suites
  share one CSP watcher (`recordViolations`) that also hears Firefox's "Content-Security-Policy:" reports (the old
  pattern never matched them, so those checks passed vacuously in Firefox) and leaves out only WebKit's own report for
  a `<select>`, paired with its violation event; `e2e/csp.spec.ts` proves both per browser. The Tab test of the SQL
  editor checks the focus lands on the next control; the tooltip test reaches `:focus-visible` with Tab; Copy DDL is
  checked for the exact text in every browser; the saved script's offered name is checked as the page offers it;
  the keyboard-only connect flow reaches "Connect" with Shift+Tab instead of relying on the focus wrapping around.
  After the review: the watcher also counts the page's own `securitypolicyviolation` events in every browser (WebKit's
  select exempt once per element), `connection-lost-backend.spec.ts` uses it too, and the Tab test also checks Tab
  without a result.
- CI (TD-047, PR #67): the jobs `e2e (firefox)` and `e2e (webkit)` are required. They lost `continue-on-error` and
  their `advisory` entries in `.github/ci-manifest.json`, so `ci-ok` fails when either fails, like `e2e`.
- The view paths `/connections` and `/settings` answer `HEAD` as well as `GET`, like `/` and the static files
  (review U-4).
- The dialog's initial focus escapes the action ID in its selector (`CSS.escape`), so an ID with a quote or a
  backslash focuses its button instead of throwing (review U-2).
- `docs/development/building.md` explains the Chromium build that the locked Playwright expects and how to use a
  preinstalled browser through `PLAYWRIGHT_CHROMIUM_EXECUTABLE` (review U-5).

- `/api/heartbeat` and `/api/openapi.json` need a session. The control endpoint `POST /api/control/launch` takes
  `{"redirect": true|false}` and answers the launch URL and the redirect file. The Vite development proxy presents the
  backend's own Host and Origin.

### Fixed

- Tests: `tests/unit/test_server.py` built a `uvicorn.Config` with Uvicorn's default logging config, which stops the
  `uvicorn` loggers from propagating for the rest of the run; when it ran before `tests/security/test_canary.py`
  (`pytest tests/unit tests/security`), the lifespan test there saw no Uvicorn record and failed. It now passes
  `log_config=None`, like the product.
- Theme switch: buttons kept their old background for 120 ms (a transition) while their text took the new theme,
  so for that moment a primary button's label had too little contrast (TD-047, found by axe in WebKit). The theme now
  switches with transitions off, and text and background change together.
- The README's install command had no Tabrion pin and failed (`No matching distribution found for tabrion`); a test
  now checks every documented install command. The README status names what is on `master`. The settings page no
  longer says that SQL statements are logged when "Write SQL statements to the log" is on: the setting is saved but
  not used yet (TD-013); it says when the row limit and the timeout apply.
- The export dialog's format, column and option choices are 24 px high targets; they were 21 px (STU-053 F-6,
  WCAG 2.5.8).
- SQL editor: a cancel (Ctrl+., Escape, the button, or closing the tab) while the start request of a query was still
  on its way was lost: the page had no query id yet, so it sent no cancel, and the query ran on until its timeout
  while the page showed it running. The page now cancels the query as soon as the server answers with its id. This
  was the cause of `editor-backend.spec.ts` › "cancel stops an endless query within a second" failing under load. That
  test now waits for the start to be answered and takes the cancel time in the page (key to "Canceled"), not as the
  test's wall time, which included the retry schedule of `expect`.
- `tabrion-studio` no longer ends with a `BrokenPipeError` traceback and a non-zero exit status (1, or 120 with buffered
  output) when the terminal or the parent process stopped reading its output before a clean stop; the last line is
  dropped and the exit status stays 0. Other errors writing that line are still raised (TD-044, joint regression JR-1).
- STU-050: the start page said the project and connection views "arrive with the next versions"; it now leads to
  Connections, Explorer, SQL editor, Migrations and Diagnostics (en/de).
- STU-050: connection failures (a missing or linked SQLite file, a file that is no database, an unset password
  variable, an unreachable server, a timeout, settings the driver refused, a full disk) now say what to check, as
  08 section 5 asks.
- STU-050: the migration view showed an empty status bar under its header before its first message.
- `e2e/explorer-reference.spec.ts` can run its PostgreSQL part against a server with password authentication
  (`TABRION_STUDIO_E2E_PG_PASSWORD`).
- TABRION QA-21: `scripts/check_tabrion_api.py --surface` no longer reports enum members and dataclass fields as
  missing. Tabrion's `docs/api/surface.json` lists them in the parent entry (`members`, `fields`), not as names of
  their own, so `tabrion.TLSMode.AUTO` failed against tabrion v0.1.0; such a name is now checked against its parent's
  entry and passes when the parent is stable (`S`). Tooling only; the four copies stay identical.
