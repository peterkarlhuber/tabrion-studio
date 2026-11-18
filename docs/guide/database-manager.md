# Database Manager (in development)

!!! danger "In development, locked by G-S4"
    The Database Manager is **not usable or supported**. It exists only behind the `database_manager` switch in
    `write-areas.toml`, which is **off by default**, and it stays locked until the security gate G-S4 is granted.
    G-S4 is not granted: no Studio version turns the switch on, and nothing in this page is a release of the
    feature. This page describes what backup and restore do when a developer turns the switch on in a test setup, so
    that their behavior and their limits can be reviewed. The rules are
    [STU-ADR-0008](../adr/0008-database-manager-backup-restore-roles.md).

The **Database Manager** section on the **Connections** page backs up a DEV or TEST database into Studio's own backup
folder, restores a backup into a new database or file and deletes a DEV or TEST database after a verified backup.
Managing roles is not built yet.

!!! warning "A PostgreSQL backup is not a complete backup"
    A `pg_dump` archive holds **one database**. Global roles, role memberships, tablespaces and other cluster objects
    are **not** in it. Back them up outside Studio (for example with `pg_dumpall --globals-only`) before you rely on
    a Studio backup to rebuild a server. Every PostgreSQL backup's description says so too.

## Turn it on for development

The switch is read once, when Studio starts, from `write-areas.toml` in Studio's configuration folder, as for the
[Data Editor](data-editor.md#turn-it-on-for-development); only the key differs:

```bash
mkdir -m 700 -p /tmp/studio-dev/state/config
printf 'version = 1\n[areas]\ndatabase_manager = true\n' > /tmp/studio-dev/state/config/write-areas.toml
chmod 600 /tmp/studio-dev/state/config/write-areas.toml
tabrion-studio ./my-project --state-dir /tmp/studio-dev/state
```

Nothing else turns it on: no environment variable, project file, setting, URL, query parameter, header or profile,
and the `data_editor` and `schema_designer` switches do not open it. With the switch off, the section is not shown,
every route of the area answers `405` and its code is not even loaded.

## What you need

- **An admin profile** for the connection: a write profile of the kind `admin`, coupled to the read profile
  (**Write profiles** on the Settings page). Its role is set up **outside Studio** with only the rights the Database
  Manager needs; Studio never creates or widens it. An admin profile unlocks only while the `database_manager` switch
  is on, and only on a DEV or TEST profile: Studio never unlocks PROD.
- **For PostgreSQL, the tools** `pg_dump` and `pg_restore` (below), and **a restore profile** (below) to restore.

Every action checks the admin role's rights again on its own session first. A role that has or reaches SUPERUSER,
BYPASSRLS, REPLICATION, a server-file role (`pg_read_server_files`, `pg_write_server_files`,
`pg_execute_server_program`) or CREATEROLE before PostgreSQL 16 is refused (`studio.admin_over_privileged`), and the
unlock ends.

## The PostgreSQL tools

Studio runs `pg_dump` and `pg_restore` **only** from absolute paths that you set in `database-tools.toml`, next to
`write-areas.toml` in the same configuration folder:

```toml
version = 1

[postgresql]
pg_dump = "/usr/lib/postgresql/17/bin/pg_dump"
pg_restore = "/usr/lib/postgresql/17/bin/pg_restore"
```

The file must be yours, mode `0600`, not a link, in a folder only you may write. Studio never takes a tool from
`PATH`, the page, a settings save, a project file, an environment variable or a profile. Before every run it checks
that each tool:

- is an absolute path to a regular file, not a link, owned by you or `root`, not writable by group or others, in a
  folder with the same rules, and every folder above it belongs to you or `root` and is not writable by group or
  others (unless it has the sticky bit, as `/tmp`);
- answers `--version` with its own program name;
- is PostgreSQL 16 or newer and at least as new as the server (`pg_restore` also at least as new as the archive).

The section shows whether each tool is usable and, if not, why (never the path). Studio runs the tools without a
shell, with an environment of its own (no `PG*` variable of yours reaches them), the password only in a temporary
file that only you may read and that is removed afterwards, and a time limit. The tools' messages are never shown or
logged; Studio says only what kind of failure it was (sign-in, connection, permission, version, other).

**Not on Windows yet.** On Windows Studio does not run the tools (`tool.platform`); SQLite backups work there.

**Certificates.** Studio passes no `sslrootcert`, `sslcert` or `sslkey` to the tools, as for its own connections:
libpq then uses the files in `~/.postgresql/` (`root.crt` for `verify-full`, and a client certificate there is used
without asking). A server whose CA is not in `~/.postgresql/root.crt` is refused at `verify-full`.

**Row security.** Studio never passes `--enable-row-security`. If a table has row security and the admin role does
not have BYPASSRLS (which Studio refuses anyway), `pg_dump` fails instead of writing a backup that silently lacks
rows.

## Make a backup

1. Open the connection, then **Connections**. In **Database Manager**, choose the admin profile and type its name to
   unlock.
2. Choose **Make a backup**.

Studio writes the backup into `backups/database-manager` next to `write-areas.toml` (the folder `0700`, each backup
`0600`, never overwritten). A PostgreSQL backup is written to a temporary file first and kept only when `pg_dump`
succeeded and `pg_restore --list` can read it; otherwise nothing is kept. Next to each backup is its description:
when it was made, the engine, the server and tool versions, its size and SHA-256, the schema fingerprint of the
source, the number of extensions, and the scope notice. It holds no password and no data.

The section lists the backups with their count and total size. **Studio never deletes a backup:** remove the ones you
no longer need yourself.

## Restore

1. Choose a backup and **Look at the restore**. This changes nothing; for PostgreSQL it shows how many extensions the
   archive creates.
2. Choose the target and type its name. **Restore** stays disabled until it matches, and it never has the focus by
   default.

Studio restores only backups from its own folder whose file still matches its description (size and SHA-256); a
changed backup, a link or a file from elsewhere is refused.

- **SQLite:** into a **new file** next to the connection's file (a plain name ending in `.db`, `.sqlite` or
  `.sqlite3`; the folder must be yours and writable only by you, every folder above it safe as for
  [New database](new-database.md), and not one of Studio's own), or over the connection's **own file**. Before the file is replaced Studio backs it up, closes its own
  connections to it and checks afterwards that it is still the same file. SQLite's `integrity_check` runs on the
  restored file.
- **PostgreSQL:** only into a **new database** on the same server, which Studio creates. The archive's functions run
  while the data is loaded, as the role that restores, so the restore never runs as the admin role. It runs as a
  **restore profile**: a second admin profile of the same read profile, whose role is lean: none of SUPERUSER,
  BYPASSRLS, REPLICATION, CREATEROLE or CREATEDB and no predefined role. Studio checks that right before the restore
  and refuses otherwise (`studio.dbm_restore_role_unsafe`). Set the role up outside Studio, and let the admin role act
  as it (`GRANT <restore role> TO <admin role>`), because the new database belongs to the restore role. The restore
  runs in one transaction; if it fails, the new database stays empty for you to inspect or drop. Use the restore
  role for nothing else and let it own nothing else: it owns every restored database, and the archive's code may
  create trusted extensions there (the preview shows how many).

After a restore Studio compares the schema of the target with the backup's fingerprint and says whether they match.
A backup is only as trustworthy as the database it came from: restore backups of databases whose content you trust.

Studio never restores into PROD, and never into a database or file that **any** of your profiles marks PROD, whatever
profile you are working through. It recognizes the same PostgreSQL server under another name (`localhost` and
`127.0.0.1`, another DNS name of the same address, a socket) by the server's own identity where the roles may read
it, and otherwise by its resolved addresses and port.

Every backup and restore is written to the audit log (`run.start` before, `run.result` after); if the audit log cannot
be written, nothing is done.

## Delete a database

1. **PostgreSQL:** type the name of another database on the same server. **SQLite:** the target is always this
   connection's own file. Then **Look at the delete**; this changes nothing. For PostgreSQL it shows how many other
   sessions use the database right now.
2. Type the name of the database or file. **Delete database** stays disabled until it matches, and it never has the
   focus by default.

Before anything is deleted Studio makes a **backup** of the target into its backup folder and checks it: a
PostgreSQL archive must be readable by `pg_restore`, a SQLite copy must have the same schema as the file. If the
backup fails or cannot be checked, nothing is deleted. There is no way to skip it. The PostgreSQL backup runs as the
admin role, so that role must be able to read every table of the target; if it cannot, the delete is refused. Do not
give the role more rights for this: delete such a database outside Studio. The backup stays in the folder and can be
restored like any other.

Studio never deletes:

- a database or file of a PROD profile, or one that **any** of your profiles marks PROD;
- PostgreSQL's `postgres`, `template0`, `template1` or any other template database, or the database this connection
  uses;
- a SQLite file in a folder that others may write or that is one of Studio's own folders. On Windows Studio does not
  delete SQLite files at all.

If other sessions use a PostgreSQL database, the delete is refused. On PostgreSQL 13 and later you may choose **End
the other sessions (FORCE)**; it is never preselected and needs the name typed once more. Studio's own connections
to the target are closed first, so they never count. A SQLite file that another program holds open is not deleted.

Writes to the database between its backup and the delete are lost. Every delete is written to the audit log
(`dbm.delete`, with the backup's name and whether FORCE was chosen).
