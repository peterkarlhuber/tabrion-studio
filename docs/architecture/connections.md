# Connections and read-only enforcement (STU-009)

Studio 1.0 never writes to a database. Every connection is read-only, for every environment. Production has no
write path at all: no backend route, no UI path. The boundary is in the database, not in a text filter
(03 section 5.1, threats T-06 and T-13).

## Modules

| Module | Role |
|---|---|
| `services/connections.py` | `ConnectionRegistry`: opens, checks, lists and closes connections; `OpenConnection` offers only reading |
| `services/readonly.py` | the SQLite authorizer and the statement check (`check_statement`) |
| `api/connections.py` | the routes; refusals as problems with fixed texts |

The services layer sits between the API and the configuration (TD-001). It never imports the API, the app, the
server, the CLI or `security`, and the configuration never imports it. SQLAlchemy and psycopg are used only in the
services (import-linter contracts `services-below-api` and `engines-only-in-services`).

## API

| Route | Body | Answer |
|---|---|---|
| `POST /api/connections` | `{"profile_id", "confirmed_environment"}` | 201 `{connection_id, profile_id, engine, environment, read_only: true, url, opened_at}`; `studio.prod_confirmation_required` (409, see below) |
| `GET /api/connections` | – | `{"connections": [...]}` (each checked against its profile; a stale one is closed and left out) |
| `DELETE /api/connections` | `{"connection_id"}` | 204, or `studio.connection_not_found` |
| `GET /api/connections/{id}/health` | – | `{state: "open" \| "busy", latency_ms}`; a lost connection is its `db.<kind>` problem (502 `db.connection` when the database does not answer); `studio.connection_not_found`, `studio.connection_stale` (TD-034) |
| `POST /api/connection-tests` | `{"profile_id", "confirmed_environment"}` | `{ok, engine, environment, server_version, latency_ms, read_only, facts}`; the connection is closed again; `studio.prod_confirmation_required` (409) |

The ids go in the body, like the profile routes. All routes sit behind the access middleware (session, CSRF,
Origin, JSON). `url` comes from `tabrion.safe_url` and never contains the password. The connection is checked with
`Database.health()` before the answer, so a wrong password is the answer to "open" (a `db.<kind>` problem).
An open connection of the same profile is returned again, and at most 8 are open (`studio.connection_limit`).
Opening and closing run in a worker thread. Shutdown disposes every engine.

### D-02 on the server: `confirmed_environment` (TD-038)

The page asks D-02 before it opens a PROD connection, but it decides from the profile list it loaded. That list can
be stale: another tab, or a hand edit of `profiles.toml`, may have made the profile PROD since. So the server checks
too.

- **Every caller of `POST /api/connections` and `POST /api/connection-tests` sends `confirmed_environment`:** the
  profile's environment as the page shows it (`"dev"`, `"test"` or `"prod"`), and `"prod"` only after D-02 was
  confirmed for that profile. In the page this is the third argument of `openConnection` (`api/connections.ts` and
  `api/migrations.ts`) and `testConnection`: `openConnection(fetcher, profile.id, profile.environment)`, called after
  the view's D-02 step. The argument is required (C-2 of the #48 review): a call that leaves it out does not compile,
  so `npm run build` fails until every caller names the environment it confirmed.
- The server reads the profile when it opens the connection, and checks it on that same read
  (`require_prod_confirmation` in `services/connections.py`). A profile that is `prod` now opens, is tested, or has its
  already open connection returned only with `"prod"`.
- **Fail closed:** a missing or `null` value means "not confirmed". A client that does not send the field (a script,
  or page code from before this change) still opens DEV and TEST profiles, but gets 409
  `studio.prod_confirmation_required` for every PROD profile. A value other than the three is `access.bad_request`.
