# Diagnostics

The **Diagnostics** view checks the database of a connection profile against tabrion's rules and Studio's own
checks: roles and what they may write, timeouts, TLS, foreign keys, file permissions and backups. The diagnosis only
reads; it never changes the database.

## Running a diagnosis

1. Choose the **Connection profile**.
2. Select **Run diagnosis**. Studio opens the profile's read-only connection and runs the checks. A PROD profile
   asks "Production database" first, once per page session: if you already confirmed it in the connection manager,
   the migration status or here, Studio does not ask again until you reload the page, save or delete the profile, or
   trust the project again.
3. **Cancel** stops a running diagnosis. The result then says that it is incomplete (`STU-DX-008`, "Not every check
   ran"). A diagnosis also stops at the connection's time limit (15 seconds for a PROD profile unless the profile sets
   its own).

**Run again** checks the shown connection again; it never opens a new one. Switching the profile or leaving the
view stops a running diagnosis.

## Reading the findings

Findings are sorted by severity: **Error**, **Warning** and **Note**, each with an icon and the word, never by
colour alone. Each finding shows its code, its title, the detail and the remedy, and whether it comes from tabrion or
from Studio. Studio's own titles (`STU-DX-*`, `STU-BK-*`) are in your language; details and remedies are the
checks' own English text, because they name database objects such as schemas. Unusual characters in those names
(for example a right-to-left mark) are shown as escapes such as `‮`, so a name cannot disguise itself.

## Backups

Below the findings, **Backups** lists hints about backups (`STU-BK-*`). They are not findings, and the summary
above does not count them.

| Code | Severity | What it means |
|---|---|---|
| `STU-BK-001` | Warning | SQLite: no backup was found next to the database file: a SQLite file in its folder, or in its `backups` or `backup` subfolder, whose name starts with the database's name and a `.`, `-` or `_` (`shop.db` → `shop-v2.db`, `shop.db.bak`). |
| `STU-BK-002` | Note or warning | SQLite: the newest backup is older than the last change of the database (a warning when it is more than a week older). |
| `STU-BK-003` | Warning | SQLite: a backup can be read by other users of the computer. |
| `STU-BK-004` | Note | SQLite: the folder could not be searched for backups. |
| `STU-BK-005` | Note | SQLite: a folder holds too many entries, so Studio looked only at part of it. |
| `STU-BK-010` | Note, warning on PROD | PostgreSQL: WAL archiving is off, so there is no point-in-time recovery. |
| `STU-BK-011` | Warning, error on PROD | PostgreSQL: WAL archiving fails. |
| `STU-BK-012` | Note | PostgreSQL: archiving is on, but nothing has been archived yet. |
| `STU-BK-013` | Note | PostgreSQL: the server is a standby that does not archive. |
| `STU-BK-014` | Note | PostgreSQL: the archiving state could not be read. |

Studio sees only backups that are files next to a SQLite database, and on PostgreSQL only WAL archiving. Backups
made with `pg_dump`, a backup tool or a copy elsewhere are not visible, so a hint such as "No backup found" is a
reminder to check, not proof that there is none. When `STU-BK-010` is shown, tabrion's own finding on WAL archiving
(`TAB-PG-020`) is left out, since both say the same.

## Downloading the report

After a diagnosis, **Download Markdown report** and **Download JSON report** save a report of the shown connection.
Studio runs the diagnosis again for it, on the same connection (no new connection, so no new "Production database"
question), and your browser saves the file under a name such as
`tabrion-studio-diagnosis-postgresql-prod-20261112T101500Z.md`. **Cancel** stops it; nothing is saved then.

The report is meant to be shared, for example in a ticket. It holds the findings and the backup hints with their
counts, the profile's name, engine and environment, the time and the versions of Studio and tabrion. It leaves out
passwords and tokens, connection URLs, host names and ports, the user and database names (shown as `[role]` and
`[database]`), file paths and file names, SQL text and values. Check it before you share it all the same: tabrion's
findings may name other schemas, tables and extensions of your database.

If Studio cannot read from the server the names it has to leave out, it makes no report and says so ("Studio could
not read from the server the names the report leaves out …"); try again when the connection is free, or connect
again.

The report cannot be made from the command line yet.
