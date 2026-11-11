# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- STU-015 backend (integration branch only, stacked on STU-014): `GET /api/connections/{id}/migrations` with SQLite's
  `user_version` and the Alembic status. The Alembic `current` comes from the version table; `heads` and `pending`
  need the project's revision files to be imported, so they are read only after the user trusted the project's code
  (`GET`/`POST`/`DELETE /api/project/code-trust`, off by default, bound to the digest of `alembic.ini` and the
  revision files), in a separate process that gets only the connection's target and is killed after 30 seconds.
  Otherwise they show as `unknown (project not trusted)`. Threat-model entry T-16. New dependency extra
  `tabrion[migrations]` (Alembic).
- STU-014 backend (integration branch only, stacked on STU-013; `tabrion.check` on the driver connection until
  tabrion 0.2): `POST /api/connections/{id}/diagnostics` with tabrion's findings (without those about Studio's own
  session) and Studio's `STU-DX-001..005`: a prod profile without a verified certificate, weak TLS to a remote host
  (T-10), a read-only role that can write, no statement timeout for the role or database, SQLite rows written with
  foreign keys off. Review fix round (`stu-014-review.md`): the diagnosis stops at the connection's statement
  timeout and can be canceled (`POST .../diagnostics/cancel`; `STU-DX-008` names what did not run, also SQLite's
  `quick_check`); `STU-DX-003` covers column grants, `TRUNCATE`, views, foreign tables, sequences and every role the
  session can `SET ROLE` to; `STU-DX-006` finds an owner role in any schema (it replaces `TAB-PG-003`, which sees
  the current schema only); `STU-DX-007` default privileges; role settings follow PostgreSQL's precedence, and
  `STU-DX-010` covers `lock_timeout`, `idle_in_transaction_session_timeout` and `application_name`; a broken foreign
  key definition is `STU-DX-009` instead of a failed route; no TLS finding twice, none for a Unix socket.
- STU-013 backend (integration branch only, stacked on STU-010; on tabrion 0.1 like STU-011):
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
- STU-010 backend (integration branch only, stacked on STU-011; reflection through `tabrion_compat.inspector`
  until tabrion 0.2): `GET /api/connections/{id}/catalog` (schemas and objects by kind, cached until `refresh`),
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
- STU-011 (integration branch only, stacked on STU-009; on tabrion 0.1 behind `services/tabrion_compat.py` until
  tabrion 0.2):
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
- STU-009 (integration branch only, tabrion master 546d254 pinned under RC-8; not merged before tabrion v0.1.0):
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

### Security

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

- The view paths `/connections` and `/settings` answer `HEAD` as well as `GET`, like `/` and the static files
  (review U-4).
- The dialog's initial focus escapes the action ID in its selector (`CSS.escape`), so an ID with a quote or a
  backslash focuses its button instead of throwing (review U-2).
- `docs/development/building.md` explains the Chromium build that the locked Playwright expects and how to use a
  preinstalled browser through `PLAYWRIGHT_CHROMIUM_EXECUTABLE` (review U-5).

- `/api/heartbeat` and `/api/openapi.json` need a session. The control endpoint `POST /api/control/launch` takes
  `{"redirect": true|false}` and answers the launch URL and the redirect file. The Vite development proxy presents the
  backend's own Host and Origin.