- **On 409 `studio.prod_confirmation_required`:** the problem carries no profile data (problems never carry request or
  profile values, T-05). The page reads the profile again (`GET /api/profiles`), asks D-02 for it as it is now, and
  sends the request again with `"prod"`. The connection manager does this at once, and at most once: a refusal for a
  profile it already shows as PROD is only shown. The migration status, diagnostics and explorer views and the SQL
  editor show the problem text, read their profile list again, and ask D-02 on the next click (in the editor: the
  next run).
- D-02 is still a page dialog; the server only refuses a PROD connection that no page confirmed. It cannot tell which
  person confirmed, so a script with a session can still send `"prod"`.

## Lost connections and "Reconnect" (TD-034)

08 section 5 "error recovery": when the database stops answering, every page that holds a connection (explorer with
its data and definition tabs, SQL editor, diagnostics, migration status) shows the banner "Connection lost" with
"Reconnect" above the view. Studio only reads, so nothing was lost with the connection; the banner says so.

- **Server.** `OpenConnection.check()` says `open`, `busy`, `lost` or `failed`.
  - `busy`: every session of the pool (2) is lent out, read from the pool's counts *before* the check. The check
    then does not run at all: it would only wait for a session (the pool timeout, 10 s) and learn nothing. Read after
    a failed check, the counts would include the check's own session and make a dead database look busy.
  - Otherwise `Database.health()` runs. Only a failure of the connection kind (the database does not answer) is
    `lost`. Any other failure (the check's 5 s statement timeout on a loaded server, a permission or authentication
    problem) is `failed`: reported as its `db.<kind>` problem, but the connection is kept.
  - `GET /api/connections/{id}/health` answers it; a lost or failed connection is its `db.<kind>` problem and stays
    registered, so the page can name it. `POST /api/connections` checks an already open connection of the profile
    before it returns it: only a `lost` one is logged (`studio.connection_lost`), closed and replaced by a new one, so
    a slow check never closes a connection under the queries and exports of other views. D-02
    (`confirmed_environment`) is checked first, as for every open. Two opens of the same profile at once (a
    "Reconnect" and another view's open) both connect; the second to register closes its own and returns the first
    (after the D-02 check, since the profile may have become PROD meanwhile), so no connection is left open and
    unlisted.
  - A database that only ended Studio's sessions and accepts new ones is not lost: tabrion's pool (`pool_pre_ping`)
    reconnects on the next request.
  - Limit: while both sessions are lent out and the database is down, the check says `busy`; the loss is seen when
    those statements fail (their requests answer `db.connection`).
- **Seeing the loss.** `connectionWatch.ts` wraps the page's fetcher, so it sees every answer of every view without
  a change to the views: a request on an open connection (`/api/connections/{id}/…`, or `POST /api/queries` and
  `POST /api/exports` with `connection_id` in the body) answered with `db.connection`, `studio.connection_not_found`
  or `studio.connection_stale` marks it lost. It reads a copy of the answer (`Response.clone()`) and never changes
  one; the user's own "Disconnect" is not watched. A query that fails later, in its event stream, is reported by the
  SQL editor itself (`markLost`). The header's connection is also checked every 30 seconds while the tab is visible
  (`GET …/health`); the check stops while the connection is lost.
- **State.** `connectionLoss.ts` (`ConnectionLoss`, one per page, in the connection session) keeps the connections
  the page opened or listed, the lost ones and the ones "Reconnect" replaced. Unknown and replaced connections are
  ignored, so a late answer on an old connection does not bring the banner back. A loss forgets the profile's D-02
  confirmation and shows the header's connection as disconnected.
- **"Reconnect"** (`shell/ConnectionLost.tsx`) reads the profile list again (the profile as it is now decides) and
  connects through the page's shared gate (`useConnect`): D-02 first for a PROD profile; Cancel connects nothing and
  keeps the banner. A failure is shown in the banner. When the new connection arrives, the loss ends and the views
  follow it (`useReconnected`): the explorer reads its catalog again on the new connection (also when it had dropped
  a connection the server closed), the SQL editor's tabs and runs move to it, diagnostics and the migration status
  use it for their next request. The focus moves to the main area. The SQL editor no longer reopens a PROD
  connection silently when the server says it is gone; DEV and TEST still reopen as before.
- **What "D-02 again" covers.** The confirmation is forgotten in the page that saw the loss. D-02 stays "once per
  profile and page session" (TD-038: the server cannot tell who confirmed), so another tab that has not seen the loss
  yet still holds its confirmation: its next open of that PROD profile sends `"prod"`, and the server replaces the
  lost connection without a new dialog in that tab. Before TD-034 that open got the dead connection back and failed.

## The connection test (STU-008)

`ConnectionRegistry.test` opens the profile's connection like "open" (same checks, same read-only guards, and it
counts against the limit), runs `Database.health()`, reads the facts of 03 section 2 A with `connection_facts`, and
closes the connection again.

