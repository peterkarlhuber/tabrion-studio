# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- STU-CR-7 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no default
  changes): the Schema Designer's focus, guide and browser tests. After a plan is confirmed the focus moves to the
  confirmation's status, and after a run or dry run to its outcome, never to Run (GS3-14); the guide's "Its limits at
  a glance" states revision only for Alembic projects, the PROD lock, direct runs only on DEV and TEST without
  migration management, the backup rule with what `pg_dump` leaves out, the preview warnings and that the designer
  changes no role (GS3-24, doc 15 section 3.1). New e2e of the read-only comparison in both themes with axe (three
  browsers in CI). From the review of #148: the diagram's node ids are the boxes' indexes, mapped back to the table
  names, so a table name with `"` or `\` no longer breaks drawing (I-1), and the diagram draws at most 300 foreign
  keys as lines with a note when the bound applies (I-2; `buildScene` takes the bound, the explorer is unchanged).
- G-S2 follow-up 3A (before G-R; the write flags stay off by default): the audit log's anchor in the OS keyring.
  Studio keeps the last record's number and hash in the keyring (one entry per audit folder, nothing secret) and
  moves it on with every record. A log that no longer reaches it at start (the folder removed or moved aside, cut,
  or rebuilt) is recorded as an `audit.reset` record (`anchor_seq`, `anchor_hash`, `found`) and writes go on;
  `GET /api/write/audit` reports `anchor` (`ok`, `none`, `unavailable`, `failed`, `reset`) and `resets`, and the
  write profile section says that the log was found missing or reset (en, de). Without a usable keyring the log
  works as before and says so. The security guide and the write threat model (TX-12, residual risk 3) are updated.
  The Database Manager guide now says to leave `createrole_self_grant` unset for the admin role: with `set,inherit`
  the admin role reaches every role it creates, and Studio refuses later changes to them (`own_role_reached`).
- STU-XSD-1 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no default
  changes): the Schema Designer's graphical mode. "Show the draft as: Diagram" draws the draft as an ER diagram with
  each table's status (the explorer's layout and @xyflow/react, no new dependency, loaded only when shown); the table
  view stays the default and the accessible alternative. Choosing a table shows its card from the table view below
  the diagram (from the keyboard the card takes the focus); a line drawn from a column to a column fills in the
  table's "New constraint" form, which is sent only when applied. The diagram has no route or form of its own: every
  edit is the table view's operation on the same route, checked by the server in the same way (GS3-23, TX-31). English
  and German texts; the guide's "The diagram" section.
- STU-XSD-3 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no default
  changes): the Schema Designer writes a previewed plan as an Alembic revision into the trusted project, `POST
  /api/write/schema-designer/revision` with the plan hash and a message. The server plans again from a fresh snapshot
  and compares the hash, reads the single head through the migration status (code trust, the 1.0 migration worker),
  gets the text from `tabrion.plan.revision_text`, checks that it parses, and creates
  `<script_location>/versions/<12 hex>_<slug>.py` exclusively, never through a link and never over a file. Only for a
  DEV or TEST unlock whose database no PROD profile names (review I-1 of #144: the Database Manager's PROD-alias
  matching, now shared in `tabrion_studio.write.prod_targets`, no behavior change there), a project whose profiles
  (D-01) and Alembic code are trusted, a plan with a step, and not Windows (review L-1 of #146: without `dir_fd` the
  folder check and the create could be split by a junction swap; reason `platform`); `env.py`,
  a project module, the new revision and Alembic commands never run, and nothing runs on the database. The preview
  says whether a revision may be written and that the revision does not check its source fingerprint (I-124 j 5).
  Audit `run.start`/`run.result` with action `schema_designer.revision`. New problems
  `studio.schema_revision_not_allowed`, `schema_revision_invalid`, `schema_revision_head_unknown`,
  `schema_revision_folder`, `schema_revision_exists` and `schema_revision_failed`; the "Write as an Alembic revision"
  part of the preview in English and German; the guide's "Write an Alembic revision" section.
- Tabrion repin (tabrion G-T2 C-5): pins tabrion 0.3.0 at its release commit `7488c76` (the merge commit of
  tabrion #95, not tagged yet, no PyPI). It changes only the version from `0.3.0.dev0` to `0.3.0`; the API Studio
  uses is the one of `bfba25e`. The range `>=0.2,<0.4` stays. No behavior change; the flags stay off.
- STU-XSD-2 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no default
  changes): the Schema Designer's read-only schema comparison, `POST /api/write/schema-designer/compare`. Each side is a
  schema read now on an open read-only connection (a PROD connection is compared read-only too), this session's draft,
  or the text of a level-2 snapshot file (at most one side, at most 2 MiB, only compared and shown). The answer lists
  tabrion's differences with their `breaking` flag, both fingerprints (triggers, policies and row security included)
  and each side's feature markers (domains, partitions, rules, SQLite table options; an unknown one is shown as
  unknown), with a verdict that fails closed (`inconclusive` when a marker hides part of the schema). On request it
  previews the plan from the compared side to the reference, never kept or run (no plan hash), only for a DEV or TEST
  connection of the user's own profiles and never from or to a file or for PROD. New problems
  `studio.schema_compare_invalid`, `schema_compare_schema`, `schema_snapshot_invalid`, `schema_snapshot_too_large`,
  `schema_compare_mismatch` and `schema_compare_plan_not_allowed` with `compare.*` issues; the "Schema comparison"
  section on the Connections page in English and German; the guide's "Compare schemas" section. The preview
  warnings moved from the executor module to the draft module (no behavior change).

- STU-XDM-4, PostgreSQL role management (G-S4 not granted; the `database_manager` flag stays off by default):
  `POST /api/write/database-manager/roles`, `.../roles/preview` and `.../roles/apply` create, alter and drop roles,
  change memberships and grant or revoke privileges through tabrion's stable role DDL, only through the admin profile
  of a PostgreSQL 16 or later DEV or TEST connection, never on a server a PROD profile names (also under an alias).
  Never SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION, an ADMIN or GRANT option or TRIGGER; memberships in predefined,
  server-file or dangerous roles and groups with K-2 grants in any reachable database refused; Studio's own roles
  never changed; every change previewed, bound to the preview and confirmed by the typed name; the role read again
  after the commit and any difference shown; names escaped and hidden-name roles refused; passwords only to Tabrion.
  Audit `dbm.role.<action>` with the new fields `member`, `options_set`, `password_set`, `on`, `privileges`,
  `grant_objects` and `differences`. New problems `studio.dbm_role_*` with `role.*` issues. The page's role part in
  English and German; the guide, ADR 0008, the threat model and the G-S4 evidence updated. The checks are shared
  with the STU-CR-6 runtime-role wizard. Review of #149: a role that one of Studio's own roles reaches through
  INHERIT or SET (a group above a profile's user) is refused for every action but create (`role.own_role_reached`,
  L-1); Studio's own roles include the OS user for a profile without a user and the roles of the open connections,
  and every role change is refused when that user cannot be known (`role.own_role_unknown`, I-2); the K-2 residual
  window is documented (I-1).
- STU-XDM-3, delete a database (G-S4 not granted; the `database_manager` flag stays off by default): `POST
  /api/write/database-manager/delete/preview` and `.../delete` delete another DEV or TEST PostgreSQL database of the
  unlocked server (`tabrion.postgres.drop_database`) or the connection's own SQLite file
  (`tabrion.sqlite.delete_database_file`), after a verified backup (origin `pre_delete`, no opt-out) and the exact name
  typed. Never the maintenance or template databases, the connected database, or a target a PROD profile names;
  SQLite refused on Windows and in unsafe or Studio's own folders. `force` only while other sessions exist on
  PostgreSQL 13 or later, with its own typed confirmation. Audit `dbm.delete` with `force` and `sessions`. The page's
  delete part in English and German; the guide, ADR 0008, the threat model and the G-S4 evidence updated.
  Owner's condition of G-S4: on Windows a SQLite restore into a new file is now refused (`restore.platform`), as
  STU-CR-2 refuses creating one, until a reviewed check of the folder's access list exists. Review L-1 of PR #140:
  a PostgreSQL delete also needs the backup's level-2 fingerprint (the same before and after the dump); a schema
  change during the dump or an unreadable fingerprint refuses it with `studio.dbm_delete_backup_unverified`.
- Tabrion repin: pins tabrion at the merge commit of tabrion #94 (`bfba25e`), which makes every remaining CR-DBX name
  Studio uses stable (`tabrion.schema`, `tabrion.ddl`, `tabrion.plan` with its errors,
  `tabrion.postgres.create_database` and `drop_database`, `tabrion.sqlite.restore` and `delete_database_file`,
  `tabrion.errors.DatabaseInUse`). `plan` now takes `current` and `target`, and `sqlite.restore` `source` and
  `target`, by keyword only; the Schema Designer and the Database Manager pass them by keyword. No behavior change;
  the flags stay off.
- CI: every other security leg of `backend test` (Python 3.11 to 3.14 on Linux, macOS) runs in two shards too, with
  the same complementary ignore globs as Windows (`backend test (<leg>, security)` without
  `tests/security/test_[r-z]*`, `backend test (<leg>, security 2 of 2)` with only those), because the Python 3.12 leg
  reached 19 to 20 of its 20 minutes and was cancelled at 96 % on #143; every test still runs exactly once, the
  timeouts are unchanged, and the five new checks are in `.github/ci-manifest.json`.
- CI: the Windows security leg of `backend test` runs in two shards with complementary ignore globs
  (`backend test (windows-latest, security)` without `tests/security/test_[r-z]*`, `backend test (windows-latest,
  security 2 of 2)` with only those), because it took 17 to 20 of its 20 minutes; every test still runs exactly once,
  the timeouts are unchanged, and the new check is in `.github/ci-manifest.json`.
- Review notes of PR #137 (STU-CR-5 follow-up; G-S3 not granted, flags stay off): the Schema Designer guide and the
  confirmation dialog (English and German) say that the PostgreSQL backup dumps the whole database, that it fails
  when the write role cannot read every table, and that the role must not get more rights for it (I-1). While a run
  and its backup are under way, the run form shows a status line, is marked busy and disables its buttons (I-2). CI:
  the `security tests` job runs in two shards (`security tests`, `security tests (2 of 2)`) with complementary ignore
  globs, so every test still runs once within the unchanged 15-minute limit; the new check is mandatory in
  `.github/ci-manifest.json`.
- STU-CR-5 follow-up (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no
  default changes, PROD stays locked): the Schema Designer backs up a PostgreSQL database before a run with a
  `destructive`, `rebuild` or `data_changing` step on DEV or TEST, through the verified `pg_dump` runner of STU-XDM-2
  (`tabrion_studio.write.pgtools`: the tools only from `database-tools.toml` and checked again, a clean environment,
  the password only in a temporary passfile, the archive kept only after `pg_restore --list` reads it). The archive and
  its description go to `backups/schema-designer` (0700, files 0600); the run continues only when the backup
  succeeded and is refused otherwise (`studio.backup_tool_unavailable`, `studio.backup_tool_failed`,
  `studio.write_target_changed`, `studio.schema_backup_failed`), with nothing changed and `run.result` recording the
  code. The tool's credentials are read only while the profiles are those the unlock checked. Without both tools
  configured, and on Windows, such a plan stays refused (`backup_unavailable`). No opt-out on PostgreSQL; a dry run
  makes no backup; the confirmation, the plan binding, the drift check after the locks and the limits are unchanged.
  The confirmation dialog says so (English and German). New tests `test_schema_designer_pg_backup.py` and
  `test_schema_designer_backup_postgres.py`.
- STU-XDM-2 (in development, locked by G-S4; G-S4 not granted, the `database_manager` flag stays off, no default
  changes): the Database Manager's backup and restore. A verified runner for `pg_dump` and `pg_restore`
  (`tabrion_studio.write.pgtools`, reusable by the Schema Designer later): paths only from the owner's
  `database-tools.toml` next to `write-areas.toml` (absolute, regular file, no link, owned by the user or root, not
  writable by others, the right program, PostgreSQL 16 or newer and at least the server's and the archive's version),
  an argument list without a shell, an environment built from nothing, the password only in a temporary `0600`
  passfile, time limits that end the tool's process group, `stderr` never logged. Backups go to
  `backups/database-manager` (0700, files 0600, never overwritten) with a description (engine, server and tool
  version, size, SHA-256, fingerprint, extensions, and that a `pg_dump` archive holds no global roles); an archive is
  kept only after `pg_restore --list` reads it. Restore only from that folder with a matching SHA-256: PostgreSQL only
  into a new database, run as a separate lean restore profile (no CREATEROLE, CREATEDB, SUPERUSER, BYPASSRLS,
  REPLICATION or predefined role); SQLite into a new file or over the DEV or TEST file after its own backup, with
  Studio's connections to it closed first; never into a target any PROD profile names (the server compared by its
  `system_identifier` or its resolved addresses). Routes `GET /api/write/database-manager/backups`, `POST .../backup`,
  `.../restore/preview` and `.../restore` (405 and nothing imported with the flag off); every action needs the admin
  unlock, checks the admin role again (`studio.admin_over_privileged`) and is audited (`run.start`, `run.result`).
  Thirteen new problems (`studio.backup_tool_*`, `studio.dbm_*`, `studio.admin_over_privileged`); the page section
  (English and German); guide page "Database Manager". The conditions of the STU-XDM-1 review (M-1, L-1..L-3, A-1)
  are folded into STU-ADR-0008, the threat model and the G-S4 definition. New tests `test_dbm_backup_restore.py`,
  `test_dbm_tools.py`, `test_dbm_restore_roundtrip.py`, `DatabaseManager.test.tsx` and
  `e2e/database-manager-backend.spec.ts`. Following the PR #136 review, every folder above a tool and the folder of a new
  SQLite file follow the Schema Designer's folder rules (shared in `tabrion_studio.write.folders`), and Studio's own
  folders are compared by device and inode. On Windows the target folder's access list is not checked (a residual
  recorded with TD-003).

- G-S2 follow-up 7A: pins tabrion at the merge commit of tabrion #93 (`3520b21`), which makes the names the Data
  Editor uses stable (`tabrion.rowedit.insert_row`, `update_row`, `delete_row`, `tabrion.errors.RowCountMismatch`,
  `Transaction.execute_checked`). `update_row` and `delete_row` now take `old` and `new` by keyword only; the Data
  Editor passes them by keyword. No behavior change; the `data_editor` flag stays off.

- STU-CR-5 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no default
  changes): the Schema Designer runs a confirmed plan on a DEV or TEST database. The preview keeps the plan on the
  server for 5 minutes, bound to the browser tab, connection and unlock, and shows the warnings of STU-ADR-0007
  section 8 (objects not modelled, PostgreSQL dependent code and function drift, SQLite `rowid`, fingerprint gaps,
  locks), why a plan is not run directly and what its confirmation needs. `POST
  /api/write/schema-designer/plan/confirm` takes the typed write profile and table names, each data-changing step,
  the lock acknowledgement and the backup choice; `POST .../plan/run` runs it once (a dry run only on request,
  rolled back). Studio plans again from a fresh snapshot and compares the hash and risk classes, then calls
  `tabrion.plan.execute_plan` with the environment from the stored profile, the server's version,
  `concurrent_indexes=False` and a 5 s lock timeout. Direct execution is refused for a migration version table
  (tabrion's markers, Levuvo's), an `alembic.ini` in the project, a non-transactional step and outside DEV and TEST;
  on PostgreSQL the write role's rights are checked again on the run's session. A SQLite plan with a destructive,
  rebuild or data-changing step first gets a backup with `tabrion.sqlite.backup` into `backups/schema-designer`
  (0700, files 0600; left out on DEV only with the typed file name); such a PostgreSQL plan is refused until a
  verified `pg_dump` runner exists. Each run is audited (`run.start`, `run.result`). Six new problems
  `studio.schema_run_not_allowed`, `schema_backup_required`, `schema_backup_failed`, `schema_run_check_failed`,
  `schema_run_outcome_unknown` and `schema_run_failed`; a refusal names its table by its position on the page only
  (English and German). New tests `test_schema_designer_execution.py` and `test_schema_designer_execution_postgres.py`;
  guide section "Run a plan".

- STU-CR-4 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off, no default
  changes): the Schema Designer's table view on the Connections page. `POST`/`DELETE
  /api/write/schema-designer/draft`, `POST .../draft/view`, `.../draft/edits` and `.../draft/preview` keep one draft
  per browser tab on the server only, bound to the connection and the unlock of a non-PROD write profile, started from
  a `tabrion.schema` level-2 snapshot of one schema (SQLite `main`, PostgreSQL any non-system schema). The page sends
  one change at a time (add, rename, drop a table; add, change, rename, drop a column; add and drop keys,
  constraints and indexes); the server checks names, references and limits, plans each change with tabrion and keeps
  it only when it can be planned. The preview plans from a fresh snapshot to the draft and shows each step with its
  risk classes, transaction and SQL and the plan hash; nothing is run and no route runs DDL. Every `plan` call passes
  the write connection's `server_version` (GS3-13); no request can carry a model or plan and `from_json` is never
  called (GS3-09). Seven new problems `studio.schema_draft_*`, `studio.schema_edit_invalid`,
  `studio.schema_plan_refused` and `studio.schema_model_invalid` (English and German). New tests in
  `test_schema_designer_draft.py` and `test_schema_designer_draft_postgres.py`, the kill-switch sweep and
  `test_every_route.py` extended; guide page "Schema Designer (in development)".

- G-S2 follow-up 6A for the Data Editor (behind the `data_editor` flag, which stays off by default; no default
  changes): a bytes value (`bytea`, a SQLite `BLOB`) is replaced by the content of a file the user picks, at most
  64 KiB (`value.too_large` on the page, before the file is read, and on the server); it is shown by its size and
  never typed, logged or recorded. A change the database refuses for a constraint (unique or primary key, foreign
  key, NOT NULL, CHECK, exclusion) rolls back the whole changeset as `400 studio.data_editor_constraint`, whose issue
  places it at its cell where tabrion's classification names a single column of the table, else at its row
  (`changes.<change>[.<column>]`, `constraint.<kind>`; positions only, never a value or a constraint name). The grid
  loads more than the first 100 rows ("Load 100 more rows", up to 1,000): pages are appended, so a pending change
  stays bound to its row. `POST /api/write/plans` takes a body up to the plan framework's own 256 KiB cap (it was
  held to the default 16 KiB); with every flag off it still answers 405.

- STU-CR-2 and STU-CR-3 follow-up (review conditions of PR #128, all before G-S3; still in development, locked by
  G-S3, the `schema_designer` flag stays off, no default changes). SQLite creation: the file is created relative to
  the folder Studio checked and holds open (`O_CREAT|O_EXCL|O_NOFOLLOW`, `0600`), and before and after the header is
  written Studio checks that the path still leads to that folder and to the file it created (device and inode);
  every folder above the target folder must belong to root or the user and not be writable by group or others
  unless it has the sticky bit (like OpenSSH's `StrictModes`); Studio's own folders are compared by device and inode
  (the folder and every folder above it), and the log folder and the project's `.tabrion` folder are added; a space
  other than U+0020 (Unicode `Zs`) and the line and paragraph separators are refused in file, database and owner
  names; on Windows no SQLite file is created (new problem `studio.schema_create_platform`, English and German),
  because Studio does not check a folder's access control list there. PostgreSQL: the warning finding
  `admin.createdb` (never a refusal) on a write unlock whose role has `CREATEDB` while the Data Editor's flag is on,
  and on any unlock outside DEV and TEST; the guides recommend a separate `CREATEDB` role for the Schema Designer on
  DEV and TEST only and an up-to-date server. New regression tests in `test_schema_designer_create.py`,
  `test_schema_designer_create_postgres.py` and `test_write_privileges.py`.

- Pins tabrion 0.3.0.dev0 at the merge commit of tabrion #92 (`71370a6`: adds TAB-XW-5 `execute_plan`, which
  STU-CR-5 needs, and raises tabrion's psycopg floor to 3.2.3; the Studio lock already has psycopg 3.3.9). Studio uses
  none of the new names yet.

- STU-ADR-0007 accepted after the independent review of STU-CR-1 (documentation only; G-S3 not granted, no default
  changes). The record now names the executor signature Studio uses (`execute_plan` with the recorded plan hash,
  the confirmed source fingerprint, `environment`, `allow`, `server_version` and `concurrent_indexes=False`; Studio's
  own re-plan is only a pre-check), states that database creation runs on the Schema Designer's write profile with
  `CREATEDB` (no admin connection; conditions and an owner confirmation at G-S3), that the runtime-role wizard needs
  the `database_manager` area and G-S4, and that a dry run happens only on the user's explicit request with a notice
  of its locks and consumed sequence values (new G-S3 row GS3-29). The Schema Designer threat model (TX-29, TX-30,
  residual risk 1) is aligned.

- STU-CR-2 and STU-CR-3 (in development, locked by G-S3; G-S3 not granted, the `schema_designer` flag stays off,
  no default changes): the "New database" wizard on the Connections page. `POST
  /api/write/schema-designer/create/sqlite` creates a new, empty SQLite file through tabrion's
  `create_private_file` (mode `0600`, never through a link, never over an existing file or its `-wal`, `-shm` or
  `-journal` side files, only in a folder of the user's that others cannot write to and that is not Studio's own,
  with an absolute path ending in `.db`, `.sqlite` or `.sqlite3`; confirmed by the typed file name).
  `POST /api/write/schema-designer/create/postgresql` creates a database through tabrion's `create_database` on the
  Schema Designer's unlocked write profile of a `DEV` or `TEST` connection (the stored environment re-checked, PROD
  refused), with owner, template, encoding and locale, a duplicate check before the statement and the duplicate race
  mapped, `CREATEDB` and owner refusals as catalog problems with guided steps (no SQL), driver errors only as `db.*`
  through `safe_message`. Ten new `studio.schema_*` problems in English and German; `run.start` and `run.result`
  audit records without values or folders. With the flag off both routes answer `405` and import nothing of the
  area (kill-switch sweep extended). G-S2 decision 1A applies to the wizard's unlock and is checked again at the start
  of each PostgreSQL creation on the session that runs it (a hit refuses and ends the unlock). The new profile can be saved from the wizard. New tests: security (flag off,
  PROD, links, folder modes, canaries), PostgreSQL 16, vitest with axe, and Playwright against a real server in light
  and dark. User guide page "New database (in development)".

- G-S2 granted by the owner on 2026-11-15 with decisions 1A to 7A, for local use on DEV, TEST and STAGE databases
  only: PROD stays technically write-locked, every write area stays off by default and nothing turns one on
  automatically; G-S3, G-S4, H-2 and G-R stay open, nothing is tagged or released. Binding condition: the
  `data_editor` flag is not switched on before decision 1A (below) is implemented, independently reviewed, merged
  and green in CI. The decisions and their follow-ups with deadlines are recorded in `docs/TODO.md`
  ("G-S2 readiness") and `docs/architecture/security-review-prep.md`; the security guide states that the audit
  log's retention is unlimited (decision 3A).

- STU-CR-1 and STU-XDM-1 (documentation only; G-S3 and G-S4 not granted, the `schema_designer` and
  `database_manager` flags stay off, no code of either area exists, no default changes):
  STU-ADR-0007 for the Schema Designer (plans and target models server-side only, a new plan before every run
  compared with the confirmed hash, risk classes from the new plan, `server_version` always passed, direct DDL only on
  DEV and TEST databases without migration management, revision text only for Alembic projects and never `env.py`,
  a backup before destructive runs, the preview warnings tabrion asks for), STU-ADR-0008 for the Database Manager
  (`pg_dump` and `pg_restore` only from verified absolute paths with a clean environment and a temporary passfile,
  mandatory restore tests, "not a complete backup" stated, restore and delete only with a backup and the typed name
  first, `WITH (FORCE)` only explicit, roles only through tabrion's role DDL and a minimal admin profile, an admin
  profile with SUPERUSER, BYPASSRLS, REPLICATION, a server-access role or CREATEROLE before PostgreSQL 16 refused,
  the tabrion TB-149 obligations), the Schema Designer threat model (TX-18..TX-31), the Database Manager threat
  model (TX-32..TX-46), and the G-S3 and G-S4 definitions with testable checklists in the security review
  preparation.

- G-S2 preparation (G-S2 not granted; no default changes, the `data_editor` flag stays off): the readiness
  checklist for the activation gate of the shared write foundation and the Data Editor in
  `docs/architecture/security-review-prep.md` (what G-S2 asks, the evidence, the owner's open decisions) and the
  inventory of every "before G-S2" item in `docs/TODO.md`. A new end-to-end check against a real server: a two-step
  changeset whose second step conflicts rolls the first step back too. STU-ADR-0006 names the flag source and keys
  as built.

- STU-XDE-2 (in development, locked by G-S2): the user guide page "Data Editor (in development)" with the switch for
  development, the write profile, unlock, editing, NULL and the empty text, the formats, preview, confirmation,
  execution, conflicts and the limits (PROD refused, tables without a primary key, views and partitioned tables
  refused, incomparable column types refused, `json` compared as `jsonb`, triggers and rules a notice, rows only),
  linked from the guide index and the security guide. The `data_editor` flag stays off by default; no default
  changes. A table with triggers or rules gets a notice that names them, not a block (owner decision 2026-11-15),
  row security its own notice, and the grid a short help text; English and German. New E2E tests (the harness
  states and four more against a real server: NULL against the empty text, a trigger, the refused view and table,
  PROD), with axe in light and dark, English and German, run by CI in Chromium, Firefox and WebKit.

- STU-XDE-1 (in development, locked by G-S2): validation, type editors and a change overview for the Data Editor,
  behind the same `data_editor` flag of `write-areas.toml`, which stays off by default; no default changes. The
  table route sends each column's type (kind, NULL, whether an insert may leave it out, generated or identity
  `ALWAYS`, length, precision and scale, enum labels, arrays). The grid has an editor per type: a choice for boolean
  and enum, text fields with the format in the header for numbers, decimals, dates, times, timestamps with and
  without an offset and UUIDs, JSON and arrays as JSON, bytes and generated columns read only; NULL is a button and
  never the empty text. Every changed value is checked at its cell (type, length, range, NULL, enum labels, required
  columns of a new row) and the preview waits until none is left. The server checks every new value again before it
  makes the plan: `400 studio.data_editor_value_invalid` with `issues` (`changes.<change>.<column>` and a `value.*`
  code; no value, no column name). The change overview lists every changed row with its columns before and after, the
  new rows and the deleted rows, before the preview and in the review. Page and server share their check vectors.
  An insert names only the columns that were set: SQLAlchemy no longer fetches a key ahead from a sequence, which a
  table with an identity `ALWAYS` key refused. English and German. Threat TX-17 and STU-ADR-0006 "Values of the Data
  Editor".

- STU-026 (in development, locked by G-S2): the Data Editor behind the `data_editor` flag of `write-areas.toml`,
  which stays off by default; it is not usable or supported until G-S2 is granted. While the flag is on, the plan
  executor `data_editor.changeset` turns a changeset (rows changed, added, deleted) into plan steps; preview, typed
  confirmation and one run go through the audited write transaction, each step a tabrion `rowedit` statement run
  with `execute_checked(expect_rowcount=1)`. Every shown column is passed as the row's old value, so a row changed
  elsewhere (`400 studio.data_editor_conflict`) or another row count (`400 studio.data_editor_rowcount`) rolls the
  whole changeset back without echoing a value. Tables without a primary key (`studio.plan_target_no_primary_key`),
  views, partitioned tables and PROD are refused, and so is a PostgreSQL table with a column whose type has no
  proper equality (`xml`, `point`, `box`, a composite type, `json[]`: `400 studio.data_editor_column_incomparable`),
  because such a value cannot be compared as an old value. Text columns with their own collation are compared
  binary (review L-1 of PR #119). `POST /api/write/data-editor/table` describes the table; it answers
  405 and the executor is not loaded while the flag is off. The page shows an "Edit data" tab on a table only when
  the server answers that the flag is on (English and German); the changes stay in the page's memory and never reach
  the query history, the drafts or browser storage (N-3).

- STU-XW-4 (CR-DBX): `require_auth` for write and admin connections and the I-109 j confirmation of a write secret
  (D-15), with every write area still off. Every PostgreSQL write and admin connection, and the privilege check on
  it, sets libpq `require_auth=!password,!md5` whatever the profile's owner, so libpq refuses a server that asks for
  a clear-text or MD5 password; a foreign host (not loopback) that gets a password needs `scram-sha-256`. With libpq
  older than 16 (`psycopg.pq.version()`) no write or admin connection is opened and the unlock is refused before any
  secret is read (`405 studio.require_auth_unavailable`), closing L-2 of the PR #111 review. Studio reads the
  password of a PostgreSQL write or admin profile only after the user confirmed the pair of server (host, port,
  database, TLS mode, role) and secret reference: `POST /api/write/secret-confirmations` previews it from the user's
  file with a session-bound hash, `POST /api/write/secret-confirmations/confirm` takes the hash and the typed host,
  `DELETE /api/write/secret-confirmations` revokes it. The confirmed pair's SHA-256 is kept in
  `<state>/write-secret-confirmations.json` (0600); an unconfirmed or changed pair refuses the unlock
  (`400 studio.secret_unconfirmed`), and every use of an unlock, the plan confirmation and run included, checks it
  again. A foreign host needs `sslmode` `verify-ca` or `verify-full` (`400 studio.write_tls_required`). Confirming
  and revoking are audited (`secret.confirm`, `secret.unconfirm`: the pair's hash and the kind of reference, never
  the host or the reference). The write profile section has the confirmation dialog in English and German. The new
  routes answer 405 and the new module is not loaded while every flag is off.
- STU-XW-3 (CR-DBX): the plan, preview and confirmation framework (D-09, D-10), with every write area still off.
  A change runs only as a server-side plan: `POST /api/write/plans` builds it through a pluggable executor, reads
  the targets' fingerprint on the unlock's write connection (SQLite: file identity and the file's schema; PostgreSQL:
  each target table's columns, constraints, referencing foreign keys, indexes, triggers, rules and policies) and
  answers the preview: the SHA-256 plan hash, the targets with the triggers and rules on them (TX-16: the row limits
  cover only the statement itself), a step summary without values, risk notes and the text to type.
  `POST /api/write/plans/confirm` takes the hash and the typed text (the write profile's name; for a destructive plan
  the target's name); `POST /api/write/plans/execute` runs the confirmed plan once through the audited transaction
  with the plan hash in `run.start`, after re-checking the binding to session, connection and unlock (review N-2),
  the 5-minute expiry and the plan's integrity, and, inside the transaction just before the first step, the
  fingerprint (drift; on PostgreSQL again after `LOCK TABLE ... IN ROW EXCLUSIVE MODE`) and the SQLite file
  identity. A swapped, edited, replayed, expired or foreign plan and drift are refused with `studio.plan_*` (400),
  never echoing a value. The routes answer 405 and the plan module is not loaded while every flag is off. No executor
  is registered yet (the Data Editor's comes with STU-026 and tabrion TAB-XW-6), and there is no UI yet.
  Review of PR #115: the PostgreSQL fingerprint also covers the definitions (source, settings, owner, security
  mode, signature) of the functions the targets' triggers call and their defaults, constraints, policies, rules and
  index expressions depend on, so a `CREATE OR REPLACE FUNCTION` between preview and run is drift (M-1); partitioned
  tables and partitions are refused as targets (`studio.plan_target_partitioned`, N-1); `studio.plan_unknown` says
  the plan expired or was replaced (N-3).
- STU-XW-2 (CR-DBX): the audit log of write and admin actions, with every write area still off. While an area is on,
  Studio appends a record to `<state folder>/audit/audit.jsonl` (folder 0700, files 0600, never through a link, no
  handle kept open) for every unlock (also a refused one), lock (with its reason), write or admin profile create,
  change (with the coupling) and delete, secret store and remove, and write run (`run.start` before the transaction,
  `run.result` after it, with row counts and the plan hash). Records hold names, counts, codes and hashes only, by a
  field allowlist: never a value, parameter, statement, secret or database message (canary tests). Each record is
  chained by SHA-256 to the one before, and `head.json` anchors the last; the chain is verified at start and on every
  listing, and finds changed, removed, inserted, reordered and cut records. A log that fails is not appended to.
  No record before an unlock or a run, no unlock or run (`405 studio.audit_unavailable`); a record after a run that
  fails is kept as a pending marker (`audit/pending.json`), warned about and written later (review N-4). Rotation at
  4 MB renames to `audit-<first record>.jsonl` and never overwrites or deletes. New read-only route
  `GET /api/write/audit` (405 with every flag off); the audit module is not loaded while every flag is off. The write
  profile section shows the log's state in English and German. Several Studio processes on one state folder share
  the log safely: every write, head update, rotation and verification runs under a lock across processes
  (`audit/audit.lock`) and re-reads the chain's end first (review M-1 of PR #114); rotation never replaces a name
  (hard link); a name the log does not take refuses the run with `studio.audit_record_invalid`.
- STU-XW-1 (CR-DBX): the shared write foundation, with every write area off. One flag per area (`data_editor` G-S2,
  `schema_designer` G-S3, `database_manager` G-S4) in `write-areas.toml` in the configuration folder that the
  command line names (never an environment variable, a `.env` file, a project file, the settings API, a URL, a
  header or the page), read once at start with the checks of Studio's private files and logged per area. With every
  flag off each write route answers `405 studio.method_not_allowed` and the write code is not imported. Write and
  admin profiles (`[[write_profile]]`) only in the user's `profiles.toml`, refused in a project file, coupled to the
  user's own non-PROD read profiles; a coupled profile cannot become PROD and an environment change ends every
  unlock; creating one shows and requires the "no production write credentials" confirmation. Server-side unlock
  per page session with the typed name, 15 idle minutes, no PROD code path. SQLite write connection with
  `trusted_schema = OFF`, an authorizer, defensive mode (Python 3.12+; refused on 3.11) and a device and inode check;
  PostgreSQL write connection with the fixed `search_path` `pg_catalog, pg_temp`; admin privilege check with
  findings for SUPERUSER, BYPASSRLS, CREATEROLE and REPLICATION, direct or inherited. Write secrets never appear in
  a diagnosis report. Kill-switch proof tests in `tests/security/test_write_*.py`. New import-linter contract: the
  read paths never import `tabrion_studio.write`.
- STU-XW-1 follow-up (review of PR #111): while a write area is on, write and admin profiles are used only from the
  configuration folder that the command line names or the platform's folder, never from one that
  `TABRION_STUDIO_CONFIG_DIR` or `TABRION_STUDIO_STATE_DIR` chose; such a folder's write profiles are kept in the
  file but not listed, unlocked or created (`409 studio.write_profiles_ignored`), and the start logs
  `studio.write_profiles_ignored` (L-1). The SQLite write connection never creates a missing database file: it is
  refused before anything opens it, and each pool connection opens `file:<path>?mode=rw` and then tabrion's checked
  path with `create=False` (L-3). The privilege check of write and admin connections also reports `admin.broad:<role>`
  for membership in a predefined `pg_*` role, direct or inherited (`dangerous` for `pg_execute_server_program`,
  `pg_read_server_files` and `pg_write_server_files`), and `admin.createrole_pre16` (`dangerous`) for CREATEROLE
  before PostgreSQL 16, as tabrion ADR 0015 D-1 and D-2 (I-1).
- Docs: STU-XW-5 (CR-DBX), the first work package after the development start. STU-ADR-0006 supersedes
  STU-ADR-0003: one server-side flag and one gate per area (Data Editor G-S2, Schema Designer G-S3, Database Manager
  G-S4), each off by default and set only locally by the owner (never from the page, a project file, a URL, a header
  or a profile); G-CX is not an activation; a refused write answers 400 or 405 and only the write mode 403 (W-9),
  without echoing values or secrets; no code path writes to PROD (E-X7); separate write and admin profiles, the admin
  profile with minimal rights, a privilege check on connect and a finding on over-privilege, and Studio never sets
  up admin rights for itself (E-X8, E-X6); every kill-switch pull request proves default off and a "flag off"
  negative test per endpoint. New page `docs/architecture/write-threat-model.md` (TX-01..TX-16: flag tampering,
  ineffective kill switches, PROD lock bypass, profile confusion, unlock flow, CSRF, `require_auth` on import, plan
  swap, drift, structural read-only instead of a denylist, over-privileged admin profile, audit-log integrity, values
  in refusals, hostile database objects on the write path). After the security review of PR #109 (accepted with
  conditions): write and admin profiles and their couplings only in the user's own profile store, never from a
  project file (K-1); a fixed catalog-first `search_path` with qualified names on PostgreSQL, `trusted_schema = OFF`,
  the authorizer and defensive mode on the SQLite write connection, and a file identity check at unlock and before
  each run (K-2); the six review notes as requirements for STU-XW-1..4 and STU-026. The README and the security guide state that write features exist only behind default-off gates and
  stay non-goals for 1.0; `docs/TODO.md` marks STU-XW-5 in review and STU-XW-1..4 next. No code change.
- Docs: QA-S1 passed. The owner confirmed the manual QA-S1 acceptance of Studio 1.0 on 2026-11-14 16:34Z, on the QA
  kit v2 built from master `ab01ebb` with tabrion `52353da` (QA-S1 suite 38/38, PR #103; findings S1-09, S1-15 and
  S1-32 fixed in PR #104 and PR #106). Recorded in `docs/TODO.md` (TD-046 B-5, TD-060, STU-050, STU-054 done, TD-068
  to TD-070, the CR-DBX start condition S-1), `docs/architecture/ux-acceptance.md`,
  `docs/development/qa-s1-suite.md` and the release checklist (A-2). The TD-060 follow-ups stay open. Not a release:
  H-2 and G-R stay open, nothing is tagged or published. No code change.
- TD-068: a Playwright suite for the QA-S1 checklist (`frontend/qa-s1/`, one test per case S1-01..S1-38, its
  own config, a results file by case id); opt-in through `TABRION_STUDIO_E2E_PYTHON` and `QA_S1_DEMO_BUILDER`, not
  in CI. How to run it: `docs/development/qa-s1-suite.md`. QA-S1 acceptance stays manual.
- TD-066 (STU-055): the CI job `backend integration (PostgreSQL 17)` also runs the `pgvector` tests of the vector
  data view against a second service, `pgvector/pgvector:0.8.7-pg17-trixie` (PostgreSQL 17 with pgvector available
  but not created, pinned by digest), whose superuser is `TABRION_STUDIO_TEST_PGVECTOR`; the main `postgres:17.10`
  server stays without pgvector, so a missing extension is still tested. The job still fails on any skipped test. `docs/TODO.md` marks STU-055 done (PR #97, `41763cf`).
- STU-055: the **Vector data** view (`/vectors`, `GET /api/connections/{id}/vectors`), read-only for SQLite and
  PostgreSQL: vector columns and their types, vector indexes with method and state, embedding dimensions, the
  embedding model identities of Orkexa's metadata, bounded counts of stored embeddings, and the pgvector status
  (Tabrion's `extension_status`); sqlite-vec's status is reported as not determinable. A column is shown as
  ANN-indexed only with a valid HNSW, IVFFlat or DiskANN index; everything else as "exact search (no ANN index)".
  Hints for a missing extension, no ANN index, unreadable tables and incomplete metadata, in English and German.
  Texts from the database are cut to 64 KiB in SQL and `vec0` definitions are parsed in linear time (PR #97 review
  M-1, L-1); expression and partial indexes are marked.
  Design note `docs/architecture/vector-inspection.md`, guide page `docs/guide/vector-data.md`, follow-ups TD-066.
- Docs: CR-DBX conditional development start (owner, 2026-11-14 11:01Z, G-CX). STU-ADR-0005 gets an amendment,
  the README, `docs/adr/README.md` and the CR-DBX section of `docs/TODO.md` follow: development of Studio 1.1 to 1.3
  starts without a new approval once S-1 (Studio 1.0 complete, QA-S1 accepted), S-2 (security findings fixed,
  reviews complete), S-3 (Tabrion migration incl. W6 and pin cascades) and S-4 (mandatory CI green) are proven;
  H-2 and G-R are needed for public releases only; write and admin code stays behind default-off kill switches until
  G-S2, G-S3 or G-S4. No code change.
- Docs: change request CR-DBX (planning document 15; the owner accepted the planning on 2026-11-13, not the start of
  implementation). STU-ADR-0005 amends STU-ADR-0003: one server-side flag and one gate per area (Data Editor 1.1
  behind G-S2, Schema Designer 1.2 behind G-S3, Database Manager 1.3 behind G-S4), G-CX enables no write feature, a
  refused write answers 400/405 and only the write mode 403, PROD stays write-locked, separate write and admin
  profiles, revisions instead of migrations (never `env.py`), `pg_dump`/`pg_restore` only as verified system tools
  and not a complete cluster backup, PostgreSQL role management without SUPERUSER, BYPASSRLS, CREATEROLE or
  REPLICATION. `docs/TODO.md` gets a CR-DBX section (all blocked on the development-start approval) and no longer
  says that the Schema Designer is not planned; the README non-goals and `docs/architecture/migrations.md` say what
  1.0 excludes and that Studio never runs migrations. No code change.
- TD-031 (owner decision G-S1 2): the nightly job `newest dependencies (Python 3.11 and 3.14)` installs Studio and the
  pinned Tabrion without the hashed runtime lock, so with the newest releases inside the `pyproject.toml` ranges,
  prints `pip freeze`, and runs `pip check` and the backend tests. It is not a pull request check; a failure opens the
  "Nightly failed" issue.
- TD-061: the nightly collects the license records of fresh runtime environments on Linux (Python 3.13 and 3.11),
  macOS and Windows and runs `scripts/release_bom.py --strict` over them (job `release SBOM (strict)`, artifact
  `release-sbom` with the SBOM, the license report and the records).
- STU-016: G-S1 passed for master `9a5d4faa69f1c974999d258b36258fbc10b3323f`. The owner granted it on the condition
  that all 21 required CI jobs are green on that SHA, which was met on 2026-11-13 (run 43457105100); recorded in
  `docs/TODO.md`, `docs/architecture/security-review-prep.md`, `docs/guide/security.md`, the README and
  `docs/development/release-checklist.md` (H-1, G-1). G-R is **not** granted: the mandatory human security review
  before any public release remains, STU-026 stays locked behind G-S2, and nothing is tagged or published.
- STU-054 owner decisions (2026-11-13, all four recommendations accepted, option A), recorded in
  `docs/architecture/ux-acceptance.md` and TD-060: refused writes answer HTTP 400/405 rather than 403 everywhere (a
  decision on error semantics only, not a release or security acceptance of STU-026, which stays locked behind
  G-S2); D-07 after 1.0; tabrion's `TAB-*` finding texts stay English (marked `lang="en"`); no project chooser on
  the start page. TD-032's dependency is resolved by PR #85. STU-054 is not accepted yet: it waits on the manual
  QA-S1 session (TD-046 B-5).
- `src/i18n/coverage.test.ts` also reads the text on the right side of `&&` in the markup, and its self-test runs
  the same function as the real check (notes N-1 and N-2 of the PR #84 review); no hard-coded text was found.

- TD-036 (STU-025): `tabrion-studio diagnose [PROJECT] --profile PROFILE [--format markdown|json] [--output FILE
  [--force]] [--confirm-prod]` writes the diagnosis report of one profile from the command line, for scripts, CI and agents,
  without a running Studio. It is the report of the download (the same allowlist: no password, token, URL or DSN,
  host, port, user or database name, path or value). Project profiles count only after you trusted them in Studio;
  a PROD profile needs `--confirm-prod` on every run, and the connection checks it again on the profile it opens. No
  password prompt; refusals are Studio's fixed texts; exit codes 0 (written), 1 (not made), 2 (usage).
  `--output` writes a new file with mode 0600 in one step, replaces an existing file only with `--force`, never
  writes through a link in the file name and never touches the profile's database files; the JSON report escapes
  every non-ASCII character.
- TD-032: Studio's own findings and backup hints (`STU-DX-*`, `STU-BK-*`) show their details and remedies in German
  as well as English. The diagnostics route sends each one's facts as `params` (counts, setting names, the `sslmode`,
  the checks that did not run), and the page words them from its translations; tabrion's `TAB-*` texts stay English
  and marked as such. The JSON diagnosis report carries the `params` too.
- STU-054 (UX acceptance of Studio 1.0 without the Data Editor): the 1.0 section of
  `docs/architecture/ux-acceptance.md` maps every 1.0 criterion of the UX blueprint and of the backlog to its test or
  manual check. New `e2e/ux-acceptance-1-0.spec.ts` against a real server: the 1.0 tour (DDL, ER diagram, a query with
  a parameter, Format, the export dialog, the history, the migration status, a diagnosis) in English and German with
  axe in light and dark, and no English catalog text on the German pages; negative tests that writing stays locked
  (no data editing in the page, the access chip opens nothing, every write refused by the API on DEV and PROD with
  the database file unchanged, every changing route of the documented API on a reviewed list).
  `tests/security/test_every_route.py` asserts the same reviewed list over every route of the app, including those
  left out of the OpenAPI document (`POST /api/session`, `POST /api/control/{action}`). New `src/i18n/coverage.test.ts`: every
  key the page names exists, both plural forms in both languages, no visible text written into the markup. Open
  items in TD-060; the German diagnosis texts come with TD-032 (PR #85).
- The migration status shows the command that applies pending Alembic revisions (`alembic upgrade head`, or `heads`
  with more than one head) with **Copy command**, unless the database names a revision the project does not know
  (Alembic would stop there); Studio still runs no migration (TD-046 B-3).
- STU-028 (release preparation only; G-R not granted, nothing is tagged or published): the 1.0 release checklist
  `docs/development/release-checklist.md` (every step with its check and state; hard stops G-S1, the mandatory human
  security review before any public release, E-QA2, and G-R per publication), and `scripts/release_bom.py`, which
  writes the release SBOM (CycloneDX 1.5) and the third-party license report (with the psycopg LGPL-3.0 notice and
  the frontend licenses) from the runtime lock, the Tabrion pin and the shipped packages of the npm lockfile, with
  the STU-031 license policy (copyleft, unknown, missing or stale license records fail; `--strict` for the release
  run) and the same bytes for the same inputs; tested offline (`tests/supply_chain/test_release_bom.py`). The
  clean install of master `d72a14f` on Linux (`smoke_wheel.py`, `uv tool install`, and `pipx install` with pip as
  its installer and the Tabrion pin only) is recorded there. `scripts/collect_licenses.py` also records the Git
  commit of a VCS install (PEP 610 `direct_url.json`; never a URL with user information), and `release_bom.py`
  fails when a Tabrion record is not of the pinned commit. Every license record of a name, from every `--licenses`
  file, is judged, so the result does not depend on the order of the files. `check_licenses.py` gains the shared
  helpers `npm_license` and `npm_allowed`.
- TD-033 drafts part (security review F-5): the SQL editor's drafts (each tab's title, profile, text and script
  binding, and the open tab) are kept in Studio's state database `state.sqlite` (0600) behind the authenticated
  `GET`/`PUT /api/drafts` (session cookie and CSRF header; a tab with any field but the known ones is refused, so a
  draft has no place for a parameter value; at most 32 tabs, 512 K characters per text and 2 M in all,
  `studio.drafts_too_large`). Nothing is written to browser storage any more, and drafts now survive a restart on
  another port. Drafts an earlier version kept in `localStorage` for this project (its own key and the unscoped key)
  are moved to the server once and those keys are then removed; other projects' keys stay until that project's
  editor moves them, and the keys stay while the server cannot store them or not all of them fit the caps.
  `[history] retention_days` also applies to drafts (counted from the project's last save; opening the editor saves);
  `enabled = false` and "Delete history" leave them. A save or read that removed text checkpoints the write-ahead
  log; a lone surrogate is refused (`studio.invalid_request`; the page replaces it with U+FFFD), and a body with far
  more values than a drafts document is refused before it is parsed, by a scan that reads each byte once. Old drafts
  stay in browser storage unless both of this project's keys together fit the caps. The state database moves to schema version 2
  (table `drafts`; a file with data is copied 0600 before the migration and the copy removed after it; after a
  failed migration it stays until a later one succeeds; no downgrade to an older Studio).
- STU-020 (page): **Query history** (`/history`, ⌘Y / Ctrl+H): the statements you ran with their profile,
  environment, state and parameter names, never the values; filter by profile, search, "Open in editor" (runs
  nothing; the values are asked again), delete one or all after a confirmation; history settings and "Delete history"
  in Settings. **Favorites**: a star in the connection manager; the start page lists favorite and recently opened
  connections with Connect (through the manager, D-02 for PROD). **Scripts** in the SQL editor: Open… lists the
  project's `.sql` files, Save (⌘S / Ctrl+S, and D-06's Save) writes back to the script or asks for a path in the
  project, asks before overwriting a script that changed on disk, keeps CRLF line endings; "Download instead" stays.
  **Format** (⌥⇧F / Alt+Shift+F, sql-formatter 15.9.0, MIT) of the selection or the whole text as one undoable
  change. **Completion** of schemas, tables and views from the catalog of the open connection and of columns after
  `table.` (DEV and TEST); on PROD only a catalog already read, or after "Load names". The completion list now closes
  when a missing parameter takes the focus (TD-046 B-6). A history step on the start page.
- STU-020 review fixes (page): a script with mixed line endings is saved byte for byte (only an all-CRLF script is
  edited with LF); the tab title escapes invisible and bidi characters; the save dialog checks the server's new path
  rules; the Open… dialog says how many links it left out; texts for the new script problems; a cut history entry
  says so in the editor; the delete-all dialog says it also clears the user's own profiles in every project; the
  settings say that retention deletes old statements also while the history is off.
- STU-020 delta review (page): `visible()` also escapes zero-width and blank-looking characters (Hangul fillers,
  the braille blank, U+034F), the editor's accessible name shows a script's name through it, and the save dialog
  refuses blank-looking characters like the server.
- STU-020 (backend): Studio's state database `state.sqlite` in the state folder (0600, opened through
  `tabrion.sqlite.connect`, links refused, `secure_delete`); the query history of every finished query, per profile
  and project, with the statement text and its placeholder names but never a parameter value (`GET /api/history`,
  `DELETE /api/history`; settings `[history] enabled` and `retention_days`, 30 days by default, at most 5,000
  entries); favorite and recently opened profiles (`GET`/`PUT /api/favorites`); `.sql` scripts in the project folder
  (`GET /api/scripts`, `GET`/`PUT /api/scripts/file`: only `.sql` files inside the project, no links, atomic,
  byte-exact with the line endings kept, and bound to what the page read); `?cached_only=true` on the catalog, so completion never reads a PROD catalog
  unasked. New problem codes `studio.state_unavailable`, `studio.favorites_full`, `studio.catalog_not_loaded` and
  `studio.script_*`.
- STU-020 review fixes (backend): the history's retention period now holds on disk while nothing is recorded (the
  history off, an idle project): expired entries are deleted, with a checkpoint, when Studio starts, when the history
  is read and when the settings are saved. Script paths refuse Windows device names (also with an extension), 8.3
  short names, names that end in a dot and format characters (bidi, zero-width) on every system; the file is reached
  through folder descriptors with `O_NOFOLLOW` on POSIX (07 TM-04), and on Windows the opened handle must be the
  checked file; hard-linked files are refused. A save keeps the file's byte order mark and holds one lock from the
  digest check to the replace. Links, hard links and reparse-point folders (OneDrive placeholders) answer the new
  `studio.script_link`, and the listing counts what it skipped (`skipped_links`); refusals of the file system answer
  `studio.script_permission_denied`, `studio.script_locked`, `studio.script_disk_full` or
  `studio.script_write_failed` instead of an internal error. Recording a history entry waits at most 250 ms for
  another instance's write lock; the checkpoint result is checked and retried; the 100 favorites count per project.
- STU-020 delta review fixes (backend): a checkpoint of the state database never waits for another connection
  (`busy_timeout = 0`), so a reader holds up neither a query's end nor the store; a checkpoint that finds the log in
  use is retried on every purge until it succeeds. The purge after a settings save runs off the event loop and, like
  recording, waits at most 250 ms for another instance's write lock. Script names refuse blank-looking characters
  (spaces other than U+0020, Hangul fillers, the braille blank, the combining grapheme joiner).
- STU-023, the ER diagram: **ER diagram** next to **Objects** in the explorer draws the tables of one schema (at most
  150; **Tables** chooses them) with their columns, keys and a line for each foreign key, laid out by dagre and shown
  with @xyflow/react 12 (pan, zoom, overview map); 150 tables are laid out in under 2 s, and the page says how long
  it took. A table under the diagram lists the same tables and keys as text; every box's name is a button in the
  Tab order that shows the table in the tree. **Save as SVG** and **Save as PNG** (twice the size) are drawn in the
  page from the diagram, without any remote resource. New read route `GET /api/connections/{id}/diagram?schema=`
  (tables, columns, primary keys and keys out of a schema in three catalog reads; `studio.diagram_too_large` past
  1000 tables). New frontend dependencies `@xyflow/react` 12.12.0 and `@dagrejs/dagre` 3.1.3 (MIT, named in the
  plan). `npm test` runs the timed acceptance tests (`*.perf.test.ts`) after the unit tests, alone in one worker.
  Open items: TD-052.
- TD-008 (review C-2): the required CI job `T-2 path checks (windows-latest)` runs the T-2 path tests by name on
  Windows (no `nt._getfinalpathname` for a UNC value from the project file) and fails when one of them is missing,
  skipped or failed.

- STU-017 docs (no tag, no release): the documentation checklist `docs/development/docs-checklist.md` maps every
  requirement of the package (installation, start, security model, troubleshooting, the feature pages of TD-023) to
  its page and check, and records the clean-install test: on Linux by hand from a locally built wheel (start without
  Node.js, sign-in, a SQLite profile connected read-only, explorer and data viewer, `--stop`; passed), and automated
  in `scripts/smoke_wheel.py`, which now installs with the Tabrion pin, signs in, connects a SQLite profile and reads
  its rows (CI `packaging` job on Linux, macOS and Windows). The guide covers installing from a wheel, the residual
  risks, and troubleshooting while installing, in the views and for `--stop`; the installation page names the
  minimum OS versions and has PowerShell commands for Windows.
- TD-034: the banner **Connection lost** with **Reconnect** (08 section 5 "error recovery") above every page that
  holds a connection (explorer with its data and definition tabs, SQL editor, diagnostics, migration status), when a
  request finds the database gone or the connection closed, or when the header connection's check (every 30 seconds
  while the tab is visible; new route `GET /api/connections/{id}/health`) fails. **Reconnect** reads the profile
  again and asks D-02 again for PROD; the page goes on with the new connection. The server now replaces a lost
  connection on open instead of returning it; a busy pool (checked without waiting for a session) or a check that
  fails otherwise (a timeout) keeps the connection. The SQL editor no longer reopens a PROD connection without D-02
  when the server says it is gone.
- STU-050, UX acceptance of the MVP: `docs/architecture/ux-acceptance.md` maps every criterion of 08 sections 3 to 6
  and of the plan's acceptance to its test or manual check, with the results of the run on 2026-11-13 (SQLite and
  PostgreSQL 16, Chromium). `e2e/ux-acceptance.spec.ts` runs 08 section 5's "First start" (launch link, D-01, the
  start page, the first table's rows in under 60 s) and "Connect" (a failed test says what to check) against a real
  server with axe. User guide page "Migration status"; the guide's index and security page cover the views that
  landed and PROD profiles (D-02). Open items are TD-046 and TD-047.
- STU-016 preparation (not the gate G-S1): `docs/architecture/security-review-prep.md`, a readiness checklist that
  maps SQ-01..SQ-08 and the threat model T-01..T-16 to their code and tests, lists the open security-relevant TD rows,
  the residual risks and the points for the human reviewer. Two security tests from the internal pre-review:
  `tests/security/test_every_route.py` (every API route of the full application, read from the app itself, needs the
  session and the CSRF header and refuses other sites, unsafe methods refuse non-JSON bodies, every refusal carries
  the security headers, and nothing on disk changes; the only other `/api/` route is `/api/openapi.json`) and
  `tests/security/test_canary_flows.py` (a canary in the database, parameters, SQL text, a viewer filter and export
  parameters reaches no log line and no problem answer of the editor, viewer, export and diagnosis routes; exports
  Studio refuses included). The security headers are pinned literally in the tests
  (`test_security_headers_are_pinned`), not compared with the code's own constant.
  `docs/TODO.md`: the package rows of STU-012, STU-022 and STU-025 are done with their PR numbers, and merged pages
  carry their PR numbers instead of "draft".
- STU-052 (rest of TD-020): F6 and Shift+F6 also step through the panes of the main area (08 section 7,
  "Explorer/Editor/Ergebnis"): in the explorer the tree (its current item) and the details (the selected tab), in the
  SQL editor the SQL text, the first parameter value and the result grid. On other pages F6 stops at the main area as
  before. Keyboard-only end-to-end tests for every main flow of the MVP from the page's start, without a click:
  connect with the test and D-02 for PROD, the explorer, and a query with a parameter, then a cancel with Escape in
  the result and with Ctrl+. in the SQL text (`e2e/keyboard.spec.ts`).
- Accessibility audit of the work pages (STU-053, second part; `docs/architecture/accessibility.md`): the SQL
  editor with its grid, cell detail and export dialog, the explorer's tree, Data, DDL and Policies tabs and Count
  rows, the diagnostics report and D-02 in the connection manager. For each, `e2e/a11y-audit.spec.ts` checks target
  size, reflow at 320 px and at 200 % zoom, every Tab stop's focus ring (an open dialog keeps the focus), text
  spacing and reduced motion, and that each action's result is said in a live region (WCAG 4.1.3).
- STU-022 in the explorer page: a **DDL** tab for every object (a sequence shows its DDL on its own) with **Copy
  DDL**, a **Policies** tab for a PostgreSQL table (row-level security on and forced, the owner and the connection's
  role, whether the policies bind that role and why not, a warning when RLS is on without a policy, every policy with
  its command, roles, `USING` and `WITH CHECK`), and **Count rows** for a SQLite table or view (on request; a count
  past the server's timeout says so). Read-only on the explorer's open connection; every text escaped (line breaks and
  tabs in the DDL kept), the DDL cut after 100,000 characters with a note; answers for another object or connection refused.
  New user-guide page "Explorer".
- STU-013 page: the data viewer as the explorer's **Data** tab (08 section 4.5), read-only. Pages of 50 to 1,000 rows
  through the server's keyset tokens (Next, Previous, Reload), sort by a column, up to 20 filters the server binds as
  parameters, a search in the current page, and the SQL editor's grid and cell detail with "Load in full" (10 MB). A
  page asks for no more rows than fit in 64 MB at its column types' widest values, and says so; the paging hint
  shows without a primary key. The tab reads on the explorer's connection (D-02 through the shared confirmation),
  is bound to its connection and object, drops stale answers and hands a connection that is gone back to the
  explorer. The explorer gets "Show data", "SELECT … LIMIT 100 in the SQL editor" (a new editor tab; nothing runs by
  itself) and "Copy name". User guide page "Data viewer". Open items are TD-042. After the review: filters too long
  for one request (12 KB, below the server's 16 KB head limit) are refused in the form; the memory estimate counts 4
  bytes a character (code points above U+FFFF); the first page is read 150 ms after the tab opens, so passing
  through it reads nothing; "Load in full" is aborted when its cell is left.
- D-02 on the server (TD-038): `POST /api/connections` and `POST /api/connection-tests` take `confirmed_environment`,
  the environment the page confirmed for. A profile that is PROD now opens, is tested or has its open connection
  returned only with `"prod"`; otherwise 409 `studio.prod_confirmation_required` (a missing value counts as not
  confirmed). So a page with a stale profile list (the profile became PROD in another tab or in `profiles.toml`) no
  longer opens PROD without D-02: the connection manager reads the profile again and asks D-02 (at most once), and
  the migration status, diagnostics and explorer views and the SQL editor read their list again and ask on the next
  try. The page's `openConnection` and `testConnection` require the environment, so no caller can leave it out. A
  SQLite file is opened from the same read of the profile that passed the check. English and German.
- TD-043 (STU-021 in the page): the export dialog. **Export…** on a finished result of the SQL editor saves it as
  CSV or JSON: the fetched rows or up to 1 000 000 rows, with the CSV formula protection on by default. The statement
  runs again with its parameters on the result's connection (D-02 through the shared guard; no new connection). The
  dialog shows the bytes received, Cancel or Esc aborts the request and saves nothing, a file is saved only when it
  arrived whole and of the asked type, under the server's file name only when that has the expected form, and a JSON
  export the server cut says so. The page holds the file until it is saved, at most 1 GiB: past it the export is
  aborted, nothing is saved, and the dialog says to export fewer rows or columns; a stream that breaks after some
  bytes says the file did not arrive whole (not that the server does not answer). en/de; the data viewer (STU-013)
  can mount the same dialog with a table.
- STU-022 backend: DDL, row-level security policies and row estimates in the explorer, read-only.
  `GET /api/connections/{id}/tables/{schema}/{name}/ddl` rebuilds a PostgreSQL table, view, materialized view or
  sequence from the catalog (`pg_get_*def`) in the form of `pg_dump --schema-only`, and gives SQLite's stored text.
  `.../policies` lists PostgreSQL's policies, whether RLS is on and forced, and whether it applies to the connection's
  role. `.../row-count` counts a SQLite table or view on request, stopped after 10 seconds (`db.timeout`), and
  `GET /api/connections/{id}/row-estimates` gives PostgreSQL's `reltuples` per table; a partitioned table's estimate
  is the sum of its partitions' (also in the table detail). Each route also takes `?schema=&name=`. For the
  reference apps parts and measurements, the DDL equals pg_dump's statement by statement, in their own schemas and in
  `public`. The DDL includes rules, column statistics, storage and compression settings, replica identity,
  `CLUSTER ON` and a view's column defaults.
- TD-036 (STU-025 in the page): the Diagnostics view lists the backup hints (`STU-BK-*`) under "Backups", with
  en/de titles, not counted as findings; a broken `backup` field says that the hints could not be read. **Download
  Markdown report** and **Download JSON report** save the report of the shown connection (no new connection, so no
  new D-02) under the server's file name; a refused report (`studio.report_unavailable`, `studio.report_invalid`) is
  the page's en/de text, and Cancel, a profile switch or leaving the page drops it. The view's D-02 now uses the
  shared gate of the connection manager, so a PROD profile confirmed in the page session is not asked again. The
  diagnostics page route now replaces tabrion's `TAB-PG-020` with `STU-BK-010` like the report. New user-guide page
  "Diagnostics". After the review: a run that did not check the backups says "Backups were not
  checked in this run" instead of "No backup hints"; a canceled report's request is aborted, and the page says
  "canceled" only when the server confirms it; a double press starts one report.
- STU-012 SQL editor (MVP, read-only): the page `/sql` with CodeMirror 6 (PostgreSQL or SQLite highlighting and
  completion), editor tabs whose drafts are kept in the browser, run (the selection or the statement at the cursor), run
  script (statement by statement, stopping at the first error), cancel within a second, `:name` parameters (text,
  number, boolean, null, JSON), and a virtualized result grid (WAI-ARIA grid, keyboard, copy, a detail panel that shows
  the whole value as plain text). D-02 before the first PROD connection, D-06 before closing a changed tab, "Save"
  downloads a `.sql` file. At most 10 000 rows and about 64 MB are in the browser at a time, so 100 000 rows, and wide
  rows at the largest result size, stay far below the 300 MB limit. After the review: a profile change clears the tab's
  results, a run cannot be started twice while it connects and can be canceled then, the durations show (they read as 0
  before), D-02 names the profile's own timeout, drafts are kept per project, and a failed row page is asked for again.
  Before a new connection the editor reads the profile again, so a profile changed to PROD elsewhere is never connected
  to without D-02, and an open answer that does not name that profile's environment is closed again. New shortcuts in
  the help and the palette. CodeMirror runs in a shadow root, so the strict CSP stays unchanged. Open items are TD-033
  and TD-037. The migrations and diagnostics pages now have their own window titles.
- STU-010 page: the explorer (`/explorer`). Open a profile's read-only connection (D-02 first for PROD, once per
  page session as in STU-008; it becomes the header's connection) and browse its catalog as a WAI-ARIA tree
  (PostgreSQL schemas, type groups and objects plus the extensions; SQLite's file facts and type groups), filter it
  by name prefix and type (⌘P / Ctrl+P), and read an object's columns, constraints, indexes, relationships (a
  related table is one click or Enter away) and, on SQLite, `STRICT` and `WITHOUT ROWID`. Refresh reads the catalog
  again on the open connection. The page only reads; every database text is escaped and capped, the answers are
  parsed fail-closed, and stale answers after a profile switch are dropped. E2E against the parts and measurements
  reference apps (`e2e/explorer-reference.spec.ts`; not in CI yet). Open parts: TD-041.
  Follow-up: Open fails closed when the profile list cannot be read again (the offline problem, no D-02 and no
  connection), and a profile deleted meanwhile moves the picker to a remaining profile and the focus to it.
- STU-025 backend: `POST /api/connections/{id}/diagnostics/report` with
  `{"format": "markdown" | "json"}` runs a diagnosis with its backup hints and answers it as a file
  (`Content-Disposition: attachment`). The report is built from an allowlist: no password, token, connection URL or
  DSN, host, port, user or database name, file path, SQL text or parameter value. Every text also passes the log
  redaction, and the Markdown escapes text from the database. Canary tests cover both formats and the log
  (`studio.report_invalid` is new). Every diagnosis now carries backup hints (`backup: {hints, facts}`,
  `STU-BK-001..014`): SQLite backups next to the database (none found, older than the data, readable by others), and
  PostgreSQL's `archive_mode` and `pg_stat_archiver` (off, failing, nothing archived, a standby). tabrion's
  `TAB-PG-020` gives way to `STU-BK-010` only when that hint is given. After the review, the report shows the login
  role and the database as `[role]` and `[database]`, also where tabrion names them as schemas; Markdown escapes `@`;
  a linked `backups` folder is not followed; the file scan stops at a cancel or the time limit; a cut search is the
  hint `STU-BK-005`; "readable by others" means the read bits. After the delta review: a report whose names cannot
  be read from the server is refused (`studio.report_unavailable`, new), and the diagnostics page route keeps
  `TAB-PG-020` until the page shows the backup hints.
- STU-008 connection test and Connect (the parts that waited for STU-009): in the connection manager, **Test
  connection** shows the time, the server version and, for PostgreSQL, the role, superuser, TLS, encoding and time
  zone (for SQLite foreign keys, journal mode and encoding), with a warning for a superuser role or one that bypasses
  row-level security. `POST /api/connection-tests` answers these `facts` and the profile's `environment`. **Connect**
  opens the read-only connection and the header, the PROD frame and the window title show it; the first PROD
  connection of a profile in a page session asks D-02, the connection test of a PROD profile included (shared with
  the migration status view; it belongs to the profile's target and is forgotten when the profile is saved or deleted
  or the project is trusted again); **Disconnect** closes it. Only the newest connect request sets the header, and it
  never stays "connecting…" when an older request answers before or after a newer one that failed, and a connection that was open before keeps the
  header when both fail; a list of the open connections
  read while a connect ran no longer clears the new connection from the header. A
  missing password opens the password dialog and the action runs again. Failures name the remedy (host, port, user,
  password and TLS mode; a file that is no SQLite database), never the server's text; the separate class-28 and TLS
  texts are reached only by mocks with tabrion 0.1 (TD-038). The server version is the one people read (`16.15`).
  The assistant takes a pasted `postgresql://` URL apart (its password goes to the next step, never into the file;
  a refused URL loses its password from the field, `password` and `sslpassword` query parameters included) and tests the profile
  after saving. English and German.
