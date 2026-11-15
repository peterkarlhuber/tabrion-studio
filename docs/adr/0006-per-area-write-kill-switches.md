# STU-ADR-0006: Per-area write and admin kill switches (successor to STU-ADR-0003)

**Status:** proposed (work package STU-XW-5, change request CR-DBX). It records owner decisions that are already
binding (E-X7, E-X8, E-X9 with its refinement, W-5, W-9 and the conditional development start G-CX of 2026-11-14
11:01Z) and adds the rules that make them testable. It needs the independent review of STU-XW-5 and stays
"proposed" until that review accepts it. It **supersedes [STU-ADR-0003](0003-writes-behind-gate.md)**: the rules
of 0003 are carried over below, its single flag and its "403/404" are replaced. It completes
[STU-ADR-0005](0005-crdbx-write-and-admin-gates.md), which stays the record of the CR-DBX scope (E-X2..E-X13); this
record is the lock model those areas share. The threat model is
[Write and admin threat model](../architecture/write-threat-model.md).

## Context

STU-ADR-0003 has one server-side flag (`writes_enabled`) and one gate (G-S2) for everything that writes. CR-DBX
(planning document 15) adds three areas that write or administer databases, each with its own gate (W-5, E-X9), and
the owner decided on 2026-11-13 that a refused write answers 400 or 405 rather than 403 (W-9, STU-054). Since the
G-CX start conditions S-1..S-4 are proven (planning evidence tracker `cr-dbx-startbedingungen.md`, 2026-11-14),
write and admin code may be developed and merged, but only behind kill switches that are off by default and proven
effective. Planning document 15 section 9.4 asks this record for: one flag and one gate per area, G-CX is not an
activation, and the error semantics of W-9.

## Decision

### One flag and one gate per area

| Area | Version | Server-side flag (name fixed by STU-XW-1) | Gate | Work packages behind it |
|---|---|---|---|---|
| Shared write foundation and Data Editor | 1.1 | Data Editor flag | **G-S2** | STU-XW-1..STU-XW-4, STU-026, STU-XDE-1..2 |
| Schema Designer | 1.2 | Schema Designer flag | **G-S3** | STU-CR-2..STU-CR-6, STU-XSD-1..3 |
| Database Manager | 1.3 | Database Manager flag | **G-S4** | STU-XDM-2..STU-XDM-5 |

- An area's flag enables only that area. No flag implies another; the shared write foundation (write and admin
  profiles, unlocking, audit log, plan and confirmation framework) has no write path of its own and is reachable only
  through an area whose flag is on.
- A gate is the owner's decision after an independent review of the area (E-X9). Passing a gate allows the owner to
  turn the area's flag on; it does not turn it on, and it changes no default.
- **G-CX is not an activation.** The conditional development-start approval of 2026-11-14 allows writing,
  integrating and testing the code. It enables no flag, approves no productive use, and is no release. No tag and no
  public release without G-R; an independent human security review is mandatory before any public release.

### Flags: default off, set only locally by the owner

1. Every flag is **off by default**. A missing, unreadable or malformed value means off. A shipped build, the
   wheel, the test configuration of CI (outside the tests that check the "flag on" path) and every example file
   leave every flag off.
2. A flag is set **only locally, by the owner of the Studio process**, from one source that STU-XW-1 defines and that
   the frontend cannot reach. It is read once when the server starts; a change needs a restart. A command-line
   argument of the `tabrion-studio` command counts as local, by the owner. The preferred source is a file in the
   user's configuration folder (point 4). An environment variable is discouraged, because per-folder tools (direnv,
   `.env` loaders) can set it when the user enters a project folder; if STU-XW-1 chooses one anyway, Studio never
   reads `.env` files, and the start log entry (point 6) is the main safeguard (review N-1 of PR #109).
3. A flag is **never** taken from:
   - the frontend: no API route reads, sets or toggles a flag, and the settings route (`PUT /api/settings`) refuses
     a flag key like any unknown key (`config.unknown_key`);
   - a project file (`.tabrion/studio.toml`) or anything else in a project folder, trusted or not;
   - a URL, a path or query parameter, a header, a cookie, the launch token or the session;
   - a database, a profile, a connection string or a profile import;
   - browser storage.
4. If the source STU-XW-1 chooses is a file, it gets the checks of Studio's own files (0600, owned by the user, not a
   link or junction, in a folder that only the user may write).
5. The page may learn whether an area is available (to hide or show controls), read only. Hiding a control is UX; the
   server refuses regardless of what the page shows.
6. Each area's state is logged once at start (area and on or off, nothing else), so a flag left on is visible.

### Error semantics (W-9)

A refused write is answered the same way with the flag off and, after a gate, for any refusal of the lock rules:

