# Diagnostics

The diagnostics give the findings for one open connection (03 sections 2 G and 6, work package STU-014). The
diagnosis is made of tabrion's checks plus Studio's own checks, and it only reads. STU-025 adds the backup hints and
the diagnosis report (Markdown or JSON, without secrets).

## Route

| Route | Answer |
|---|---|
| `POST /api/connections/{id}/diagnostics` | `{connection_id, engine, environment, ran_at, findings: [{code, severity, title, detail, remedy, source}], counts: {error, warning, info}, backup: {hints: [...], facts: {...}}}` |
| `POST /api/connections/{id}/diagnostics/cancel` | 202 `{"canceled": true \| false}`: stops the running diagnoses of that connection within a second |
| `POST /api/connections/{id}/diagnostics/report` | Body `{"format": "markdown" \| "json"}`. 200 with the report as a file: `Content-Disposition: attachment; filename="tabrion-studio-diagnosis-<engine>-<environment>-<UTC time>.md"` (or `.json`). See [Diagnosis report](#diagnosis-report-stu-025). |

The route is a `POST` because it runs checks against the database (`PRAGMA quick_check` on SQLite can take a while),
not because it changes anything. Like every `POST`, it needs the session, the CSRF header and a JSON body (`{}`).

Findings are sorted by severity (`error`, `warning`, `info`) and then by code. `source` is `tabrion` or `studio`.
No finding names a file path, a password or a value.

**Time limit and cancel (M-1 of `stu-014-review.md`).** A diagnosis runs at most the connection's statement timeout
(the profile's, else 30 s, 15 s on `prod`; 03 section 5.3). Then, or after the cancel route, every running statement
is stopped like a query: `sqlite3.Connection.interrupt()` on SQLite, psycopg's `cancel_safe` on PostgreSQL
(`tabrion_compat.Cancel`), and the connection that got it leaves the pool. The cancel is sent again every 0.1 s (for
3 s) until the statement stops, since a cancel that comes before a statement starts does nothing. The checks that
did not run are named in `STU-DX-008`.

On SQLite, tabrion's step is the exception (N-1 of the delta review): only its integrity check (`PRAGMA
quick_check`) can take long, while the other checks are a few pragmas and file modes. So the cancel stops
`quick_check` alone. A progress handler ends it when the limit or a cancel comes while it runs, and a limit or cancel
that came before it started interrupts it as it starts (from the trace callback, while it is the only running
statement). Every other statement of tabrion's step runs, even past the limit (they take milliseconds), so tabrion's
other findings stay: a 0644 file still gives `TAB-SQ-003`. The stopped `quick_check` is tabrion's "not checked"
`TAB-SQ-006` (info), never "damaged", and `STU-DX-008` names "tabrion's integrity check (TAB-SQ-006)". No interrupt is
left for a later statement, so that connection may go back to the pool. On PostgreSQL a limit or cancel during
tabrion's step stops the step and every later check (one deadline per run; I-3 of the delta review).

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
| `STU-DX-009` | warning | SQLite: `PRAGMA foreign_key_check` failed. Only "foreign key mismatch" (parent columns without a primary key or unique index) and "no such table" (a missing parent on old SQLite) are called a broken definition; a busy, locked or unreadable file gets a neutral text (L-5). Errors that are not database errors are not findings. The other findings stay. |
| `STU-DX-010` | warning (info when only `lock_timeout` or `application_name` is missing) | No `lock_timeout`, `idle_in_transaction_session_timeout` or `application_name` for the login role in this database, with the precedence of `STU-DX-004` (L-1). |

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_diagnostics.py` (SQLite) | The 09 backlog's prepared databases. A 0644 file gives `TAB-SQ-003`. Rows written with foreign keys off give `STU-DX-005`. A rollback journal gives `TAB-SQ-002`. Session findings are left out. A clean WAL file at 0600 has no finding. No path appears in a finding. A broken foreign key definition gives `STU-DX-009` and keeps `TAB-SQ-003`. |
| `tests/security/test_diagnostics.py` (time limit, cancel) | A 1 ms statement timeout on a 400 000-row file ends the run within the limit with `STU-DX-008`, and `quick_check` was stopped (`TAB-SQ-006` "not checked"; tabrion's step itself ran). A cancel from another thread stops a run on a 1.5 M-row file the same way. |
| `tests/security/test_diagnostics.py` (delta review of `a11c8bb`) | N-1: a 0644 file with a 1 ms limit keeps `TAB-SQ-003` and `TAB-SQ-002`, and `TAB-SQ-006` is "not checked"; at the compat layer a cancel requested before the check stops `quick_check` only. T-1: a cancel during tabrion's PostgreSQL check stops it and closes that driver connection (attach, discard); a cancel stops Studio's own PostgreSQL queries and SQLite's foreign key check; canceling connection B leaves A's run alone; the cancel repeats until the run ends; an owner through a group role is `STU-DX-006`. L-5: lock and I/O errors are not called a broken definition. CI-1: what a plain database owner can check itself (its role-in-database setting over the database's, an owned table off the search path). The 11 mutations of the review's survivors and of these fixes are all killed. |
| `tests/security/test_diagnostics.py` (route) | The route is behind the session check; a signed-in request without the CSRF header gets `access.csrf_invalid` on both routes; the cancel route answers 202. |
| `tests/security/test_diagnostics.py` (rules) | Session and replaced codes are left out. The owner rule is an error on `prod`. The T-10 cases (STU-T-021): remote `prefer`/`disable`, loopback, Unix sockets, and `prod` with `require` and `verify-ca`; tabrion's TLS finding replaces `STU-DX-002`. The read-only role, default privileges and the role settings with their precedence. |
| `tests/security/test_diagnostics.py` (PostgreSQL; the role-making tests carry the marker `postgres_superuser`, see below) | No statement timeout for the role (`STU-DX-004`, gone after `ALTER ROLE ... SET statement_timeout`). The owner role that the profile calls read-only (`STU-DX-003`, `STU-DX-006`). The `prod` rules. With `TABRION_STUDIO_TEST_POSTGRES_SUPERUSER` set: a superuser gives `TAB-PG-001`; and on roles made for the test, every write path of M-2 (column grant, `TRUNCATE`, a view, a sequence, INHERIT and NOINHERIT membership) gives `STU-DX-003` on `dev` and `prod`, default privileges give `STU-DX-007`, an owner outside the search path gives `STU-DX-006`, and the role-in-database setting wins over the role's own (`STU-DX-004` once it is 0 there; `STU-DX-010` until the role sets the other three). The precedence over database settings is in the rule tests. |

**Markers (CI-1 of the delta review).** The tests that make roles need `TABRION_STUDIO_TEST_POSTGRES_SUPERUSER` (a
superuser of the same server) and carry the marker `postgres_superuser` instead of `postgres`: a role that owns its
database but may not create roles (the PostgreSQL 17 CI job's `studio_owner`) cannot set them up. The tests that only
need the owner keep the marker `postgres`. Run both with `pytest -m "postgres or postgres_superuser"`
and both URLs set. The PostgreSQL 17 CI job does exactly that: the service's own superuser URL is set on its pytest
step only, and its "none skipped" check expects at least 50 tests.

**Cost of the role query (I-2 of the delta review).** `STU-DX-003` checks every table against every role the session
can become, so its cost is tables times member roles: a NOINHERIT role with 400 memberships on 3000 tables took
1.8 s in the review, linear and bounded by the time limit (then `STU-DX-008`).

## Backup hints (STU-025)

03 section 2 G asks for backup hints: the last backup artifact on SQLite, and `archive_mode` and `pg_stat_archiver`
on PostgreSQL. Studio does not make or restore backups (03 section 1), so the hints only say what Studio can see.
They come with every diagnosis of the route (`run(..., backup=True)`), under the same time limit and cancel; a
skipped step is named "backup hints" in `STU-DX-008`. They are in `backup.hints`, not in `findings`, and they are
not counted in `counts`. `backup.facts` holds what they are based on. Code: `services/backup_hints.py`.

**SQLite.** `tabrion.sqlite.backup` has no naming rule, so Studio counts as a backup a regular file (no link, no
FIFO) in the database's folder or in its `backups` or `backup` subfolder whose name is the database's name without
its last suffix followed by a separator `.`, `-` or `_` (for `shop.db`: `shop-v2.db`, `shop.db.bak`,
`shop_2026-11-12.db`; not `shop1.db` or `shopping.db`). It must start with SQLite's 16-byte file header; the
database itself, its `-wal`, `-shm` and `-journal` files and tabrion's `.tmp` files do not count. The details, after
the review (`stu-025-review.md`):

- A `backups` or `backup` subfolder that is a link is not followed (L-2): it is checked with `lstat`, and on POSIX
  each folder is opened with `O_DIRECTORY | O_NOFOLLOW` and listed through that descriptor, with every candidate
  opened relative to it. A candidate is refused twice over: `lstat` must say regular file, and it is opened with
  `O_NOFOLLOW` and `O_NONBLOCK` and checked with `fstat`. Only its first 16 bytes are read.
- The scan asks the run's cancel hook before every entry and stops at the time limit or a cancel (L-3); the step is
  then named "backup hints" in `STU-DX-008`.
- At most 10 000 entries of each folder are read, in no particular order; a cut search is the hint `STU-BK-005`
  as well as the fact `search_truncated` (L-4).
- "Readable by others" means the read bits `0o044` (L-5); a write- or execute-only bit does not count.
- Accepted (L-4): another database whose name starts with the same name and a separator (`shop-orders.db`, or any
  `a_*` file next to `a.db`) counts too; the hint says where Studio looked.

The database's last change is the newer modification time of the file and its `-wal` file.

**PostgreSQL.** One query reads `current_setting('archive_mode')`, `pg_is_in_recovery()` and `pg_stat_archiver`
(counts and times), which every role may read, through Studio's read-only statement path. `archive_command` is
not read: it may hold a secret, and only a superuser may read it. Backups made with `pg_dump` or a tool that does not
archive WAL are not visible. When the hints hold `STU-BK-010`, tabrion's `TAB-PG-020` ("WAL archiving is off") is
left out of the findings, since `STU-BK-010` says the same with the archiver's state (one cause, one entry). It is
decided after the backup step (C-2 of the review): after a cancel, the time limit or `STU-BK-014`, and whenever the
archiver read gives no `STU-BK-010`, `TAB-PG-020` stays.

| Code | Severity | Hint |
|---|---|---|
| `STU-BK-001` | warning | SQLite: no backup found where Studio looks. |
| `STU-BK-002` | info (warning when more than 7 days) | SQLite: the newest backup is older than the database's last change; the detail says by how much. |
| `STU-BK-003` | warning | SQLite (POSIX): backup files that the group or others may read (`tabrion.sqlite.backup` makes them 0600, TAB-T-122). |
| `STU-BK-004` | info | SQLite: the folder or a backup subfolder could not be listed. |
| `STU-BK-005` | info | SQLite: a folder holds more than 10 000 entries; a backup may not have been seen. |
| `STU-BK-010` | info (warning on `prod`) | `archive_mode` is off: no point-in-time recovery; `pg_dump` backups are not visible. |
| `STU-BK-011` | warning (error on `prod`) | The archiver failed, and its last attempt failed (`last_failed_time` not older than `last_archived_time`). |
| `STU-BK-012` | info | `archive_mode` is on, but nothing was archived since the statistics were reset. |
| `STU-BK-013` | info | A standby with `archive_mode = on`, which archives only on the primary. |
| `STU-BK-014` | info | `archive_mode` and `pg_stat_archiver` could not be read with this role; the diagnosis goes on. |

Facts (`backup.facts`), all without a path or name: SQLite `backups_found`, `newest_backup_at`,
`newest_backup_age_s`, `database_changed_at`, `backups_readable_by_others`, `search_truncated`, `folders`;
PostgreSQL `archive_mode`, `standby`, `archived_count`, `failed_count`, `last_archived_at`, `last_failed_at`,
`stats_reset_at`. Times are epoch seconds in the route's answer and ISO 8601 UTC text in the report.

## Diagnosis report (STU-025)

`POST /api/connections/{id}/diagnostics/report` runs a new diagnosis with its backup hints (so the report shows
what is true when it is made; the cancel route stops it like any diagnosis) and answers it as a file in one of the
formats of 03 section 2 G. The report is small, so it is one response body, not a stream like an export; the
`Content-Disposition` header is the export's (`attachment; filename="..."`). The page downloads it with `fetch`
behind the session and the CSRF check, like an export. `studio.report_invalid` (400) answers another format, a body
that is not a JSON object or one over 1 KiB; an unknown connection is `studio.connection_not_found`.
Code: `services/diagnosis_report.py`, route in `api/diagnostics.py`.

| Format | Media type | Content |
|---|---|---|
| `markdown` | `text/markdown; charset=utf-8` | A title, a list (time, versions, profile, engine, environment, on PostgreSQL the read-only role expectation and `sslmode`, counts), the findings and backup hints (one heading each with severity, code and title, then detail, remedy and source), a table of the backup facts and a closing line that names what was left out. |
| `json` | `application/json` | `{report: "tabrion-studio-diagnosis", format_version: 1, generated_at, studio_version, tabrion_version, connection: {profile, engine, environment, read_only, read_only_role?, sslmode?}, diagnosis: {ran_at, counts, findings}, backup: {hints, facts}, left_out}` |

**Without secrets (09 STU-025 acceptance; T-05, SQ-04).** The report is built from an allowlist, never from the
profile or the connection as a whole:

- In it: the report's time, Studio's and tabrion's versions, the profile's name, engine and environment, on
  PostgreSQL whether the profile expects a read-only role and its `sslmode`, the findings, hints and backup facts.
- Never in it: a password or other secret, a token of the process (control, launch, session, CSRF), the connection
  URL or a DSN, the host, port, a file path or file name, the connection ID, SQL text and parameter values. Studio's
  findings and hints name none of these.
- The login role and the database (C-1 of the review): tabrion's details may name schemas and extensions
  (`TAB-PG-009`, `TAB-PG-030`), and a schema is often named exactly like the login role (the `"$user"` default of
  `search_path`) or the database. So on PostgreSQL the report asks the server `SELECT session_user, current_user,
  current_database()` and adds the profile's `user` and `database`, and replaces each of these names with `[role]`
  or `[database]` in every text of the report (titles, details, remedies, facts, the profile name) where it stands as
  a whole identifier (`app` stays inside `application_name`). Names of other database objects stay; the report's
  closing line says so.
- Second line of defense: every text of the report, keys included, passes `logs.scrub`, the log redaction. It
  replaces the process's registered secrets (resolved passwords, keyring/`env:`/`file:` values, the tokens) and
  anything shaped like a URL password, a `password=`-style pair, a bearer token, an `Authorization` header or the
  session cookie with `[redacted]`. A tabrion finding that ever carried such text would be cut there.

**Markdown from database text (T-07).** Some details hold names from the database (tabrion names schemas and
extensions). In the Markdown report every such text is put on one line (controls and line separators become spaces),
never starts a line, and has the characters of inline markup escaped with a backslash (`` \ ` * _ [ ] < > ! | ~ & # @ ``; `@` for mentions and e-mail autolinks, L-1),
and `://` and `www.` are broken up, so a name cannot become a link, an image, HTML, a table cell or a heading where
the report is rendered. The JSON report is plain JSON.

### Tests

| Test | Covers |
|---|---|
| `tests/security/test_diagnosis_report.py` (backup hints) | SQLite: no backup, a fresh private backup, a backup older than the data by hours and by days, the `-wal` file as a change, a backup readable by others, what does not count (no header, another name, `.tmp`, a link, a folder, a FIFO, a subfolder that is not the database's own), the entry bound, an unreadable subfolder, no path or name in hints or facts. PostgreSQL: every rule of the table with `prod`, the facts, `TAB-PG-020` replaced only in a run with hints, an unreadable archiver; against the server, the archiver query passes the statement check and reads. The time limit skips the hints and names them in `STU-DX-008`. |
| `tests/security/test_diagnosis_report.py` (report) | Both formats (media type, file name, JSON keys and times, Markdown headings), the hostile Markdown text, the route (`Content-Disposition`, `no-store`, the view's `backup`, `studio.report_invalid` for other formats and bodies, an unknown connection, the CSRF and session checks). |
| `tests/security/test_diagnosis_report.py` (canaries) | `tabrion-canary-<uuid>` as the SQLite file's folder and name, a backup's name, a stored value and a parameter value, with the session, CSRF, launch and control tokens and secret-shaped finding texts (URL passwords, DSN `password=`, bearer, API key, cookie) over HTTP; on PostgreSQL as the role's password, its `application_name` setting, a stored value and a parameter value, with DSN-shaped texts (marker `postgres_superuser`). Neither format nor the log holds the canary; the PostgreSQL report holds no host, port, user or database name. A registered secret in any finding text is redacted. |
| `tests/security/test_diagnosis_report.py` (review `stu-025-review.md`) | C-1: the PostgreSQL canary role has a schema named exactly like it (`"$user"`) and may create in one named exactly like the database; the plain diagnosis names both (the control), and neither report holds either name, raw or Markdown-escaped, but `[role]` and `[database]`; whole-identifier replacement. C-2: `TAB-PG-020` stays for `STU-BK-014`, a disagreeing archiver read, and a cancel or the time limit during the archiver query (which is `STU-DX-008`, never `STU-BK-014`). L-1 `@`. L-2 a linked `backups`/`backup` folder. L-3 the scan stops between entries, and a cancel during it is `STU-DX-008`. L-4 the separator rule and `STU-BK-005`. L-5 and L-6: modes `0o640`, `0o604` count, `0o620`, `0o602`, `0o611` do not; the `lstat` check and `O_NOFOLLOW` each alone; a valid JSON body over 1 KiB; JSON key redaction; the hint order; the Markdown side's own redaction. 16 targeted mutations (the review's survivors and each fix) are all killed. |
