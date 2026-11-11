# Diagnostics

The diagnostics give the findings for one open connection (03 sections 2 G and 6, work package STU-014). The
diagnosis is made of tabrion's checks plus Studio's own checks, and it only reads.

## Route

| Route | Answer |
|---|---|
| `POST /api/connections/{id}/diagnostics` | `{connection_id, engine, environment, ran_at, findings: [{code, severity, title, detail, remedy, source}], counts: {error, warning, info}}` |
| `POST /api/connections/{id}/diagnostics/cancel` | 202 `{"canceled": true \| false}`: stops the running diagnoses of that connection within a second |

The route is a `POST` because it runs checks against the database (`PRAGMA quick_check` on SQLite can take a while),
not because it changes anything. Like every `POST`, it needs the session, the CSRF header and a JSON body (`{}`).

Findings are sorted by severity (`error`, `warning`, `info`) and then by code. `source` is `tabrion` or `studio`.
No finding names a file path, a password or a value.

**Time limit and cancel (M-1 of `stu-014-review.md`).** A diagnosis runs at most the connection's statement timeout
(the profile's, else 30 s, 15 s on `prod`; 03 section 5.3). Then, or after the cancel route, every running statement
is stopped like a query: `sqlite3.Connection.interrupt()` on SQLite, psycopg's `cancel_safe` on PostgreSQL
(`tabrion_compat.Cancel`), and the connection that got it leaves the pool. An interrupted `quick_check` is tabrion's
"not checked" `TAB-SQ-006` (info), never "damaged". The checks that did not run are named in `STU-DX-008`.

## Findings

**tabrion** (`TAB-SQ-*`, `TAB-PG-*`): `tabrion.check` runs on the driver connection of one `Database.read()`
(`tabrion_compat.check`; with tabrion 0.2 this becomes `tabrion.check(db)`, TD-013). On SQLite that connection is
read-only (`mode=ro`, `query_only`, the authorizer). On PostgreSQL, SQLAlchemy has not sent `BEGIN` yet when the
collectors start, so tabrion opens and rolls back its own `BEGIN READ ONLY` transactions on Studio's read-only
session; the session's startup `statement_timeout` bounds them, not the per-`read()` one (I-1 of the review).

Some of tabrion's findings describe the session they run in. That session is Studio's, not the application's, so
Studio leaves those findings out:

| Code | Why it is left out |
|---|---|
| `TAB-PG-004`, `-005`, `-006` | Studio sets its own statement, lock and idle timeouts. |
| `TAB-PG-007` | Studio sets its own `application_name`. |
| `TAB-PG-021` | "Standby with a write session" reads `default_transaction_read_only`, which is always on in Studio (I-2). |
| `TAB-SQ-001`, `-005`, `-009` | Studio's connection sets `foreign_keys`, `busy_timeout` and `trusted_schema` itself. |
| `TAB-PG-003` | Replaced: it counts tables in `current_schema()` only. Studio's `STU-DX-006` looks at every schema (M-3; TODO "STU-014 diagnostics"). |

In their place, Studio checks what the application actually gets (`STU-DX-004`, `-005`, `-010`).

**Studio** (`STU-DX-*`):

| Code | Severity | Finding |
|---|---|---|
| `STU-DX-001` | error | A `prod` profile whose `sslmode` is neither `verify-ca` nor `verify-full` (03 section 5.4). Not for a Unix socket folder, which has no TLS (L-3). |
| `STU-DX-002` | warning | `sslmode` `disable`, `allow` or `prefer` to a host that is not loopback (T-10, STU-T-021). Left out when tabrion already reports the live connection's TLS (`TAB-PG-010`, `-011`), so one cause gives one finding (L-2). On `prod` with `verify-ca`, `STU-DX-001` is satisfied and `TAB-PG-010` still says that the host name is not verified. |
| `STU-DX-003` | warning (error on `prod`) | The profile sets `read_only_role = true`, but the role, or any role it is a member of (INHERIT or not: one `SET ROLE` away), may write: `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` on a table, view or foreign table, a column-level `INSERT` or `UPDATE`, ownership, superuser, or `UPDATE` on a sequence (`setval`). At most 1000 objects are counted. `USAGE` on the schema is not checked (I-3): a grant in a schema the role cannot enter still counts. |
| `STU-DX-004` | warning | No `statement_timeout` for the login role in this database: `pg_db_role_setting` with PostgreSQL's precedence (role in database, then role, then database; L-4), and a 0 counts as none. `postgresql.conf` cannot be read without `pg_read_all_settings`, so the detail says that a server-wide setting may still apply. |
| `STU-DX-005` | warning | SQLite rows that break a foreign key (`PRAGMA foreign_key_check`, at most 1000 counted): the data was written with `foreign_keys` off. |
| `STU-DX-006` | warning (error on `prod`) | The role owns, or can become the owner of, tables, views or foreign tables in any schema (`pg_class.relowner`, `pg_has_role(..., 'MEMBER')`). 03 section 5.4 requires a non-owner role on `prod` (M-3). |
| `STU-DX-007` | info | `read_only_role = true`, and default privileges (`ALTER DEFAULT PRIVILEGES`) grant the role, a role it belongs to or `PUBLIC` write access to future tables or sequences. |
| `STU-DX-008` | info | Not every check ran: the time limit ended the diagnosis, or the user canceled it. The detail names the checks. |
| `STU-DX-009` | warning | SQLite: `PRAGMA foreign_key_check` failed, for example a foreign key that points at columns without a primary key or unique index ("foreign key mismatch"). The other findings stay (L-5). |
| `STU-DX-010` | warning (info when only `lock_timeout` or `application_name` is missing) | No `lock_timeout`, `idle_in_transaction_session_timeout` or `application_name` for the login role in this database, with the precedence of `STU-DX-004` (L-1). |

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_diagnostics.py` (SQLite) | The 09 backlog's prepared databases. A 0644 file gives `TAB-SQ-003`. Rows written with foreign keys off give `STU-DX-005`. A rollback journal gives `TAB-SQ-002`. Session findings are left out. A clean WAL file at 0600 has no finding. No path appears in a finding. A broken foreign key definition gives `STU-DX-009` and keeps `TAB-SQ-003`. |
| `tests/security/test_diagnostics.py` (time limit, cancel) | A 1 ms statement timeout on a 400 000-row file ends the run within the limit with `STU-DX-008`, and `quick_check` was stopped (`TAB-SQ-006` "not checked" or tabrion's step named). A cancel from another thread stops a run on a 1.5 M-row file. |
| `tests/security/test_diagnostics.py` (route) | The route is behind the session check; a signed-in request without the CSRF header gets `access.csrf_invalid` on both routes; the cancel route answers 202. |
| `tests/security/test_diagnostics.py` (rules) | Session and replaced codes are left out. The owner rule is an error on `prod`. The T-10 cases (STU-T-021): remote `prefer`/`disable`, loopback, Unix sockets, and `prod` with `require` and `verify-ca`; tabrion's TLS finding replaces `STU-DX-002`. The read-only role, default privileges and the role settings with their precedence. |
| `tests/security/test_diagnostics.py` (PostgreSQL) | No statement timeout for the role (`STU-DX-004`, gone after `ALTER ROLE ... SET statement_timeout`). The owner role that the profile calls read-only (`STU-DX-003`, `STU-DX-006`). The `prod` rules. With `TABRION_STUDIO_TEST_POSTGRES_SUPERUSER` set: a superuser gives `TAB-PG-001`; and on roles made for the test, every write path of M-2 (column grant, `TRUNCATE`, a view, a sequence, INHERIT and NOINHERIT membership) gives `STU-DX-003` on `dev` and `prod`, default privileges give `STU-DX-007`, an owner outside the search path gives `STU-DX-006`, and the role-in-database setting wins over the role's own (`STU-DX-004` once it is 0 there; `STU-DX-010` until the role sets the other three). The precedence over database settings is in the rule tests. |
