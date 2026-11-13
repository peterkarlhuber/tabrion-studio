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
from Studio. Studio's own findings and hints (`STU-DX-*`, `STU-BK-*`) are in your language: title, detail and
remedy. tabrion's findings (`TAB-*`) are tabrion's own English text, because tabrion has no German texts yet and its
details name database objects such as schemas; a note above the list says so when such a finding is shown.
Unusual characters in those names (for example a right-to-left mark) are shown as escapes such as `\u202E`, so a
name cannot disguise itself.

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

When a diagnosis was canceled or stopped at the time limit before it looked at the backups, the section says
"Backups were not checked in this run" instead of "No backup hints": no hint then says nothing about your backups.

Studio sees only backups that are files next to a SQLite database, and on PostgreSQL only WAL archiving. Backups
made with `pg_dump`, a backup tool or a copy elsewhere are not visible, so a hint such as "No backup found" is a
reminder to check, not proof that there is none. When `STU-BK-010` is shown, tabrion's own finding on WAL archiving
(`TAB-PG-020`) is left out, since both say the same.

## Downloading the report

After a diagnosis, **Download Markdown report** and **Download JSON report** save a report of the shown connection.
Studio runs the diagnosis again for it, on the same connection (no new connection, so no new "Production database"
question), and your browser saves the file under a name such as
`tabrion-studio-diagnosis-postgresql-prod-20261112T101500Z.md`. **Cancel** stops it; nothing is saved then. If the
cancel reached the server too early to stop the report's diagnosis, the page says so: the diagnosis then ends at the
connection's time limit.

The report is meant to be shared, for example in a ticket. It holds the findings and the backup hints with their
counts, the profile's name, engine and environment, the time and the versions of Studio and tabrion. It leaves out
passwords and tokens, connection URLs, host names and ports, the user and database names (shown as `[role]` and
`[database]`), file paths and file names, SQL text and values. Check it before you share it all the same: tabrion's
findings may name other schemas, tables and extensions of your database.

If Studio cannot read from the server the names it has to leave out, it makes no report and says so ("Studio could
not read from the server the names the report leaves out …"); try again when the connection is free, or connect
again.

The report is in English, whatever the language of the page, since it is meant to be passed on. The JSON report
also gives the facts behind each of Studio's findings and hints (`params`: counts, setting names, the `sslmode`, the
checks that did not run), so a tool can word them itself.

## The report from the command line

`tabrion-studio diagnose` writes the same report without opening the browser, for example in a script or for an
agent. No Studio needs to run for it:

```
tabrion-studio diagnose [PROJECT] --profile PROFILE [--format markdown|json] [--output FILE [--force]] [--confirm-prod]
```

| Option | Meaning |
|---|---|
| `PROJECT` | the project folder (default: the current folder); its profiles count only after you trusted them in Studio |
| `--profile` | the profile: its id (`user:NAME` for your own profiles, `project:NAME` for the project's) or its name, when only one profile has that name; a value that starts with `user:` or `project:` is only compared with the ids |
| `--format` | `markdown` (the default) or `json` |
| `--output` | write the report to this file, a new file that only you may read (mode 0600 on Linux and macOS); without it, the report goes to standard output as UTF-8 |
| `--force` | with `--output`: replace a file that already exists; without it, an existing file is refused |
| `--confirm-prod` | needed for a profile marked as PROD: your confirmation that Studio may connect to the production database ("Production database"); the connection stays read-only and limited to the profile's time limit (15 seconds unless the profile sets its own) |
| `--state-dir`, `--config-dir` | the folders Studio uses, as for `tabrion-studio` itself |

It opens the profile's read-only connection, runs the diagnosis with the backup hints, writes the report and closes
the connection. The report leaves out the same things as the downloaded one. The command never asks anything:

- **Project profiles** are used only after you chose **Trust** in Studio's dialog **Trust the project profiles?**
  (see [Connections](connections.md)), and only while the file is unchanged since. Otherwise the command says that
  the profile was not found and that the project's profiles need your trust first.
- **PROD** profiles need `--confirm-prod` on every run; without it the command connects to nothing and stops with
  exit code 2. If the profile becomes PROD while the command runs, it is refused as well.
- **Passwords** come from the system keyring or from the profile's `env:` or `file:` reference, as in Studio; the
  command itself does not prompt for one, but your system keyring may ask to be unlocked. In a script or CI job
  without a keyring, give the profile an `env:` reference and set `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`
  so that no keyring is asked.

The output file is written whole or not at all: the report goes to a temporary file in the same folder, which then
takes the file's name. `--output` never follows a link, never writes to a folder or a device, and always refuses the
profile's database file and its `-wal`, `-shm` and `-journal` files, even with `--force`. The JSON report escapes
every character outside ASCII (`\u...`), and the Markdown report leaves out control and direction characters, so
neither can change how a terminal or an editor shows the text around it.

The command reads the folder given by `--state-dir` (or Studio's default one) only when it is private, as Studio
does: owned by you and not writable by others. If not, it stops with exit code 1 and says why. When the profile is
not found and your profiles file has problems, the command lists them (the place in the file and the problem's
code), so you can fix the file.

Exit codes: `0` the report was written, `1` it could not be made (for example the database does not answer, or the
password is missing, or the output file could not be written), `2` a usage error (an unknown profile, a name that
more than one profile has, PROD not confirmed, an output file that exists without `--force` or that is refused). Messages go to standard error. A project folder named `diagnose` is given as `./diagnose`.
