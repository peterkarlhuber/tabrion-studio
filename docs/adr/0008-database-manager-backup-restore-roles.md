# STU-ADR-0008: Database Manager: verified backup tools, restore and delete with a backup first, roles only through a minimal admin profile

**Status:** proposed (work package STU-XDM-1, change request CR-DBX, Studio 1.3). It records owner decisions that
are already binding (E-X5, E-X6, E-X7, E-X8, E-X9 with their refinements, W-9, W-10) and the conditions tabrion's
reviews set for Studio (tabrion contract I-170, I-171, I-180..I-186, I-190..I-193; the "Open for Studio before
G-S4" list of tabrion TODO TB-149), fixes what an over-privileged admin profile leads to (planning document 15
section 3.1 item 3, left to STU-XDM-1), and adds the rules that make them testable. It needs the independent review
of STU-XDM-1. **That review accepted it with conditions** (2026-11-15, `reviews/stu-xdm1-review.md` in the
planning documents: M-1, L-1..L-3, A-1 and the notes I-1..I-4); STU-XDM-2 folds the conditions into this record, the
threat model and the G-S4 definition, and the record goes to "accepted" when that change is merged. **G-S4 is not
granted and this record grants nothing:** the `database_manager` flag stays off by default and no default changes.
STU-XDM-2 builds backup and restore behind the flag (see "As built (STU-XDM-2)" below).
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
- Creating a database (STU-CR-2, STU-CR-3) belongs to this area since the owner's grant of 2026-11-18 (section 10);
  before, it was the Schema Designer's (STU-ADR-0007 section 1, amended).

### 2. PROD (E-X7)

On a profile whose stored environment is `prod` the only action is **creating a backup**, which reads and runs with
the read profile's role (a PROD profile has no coupled write or admin profile). Restore to PROD, deleting a PROD
database or file and role changes on PROD have no code path. In addition Studio refuses to restore into, delete or
change roles on a database that **any** profile in the user's store labels `prod`, whichever profile the request
came through. The server is compared by an identity it reports itself where Studio can read it (review L-1): the
`system_identifier` of `pg_control_system()` read on an open connection of the PROD profile and on the target. Where
that is not available, normalized addresses decide: the same port and an overlap of the resolved addresses (a
Unix socket directory, `localhost` and every loopback address count as this computer), so `localhost`,
`127.0.0.1`, another DNS name of the same address and a socket path are one server. A SQLite file is compared by
device and inode. What stays is a residual risk (threat model): a PROD server reached under an address Studio cannot
relate (a port forward, a proxy) and whose identity Studio cannot read.

A backup of a PROD database runs with the read profile's role; such a backup follows the same connection rules as a
write or admin profile (review L-2 b: `require_auth` SCRAM for a password, `verify-full` for a host that is not this
computer, nothing weakened), or it is refused. STU-XDM-2 does not build it yet: every Database Manager action needs
an admin unlock, which a PROD connection never gets, so a PROD backup is refused (fail closed; decided at G-S4 as
the safest option, section 12: no Database Manager action on PROD in 1.3).

### 3. PostgreSQL backup and restore only through verified tools (E-X5, R-X5)

- **Which tool:** `pg_dump` and `pg_restore` are taken only from absolute paths the owner sets in the local
  configuration folder of `write-areas.toml` (with that file's checks: user-owned, 0600, no link, a folder only the
  user may write). Never from `PATH`, the page, a settings save, a project file, an environment variable or a
  profile. The file is `database-tools.toml` next to `write-areas.toml`: `version = 1` and a table `[postgresql]`
  with the keys `pg_dump` and `pg_restore` (absolute paths); any other key makes the file invalid (STU-XDM-2). Planning document 15 says "from the settings"; this record reads it as the
  owner's local configuration, because a tool path set from the page would let whoever controls the page choose a
  program to run.