- STU-014 page: the view Diagnostics (`/diagnostics`) runs the diagnosis of a chosen profile on a click and lists the
  findings by severity, each with an icon and a word for its severity, its code, title, detail, remedy and source.
  A PROD profile asks D-02 before its connection opens; "Run again" uses the shown connection. Cancel stops a
  running diagnosis (`.../diagnostics/cancel`), and a result with `STU-DX-008` says that it is incomplete; a profile
  switch or leaving the page cancels too, and an answer that arrives after a profile switch is dropped. The titles of
  Studio's own findings are in English and German; the server's text is shown escaped and capped (U-1), at most 100
  findings, and an answer the page cannot read shows nothing (`studio.answer_invalid`). The activity bar and the
  command palette offer "Diagnostics". After the review: an out-of-range `ran_at` no longer blanks the page, the
  buttons stay focusable while a run starts and ends (`aria-disabled`), and a cancel that does not take says so
  and can be tried again.
- STU-021 backend (on tabrion 0.1 like STU-011): `POST /api/exports`
  exports a query result or a table as CSV or JSON, streamed with bounded memory (1 million rows under 100 MB). The
  statement and object checks run before the first byte, so refusals are problem documents. Limits: at most
  1 000 000 rows, 10 minutes, one export per connection and 2 at a time. CSV escapes formula-like text and base64
  with a leading `'` (`escape_formulas`); JSON keeps rows as arrays and marks a cut export (`truncated`).
  After the review: an export fetches at most 32 rows at a time, so wide rows stay under 100 MB too. The fullwidth
  formula starts are escaped. Non-finite numbers are spelled `NaN`/`Infinity`. Large integers are text in JSON, and
  intervals are ISO 8601 durations.
