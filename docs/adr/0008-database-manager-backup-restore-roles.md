# STU-ADR-0008: Database Manager: verified backup tools, restore and delete with a backup first, roles only through a minimal admin profile

**Status:** proposed (work package STU-XDM-1, change request CR-DBX, Studio 1.3). It records owner decisions that
are already binding (E-X5, E-X6, E-X7, E-X8, E-X9 with their refinements, W-9, W-10) and the conditions tabrion's
reviews set for Studio (tabrion contract I-170, I-171, I-180..I-186, I-190..I-193; the "Open for Studio before
G-S4" list of tabrion TODO TB-149), fixes what an over-privileged admin profile leads to (planning document 15
section 3.1 item 3, left to STU-XDM-1), and adds the rules that make them testable. It needs the independent review
of STU-XDM-1 and stays "proposed" until that review accepts it. **G-S4 is not granted and this record grants
nothing:** the `database_manager` flag stays off by default, no code of the area exists yet, and no default changes.
It builds on [STU-ADR-0006](0006-per-area-write-kill-switches.md), which applies unchanged. The threat model is
[Database Manager threat model](../architecture/database-manager-threat-model.md) (TX-32..TX-46); the gate is
defined in [G-S4 definition](../architecture/security-review-prep.md#g-s4-definition-database-manager).

## Context

The Database Manager (planning document 15 section 4; STU-XDM-2..STU-XDM-7) backs up, restores and deletes
databases, manages PostgreSQL roles and handles profile pairs. Planning document 03 section 1 had backup execution
and role management as non-goals; E-X5 and E-X6 allow them, each with binding limits. Tabrion provides the SQLite
side (`backup`, `restore`, `delete_database_file`; TAB-XW-7), PostgreSQL `create_database`, `drop_database` and
`database_sessions` (TAB-XW-9) and the role DDL with the attribute ban (TAB-XW-8), all in the pinned commit as
experimental (`X`) names. Tabrion has no `pg_dump` wrapper (tabrion ADR 0015 section 6): running the PostgreSQL
tools is Studio's.

## Decision

### 1. Area, flag and gate

- Every route of the Database Manager that changes a database, a role, a backup or a profile pair, or runs a tool,
  is behind the `database_manager` key of `write-areas.toml` (STU-ADR-0006, "As built") and the gate **G-S4**. With
  the flag off such a route answers 405 `studio.method_not_allowed` (W-9), does not import the area's modules or
  Tabrion's role, restore or delete functions, starts no process and changes nothing.
- Creating a database belongs to the Schema Designer (STU-CR-2, STU-CR-3, G-S3); its rules for names, owners and
  driver errors are the same as here (section 5).

### 2. PROD (E-X7)

On a profile whose stored environment is `prod` the only action is **creating a backup**, which reads and runs with
the read profile's role (a PROD profile has no coupled write or admin profile). Restore to PROD, deleting a PROD
database or file and role changes on PROD have no code path. In addition Studio refuses to restore into, delete or
change roles on a database that **any** profile in the user's store labels `prod` (same host, port and database, or
the same SQLite file by device and inode), whichever profile the request came through.

### 3. PostgreSQL backup and restore only through verified tools (E-X5, R-X5)

- **Which tool:** `pg_dump` and `pg_restore` are taken only from absolute paths the owner sets in the local
  configuration folder of `write-areas.toml` (with that file's checks: user-owned, 0600, no link, a folder only the
  user may write). Never from `PATH`, the page, a settings save, a project file, an environment variable or a
  profile. STU-XDM-2 fixes the key names. Planning document 15 says "from the settings"; this record reads it as the
  owner's local configuration, because a tool path set from the page would let whoever controls the page choose a
  program to run.