- **PostgreSQL:** one fixed catalog query of Studio's own through `OpenConnection.fetch` (read-only, the statement
  check, a prepared statement): `current_user`, `rolsuper` and `rolbypassrls` from `pg_roles`, `server_encoding`,
  `TimeZone`, `pg_stat_ssl.ssl` of Studio's own session and `server_version`. tabrion's `postgres.role_info` needs the
  driver connection; the catalog query gives the same `superuser` and `bypass_rls` without one. tabrion 0.1's
  `Health.server_version` is PostgreSQL's `server_version_num` (`160015`), so the answer's `server_version` is the
  first word of `current_setting('server_version')` (`16.15`) instead; the page also turns a 5- or 6-digit number into
  `major.minor` if it gets one.
- **SQLite:** `PRAGMA foreign_keys`, `PRAGMA journal_mode` and `PRAGMA encoding` (reading pragmas, allowed by the
  authorizer).
- Text facts are sent only as printable ASCII of at most 64 characters (`_fact_text`), else `null`. A database error
  while reading them leaves `facts` empty and is logged with its type only; the test's answer is the health check.

The page runs the test on a saved profile only: a password typed in the assistant goes to the secret route and
nowhere else (OA-010), so "Test the connection after saving" replaces 08's test before saving.

The test opens a connection, so for a PROD profile the page asks D-02 before it, like before "Connect": the button,
the test after saving and the retry after the password dialog (`stu-008-connect-review.md` C-2). The confirmation
holds for the page session and belongs to the profile's target (id, environment, engine, host, port, database, user,
path); saving or deleting the profile and trusting the project file again forget it. The routes check it too: see
"D-02 on the server" above.

## How a connection is opened

1. The profile entry comes from `Configuration.entry(id)`. Project profiles exist only while the project file is
   trusted.