- STU-024 backend: `GET /api/connections/{id}/migrations` also reports the
  Levuvo formats on PostgreSQL. It compares Nodestro's and Axentra's version tables with the project's SQL migration
  files (checksums as their runners record them; pending steps with an SQL preview), and Orkexa's version table with
  `SCHEMA_VERSION`. Studio reads these files as text and runs nothing. A version table the role may not read, or
  one with unexpected columns or types, is reported as `state: unreadable` with its problem
  (`studio.migrations_table` is new) and does not fail the answer.
- STU-015 backend: `GET /api/connections/{id}/migrations` with SQLite's
  `user_version` and the Alembic status. The Alembic `current` comes from the version table; `heads` and `pending`
  need the project's revision files to be imported, so they are read only after the user trusted the project's code
  (`GET`/`POST`/`DELETE /api/project/code-trust`, off by default, bound to the digest of `alembic.ini` and the
  revision files), in a separate process that gets only the connection's target and is killed after 30 seconds.
  Otherwise they show as `unknown (project not trusted)`. Threat-model entry T-16. New dependency extra
  `tabrion[migrations]` (Alembic). After the security review, the worker runs exactly the revision bytes it hashed
  and reads no bytecode of the project, loads the database driver before the project's folders are on `sys.path`,
  reads its answer with a size and time bound, and only one worker runs at a time (`studio.migrations_busy`).
  On Windows the worker runs in a job object that ends its children, and the backend tests are green there; a test
  that hangs for 300 s now ends the pytest run after its stack dump, so the CI log is kept.
