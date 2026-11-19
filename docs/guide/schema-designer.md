# Schema Designer (in development)

!!! danger "In development, not released"
    The Schema Designer is **not released or supported**. It exists only behind the `schema_designer` switch in
    `write-areas.toml`, which is **off by default**; no Studio version turns it on. Its security gate G-S3 is
    effective since 2026-11-19 for local DEV, TEST and STAGE only (PROD stays locked); the human security review and
    the release decision are still open, so nothing in this page is a release of the feature.
    This page describes what the designer does when a developer turns the switch on in a test setup, so that its
    behavior and its limits can be reviewed.

The **Schema Designer** section on the **Connections** page lets you design the tables, columns, keys, constraints
and indexes of one schema of a development or test database, and shows the plan Tabrion makes for the change: every
step with its risk classes, whether it runs in a transaction, its exact SQL, and the plan hash. On a **DEV** or
**TEST** database that no migration tool manages, you can confirm the plan and run it (see
[Run a plan](#run-a-plan)).

Designing and previewing never write to the database, the project folder or the disk. Only a confirmed run changes
the database, and only after Studio made its backup where one is needed.

## Its limits at a glance

These rules hold for every change the Schema Designer makes; the sections below say how each is checked.

- **Alembic projects get a revision, never a run.** When the project has an `alembic.ini`, or the database has a
  migration version table (Alembic, Nodestro, Axentra, Levuvo, or a SQLite `user_version` above 0), Studio never
  changes the database itself. For an Alembic project it can only write the plan as a new revision file into the
  trusted project, which you apply with your own tooling; it never runs `env.py`, project code or an Alembic command
  ([Write an Alembic revision](#write-an-alembic-revision)).
- **PROD is locked.** A PROD connection is never unlocked: Studio designs, plans, runs and writes revisions only for
  DEV and TEST. A PROD schema can only be compared, read-only ([Compare schemas](#compare-schemas)).
- **A direct run only on DEV or TEST without migration management**, only from your own profiles, only after the
  preview, the confirmation and a drift check right before the run; everything runs in one transaction
  ([Run a plan](#run-a-plan)).
- **The backup rule.** Before a plan that removes, rebuilds or changes data, Studio makes a backup and runs nothing
  when the backup fails: a copy of the SQLite file, or on PostgreSQL a checked `pg_dump` archive. Only on DEV may a
  SQLite backup be left out, typed and recorded. **`pg_dump` is not a complete backup**: global roles, role
  memberships, tablespaces and other cluster objects are not in it ([The backup](#the-backup)).
- **Read the preview's warnings** before you confirm: views, triggers, policies and functions are not in the draft,
  the fingerprint does not cover everything, a rebuilt SQLite table may get new `rowid` values, and a locking step
  waits at most 5 seconds ([The plan](#the-plan)). A rename is planned as a drop and an add: its data is lost.
- **No role is changed here.** The Schema Designer creates no role and grants nothing. Role changes need a separate
  admin profile with the least rights it needs, set up outside Studio, under the Database Manager and its gate;
  Studio never grants `SUPERUSER`, `BYPASSRLS`, `CREATEROLE` or `REPLICATION`
  ([Database Manager](database-manager.md)). On PostgreSQL, the write role a run uses is checked for too many rights
  before each run.

## Turn it on for development

The switch is read once, when Studio starts, from `write-areas.toml` in Studio's configuration folder, as for
the [Data Editor](data-editor.md#turn-it-on-for-development) (creating a database is not part of the Schema
Designer; it is the Database Manager's, see [New database](new-database.md)):

```bash
mkdir -m 700 -p /tmp/studio-dev/state/config
printf 'version = 1\n[areas]\nschema_designer = true\n' > /tmp/studio-dev/state/config/write-areas.toml
chmod 600 /tmp/studio-dev/state/config/write-areas.toml
tabrion-studio ./my-project --state-dir /tmp/studio-dev/state
```

Nothing else turns it on, and the `data_editor` and `database_manager` switches do not open it. With the switch off,
the section is not shown, every route of the designer answers `405` and its code is not even loaded.

## Design a change

1. **Connect** to a database of one of your own profiles that is not PROD, and add a write profile coupled to it
   (see the [Data Editor](data-editor.md#before-the-first-edit-a-write-profile)). The designer works on SQLite and PostgreSQL.
2. In **Schema Designer**, choose the connection and **unlock** it by typing the write profile's name. The unlock
   ends after 15 minutes without use, when you lock it, reconnect or change the profile.
3. **Start draft**: enter the schema (empty means `main` on SQLite and `public` on PostgreSQL). The draft starts from
   the tables of that schema as they are now, read on the write connection.
4. Edit the draft, one change at a time:
    - **Add table** (a name and its first column), **Rename table**, **Drop table**;
    - **Add column**, and per column **Change** (type, nullable, default), **Rename** and **Drop**;
    - **Add constraint**: a primary key, a unique constraint, a foreign key (to a table of the draft, with an action
      on delete) or a check; **Drop** next to each key and constraint;
    - **Add index** (unique or not, optionally partial) and **Drop** next to each index.
5. **Show plan** shows the plan from the database as it is now to your draft.

A type is written as the database writes it (`INTEGER`, `TEXT`, `VARCHAR(40)`, `timestamp with time zone`). A
default, a check and the condition of a partial index are one SQL expression each (`0`, `'new'`, `price > 0`);
Tabrion refuses a text that could leave its place in the statement (a `;`, a comment, unbalanced brackets).

### The diagram

**Show the draft as: Diagram** draws the same draft as an ER diagram: one box per table with its columns (keys
marked) and its status (*new* or *changed*), and one line per foreign key between two tables. The layout is automatic
(dagre); the zoom buttons and **Fit to view** move the view. **Table view** goes back; it stays the default.

- **Edit a table:** choose its name in the diagram (with Tab and Enter, or a click on the box or on one of its
  lines). Its card from the table view appears below the diagram with every change it offers; chosen with the
  keyboard, the card takes the focus.
- **Add a foreign key:** drag with the pointer from the dot on the right of a column to the dot on the left of the
  column it refers to (in another table or the same one). The table's **New constraint** form opens with the key
  filled in. Nothing is sent until you **Apply** it; you can still name the key (required on PostgreSQL), set an
  action on delete, or cancel.
- **Remove a key:** **Drop** next to it in the table's card.

The diagram is another view of the draft, not another way to change it: every change goes through the same forms to
the same route as in the table view, the server checks each one in the same way, and the plan, the confirmation, the
backup and the run are the same. Drawing a key needs a pointer; with the keyboard, use **Add constraint** in the
table's card or the table view, which offers every change. The diagram shows the first 150 tables of a draft (the
table view shows all), the first 20 columns of each box and the first 300 foreign keys among those tables as
lines; a note says when a bound applies.

!!! warning "A rename is a drop and an add"
    Tabrion matches tables and columns by name. A renamed table or column is therefore planned as dropping the old
    one and adding a new one: **its data is not carried over**. The plan shows this as a `destructive` step.

## What the server checks

The draft lives **only on the server**, for your browser tab, and is bound to the connection and to the unlock it was
started under. It is never saved to disk; it ends when the unlock ends or when you choose **Discard draft**. The page
sends one change at a time and never sends a whole model or a plan; the server refuses a request that tries.

Every change is checked before it is kept, and a refused change leaves the draft as it was:

- names: not empty, at most 63 bytes, no control or unusual space character, no blank at either end, no dot;
- the objects it names must exist (or must not, for a new one); a column a key, constraint or index uses cannot be
  dropped before that key, constraint or index; a column that a check, an index expression, the condition of a
  partial index or a generated column may name cannot be dropped or renamed (Studio errs on the careful side);
  a table another table's foreign key refers to cannot be dropped, nor can its primary key;
- on PostgreSQL every unique, foreign key and check constraint needs a name; on SQLite a unique constraint has none;
- then Tabrion plans the change. What Tabrion cannot plan is refused with its reason: an unknown type, an unsafe
  text, a change Tabrion does not support (for example a changed PostgreSQL primary key), or a table with a feature
  Tabrion does not check (partitions, inheritance, rules, a domain column, an event trigger, a view that names the
  column). There is no way around such a refusal.

If the page and the server disagree about the draft (for example after a change in another tab), the change is
refused and the page reads the draft again.

### Limits

| Limit | Value |
|---|---|
| Tables in a draft (and in the schema it starts from) | 500 |
| Columns in a table | 250 |
| Keys, constraints and indexes on a table | 100 |
| Columns in one key or index | 32 |
| Changes in one draft | 500 |
| Size of the draft | 2 MiB |
| A default, check or partial-index condition | 4000 characters |
| A type | 128 characters |
| A name | 63 bytes (UTF-8) |
| One change request | 16 KiB |
| Drafts open in one Studio process | 16 |

Above a limit the change is refused (`studio.schema_draft_limit`) and the draft stays as it was; start a new draft
for a larger change.

## The plan

**Show plan** takes a fresh snapshot of the schema on the write connection and lets Tabrion plan from it to the
draft, for the version of the database the write connection uses (on SQLite the version of the SQLite library, on
PostgreSQL the server's version). It shows:

- every step: the operation, the table, its **risk classes** (`additive`, `changes data`, `destructive`, `rebuild`
  for a SQLite table that is copied, dropped and created again, `locks the table` on PostgreSQL), whether it runs in
  the transaction, and its exact SQL;
- the fingerprint of the database and of the draft, and the plan hash;
- a warning when the database changed since the draft started: the plan then also undoes that change (for example,
  it drops a table that was added meanwhile, because the draft does not have it).

The plan also lists **warnings** to read before a run:

- views, triggers, row security policies and other objects are not shown in the draft: check them yourself;
- on PostgreSQL, functions and other code that may use a table you drop or whose column type you change are not
  tracked, and a changed function is not part of the fingerprint;
- on SQLite, a table without an `INTEGER PRIMARY KEY` that is rebuilt may get new `rowid` values;
- the fingerprint does not cover everything (rows, functions, grants), so a change there is not found as drift;
- a step that locks its table waits at most 5 seconds for the lock; then the run stops without changing anything.

When a table is the reason Tabrion refuses a plan, the page names it from its position in the draft; the problem
itself carries no name.

The SQL is shown to you only. It is never written to Studio's log, the audit log, the query history or a diagnosis
report.

## Run a plan

Studio runs a plan directly only when **all** of these hold, checked on the server when the plan is shown and again
right before the run:

- the read profile's stored environment is **DEV** or **TEST** (PROD is never unlocked);
- the database has no migration version table: no `alembic_version`, `nodestro_schema_migrations` or
  `axentra_schema_migrations` in any schema, no Levuvo version table (Orkexa's `orkexa_schema` included) and, on
  SQLite, no `user_version` above 0;
- the project folder has no `alembic.ini`;
- every step runs in a transaction;
- on **PostgreSQL**, a plan with a `destructive`, `rebuild` or `changes data` step runs only when `pg_dump` and
  `pg_restore` are set in `database-tools.toml` next to the write settings (see the
  [Database Manager](database-manager.md) guide for the file and its rules) and Studio is not running on Windows.
  Otherwise such a plan is refused (`backup_unavailable`). Plans that only add, or that lock a table, need no
  backup and run.

Otherwise the page says why and offers no confirmation; use your own migration tooling for that change.

**Confirm.** Type the write profile's name and the name of every table the plan changes, tick every step that
changes or removes data, tick the lock warning when a step locks a table, and keep the backup. Studio compares all of
it with the plan it keeps on the server; the page only sends the plan hash it was shown. A confirmation is valid for
5 minutes, for this browser tab, connection and unlock, and for one attempt.

**Run the plan** or **Dry run.** Neither button has the focus by default. Right before the run Studio takes a fresh
snapshot, plans again and compares the plan hash and the risk classes with the confirmed ones; if anything changed,
nothing runs (`studio.plan_drift`). On PostgreSQL it also checks the write role's rights again on the session that
runs the plan (`studio.write_role_over_privileged` ends the unlock). Tabrion then plans once more inside its own
transaction, runs every step, checks foreign keys (and, on SQLite, the file's integrity) before the commit, and rolls
everything back if a check fails.

A **dry run** happens only when you ask for it: it runs every step and rolls back, so the schema does not change.
It takes the same locks as the run (on PostgreSQL an `ACCESS EXCLUSIVE` lock on each changed table), and sequence
values it uses stay used. It uses the confirmation up; show the plan and confirm it again to run it.

### The backup

Before a SQLite plan with a `destructive`, `rebuild` or `changes data` step, Studio copies the database file with
SQLite's backup function into the `backups/schema-designer` folder next to `write-areas.toml` in Studio's
configuration folder. The folder is readable by you only (0700) and so is each file (0600); a file is named by the
time of the run in UTC and a random part. When the backup fails, nothing runs (`studio.schema_backup_failed`).

Before such a **PostgreSQL** plan, Studio makes a `pg_dump` archive (custom format) of the whole database with the
`pg_dump` and `pg_restore` set in `database-tools.toml`, with every check the [Database Manager](database-manager.md)
guide describes: the tool paths, owners and modes, the version (16 or newer and at least the server's), a clean
environment, the write profile's password only in a temporary passfile. The archive goes into the same folder as
`<time>-<random>.dump` with a `.json` description next to it, and it is kept only after `pg_restore --list` could
read it. The run starts only when all of that succeeded; otherwise nothing runs and the page shows why
(`studio.backup_tool_unavailable`, `studio.backup_tool_failed`, `studio.schema_backup_failed`). Things to know:

- The archive holds the **whole database**, not only the draft's schema, and it is taken with the **write role**. So
  that role must be able to read every table of the database. A table it may not read, or one with row security that
  applies to it, makes `pg_dump` fail, and the run is refused.
- **Do not give the write role more rights** (read access to other tables, `BYPASSRLS`, a superuser) just to make the
  backup work: that widens the role Studio changes the schema with. Instead, leave that database without destructive
  runs from Studio and make such changes with your own migration tooling, or have the database owner set up a
  dedicated arrangement for it, for example a DEV or TEST database that holds only the tables the write role owns.
- While the backup runs, the page says that it is making the backup and then running the plan. A large database can
  take a while; the draft stays locked until the backup and the run have finished, so other edits of the same draft
  wait.
- The archive is made just before the run, not inside its transaction: a row someone else writes in between is not
  in it.
- It is **not a complete backup**: roles, role memberships, tablespaces and other cluster objects are not in it.
- It cannot be left out, on DEV or TEST. A dry run makes no backup.
- To restore it, use `pg_restore` yourself (the Database Manager lists only its own backups).

On **DEV only** you may leave a SQLite backup out: tick **Leave the backup out** and type the database file's name.
The audit log records that the backup was left out. On TEST the backup cannot be left out.

Studio never deletes a backup. Remove old ones yourself: delete the files in `backups/schema-designer` that you no
longer need (a `.sqlite` file is a plain SQLite file you can open to check it first).

### What is recorded

Each run and dry run writes a `run.start` and a `run.result` record to the audit log: the action
(`schema_designer.run` or `schema_designer.dry_run`), the environment, the changed tables, the plan hash, the risk
classes, the number of steps, the backup state (`made`, `opted_out`, `not_needed` or `dry_run`) and the result. When
the audit record cannot be written, nothing runs. No value, default or check text and no SQL is recorded.

## Write an Alembic revision

When the project has an Alembic setup (an `alembic.ini` with a `script_location`), Studio never runs a plan
directly: it writes the plan as a new **revision file** instead, which you apply with your own tooling. The preview
has a **Write as an Alembic revision** part. It is offered only when all of these hold, checked when the plan is shown
and again when the file is written:

- the connection's stored environment is DEV or TEST (never PROD), and no PROD profile of yours names the same
  database (the same SQLite file, or on PostgreSQL the same database name on the same server, as the Database Manager
  matches it);
- you trusted the project's profiles (`.tabrion/studio.toml`, the trust dialog) **and** its Alembic code (the code
  trust of the migration status), and neither changed since;
- the plan has a step;
- Studio is not running on Windows (there the file could not be created safely against a folder swapped for a
  junction, so the revision output stays locked).

Enter a message (one line, at most 200 characters) and choose **Write revision**. Studio plans again from the
database as it is now and refuses when the plan changed. It reads the project's single head with the migration
status (in its separate process, under the code trust): with several heads, none, or revisions it cannot read, it
writes nothing. The file goes to `versions` under the `script_location`, named `<revision id>_<message>.py` (12
random hex digits, then the message in lower case letters, digits and `_`, at most 40). Studio never overwrites a file
and never writes through a link; the page shows the file's path.

Studio never runs the revision, `env.py`, a project module or an Alembic command, and changes nothing in the
database. The revision **does not check the database it runs on**: apply it only to the database it was planned for
(the preview shows its source fingerprint), soon after the preview. Its `downgrade()` raises an error (a plan has no
reverse steps). The new file changes the project's code, so the migration status asks you to trust the code again.

Each written revision is recorded in the audit log with a `run.start` and a `run.result` record (action
`schema_designer.revision`, the tables, the plan hash, the risk classes, the number of steps and the result); the
message and the path are not recorded.

## Compare schemas

The **Schema comparison** section (shown with the same switch) compares two schemas **read-only**. Nothing is
changed, no audit record is written and nothing is kept: neither the comparison nor its plan preview.

Each side is one of:

- **Connection:** one schema of an open connection, read now on its read-only connection (empty means `main` on
  SQLite and `public` on PostgreSQL). Any open connection can be compared, **PROD included**, because it is only read;
  a PROD connection is open only after you confirmed its environment when you connected.
- **Draft:** this tab's draft of a connection in the table designer above (the connection must be unlocked).
- **Snapshot file:** a level-2 snapshot that Tabrion wrote (`SchemaSnapshot.to_json()` of
  `tabrion.schema.snapshot(..., level=2)`), at most 2 MiB. At most one side can be a file. A file is **only compared
  and shown**: Studio reads it with Tabrion's own parser and the draft's limits, and never plans from it or to it.

Choose the **reference** (the schema as it should be) and the **compared** side (the schema you look at), then
**Compare**. Tables are matched by their schema-qualified name, so compare the same schema name on both sides (for
example `public` of a DEV and of a TEST database). The result shows:

- the **verdict**: *same*, *different*, or *inconclusive*. *Same* means no difference, equal fingerprints and no table
  with a feature the snapshot does not describe. *Inconclusive* means nothing differs, but such a feature (for example
  a domain column, a partitioned table, a rule, an event trigger, a SQLite `WITHOUT ROWID` table) means the schemas
  may still differ where the snapshot does not look; a feature Studio does not know is shown as unknown and never
  counts as *same*;
- every difference with its table, column and constraint or index name, and whether it is **breaking** (an added
  table, column or index and a changed column order are not);
- both level-2 fingerprints (they include triggers, row-level security policies and the functions they call
  directly), the number of tables, triggers and policies of each side, and each side's flagged features.

### Plan preview

Tick **Also preview the plan** to see the plan that would change the compared side to match the reference, with
every step, its risk classes, its SQL and the warnings of [The plan](#the-plan). It is planned for the version of the
compared database (SQLite library or PostgreSQL server). It is **never run from here**: it has no plan hash, no
confirmation and no Run button. To change a database, use the table designer or your own migration tooling.

Studio makes the preview only when the compared side is a connection of one of **your own DEV or TEST profiles** and
the reference is a connection or the draft. It is refused, while the comparison is still shown, when either side is a
snapshot file, when the compared side is the draft, and for **PROD** or a project profile. Tabrion's own refusals (a
change of a table with a feature it does not check) are shown with the feature's kind.

The tables, columns and SQL of a comparison are shown to you only; Studio's log records only that a comparison ran.

## Export profiles

The **Export profiles** section (shown with the same switch) writes read profiles to a new **connection profile
file** (`*.tabrion-profile.toml`, Tabrion's profile file format v1) that other Tabrion tools can read.

1. Tick the read profiles to export. The list shows your own profiles and, while the project file is trusted, the
   project's. Each line says what the file will hold for its password.
2. Enter the full path of the new file, ending in `.tabrion-profile.toml`.
3. Type the file's name to confirm, then **Export profiles**.

The file holds no secret, but it names hosts, users, databases and environments (PROD too, if you chose such a
profile): share it only with people who may know your database layout.

What the file holds, and what it never holds:

- **Only the connection fields** of each profile: name, engine, file path or host, port, database, user, TLS mode,
  environment, `read_only_role` and statement timeout. Each profile is written with `mode = "read_only"`. Studio
  builds every profile from this fixed list of fields; nothing else of its configuration is copied.
- **No secret, ever.** Write and admin profiles cannot be chosen and are never read; no password is read from the
  keyring, a secret file or an environment variable. A profile whose password comes from an environment variable
  keeps that **reference** (`env:NAME`, the variable's name only); a password kept in the keyring or in a file is
  written as `prompt`, so the program that reads the file asks for it.
- **One host per profile.** A profile with several hosts (`db1,db2`) is refused with its own message and never
  shortened to its first host. (Studio does not save such a profile in the first place.)
- A PostgreSQL profile without a port is written with port `5432`, the port Studio connects to.

The file follows the same rules as a new SQLite file (see [A SQLite file](new-database.md#a-sqlite-file)): the folder must exist,
belong to you, be writable only by you and be given by its real path (no link on the way), and it must not be one of
Studio's own folders. Studio creates the file readable and writable only by you (`0600`) and **never overwrites a
file or a link**: choose a new name for each export. On Windows Studio does not write exports yet.

The audit log records each export with the profile names and the file name, not the folder.

## Not yet

- A PostgreSQL backup before a run on Windows (Studio refuses the tools there).
- Moving the boxes of the diagram by hand, and drawing anything other than a foreign key.
- The revision output and the profile export on Windows.
- The runtime-role wizard (STU-CR-6 part 2), and importing a profile file.
- A revision with `CREATE INDEX CONCURRENTLY` steps (the revision uses the plan of a direct run, without them), and
  a revision output without an unlocked write profile.
- Starting a draft from a schema file, and saving a draft.
- Saving a snapshot file from Studio (use `tabrion.schema.snapshot(..., level=2).to_json()`), and comparing schemas
  with different names.
