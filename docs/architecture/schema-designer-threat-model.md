# Schema Designer threat model (STU-CR-1, G-S3)

This page is the threat model of the Schema Designer of Studio 1.2 (STU-CR-2..STU-CR-8, STU-XSD-1..STU-XSD-3,
planning document 15 sections 4, 7 and 10). It extends the [Write and admin threat model](write-threat-model.md),
whose rows marked "shared" (flags, PROD lock, profile separation, unlocking, plan binding, audit log, hostile
database objects, TX-01..TX-16) apply here unchanged, and it is the threat model that
[STU-ADR-0007](../adr/0007-schema-designer-plans-and-revisions.md) refers to. The gate is defined in
[G-S3 definition](security-review-prep.md#g-s3-definition-schema-designer).

**State:** proposed with STU-CR-1; needs the independent review of STU-CR-1. No code of the area exists. Every
measure is a requirement for the work package named in its row and is checked by the G-S3 gate review. The
`schema_designer` flag stays off by default; **G-S3 is not granted.**

The repository is public, so this page describes threats and measures, not how to exploit them.

## Scope

- **In scope:** the draft (target model) and its edits; the plan from Tabrion (`tabrion.plan`, `tabrion.ddl`,
  `tabrion.schema` level 2) with its preview, confirmation and run; the choice between direct execution and revision
  output (E-X4); writing an Alembic revision into a trusted project; the backup before a destructive run; the schema
  comparison against a connection, a snapshot file or the draft; the graphical mode; the database creation wizards
  (STU-CR-2, STU-CR-3); the runtime-role wizard and the profile export (STU-CR-6).
- **Shared:** TX-01..TX-16. The role rows of the [Database Manager threat model](database-manager-threat-model.md)
  (TX-40..TX-44) apply to the runtime-role wizard.
- **Out of scope here:** backup, restore and delete as actions of their own, role management beyond the runtime role
  (G-S4).

## Assets and attackers

The assets A1..A9 and boundaries B1..B7 stay. Sharper here: **A3, the integrity of the connected databases**,
because a schema change can lose whole columns or tables; **A10, the trusted project folder**, because Studio now
writes into it; **A11, the Alembic history** of a project, which a direct change outside it puts out of step. The
main attacker stays AT7, the developer's own mistake (wrong database, an old preview, a migration-managed database
changed directly); next come a hostile database (a crafted catalog or SQLite schema, AT4) and hostile project files
(AT3).

## Threats and measures