- STU-015 page: the view Migrations (`/migrations`) shows the trust state of the project's Alembic files and, for a
  chosen profile, the status of every migration format.
  The trust dialog lists `alembic.ini`, the `script_location` and every revision file (escaped, like D-01), opens on
  "Don't trust", sends the digest it showed and shows the new list when the files changed meanwhile; the trust can
  be taken back. A PROD profile asks D-02 before its connection opens. English and German.
  The command palette offers "Go to Migrations" like the activity bar.
  The Nodestro, Axentra and Orkexa version tables that STU-024 reports are shown with their state, current version
  and the steps not applied (at most 20, then "and N more"; names escaped and capped), and an unreadable table says
  why (no read permission, unexpected columns, statement refused, timeout); a format the page does not know is
  counted in a fixed line, so a database with only such tables never reads as "no migration format" (C-1 of
  `stu-015-ui-review.md`). A status that arrives after the profile was switched is dropped.
- STU-014 backend (`tabrion.check` on the driver connection until tabrion 0.2):
  `POST /api/connections/{id}/diagnostics` with tabrion's findings (without those about Studio's own
  session) and Studio's `STU-DX-001..005`: a prod profile without a verified certificate, weak TLS to a remote host
  (T-10), a read-only role that can write, no statement timeout for the role or database, SQLite rows written with
  foreign keys off. Review fix round (`stu-014-review.md`): the diagnosis stops at the connection's statement
  timeout and can be canceled (`POST .../diagnostics/cancel`; `STU-DX-008` names what did not run, also SQLite's
  `quick_check`); `STU-DX-003` covers column grants, `TRUNCATE`, views, foreign tables, sequences and every role the
  session can `SET ROLE` to; `STU-DX-006` finds an owner role in any schema (it replaces `TAB-PG-003`, which sees
  the current schema only); `STU-DX-007` default privileges; role settings follow PostgreSQL's precedence, and
  `STU-DX-010` covers `lock_timeout`, `idle_in_transaction_session_timeout` and `application_name`; a broken foreign
  key definition is `STU-DX-009` instead of a failed route; no TLS finding twice, none for a Unix socket.
  Delta review: on SQLite a time limit or cancel stops tabrion's `quick_check` alone, so tabrion's other findings
  (a 0644 file's `TAB-SQ-003`, say) stay and `TAB-SQ-006` says "not checked"; `STU-DX-009` calls only "foreign key
  mismatch" and "no such table" a broken definition (a lock or I/O error gets a neutral text). A deterministic test
  requests the limit or the cancel before tabrion's step starts on SQLite (finding 3 of
  `stu-014-delta-v010-review.md`).