| Request | Answer |
|---|---|
| A writing statement on a read route (query, export) | **400** `studio.statement_*`, before it runs |
| A changing method on a read route, or on a write route of an area whose flag is off | **405** `studio.method_not_allowed` |
| A request for the write mode (for example `mode: "write"`) while it is not available | **403** `studio.query_write_unsupported` |

- 403 is used **only** for the write mode. A refused write is never 403 and never 404.
- With its flag off, an area's write route is answered by a refusing handler that does not import, construct or call
  any of the area's write code.
- A refusal is a catalog problem (`problems.problem`): it never echoes a cell value, a statement, a parameter, an
  object name taken from the request, a secret or a database message, and the same holds for its log record and any
  audit or undo record (see the threat model, TX-14).

### PROD is write-locked (E-X7)

- There is **no code path that writes to a PROD database**, in any area, with any flag. A profile with
  `environment = "prod"` cannot have a write or admin profile coupled to it, and every write and admin entry point
  checks the environment on the server again, from the stored profile, never from the request.
- **The environment of a write target comes only from the user's own profile store** (condition K-1 of the PR #109
  review). A project profile (`.tabrion/studio.toml`) carries its own `environment`, so a hostile or wrong project
  file could label a production host "dev". Therefore write and admin profiles and their couplings exist only in the
  user's `profiles.toml` and couple only to user-local read profiles, never to a project profile, trusted or not
  (see the profile section). Changing the environment of a coupled read profile ends every unlock; lowering it from
  `prod` cannot happen while a coupling exists, because a PROD profile cannot be coupled.
- The only Database Manager action on PROD is creating a backup, which reads. Restore to PROD is locked.
- Loosening the PROD lock needs a new owner decision and its own gate; no flag can do it.
- Read paths (query editor, data viewer, explorer, export, diagnostics, vector data, migration status and its worker,
  history) always use the read profile's connection and never an unlocked write or admin connection.

### Separate write and admin profiles (E-X8)

- Writing uses a **write profile** and administration an **admin profile**, each with its own database role and its
  own secret, coupled to a read profile. The read profile stays the default. The database role is the hard boundary;
  the text check of statements is UX only.
- **Only in the user's own profile store (K-1):** write and admin profiles, and the coupling of a write or admin
  profile to a read profile, are defined, imported and changed only in the user's own profile store. They are never
  defined, imported or changed from a project folder. A write or admin profile couples only to a **user-local read
  profile**, never to a project profile, trusted or not. A project file that defines a write or admin profile or a
  coupling, or names a user's write profile to couple to, is refused or ignored; STU-XW-1 has a test for each case.
- When a write profile is created, the page says, at that moment and not only in the guide, that Studio relies on
  the environment the user gives the profile and that write credentials for a production database do not belong in
  Studio (review N-6).
- The **admin profile has the minimal rights** its area needs. On every connect Studio checks the role's privileges
  (attributes, memberships, privileges reachable through `SET ROLE` or inheritance, ownership) and reports a
  **finding on over-privilege** (for example SUPERUSER). What the finding blocks is fixed by STU-XDM-1 (G-S4
  definition).
- **Studio never sets up admin rights for itself.** The admin rights are provisioned outside Studio; Studio does not
  create, grant or alter its own admin role, and never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION,
  directly or through membership (E-X6).

### Hostile database objects on the write path (K-2)

On the write path, objects the database defines (functions, operators, triggers, rules) run with the write role. A
hostile or simply unexpected object must not change which rows the optimistic lock and the row count see, nor
escape the write connection's limits.

| Measure | Work package |
|---|---|
| **PostgreSQL:** write and admin statements run under a fixed catalog-first `search_path` (as `pin_catalog_path` does for catalog reads, STU-022 H-1), and every name in them is schema-qualified, so no operator or function in a user schema takes part in the optimistic lock (TX-09) or the row count | STU-XW-1 (write connection setup), STU-XW-3 (plan execution), STU-026 (changeset statements); tests with a shadowing operator and function in a user schema |
| **PostgreSQL:** triggers and rules on the target table run with the write role; this is part of the residual risk "the role is the boundary", and the preview names them where Tabrion reports them | STU-XW-3 (preview), STU-026 |
| **SQLite:** the write connection keeps `trusted_schema = OFF`, Studio's authorizer (no `ATTACH`, no `load_extension`, no PRAGMA writes beyond an allowlist) and defensive mode (`SQLITE_DBCONFIG_DEFENSIVE`), so triggers in a hostile file run only within these limits and no statement can corrupt the schema | STU-XW-1 (write connection setup); tests for each setting and a trigger that tries to leave the limits |
| **SQLite target identity:** the write connection opens the same file as the read profile, not through a link, checked by device and inode (or the Windows file ID) at unlock and again before each run; a different file stops the run, so a file swapped between preview and run cannot be compared against the wrong fingerprint | STU-XW-1 (unlock), STU-XW-3 (before each run), STU-026; a test that swaps the file between preview and run |

