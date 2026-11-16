# Schema Designer (in development)

!!! danger "In development, locked by G-S3"
    The Schema Designer is **not usable or supported**. It exists only behind the `schema_designer` switch in
    `write-areas.toml`, which is **off by default**, and it stays locked until the security gate G-S3 is granted.
    G-S3 is not granted: no Studio version turns the switch on, and nothing in this page is a release of the feature.
    This page describes what the designer does when a developer turns the switch on in a test setup, so that its
    behavior and its limits can be reviewed.

The **Schema Designer** section on the **Connections** page lets you design the tables, columns, keys, constraints
and indexes of one schema of a development or test database, and shows the plan Tabrion makes for the change: every
step with its risk classes, whether it runs in a transaction, its exact SQL, and the plan hash. On a **DEV** or
**TEST** database that no migration tool manages, you can confirm the plan and run it (see
[Run a plan](#run-a-plan)).

Designing and previewing never write to the database, the project folder or the disk. Only a confirmed run changes
the database, and only after Studio made its backup where one is needed.

## Turn it on for development

The switch is read once, when Studio starts, from `write-areas.toml` in Studio's configuration folder, as for
[New database](new-database.md#turn-it-on-for-development):

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

- The archive is taken with the **write role**, so that role must be able to read every table of the database. A
  table it may not read, or one with row security that applies to it, makes `pg_dump` fail, and the run is refused.
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

## Not yet

- A PostgreSQL backup before a run on Windows (Studio refuses the tools there).
- The graphical mode (STU-XSD-1), the schema comparison (STU-XSD-2) and the Alembic revision output (STU-XSD-3).
- Starting a draft from a schema file, and saving a draft.
