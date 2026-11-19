# Write and admin threat model (STU-XW-5, CR-DBX)

This page is the threat model of the shared write foundation of Studio 1.1 (STU-XW-1..STU-XW-4) and the Data Editor
(STU-026, STU-XDE-1..2), the first areas of change request CR-DBX (planning document 15). It extends the 1.0 threat
model T-01..T-16 (planning document 03 section 7.3, [Security review preparation](security-review-prep.md#threat-model-t-01t-16))
and is the threat model that [STU-ADR-0006](../adr/0006-per-area-write-kill-switches.md) refers to. The Schema
Designer and the Database Manager have their own extensions, [Schema Designer threat model](schema-designer-threat-model.md)
(STU-CR-1, G-S3, TX-18..TX-31) and [Database Manager threat model](database-manager-threat-model.md) (STU-XDM-1,
G-S4, TX-32..TX-46); the rows marked "shared" here apply to them as well.

**State:** accepted with STU-XW-5 (independent review 2026-11-14). The work packages STU-XW-1..STU-XW-4 implement
their rows behind the default-off flags (TX-07: `require_auth` and the I-109 j confirmation, STU-XW-4). Every measure is a requirement for the work
package named in its row and is checked by the gate review of G-S2. Studio 1.0 has no write route, and every area
stays behind its default-off flag until its gate (STU-ADR-0006).

The repository is public, so this page describes threats and measures, not how to exploit them. Reproduction
details of findings are kept with the planning documents.

## Scope

- **In scope:** write and admin profiles and their coupling to a read profile; unlocking the write mode; the
  per-area flags; the PROD lock; the plan, preview and confirmation framework (D-09..D-11) with plan hash and
  fingerprint; the local audit log; the I-109 j confirmation of secret references on profile import (D-15) with
  `require_auth`; the Data Editor's changesets.
- **Shared with later areas:** the flags, the PROD lock, the profile separation, the plan binding and the audit log.
- **Out of scope here:** DDL execution and Alembic revisions (STU-CR-1, G-S3, [its threat model](schema-designer-threat-model.md));
  backup, restore, database deletion and role management (STU-XDM-1, G-S4, [its threat model](database-manager-threat-model.md)),
  which add the rows planning document 15 section 7 lists (restore to the wrong database, `pg_dump` from a foreign
  `PATH`, role escalation).

## Assets, boundaries and attackers

The assets A1..A6, the boundaries B1..B6 and the attackers AT1..AT7 of 03 section 7 stay. New or sharper:

| Id | Asset or boundary | Why it matters now |
|---|---|---|
| A3 | Integrity of the connected databases | Studio can now change them; before 1.1 it could not |
| A7 | The write and admin secrets | They grant more than the read secret; a leak is worse |
| A8 | The audit log | The only local record of what Studio changed |
| A9 | The flag state | The single switch between "locked" and "can write" |
| B7 | Page ↔ write routes | A changing request from the page must be the user's, for the plan the user saw |

The main risk stays AT7 (the developer's own mistake: the wrong profile, the wrong environment, an old plan).

## Threats and measures

STRIDE as in 03 section 7.3. "Tests" names what the work package must add; the gate review checks it.

| Id | STRIDE | Threat | Measures | Work package, tests |
|---|---|---|---|---|
| TX-01 | E | **Flag tampering:** a flag is turned on from outside the owner's control (frontend, project file, URL, header, profile, database, a settings save) | Default off; a missing or malformed value is off. One local source only, defined by STU-XW-1, read once at start (a command-line argument counts as local; preferred is a file in the user's configuration folder; an environment variable is discouraged because direnv or `.env` loaders can set it per project folder, and if used, Studio never reads `.env` files); no route reads or sets it; the settings writer refuses it as an unknown key; never from a project folder, a URL or query parameter, a header, a cookie, the session, a profile, a profile import or the database. Each area's state logged at start. A flag enables only its own area | STU-XW-1: "default off" test; for each source above a test that the flag stays off; a test that one area's flag does not open another's routes |
| TX-02 | E/T | **Kill switch not effective:** an area's write code is reachable with its flag off (an extra route, a background task, an import side effect, a shared helper called from a read route) | With the flag off, each write route is a refusing handler that never imports or calls the area's write code; write code is not wired into read routes or background tasks; `CHANGING_ROUTES` lists every changing route with its reason | Every kill-switch PR: a negative test "flag off" per endpoint (W-9 answer and nothing changed: database bytes or rows, schema, files, audit log) |
| TX-03 | T/E | **PROD lock bypass:** a write reaches a PROD database through any path (a write route, a migration, the migration worker, diagnostics, the vector view, export, history, a background task, a reconnect, a stale unlock) | No code path writes to PROD. A PROD profile cannot have a write or admin profile coupled to it (refused at load and on save). Write and admin profiles and their couplings exist only in the user's own profile store and couple only to user-local read profiles, never to a project profile, so the environment of a write target never comes from a project file (K-1); every write entry point re-reads the environment from the stored profile on the server, never from the request. Read paths (query, viewer, explorer, export, diagnostics, vector data, migration status and its worker, history) only ever get the read profile's connection, which stays read-only on the server side (03 section 5.1); Studio never runs migrations or `env.py` (T-08, T-16). No flag loosens the PROD lock | STU-XW-1: a test per write entry point with a PROD profile and the flag on; a test that the read paths and the migration worker never receive a write connection; a test that coupling a write profile to a PROD profile is refused; a test that a project file that defines a write or admin profile or a coupling, or names a user write profile to couple to, is refused or ignored (K-1) |
| TX-04 | S/E | **Profile confusion:** the write goes to another database than the user believes: the write profile points at another host, database or environment than its read profile; the read profile is edited (host, database, environment) while unlocked; two tabs unlock different profiles | A write or admin profile is coupled to exactly one **user-local** read profile (never a project profile, trusted or not; write profiles and couplings are never defined, imported or changed from a project folder, K-1) and must name the same target (host, port, database or file) and environment; a mismatch is refused. For SQLite the same target means the same file, opened not through a link and checked by device and inode (or the Windows file ID) at unlock and before each run (TX-16). Editing the read profile, the write profile or the coupling, including its environment, ends every unlock of it; lowering a coupled profile from `prod` cannot happen, since a PROD profile cannot be coupled. The write state shows the write profile, its role and its environment (08 section 3); the confirmation dialogs name the target | STU-XW-1: tests for a target mismatch, for an edit during an unlock (environment included), for two connections, and the K-1 project-file test of TX-03 |
| TX-05 | S/T | **Unlock flow abuse:** the write mode is unlocked without the user (a replayed or forged unlock request, an unlock that outlives its 15 minutes, an unlock carried from one connection or session to another) | Unlock only on the server, per connection and per session, by typing the profile name (D-03); the unlock is bound to the session, the connection and the write profile, expires after 15 minutes of inactivity and on disconnect, reconnect, profile edit and server restart; the expiry is checked on the server for every write, not by the page | STU-XW-1: tests for expiry, wrong name, other session, other connection, reconnect |
| TX-06 | T | **CSRF on write routes:** another web page makes the user's browser send a write (AT1) | The 1.0 access protection applies to every write route unchanged: Host and Origin allowlist, fetch metadata, session cookie `SameSite=Strict`, the CSRF header, JSON only (T-01, T-02, SQ-02, SQ-03). In addition every write carries the plan hash the user confirmed (TX-08), so a request without the preview fails | Every write route is in `tests/security/test_every_route.py`; STU-XW-3: a write without a valid plan hash is refused |
| TX-07 | I/S | **Credential theft on profile import:** an imported profile pairs a secret reference with a foreign host, so Studio sends a write or admin secret to a server the user did not intend, or downgrades the authentication | I-109 j confirmation (D-15): the user confirms each pair of host and secret reference once before the first unlock of every PostgreSQL write or admin profile, and again after any change of the pair; the confirmed digests and the audit log live in the folder of `write-areas.toml`, never in a state folder an environment variable chose (review N-1 of PR #116); a foreign host needs `sslmode` `verify-ca` or stricter; connections use libpq `require_auth` so that a cleartext password is not sent (as for project profiles since B-3), and a connection that sends a password requires `scram-sha-256` on every host, loopback and socket included, with the optional `requirepeer` (the server's OS user) checked on a Unix socket (review N-2 of PR #116; residual risk 4); a libpq host list (a `host` with a comma) is refused in every profile and never counts as loopback, so no list can pass as a local host (M-1 of the PR #117 review, TD-072); likewise a host with a leading `@` (an abstract Unix socket, where TLS does not apply), refused in every profile and on every connection path (review L-1 of PR #153); no libpq environment variable chooses the server, the authentication or TLS: Studio gives every option libpq would take from a `PG*` variable itself, or refuses the connection while a variable it cannot override is set (`studio.connection_environment`), so `requirepeer` and the loopback rules apply to the server the profile names (G2-01 of the G-S2 gate review); write and admin secrets are never exported: not in a profile export, a diagnostics report or bundle, or any support output (review N-5) | STU-XW-4: tests for the confirmation, the refused weaker `sslmode`, `require_auth` (SCRAM for every password, `requirepeer` only on a socket), the confirmations and audit log of a moved state folder neither read nor written; canary test that no write or admin secret is in a profile export or a diagnostics report |
| TX-08 | T | **Plan swap:** the statement that runs is not the one the user previewed (a changed request, a second tab, a plan built anew after the preview) | The server builds the plan, shows it and returns its hash; the confirmation sends the hash back; the server runs exactly the plan with that hash and nothing else. The hash is bound to the session, the connection and the unlock and is valid for one run only (review N-2). Studio builds no SQL of its own (plans come from Tabrion) | STU-XW-3: tests for a changed plan, a foreign hash, a reused hash after it ran, a hash from another session, another connection or an earlier unlock |
| TX-09 | T | **Drift:** the database changed between the preview and the run (another client, another tab) | The plan is bound to the schema fingerprint and, for the Data Editor, to the loaded row values (optimistic lock); the fingerprint is checked again just before the run and the run stops on a difference; a statement that should affect one row and affects another number rolls the whole changeset back | STU-XW-3: drift test (schema changed after the preview); STU-026: conflict and row-count tests |
| TX-10 | E/T | **Read-only bypass:** a write slips through a path that is meant to read, because a statement check missed it | The lesson of finding B-1: a denylist of statement words is not a boundary. Read paths are read-only **structurally**: the read profile's role and the read-only session or `mode=ro` connection (03 section 5.1), the SQLite authorizer with an allowlist, and read routes that never receive a write connection (TX-03). The statement check stays as UX only. Write paths are allowed only on write routes with an unlock, a flag and a plan | STU-XW-1: tests that a read route with a write profile coupled still uses the read connection; the 1.0 read-only tests (STU-T-010..013, -024) stay green |
| TX-11 | E | **Over-privileged admin profile:** the admin profile has more rights than its area needs, or Studio grants itself rights | Separate admin profile with minimal rights provisioned outside Studio; on every connect Studio checks attributes, memberships, roles reachable through `SET ROLE` or inheritance and ownership, and reports a finding on over-privilege (a predefined role counts only with a `pg_` name and an OID below 16384, as tabrion #77 D-2) (its consequence for an admin profile is fixed by STU-XDM-1: [STU-ADR-0008](../adr/0008-database-manager-backup-restore-roles.md) section 6, TX-40); for a **write** profile SUPERUSER, BYPASSRLS, REPLICATION and membership in `pg_read_server_files`, `pg_write_server_files` or `pg_execute_server_program`, held directly or through a reachable role, refuse the unlock (`405 studio.write_role_over_privileged`, owner decision 1A at G-S2), CREATEROLE and the other predefined roles stay warnings; Studio never creates or widens its own role and never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION | STU-XW-1: privilege check and finding; G-S2 decision 1A: the write refusal (`tests/security/test_write_privileges.py`, `test_write_unlock.py`, `test_write_postgres.py`); STU-XDM-1 and STU-XDM-4 for role management |
| TX-12 | R/T | **Audit-log integrity:** a write leaves no record, a record is changed or removed, or rotation can be used to replace the log from outside (A-02) | Local audit log, append only, 0600 in the private folder of `write-areas.toml` (the command line's configuration folder or the platform's; never a state folder that `TABRION_STUDIO_STATE_DIR` chose, review N-1 of PR #116), opened without following links; one record per action before and after it runs (time, profile, environment, action, objects, row counts, plan hash, result); rotation only by Studio, to names an outsider cannot plant (as for the logs since A-02); a write whose record before the run cannot be written does not run; if the record after the run fails, the write has happened, so Studio keeps a pending marker and shows a warning, never a silent loss (review N-4) | STU-XW-2: tests for append only, permissions, links, rotation, refused write on an unwritable log, pending marker and warning when the record after the run fails; G-S2 3A: the chain's end also in the OS keyring, a log that no longer reaches it recorded as `audit.reset` and shown (`tests/security/test_write_audit_anchor.py`) |
| TX-13 | I | **Values or secrets in the audit log:** the record of a change carries the changed values or a secret | The audit log has no values: no cell values, no parameters, no statement text with literals, no secrets; object names and counts only | STU-XW-2: canary test with a marked value and a marked secret |
| TX-14 | I | **Values echoed by a refusal:** a refused write (W-9) or a failed write repeats cell values, parameters, statements or secrets in its answer, a log record or undo or audit data | Every refusal is a catalog problem (`problems.problem`) with fixed texts; database errors become `db.<kind>` with the SQLSTATE only, the server's message and `DETAIL` (which can contain values) go to the log only through the redaction filters; the changeset itself is never logged; write-mode statements and changesets never reach the persisted query history or the draft-save queue (they hold cell values; review N-3); the page may keep old values for the conflict dialog and undo in memory only, never on the server or in the audit log | Every kill-switch PR and STU-026: canary tests that a marked value and a marked secret appear in no answer, log or audit record, for a refused and for a failed write; STU-026: a canary test that a changeset value reaches neither the query history nor the saved drafts |
| TX-15 | T | **Wrong area enabled by a gate mix-up:** an area runs on another area's gate, or G-CX is read as an activation | One flag per area (STU-ADR-0006); G-CX enables nothing; each gate review checks the lock of its area | Gate reviews G-S2, G-S3, G-S4 |
| TX-16 | T/E | **Hostile database objects on the write path:** functions, operators, triggers or rules the database defines run with the write role and change which rows the optimistic lock and the row count see, or escape the write connection's limits; a SQLite file is swapped between preview and run (TOCTOU) | PostgreSQL: write and admin statements under a fixed catalog-first `search_path` (as `pin_catalog_path` for catalog reads, STU-022 H-1) with schema-qualified names; triggers and rules on the target are part of the residual risk "the role is the boundary" and named in the preview where Tabrion reports them; the plan fingerprint (STU-XW-3) covers the target's triggers, rules and policies and the definitions of the functions they and its defaults and constraints call, checked again inside the run's transaction after a `ROW EXCLUSIVE` lock (the window after that check is residual risk 2). SQLite write connection: `trusted_schema = OFF`, Studio's authorizer (no `ATTACH`, no `load_extension`, PRAGMA writes only from an allowlist), `SQLITE_DBCONFIG_DEFENSIVE`. SQLite target identity: same file as the read profile, not through a link, device and inode (or Windows file ID) checked at unlock and before each run (condition K-2 of the PR #109 review) | STU-XW-1: write connection settings (PG `search_path`, SQLite `trusted_schema`, authorizer, defensive mode), identity check at unlock; STU-XW-3: identity check before each run, triggers and rules in the preview; STU-026: schema-qualified changeset statements, tests with a shadowing operator or function in a user schema, a hostile trigger, and a file swapped between preview and run |
| TX-17 | T/I | **Values that do not fit, or that change meaning on the way:** a changeset carries a value its column cannot take (wrong type, too long, out of range, a label an enum does not have, NULL in a NOT NULL column, a value for a generated column), because the page's checks were skipped or are wrong; a value is stored as something the user did not mean: NULL as the empty text or the other way round, a JSON `null` as SQL `NULL`, a `timestamp` written with an offset (silently dropped) or a `timestamptz` without one (read in the session's time zone); a refusal names the value | The page checks every changed value for its column (STU-XDE-1, `cellValues.ts`) and holds the preview until no issue is left, but **the server is the authority**: when the plan is made, `DataEditorExecutor.check` checks every new value (an update's `new`, an insert's `values`) against the column types read from the catalog, and an insert that leaves out a NOT NULL column without a default, before a plan exists; a refusal is `400 studio.data_editor_value_invalid` (W-9) whose issues are the change's and the column's positions and a `value.*` code, never a value or a name; the column types are covered by the plan fingerprint (drift). The checks are strict where a silent conversion would change the meaning: `timestamptz` and `timetz` need an offset, `timestamp` and `time` refuse one, a top-level JSON `null` is refused on the page (NULL is a button), NULL and the empty text are separate in every editor and in the change overview; an insert names exactly the columns the user set, never a key SQLAlchemy fetched ahead. The database stays the last instance (CHECK, UNIQUE, foreign keys, domain constraints, triggers); a step it refuses for a constraint rolls back the whole changeset as `400 studio.data_editor_constraint` (G-S2 6A) whose one issue is the change's position, the column's position only where tabrion's classification names a single column of the target (never from the database's message), and a `constraint.*` code, never a value or a name; any other database refusal, and a constraint checked at commit, stays a `db.<kind>` problem as in TX-14. A bytes value is only replaced by a picked file of at most 64 KiB (`value.too_large`, checked on the page before the file is read and by the server); the plan route's body cap is the plan framework's 256 KiB. The page and the server run the same check vectors | STU-XDE-1: `test_data_editor_values.py` and `cellValues.test.ts` on the shared vectors; refusals with positions only and no echo of a marked value on SQLite (`STRICT`) and PostgreSQL (enum, domain, varchar, int2, arrays, identity `ALWAYS`, generated, `timestamp`, `timetz`); the type editors' values written and read back as old values without a false conflict; the page and Chromium e2e: editors per type, issues at the cell, the server's refusal placed at its cell; G-S2 6A: every constraint kind on SQLite and PostgreSQL placed at its change or cell with the changeset rolled back and no echo, the bytes limit at its edge, a bytes value in no answer, log or audit record, and the real-server e2e for a replaced BLOB, a UNIQUE refusal at its cell and an edit beyond the first page |

## Residual risks

1. **The environment is the user's label.** Studio cannot know that a database is production; a PROD database saved
   as DEV is not protected by the PROD lock. Mitigation: the coupled write profile has its own role and secret, so
   the user decides separately to give Studio write credentials, write profiles exist only in the user's own store
   (K-1), and the page says not to give Studio write credentials for a production database at the moment a write
   profile is created, not only in the guide (review N-6).
2. **The database role is the boundary.** A write role can do what its rights allow, inside or outside Studio's
   plan, and triggers and rules on a target table run with it (TX-16); the previews and the statement classes are UX. Mitigation: minimal write and admin roles, the privilege
   check of TX-11, which refuses a write role with superuser, BYPASSRLS, REPLICATION or server-file rights
   (decision 1A at G-S2); a write role with CREATEROLE or another predefined role such as `pg_write_all_data` is
   accepted with a warning and stays inside this residual risk.
   - **Plan drift on PostgreSQL (STU-XW-3, review M-1 of PR #115).** The fingerprint covers the target tables'
     catalog and the definitions of the functions their triggers call and their defaults, constraints, policies,
     rules and index expressions depend on. It is compared inside the run's transaction twice, the second time
     after `LOCK TABLE ... IN ROW EXCLUSIVE MODE`, which blocks DDL, trigger and rule changes on the targets. Replacing
     a function (`CREATE OR REPLACE FUNCTION`) takes no lock on the table, so a replacement that commits after the
     second comparison and before the steps run is not seen; the window is a few statements long. Functions called
     only from inside another function's body, and objects outside the target tables (another table a rule or
     trigger writes to), are not fingerprinted. Both are part of this residual risk: the role is the boundary.
3. **Malware running as the user** can read the keyring, Studio's files and the flag source, as in 1.0; that is
   outside the protection goal (03 section 7.3). G-S2 decision 3A narrows the audit part of it: the last
   `(seq, hash)` of the chain is also kept in the OS keyring, so removing the `audit` folder, cutting the log or
   rebuilding a consistent chain is reported (an `audit.reset` record and a notice on the page) unless the keyring
   entry is changed as well; without a usable keyring the old behavior stays and the page says so
   (`tests/security/test_write_audit_anchor.py`).
4. **A local port taken over before the server (review N-2 of PR #116).** A write or admin password is sent only
   through SCRAM (`require_auth=scram-sha-256`), on loopback and over a socket as well, so a process that holds the
   server's local port or socket cannot make libpq skip authentication or use a weaker method. On a Unix socket the
   write profile can name the server's operating system user (`requirepeer`), which libpq checks before any
   exchange. Over loopback TCP without TLS there is no such check: a process of another local user that took the
   port first receives a SCRAM exchange, which reveals no password but allows an offline guess at a weak one.
   Mitigation: a long random password for write roles, the socket with `requirepeer`, or TLS with `verify-full` also
   on this computer. At G-S2 (2026-11-15, decision 2A) the owner accepted this risk for local DEV, TEST and STAGE
   use; whether loopback TCP must use TLS with `verify-full` (or a socket with `requirepeer`) is checked again
   before G-R (docs/TODO.md). Prepared for that recheck (G-S2 2A follow-up): `loopback_tls` in `write-areas.toml`,
   `"accept"` by default (the G-S2 decision, unchanged); `"require"` refuses a write or admin password to a server on
   this computer unless TLS `verify-full` or a socket with `requirepeer` lets libpq check who answers
   (`studio.write_loopback_tls_required`, before a referenced secret is read and before any password is sent). With
   `"accept"` the D-15 dialog and the unlock state (`local_unverified`) say that the server is not checked, and the
   unlock is logged (`studio.write_loopback_unverified`). Whether the default becomes `"require"` is the owner's
   decision at G-R. A host that starts with `@` (on Linux a Unix socket in the abstract namespace: no file, no
   permissions, any local process can bind it, and libpq applies no TLS there) would pass neither rule, so it is
   refused in every profile (`config.host_abstract_socket`) and again on every connection path (the secret pair,
   both transport checks under either setting, the connection URL, the `pg_dump`/`pg_restore` connection, the
   profile export), independent of what the driver would do with it (review L-1 of PR #153).
5. **The value checks are about form, not about every rule (TX-17).** Studio checks a value's type, length,
   range, NULL and an enum's labels; a CHECK constraint, a domain's own constraints, uniqueness, foreign keys and
   triggers are left to the database, which refuses such a value inside the run's transaction (nothing is saved;
   since G-S2 6A the page marks the refused change at its cell or row).
   A label added to an enum after the preview is not drift: it can only make the database take a value Studio
   refused. On SQLite only `STRICT` tables have column types (as SQLite reports them in `pragma_table_list`, not
   read from the SQL text); an ordinary table's declared types are advice, so its
   values are checked for NULL only and stored as SQLite's affinity rules decide.

## Review of PR #109: conditions and notes

The security review of PR #109 (`pr109-stuxw5-review.md`, kept with the planning documents) accepted this model
with two conditions and six notes. Where each one lands:

| Item | Requirement | Where | Work package |
|---|---|---|---|
| K-1 | Write and admin profiles and couplings only in the user's own store, coupled only to user-local read profiles; project files cannot define or change them | TX-03, TX-04; STU-ADR-0006 (PROD, profiles, proof item 5) | STU-XW-1 |
| K-2 | Fixed PG `search_path` and qualified names; SQLite `trusted_schema = OFF`, authorizer, defensive mode; SQLite file identity at unlock and before each run | TX-16; STU-ADR-0006 (hostile database objects) | STU-XW-1, STU-XW-3, STU-026 |
| N-1 | Prefer a file as the flag source; an environment variable only with no `.env` reading; a CLI argument is local | TX-01; STU-ADR-0006 (flags, point 2) | STU-XW-1 |
| N-2 | Plan hash bound to session, connection and unlock, one-time | TX-08 | STU-XW-3 |
| N-3 | No write statements or changesets in the query history or the draft-save queue | TX-14 | STU-026 (and STU-XW-1 for the write mode) |
| N-4 | Failed audit record after the run: pending marker and warning | TX-12 | STU-XW-2 |
| N-5 | Write and admin secrets in no export, diagnostics report or support output | TX-07 | STU-XW-4 (exports), STU-XW-1 (diagnostics) |
| N-6 | The "no production write credentials" text shown when a write profile is created | Residual risk 1; STU-ADR-0006 (profiles) | STU-XW-1 |

## Review of PR #116: notes

The review of STU-XW-4 (`pr116-stuxw4-review.md`, kept with the planning documents) accepted it with four notes.

| Item | Requirement | Where |
|---|---|---|
| N-1 | The secret confirmations and the audit log follow the trust rule of `write-areas.toml` | TX-07, TX-12 (folder of `write-areas.toml`; a moved state folder is neither read nor written, `studio.write_state_ignored`) |
| N-2 | SCRAM on loopback too; `requirepeer` for Unix sockets | TX-07; residual risk 4 |
| N-3 | A confirmation lost to concurrent confirmations fails safe | Unchanged: the user confirms again |
| N-4 | The guide says that every PostgreSQL write profile needs the pair confirmation and that a changed pair needs a new one | [How Studio protects your data](../guide/security.md) |

## Acceptance (planning document 15 section 9.4)

| Criterion | Where |
|---|---|
| Flag per area off by default and not switchable from the frontend; "flag off" negative test per endpoint (400/405, 403 only for the write mode); no code path for PROD | TX-01, TX-02, TX-03; STU-ADR-0006 |
| Successor ADR to STU-ADR-0003: one flag and one gate per area, G-CX is not an activation, W-9 | [STU-ADR-0006](../adr/0006-per-area-write-kill-switches.md) |
| Separate write and admin profiles; admin profile with minimal rights, privilege check on connect, finding on over-privilege | TX-04, TX-11 (STU-XW-1) |
| Plan binding (plan hash and fingerprint), drift check just before the run with abort, preview and explicit confirmation; tests for plan swap and drift | TX-08, TX-09 (STU-XW-3) |
| Non-goals of 03 section 1 in the repository | README, [How Studio protects your data](../guide/security.md) |
| Data Editor values checked before the plan, the server as the authority, refusals 400 without values (STU-XDE-1) | TX-14, TX-17; [STU-ADR-0006](../adr/0006-per-area-write-kill-switches.md), "Values of the Data Editor" |