### Rules carried over from STU-ADR-0003

After an area's gate, and in tests before it:

- unlocking per connection and session with the typed profile name, expiring after 15 minutes of inactivity, with a
  visible write state;
- changes as parameterized statements with an optimistic lock, a diff or plan preview and one transaction; a
  statement that should affect one row and affects another number rolls back;
- the plan hash is bound to the session, the connection and the unlock, and is valid for one run only (review N-2);
- write-mode statements and Data Editor changesets never reach the persisted query history or the draft-save queue,
  because a changeset holds cell values (review N-3);
- destructive classes need an extra typed confirmation; the destructive action never has the default focus;
- Studio never changes a schema of its own accord: only an explicitly confirmed plan, bound to its preview by the
  plan hash and the schema fingerprint, with a drift check just before it runs (E-X4);
- a local append-only audit log without values (STU-XW-2): a write whose record before the run cannot be written
  does not run; if the record after the run fails, the write has happened, so Studio keeps a pending marker and shows
  a warning instead of losing the record silently (review N-4);
- write and admin secrets are never exported: not in a profile export, a diagnostics report or bundle, or any
  support output (review N-5).

### Values of the Data Editor (STU-XDE-1)

A value a changeset writes is checked twice; the server's check decides (threat TX-17):

- The table route sends each column's type, read from the catalog (kind, NULL, whether an insert may leave it out,
  whether it takes a value at all, length, precision and scale, an enum's labels, arrays). The page picks a cell
  editor from it and checks every changed value before the preview. That is help for the user, not a boundary.
- When a plan is made, the executor checks every new value against the same types read again from the catalog, and
  that an insert does not leave out a NOT NULL column without a default. A refusal is `400
  studio.data_editor_value_invalid` (W-9) before a plan exists. Its issues give the change's position in the request,
  the column's position in the table and a fixed `value.*` code; never a value, a column name or database text.
- The column types are part of the plan fingerprint, so a schema change between preview and run is drift.
- The database stays the last instance (CHECK, UNIQUE, foreign keys, domain constraints, triggers).
- Where a silent conversion would change a value's meaning, the checks refuse instead of converting: a
  `timestamptz` or `timetz` needs an offset, a `timestamp` or `time` takes none, NULL is never the empty text, and a
  top-level JSON `null` is not taken for SQL `NULL`.
- Where PostgreSQL takes a spelling without changing the value, the checks take it too: whitespace around a number,
  and a time of exactly `24:00`. The checks parse each part themselves and do not depend on the Python version (3.14's
  `fromisoformat` takes `24:00`, earlier ones do not).

### Proof required from every kill-switch pull request

Every pull request that adds or changes write or admin code must show, in its tests and description:

1. **Default off:** a test that starts the server with no flag source and asserts the area is off.
2. **Effective:** for **each** write or admin endpoint of the area a negative test "flag off" that expects the W-9
   answer (400, 405, or 403 for the write mode) and asserts that nothing changed (database bytes or rows, schema,
   files, audit log).
3. **Not switchable from outside:** a test that the flag stays off when a project file, a settings save, a query
   parameter or a header tries to turn it on.
4. **PROD:** a test that the endpoint refuses a PROD profile with the flag on.
5. **Project files:** for profile and coupling code, a test that a project file cannot define a write or admin
   profile or a coupling (K-1).
6. The new changing routes are added to `CHANGING_ROUTES` in `tests/security/test_every_route.py` with their reason.

The pull request is not merged without these, and the gate review re-checks them.

## Consequences

- STU-ADR-0003 is superseded; its rules live on above. Studio 1.0 is unchanged: no write route exists, the 1.0
  negative tests (STU-054, `ux-acceptance-1-0.spec.ts`) stay as they are.
- STU-XW-1 fixes the flag names and their single local source within the constraints above.
- Planning document 03 section 8 still says "prod-Schreibversuche über API alle 403" for the Data Editor row; under
  W-9 a refused PROD write answers 400 or 405 and only the write mode 403. The planning text needs the same
  correction as the 1.0 row (open point for the coordinating thread).

## Tests

Per area before its gate: the kill-switch proof above; plan binding and drift tests; the PROD lock on every entry
point; canary tests that no value or secret reaches an answer, a log, the audit log, a plan, a revision or backup
metadata; and for the Database Manager the refused role attributes and the restore tests (STU-ADR-0005).