- **Checks before every run:** the path is absolute, a regular file and not a link, not writable by group or others,
  owned by the user or root, in a folder with the same rules, and every folder above that one of root or the user
  and not writable by group or others unless sticky (review L-1 of PR #136; the tool is still started by its path,
  so a swap by a process of the same user stays residual risk 2 of the threat model); `--version` is run (argument list, no shell, the
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
- **Output and errors:** the tool's `stderr` can hold object names and values from server messages. STU-XDM-2 is
  stricter than "through the redaction filters": `stderr` is read only to classify the failure (authentication,
  connection, permission, version, other) and is never logged; the answer is a catalog problem with that class as an
  issue (`studio.backup_tool_failed`, `tool.<class>`), never the tool's text.
- **Incomplete output (review L-3):** `pg_dump` writes to a temporary file Studio created exclusively (0600) in the
  backup folder; on a timeout the tool's whole process group is ended. Only after exit 0, a readable archive header
  and a successful `pg_restore --list` of the new file (with a `pg_restore` of at least the archive's version) is
  the file renamed to its final name without overwriting and its metadata written. Otherwise the temporary file is
  removed and the action that depends on the backup (a restore, a delete, a Schema Designer run) is not done. The
  same holds for the backup of a target before a restore or a delete.
- **Row security (review L-2 c):** Studio never passes `--enable-row-security`. A role without BYPASSRLS then fails
  on a table with row security instead of writing an archive that silently lacks rows; the guide says so.
- **Who may use the runner (review L-2 a):** the runner (`tabrion_studio.write.pgtools`) is not part of the
  Database Manager's package, so the Schema Designer may import it under its own `schema_designer` flag for the
  backup before a destructive PostgreSQL run (STU-ADR-0007 section 7). GS4-04..GS4-07 and GS4-22 are then also
  conditions of G-S3. STU-XDM-2 did not change the Schema Designer; the STU-CR-5 follow-up wires the runner into its backup
  before a destructive PostgreSQL run (STU-ADR-0007 section 7), so `backup_unavailable` remains only where the tools
  are not configured or the system is Windows.
- **Platforms:** STU-XDM-2 runs the tools on Linux and macOS only; on Windows the checks of owner and mode do not
  apply as written, so the tools are refused (`tool.platform`; an open point).
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
  secret, no value. Studio never deletes a backup of its own accord. STU-XDM-2 uses the layout of the Schema
  Designer's backups (STU-CR-5): `backups/database-manager` next to `write-areas.toml`, beside
  `backups/schema-designer`. The page lists the backups with their count and total size and says that Studio never
  deletes one (review I-3: the same rule as STU-CR-5; retention decided at G-S4, section 12).
- **Which backups can be restored:** only a backup from Studio's own backup folder whose metadata matches its file
  (SHA-256, size) and the target's engine. An archive from elsewhere is not restored in 1.3 (a custom-format archive
  holds SQL that runs with the restoring role).
- **Target:** a new database or file, or an existing one whose stored environment is `dev` or `test`. PostgreSQL in
  1.3 restores only into a **new** database that Studio creates for it (`create_database` on the admin connection,
  template `template0`, owner the restore role below), so no `--clean` runs; SQLite restores into a new file next to
  the connection's file or, with `overwrite=True` (tabrion I-170), the connection's own non-PROD file.
- **The restore role (review M-1).** An archive holds functions, and CHECK constraints, domain checks, index
  expressions and the like run them while the data is loaded, as the role that restores. So a PostgreSQL restore
  never runs with the admin role's rights:
  - it runs as a **separate restore profile**: another admin profile of the same read profile with its own login
    role, provided outside Studio like the admin rights (E-X6). The restore connects as the restore role itself
    (its own login, not a role switch inside the admin's session);
  - right before the restore Studio reads that role's privileges on its own checked connection with the logic of
    decision 1A and refuses (`studio.dbm_restore_role_unsafe`) when it has or reaches SUPERUSER, BYPASSRLS,
    REPLICATION, CREATEROLE (also on 16 and later), CREATEDB or any predefined role;
  - the new database belongs to the restore role (the admin role needs to be able to act as it, set up outside
    Studio); the archive's objects are created without owners or grants (`--no-owner`, `--no-privileges`);
  - without such a profile a PostgreSQL restore is refused (fail closed). The preview shows the number of extensions
    the archive creates (review I-2; with a lean restore role a trusted extension's script is limited to that
    role's database).
- **Before:** an existing target gets its own backup first (mandatory; opt-out only on `dev` with a typed
  confirmation, audited; STU-XDM-2 offers no opt-out). The user types the target's name (D-12). The target's identity
  (SQLite device and inode; PostgreSQL host, port, database name and the database's OID) is checked again right before
  the run. Before a SQLite file is replaced, Studio closes its own open connections to that file (read pools and the
  unlock) and checks the file's identity again afterwards (review I-4).
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
  - For `kind = "write"` the owner decided at G-S2 (decision 1A, PR #127): `studio.write_role_over_privileged`,
    GS3-28. Since section 10 a write role that has or reaches CREATEDB is refused the same way (`admin.createdb`,
    `dangerous`), at the unlock and again at the start of every run: no write area creates databases.
  - **Decided at G-S4** (owner grant of 2026-11-18: over-privileged admin profiles are refused; section 12): the
    admin unlock itself is refused with 405 `studio.admin_over_privileged` and its connection closed (STU-XDM-7,
    review B-1 of the G-S4 gate review). Every Database Manager action still reads the check again on its own
    session and refuses these findings, which ends the unlock (STU-XDM-2).
    STU-XDM-4 applies the same check to every role request and once more on the changing session.
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
As built in STU-XDM-5: see [As built (STU-XDM-5)](#as-built-stu-xdm-5).

### 9. Audit

Every action writes `run.start` and `run.result` (STU-XW-2): backup (`dbm.backup`, file name and SHA-256, tool
version), restore (`dbm.restore`, backup and target), delete (`dbm.delete`, with `force` as its own field), each role
change (`dbm.role.*`, role names escaped, privileges and object names, never a password) and an opted-out backup.
A record before the run that cannot be written stops the action.

### 10. Creating a database (STU-CR-2, STU-CR-3)

Owner grant of 2026-11-18 (G-S3 and G-S4, conditional): "database creation and role management belong to the admin
area G-S4 with separate permissions". It amends STU-ADR-0007 section 1.

- Both wizards are routes of this area: `POST /api/write/database-manager/create/sqlite` and
  `.../create/postgresql`, behind the `database_manager` flag (405 `studio.method_not_allowed` and no import of
  `tabrion_studio.write.database_manager.create` with the flag off, also when the `schema_designer` or
  `data_editor` flag is on). The flag stays off by default.
- **PostgreSQL** needs the connection's **admin** unlock (`kind = "admin"`); a write unlock is refused with 405
  `studio.method_not_allowed` (the write kind does not serve this area). The admin check of section 6 runs again on
  the run's own session before `CREATE DATABASE` and refuses its findings with `studio.admin_over_privileged`,
  which ends the unlock. The admin role needs CREATEDB on DEV and TEST (a fact there, a warning elsewhere).
- **SQLite** needs the flag but no admin unlock: a new file has no role, no server and no connection to unlock, so
  there is no admin profile to check. This is the safest practical option, not an exception: every other safeguard
  of STU-CR-2 stays (the folder rules, the reserved folders, `0600`, never a link or an overwrite, the typed file
  name, the Windows refusal `studio.schema_create_platform`, the audit record that fails closed).
- **Write roles never need CREATEDB.** A `kind = "write"` role that has or reaches CREATEDB is refused at the unlock
  and at each run (`studio.write_role_over_privileged`), consistent with section 6: an admin role is refused only for
  what no admin action needs, and CREATEDB is what creation needs, so it stays a fact for the admin kind.
- Kept unchanged: the name, owner, template, encoding and locale rules, PROD refused (also through a non-PROD
  profile), driver errors only as `db.<kind>` with the safe message, the audit (`run.start` fails closed, then
  `run.result`), now with the actions `dbm.create_sqlite` and `dbm.create_postgresql` and the area
  `database_manager`.

### 11. Owner points of STU-XDM-3: decided per G-S3/G-S4 grant 2026-11-18, safest option

The owner's grant asks to close the open architecture decisions by the safest practical option. The eight points
recorded for STU-XDM-3 (PR #140) were each already built as the safest practical option and are **decided per
G-S3/G-S4 grant 2026-11-18, safest option**:

1. No opt-out of the backup before a delete, also on DEV (section 5 would allow one).
2. `force` is confirmed by typing the name a second time, as its own confirmation.
3. On SQLite the delete target is only the connection's own file; Studio closes its own connections and ends the
   unlock to delete it.
4. The PostgreSQL backup before a delete runs as the admin role; a role that cannot read the whole target database
   makes the delete refused, never a broader role.
5. SQLite delete is refused on Windows, as STU-CR-2 refuses create.
6. Backups made before a delete stay in the backup folder, are listed and restorable, and Studio never deletes them.
7. The residual window is accepted: writes between the backup and the drop are lost; Studio re-checks the OID (or
   the file's device and inode), not the content.
8. A SQLite restore into a new file is refused on Windows (`restore.platform`) until a reviewed check of the
   folder's ACL exists (the grant's Windows condition).

### 12. Owner points of G-S4: decided per G-S3/G-S4 grant 2026-11-18, safest option

The open G-S4 decisions are closed by the safest practical option, as the grant asks; the independent G-S4 gate
review (STU-XDM-7, `reviews/gs4-gate-review.md`) found none of them a new material risk:

1. **Over-privileged admin profile (section 6):** SUPERUSER, BYPASSRLS, REPLICATION, a server-access role or
   CREATEROLE before PostgreSQL 16, on the role or a role it reaches, refuse the admin unlock
   (`studio.admin_over_privileged`) and every action; the other findings are shown.
2. **Tool paths** (`pg_dump`, `pg_restore`) only from the owner's local `database-tools.toml`, never from the page's
   settings (E-X5 says "from the settings"; a path set from the page would let whoever controls the page choose the
   program).
3. **PostgreSQL restore only into a new database** in 1.3, as the lean restore role (review M-1 of STU-XDM-1).
4. **Retention:** Studio never deletes a backup of its own accord; the page shows their count and total size. A
   backup is a full copy of the data, as sensitive as the database: the user removes old backups by hand (guide).
5. **No release exception** for Tabrion's names: every name Studio uses is stable (`S`) since tabrion #94 and the
   0.3.0 release commit Studio pins.
6. **PROD:** no Database Manager action on PROD in 1.3, the backup with the read profile included (fail closed;
   E-X7 would allow a PROD backup; building it is later work with its own review).
7. **Windows:** the PostgreSQL tools, the SQLite delete and a SQLite restore into a new file are refused; a SQLite
   backup and a restore over the connection's own file stay allowed, protected by the access list of the
   configuration folder.
8. The owner points recorded under "As built" for STU-XDM-4 and STU-XDM-5, and section 11 for STU-XDM-3, stand as
   decided.

## Consequences

- STU-XDM-2 implements sections 2 to 4 for backup and restore, STU-XDM-3 section 5; STU-XDM-4..STU-XDM-7 follow, each behind the
  `database_manager` flag with the kill-switch proof of STU-ADR-0006.
- CI needs `pg_dump` and `pg_restore` of the server's major version or newer for the restore tests (the
  `postgres` job already has `pg_dump`).
- Tabrion's `rowedit` and `Transaction.execute_checked` are stable since decision 7A; the role, restore, delete and
  database names Studio uses stay experimental (`X`). The review of STU-XDM-1 recommends no release exception:
  Tabrion promotes the names Studio uses to stable (`S`) before Studio 1.3 (a TODO item).
- The planning non-goal "no backup execution, no role management" of document 03 section 1 stays for 1.0 to 1.2.

## As built (STU-XDM-2)

- Routes, all behind `database_manager` (405 and no import with the flag off): `GET /api/write/database-manager/backups`
  (the list, count, total size and the tools' state without a path), `POST .../backup`, `POST .../restore/preview`
  (changes nothing) and `POST .../restore`. The page sends IDs, the target's name, the typed text and, on PostgreSQL,
  the restore profile's ID; never a path, a tool or an environment.
- Every action needs the connection's admin unlock (`kind = "admin"`), and the admin check of section 6 runs again
  at its start. Backups of SQLite go through `tabrion.sqlite.backup`, restores through `tabrion.sqlite.restore`
  (with `integrity_check`).
- Audit: `run.start` (fail closed) and `run.result` with the action (`dbm.backup`,
  `dbm.restore`), the backup file, its SHA-256, the tool version, the target, the restore profile, the
  target's own backup and whether the fingerprint matched; no value, no path, no secret.
- Stricter than the text above: no opt-out of the target's backup; `stderr` never logged; Windows refused.
- Owner's condition of G-S4 (2026-11-18): on Windows a SQLite restore into a **new file** is refused
  (`restore.platform`) until a check of the folder's access list exists and has been reviewed, as STU-CR-2 refuses
  creating one; a restore over the profile's own file stays possible.

## As built (STU-XDM-3)

- Routes behind `database_manager` (405 and no import with the flag off): `POST /api/write/database-manager/delete/preview`
  (changes nothing; the name to type, on PostgreSQL the other sessions and whether `force` is offered) and
  `POST .../delete` (`connection_id`, `name` on PostgreSQL, `typed`, `force`, `force_typed`). Module
  `tabrion_studio.write.database_manager.delete`.
- Only an admin unlock whose stored environment is `dev` or `test` (PROD has no unlock, E-X7); a target that any PROD
  profile names is refused (the checks of STU-XDM-2, review L-1).
- PostgreSQL: another database of the unlocked server by name, through `drop_database` on the admin session in
  autocommit. Refused before anything is read: `postgres`, `template0`, `template1`, any template (`datistemplate`),
  the database the admin connection uses. The OID is read before the backup and again on the dropping session;
  a different OID refuses (`studio.dbm_delete_target_changed`). Studio closes its own read connections to the target
  first. Other sessions without `force` refuse before the backup (`studio.dbm_delete_in_use`, 409); `force` is a
  strict boolean, accepted only while other sessions exist on PostgreSQL 13 or later, needs `force_typed` (the name
  again), is never retried and is audited as `force` with the session count `sessions`.
- SQLite: only the connection's own file (no name from the request), its folder checked by the folder rules of
  STU-CR-2/STU-XDM-2 (not group- or world-writable, every folder above safe, none of Studio's own folders by device
  and inode); Studio's connections to it and the unlock are closed, then `delete_database_file`. **Refused on
  Windows**, as STU-CR-2 refuses a new SQLite file there.
- The backup first, fail closed, **no opt-out** (stricter than section 5): PostgreSQL through the verified runner as
  the admin role (`pg_restore --list` reads the archive before it is kept), so the admin role must be able to read the
  whole target database, and the backup must carry the target's level-2 fingerprint, read the same before and after
  the dump (review L-1 of PR #140: a schema change during the dump or an unreadable fingerprint leaves none and
  refuses the delete with `studio.dbm_delete_backup_unverified`); SQLite through `tabrion.sqlite.backup`, then the copy's level-2 fingerprint must equal the
  source's (`studio.dbm_delete_backup_unverified`). Origin `pre_delete` in the metadata; the backup is listed and
  restorable like any other.
- The typed name compared byte for byte; a name with a character that cannot be seen is refused (GS4-17).
- Residual window: writes to the target between its backup and the drop are lost (Studio checks the OID, or device
  and inode, not the content). Other sessions are refused unless `force` ends them, which narrows it on PostgreSQL;
  on SQLite tabrion's exclusive-lock check sees a connection that holds a lock, not an idle one.

## As built (STU-XDM-4)

- Routes behind `database_manager` (405 and no import with the flag off, which is the default):
  `POST /api/write/database-manager/roles` (the server's roles, not the predefined ones, with their attributes,
  connection limit and expiry, names escaped, Studio's own roles marked), `POST .../roles/preview` (changes nothing)
  and `POST .../roles/apply`. Module `tabrion_studio.write.database_manager.roles`; every change is one call of
  Tabrion's stable role DDL (tabrion 0.3.0): `create_role`, `alter_role`, `drop_role`, `grant_membership`,
  `revoke_membership`, `grant_privileges`, `revoke_privileges`, with `RoleOptions` as the only attributes.
- **Who and where:** only an admin unlock (the separate minimal admin profile) of a PostgreSQL connection whose
  stored environment is `dev` or `test`; PostgreSQL 16 or later (`studio.dbm_role_requires_16`); refused
  (`studio.dbm_role_not_allowed`, `role.prod`) when any PROD profile of the user's store names the **server**
  (any database: roles are the cluster's) by address and port or, where its connection is open, by
  `system_identifier`, so `localhost` for `127.0.0.1` does not hide it (the shared server matching of
  `tabrion_studio.write.prod_targets`, `addresses` and `system_identifier`, without its database-name check). The admin check of section 6 runs on every
  request and again on the changing session; a refusing finding answers `studio.admin_over_privileged` and ends the
  unlock (also when Tabrion reports `postgres.caller_unsafe`).
- **Never granted:** SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION, an ADMIN or GRANT option (a request that names one
  is `studio.dbm_role_attribute_banned` before anything runs), TRIGGER (K-2; only revocable).
- **Checked on every change** (the preview, and again inside apply right before the call): `role_privileges` of the
  target and of the member; a membership in a predefined role, a server-access role
  (`pg_read_server_files`, `pg_write_server_files`, `pg_execute_server_program`) or a group that is or reaches a
  dangerous attribute (`role.privileged_role`); a member that already reaches one; a group with a K-2 grant
  (TRIGGER, EXECUTE on a foreign SECURITY DEFINER function, CREATE on a schema) in **any** database the admin role
  may connect to (at most 64; `role.escalation_grant`; the preview lists the databases checked, not checked and how
  many the admin role may not connect to); a role that still owns objects or default privileges for a drop
  (`role.owns_objects`). Tabrion's own refusals (`RoleUnsafe`) arrive as `studio.dbm_role_unsafe` with fixed
  `role.<reason>` issues.
- **Studio's own roles** are never changed (`role.own_role`): `CURRENT_USER`, `SESSION_USER` and the user of every
  profile (the user's store and a trusted project) and write or admin profile (the admin role and the current user's
  role included), `CURRENT_USER` and `SESSION_USER` of every open read connection, and for a profile without `user`
  the OS user name libpq takes; when that cannot be known, every role change is refused (`role.own_role_unknown`,
  review I-2 of #149). **Nor widened or narrowed through a group** (review L-1 of #149): every action but create is
  refused (`role.own_role_reached`) when one of these roles reaches the role whose rights change (the member of a
  membership, otherwise the role) through any chain of memberships with INHERIT or SET
  (`pg_has_role(own, role, 'USAGE')` or `'SET'`), so no privilege or membership is given to a group above Studio's own
  role, and no such group is changed, revoked from or dropped. A membership with only the ADMIN option, which
  PostgreSQL 16 gives the creating admin role on every role it creates, passes no rights on and does not count.
- **Preview, binding and typed confirmation for every change**, not only for drop and revoke: the apply carries the
  preview's `state` (a SHA-256 over the request, the target's and member's privileges and the K-2 result) and must
  match a fresh read (`studio.dbm_role_changed`); the typed name is the role whose rights change (the member for a
  membership), compared byte for byte (`studio.dbm_role_name_mismatch`). The apply button is never the default
  focus; drop and revoke use the danger style.
- **Residual window (review I-1 of #149):** the K-2 check of the other databases runs before the change, outside
  Tabrion's transaction (Tabrion checks only the connected database inside it). A TRIGGER, SECURITY DEFINER or
  schema CREATE grant made in another database by another privileged session between that check and the commit is
  not seen; it needs a concurrent privileged change and is not reported afterwards either.
- **After the commit** `role_privileges` is read again and every field that differs from Tabrion's result is
  answered and audited as `differences` (P90-06).
- **Alter sets every option** (Tabrion's `alter_role` sets every field of `RoleOptions`): a request that leaves one
  out is refused (`role.options_incomplete`), and the page starts from the role's current values, so nothing falls
  back to a default unseen.
- **Privileges** only in the connected database (the admin connection's); the objects as Tabrion takes them
  (`schema.table`, `schema.fn(args)`, plain schema and database names); warnings for CREATE on a database or a
  schema, `public_definers` (a superuser owner first), CREATEDB, and that a revoke removes only the admin role's own
  grants.
- **Names:** every name in an answer, the audit log and the log is escaped (`<U+XXXX>` for Cc, Cf, Cs, Co, Cn, Zl,
  Zp and every space but U+0020); a name with such a character is refused for every action
  (`studio.dbm_role_name_invalid`, `role.name_hidden`).
- **Passwords:** only for create and alter, only in the apply request, handed straight to Tabrion (a client-side
  SCRAM verifier); the audit record carries `password_set` only. Never in the preview, an answer, the audit log or
  the log.
- **Audit:** `run.start`/`run.result` with action `dbm.role.<action>`, the role in `objects`, `member`, the option
  names in `options_set` (never their values), `on`, `privileges`, `grant_objects`, `password_set`, and `differences`
  on the result. A start record that cannot be written stops the change.
- **Errors:** fail closed; Tabrion's refusals as fixed `studio.dbm_role_*` problems; driver errors as `db.<kind>`
  with only `safe_message` in the log.
- **Windows:** role management uses no file or tool, so it is not refused on Windows; the rules above apply there
  unchanged.
- **Shared with STU-CR-6** (runtime-role wizard, TB-148, ADR 0007 section 12): the wizard is under the same area and
  gate, so it reuses `server_checks`, `own_roles`, `read_privileges`, `k2_scan`, `display_name` and `refusal` of this
  module rather than a second copy of the rules.
- **Owner points, decided with the safest practical option** (STU-XDM-4; the owner may revisit them at G-S4):
  1. Every role change, not only drop and revoke, needs a preview and the typed name.
  2. The apply is bound to the preview's `state`; any change in between refuses.
  3. Studio's own roles are the session's roles, the user of every profile (or the OS user for one without), and the
     roles of the open connections; a role any of them reaches through INHERIT or SET is refused for every action
     but create (review L-1 and I-2 of #149).
  4. A PROD alias refuses on the server level, not only for the connected database.
  5. A K-2 check that could not reach every database warns and names the gaps; a K-2 grant found refuses.
  6. Privilege grants only in the connected database.
  7. PostgreSQL 16 or later only.
  8. A role whose name holds a hidden character is refused for every action, not only for destructive ones.
  9. Alter needs every option named (no silent defaults).
  10. Windows: not refused (no file or tool is involved).

## As built (STU-XDM-5)

- Routes behind `database_manager` (405 and no import with the flag off, which is the default):
  `POST /api/write/database-manager/profiles/export/preview` (writes nothing), `POST .../profiles/export`,
  `POST .../profiles/import/preview` (changes nothing) and `POST .../profiles/import`. Module
  `tabrion_studio.write.database_manager.profiles`.
- **Pairs** stay what K-1 and STU-XW-4 made them: a `[[write_profile]]` of the user's store coupled to one of the
  user's read profiles by `read_profile`, never to a PROD one, with the I-109 j confirmation of server and secret
  reference before a PostgreSQL pair's secret is read. No pair route, store or unlock of its own: the page shows the
  pairs from `/api/profiles` and `/api/write/profiles`. An imported profile is a read profile without a partner; a
  pair made with it later needs the same confirmation (`studio.secret_unconfirmed` before any secret is resolved).
- **Export** reuses `tabrion_studio.write.profile_export` of STU-CR-6 (one allowlist of fields, `mode = "read_only"`,
  `env:` kept, any other reference written as `prompt`, a host list refused, the folder rules of
  `tabrion_studio.write.folders`, `O_EXCL|O_NOFOLLOW`, `0600`), with area `database_manager` and audit action
  `dbm.profile_export`. Only read profiles can be named. The preview names the profiles and what the file will say
  about each password, and the write and admin profiles coupled to them as left out; their fields and secrets are
  never read. The export carries the preview's `state` (a SHA-256 over the path and the text to be written) and the
  typed file name; any change in between refuses (`studio.profile_export_changed`).
- **Import** reads a `*.tabrion-profile.toml` with `tabrion.profilefile.parse` after
  `tabrion_studio.write.folders.read_user_file`: the folder passes `check_folder` and is not one of Studio's own
  folders; the file is opened relative to the held folder with `O_NOFOLLOW|O_NONBLOCK` and must be a regular file of
  the user, not writable by group or others, with one name only (no hard link), at most 256 KiB. The whole file is
  refused (`studio.dbm_profile_import_invalid` with `import.<code>`, or `_multi_host`, `_folder`, `_reserved`,
  `_missing`, `_too_large`, `_platform`) when it is not valid TOML or UTF-8, not format version 1, holds no profile or
  more than 64, holds a password, a key Tabrion warns about (a write or admin key among them), or a host list in any
  profile (W-10, TB-148: never cut, never partly imported).
- **What is taken:** only the fields of the export's allowlist; a relative SQLite path is resolved against the file's
  folder. `allow`, `schema_fingerprint`, `application_name` and `description` are named in the preview as not imported.
  Each profile is checked again by Studio's own validator. A profile is not importable when its `mode` is not
  `read_only`, when it names no environment, or when it is not PROD but names the database of a PROD profile (in the
  user's store, a trusted project or the same file; PostgreSQL by database, port and overlapping addresses, SQLite by
  device and inode). An imported PROD profile stays PROD.
- **Secrets:** an imported profile never carries one and keeps **no reference** of the file. Every reference,
  `env:NAME` included, becomes Studio's empty keyring entry, asked for when used; a SQLite profile has none. The file
  is not trusted: keeping an `env:` reference would let it pair a server of its choice with any variable of the
  user's environment, whose value would then be sent there on the first test or connect (review M-1 of #155).
- **Names:** an import never replaces a profile. A name that a read or write profile of the user's store has already
  needs a new name in the request (`as`) or the profile is left out; otherwise `studio.dbm_profile_import_exists`
  (409). The clash is checked again inside the store's save, so a profile saved in between is not replaced. A name
  with a hidden character is refused (`import.name_hidden`).
- **Binding:** the import carries the preview's `state` (the file's SHA-256) and the store's digest; a changed file is
  `studio.dbm_profile_import_changed`, a changed store `studio.config_stale`, and a store written by hand needs
  `overwrite` as every save does.
- **Audit:** `run.start`/`run.result` with action `dbm.profile_export` or `dbm.profile_import`, the profile names in
  `objects` and the file's name (never its folder) as `target`; never a host, a path, a reference or a secret. A start
  record that cannot be written stops the action.
- **Windows:** export and import are refused (no `dir_fd`, no check of a folder's access list), as for the STU-CR-6
  export.
- **Owner points, decided with the safest practical option** (STU-XDM-5; the owner may revisit them at G-S4):
  1. Pairs stay the existing coupling and confirmation; no new pair model.
  2. An import adds read profiles only and never replaces one; a clash needs an explicit new name.
  3. A profile whose `mode` is not `read_only` is not importable (not silently made read only).
  4. A profile without an environment is not importable (no default label).
  5. A non-PROD profile of a PROD database is not importable (no second label for a PROD target).
  6. No reference of the file is kept, `env:` included: every password becomes a prompt, and nothing is resolved
     during or after an import (review M-1 of #155, option a; first built keeping `env:`).
  7. A host list, a password or an unknown key refuses the whole file.
  8. A relative SQLite path is resolved against the file's folder.
  9. The file must be the user's own, private, single-named regular file of at most 256 KiB and 64 profiles.
  10. No admin unlock is needed: neither operation touches a database.
  11. Both operations are bound to their preview by `state`.
  12. Windows: refused.
  13. A name with a hidden character is refused.

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
