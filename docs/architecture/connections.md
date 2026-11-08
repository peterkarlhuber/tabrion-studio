# Connections and read-only enforcement (STU-009)

**Integration branch only.** This page describes the STU-009 draft. It is built on a pinned tabrion commit and must
not merge to master before tabrion v0.1.0 (RC-8, `docs/TODO.md` TS-001).

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
| `POST /api/connections` | `{"profile_id"}` | 201 `{connection_id, profile_id, engine, environment, read_only: true, url, opened_at}` |
| `GET /api/connections` | – | `{"connections": [...]}` (each checked against its profile; a stale one is closed and left out) |
| `DELETE /api/connections` | `{"connection_id"}` | 204, or `studio.connection_not_found` |
| `POST /api/connection-tests` | `{"profile_id"}` | `{ok, engine, server_version, latency_ms, read_only}`; the connection is closed again |

The ids go in the body, like the profile routes. All routes sit behind the access middleware (session, CSRF,
Origin, JSON). `url` comes from `tabrion.safe_url` and never contains the password. The connection is checked with
`Database.health()` before the answer, so a wrong password is the answer to "open" (a `db.<kind>` problem).
An open connection of the same profile is returned again, and at most 8 are open (`studio.connection_limit`).
Opening and closing run in a worker thread. Shutdown disposes every engine.

## How a connection is opened

1. The profile entry comes from `Configuration.entry(id)`. Project profiles exist only while the project file is
   trusted.
2. **SQLite:** the file comes from `Configuration.database_file(id)`.
   - For a project profile, that call goes to `ProjectConfig.database_file`, which checks the trust state and the
     confirmed containment again on the file system (review T-1). Studio opens exactly the real path it returns.
   - A relative path of the user's own profile is relative to the configuration folder.
   - A missing file is `studio.database_missing`; Studio never creates one.
3. **PostgreSQL:** the password comes from `Configuration.resolve_secret(id)` when the connection opens.
   - A profile without a `password` reference and without a password entered for the session connects without one
     (trust or peer authentication, or `.pgpass`).
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
   - A profile without `sslmode` (a loopback one) maps to `AUTO`.
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
- It allows reading, transactions and savepoints, recursive queries, functions other than `load_extension`, and
  pragmas that only read (or set `query_only` on).
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
- So are `COPY`, `LOAD`, `NOTIFY`, `LISTEN`, `UNLISTEN`, `LOCK`, `VACUUM`, `CHECKPOINT` and `CLUSTER`.

Why the single-statement rule matters: PostgreSQL runs a text with several statements in one round trip. Without the
check, `COMMIT; BEGIN READ WRITE; INSERT ...` inside Studio's read-only transaction would end it and write. The STU-009
probe reproduced this against PostgreSQL 16, and `test_the_known_gap_of_multiple_statements_is_closed_by_the_text_check`
now refuses it. **Binding for STU-011:** user SQL goes through `check_statement`, with one `read()` per statement.

**Known limits** (documented in 03 section 5.1 and ADR R-S3):
- `READ ONLY` does not stop functions with side effects (`dblink`, extensions, `COPY ... TO` through a function).
  Only the role's privileges are a hard boundary, so production profiles should use a role without write
  privileges. The diagnostics (STU-014) check this (`read_only_role`).
- libpq also reads the user's own environment (`PGHOSTADDR`, `PGSERVICE`, `PGSSLROOTCERT`, ...) for what the URL
  leaves out. That is the user's environment, not the project file's. A project file can change only the fingerprinted
  fields.

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_read_only.py` (SQLite, always) | STU-T-010: 15 kinds of write fail in SQLite itself and the file's SHA-256 is unchanged; STU-T-024: ATTACH, VACUUM INTO and `load_extension` refused, no file created; writes, ATTACH and VACUUM INTO fail on a raw DBAPI connection as well (the guards sit on the connection, not on the handle); the authorizer is an allowlist; the statement check; STU-T-011: every environment gets a read-only profile with Studio's timeouts; STU-T-012: a handle offers no write method; the driver gets only fingerprinted fields; a changed, removed or swapped profile closes its connection; a missing file is never created; reuse and limit |
| `tests/security/test_read_only.py` (PostgreSQL, `TABRION_STUDIO_TEST_POSTGRES`) | STU-T-010: INSERT, UPDATE, DELETE, CREATE, CREATE TEMP, DROP and TRUNCATE fail in the server as the owner role; STU-T-013: the session and every transaction are read-only, `SET TRANSACTION READ WRITE` in the same transaction is rolled back; the multi-statement gap is refused; a wrong password is `db.connection` without server text; `url` has no password |
| `tests/security/test_connections_api.py` | open, reuse, list, test and close through the API; a changed profile and a changed project file close the connection; a project file opens only after trust; refusals as fixed problems; session and CSRF required; shutdown disposes the engines |
| `tests/unit/test_problems.py` | `DB_KINDS` is `tabrion.errors.Kind`; a `db.<kind>` problem takes only the kind and the SQLSTATE from the error |

The PostgreSQL tests need a database whose owner role they may use. Locally:

```bash
TABRION_STUDIO_TEST_POSTGRES=postgresql://studio_owner:pw@127.0.0.1:5432/studio_test pytest -m postgres
```

The CI job `backend integration (PostgreSQL 17)` (03 section 8) changes `.github/` and needs a workflow-change review
(OA-003). Until then, the PostgreSQL tests are skipped in CI.
