# STU-ADR-0007: Schema Designer: server-side plans, direct DDL only on DEV and TEST, revisions only for Alembic projects

**Status:** accepted (work package STU-CR-1, change request CR-DBX, Studio 1.2; the independent review of STU-CR-1
accepted it on master `4b9fb7c` with conditions A-1..A-4, which this version takes in). It records owner decisions that are
already binding (E-X3, E-X4 with its refinement, E-X7, E-X9, W-9, D-5 of tabrion ADR 0015 section 10) and the
conditions tabrion's reviews set for Studio (tabrion contract I-124 a, e, j; ADR 0015 sections 2 to 4), and adds the
rules that make them testable. **G-S3 is not granted and this record grants nothing:** the `schema_designer` flag stays off by default,
no code of the area exists yet, and no default changes. It builds on [STU-ADR-0006](0006-per-area-write-kill-switches.md)
(one flag and one gate per area, the PROD lock, write profiles, the kill-switch proof), which applies unchanged. The
threat model is [Schema Designer threat model](../architecture/schema-designer-threat-model.md) (TX-18..TX-31); the
gate is defined in [G-S3 definition](../architecture/security-review-prep.md#g-s3-definition-schema-designer).

## Context

The Schema Designer (planning document 15 section 4; STU-CR-2..STU-CR-8, STU-XSD-1..STU-XSD-3) lets a developer
design tables, columns, constraints and indexes, compare schemas, and turn a design into a change of a database.
Changing a schema is the most destructive thing Studio would do: a dropped column or a narrowed type loses data, a
SQLite rebuild with foreign keys on deletes child rows, and an Alembic project whose database Studio changes behind
its back is out of step with its own history. The owner decided (E-X3) that DDL, the difference and the plan come
from Tabrion, built on SQLAlchemy Core and Alembic operations, not from Studio, and (E-X4) that Studio changes a
schema directly only on DEV and TEST databases without migration management, and writes only revision text for an
Alembic project. Tabrion's part exists in the pinned commit as experimental (`X`) names: `tabrion.schema` level 2
(TAB-XW-2), `tabrion.ddl` (TAB-XW-3) and `tabrion.plan` (TAB-XW-4: `plan`, `MigrationPlan`, `revision_text`,
`migration_markers`) and the executor `tabrion.plan.execute_plan` (TAB-XW-5, tabrion #91 `7c0da1a`, reviewed and
merged: plan binding, the drift check after the locks, dry run, `lock_timeout`, the foreign key check). Studio pins
tabrion `71370a6` (merge of tabrion #92), which contains it. The G-T2 review of 0.3 (TAB-XW-10) is still open.

## Decision

### 1. Area, flag and gate

- Every route of the Schema Designer that changes a database or a project file is behind the `schema_designer` key
  of `write-areas.toml` (STU-ADR-0006, "As built") and the gate **G-S3**. With the flag off each such route is a
  refusing handler (405 `studio.method_not_allowed`, W-9) that does not import `tabrion.ddl`, `tabrion.plan` or the
  area's executor, and nothing changes (database, project folder, audit log).
- The area covers STU-CR-4..STU-CR-6 and STU-XSD-1..STU-XSD-3 (STU-ADR-0006 table): the designer and its plans, the runtime-role wizard and the profile export (STU-CR-6), the graphical mode, the schema comparison and the
  revision output. Reading and showing a level-2 snapshot of a connected database is a read path and may exist with
  the flag off (it runs on the read profile's connection, TX-03).
- G-S3 cannot be granted before the pinned Tabrion has the executor of TAB-XW-5 with the D-5 parts that need a
  connection (D-5.4), independently reviewed (tabrion ADR 0015 section 10: "G-S3 stays locked until the
  implementation is in place, independently reviewed and tested"). Tabrion #91 (`7c0da1a`) delivers it and was
  accepted; Studio pins a commit that contains it (GS3-P1).
- **Amended 2026-11-18 (G-S4, owner grant of 2026-11-18):** creating a SQLite file or a PostgreSQL database
  (STU-CR-2, STU-CR-3; tabrion `create_private_file`, `create_database`) is no longer part of this area. The owner
  decided that database creation and role management belong to the admin area G-S4 with separate permissions; the
  wizards are behind the `database_manager` flag and, for PostgreSQL, the admin profile
  ([STU-ADR-0008 section 10](0008-database-manager-backup-restore-roles.md#10-creating-a-database-stu-cr-2-stu-cr-3)).
  The `schema_designer` flag alone never creates a database. A write profile never needs `CREATEDB` any more: a
  write role that has or reaches it is refused like the roles of decision 1A (`studio.write_role_over_privileged`).
  The former text (the write profile with `CREATEDB`, the warning conditions of the review of #128, GS3-26) is
  superseded.

### 2. Studio builds no DDL and no plan text of its own (E-X3)

- Every statement the Schema Designer shows, writes into a revision or runs comes from `tabrion.plan.plan` (which
  uses `tabrion.ddl`). Studio never assembles, edits, filters or reorders a statement and never concatenates SQL.
- A refusal of Tabrion (`PlanRefused` with its reason, `UsageError("plan.*")`, `ddl.*`) is shown as a catalog problem
  with a fixed text per reason and code; Studio never offers a way around it. Tabrion has no `force` for plan
  binding, migration management or the D-5 refusals, and Studio adds none.

### 3. Plans and target models live on the server (I-124 a, N-2)

- The **draft** (the target model the user designs) is held by the server in the user's session, bound to the
  connection and the unlock. The page sends edit operations on the draft (add a column, drop an index, ...); the
  server checks each against the level-2 model and keeps the result. A complete target model, a `MigrationPlan` or
  its JSON is never taken from the page, a file, a project folder, a URL, a profile import or a database:
  `MigrationPlan.from_json` proves integrity, not authenticity, and is not called on input from outside the server.
- A schema snapshot loaded from a file for the comparison (STU-XSD-2, `SchemaSnapshot.from_json`) is untrusted data:
  it is shown and compared, never used as the source or target of a plan that runs or of a revision. Making such a
  snapshot the start of a draft is not part of 1.2 (open point, docs/TODO.md).
- Drafts are not written to disk in 1.2; they end with the session.

### 4. Re-plan before the run, compare the hash, take the risks from the new plan (I-124 a)

1. **Preview:** the server takes a level-2 snapshot (on the write connection for direct execution; on the read
   profile's connection for revision output, which writes nothing to the database), plans from it to the
   server-side target and shows the plan: every step with its operation, risk classes, transactional or not, and
   its exact SQL; the source and target fingerprints; the plan hash. The STU-XW-3 framework binds the hash to the
   session, the connection, the unlock (for direct execution) and one run (TX-08).
2. **Confirmation:** the user confirms the plan hash with the typed text of STU-XW-3 and, for every risk class that
   needs it (section 6), the extra confirmation. The server records the confirmed hash and the confirmed classes.
3. **Run:** the server plans **again** from a fresh snapshot and the server-side target, with the same
   `server_version` and options, and compares the new `plan_hash` with the hash the user confirmed (the one the
   server recorded, never one sent with the request). A different hash refuses the run before anything is sent
   (`400 studio.plan_drift`). The risk classes the executor may run (`allow`) are the classes the user confirmed,
   and they must cover every class of the new plan.
4. Studio's own re-plan in step 3 is only a pre-check. The binding check is the executor's: Studio calls
   `execute_plan(database, target, plan_hash=<the recorded hash>, expected_fingerprint=<the source fingerprint of
   the confirmed preview>, environment=<"dev" or "test", section 5>, allow=<the confirmed classes>,
   server_version=<section 9>, concurrent_indexes=False)`. The executor plans again itself inside its transaction
   (it never takes a plan from the caller), locks the target tables under `lock_timeout` and checks the fingerprint
   again after the locks (D-5.4). Its `SchemaDrift` or `PlanMismatch` is `400 studio.plan_drift`, without data.
   Because direct execution allows no non-transactional step, a direct run either commits completely or changes
   nothing: there is no partial result in this path, and the TB-149 obligations for drops and partial runs apply
   only to revision output.
5. **Dry run:** `execute_plan(dry_run=True)` runs the plan and rolls it back. On PostgreSQL it takes the same
   `ACCESS EXCLUSIVE` locks and advances sequences, which a rollback does not undo. Studio runs a dry run only when
   the user asks for it explicitly, never automatically, with the same confirmations and unlock as a real run, and
   with a notice of the locks and of sequence values that stay consumed (GS3-29). It is audited like a run.

### 5. Two outputs, chosen by the server (E-X4)

**Direct execution** is offered and accepted only when **all** of these hold, each checked on the server at preview
and again right before the run:

- the `schema_designer` flag is on and a write profile is unlocked for the connection (STU-ADR-0006);
- the environment of the coupled read profile in the user's own profile store is `dev` or `test` (never `prod`,
  never an unset environment, never a value from the request or a project file, K-1); it is passed to
  `execute_plan(environment=...)`, which refuses any other value as a second lock (tabrion ADR 0015 D-3);
- `tabrion.plan.migration_markers` finds no migration management on the database (`alembic_version`,
  `nodestro_schema_migrations`, `axentra_schema_migrations` in any schema, or a SQLite `user_version` above 0), and
  the executor's own refusal (`PlanRefused`, reason `migration_managed`) stays in force;
- Studio's migration status (STU-015, STU-024) finds no format for the connection: no Alembic project (an
  `alembic.ini` with `script_location` in the project folder) and no Levuvo version table (Nodestro, Axentra, and
  Orkexa's `<prefix>orkexa_schema`, which `migration_markers` does not know). An Alembic project gets revisions only,
  even when its database has no version table yet;
- the plan has no non-transactional step (direct execution plans with `concurrent_indexes=False`).

**Revision output** (STU-XSD-3) is the only output for an Alembic project, and is available for any trusted
project. It needs the `schema_designer` flag but no write profile. In 1.2 it is refused for a connection whose stored
environment is `prod` (whether to allow it, since it writes only the project file, is an open decision for G-S3):

- The text comes from `tabrion.plan.revision_text` (a string; Tabrion reads no project and writes no file). Studio
  never runs `env.py`, imports a project module, runs a revision, calls `alembic upgrade` or any `alembic.command`,
  or opens a `MigrationContext` that runs revisions; the migration status keeps its 1.0 rules (T-08, T-16).
- Writing the file needs project trust (D-01) and happens only into the project's revision folder as the migration
  status reported it, resolved without following links and inside the trusted project folder. The file name is
  built by Studio from the revision id (`[0-9a-f]{12}`, generated) and a slug of the message limited to
  `[a-z0-9_]{1,40}`; no path part, no `..`. The file is created exclusively (no overwrite) with no-follow semantics
  and the project's normal mode, and the page shows the path.
- `down_revision` is the single head the migration status reported, which needs the project's code trust (the
  1.0 rule of the migration worker); with several heads, none readable or no code trust the revision output is
  refused (`studio.schema_revision_head_unknown`).
- The preview says that the revision does not check its source fingerprint (I-124 j 5): apply it only to the
  database it was planned for, soon after the preview. It shows the source fingerprint. A plan with
  `CONCURRENTLY` steps is written as a revision of its own, as Tabrion orders them.

### 6. Risk classes and confirmations (doc 15 section 4, TM-DB-08)

| Class (I-124 c) | What Studio asks before the run |
|---|---|
| `additive` | The STU-XW-3 confirmation (plan hash, typed write profile name) |
| `data_changing` | As above, plus the step named in the confirmation dialog; backup before the run (section 7) |
| `destructive` | As above, plus the typed name of each affected table (D-10); backup before the run |
| `rebuild` (SQLite) | As `destructive`; the dialog names the table and that it is copied, dropped and created again |
| `locking` (PostgreSQL) | A notice that the step blocks writes on the table while it runs, with the `lock_timeout` |
| `non_transactional` | Not run directly in 1.2; revision output only (section 5) |

The destructive action never has the default focus (08 section 6). Every confirmation names the target (profile,
environment, database, table).

### 7. Backup before a destructive run (D-11; doc 15 section 7 item 7)

- Before a plan with a `destructive`, `rebuild` or `data_changing` step runs, Studio makes a backup of the database:
  SQLite with `tabrion.sqlite.backup` into Studio's backup folder (0700, files 0600); PostgreSQL with `pg_dump`
  under the rules of [STU-ADR-0008](0008-database-manager-backup-restore-roles.md) section 3 (the same runner,
  whichever work package builds it first). A failed backup stops the run.
- Leaving the backup out is possible only on a `dev` profile, with a typed confirmation, and is audited.
- **PostgreSQL (STU-CR-5 follow-up).** STU-CR-5 refused such a PostgreSQL plan (`backup_unavailable`) because the runner did not
  exist yet. The STU-CR-5 follow-up wires in the runner of STU-XDM-2 (`tabrion_studio.write.pgtools`, imported under the
  `schema_designer` flag; review L-2 a of STU-XDM-1): before a real run on DEV or TEST, after `run.start` and before
  `execute_plan`, Studio verifies `pg_dump` and `pg_restore` again (only the paths of `database-tools.toml`, every
  check of STU-ADR-0008 section 3, a major of at least 16 and at least the server's), reads the write profile's
  credentials with every check of an unlock and only while the profiles are the ones the unlock checked, and makes a
  `pg_dump -Fc` archive of the unlocked database into `backups/schema-designer/<time>-<hex>.dump` (0600, with its
  metadata, origin `schema_designer`), kept only after exit status 0 and a readable `pg_restore --list`. The run
  continues only when that succeeded; otherwise it is refused before any statement (`studio.backup_tool_unavailable`,
  `studio.backup_tool_failed`, `studio.write_target_changed` or `studio.schema_backup_failed`) and `run.result`
  names the code. Without both tools configured, without the folder of `write-areas.toml` and on Windows such a plan
  is still refused at the preview and again before the run (`backup_unavailable`). There is no opt-out on
  PostgreSQL. A dry run makes no backup. The plan binding, the confirmation, the drift check after the locks and the
  limits are unchanged.

### 8. Preview warnings Tabrion asks for (I-124 e, j)

The preview shows, with fixed texts in English and German:

- **PostgreSQL dependent code (I-124 j 2):** for every `drop_column`, `drop_table` and column type change, that a
  PL/pgSQL or non-`ATOMIC` SQL function body is not tracked by PostgreSQL and breaks at run time if it names the
  column or table. Studio may also name functions of the snapshot whose body names the column (a token match,
  marked as a hint).
- **SQLite `rowid` (I-124 j 4):** a rebuild of a table without an `INTEGER PRIMARY KEY` gives its rows new `rowid`
  values.
- **Fingerprint gaps (I-124 j 3):** the plan binding does not cover function owners and privileges, table owners,
  privileges, comments, views and the other items Tabrion lists.
- **Triggers and policies (D-5.1):** functions called from a trigger or policy function are not in the fingerprint,
  and a function can change after the drift check because it cannot be locked.
- **Refusals (I-124 e, D-5.2, D-5.3):** a table with a feature marker (partitioning, inheritance, a domain column, a
  foreign table, a rule, `UNLOGGED`, ...), an event trigger, a trigger or policy function in another language than
  PL/pgSQL or SQL, a change to triggers, policies, functions or views, a dependent SQLite view or trigger: the plan is
  refused and the preview says which table and which feature, from Tabrion's `details`, never more.

### 9. `server_version` is always passed (I-124 e)

Studio passes `server_version` to every `plan` call: on SQLite `sqlite3.sqlite_version_info` of the library the
write connection uses, on PostgreSQL the server's `server_version_num` read on the write connection. `None` is never
passed, so a plain SQLite column drop runs in place under SQLite's own check and the plan matches the engine.

### 10. SQLite runs (K-1 at execution, R-CR-5)

A SQLite plan runs on the write connection of STU-XW-1 (`trusted_schema = OFF`, Studio's authorizer, defensive
mode, the file identity checked at unlock and before the run, TX-16) through the executor, which turns foreign keys
off before `BEGIN`, runs `foreign_key_check` and `integrity_check` before the commit and refuses schema text that
fails K-1 again at execution. Studio adds no statement of its own around it.

### 11. Audit, errors, values

- Every run writes `run.start` and `run.result` through `Unlocks.audited_transaction` with the plan hash, the
  table names, the risk classes and the step count; never a statement, a default or check text, or a value. A
  revision written into a project is audited as `schema.revision` with the revision id and the project's name.
- Database errors are `db.<kind>` problems built from `tabrion.errors.classify` and `safe_message` only; Tabrion's
  `PlanRefused`, `PlanMismatch`, `SchemaDrift` and `UsageError` become fixed catalog problems (`studio.plan_*`,
  `studio.schema_*`) with codes and positions, never the server's text.
- The plan preview shows the plan's SQL to the user who designed it; that SQL never reaches a log, the audit log,
  the query history or a diagnostics report.

### 12. Runtime role and profile export (STU-CR-6)

The runtime-role wizard creates a role and grants it object privileges through the role DDL of Tabrion (TAB-XW-8)
and therefore follows every role rule of STU-ADR-0008 section 6 (admin profile, attribute ban, checks before and
after, the TB-149 obligations). Role DDL needs an admin profile, and an admin profile is unlocked only under the
`database_manager` area. The runtime-role wizard therefore **requires the `database_manager` area and G-S4**: it is
built with STU-CR-6 but runs only when both flags are on; the Schema Designer adds no admin unlock of its own. (The
alternative, a separate admin unlock under `schema_designer` with the rules of GS4-11, GS4-12 and GS4-19, is not
chosen; changing this needs the owner.) The profile export
writes single-host profiles only (W-10, TB-148): a multi-host profile is refused, never cut short. Write and admin
secrets are never exported (N-5).

## Consequences

- Nothing of the Schema Designer is implemented by this record; STU-CR-2..STU-CR-8 and STU-XSD-1..STU-XSD-3 follow
  it, each behind the `schema_designer` flag with the kill-switch proof of STU-ADR-0006.
- Studio uses `tabrion.schema` level 2, `tabrion.ddl` and `tabrion.plan`, which are experimental (`X`). A release
  of Studio 1.2 needs them stable (02 section 14 S-2) or an accepted exception, as for `tabrion.rowedit` in 1.1.
- The executor (TAB-XW-5, pinned since `71370a6`) and the 0.3 G-T2 review (TAB-XW-10) are preconditions of G-S3.
- Open sizing and retention rules for STU-CR-4 and STU-CR-5 (review info I-1, I-2): a limit for the size of a draft
  and the number of edits in it, and how long backups made before a run are kept (and how the user removes them).
- Studio still never runs a migration: direct execution runs one confirmed plan once and keeps no version table.

## Tests

Before G-S3 (the full list with test names is the [G-S3 definition](../architecture/security-review-prep.md#g-s3-definition-schema-designer)):
the kill-switch proof per route; direct execution refused for PROD, an unset environment, migration markers, an
Alembic project and a non-transactional step; no `env.py`, project module or Alembic command ever run (a planted
`env.py` that would leave a mark); the revision file name and folder checks; re-plan with a changed database and a
client-supplied plan refused; `server_version` passed; every risk class with its confirmation; backup before a
destructive run and the stop on a failed backup; the preview warnings; SQLite with foreign keys on refused and no
row lost; canaries for values and secrets in plans, revisions and the audit log.