| Id | STRIDE | Threat | Measures | Work package, tests |
|---|---|---|---|---|
| TX-18 | T/E | **Direct DDL where it is not allowed (E-X4):** Studio changes a PROD database, a database without a declared environment, a database with migration management, or the database of an Alembic project directly | Direct execution only when the stored environment (user's store, K-1) is `dev` or `test`, `migration_markers` is empty, the migration status finds no Alembic project and no Levuvo format (Orkexa included), and the plan has no non-transactional step; checked at preview and again right before the run; `execute_plan(environment=...)` from the stored profile refuses any other value and refuses `migration_managed` itself (second lock). Otherwise only revision output (ADR 0007 section 5) | STU-CR-5: negative tests for PROD, unset environment, each marker (`alembic_version` in another schema, `nodestro_schema_migrations`, `axentra_schema_migrations`, `<prefix>orkexa_schema`, SQLite `user_version` 1), an Alembic project without a version table, a marker added between preview and run, a `CONCURRENTLY` step |
| TX-19 | E | **Project code runs (T-08, T-16):** writing or preparing a revision runs `env.py`, imports a project module or a revision, or calls an Alembic command | Studio uses `revision_text` (a string; Tabrion reads no project) and writes the file itself; no `alembic.command`, no `MigrationContext` that runs revisions, no import of anything in the project; the head comes from the 1.0 migration status with its code-trust rule | STU-XSD-3: a planted `env.py`, revision and package `__init__.py` that would leave a mark are never run, in-process and in the worker; a static check that the area's modules do not import `alembic.command` or `alembic.runtime.environment` |
| TX-20 | T | **Revision file write abuse:** a file written outside the revisions folder (a `..`, a link, an absolute name), an existing revision overwritten, a revision written into an untrusted project, code injected through a table name or the message | Project trust (D-01) required; the folder from the migration status, resolved without links and inside the trusted project; Studio makes the name from a generated id `[0-9a-f]{12}` and a slug `[a-z0-9_]{1,40}`; exclusive create, no-follow, no overwrite; the text's identifiers and message are literals (tabrion K-3, I-124 h) | STU-XSD-3: tests for a link as the folder or the file, an existing name, an untrusted project, a message and table names with `"""`, `\`, line breaks and NUL (checked with `ast.parse`: literals only) |
| TX-21 | T | **Plan swap or a plan from outside (I-124 a, N-2):** the plan that runs is not the one the user saw; a plan or target model is sent by the page, read from a file or an import; the risk classes of an old plan are trusted | Drafts and plans are server-side only; `from_json` never on outside input; at the run the server plans again from a fresh snapshot and the server-side target, compares the new `plan_hash` with the hash it recorded at the confirmation, and takes the classes to allow from the new plan; the STU-XW-3 binding (session, connection, unlock, one run) holds | STU-CR-5: a changed draft after the confirmation, a plan JSON in the request ignored or refused, a hash from another session, a reused hash, a database change that changes the plan, a new plan with a class the user did not confirm |
| TX-22 | T | **Drift and blind spots (D-5):** the database changes between preview and run in a way the fingerprint does not see (a trigger, policy, function, row security, a table feature), or after the drift check | Level-2 fingerprint with triggers, policies, row security and their directly referenced functions (D-5.1); feature markers refuse a change of an affected table (D-5.2, D-5.3); the executor locks the targets under `lock_timeout` and repeats the drift check in the run's transaction (D-5.4); the remaining gaps (nested function calls, a function replaced after the check, I-124 j 3) are named in the preview | STU-CR-5 with TAB-XW-5: drift tests for a column, a trigger, a policy, a function body and row security changed after the preview; a refused partitioned table, domain column, rule, foreign table, event trigger, C trigger function |
| TX-23 | T/D | **Data loss by a destructive step (TM-DB-08):** a dropped column or table, a narrowed type, `NOT NULL` on existing rows, a SQLite rebuild; a function body that names a dropped column breaks later; a rebuilt SQLite table gets new `rowid` values | Risk classes from the new plan; typed table names for `destructive` and `rebuild`, the step named for `data_changing` (D-10); the destructive action never has the default focus; a backup before every `destructive`, `rebuild` or `data_changing` run, a failed backup stops it, opt-out only on `dev` with a typed confirmation; the preview warns about PL/pgSQL references for every drop and type change (I-124 j 2) and about `rowid` (j 4); PROD locked | STU-CR-5: a negative test per destructive class (no confirmation, a wrong name, default focus), backup taken and the run stopped on a failed backup, the warnings present for each operation; STU-CR-5 follow-up: on PostgreSQL the verified `pg_dump` archive of STU-ADR-0008 section 3 before the run, which continues only when it succeeded (no tools, an unsafe tool or configuration, a failing tool, a changed profile or Windows refuse it; `test_schema_designer_pg_backup.py`, `test_schema_designer_backup_postgres.py`) |
| TX-24 | T | **SQLite rebuild hazards:** with foreign keys on, `DROP TABLE` deletes child rows through `ON DELETE CASCADE`; triggers or views lost; hostile schema text from `sqlite_schema` (a second statement, another object's name) runs | The executor turns foreign keys off before `BEGIN`, runs `foreign_key_check` and `integrity_check` before the commit; triggers re-created from `sqlite_schema` as one statement each under the K-1 checks at plan time and again at execution, on the write connection with `trusted_schema = OFF`, the authorizer and defensive mode (TX-16); dependent views and triggers refuse a drop (I-124 e); views checked with `SELECT * FROM <view> LIMIT 0` | STU-CR-5 with TAB-XW-5: "foreign keys on: refused, no row lost"; a hostile `writable_schema` file; a rebuild that keeps rows, triggers, views and referencing tables |
| TX-25 | D | **Locks and partial changes on PostgreSQL:** a step holds a lock that blocks the application's writes for long; a non-transactional step commits what ran before it and a later failure leaves the database half changed | `lock_timeout` (5 s) on every run; the `locking` notice; no non-transactional step in direct execution (revision output only, as a revision of its own, I-124 j 5) | STU-CR-5: a run that waits for a held lock fails after the timeout with nothing changed; a plan with `CONCURRENTLY` refused for direct execution |
| TX-26 | T | **A plan made for another engine version:** without `server_version` a SQLite drop is planned as a rebuild, or the plan does not match what the engine does | `server_version` always passed: SQLite `sqlite_version_info` of the write connection's library, PostgreSQL `server_version_num` from the write connection | STU-CR-4/5: a test that every `plan` call passes a tuple (a spy on the call) |
| TX-27 | I | **Values or secrets in plans, revisions, logs:** a default or check text, a statement or a secret reaches the log, the audit log, the history or a diagnostics report; a revision holds a secret | The preview shows the plan's SQL to the user only; logs and audit records hold plan hash, table names, risk classes, counts; revisions hold the plan's statements and fingerprints only (no connection data); errors are catalog problems and `db.<kind>` from `safe_message` | STU-CR-5, STU-XSD-3: canaries (a marked default text and a marked secret) absent from logs, audit log, history, diagnostics and the revision's non-SQL parts |
| TX-28 | I/T | **Untrusted schema input in the comparison (STU-XSD-2):** a snapshot file is crafted (huge, deep, odd names) or used as the target of a plan that runs | `SchemaSnapshot.from_json` (Tabrion's parser: format, shape and level checks, a recursion error refused) after Studio's own caps (the body cap of the route, 2 MiB of text, level 2 only, then the draft's limits of tables, columns and elements); a loaded snapshot is shown and compared only, never the source or target of a plan (also not of the comparison's preview, which is never kept or run) or of a revision; at most one side of a comparison is a file; names escaped in every display (React text, invisible characters shown) | STU-XSD-2 (built): `test_schema_designer_compare.py`: an oversized, an over-limit, a malformed, a too deep and a level-1 snapshot refused; a spy shows no plan is made from or to a file; the only `from_json` of the area is in `compare.py` (static) |
| TX-29 | S/T | **Creating a database or file (STU-CR-2, STU-CR-3):** a SQLite file created through a link or in a folder others may write; a PostgreSQL database created with an owner the admin role should not set; a driver error with server text shown | SQLite through tabrion's private-file creation (0600, no-follow); PostgreSQL through `create_database` (name and owner checked, `CREATEDB` and the `SET` option checked first, I-190, I-191) on the Schema Designer's write profile with `CREATEDB` (no admin connection in this area; a separate DEV/TEST role, a `CREATEDB` warning elsewhere, keep the server up to date; owner confirmation at G-S3), DEV and TEST only, PROD locked; driver errors only through `safe_message` | STU-CR-2/3: link and folder-mode tests; a PostgreSQL owner the write role cannot become; a duplicate database answered without server text |
| TX-30 | E/I | **Runtime role and profile export (STU-CR-6):** the wizard grants more than the runtime role needs or uses an over-privileged admin profile (the wizard needs the `database_manager` area and G-S4, ADR 0007 section 12); the export leaks a write or admin secret or cuts a multi-host profile | The role rows TX-40..TX-44 apply; export of single-host profiles only, a multi-host profile refused (W-10, TB-148); write and admin secrets never exported (N-5) | STU-CR-6: the role tests of G-S4 that apply; a multi-host export refused; a canary secret absent from the export |
| TX-31 | T | **The graphical mode bypasses a check (STU-XSD-1):** the canvas sends edits the table view would refuse | The canvas and the table view send the same edit operations to the same server routes; the server checks every edit; the graphical mode has no route of its own | STU-XSD-1: the server refuses each invalid edit whichever view sent it; axe and the table alternative (08 section 4.7) |

## Residual risks

1. **The database role is the boundary** (as TX-16 and residual risk 2 of the write threat model): a write role
   with DDL rights can change the schema outside Studio's plan. An over-privileged write role is refused at the
   unlock and again at the start of each run (G-S2 decision 1A, GS3-28).
2. **Function drift (D-5.1, D-5.4):** functions called from a trigger or policy function are not in the
   fingerprint, and a function replaced after the drift check is not seen; a PL/pgSQL body that names a dropped
   column breaks only when it runs. The preview says so.
3. **A revision does not check its source** (I-124 j 5): applied to another database or much later, it runs the
   previewed statements against a schema Studio never saw. The preview shows the fingerprint and the condition.
4. **The environment is the user's label** (residual risk 1 of the write threat model): a production database saved
   as `dev` can be changed directly.

## Acceptance (planning document 15 sections 9.4 and 10, G-S3)

| Criterion | Where |
|---|---|
| Flag `schema_designer` off by default, not switchable from the page; "flag off" test per route (W-9); no code path for PROD | STU-ADR-0006, TX-01..TX-03, TX-18 |
| TM-DB-08 (the designer destroys data) | TX-23 |
| Plan binding (hash and fingerprint), drift check right before the run with abort, preview, explicit confirmation; tests for plan swap and drift | TX-21, TX-22 |
| Alembic project: revision text only, after project trust; no `env.py`, no project code, no `alembic upgrade` (negative test); direct execution only DEV/TEST without migration management (negative tests: Alembic project, version table, PROD) | TX-18..TX-20; STU-ADR-0007 section 5 |
| SQLite rebuild with `foreign_key_check` and `integrity_check` (R-CR-5) | TX-24 |
| D-5 implemented in Tabrion and reviewed (tabrion ADR 0015 section 10) | TX-22; G-S3 precondition |
| Export and import of single-host profiles only (W-10) | TX-30 |