- STU-013 backend (on tabrion 0.1 like STU-011):
  `GET /api/connections/{id}/tables/{schema}/{name}/rows` (pages of 1 to 1000 rows, sort, JSON filters, `next`
  token) and `GET .../cell` (one value in full, up to 10 MB). Every statement has a `LIMIT`; pages go by primary
  key, by `rowid` or `ctid` without one, and by `OFFSET` for views (`hint: unstable_order`). Column names must be
  reflected columns, filter values are bound parameters, and key values stay in the backend. Past the profile's
  timeout the answer is `db.timeout`.
  - Review `stu-013-review.md`: PostgreSQL pages without a primary key go by `(tableoid, ctid)`, so partitioned
    tables and inheritance parents lose no rows; keys are compared in the column's own type (a `real`, `jsonb` or
    array sort no longer loops or fails on page 2), and `json`, `xml` and geometric columns are refused for sorting
    (`studio.viewer_sort_unsupported`); NULLs in a SQLite primary key are ordered by the row id; the cell detail by
    `ctid` or `rowid` answers `studio.viewer_row_gone` when another row took the id; lengths are measured on the
    server before values are fetched (64 KB per cell, `max_result_mb` per page with `truncated: "bytes"`, 10 MB of
    bytes for the cell detail, fetches of about 2 MB); tokens are bound to the session, at most 64 per session and
    connection, and dropped with their connection; reflection is cached for 10 s; at most 4 viewer statements at a
    time, 2 per connection.
  - Delta re-review of `stu-013-review.md`: a primary key column whose name starts with `@` is a quoted column like
    any other (the row identity is typed, not marked by a prefix); every PostgreSQL session starts with
    `extra_float_digits=3`, and a keyset walk that makes no progress goes on by `OFFSET` instead of repeating pages;
    the fetch batch is capped by the widest row the byte caps allow, so a small first row cannot inflate it; a
    decoded `json`, `jsonb` or array value counts toward the batch and the page cap at the memory its Python objects
    take, not at its text bytes.
- STU-010 backend (reflection through `tabrion_compat.inspector` until tabrion 0.2):
  `GET /api/connections/{id}/catalog` (schemas and objects by kind, cached until `refresh`),
  `GET /api/connections/{id}/tables/{schema}/{name}` (columns, keys in and out, constraints, indexes, row estimate),
  `GET /api/connections/{id}/extensions` (PostgreSQL) and `GET /api/connections/{id}/sqlite-info` (pragmas, file
  size and mode, `STRICT`, `WITHOUT ROWID`). 500 tables in under a second.
  - Review `stu-010-review.md`: PostgreSQL foreign keys come from `pg_constraint` (a key into a search-path schema
    such as `public` kept the wrong schema); an object dropped after the catalog was built is
    `studio.object_not_found`; a busy pool is `studio.connection_busy` (503); unknown names rebuild the catalog at
    most every 5 seconds; a closed connection's catalog is dropped at once; `GET /api/connections/{id}/table?schema=&name=`
    for names with `/`; SQLite facts without `PRAGMA table_list` before SQLite 3.37.
  - Delta re-review of `stu-010-review.md`: a partitioned table's foreign key is shown once, without its
    partitions' copies (N-1); a query that finds no free session is `studio.connection_busy` too (I-9); SQLite
    before 3.37 still reports `without_rowid` as true or false.
- STU-011 (on tabrion 0.1 behind `services/tabrion_compat.py` until tabrion 0.2):
  - The query engine and the routes `POST /api/queries`, `GET /api/queries/{id}`, `GET /api/queries/{id}/events`
    (server-sent events), `GET /api/queries/{id}/rows` and `POST /api/queries/{id}/cancel`.
  - One checked statement per `Database.read()`, through `ConnectionRegistry.get`; `:name` parameters are bound by
    the driver (psycopg `%(name)s`, sqlite3 `:name`), never through SQLAlchemy `text()`, so a colon inside a literal
    stays text.
  - Limits of 03 section 5.3: 1 000 rows by default up to the `max_rows` setting, 20 MB per result, 64 KB per cell,
    the profile's timeout for the whole query, 4 queries at a time (2 per connection), the last 16 results kept.
    PostgreSQL queries are read through a server-side cursor.
  - Cancel within a second (PostgreSQL `cancel_safe()`, SQLite `interrupt()`); a canceled PostgreSQL connection
    leaves the pool.
  - No SQL text, parameter value or row in the log.
  - Review `stu-011-review.md` (and H-1 of `tab-033-review.md`):
    - M-1, M-2, I-2: `check_statement` allows a statement only by its first word (`SELECT`, `WITH`, `VALUES`,
      `TABLE`, `EXPLAIN` of a query, `SHOW`, `PRAGMA`), so `DECLARE ... WITH HOLD`, `FETCH`, `MOVE`, `CLOSE`, `SET`
      and every write are refused before the database. PostgreSQL connections get Studio's session settings as
      startup options and run `RESET ALL` on check-in.
    - M-3: a fetch loads about 2 MB at most (one row first, then as many as fit).
    - L-1: `OpenConnection.stream` checks the driver text it executes.
    - L-2, H-1: a connection that got a cancel is invalidated on both engines (closed if that fails), with tests of
      both cancel guards.
    - L-3: one cancel worker per query. I-3: the byte cap is the new `max_result_mb` setting (20 MB, up to 200 MB).
  - Delta re-review of `stu-011-review.md`:
    - R-H1: the first word of a statement comes from the lexer, read the way PostgreSQL (with and without backslash
      escapes) and SQLite lex it; the readings must agree. `-- x\r<statement> /*\nshow */` and a SQLite nested
      comment can no longer hide a statement. The check-in reset also closes held cursors and session-level advisory
      locks.
    - R-M1: the fetch budget follows the largest row so far, grows at most twice per fetch and counts arrays and json
      in full.
    - R-I1: a `search_path` or `transaction_timeout` session setting is refused at startup (Studio sets neither).
  - Delta re-review 2 of `stu-011-review.md` (head 70ca2f9):
    - R2-L1: white space and `--` comments are skipped with one regular expression, so checking a text with many line
      comments is linear (512 k characters of `--\n`: 10 s before, about 0.2 s now); the same for the placeholder
      scan.
    - R2-I2: a text with a NUL character is refused.
    - R2-I1: the check-in reset also takes back a role change (`SET SESSION AUTHORIZATION DEFAULT`, `RESET ROLE`),
      which `RESET ALL` keeps.
  - Delta review `stu-011-leak-delta-review.md`:
    - A session that a running statement returns after its connection was closed is closed at check-in, not left
      open in the disposed pool (the unclosed-database ResourceWarning); the listener holds its Database weakly.
    - F-4: the cancel hook is detached before the cursor closes, so a late cancel can no longer hit the server
      cursor's `CLOSE` and leave it open (the flaky `ServerCursor ... deleted while still open` warning); a cursor
      that cannot close is closed with its connection.