2. **SQLite:** the file comes from `Configuration.database_file(entry)`, with the entry of step 1: the same read of
   the profile that passed the D-02 check, never a second read of `profiles.toml` (C-1 of the #48 review), so a file
   rewritten in between cannot put another database behind a check that passed.
   - For a project profile, that call goes to `ProjectConfig.database_file`, which checks the trust state and the
     confirmed containment again on the file system (review T-1). Studio opens exactly the real path it returns. The
     trusted profile it reads must equal the entry, or it refuses with `studio.project_untrusted` (fail closed).
   - A relative path of the user's own profile is relative to the configuration folder.
   - A missing file is `studio.database_missing`; Studio never creates one.
3. **PostgreSQL:** the password comes from `Configuration.resolve_entry_secret(entry)` when the connection opens:
   the secret of the same entry the URL is built from, so both come from one read of the profile file (L-1 of
   `stu-009-review.md`).
   - A profile without a `password` reference and without a password entered for the session connects without one
     (trust, peer or certificate authentication). libpq gets no password of its own: `passfile` points to a path
     that cannot exist, and the password is the empty string, so `PGPASSWORD`, `PGPASSFILE`, `~/.pgpass` and a
     service file's password are never sent to the host a (project) profile names (M-1). Use `env:PGPASSWORD` to
     send that variable on purpose.
4. **The URL** is built by `connection_url` from the fields in `TARGET_FIELDS` and the password, and from nothing
   else. A stored password is bound to the fingerprint of exactly these fields (C-1 of `stu-007-review.md`; L-1 of
   `stu-008-review.md`, binding for STU-009).
   - Session settings (statement and lock timeout, `application_name`) are not connection targets.
   - `tests/security/test_read_only.py::test_the_driver_gets_only_the_fingerprinted_fields` changes every other field
     and checks that the URL stays the same.
5. **The tabrion profile** is always `read_only=True` (`studio_profile`), with a pool of 2 and the timeouts of
   03 section 5.3: statement timeout 30 s, or 15 s for `prod` unless the profile sets one, and lock timeout 5 s.
6. **TLS:** the profile's `sslmode` maps to tabrion's TLS mode.
   - `verify-full` maps to `VERIFY_FULL`.
   - `require` and `verify-ca` map to `REQUIRE`.
   - `prefer`, `allow` and `disable` map to `INSECURE`: the user chose them explicitly, and D-01 marks them as
     `weak_tls`.
   - The parser always sets `sslmode` for PostgreSQL (`prefer` for loopback, `verify-full` otherwise). Only a
     profile built in code has none; it maps to `AUTO`, which lets tabrion require TLS for a remote host.
   - tabrion refuses a weak mode for a remote host in `REQUIRE` and `VERIFY_FULL` (`studio.connection_tls`).

Every use of a handle checks it again. If the profile is gone, no longer trusted, points to another target or a
different environment, or if `database_file` now resolves elsewhere, the connection is closed
(`studio.connection_stale`).

## Read-only, engine by engine

| Engine | In the database | Studio's own layer |
|---|---|---|
| SQLite | `file:...?mode=ro`, `PRAGMA query_only=ON`, no attached database (tabrion) | authorizer allowlist on every DBAPI connection; `check_statement` |
| PostgreSQL | `default_transaction_read_only=on` for the session (tabrion); every statement in `Database.read()` (`BEGIN READ ONLY`, re-checked by tabrion before `COMMIT`, I-56) | one statement per transaction; `check_statement` refuses transaction control |

**SQLite authorizer** (`sqlite_authorizer`, STU-T-024):
- It allows reading, transactions and savepoints, recursive queries, pragmas that only read (or set `query_only`
  on), and SQLite's built-in functions that only compute a value: an allowlist (`readonly._SQLITE_FUNCTIONS`: the
  core scalar, aggregate, window, date, math and JSON functions, and the FTS3/4/5 and R-tree functions that only
  read). Since the STU-016 review (F-2), every other function is denied, since reading needs none of them:
  `load_extension`, `fts3_tokenizer`, `fts5`, `optimize` (FTS4 maintenance), `rtreenode` and `rtreedepth`
  (debugging), `sqlite_log`, and any function a build or a later SQLite adds. A view of the file that calls a denied function fails to read.
- Every SQLite connection sets `PRAGMA trusted_schema = OFF` before the authorizer is installed (tabrion's default
  profile sets it too), so views and triggers of a file can call only functions marked innocuous.
- It denies everything else: every write, every schema change (temporary objects included), `ATTACH`, `DETACH`
  (and so `VACUUM INTO`, which starts with an `ATTACH`), `ANALYZE`, `REINDEX`, and pragmas that set a value or act
  (`optimize`, `wal_checkpoint`, ...).
- An action that SQLite adds later is denied.

**Statement check** (`check_statement`, a user-experience layer, 03 section 5.1):
- Exactly one statement (`tabrion.sql.split_statements`).
- These first keywords are refused: `BEGIN`, `START`, `COMMIT`, `END`, `ROLLBACK`, `ABORT`, `SAVEPOINT`, `RELEASE`,
  `PREPARE`, `RESET`, `DISCARD`.
- These `SET` forms are refused: `SET TRANSACTION`, `SET SESSION CHARACTERISTICS`, `SET [SESSION|LOCAL]
  default_transaction_read_only | transaction_read_only | ROLE | SESSION AUTHORIZATION`.
