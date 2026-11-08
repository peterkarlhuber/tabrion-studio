# Diagnostics

The diagnostics give the findings for one open connection (03 sections 2 G and 6, work package STU-014). The
diagnosis is made of tabrion's checks plus Studio's own checks, and it only reads.

## Route

| Route | Answer |
|---|---|
| `POST /api/connections/{id}/diagnostics` | `{connection_id, engine, environment, ran_at, findings: [{code, severity, title, detail, remedy, source}], counts: {error, warning, info}}` |

The route is a `POST` because it runs checks against the database (`PRAGMA quick_check` on SQLite can take a while),
not because it changes anything. Like every `POST`, it needs the session, the CSRF header and a JSON body (`{}`).

Findings are sorted by severity (`error`, `warning`, `info`) and then by code. `source` is `tabrion` or `studio`.
No finding names a file path, a password or a value.

## Findings

**tabrion** (`TAB-SQ-*`, `TAB-PG-*`): `tabrion.check` runs on the connection's driver connection, inside one
`Database.read()` (`tabrion_compat.check`; with tabrion 0.2 this becomes `tabrion.check(db)`, TD-013).

Some of tabrion's findings describe the session they run in. That session is Studio's, not the application's, so
Studio leaves those findings out:

| Code | Why it is left out |
|---|---|
| `TAB-PG-004`, `-005`, `-006` | Studio sets its own statement, lock and idle timeouts. |
| `TAB-PG-007` | Studio sets its own `application_name`. |
| `TAB-SQ-001`, `-005`, `-009` | Studio's connection sets `foreign_keys`, `busy_timeout` and `trusted_schema` itself. |

In their place, Studio checks what the application actually gets (`STU-DX-004` and `STU-DX-005`). On a `prod`
profile, `TAB-PG-003` (the role owns tables) is raised to an error, because 03 section 5.4 requires a non-owner role.

**Studio** (`STU-DX-*`):

| Code | Severity | Finding |
|---|---|---|
| `STU-DX-001` | error | A `prod` profile whose `sslmode` is neither `verify-ca` nor `verify-full` (03 section 5.4). |
| `STU-DX-002` | warning | `sslmode` `disable`, `allow` or `prefer` to a host that is not loopback (T-10, STU-T-021). |
| `STU-DX-003` | warning (error on `prod`) | The profile sets `read_only_role = true`, but the role may insert, update or delete in a table (`has_table_privilege`; at most 1000 tables are counted). |
| `STU-DX-004` | warning | Neither the role nor the database sets `statement_timeout` (`pg_db_role_setting`). A role without superuser rights cannot read `postgresql.conf`, so the detail says that a server-wide setting may still apply. |
| `STU-DX-005` | warning | SQLite rows that break a foreign key (`PRAGMA foreign_key_check`, at most 1000 counted): the data was written with `foreign_keys` off. |

## Tests

| Test | Covers |
|---|---|
| `tests/security/test_diagnostics.py` (SQLite) | The 09 backlog's prepared databases. A 0644 file gives `TAB-SQ-003`. Rows written with foreign keys off give `STU-DX-005`. A rollback journal gives `TAB-SQ-002`. Session findings are left out. A clean WAL file at 0600 has no finding. No path appears in a finding. The route is behind the session check. |
| `tests/security/test_diagnostics.py` (rules) | Session codes are left out. `TAB-PG-003` is an error on `prod`. The T-10 cases (STU-T-021): remote `prefer`/`disable`, loopback, and `prod` with `require` and `verify-ca`. The read-only role and role timeout rules. |
| `tests/security/test_diagnostics.py` (PostgreSQL) | No statement timeout for the role (`STU-DX-004`, gone after `ALTER ROLE ... SET statement_timeout`). The owner role that the profile calls read-only (`STU-DX-003`, `TAB-PG-003`). The `prod` rules. With `TABRION_STUDIO_TEST_POSTGRES_SUPERUSER` set, a superuser gives `TAB-PG-001`. |