- STU-009 (tabrion v0.1.0 pinned by full SHA):
  - The connection registry and the routes `POST`/`GET`/`DELETE /api/connections` and `POST /api/connection-tests`.
  - Every connection is read-only. SQLite: `mode=ro`, `query_only` and an authorizer allowlist (no ATTACH, no
    VACUUM INTO, no `load_extension`, no write or schema change). PostgreSQL: a read-only session, `Database.read()`,
    one statement per transaction and no transaction control.
  - URLs only through `tabrion.safe_url`; the driver gets only the fingerprinted fields.
  - `db.<kind>` problems from `tabrion.errors.classify`, and tabrion's `DbErrorRedactionFilter` in the log handlers.
  - Engines are disposed on shutdown.
  - Review `stu-009-review.md`:
    - H-1: every PostgreSQL statement is a prepared statement (`prepare_threshold=0`), so the server refuses several
      commands, whatever a text split thinks (the `standard_conforming_strings = off` escape no longer writes).
      `SET standard_conforming_strings`, `SET backslash_quote` and `set_config(` are refused.
    - M-1: without a password, libpq gets none of its own (no `PGPASSWORD`, `.pgpass` or service-file password).
    - L-1: the password is resolved from the same entry as the URL.
    - Info items: a connection test shares the connection limit.
  - Delta re-review of `stu-009-review.md`:
    - R-L1: the function check reads the statement's tokens (comments and literals left out, quoted identifiers
      unquoted, `'...'` lexed with and without backslash escapes), so `"set_config"(` and `set_config/**/(` are
      refused; so are `U&"..."` identifiers.
    - I-R1: `pg_notify()` and the session-level advisory lock functions are refused like `NOTIFY` and `LOCK`.
    - R-L2: a requirements or constraints file, at any depth, holds only plain requirements and the full-SHA tabrion
      pin: pip options (`-e`, `-r`, `-c`, `-f`, index and host options), paths, wheel files, other URLs and
      fragments are refused.
    - I-R2: a test that fails when the secret comes from a second read of the profile.
  - Re-review 2 of `stu-009-review.md`:
    - R2-L1: `DO` and the functions that run a query given as text (`query_to_xml`, `cursor_to_xml` and their schema
      forms, `ts_stat`, `ts_rewrite`, `crosstab`, `dblink`) are refused.
    - R2-I2: tests for both ways of lexing `'...'` and for `E'...'` escapes.
    - R2-I1: a requirement's marker cannot carry a pip option (`--config-settings`, `--global-option`, `-e`).
    - CI: the PostgreSQL service port is published on `127.0.0.1` only.
  - The dependency rule follows the ADR 0009 amendment (no PyPI publication): a full-SHA tabrion pin is allowed in a
    constraints file, and `pyproject.toml` keeps the final range with no direct reference.
  - tabrion v0.1.0 (`d5821faa8742dea012abda3b663549c038d76543`, the merge commit of tabrion PR #56) is pinned in
    `constraints/tabrion-integration.txt`, and `pyproject.toml` declares `tabrion[postgres,sqlalchemy]>=0.1,<0.2`.
    CONTRIBUTING and the guide's installation page install with `-c constraints/tabrion-integration.txt`.
- TABRION QA-21 (AC-7): `scripts/check_tabrion_api.py` is now the check shared with axentra, orkexa and
  nodestro-runtime. Besides private Tabrion modules it refuses `_` attributes on Tabrion names, `getattr` and
  `import_module` with a private constant and star imports. Every used name must be in its module's `__all__` in the
  installed Tabrion (a used name without an installed Tabrion fails), and `--surface` compares with Tabrion's frozen
  API snapshot and refuses experimental (`X`) names. `--export` writes the used names to `docs/api-consumers.md` (in
  the docs under Development), and `tests/architecture/test_tabrion_api_export.py` fails when that file is stale or
  missing.
- Reproducible-wheel check (QA-8): the packaging tests build the wheel twice more and require the same SHA-256 as the
  CI build, with the build backend pinned exactly (`hatchling==1.33.0`, hashed in `build-constraints.txt`); the QA-8
  evidence (audits, SBOMs, hashes) is in `docs/development/ci.md`. A fixed `SOURCE_DATE_EPOCH` build is tested too, and
  `scripts/check_frontend_reproducible.py` checks by hand that two frontend builds match.
- Conventions audit (TABRION QA-22): `docs/development/conventions.md`, `tests/security/test_conventions.py` (license,
  security policy, community files, Dependabot policy, CI manifest), and the missing `CODE_OF_CONDUCT.md` and
  `SUPPORT.md`.
- Project skeleton: Python package `tabrion_studio` (hatchling, ruff, mypy strict, import-linter) and the React 19 /
  TypeScript 5.9 / Vite 8 frontend in `frontend/` (eslint, prettier, strict `tsc`, vitest) with a placeholder page
  (STU-001).
- `tabrion-studio` command and process model: loopback-only server on a free port, one instance per project folder
  (a second start reuses it), idle exit after 30 minutes, `--stop`, clean shutdown on Ctrl+C and SIGTERM (STU-002).
- CI (`ci.yml`, `ci-ok.yml` with the commit status `ci-ok`, `nightly.yml`) on Linux, macOS and Windows; the wheel
  build hook that requires the built frontend; a wheel smoke test without Node (STU-018).
- License policy check with negative probes and CycloneDX SBOMs for the Python runtime and the frontend (STU-031).
- Access protection (STU-003, `docs/architecture/access-protection.md`): one-time launch token in the URL fragment
  (`/launch#<token>`, 120 s, once), exchanged by the page through `POST /api/session` for an `HttpOnly;
  SameSite=Strict` session cookie (`tabrion_studio_session_<port>`) and a CSRF value kept in `sessionStorage` and sent
  as `X-Tabrion-CSRF`; `tabrion-studio --print-url` prints a one-time sign-in link. Every request passes the access
  middleware: Host allowlist `127.0.0.1:<port>` on every route, no forwarded headers, Origin and fetch-metadata
  checks, JSON-only API requests, session plus CSRF header on every API route except health and the launch exchange.
  Every answer carries a strict Content-Security-Policy, `Cache-Control: no-store`, `nosniff`, `no-referrer`,
  `X-Frame-Options: DENY` and same-origin opener and resource policies (STU-T-001..006, -014, -025, -026).

- App shell, design system and languages (STU-005, `docs/architecture/frontend.md`): header, activity bar, status
  bar and the views Start, Connections, Settings and Not found; `--tb-` design tokens with a complete light and dark
  theme (system default, selectable) and compact or normal density; components copied from axentra-studio with a
  provenance note; German and English (i18next), with a completeness test. Contrast test of the tokens in both themes
  and axe without serious or critical violations (jsdom in CI; Chromium with colour contrast in `frontend/e2e/`).
- Status displays and dialogs (STU-051): environment badge (DEV, TEST, PROD) in the header, on every tab and in the
  window title (`[PROD] name – Tabrion Studio`) with a red frame for PROD; access mode, transaction and connection
  displays; the dialogs D-01, D-02, D-06 and D-08 with tested focus rules (initial focus on the safe option, focus
  trap, Escape cancels, focus returns to the opener).
- Brand assets (`assets/branding/`): the Tabrion Studio lockup, marks and favicons derived from Peter Karlhuber's logo
  master with the shared brand kit (no redraw, no CSS filters), the social preview, and `TRADEMARKS.md` (the names and
  logos are not MIT). The committed masters carry no C2PA or eXIf metadata; a test proves the pixels unchanged.
- Keyboard and command palette (STU-052, first part; `docs/architecture/frontend.md`, "Keyboard"): ⌘K / Ctrl+K opens
  a command palette (go to a view, theme, density, language, help) with the combobox pattern; ⌘/ / Ctrl+/ shows the
  keyboard shortcuts; F6 and Shift+F6 move between header, activity bar, tabs, main area and status bar. Shortcuts are
  shown with the keys of the platform (⌘ on macOS, Ctrl or Strg elsewhere); the header has buttons for both dialogs.
  In German the theme commands read "Darstellung", like the settings view.
- Language, theme and density are saved in Studio's `settings.toml` as soon as they are chosen, and applied from it
  when the page opens. They were browser preferences only, which a browser keeps per port, so they were lost at every
  start (TD-021). `settings.toml` accepts `density = "compact" | "normal"`. A file with issues or one written by hand
  is not changed from the general settings; the choice then stays in the browser and the view says so. The command
  palette's theme, density and language commands are saved the same way.
- Accessibility audit of the shell (STU-053, first part; `docs/architecture/accessibility.md`): WCAG 2.2 AA
  checklist, and `e2e/a11y-audit.spec.ts` checks target size, reflow at 320 px, text spacing, focus visible and not
  obscured, reduced motion and the landmarks. Fixed on the way: a PROD workspace no longer scrolls sideways at 320 px
  (the header wraps; the tabs' screen-reader texts stay inside the tab list), the D-01 "Show full value" and "and N
  more" buttons are 24 px high, the tooltips can be hovered and hidden with Escape, and a click beside a focused
  link no longer follows it through its tooltip (WCAG 1.4.10, 2.5.8, 1.4.13). A long connection name or tab title
  is cut with "…" and shown in full as a title; below 720 px the logo link keeps its accessible name (4.1.2). After "Go to …" in the
  command palette the focus moves to the new page's main area.
- The command prints a hint when the default browser on Linux is a snap or a Flatpak, which cannot open the sign-in
  file in a hidden state directory (TD-002, review S-4 of `stu-003-review.md`).
- CI job `e2e` (mandatory check): the Playwright suites in Chromium on every pull request, namely axe with color
  contrast in light and dark and in English and German, rendered contrast, dialog focus rules, status displays, and
  `e2e/backend.spec.ts` against the built wheel in a real `tabrion-studio` under the CSP. It installs the Chromium build
  of the locked Playwright (`npm run e2e:install`) and fails if a required suite was skipped (review U-3 of
  `stu-005-051-review.md`). `npm run e2e` runs the suites locally.

- Problem JSON and logging (STU-004, `docs/architecture/errors-and-logging.md`): every error answer is
  `application/problem+json` with `code`, `kind`, `status` and fixed `title`, `detail` and `hint` texts from one
  catalog, including the access refusals, 404, 405 and validation errors (which no longer echo the input); an
  unexpected exception gives `studio.internal_error` with a log ID and is logged once, redacted. Logs go to a rotating
  JSON-lines file per project instance (5 x 5 MB, 0600 in a 0700 folder, symlinks refused) and WARNING and above to
  stderr; `--log-dir` and `TABRION_STUDIO_LOG_DIR` choose the folder (default: the per-user log directory, or
  `STATE_DIR/logs` with `--state-dir`). A request log names the method, route template, status, duration and problem
  code, never the query string. Uvicorn's loggers use the same handlers; its access log stays off. Canary tests
  (STU-T-008, -009) prove that secrets, parameter values, server `DETAIL` texts and tokens reach neither an answer nor
  a log line.

- Configuration, profiles and project trust (STU-006, `docs/architecture/configuration.md`): TOML schema version 1 for
  the project's `.tabrion/studio.toml`, the user's `profiles.toml` (must be 0600) and `settings.toml`, with key-path
  issues that never repeat a value; `--config-dir` and `TABRION_STUDIO_CONFIG_DIR`. `GET /api/project`,
  `POST /api/project/trust`, `GET /api/profiles` and `GET /api/settings`. Project profiles are used only after the
  user confirmed the file's exact bytes in D-01; a changed file needs a new confirmation and shows the diff. D-01 now
  lists each connection, host, file reference, password variable and unknown key as its own item with control and
  bidi characters escaped, long values and lists capped, and the real totals (binding requirement U-1 of
  `stu-005-051-review.md`). The German D-01 text says "definiert" (review wording nit).

- Secret management (STU-007, `docs/architecture/secrets.md`): `keyring:` references use the OS keyring (service
  `tabrion-studio`, one entry per profile and project folder) with no plain-text fallback (the `fail`, `null` and
  `keyrings.alt` backends are refused); `env:NAME` reads the process environment; `file:PATH` is read with the checks
  of review C-3 at read time (no `~` expansion, containment, no link on the path, owner, 0600, one link, no FIFO,
  4 KB); a password can be entered for the current session only. `PUT` and `DELETE /api/profiles/{id}/secret` store
  and remove an entered password (204, never echoed), `GET /api/secrets` says whether a keyring is available, and
  `GET /api/profiles` shows only `secret: {source, has_secret}`. Every resolved or entered secret is redacted from the
  logs. `file:` values are never URL-decoded or Unicode-normalized.
- Re-review of PR #9 (`stu-conditions-rereview.md`): R-1, the link walk refuses a step to `/net/<host>` or another
  network form before it `lstat`s it (a relative link that climbs to `/net` triggered the macOS automount); R-3, a
  leading `\??\` is refused as text.
- Connection manager and server settings (STU-008, `docs/architecture/configuration.md`): the Connections view lists
  the project's and the user's profiles with a filter and a detail panel; the three-step "New connection" assistant
  (database, password source, name and the required environment) creates and edits the user's profiles; a profile
  can be deleted (Cancel is the default) and a password entered for the keyring or the session. The Settings view
  saves the query limits and the SQL logging opt-in. `POST`, `PUT` and `DELETE /api/profiles` and `PUT /api/settings`
  write only Studio's own `profiles.toml` and `settings.toml`: into a private folder, atomically with 0600 and
  `O_NOFOLLOW`, bound to the digest the page read (`studio.config_stale`), never over a file with issues, and over a
  hand-written file only after a confirmation. Refused forms name fixed keys and codes, never values. The project's
  file is never written. Testing and connecting wait for STU-009.
- Review of PR #10 (`stu-007-review.md`): C-1, a stored keyring or session secret is bound to a fingerprint of the
  connection target (engine, host, port, database, user, path), so a project profile that keeps its name but points
  to another host no longer gets the saved password, and D-01 lists it under "Saved password not used"; I-1, secrets
  of 4 to 7 characters are redacted too; I-3, one leading BOM is removed from a `file:` secret; I-2, the FIFO test
  fails instead of hanging, a deterministic link-swap test, and test servers use the null keyring backend.

- Review of PR #11 (`stu-008-review.md`): the effective TLS mode is part of the saved-password fingerprint (a TLS
  downgrade asks for the password again); a lone surrogate in a field is a form issue and a file that would not read
  back is a 422, not a 500; a keyring that refuses to delete a saved password is logged and reported; a save reads
  the file again just before it replaces it; the e2e server uses the null keyring; an `env:` password reads "set in
  the environment".

- Network log check (QA-9, SQ-08): `e2e/network.spec.ts` visits every view, dialog and notice of the e2e harness, and
  `e2e/backend.spec.ts` the real server, and fails if the page requests any other host (no CDN font, telemetry,
  external image or script). It listens on the browser context, so popups, new pages and workers count too.
- User guide (STU-017, first part; `docs/guide/`): installation from a Git commit, starting and stopping with every
  option, connections and the trust dialog, passwords, settings and keyboard, how Studio protects your data, and
  troubleshooting with the messages of the page and the command. A test checks that every command-line option has a
  row in the guide's options table and that every guide page has an entry in the site navigation.

- Dependency policy (QA-17, AC-2): `dependency-policy.json` is this repository's copy of the dependency policy K-33
  from nodestro-studio (only its outgoing edges: `tabrion[postgres,sqlalchemy,migrations]` in the core), and
  `scripts/check_dependency_policy.py` is the unchanged checker from there; `scripts/DEPENDENCY_POLICY_SOURCE.md`
  records their origin and SHA-256. `python scripts/check_dependency_policy.py` checks `pyproject.toml` (core, every
  extra and dependency group) against the copy. `tests/architecture/test_dependency_policy.py` checks the checksums,
  runs the checker and probes forbidden requirements (Axentra, a platform, extras beyond the edge). Ruff skips the
  vendored script (it is linted in nodestro-studio). The sdist ships the policy copy.

- Import-contract mutation probe (QA-16, AC-1): `tests/architecture/test_forbidden_probe.py` runs `lint-imports` on
  the repository and on a copy of the package, and on copies with one forbidden import each, which must fail and name
  the broken contract (every contract has at least one probe). It also checks that the edge to `tabrion` stays
  allowed (K-33), that the K-33 contract names every known Levuvo platform, and scans for imports of any `orkexa*`,
  `axentra*`, `nodestro*` or `levuvo*` package by prefix.

- TD-062 (JR-N1, G-S1 decision 4): `e2e/connection-lost-prod-retry.spec.ts` tests the PROD case of **Reconnect** in
  light and dark, English and German, with axe: after a lost connection the first **Reconnect** asks D-02; when it
  fails (the database is down) the retry in the same page session does not ask again, also once the database is back;
  a new loss asks D-02 again. The e2e harness gets `window.harnessDatabaseDown(down)` (opening a connection fails with
  `db.connection`) and records every `POST /api/connections` body. Test only; the behavior is unchanged.

### Security

- **An over-privileged PostgreSQL write role is no longer unlocked** (owner decision 1A at G-S2, review I-3 of PR
  #111). A write profile (`kind = "write"`) whose role holds SUPERUSER, BYPASSRLS or REPLICATION, or is a member of
  `pg_read_server_files`, `pg_write_server_files` or `pg_execute_server_program`, directly or through a role it
  reaches by membership, refuses the unlock: `405 studio.write_role_over_privileged`, which names no role and no
  finding. The write connection is closed and nothing is unlocked; the refusal is the existing `unlock` record of the
  audit log with this result code (no new audit event, decision 4A). CREATEROLE (also before PostgreSQL 16) and the
  other predefined roles, such as `pg_read_all_data` and `pg_write_all_data`, stay warnings in the write state.
  Admin profiles are unchanged (G-S4). Texts in English and German; tests without a server, through the unlock route
  and against PostgreSQL (each refusing attribute and role held directly and through a group role).

- **Behavior change for Studio 1.0 users: `PG*` environment variables no longer reach PostgreSQL connections**,
  read connections included (G2-01 below). A profile without a host, user, database or TLS mode no longer takes it
  from `PGHOST`, `PGUSER`, `PGDATABASE` or `PGSSLMODE`; with such a variable set, the connection is refused
  (`422 studio.connection_environment`), as it is while `PGSERVICE`, `PGSERVICEFILE`, `PGSYSCONFDIR`,
  `PGREQUIREPEER`, `PGREQUIREAUTH`, `PGSSLCERTMODE`, `PGTZ`, `PGDATESTYLE` or `PGGEQO` is set. Variables Studio
  overrides (`PGHOSTADDR`, `PGPORT`, `PGSSLROOTCERT`, `PGPASSWORD`, ...) are ignored without an error.
  **Remedy:** put the host, user, database and TLS mode into the profile under Connections, and start
  `tabrion-studio` without the refused variables (for example `env -u PGSERVICE -u PGTZ tabrion-studio`).
  `PGCONNECT_TIMEOUT` and `PGCLIENTENCODING` still apply.
- Findings of the independent G-S2 gate review (G-S2 not granted; every write area stays off, no default changes):
  - G2-01: libpq's environment no longer chooses the server, the authentication or TLS of any Studio PostgreSQL
    connection (read connections, the connection test, the migration worker, write and admin connections). Every
    option the profile leaves out that libpq would take from a `PG*` variable is given as libpq's own default
    (for example an empty `hostaddr` and empty TLS file options, so `PGHOSTADDR` or `PGSSLROOTCERT` no longer
    apply); where that is not possible (`PGSERVICE`, `PGSERVICEFILE`, `PGSYSCONFDIR`, `PGREQUIREPEER`,
    `PGREQUIREAUTH`, `PGSSLCERTMODE`, or `PGUSER`, `PGDATABASE`, `PGHOST`, `PGSSLMODE` for a profile without that
    field), and for `PGTZ`, `PGDATESTYLE` and `PGGEQO`, which libpq sends as session settings (`TimeZone` decides how
    a `timestamptz` without an offset is read; I-1 of the PR #125 review), the connection is refused while the
    variable is set: `422 studio.connection_environment`, which names
    no variable and no value. `PGCONNECT_TIMEOUT` and `PGCLIENTENCODING` still apply. A socket or loopback profile
    with `requirepeer` can therefore no longer be sent to another server over TCP.
  - G2-02: on SQLite an integer wider than 64 bits (a new value, a key or an `old` value, in any column) is refused
    before the plan (`400 studio.data_editor_value_invalid`, `value.out_of_range`) instead of failing at run time;
    a run that still meets one rolls back with the same problem.
  - G2-03: a time or timestamp at exactly `24:00`, which the check takes as PostgreSQL does, is bound as text for the
    database to read, the same on every supported Python version (before 3.14 `fromisoformat` refused it at run
    time; from 3.14 on it would have turned a `time` into `00:00`).
  - G2-04: the platform configuration folder of `write-areas.toml`, the audit log and the secret confirmations no
    longer follows an environment variable: `XDG_CONFIG_HOME`, `HOME` and platformdirs' `WIN_PD_OVERRIDE_*` are
    ignored (the home folder comes from the account database on POSIX, the `LocalAppData` known folder on
    Windows), as STU-ADR-0006 states. When such a variable moves Studio's other configuration, write profiles stay
    unused while an area is on (`studio.write_profiles_ignored`); start Studio with `--config-dir` to use them. A
    user without an entry in the account database (a container started with `--user 1234`, OpenShift) has no
    such folder: Studio starts read-only with every area off and logs `studio.write_areas_no_folder` (L-1 of the
    PR #125 review; the first version of this fix stopped Studio from starting there).

- STU-XW hardening (CR-DBX; notes N-1, N-2 and N-4 of the PR #116 review, I-1 consistency with tabrion #77 D-2),
  with every write area still off:
  - the audit log (`audit/`) and the confirmed secret pairs (`write-secret-confirmations.json`) now live in the
    folder of `write-areas.toml` (the folder `--config-dir` names, `<--state-dir>/config`, or the platform's
    configuration folder), never in a state folder that `TABRION_STUDIO_STATE_DIR` chose, the same rule as for the
    write profiles (L-1). If the variable moved the state folder, Studio neither reads nor writes these files there
    and logs `studio.write_state_ignored` at start while an area is on;
  - every PostgreSQL write and admin connection that sends a password now requires SCRAM
    (`require_auth=scram-sha-256`) on every host, loopback and Unix socket included, not only on a foreign host;
    without a password it stays `!password,!md5`. A write profile can name the server's operating system user
    (optional key `requirepeer`, Unix socket only, `config.requirepeer_socket_only` otherwise; part of the confirmed
    pair), which libpq checks before it authenticates; the form asks for it for a socket profile (en, de). Loopback
    TCP without TLS stays a residual risk (threat model, residual risk 4) and a G-S2 item;
  - the privilege check counts a role as predefined (`admin.broad:<role>`) only with a `pg_` name and an OID below
    16384, in the findings as well as in the query, so a role a user created under a `pg_` name is not reported;
  - the security guide says that every PostgreSQL write or admin profile needs the pair confirmation once, and that
    any change of the pair (host, port, database, TLS mode, role, secret reference, `requirepeer`) needs a new one.

- M-1 of the PR #117 review (TD-072, pre-existing): a profile `host` with a comma, which libpq reads as a list of
  hosts, is refused in every profile, project or user, and so for write and admin profiles too (`config.host_list`,
  en/de text). Before, such a list could count as loopback, which skipped the TLS default, the D-01 warnings and the
  foreign-host checks of write profiles. `is_loopback()` is now False for any list, and `connection_url()`, which
  every connection goes through, refuses a host or port list before the driver sees it. Studio is now as strict as
  tabrion's `profilefile`, which refuses a comma in a host name.
- DC-TS-1 follow-up: `scripts/check_tabrion_source.py` is nodestro-studio's new copy (#198, SHA-256
  `f1edfc71b5da4986547abe0824833fb73bba532cb5e6a00e202c9ff317747456`): the poisoned `tabrion` is 0.2.99 (was 0.1.0,
  outside Studio's `>=0.2,<0.3`), so an install by range would pick it over the pinned 0.2.1.
- C-03 / TD-071 (security assessment, tabrion's redaction residues): Studio pins tabrion 0.2.1, whose
  `DbErrorRedactionFilter` also removes psycopg's `LINE n:` statement echo in the middle of a line or as the only
  label (also escaped inside a `repr`, tabrion C-02), an inline `DETAIL:`/`HINT:`/`CONTEXT:`/`QUERY:` label directly
  before a line break, and a label followed by a no-break space (tabrion TB-145). Each form kept the value with
  tabrion 0.2.0. New canary tests through Studio's log setup
  (`test_the_redaction_residues_of_tabrion_0_2_0_are_removed`, `test_an_escaped_line_echo_in_a_repr_is_removed`)
  fail with tabrion 0.2.0 and pass with 0.2.1.

- DC-TS-1 (dependency confusion; tabrion and tabrion-studio are not registered on PyPI, owner decision of
  2026-11-13): `scripts/runtime_lock.py check-installed` no longer skips tabrion. The installed tabrion's
  `direct_url.json` must name the Git URL and the full commit of `constraints/tabrion-integration.txt`; a tabrion
  without one (from a package index), from a local directory or archive, or at another commit fails the CI jobs.
  Every CI job that installs with `-c constraints/tabrion-integration.txt` first raises pip to `>=26.1` (pip 24.0 to
  26.0 crash on that constraint with extras), and a test keeps it so. `docs/development/building.md` installs with
  the pin and the hashed lock instead of a bare `pip install -e ".[dev]"`. New CI job `install (poisoned index)`: the
  development and the documented runtime install run against a local index that serves a poisoned tabrion with a
  forged `direct_url.json` (`scripts/check_tabrion_source.py`, the SHA-256-pinned copy of nodestro-studio's script)
  and must use that index, pass no option that bypasses it, make no request for tabrion and run no poisoned code; the
  job is reported by ci-ok. The zero-request assertion is the proof that the Git pin works; `check-installed`'s
  `direct_url.json` check detects an accidental index install but is no proof of origin (TD-064).
- TD-013: a SQLite `path` with a `?`, `#` or `%` in a profile file (`.tabrion/studio.toml` or `profiles.toml`) is
  the error `config.url_character`, as in tabrion 0.2's profile file reader (`profilefile.value`, tabrion G-T2 review
  B-1: in a SQLAlchemy URL `%` is unquoted, so `%2F%2Fhost/share/x.db` would become a network path). Such a project
  file is shown as invalid with that issue and cannot be trusted; the value is not repeated. Studio's own connection
  never read the path as a URL; this aligns the SQLite `path` with Tabrion's reader (not the PostgreSQL `database`,
  which Tabrion's reader checks more strictly; docs/TODO.md TD-013). A profile in your own `profiles.toml` with such a
  path is skipped and shown with that issue.
- TD-056 / GR-5: with tabrion 0.2.0, a database `DETAIL:`, `HINT:`, `CONTEXT:` or `QUERY:` part in the middle of a
  one-line log message (one blank after the label, no database exception) is removed from Studio's logs; a canary
  test through Studio's log setup pins it (it fails with tabrion 0.1.0).

- TD-061 (review L-6, re-check R-1): `scripts/check_licenses.py` reads SPDX parentheses as groups (AND binds tighter
  than OR), so `(MIT OR GPL-3.0-only) AND LGPL-2.1-only` now fails; a malformed expression fails too, and a trove
  classifier name with parentheses (`ISC License (ISCL)`) stays one name. `scripts/release_bom.py` fails on a license
  record of a package that the lock does not pin (other than `tabrion-studio`), also without `--strict`, instead of
  ignoring it.

- A-01 of the security assessment (TD-065): every start listens on a fresh random loopback address `127.a.b.c`
  instead of `127.0.0.1`, also with `--port`, and answers only that address and port. Nothing another local program
  left in the browser for an earlier Studio origin (a service worker, storage) applies to a later Studio. Where only
  `127.0.0.1` is configured (macOS by default) Studio uses it, and with `--port` says that the address is then the
  same every time; the guide advises leaving `--port` out on a shared computer. The instance info file records the
  address. The page removes any service worker found on its origin. For `npm run dev`, set `TABRION_STUDIO_BACKEND`
  to the printed address.

- Low and Info findings of the security assessment (TD-067):
  - A-02: a connection that does not send a complete request within 10 seconds of the server waiting for one is
    closed (`server.RequestTimeoutProtocol`; arriving data does not extend the time, a long query is not cut).
    Records of Uvicorn's and asyncio's loggers are limited to 20 per first line and minute (`logs.FloodFilter`), so
    such records can no longer rotate the log away. Uvicorn's `limit_concurrency` is deliberately not set.
  - B-3: connections of project profiles, and the migration worker's, refuse a server's request for the password in
    clear text (libpq `require_auth=!password`). User profiles are unchanged; with a libpq older than 16 Studio logs
    `studio.require_auth_unavailable`.
  - C-01: the control token and every password stay registered for log redaction for good; only launch tokens,
    session IDs and CSRF values share the bounded store.
  - C-02: an exception inside a list, tuple, set or dict log argument is formatted by its type only, like one passed
    directly; containers too deep or too large to check are not formatted.
  - C-04: only the `keyring` package's own OS backends count as a keyring; another package's backend only when named
    in the new variable `TABRION_STUDIO_KEYRING_BACKEND`. The selected backend is logged (`studio.keyring_backend`).
  - B-4: the README, the user guide's start page and the header's read-only hint no longer say that Studio never
    writes, but that it sends only reading statements and that only a role without write privileges is a hard
    boundary.
  - C-08: the uninstall section names the query history, the SQL editor drafts and the favorites in `state.sqlite`.

- B-1 and B-2 of the security assessment (TD-063): the read-only statement check now recognizes every call form of
  refused functions, and also refuses pg_surgery's `heap_force_kill` and `heap_force_freeze` and pg_visibility's
  `pg_truncate_visibility_map`. The PostgreSQL check-in reset closes the session's dblink connections. A column named
  like a refused function can no longer be written as `table.column` (the bare name and aliases still work). The
  database role stays the boundary; the check is defense in depth.

- `tabrion-studio diagnose --output`: hardened against a folder swap. On POSIX the output folder is opened once and
  the checks, the temporary file and the final move all act on that opened folder; the guide now says that a folder
  link in the path is followed and what Windows does (review D-1 of PR #85). The final move never follows a link
  at the temporary file's name, and a failed move leaves no temporary file (review L-1, L-2 of PR #88).
- Drafts (G-S1 delta audit A-1): a draft text deleted while another connection was reading the state database (for
  example a second Studio instance on the same state folder) could stay in `state.sqlite-wal` until the next history
  activity, settings save, start or close, and with `[history] enabled = false` longer. Every read and save of the
  drafts (and their purge) now retries a pending checkpoint, with the history on or off; it still never waits for the
  reader.
- TD-055: Studio's log redaction no longer depends on Uvicorn's loggers being unconfigured. `configure_logging`
  takes over Uvicorn's and asyncio's loggers (their own handlers removed, propagation on, the redaction filters on the
  loggers themselves) and restores them on close, so a `uvicorn.Config` with the default `log_config` or another
  `dictConfig` in the same process cannot make Uvicorn print a failed lifespan's traceback text unredacted. The
  `tabrion-studio` command was not affected (it passes `log_config=None`). Tests restore the logging state after
  each one, which fixes the lifespan canary failing after `tests/unit/test_server.py`.

- Security review F-6 / TD-031: the Python runtime dependencies are installed from the hashed lock
  `constraints/runtime-lock.txt` (exact versions, SHA-256 of every file, `pip --require-hashes --no-deps`, wheels
  only) in every `ci.yml` job, in `scripts/smoke_wheel.py` and in the installation guide; Tabrion stays pinned by its full
  commit SHA. `scripts/runtime_lock.py check` keeps the lock inside the unchanged `pyproject.toml` ranges and in step
  with `build-constraints.txt`, `check-installed` fails a CI job whose runtime closure differs from the lock, and
  `update` regenerates it (`docs/development/ci.md`, "Hashed runtime pins"). The CI wheel builds now take the build
  backend from the hashed `build-constraints.txt`. `tests/security/test_workflows.py` now loads every workflow with PyYAML
  (a dev dependency), because an unparsable `ci.yml` ran no job and no check noticed (review of PR #76, B-1/M-1).

- STU-016: the independent security review was re-run on master `2a8a229` and accepted (F-1..F-4, F-8, F-9 fixed in
  PR #65 and PR #66). `security-review-prep.md` and `docs/TODO.md` record this and the owner decisions still open
  for G-S1 (F-5/TD-033, F-6/TD-031, F-7/T-16, TD-005, TD-002/TD-007, TD-003/TD-008, the PostgreSQL read-only model);
  G-S1 is not passed, and the human review before a public release (STU-028, G-R) stays required. New TD-053.
- STU-016 review F-7 (T-16, TD-054): the migration code trust also covers the project modules under the
  `prepend_sys_path` folders (every `*.py` in identifier-named folders, by path and bytes), so a changed or new
  module asks for trust again. The digest is versioned: a project trusted before needs trust again. Over the caps
  (5 000 modules, 32 MB, 256 KB per module) or with a link out of the project, the code cannot be trusted. The
  worker runs only the hashed module bytes, and the trust dialog lists the modules and says what is checked (en/de).
  Review C-1 of the fix: project folders on the interpreter's path are loaded through the hashed loader too. An
  import guard first on `sys.meta_path` holds every finder (also those of an editable install in Studio's
  environment) to the hashed bytes for any module in the project folder outside Studio's installation, and refuses
  the rest; the worker starts with `-S` and runs `site.main()` under the guard. Studio running from a source
  checkout inside the project does not read the revisions (fail closed). The page names the file when the trust
  state is invalid because of one file (`issue_file`, review I-1). Delta review: the guard goes back to the front of
  `sys.meta_path` after `site.main()`, and the worker fails closed when a finder is in front of it before the
  revisions are read (C-2); a namespace package from Python's path finder is no longer refused (N-1).
- STU-016 review, read-only path: the statement check refuses write-capable statement forms on the read-only path
  (any `INTO` outside literals, comments and quoted identifiers, and a number directly followed by a word character,
  so numbers must be written in decimal) in the editor, the
  export route and every other path; PostgreSQL sessions drop temporary tables when they go back to the pool
  (`DISCARD TEMP`); the diagnostic `STU-DX-003` also reports `CREATE` on a schema (warning, error on PROD) and `TEMP`
  on the database (an info on its own). (F-1)
- STU-016 review, SQLite: the authorizer's function branch is an allowlist of SQLite's built-in reading functions,
  so `fts3_tokenizer`, `fts5`, the FTS4 maintenance and R-tree debugging functions and anything a build adds are
  denied; every SQLite session sets `trusted_schema = OFF` before the authorizer. (F-2)
- STU-016 review, PostgreSQL side-effect functions: the statement check also refuses functions whose effect a
  read-only transaction does not stop (other sessions, the WAL, replication slots and origins, transaction IDs,
  server reloads and resets, server files, index maintenance, every `dblink` function, and `connectby` and
  `xpath_table`, which run a query given as text); the list and the reason for each are in
  `docs/architecture/connections.md`. (F-3)
- Request bodies are capped while they stream in, before any route reads them (STU-016 review F-4). A body over the
  route's cap gets 413 without any of it being kept: a chunked body is stopped one chunk past the cap, and a declared
  `Content-Length` over the cap is never read into memory. Before the 413, the rest of the body is read and discarded
  (at most 4 MiB and 2 seconds), so the client gets the 413 instead of a connection reset; a declared length more than
  that over the cap is answered at once. The answer closes the connection. Caps: 4 KB for `/api/session` and the
  control endpoints, 2 MB for `/api/queries` and `/api/exports` (`studio.query_too_large` as before), 16 KB for every
  other route (new problem code `access.body_too_large`). The routes' own, smaller caps are unchanged; an oversized
  `/api/session` or `/api/control/launch` body is now 413 `access.body_too_large` instead of 400
  `access.bad_request`.
- The access middleware checks the control token itself for every request under `/api/control/` except `GET` and
  `HEAD` of the proof, so a control route added later is never reachable without it, and an `/api/*` path with a `.`
  or `..` segment is 404 before routing (STU-016 review F-8).
- `docs/architecture/security-review-prep.md` records the outcome of the first independent security review (not
  accepted at `e186178`): SQ-05, SQ-07, T-06 and T-13 are not met until the read-only hardening PR is merged and
  re-reviewed, and F-5, F-6 and F-7 are owner decisions (STU-016 review F-9).
- Catalog reads now use a fixed search_path; hardening against object resolution through user schemas (STU-022
  review). User SQL, the viewer and the exports keep the session's `search_path`.
- Opening a PostgreSQL connection now runs the driver's and SQLAlchemy's setup queries under a fixed search_path;
  hardening against object resolution through user schemas (TD-039). The session's own `search_path` is restored
  before the connection is used, so user SQL, the viewer and the exports see the path the user configured.
- `hstore` values now come back as the server's text (`"a"=>"1"`) in query results, the data viewer and the exports,
  not as a mapping: the driver's native hstore adaptation is off, so no type lookup runs when a connection is opened
  (TD-039).
- D-01 shows where each PostgreSQL connection's password goes (TD-010 T-5, T-4 rest; `stu-007-review.md` L-1): the
  secret's source, `host:port` and the TLS mode, marked for a host outside loopback, TLS weaker than `verify-full`, a
  variable that looks like another service's credential and a password file outside the project. Projects trusted
  before this change show as changed once and must be confirmed again.
- Conditions of the reviews of PR #7 (`stu-004-review.md`) and PR #8 (`stu-006-review.md`):
  - L-1 (C-1): exception text that reaches a log record as its *message* is removed too. Records of the `asyncio`
    logger keep only a known fixed first line (no `repr` of a task, future or callback with its exception or
    arguments), a formatted traceback in a message (Starlette's lifespan failure, which Uvicorn logs as text) is cut,
    Studio's lifespan logs its own exception with frames only (`studio.lifespan_failed`), and an exception passed as a
    `%s`/`%r` argument formats as its type. L-2: the scrub also removes `db_password=`, `PGPASSWORD=`, `*_token=`,
    `sslpassword=`, `auth_secret:`, `privatekey=`, `Authorization: Basic ...` and URL passwords that contain `@` or
    `/`; every pattern runs in linear time. L-3: a traceback keeps the first and last 20 frames of each exception and
    is capped at 32 KB per record.
  - T-1 (C-1): a trusted project file stays trusted only while the review equals the one the user confirmed, so a
    database folder that later becomes a link out of the project makes it `changed`; `ProjectConfig.database_file`
    re-checks containment when a database file is opened (for STU-009).
  - T-2 (C-2): UNC (`//host`, `\\host`), device (`\\?\`, `\\.\`), drive-relative (`C:foo`), `file://host` and
    `/net/<host>` paths in a profile file are refused as `config.network_path`, and containment is decided as text
    before any file-system access; no value from a project file is passed to `Path.resolve()` (on Windows that opened
    an SMB connection to the named host).
  - T-3: deeply nested TOML is `config.too_deep`, a damaged trust record means untrusted, and the user's own profiles
    still load when the project file cannot be read (no more HTTP 500). T-4: only `localhost`, a loopback IP address
    and a socket folder default to `sslmode = "prefer"`; `127.evil.example` gets `verify-full`.
  - I-2: D-01 shows "Unknown keys removed" and says when it shows a file that changed after it was opened
    (`studio.trust_stale`); an invalid project file gets a notice with its issues (English and German).

- STU-004: access refusals no longer repeat request data in their text (`access.forwarded_not_allowed` named the
  forwarded headers); the process's control token, launch tokens, session IDs and CSRF values are registered for log
  redaction when they are created.

- Review of PR #4 (`stu-003-review.md`), S-1: when the server no longer knows the tab's session (a restart, or a
  program on another local port that replaced the session cookie), the page stops the heartbeat and says "Your session
  has ended. Run `tabrion-studio` again to open Studio." The cookie-tossing residual risk is documented.

- Review of PR #1 (`stu-002-018-review.md`): the control client makes the server prove the control token (HMAC over a
  client nonce) before it sends the token, and opens only launch URLs of that instance; stale info files are removed
  when the lock is taken and at the start of a shutdown (F-01). SIGHUP and Windows CTRL_CLOSE stop Studio cleanly
  (F-04). Only authenticated control requests and same-origin heartbeats (`GET /api/heartbeat`) reset the idle timer
  (F-06). Unsafe state directories are refused: group- or world-writable or foreign-owned on POSIX (F-10), outside
  the profile without a private ACL or behind a junction on Windows (F-07). `[::1]` on the same port is held (F-08).
  ci-ok reports NOT OK for unreviewed changes to `.github/workflows/**` or `.github/scripts/**` (label
  `workflow-change-reviewed`, F-03) and retries the status post, failing loudly (F-11).
- STU-003 binding requirements of the review (`stu-002-018-review.md`, condition C-3): the browser is opened with the
  `file://` path of a 0600 redirect file and never with the token URL, so no process argv shows the token; the file
  is deleted when the token is used or expires, at shutdown and at the next start (F-02). The Host allowlist covers
  every route, and the control endpoints refuse requests with `Origin` or `Sec-Fetch-*` (F-05). `localhost` and
  `[::1]` are not answered (F-08). A session cookie alone opens nothing without its CSRF value (F-09). Requests with
  a valid session count as activity; the heartbeat needs the session (F-06, R-5).
- Re-review recommendations: the control client sends the proof request and the request with the token over one
  keep-alive connection and never reconnects in between (R-4). The workflow-change gate guards every file under
  `.github/` except the issue and pull request templates (R-3), accepts the label `workflow-change-reviewed` only if
  it was set after the latest push (R-2), and the `ci-ok` status description names its run; the merging party checks
  the ci-ok run link before every merge (R-1).

### Changed

- Database creation moved to the admin area (G-S4; owner grant of 2026-11-18, which assigns database creation and
  role management to the admin area with separate permissions; all flags stay off by default). The STU-CR-2 and
  STU-CR-3 wizards are now `POST /api/write/database-manager/create/sqlite` and `.../create/postgresql`, behind the
  `database_manager` flag instead of `schema_designer` (405 and nothing of `database_manager.create` imported
  otherwise, also with `schema_designer` on). A PostgreSQL database needs the connection's admin unlock; a write
  unlock is refused (`studio.method_not_allowed`), and the admin check runs again before `CREATE DATABASE`
  (`studio.admin_over_privileged`, which ends the unlock). A SQLite file needs the flag but no unlock, because no
  role exists to check. The audit actions are `dbm.create_sqlite` and `dbm.create_postgresql` in the area
  `database_manager`. The folder rules, name rules, Windows refusal, PROD lock and audit are unchanged. The page
  shows the wizard only while `database_manager` is on and offers the admin profile (en, de).
- A write role that has or reaches CREATEDB is refused like decision 1A (`studio.write_role_over_privileged`), at
  the unlock and again at the start of every run (`admin.createdb`, now `dangerous` for write roles); for admin
  roles CREATEDB is a fact on DEV and TEST and a warning elsewhere. STU-ADR-0007 section 1 and STU-ADR-0008
  (sections 1, 6, 10 and 11), the threat models (TX-29, TX-40), the G-S3 and G-S4 definitions (GS3-03, GS3-21,
  GS3-27, GS3-28, new GS4-27), the guides and the problem texts are amended. Tests moved to `test_dbm_create.py`
  and `test_dbm_create_postgres.py`, with new cases for the `schema_designer` flag alone, a write profile, a write
  role with CREATEDB and a right gained after the admin unlock.
- The eight STU-XDM-3 owner points (PR #140) are recorded as decided per G-S3/G-S4 grant 2026-11-18, safest option
  (STU-ADR-0008 section 11).

- tabrion pin moved to the merge commit of tabrion PR #90 (`28c8839f72a2d7232d973c71f99df1e57001a260`, still
  0.3.0.dev0; range `>=0.2,<0.4` unchanged). It adds the experimental CR-DBX primitives TAB-XW-2 (schema level 2),
  TAB-XW-3 (`tabrion.ddl`), TAB-XW-4 (`tabrion.plan`), TAB-XW-7 (SQLite restore and file deletion), TAB-XW-8 (role
  DDL) and TAB-XW-9 (PostgreSQL create and drop database); Studio uses none of them yet, so nothing else changes.

- STU-XDE-2: a PROD connection has no "Edit data" tab even with the `data_editor` flag on (the server refused its
  unlock before too), and the Data Editor's "no write profile" text points to Settings, where the write and admin
  profiles are (it named Connections).

- CI: every leg of `backend test` (Python 3.11 to 3.14 on Linux, macOS, Windows) now runs in the same two shards,
  `backend test (<leg>, security)` and `backend test (<leg>, other)`: the Linux legs took 17 to 19 minutes, and the
  master push run of `3af4b79` was cancelled at the 20-minute timeout in `backend test (Python 3.12)` after 98 % of its
  tests had passed. The timeout is unchanged; `.github/ci-manifest.json` lists all twelve shards (TD-073).
- CI: the Windows leg of `backend test` runs in two shards, `backend test (windows-latest, security)` (`tests/security`)
  and `backend test (windows-latest, other)` (the rest): the whole suite took 18 to 20 minutes against the job's
  20-minute timeout and was cancelled after all tests had passed. The timeout is unchanged; `.github/ci-manifest.json`
  lists both shards.
- STU-026: Studio is built and tested against tabrion 0.3.0.dev0, the merge commit of tabrion PR #80
  (`72e2d03fea2b83e159a4ae9df3b7c40246b8a232`; untagged, no release; it contains tabrion 0.2.2), pinned by full SHA in
  `constraints/tabrion-integration.txt`; the range in `pyproject.toml` is `>=0.2,<0.4`.
- TD-071: Studio is built and tested against tabrion 0.2.1, the untagged merge commit of tabrion PR #71
  (`af0458d54467571abfc1afdd44d76e1f4d727c06`; no tag, no release), pinned by full SHA in
  `constraints/tabrion-integration.txt` (was 0.2.0, `52353da`). The range `>=0.2,<0.3` stays. tabrion 0.2.1 has no
  API change and the same runtime dependencies, so `constraints/runtime-lock.txt` is unchanged
  (`runtime_lock.py update` writes it byte for byte, `check-installed` passes); no `tabrion_compat` shim goes away.
  tabrion 0.2.1 declares `LicenseRef-Proprietary` instead of MIT; `scripts/check_licenses.py` already lists it as a
  first-party exception, and the release BOM tests use that license now.

- TD-013 (part): Studio is built and tested against tabrion 0.2.0, the untagged merge commit of tabrion PR #65
  (`52353daee6cd6e37a7efc45b74061a48beb2b280`; no tag, no release), pinned by full SHA in
  `constraints/tabrion-integration.txt`; `pyproject.toml` takes `tabrion[migrations,postgres,sqlalchemy]>=0.2,<0.3`.
  The runtime dependency set is unchanged (tabrion 0.2.0 changes only its `dev` extra), so
  `constraints/runtime-lock.txt` stays as it is. Reflection (explorer, definitions) uses tabrion's
  `Database.inspector()` instead of the shim `tabrion_compat.inspector`; on PostgreSQL the fixed catalog search path
  is still set first (`tabrion_compat.pin_catalog_path`). A SQLite `path` with `?`, `#` or `%` is now refused (see
  Security). The other shims stay for now (docs/TODO.md TD-013).

- TD-032: the English `detail` and `remedy` of Studio's findings and backup hints use plurals ("2 rows" instead of
  "2 row(s)"), and the check names in `STU-DX-008` are made from `params`; the page's note that texts are English now
  shows only when one of tabrion's texts is listed.
- STU-016 docs (G-S1 decisions; G-S1 itself is not yet granted): `docs/architecture/security-review-prep.md`
  records the owner's acceptance on 2026-11-13 of the 13 recommendations of the G-S1 decision package (each option
  A: decision, accepted residual risk, follow-up, whether it is a G-R precondition); the formal G-S1 confirmation on
  a concrete master SHA is pending. `docs/development/release-checklist.md` lists the G-R preconditions from those
  decisions (GR-1..GR-5: TD-002, TD-007, TD-003 with a check on a real Windows machine of device names and OneDrive
  folders, TD-056) and the remaining install risks (`uv tool install` takes the lock's versions but does not verify
  its hashes, pipx resolves afresh without hashes; pip with `--require-hashes` is the secured path), and notes review
  R-1 under TD-061. `guide/installation.md` marks the hashed pip path as the secured one and the pipx/uv path as
  less secured. The user guide (`guide/security.md`, `guide/connections.md`) and `architecture/connections.md` say
  prominently that Studio's read-only checks are defense in depth and that a really read-only PostgreSQL role is
  what makes write protection reliable, with a generic example of such a role (`GRANT USAGE`/`SELECT`, default
  privileges, `PUBLIC` rights, functions with side effects); `guide/security.md` names the accepted residual risks.
  `docs/TODO.md`: the affected TD entries and the STU-016 row updated, TD-062 (the JR-N1 PROD e2e test) added.
- STU-028 docs: the user guide's index and the README name the 1.0 features on `master` (history, favorites,
  scripts, Format, completion, the ER diagram); `guide/installation.md` has a "pipx or uv" section (bash and
  PowerShell; `uv tool install` with the lock's versions, pipx with the Tabrion pin only, since pip refuses the
  hashed lock as a pipx constraint; it says that neither checks the lock's hashes) and says that no Git-URL install works (the wording of 03 §4.6; a note in ADR 0001); the docs checklist gains D-24..D-26.
- STU-020 (E-1 and the Info item of the delta review): script names may hold the zero-width non-joiner U+200C and the
  zero-width joiner U+200D between two letters or marks (Persian `می‌خواهم.sql`, Indic conjuncts; a ZWJ also inside
  an emoji sequence) and the ideographic space U+3000 inside a name, like U+0020 (Japanese `売上　集計.sql`). A
  joiner at the start or end of a name, doubled or next to a space, a dot or a digit, the bidi controls and the other
  format and blank characters stay refused; the save dialog checks the same rule. The page keeps showing the joiners
  escaped (`\u200C`) in the Open… list and in tab titles, so look-alike names stay apart. `GET /api/scripts` counts
  the folders and `.sql` files it leaves out for their name in `skipped_invalid`, and the Open… dialog says how many
  (en/de). Another instance's write lock on the state database is logged at info level as `studio.state_busy`
  without a traceback; other database errors stay `studio.state_error` warnings.

- e2e (TD-050, joint regression JR-N2, PR #68): `backend.spec.ts` removes its `studio-e2e-*` temp directory after
  the server has exited, like the other real-server suites; before, every run left one behind.
- CI (STU-050, TD-047): the e2e suites also run in Firefox and WebKit, in the jobs `e2e (firefox)` and
  `e2e (webkit)` with the browsers of the locked Playwright version; advisory in `ci-manifest.json` until both are
  green. `STUDIO_E2E_BROWSERS` picks the browsers locally (Chromium by default); the heap measurements through CDP
  skip outside Chromium with a "chromium only:" reason, the only skip those jobs accept.
- e2e (TD-047, PR #63): the suites pass in Firefox and WebKit too, with no product change. The real-server suites
  share one CSP watcher (`recordViolations`) that also hears Firefox's "Content-Security-Policy:" reports (the old
  pattern never matched them, so those checks passed vacuously in Firefox) and leaves out only WebKit's own report for
  a `<select>`, paired with its violation event; `e2e/csp.spec.ts` proves both per browser. The Tab test of the SQL
  editor checks the focus lands on the next control; the tooltip test reaches `:focus-visible` with Tab; Copy DDL is
  checked for the exact text in every browser; the saved script's offered name is checked as the page offers it;
  the keyboard-only connect flow reaches "Connect" with Shift+Tab instead of relying on the focus wrapping around.
  After the review: the watcher also counts the page's own `securitypolicyviolation` events in every browser (WebKit's
  select exempt once per element), `connection-lost-backend.spec.ts` uses it too, and the Tab test also checks Tab
  without a result.
- CI (TD-047, PR #67): the jobs `e2e (firefox)` and `e2e (webkit)` are required. They lost `continue-on-error` and
  their `advisory` entries in `.github/ci-manifest.json`, so `ci-ok` fails when either fails, like `e2e`.
- The view paths `/connections` and `/settings` answer `HEAD` as well as `GET`, like `/` and the static files
  (review U-4).
- The dialog's initial focus escapes the action ID in its selector (`CSS.escape`), so an ID with a quote or a
  backslash focuses its button instead of throwing (review U-2).
- `docs/development/building.md` explains the Chromium build that the locked Playwright expects and how to use a
  preinstalled browser through `PLAYWRIGHT_CHROMIUM_EXECUTABLE` (review U-5).

- `/api/heartbeat` and `/api/openapi.json` need a session. The control endpoint `POST /api/control/launch` takes
  `{"redirect": true|false}` and answers the launch URL and the redirect file. The Vite development proxy presents the
  backend's own Host and Origin.

### Fixed

- Test: `test_uvicorn_stops_reading_an_endless_chunked_body` also accepts `ConnectionAbortedError` (Winsock
  `WSAECONNABORTED`, 10053), which Windows raises instead of `ConnectionResetError` when its stack has already
  dropped the connection the server closed; the bound and the closed-connection requirement are unchanged.

- `scripts/check_tabrion_api.py --surface` no longer reports `tabrion.errors.Kind.PERMISSION.value` as missing from
  Tabrion's surface: Studio names the `Kind` member (a `StrEnum`) instead of its `.value`. The Data Editor's end-to-end
  teardown waits for the server to stop before it removes the folder of `write-areas.toml`, so the audit log no longer
  reports a failed write on shutdown.

- A configuration file nested deeper than 64 levels (arrays, inline tables and the segments of dotted keys and table
  headers) is refused (`config.too_deep`) before the TOML parser runs, so the parser no longer recurses to the
  interpreter's limit, where a finalizer run by the garbage collector (an event loop's) failed with its own
  `RecursionError` (seen in CI on Windows, Python 3.13).
- The data viewer and the SQL editor show an array of dates, decimals, UUIDs, times, intervals, bytes or non-finite
  floats item by item, each as it is shown alone; such a table or result answered 500 before (review I-3 of #120).
- TABRION QA-7: `scripts/check_tabrion_api.py --surface` no longer reports a `__x__` name such as
  `tabrion.postgres.SessionSettings.__dataclass_fields__` (axentra) as missing from Tabrion's
  `docs/api/surface.json`. Such names are public (the `__all__` check already skipped them) and have no entry of
  their own; `--surface` now checks the name before the first `__x__` part (here the stable
  `tabrion.postgres.SessionSettings`). The docstring names tabrion-studio's copy as the source and nodestro-api as
  a new copy holder; the test has a case for dunder names and pins the new SHA-256 (`e48cc7f230c1…`), the same in
  tabrion-studio, axentra, orkexa, nodestro-runtime and nodestro-api. Tooling only.
- QA-S1 S1-32 (TD-070, owner decision of 2026-11-14 14:13Z, option A): after "Disconnect" in the connection manager,
  "Run" in the SQL editor no longer opens the connection again by itself. It opens nothing and sends no query; the
  banner "Disconnected: <name>" ("Verbindung getrennt: <name>") with "Reconnect" shows, and the editor says that
  nothing was run. The same holds for "Show status" (migrations), "Run diagnosis" and "Inspect" (vector data). Only
  "Reconnect" opens the connection again and then runs the requested query or goes on with the view; on a PROD profile
  "Reconnect" asks D-02 again first, and "Cancel" leaves it disconnected. A connection the editor opens now becomes
  the header's connection, so the header no longer says "No connection" while the editor reads on it. The page's
  connection session tells the user's disconnect from a lost connection; after a loss the editor still opens a DEV
  connection again by itself, as before.
- QA-S1 (TD-069): a mouse click on a table's box in the ER diagram shows the table in the explorer, as Enter on its
  name does (S1-09). The SQL editor no longer loses a tab or its latest text when the page leaves it within the save
  delay, for example to open a history entry in the editor (S1-15): the pending draft save is sent when the editor
  goes, and an editor reads the drafts only after the earlier saves have landed. Each draft save is aborted after 10 s,
  so a save the server never answers no longer holds back every later one until the page reloads. A request refused
  because the tab's session ended (a second start, `access.csrf_invalid`) says so in English and German and how to go
  on, instead of the raw code; `access.session_required` also covers a tab that was never signed in, and every other
  `access.*` code has a text too (S1-32).
- Connections: two concurrent opens of one profile (the migrations page after a profile switch, or "Reconnect" and
  another view at once) could both connect; the second connection was closed again, but the database saw two
  logins. Opens of one profile now run one at a time behind a per-profile lock, so the second open waits and gets
  the first one's connection; opens of other profiles do not wait. The check after the connect stays as defense in
  depth (TD-027, F-2 of the #32 page delta review).
- SQLite: a function call that Studio's authorizer denies was answered as `db.unknown` ("Database error"), because
  SQLite reports it as a plain `SQLITE_ERROR` and tabrion 0.2 does not classify its text. Studio's error layer now
  answers it as `db.permission` (403), like a denied table or pragma; only an error of the sqlite3 driver with that
  code whose message starts with SQLite's fixed text matches. The statement is refused as before (TD-053).
- Export: starting the next export on a connection right after the previous one was downloaded to its end could
  be refused with `studio.export_busy`, because the finished export freed its slot only after its last log line.
  The slot is now freed as soon as the export's transaction has ended, before the end or the error reaches the
  client; an earlier export can no longer free a later one's slot (TD-057).
- Export and query engine: when a worker or its timeout timer could not start (no more threads), the export's slot
  or a `running` query stayed taken until restart, and the export's download waited forever. Both now end with an
  error and free their slot (TD-057, review F-1 of PR #80).
- The accessibility audit's Tab-stop walk (STU-053) measures each stop one task after the focus moved, because WebKit
  scrolls a newly focused element into view from a zero-delay timer; the explorer's selected tree row, below the
  fold since the "View" group was added, read as out of view now and then in the WebKit job (TD-058). Test only.
- Tests: `tests/unit/test_server.py` built a `uvicorn.Config` with Uvicorn's default logging config, which stops the
  `uvicorn` loggers from propagating for the rest of the run; when it ran before `tests/security/test_canary.py`
  (`pytest tests/unit tests/security`), the lifespan test there saw no Uvicorn record and failed. It now passes
  `log_config=None`, like the product.
- Theme switch: buttons kept their old background for 120 ms (a transition) while their text took the new theme,
  so for that moment a primary button's label had too little contrast (TD-047, found by axe in WebKit). The theme now
  switches with transitions off, and text and background change together.
- The README's install command had no Tabrion pin and failed (`No matching distribution found for tabrion`); a test
  now checks every documented install command. The README status names what is on `master`. The settings page no
  longer says that SQL statements are logged when "Write SQL statements to the log" is on: the setting is saved but
  not used yet (TD-013); it says when the row limit and the timeout apply.
- The export dialog's format, column and option choices are 24 px high targets; they were 21 px (STU-053 F-6,
  WCAG 2.5.8).
- SQL editor: a cancel (Ctrl+., Escape, the button, or closing the tab) while the start request of a query was still
  on its way was lost: the page had no query id yet, so it sent no cancel, and the query ran on until its timeout
  while the page showed it running. The page now cancels the query as soon as the server answers with its id. This
  was the cause of `editor-backend.spec.ts` › "cancel stops an endless query within a second" failing under load. That
  test now waits for the start to be answered and takes the cancel time in the page (key to "Canceled"), not as the
  test's wall time, which included the retry schedule of `expect`.
- `tabrion-studio` no longer ends with a `BrokenPipeError` traceback and a non-zero exit status (1, or 120 with buffered
  output) when the terminal or the parent process stopped reading its output before a clean stop; the last line is
  dropped and the exit status stays 0. Other errors writing that line are still raised (TD-044, joint regression JR-1).
- STU-050: the start page said the project and connection views "arrive with the next versions"; it now leads to
  Connections, Explorer, SQL editor, Migrations and Diagnostics (en/de).
- STU-050: connection failures (a missing or linked SQLite file, a file that is no database, an unset password
  variable, an unreachable server, a timeout, settings the driver refused, a full disk) now say what to check, as
  08 section 5 asks.
- STU-050: the migration view showed an empty status bar under its header before its first message.
- `e2e/explorer-reference.spec.ts` can run its PostgreSQL part against a server with password authentication
  (`TABRION_STUDIO_E2E_PG_PASSWORD`).
- TABRION QA-21: `scripts/check_tabrion_api.py --surface` no longer reports enum members and dataclass fields as
  missing. Tabrion's `docs/api/surface.json` lists them in the parent entry (`members`, `fields`), not as names of
  their own, so `tabrion.TLSMode.AUTO` failed against tabrion v0.1.0; such a name is now checked against its parent's
  entry and passes when the parent is stable (`S`). Tooling only; the four copies stay identical.