- So are `COPY`, `LOAD`, `NOTIFY`, `LISTEN`, `UNLISTEN`, `LOCK`, `VACUUM`, `CHECKPOINT`, `CLUSTER` and `DO`.
- So is every statement with the word `INTO` outside a literal, a comment or a quoted identifier, in any of the
  lexings the check reads (F-1 of the STU-016 review). `SELECT ... INTO` creates a table, and a read-only editor
  never needs `INTO` (`INSERT INTO`, `MERGE INTO`, `REPLACE INTO` and `VACUUM INTO` are refused anyway). A number
  directly followed by a letter, `_` or `$` is refused, and so are PostgreSQL 16's hexadecimal, octal, binary and
  underscore number forms (`0x1f`, `1_000`) and SQLite's hexadecimal ones: write numbers in decimal. A column named `into` must be written as a quoted identifier (`"into"`; in
  SQLite also `[into]` is read as the keyword by the PostgreSQL lexings, so it is refused). `into` inside a
  dollar-quoted string (`$$...$$`) is refused too, since SQLite has no dollar quoting; use `'...'` there.
- Since the STU-011 fix round, every other first word too: only `SELECT`, `WITH`, `VALUES`, `TABLE`, `EXPLAIN` of a
  query, `SHOW` and `PRAGMA` pass (an allowlist; see [Query engine](queries.md), "Statements and session state").
  PostgreSQL connections also get Studio's session settings as startup options and run `RESET ALL` on check-in
  (after `DISCARD TEMP`, which drops temporary tables a statement may have left: F-1 of the STU-016 review).

