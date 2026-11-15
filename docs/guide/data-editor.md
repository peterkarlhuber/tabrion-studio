# Data Editor (in development)

!!! danger "In development, locked by G-S2"
    The Data Editor is **not usable or supported**. It exists only behind the `data_editor` switch in
    `write-areas.toml`, which is **off by default**, and it stays locked until the security gate G-S2 is granted. G-S2
    is not granted: no Studio version turns the switch on, and nothing in this page is a release of the feature. This
    page describes what the Data Editor does when a developer turns the switch on in a test setup, so that its
    behavior and its limits can be reviewed. Do not turn it on for a database whose data matters.

The **Data Editor** changes the rows of one table: change cells, add rows and delete rows, then preview the changes,
confirm them by typing and save them in one transaction. It is the **Edit data** tab of a table in the
**Explorer**. Everything else in Studio stays read-only; the [data viewer](data-viewer.md) only reads.

## Turn it on for development

The switch is read once, when Studio starts, from `write-areas.toml` in Studio's configuration folder (the folder
`--config-dir` names, else the `config` subfolder of `--state-dir`, else the platform's configuration folder). Nothing
else turns it on: no environment variable, project file, setting, URL or profile
([How Studio protects your data](security.md#write-areas-are-off-unless-you-turn-them-on)).

1. Use a separate configuration folder for the test, for example with `--state-dir`. The folder should not exist
   yet: an existing folder that other users can write to makes Studio ignore the file, and the area stays off
   without a message.

    ```bash
    mkdir -m 700 -p /tmp/studio-dev/state/config
    printf 'version = 1\n[areas]\ndata_editor = true\n' > /tmp/studio-dev/state/config/write-areas.toml
    chmod 600 /tmp/studio-dev/state/config/write-areas.toml
    tabrion-studio ./my-project --state-dir /tmp/studio-dev/state
    ```

    Only a literal `true` turns the area on. A missing or malformed file, another `version`, a link, a file that
    others may read or write (not `0600`) or a folder that others may write means off. The start log names the
    state of each area (`studio.write_area_state`).
2. Change the file back to `data_editor = false` (or delete it) and restart Studio to turn the area off again. A
   change needs a restart.

With the switch off, the **Edit data** tab is not shown, every route of the Data Editor answers `405` and its code is
not even loaded.

## Before the first edit: a write profile

The Data Editor never writes through the read profile you browse with. It needs a **write profile** coupled to it:

1. Open **Settings**. While a write area is on, the page has the section **Write and admin profiles**.
2. Choose the profile to couple (one of your own profiles, not a project profile, and never a `PROD` profile), give
   the write profile a name and, on PostgreSQL, its **own database role** with only the rights it needs (not the role
   of the read profile). Studio refuses to unlock a role that is a superuser, has `BYPASSRLS` or `REPLICATION`, or
   belongs, directly or through another role, to `pg_read_server_files`, `pg_write_server_files` or
   `pg_execute_server_program` (see [Limits](#limits)).
3. Read the note on production credentials and confirm it: Studio relies on the environment you gave the profile and
   cannot tell whether a database is production. Write credentials for a production database do not belong in Studio.
4. On PostgreSQL, confirm the pair of server and secret reference in the same section before the first unlock
   (I-109 j). A server that is not on this computer needs TLS mode `verify-full` or `verify-ca`. Every write
   connection that sends a password uses SCRAM authentication only, also on this computer; a local socket
   connection can name the expected server user with `requirepeer`.

Write profiles live only in your own `profiles.toml`, never in a project file. On SQLite, the write connection needs
Python 3.12 or later; on Python 3.11 Studio refuses the unlock (`studio.write_unsupported`).

## Unlock

1. In **Explorer**, open the read profile, select a table and choose the **Edit data** tab.
2. **Unlock write mode**: type the write profile's name exactly (D-03) and press **Unlock**. With more than one
   coupled write profile, choose it first.
3. The tab says "Write mode is unlocked for this connection." **Lock** ends the unlock at once.

The unlock belongs to this connection and this page session. It ends after 15 minutes without use, when the
connection closes, when the profile or its read profile changes, and when Studio stops.

## Edit

- **Change a cell.** Each cell has the editor of its column's type: a choice for booleans and enums, a text field for
  the rest, with the expected format under the column name (for example `date`, `numeric(6,2)` or `timestamptz`).
  A changed cell is marked.
- **NULL is not the empty text.** Emptying a text cell gives the empty text `''`. A column that takes NULL has a
  **NULL** button next to the cell; a choice has a **NULL** entry. A NOT NULL column has neither. The change overview
  shows "(empty text)" and "NULL" apart.
- **Add row** adds an empty row at the end. A column you leave untouched gets its default; set it to give a value.
  A NOT NULL column without a default must be filled. **Remove new row** removes it again.
- **Delete row** marks a row for deletion; **Keep row** takes that back.
- **Replace a bytes value by a file.** A bytes cell (`bytea`, a `BLOB` of a `STRICT` table, or an ordinary SQLite
  column that holds bytes) shows the value's size, never its bytes. **Replace** opens the file chooser; the content of
  the file you pick becomes the new value, and the change overview shows the size before and after. Bytes are never
  typed or edited in place. A file may have at most **64 KiB**: a larger one is not read, its cell is marked "Too
  large", and **Preview** waits until you pick another file or set NULL. The server checks the size again
  (`value.too_large`). A changeset carries at most 256 KiB in all (bytes travel as base64, a third larger), so save
  after one or two large files.
- **Load more rows.** The tab loads 100 rows; **Load 100 more rows** adds the next 100 below them, up to 1,000 rows.
  Rows already loaded keep their place and their pending changes, so an edit always stays with its row. The line
  above the grid says how many rows are loaded and whether the table has more. **Discard changes**, a reload and a
  save start again at the first 100 rows.
- **Discard changes** drops every pending change and reads the rows again.

Studio checks every changed value for its column while you type: its kind, length, range, NULL, an enum's labels and
a required value of a new row. A value that does not fit is marked at its cell, the cell names the problem, the list
under the grid names every such cell, and **Preview** waits until none is left. The formats:

| Column type | Enter |
|---|---|
| Whole numbers (`int2`, `int4`, `int8`) | digits, within the column's range |
| `numeric` | a decimal number such as `12.50` that fits the precision and scale |
| `float4`, `float8` | `1.5`, `2e-3`, `NaN`, `Infinity` |
| `date` | `YYYY-MM-DD` |
| `time` | `HH:MM` or `HH:MM:SS`; `24:00` is the end of the day and is stored as it is |
| `timestamp` | `YYYY-MM-DD HH:MM:SS`, without an offset |
| `timestamptz` | with its offset: `2025-01-01 10:00+02:00`, or `Z` for UTC (a value without an offset is refused, so nothing is shifted to another time zone) |
| `uuid` | `123e4567-e89b-12d3-a456-426614174000` |
| `json`, `jsonb` | a JSON value such as `{"a": 1}` |
| Arrays | a JSON array such as `[1, 2]`; NULL elements are allowed, sub-arrays have the same length |
| Bytes | a picked file of at most 64 KiB (**Replace**), or NULL |
| Generated and identity `ALWAYS` columns | read only |

On SQLite, only `STRICT` tables have column types; in an ordinary table any value is taken as SQLite takes it,
except a whole number wider than 64 bits, which SQLite cannot store (the server refuses it with the preview). The
checks on the page are help only: the server checks every value again before it makes the plan (a refused value
marks its cell; the answer names positions, never a value), and the database checks the rest (CHECK constraints,
uniqueness, foreign keys, domains, triggers).

## Preview, confirm, execute

1. Before the preview, the **change overview** lists every changed row with its columns before and after, the new
   rows with their values, and the deleted rows by their key.
2. **Preview** sends the changes to the server, which checks them and makes a plan. The review repeats the overview
   and names the triggers and rules that also run. While the review is open, the grid takes no edit; **Back**
   discards the plan.
3. **Confirm**: type the text the review asks for. It is the write profile's name, or the **table's name** when the
   changes delete a row (a delete is destructive, D-10). A plan expires 5 minutes after the preview; preview again
   then.
4. **Execute** runs exactly the confirmed plan, once, in one transaction. The tab says what was saved ("Saved: 1
   updated, 1 inserted, 1 deleted.") and reads the rows again.

**Nothing or everything is saved.** Every column the page showed is part of the row's old value. If a row was changed
or deleted by someone else since it was loaded, or a change did not affect exactly one row, the whole changeset is
rolled back and the message names no value: **Reload the rows** and make the changes again.

**A rule of the table.** The database is the last instance: a change can break a unique value or the primary key, a
foreign key (a referenced row that is missing, or rows that still refer to a deleted one), NOT NULL or a CHECK. Then
the whole changeset is rolled back as well and nothing is saved (`studio.data_editor_constraint`). Your changes stay
in the grid, and the change the database refused is marked: at its **cell** where the database names a single column
(a NOT NULL column, a unique or foreign key constraint of one column), else at its **row**, next to its action. The
list under the grid names it with the kind of rule. Correct it and **Preview** again; the next edit clears the
marks. The message never names a value, nor the constraint. A constraint that the database checks only at the commit
(a deferred one) belongs to no single change: it is shown for the whole changeset (`db.foreign_key` and the like). If the table's columns,
triggers, rules or the database file changed since the preview, nothing runs (`studio.plan_drift`); preview again.

The changes live only in the page's memory: never in the query history, the drafts, the browser's storage or the
logs. The local audit log records the time, the profile, the action, the table, the row counts, the plan hash and the
result, never a value.

## Limits

These are refused on purpose, by the server, whatever the page shows:

- **`PROD` is refused.** A `PROD` profile cannot get a write profile, a coupled profile cannot be changed to
  `PROD`, a `PROD` connection has no **Edit data** tab, and the server unlocks nothing on it
  (`studio.write_prod_locked`); Studio re-checks the stored profile on every use. There is no code path for
  `PROD` (E-X7).
- **No over-privileged write role (PostgreSQL).** The role of the write profile is checked at every unlock,
  including every role it can reach through membership. A superuser, `BYPASSRLS`, `REPLICATION` or membership in
  `pg_read_server_files`, `pg_write_server_files` or `pg_execute_server_program` refuses the unlock
  (`405 studio.write_role_over_privileged`): the connection is closed and nothing is unlocked. `CREATEROLE` and the
  other predefined roles, such as `pg_read_all_data`, are shown as warnings in the write state and do not block
  (owner decision 1A at G-S2).
- **Only tables with a primary key.** A table without one is refused before you edit anything
  (`studio.plan_target_no_primary_key`); Studio never edits rows by `rowid` or `ctid`. If a column of the primary key
  is not shown, the rows are read only.
- **No views, foreign tables or partitioned tables.** A view has no **Edit data** tab; a foreign table, a
  partitioned table or one of its partitions is refused (`studio.plan_target_invalid`,
  `studio.plan_target_partitioned`).
- **Columns that cannot be compared.** A PostgreSQL table with a column whose type has no proper equality (`xml`,
  `point`, `box`, a composite type, `json[]`) is refused as a whole, whichever columns are shown
  (`studio.data_editor_column_incomparable`): a change made by someone else to such a value could not be detected.
  Edit such a table with another tool.
- **`json` is compared as `jsonb`.** A change made meanwhile only to the key order, the whitespace or a duplicate
  key of a `json` value is not a conflict. Text is compared exactly, whatever collation the column has.
- **Triggers and rules get a notice, not a block.** A table with triggers or rules (or row security on
  PostgreSQL) can be edited. The tab names them above the grid and again in the review: they run with every change
  and can change other rows, which Studio does not count; row security can hide rows or refuse a change. The
  database decides.
- **Rows only, and locked like the other write areas.** The Data Editor never changes the schema, runs no migration,
  writes no Alembic revision, makes no backup and grants nothing to roles. Those belong to the Schema Designer
  (G-S3) and the Database Manager (G-S4), which have their own switches and stay locked too. Granting G-CX turned
  nothing on: each area stays off until its own gate, and a public release needs an independent human security
  review besides. The owner granted G-S2 on 2026-11-15 for local use on DEV, TEST and STAGE databases only; the
  switch still stays off by default, nothing turns it on automatically, and PROD stays locked.
- **Rows, files and changesets have limits.** The tab loads up to 1,000 rows, 100 at a time, in the order of the
  primary key; it has no filter or sort of its own (use the [data viewer](data-viewer.md) to find rows). A row with a
  cell the viewer cut (longer than 64 KB of text or 32 KB of bytes) is read only. A file that replaces a bytes value
  has at most 64 KiB, a changeset at most 1,000 changes and 256 KiB.

## Messages

| Message | What to do |
|---|---|
| No write profile is coupled to this connection's profile. | Create one under **Settings**, **Write and admin profiles** (above). |
| Studio never writes to a PROD database. | Use a DEV or TEST profile; PROD is never edited. |
| The write role is a superuser, has BYPASSRLS or REPLICATION, or is a member of a server-file role … | Give the write profile a role with only the table rights it needs; see [Limits](#limits). |
| The table has no primary key, so Studio cannot edit its rows. | Add a primary key with your migration tool, or edit the table with another tool. |
| A column of this table has a type that cannot be compared … | The table is refused as a whole; edit it with another tool. |
| A value does not fit its column … the cells in question are marked. | Correct the marked cells, then **Preview** again. |
| Too large: at most 64 KiB. | Pick a smaller file for the bytes cell, or set NULL. |
| These changes are too large to save at once … | Save some of the changes first (for example one replaced file), then the rest. |
| The database refused a change because it breaks a rule of the table … | The marked cell or row breaks a unique value, a reference, NOT NULL or a check. Correct it and **Preview** again; nothing was saved. |
| The table's columns changed since the rows were loaded … | **Reload the rows**; your pending changes are discarded. |
| A row was changed or deleted by someone else since it was loaded. Nothing was saved. | **Reload the rows** and make the changes again. |
| The target changed since the preview … | The table changed; **Preview** again. |
| The write mode is locked. Unlock it again. | The unlock ended (15 minutes without use, or the connection closed); unlock again. |

## Related pages

- [How Studio protects your data](security.md#write-areas-are-off-unless-you-turn-them-on): the write switches, write
  profiles, the unlock and the Data Editor's safeguards.
- [Write and admin threat model](../architecture/write-threat-model.md) and
  [STU-ADR-0006](../adr/0006-per-area-write-kill-switches.md): why each area has its own switch and gate.
