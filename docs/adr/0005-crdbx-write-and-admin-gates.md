# STU-ADR-0005: Write and admin features of CR-DBX locked per area until G-S2, G-S3 and G-S4

**Status:** accepted as planning (owner decisions on change request CR-DBX of 2026-11-13 23:28Z, E-X1 and E-X2, and
23:35Z, E-X3..E-X13 as recommended with binding refinements of E-X4, E-X5, E-X6 and E-X9). It accepts the plan,
**not** the start of implementation. It amends [STU-ADR-0003](0003-writes-behind-gate.md), which stays in force for
Studio 1.0. The threat model and the full successor record are work package STU-XW-5 after the development start.

## Context

Change request CR-DBX (planning document 15) adds three areas to Studio: the Data Editor, the Schema Designer and
the Database Manager. STU-ADR-0003 has one flag and one gate (G-S2) and says that the Schema Designer "uses the same
flag and gate model", while CR-DB set its own gate G-S3 for it (contradiction W-5). STU-ADR-0003 also expects 403/404
for a refused write, while the owner decided on 2026-11-13 (STU-054) that a refused write answers 400 or 405 and only
the write mode answers 403 (contradiction W-9). Planning document 03 section 1 lists backup execution and role
management as non-goals, which the Database Manager needs (contradiction W-7).

## Decision

- **Order and versions (E-X2, E-X10):** Studio 1.1 = shared write foundation and Data Editor (STU-026, STU-XW-1..5,
  STU-XDE-1..2); Studio 1.2 = Schema Designer (STU-CR-1..8, STU-XSD-1..3, milestone M10); Studio 1.3 = Database
  Manager (STU-XDM-1..7, milestone M11). Work starts only after Studio 1.0 (G-R), the repository migration and a
  separate development-start approval (G-CX).
- **One server-side flag and one gate per area (E-X9):** Data Editor behind G-S2, Schema Designer behind G-S3,
  Database Manager behind G-S4. G-CX is **not** an approval to enable a write feature: every new write and admin
  feature stays technically locked (flag off, not switchable from the frontend, no executable path) until the gate of
  its area. An appropriate independent human security review is mandatory before any public release; G-R stays
  locked. Each area has its "flag off" negative tests.
- **Error semantics (W-9):** with a flag off, a refused write answers HTTP 400 (a writing statement,
  `studio.statement_*`) or 405 (a changing method on a read route, `studio.method_not_allowed`); only the write mode
  answers 403 (`studio.query_write_unsupported`). This replaces the "403/404" of STU-ADR-0003.
- **PROD (E-X7):** writing stays technically locked on PROD for all three areas; the only action on PROD is creating
  a backup. Restore to PROD is locked.
- **Separate identities (E-X8):** writing uses a write profile and administration an admin profile, each with its own
  role and secret, coupled to the read profile; the read profile stays the default and the database role stays the
  hard boundary. The text check of statements is UX only.
- **Schema changes (E-X4):** direct execution only on DEV and TEST databases without existing migration management.
  For Alembic projects Studio by default only generates a revision into the trusted project and **never runs
  `env.py`** or the project's revision code; the user runs `alembic upgrade` outside Studio. Preview, binding to the
  plan hash and the schema fingerprint, a drift check and an explicit confirmation are mandatory before any
  execution. Studio builds no SQL of its own; plans come from Tabrion (tabrion ADR 0014).
- **PostgreSQL backup and restore (E-X5):** only through the verified system tools `pg_dump`/`pg_restore`: an
  absolute path from the settings, a client version check (client at least the server version), an argument list
  without a shell, the password only in a temporary 0600 `PGPASSFILE`, never on the command line or in logs. Restore
  tests are mandatory. The documentation says plainly that `pg_dump` is **not** a complete backup: it does not cover
  global roles, tablespaces and other cluster objects. SQLite uses Tabrion's `backup()` and `restore()`.
- **Role management (E-X6):** PostgreSQL only, only through the admin profile with the minimal rights it needs.
  Studio never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION. Every change checks the existing grants, the
  role memberships and the privileges reachable indirectly (`SET ROLE`, inherited roles). The admin rights themselves
  are provisioned outside Studio; Studio does not create its own admin role.
- **Common rules for every write mode** (planning document 15 section 7): typed confirmations, the destructive
  action never has the default focus, a backup before destructive operations, a local append-only 0600 audit log
  without values, no secrets in logs, plans, revisions, backup metadata or profile exports, and the I-109 j
  confirmation of secret references on import.
- **Non-goals (W-7):** for Studio 1.0 writing, schema changes, backup execution and role management stay excluded.
  Later versions may add them only within CR-DBX and only behind the gate of their area.

## Consequences

- STU-ADR-0003 keeps its rules for 1.0 and the Data Editor; its "same flag and gate model" for the Schema Designer
  and its "403/404" are replaced by this record.
- Until G-CX nothing of this record is implemented; the 1.0 negative tests (STU-054, `ux-acceptance-1-0.spec.ts`)
  stay as they are.
- Studio still never runs a migration of its own accord: the 1.0 migration view only reads, and the Schema Designer
  writes revisions, not upgrades.

## Tests

Per area before its gate: "flag off" negative tests on DEV and PROD (400/405, 403 only for the write mode), plan
binding and drift tests, the PROD lock, refused role attributes, canary tests for secrets in plans, revisions,
backups and the audit log, and restore tests for SQLite and PostgreSQL.