**One statement, enforced by the server (H-1 of `stu-009-review.md`):** every PostgreSQL connection uses
`prepare_threshold=0` (`driver_options`). So every statement is a prepared statement on the extended query protocol,
and the server refuses a text with several commands (`42601`). The text split cannot be the boundary. With
`standard_conforming_strings = off`, the review's text `SELECT '\''; ROLLBACK; BEGIN READ WRITE; INSERT ...` is one
statement to `tabrion.sql.split_statements` and six to the server. Over the simple query protocol, the review's probe
wrote three rows through Studio's read-only connection. Now the server refuses that text. `check_statement` also
refuses `SET standard_conforming_strings` and `SET backslash_quote`, and a call of `set_config` (which also reaches
`role`), `pg_notify` or a session-level advisory lock function (`pg_advisory_lock`, `pg_try_advisory_lock` and their
`_shared` forms, which would stay held on the pooled connection) anywhere in the statement. That check reads tokens:
comments and literals are left out and quoted identifiers unquoted, and `'...'` is lexed both with and without
backslash escapes, so `"set_config"(`, `set_config/**/(` and `pg_catalog.set_config (` are caught (R-L1 and I-R1 of
the re-review). A Unicode-escaped identifier (`U&"..."`) is refused, since Studio does not decode it. `DO` blocks and the functions that run a query given as text (`query_to_xml`,
`query_to_xmlschema`, `query_to_xml_and_xmlschema`, `cursor_to_xml`, `cursor_to_xmlschema`, `ts_stat`, `ts_rewrite`,
`crosstab`, `connectby`, `xpath_table`, `dblink` and its forms) are refused as well, since the check cannot see inside a literal (R2-L1 of
re-review 2). Since the STU-016 review (F-3), so are the functions whose effect a read-only transaction does not stop
(`readonly._REFUSED_FUNCTIONS` and `_REFUSED_PREFIXES`, each justified there): `pg_terminate_backend` and
`pg_cancel_backend` (a role may end its own other sessions, the application's among them when it shares the role);
`pg_logical_emit_message`, `pg_switch_wal`, `pg_create_restore_point`, `pg_log_standby_snapshot`, the backup functions
and the replication slot functions that create, drop, copy, advance or consume a slot (the WAL and its consumers);
`txid_current` and `pg_current_xact_id` (they assign a transaction ID; the `_if_assigned` forms stay allowed);
`pg_reload_conf`, `pg_rotate_logfile`, `pg_log_backend_memory_contexts`, `pg_promote`, the WAL replay pause and
resume, `pg_import_system_collations`, every `pg_stat_reset*` function and `pg_stat_statements_reset` (the server);
`lo_import`, `lo_export` and adminpack's `pg_file_*` writers (server files); `brin_summarize_new_values`,
`brin_summarize_range`, `brin_desummarize_range` and `gin_clean_pending_list` (index changes outside the
transaction's data); every `dblink*` function; and every
`pg_replication_origin_*` function. The large-object writers (`lo_create`, `lo_put`, ...), `nextval` and `setval` are
left to the database, which refuses them in a read-only transaction; the transaction-level advisory locks end with
Studio's transaction and stay allowed. This is still a best-effort check, not the boundary: a database function that calls `set_config`, or
a function of an extension Studio does not know that runs a query from text, is invisible in the text. SQLite's
`execute` runs one statement only.

**Binding for STU-011 (kept by the [query engine](queries.md)):** user SQL goes through `check_statement`
(`OpenConnection.prepare`, and again in `OpenConnection.stream`), with one `read()` per statement, and always through
`ConnectionRegistry.get`: a handle does not check itself again. User SQL never goes through SQLAlchemy `text()`, so
`:name` inside a literal stays text (review I-2): `OpenConnection.fetch` uses the same path.
For Studio's own catalog statements `fetch` (`catalog=True`, the default) also sets a fixed `search_path`
(`pg_catalog, pg_temp`) for its own read transaction, a transaction-local setting and not a write; user SQL, the viewer
and the exports keep the session's `search_path` (see [explorer](explorer.md)).

**Connection setup (TD-039):** when a PostgreSQL connection is opened, SQLAlchemy's dialect sends setup queries of its
own (`current_schema()` among them) before Studio or tabrion send anything, and a database or role setting may put a
user schema ahead of `pg_catalog`. Studio's first `connect` listener (`tabrion_compat.pin_setup_path`) sets
`search_path` to `tabrion_compat.CATALOG_SEARCH_PATH` (`pg_catalog, pg_temp`, the path of Studio's own catalog reads)
for the setup (hardening against object resolution through user schemas), and its last one (`restore_setup_path`) runs
`RESET search_path`, so the session gets back the path of the database, role or startup settings before it is used; the
dialect's default schema is then read again with a qualified `pg_catalog.current_schema()`. Studio sets no `search_path`
of its own (`startup_options` refuses one), so tabrion's session setup, which runs between the two, sets none either.
Neither statement is a write. The driver's native hstore adaptation is off (`use_native_hstore=False`), so no type
lookup runs at connect time and hstore values are the server's text. User SQL, the viewer and the exports run under the
session's path.

**Known limits** (documented in 03 section 5.1 and ADR R-S3):
- `READ ONLY` does not stop functions with side effects (`dblink`, extensions, `COPY ... TO` through a function).
  Only the role's
  privileges are a hard boundary, so production profiles should use a role without write privileges, without
  `CREATE` on any schema and without `TEMP` on the database. The diagnostics (STU-014) check this (`read_only_role`,
  `STU-DX-003`).
- libpq still reads other variables of the user's own environment (`PGHOSTADDR`, `PGSERVICE`, `PGSSLROOTCERT`, ...)
  for what the URL leaves out; its password fallbacks are off (M-1). That is the user's environment, not the project
  file's. A project file can change only the fingerprinted fields.

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_connect_path.py` (PostgreSQL superuser) | TD-039: with `ALTER DATABASE ... SET search_path` putting a schema of another role, holding objects that raise when called, ahead of `pg_catalog`, opening a connection calls none of them; the dialect's default schema is the session's; user SQL sees the database's path on two sessions at once and on later queries, and reaches the planted objects (the control); an `hstore` value comes back as the server's text (L-2) |
| `tests/security/test_read_only.py` (SQLite, always) | STU-T-010: 15 kinds of write fail in SQLite itself and the file's SHA-256 is unchanged; STU-T-024: ATTACH, VACUUM INTO and `load_extension` refused, no file created; F-2: `fts3_tokenizer` (both forms), `fts5`, `optimize`, `rtreenode`, `rtreedepth` and `sqlite_log` are denied on every pooled session while FTS `MATCH` with its auxiliary functions, JSON, dates, math, window functions, `->>`, a view's functions and `pragma_table_info` still read, and `trusted_schema` is off on every session (set before the authorizer); writes, ATTACH and VACUUM INTO fail on a raw DBAPI connection as well (the guards sit on the connection, not on the handle); the authorizer is an allowlist; the statement check; STU-T-011: every environment gets a read-only profile with Studio's timeouts; STU-T-012: a handle offers no write method; the driver gets only fingerprinted fields; a changed, removed or swapped profile, or a changed environment, closes its connection; several statements fail in sqlite3 past the text check; the driver options turn libpq's password fallbacks off and use prepared statements; a connection test shares the limit; a missing file is never created; reuse and limit |
| `tests/security/test_read_only.py` (PostgreSQL, `TABRION_STUDIO_TEST_POSTGRES`) | STU-T-010: INSERT, UPDATE, DELETE, CREATE, CREATE TEMP, DROP and TRUNCATE fail in the server as the owner role; STU-T-013: the session and every transaction are read-only, `SET TRANSACTION READ WRITE` in the same transaction is rolled back; H-1: the review's escape text verbatim, after `set_config('standard_conforming_strings','off')`, and several statements past the text check are refused by the server (`42601`), with no row written; M-1: with `PGPASSWORD` and a wildcard `PGPASSFILE` set, a profile without a password does not connect; L-1: the password comes from the entry the URL was built from, and it is registered for redaction; the multi-statement gap is refused by the text check as well; F-1: `EXPLAIN ANALYZE SELECT ... INTO` (plain, `UNLOGGED`, `TEMPORARY`, schema-qualified, under `WITH`) and `SELECT ... INTO` are refused and `pg_class` has no such table afterwards, a writable CTE or `FOR UPDATE` under `EXPLAIN ANALYZE` fails in the server (`read_only`), and a temporary table left on a session (past the check) is gone at check-in, so it cannot shadow `pg_namespace`; F-3: `pg_terminate_backend` and `pg_cancel_backend` on another session of the same role, `pg_logical_emit_message` and the transaction-ID functions are refused, and that session stays alive; a wrong password is `db.connection` without server text; `url` has no password |
| `tests/security/test_read_only.py` (STU-008) | the connection test's facts on SQLite (WAL, foreign keys, encoding; the file unchanged, nothing left open) and PostgreSQL (role, superuser, bypass RLS and encoding as the catalog says, time zone, TLS); facts that cannot be read leave the answer; only short printable text |
| `tests/security/test_connections_api.py` | open, reuse, list, test and close through the API; a changed profile and a changed project file close the connection; a project file opens only after trust; refusals as fixed problems; session and CSRF required; D-02 on the server (TD-038); a SQLite file from the read that passed the check, with `profiles.toml` rewritten in between, and a project profile that is no longer the checked read (C-1 of the #48 review); shutdown disposes the engines |
| `tests/unit/test_problems.py` | `DB_KINDS` is `tabrion.errors.Kind`; a `db.<kind>` problem takes only the kind and the SQLSTATE from the error |

The PostgreSQL tests need a database whose owner role they may use. Locally:

```bash
TABRION_STUDIO_TEST_POSTGRES=postgresql://studio_owner:pw@127.0.0.1:5432/studio_test pytest -m postgres
```

The CI job `backend integration (PostgreSQL 17)` (03 section 8) changes `.github/` and needs a workflow-change review
(OA-003). Until then, the PostgreSQL tests are skipped in CI.