- **Checks before every run:** the path is absolute, a regular file and not a link, not writable by group or others,
  owned by the user or root, in a folder with the same rules; `--version` is run (argument list, no shell, the
  environment below) and must name the expected program; the client major version must be at least the server's
  (for `pg_restore` also at least the archive's). The tool's file name, version and SHA-256 are recorded in the
  backup metadata. The libpq of the tool must support `require_auth` (16 or later) when the connection sends a
  password, as for every write and admin connection (STU-XW-4).
- **Process:** an argument list without a shell (`subprocess` with `shell=False`), `--no-password`, a timeout, the
  output of `pg_dump -Fc` written to a file Studio opened itself (exclusive, 0600, no link) in its backup folder, and
  `stdin` closed. The connection is given as one `--dbname` connection string built from the stored profile: host,
  port, database, user, `sslmode`, `sslrootcert`, `require_auth` and `requirepeer` as the write and admin rules ask
  (`verify-full` for a remote host; SCRAM for a password), never the password.
- **Environment:** built from nothing, not inherited: no `PG*` variable from Studio's own environment (as G2-01 of
  the G-S2 gate review for Studio's own connections), `PGPASSFILE` set, `PGSERVICEFILE` and `PGSYSCONFDIR` pointing
  at an empty private folder, `LC_ALL=C`; never `PGPASSWORD`.
- **Secret:** the password only in a temporary passfile (0600, in a 0700 private folder, one exact entry with `:`
  and `\` escaped), removed in a `finally` block; leftovers from a crashed run are removed at the next start. The
  password is never in an argument, the environment, a log, the audit log, the backup metadata or an answer.
- **Output and errors:** the tool's `stderr` can hold object names and values from server messages: it goes to the
  log only through the redaction filters, cut to a limit; the answer is a catalog problem with the exit status class
  (`studio.backup_tool_failed`), never the tool's text.
- **Archive format:** only the custom format (`-Fc`). A restore runs `pg_restore` directly against the database with
  `--single-transaction`, `--exit-on-error`, `--no-owner` and `--no-privileges`; Studio never produces a plain SQL
  script and never runs `psql`.
- **Scope:** every place that shows or stores a PostgreSQL backup (the page, the guide, the metadata) says that a
  `pg_dump` archive is **not a complete backup**: global roles, role memberships, tablespaces and other cluster
  objects are not in it.

### 4. Backups, restore and the restore tests

- **Where:** a backup folder in Studio's private data folder (0700, no link); backup files 0600 with names Studio
  makes; next to each a metadata file: time, the profile's `safe_url`, environment, engine, size, SHA-256, format,
  tool and version (PostgreSQL), server version, the level-2 fingerprint of the source, and the scope notice. No
  secret, no value. Studio never deletes a backup of its own accord.
- **Which backups can be restored:** only a backup from Studio's own backup folder whose metadata matches its file
  (SHA-256, size) and the target's engine. An archive from elsewhere is not restored in 1.3 (a custom-format archive
  holds SQL that runs with the restoring role).
- **Target:** a new database or file, or an existing one whose stored environment is `dev` or `test`. PostgreSQL in
  1.3 restores only into a **new** database that Studio creates for it (`create_database`, owner the admin profile's
  role), so no `--clean` runs; SQLite restores into a new file or, with `overwrite=True` (tabrion I-170), an existing
  non-PROD file.
- **Before:** an existing target gets its own backup first (mandatory; opt-out only on `dev` with a typed
  confirmation, audited). The user types the target's name (D-12). The target's identity (SQLite device and inode;
  PostgreSQL host, port, database name and the database's OID) is checked again right before the run.
- **After:** SQLite `integrity_check` (in tabrion's `restore`) and the level-2 fingerprint compared with the backup's
  metadata; PostgreSQL the level-2 fingerprint of the new database compared with the metadata. A difference is
  shown as a failed restore; the restored database is left for the user to inspect, never used silently.
- **Restore tests are mandatory** (E-X5): automated round trips backup, restore into a new database, integrity and
  fingerprint compare, on SQLite and on PostgreSQL in CI, before G-S4.

### 5. Deleting a database (doc 15 section 4)

- SQLite through `tabrion.sqlite.delete_database_file` (side files `-wal`, `-shm`, `-journal`; links unlinked, never
  followed; the exclusive-lock check; I-171). Studio refuses every file inside its own folders (state, configuration,
  logs, backups) by device and inode, and any file a PROD profile names.
- PostgreSQL through `tabrion.postgres.drop_database`, on an admin connection to another database of the same
  server. Tabrion protects `postgres`, the templates and the connection's own database. The preview shows the number
  of other sessions (`database_sessions`, a hint that can change).
- **`force=True`** (`WITH (FORCE)`, which ends the other sessions) is offered only when sessions were counted, on
  PostgreSQL 13 and later, as a separate, unchecked option with its own typed confirmation; it is never a default,
  never retried automatically after `postgres.database_in_use`, and audited as its own fact.
- Before every delete: a backup (mandatory; opt-out only on `dev` with a typed confirmation, audited) and the typed
  name of the database or file (D-13). The destructive action never has the default focus.

### 6. Roles (E-X6) and the admin profile

- **Only PostgreSQL 16 and later**, only through an admin profile, only through Tabrion's role DDL (`create_role`,
  `alter_role`, `drop_role`, `grant_membership`, `revoke_membership`, `grant_privileges`, `revoke_privileges`), which
  has no parameter for SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION, ADMIN OPTION or GRANT OPTION and refuses a
  change that would let a role reach one of them, a predefined role, a server-access role or a K-2 grant.
- **The admin rights are provided outside Studio.** Studio never creates, alters or grants to its own admin role
  (Tabrion refuses the session's own role) and never sets up rights for itself.
- **What an over-privileged admin profile leads to (fixed here; planning document 15 section 3.1 item 3).** The
  privilege check runs on every connect of an admin profile (STU-XW-1). For `kind = "admin"`:
  - **Refuse the unlock** (`studio.admin_over_privileged`, answered as the other unlock refusals of STU-XW-1 under
    W-9; no secret beyond the check's own connection is used) when the role or a role it reaches has SUPERUSER,
    BYPASSRLS or REPLICATION, reaches a server-access role (`server_access:*`), or has CREATEROLE before PostgreSQL
    16 (`createrole_pre16`). Tabrion refuses such a session for every role change anyway (`postgres.caller_unsafe`,
    I-182); Studio refuses earlier and for every action of the area, backups and deletes included.
  - **Show, do not refuse:** CREATEROLE on PostgreSQL 16 and later (the attribute role management needs; shown as
    "needed only for role management"), CREATEDB, memberships in other predefined roles (`broad:*`), the K-2 grants
    of the role, `owned_objects`.
  - The decision for `kind = "write"` stays the open G-S2 decision (I-3 of the PR #111 review).
- **Checks around every change (E-X6):** Tabrion checks before and after the statement in the change's transaction
  (I-182). Studio in addition:
  - shows `role_privileges` of the target before the change (attributes, memberships, reachable roles, ownership,
    default privileges, findings) in the preview (D-14);
  - **reads `role_privileges` again after the commit** and shows any difference to the result Tabrion returned
    (P90-06: a membership committed by another session in between);
  - before a membership change checks the K-2 grants in **every database** the admin profile can connect to, or, when
    it cannot connect to one, says in the preview that the check covers only the databases listed (P90-04);
  - warns in the preview about `public_definers` (a `superuser_owner` entry first) and, for CREATE on a database,
    that the grantee can install trusted extensions whose scripts run with raised rights (P90-03, P90-07).
- **Names:** every display of a role or database name (page, preview, confirmation, audit log, log) escapes control,
  format, line and paragraph separator characters (Unicode Cc, Cf, Zl, Zp, which include bidirectional overrides)
  as visible code points (P90-05). A destructive action on an object whose name holds such a character is refused
  in Studio (it cannot be confirmed by typing what the user sees); the guide says to rename it outside Studio.
- **Passwords of roles Studio creates** go only through Tabrion (a client-side SCRAM verifier, never the clear text
  on the server), straight from the request into the call and, for a runtime profile, into the keyring; never in a
  log, the audit log, an answer or an error.
- **Connection rules of an admin profile** are at least those of a write profile: `require_auth` SCRAM for every
  password, `verify-full` (not only `verify-ca`) for a host that is not this computer, the I-109 j confirmation of
  the pair of server and secret reference (P90-07).

### 7. Errors

`create_database` and `drop_database` let driver errors through unchanged (tabrion I-191 b, I-192 d); the role DDL
raises `DatabaseError` with the safe message. Studio turns every one into a `db.<kind>` problem from
`tabrion.errors.classify` and `safe_message` only, and Tabrion's `UsageError`, `PermissionDenied`, `RoleUnsafe`,
`RoleMissing`, `DatabaseInUse` and `UnsafePath` into fixed catalog problems (`studio.dbm_*`) with their codes and
reasons; never `str()` of an exception, the server's message or a path from a Tabrion error.

### 8. Profile pairs, import and export (STU-XDM-5)

Write and admin profiles and their couplings stay in the user's own profile store (K-1). An import calls the I-109 j
confirmation for every imported pair before any secret is read. Import and export take single-host profiles only
(W-10, TB-148): a multi-host profile is refused, never cut short. Write and admin secrets are never exported (N-5).

### 9. Audit

Every action writes `run.start` and `run.result` (STU-XW-2): backup (`dbm.backup`, file name and SHA-256, tool
version), restore (`dbm.restore`, backup and target), delete (`dbm.delete`, with `force` as its own field), each role
change (`dbm.role.*`, role names escaped, privileges and object names, never a password) and an opted-out backup.
A record before the run that cannot be written stops the action.

## Consequences

- Nothing of the Database Manager is implemented by this record; STU-XDM-2..STU-XDM-7 follow it, each behind the
  `database_manager` flag with the kill-switch proof of STU-ADR-0006.
- CI needs `pg_dump` and `pg_restore` of the server's major version or newer for the restore tests (the
  `postgres` job already has `pg_dump`).
- Studio uses Tabrion's experimental (`X`) role, restore, delete and database names; a release of Studio 1.3 needs
  them stable or an accepted exception.
- The planning non-goal "no backup execution, no role management" of document 03 section 1 stays for 1.0 to 1.2.

## Tests

Before G-S4 (the full list with test names is the [G-S4 definition](../architecture/security-review-prep.md#g-s4-definition-database-manager)):
the kill-switch proof per route; PROD: backup only, every other action refused, also through a non-PROD profile of
a PROD target; tool path from outside the owner's configuration, a link, a writable tool, an old client version and
`PATH` all refused; canaries for the password in arguments, environment, logs, metadata and leftover passfiles;
a planted `PGHOST`/`PGSERVICE` that does not redirect the tool; restore round trips on SQLite and PostgreSQL;
restore into PROD and into a target changed between preview and run refused; a foreign or edited archive refused;
delete with backup, typed name, `force` only explicit, Studio's own files refused; the admin-profile refusals; every
role escalation path refused (direct, membership, ADMIN option, predefined, server access, K-2); the re-read after
commit; escaped names; driver errors without server text.
