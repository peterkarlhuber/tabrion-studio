# Schema Designer (in development)

!!! danger "In development, locked by G-S3"
    The Schema Designer is **not usable or supported**. It exists only behind the `schema_designer` switch in
    `write-areas.toml`, which is **off by default**, and it stays locked until the security gate G-S3 is granted.
    G-S3 is not granted: no Studio version turns the switch on, and nothing in this page is a release of the feature.
    This page describes what the designer does when a developer turns the switch on in a test setup, so that its
    behavior and its limits can be reviewed.

The **Schema Designer** section on the **Connections** page lets you design the tables, columns, keys, constraints
and indexes of one schema of a development or test database, and shows the plan Tabrion makes for the change: every
step with its risk classes, whether it runs in a transaction, its exact SQL, and the plan hash.

**In this version nothing is run.** The designer only shows the plan. Running a plan (a confirmation per risk class,
a new plan right before the run, a backup before a destructive change) is a later step of the Schema Designer and
is not built yet. The designer never writes to the database, the project folder or the disk.

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

The SQL is shown to you only. It is never written to Studio's log, the audit log, the query history or a diagnosis
report. The designer writes no audit record, because it changes nothing.

## Not yet

- Running a plan, with its confirmations and the backup before a destructive change (STU-CR-5).
- The graphical mode (STU-XSD-1), the schema comparison (STU-XSD-2) and the Alembic revision output (STU-XSD-3).
- Starting a draft from a schema file, and saving a draft.
