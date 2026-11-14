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
   the frontend cannot reach. It is read once when the server starts; a change needs a restart.
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
- The only Database Manager action on PROD is creating a backup, which reads. Restore to PROD is locked.
- Loosening the PROD lock needs a new owner decision and its own gate; no flag can do it.
- Read paths (query editor, data viewer, explorer, export, diagnostics, vector data, migration status and its worker,
  history) always use the read profile's connection and never an unlocked write or admin connection.

### Separate write and admin profiles (E-X8)

- Writing uses a **write profile** and administration an **admin profile**, each with its own database role and its
  own secret, coupled to a read profile. The read profile stays the default. The database role is the hard boundary;
  the text check of statements is UX only.
- The **admin profile has the minimal rights** its area needs. On every connect Studio checks the role's privileges
  (attributes, memberships, privileges reachable through `SET ROLE` or inheritance, ownership) and reports a
  **finding on over-privilege** (for example SUPERUSER). What the finding blocks is fixed by STU-XDM-1 (G-S4
  definition).
- **Studio never sets up admin rights for itself.** The admin rights are provisioned outside Studio; Studio does not
  create, grant or alter its own admin role, and never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION,
  directly or through membership (E-X6).

### Rules carried over from STU-ADR-0003

After an area's gate, and in tests before it:

- unlocking per connection and session with the typed profile name, expiring after 15 minutes of inactivity, with a
  visible write state;
- changes as parameterized statements with an optimistic lock, a diff or plan preview and one transaction; a
  statement that should affect one row and affects another number rolls back;
- destructive classes need an extra typed confirmation; the destructive action never has the default focus;
- Studio never changes a schema of its own accord: only an explicitly confirmed plan, bound to its preview by the
  plan hash and the schema fingerprint, with a drift check just before it runs (E-X4);
- a local append-only audit log without values (STU-XW-2).

### Proof required from every kill-switch pull request

Every pull request that adds or changes write or admin code must show, in its tests and description:

1. **Default off:** a test that starts the server with no flag source and asserts the area is off.
2. **Effective:** for **each** write or admin endpoint of the area a negative test "flag off" that expects the W-9
   answer (400, 405, or 403 for the write mode) and asserts that nothing changed (database bytes or rows, schema,
   files, audit log).
3. **Not switchable from outside:** a test that the flag stays off when a project file, a settings save, a query
   parameter or a header tries to turn it on.
4. **PROD:** a test that the endpoint refuses a PROD profile with the flag on.
5. The new changing routes are added to `CHANGING_ROUTES` in `tests/security/test_every_route.py` with their reason.

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
