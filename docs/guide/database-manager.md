# Database Manager (in development)

!!! danger "In development, not released"
    The Database Manager is **not released or supported**. It exists only behind the `database_manager` switch in
    `write-areas.toml`, which is **off by default**; no Studio version turns it on. Its security gate G-S4 is
    effective since 2026-11-19 for local DEV, TEST and STAGE only (PROD stays locked); the human security review and
    the release decision are still open, so nothing in this page is a release of the feature. This page describes what backup and restore do when a developer turns the switch on in a test setup, so
    that their behavior and their limits can be reviewed. The rules are
    [STU-ADR-0008](../adr/0008-database-manager-backup-restore-roles.md).

The **Database Manager** section on the **Connections** page backs up a DEV or TEST database into Studio's own backup
folder, restores a backup into a new database or file and deletes a DEV or TEST database after a verified backup.
The **New database** wizard on the same page, which creates an empty SQLite file or PostgreSQL database, belongs to
the Database Manager too (see [New database](new-database.md)): on PostgreSQL it runs on the admin profile, whose
role then needs `CREATEDB`. It also manages the roles of a PostgreSQL DEV or TEST server (below). Its **Profile pairs, import and export** part shows
which write or admin profile is coupled to each of your read profiles and moves read profiles to and from profile
files (below).

!!! warning "A PostgreSQL backup is not a complete backup"
    A `pg_dump` archive holds **one database**. Global roles, role memberships, tablespaces and other cluster objects
    are **not** in it. Back them up outside Studio (for example with `pg_dumpall --globals-only`) before you rely on
    a Studio backup to rebuild a server. Every PostgreSQL backup's description says so too.

## Its limits at a glance

These rules hold for every action of the Database Manager; the sections below say how each is checked.

- **`pg_dump` is not a complete backup.** A PostgreSQL backup holds one database, without global roles, role
  memberships, tablespaces and other cluster objects ([Make a backup](#make-a-backup)). A SQLite backup is a full
  copy of the file.
- **PROD is locked.** An admin profile never unlocks on a PROD profile, and Studio never restores into, deletes or
  changes the roles of a database, file or server that **any** of your profiles marks PROD, also under another host
  name ([Restore](#restore), [Delete a database](#delete-a-database), [Manage PostgreSQL roles](#manage-postgresql-roles)).
- **A backup comes first.** Before a delete, and before a SQLite restore over the connection's own file, Studio makes
  a backup and checks it; when that fails, nothing is changed. There is no way to skip it. Studio never deletes a
  backup.
- **Look first, then type the name.** Every restore, delete and role change shows a preview that changes nothing;
  the action runs only after you type the target's name, and its button never has the focus by default.
- **Roles only through Tabrion, with the role rules.** Studio never grants `SUPERUSER`, `BYPASSRLS`, `CREATEROLE`,
  `REPLICATION`, an ADMIN or GRANT option or TRIGGER, never adds a member to a predefined or dangerous role, and
  never changes its own roles ([Manage PostgreSQL roles](#manage-postgresql-roles)).
- **The admin rights are yours to set up, outside Studio.** Studio never creates, widens or grants to the admin
  role; it checks the role's rights before every action and refuses one with too many
  ([The admin role's rights](#the-admin-roles-rights)).
- **An imported profile is a read profile without a password.** A profile file never adds a write or admin profile,
  never replaces one of yours, never relabels a PROD database, and keeps no password reference, not even one to an
  environment variable: you enter the password when you connect
  ([Import read profiles](#import-read-profiles)).
- **Not everything on Windows.** The PostgreSQL tools do not run there, and SQLite files are neither restored into a
  new file nor deleted there; profile files are neither imported nor exported there.

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

A role that has or reaches SUPERUSER, BYPASSRLS, REPLICATION, a server-file role (`pg_read_server_files`,
`pg_write_server_files`, `pg_execute_server_program`) or CREATEROLE before PostgreSQL 16 is never unlocked
(`studio.admin_over_privileged`). Every action also checks the admin role's rights again on its own session first;
a role that gained such a right since its unlock is refused the same way, and the unlock ends.

## The admin role's rights

Give the admin profile's role only what the actions you use need, on DEV and TEST servers only, and set it up with
your own tools: Studio never does. Every role must be able to log in and connect to the database.

| Action | The admin role needs |
|---|---|
| Any action | none of SUPERUSER, BYPASSRLS, REPLICATION, `pg_read_server_files`, `pg_write_server_files`, `pg_execute_server_program`, nor any role that has or reaches one of them; before PostgreSQL 16 also no CREATEROLE (the unlock or the action is refused otherwise) |
| Make a backup, and the backup before a delete | to read every table and sequence of the database: usually by owning them (or being a member of their owner); row security that applies to the role makes the backup fail ([Row security](#the-postgresql-tools)) |
| New database | CREATEDB ([New database](new-database.md#the-createdb-right)) |
| Restore | CREATEDB and membership in the lean restore role (`GRANT <restore role> TO <admin role>`); the restore itself runs as the restore profile ([Restore](#restore)) |
| Delete a database | to own the database (or be a member of its owner) and to read it for the backup first; for **End the other sessions (FORCE)** also the right to end those sessions, as for `pg_terminate_backend` |
| Manage roles, runtime role | CREATEROLE on PostgreSQL 16 or later, with `createrole_self_grant` unset; to grant privileges on objects, or to run the runtime-role wizard, to own them (or be a member of their owner) |

A SQLite action needs no database role: the file and its folder must be yours and writable only by you. Studio shows
the rights it found when you unlock (CREATEROLE as "needed only for role management", CREATEDB, other predefined
roles), so you can take away what you do not use.

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
no longer need yourself. A backup is a full copy of the database's data, as sensitive as the database itself: keep
the backup folder private and delete old backups by hand.

## Restore

1. Choose a backup and **Look at the restore**. This changes nothing; for PostgreSQL it shows how many extensions the
   archive creates.
2. Choose the target and type its name. **Restore** stays disabled until it matches, and it never has the focus by
   default.

Studio restores only backups from its own folder whose file still matches its description (size and SHA-256); a
changed backup, a link or a file from elsewhere is refused.

- **SQLite:** into a **new file** next to the connection's file (a plain name ending in `.db`, `.sqlite` or
  `.sqlite3`; the folder must be yours and writable only by you, every folder above it safe as for
  [New database](new-database.md), and not one of Studio's own; **not on Windows**, where Studio cannot check the
  folder's access list yet), or over the connection's **own file**. Before the file is replaced Studio backs it up, closes its own
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
PostgreSQL archive must be readable by `pg_restore`, and the database's schema must be the same before and after
the dump (if something changes the schema meanwhile, nothing is deleted: try again later), a SQLite copy must have the same schema as the file. If the
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

## Manage PostgreSQL roles

On an unlocked admin connection of a PostgreSQL **16 or later** DEV or TEST profile, **Roles** lists the server's
roles (not PostgreSQL's predefined `pg_*` roles) and changes them through Tabrion. You can create a role, change its
options or password, drop it, add a member to a group or remove it, and grant or revoke privileges on tables,
sequences, functions, schemas and databases of the **connected** database.

1. Choose the change, the role (for a membership the group and the member) and its options or privileges, then
   **Look at the change**. This changes nothing. It shows the role's current rights, what Studio refuses, warnings,
   and for a new membership which databases were checked for dangerous grants.
2. Type the name of the role whose rights change (for a membership: the member). The button stays disabled until it
   matches and never has the focus by default. If anything changed since you looked, nothing is done: look again.

A password (create and change only) is sent once with the change and is never shown, kept, logged or written to the
audit log; the audit log records only that one was set. Changing options always sets **every** option; the form
starts from the role's current values.

Studio never:

- grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, an ADMIN or GRANT option, or TRIGGER;
- makes a role a member of a predefined role, of `pg_read_server_files`, `pg_write_server_files` or
  `pg_execute_server_program`, or of a group that has or reaches a dangerous attribute or holds TRIGGER, EXECUTE on
  a foreign SECURITY DEFINER function or CREATE on a schema in **any** database of the server it can reach;
- changes its own roles: the admin role, the role of the session, the user of any of your profiles (for a profile
  without a user, your operating-system user name) and the role of any open connection; nor gives anything to, or
  changes, a group one of them is a member of;
- works on a server that **any** of your profiles marks PROD, also under another host name such as `localhost`;
- acts on a role whose name holds characters that cannot be seen (shown as `<U+XXXX>`): rename it outside Studio;
- drops a role that still owns objects: reassign them outside Studio first.

Leave `createrole_self_grant` unset for the admin role: with `set,inherit` it reaches every role it creates, so
Studio refuses every later change to those roles as one of its own (`own_role_reached`, fail closed).

After every change Studio reads the role again and shows any difference to what Tabrion reported (another session may
have changed it meanwhile). Every change is written to the audit log (`dbm.role.<change>`). The admin role needs
CREATEROLE on PostgreSQL 16 or later for this; it must not have SUPERUSER, BYPASSRLS, REPLICATION or a server-file
role, or the unlock ends.

### A runtime role for an application

With both the Schema Designer and the Database Manager turned on, **Runtime role for an application** appears under
**Roles** on the same admin unlock (the Schema Designer has no admin unlock of its own). It sets up the role an
application connects as: the role may read and write the rows of one schema (`SELECT`, `INSERT`, `UPDATE`, `DELETE`
on its tables, `USAGE` and `SELECT` on its sequences, `EXECUTE` on its functions) and connect to the database, but
never change the schema; the **protected tables** (by default `alembic_version`, the migration bookkeeping) stay
read-only for it. Tabrion does the work (`create_role` and `grant_runtime_role`).

1. Choose **Create a new login role** or **Grant an existing role again**, the role name, the schema and the
   protected tables, then **Look at the grants**. This changes nothing.
2. For a new role you may give a password; it is sent once and never shown or kept. Type the role's name to run.

Studio refuses everything the role rules above refuse (its own roles, a role that reaches a dangerous attribute, a
role with dangerous grants in any database), and also a system schema, a schema whose owner (or the owner of any
table, sequence or function in it) the admin role cannot act as, and a schema with a SECURITY DEFINER function.
Run the wizard with an admin role that owns the schema, usually the role that runs the migrations. Tables a
migration adds later get the role's rights only when the admin role creates them: run the wizard again after a
migration. The grant also sets the admin role's default privileges in the schema, so the runtime role can run every
function the admin role creates there later, also a SECURITY DEFINER function, which then runs with its owner's
rights: check the new functions after each migration and run the wizard again, or take the default back for
functions (`ALTER DEFAULT PRIVILEGES IN SCHEMA <schema> REVOKE EXECUTE ON FUNCTIONS FROM <role>`). The wizard looks
only inside the schema: a SECURITY DEFINER trigger function in another schema, fired by a trigger on one of the
schema's tables, runs with its owner's rights while the runtime role changes rows, so check the triggers' functions
too. If Tabrion refuses the grant after a new role was created (for example because every role may still
write a protected table), Studio drops the new role again. Every run is written to the audit log
(`dbm.role.runtime`).

## Profile pairs, import and export

### Profile pairs

A **pair** is one of your own read profiles with a write or admin profile coupled to it (see
[What you need](#what-you-need)). The part lists your read profiles and, for each, its write and admin profiles and
whether the pair of server and password reference is confirmed. A PROD profile is never paired. Studio reads the
password of a PostgreSQL pair only after you have confirmed that pair once, and again after the pair changes; an
imported profile is no exception. Pairs are made and changed on the **Settings** page under **Write and admin profiles**, not here.

### Export read profiles

1. Tick the read profiles to export and enter the full path of the new file, ending in `.tabrion-profile.toml`.
2. **Preview the export**. This writes nothing. It shows what the file will hold for each profile (read only, and
   whether its password comes from an environment variable, is asked for when used, or there is none) and names the
   write and admin profiles coupled to them, which are **not** exported.
3. Type the file's name to confirm, then **Export**. If anything changed since the preview, nothing is written:
   preview again.

The file is the same as one from the Schema Designer's [Export profiles](schema-designer.md#export-profiles): only
connection fields, `mode = "read_only"`, no secret ever (an `env:NAME` reference is kept, any other password is
written as `prompt`), one host per profile, a new file readable and writable only by you that never replaces a file.

### Import read profiles

1. Enter the full path of a profile file (`*.tabrion-profile.toml`) and **Preview the import**. This changes nothing.
   It lists each profile of the file with its server or file, where its password will come from, the keys Studio does
   not import, and why a profile cannot be imported.
2. Tick the profiles to import. A profile whose name you have already must get another name in its **Import ... as**
   field: Studio never replaces a profile.
3. Type the file's name to confirm, then **Import**. If the file or your profiles changed since the preview, nothing
   is imported: preview again.

What an import does and never does:

- **Read profiles only.** Every imported profile is a read profile and keeps its environment: an imported PROD
  profile stays PROD. A profile whose `mode` is not `read_only`, or that names no environment, is not imported. Nor is
  a profile that is not PROD but names the database of one of your PROD profiles (also under another host name such
  as `localhost`).
- **No secret and no password reference.** Every password of an imported profile is one you enter when you connect,
  also when the file names an environment variable (`env:NAME`) or a keyring entry: a file from elsewhere decides
  which server a profile names, so Studio never lets it decide where a password of yours comes from. To use an
  environment variable, edit the imported profile afterwards. Studio reads no password during an import.
- **The whole file is refused** when it is not a valid profile file (format version 1), holds a password, a key
  Studio does not know (such as a write or admin key) or a profile with several hosts (`db1,db2`): Studio never
  shortens it to one host and never imports part of such a file. At most 64 profiles per file.
- A relative SQLite path in the file is taken relative to the file's folder.
- `allow`, `schema_fingerprint`, `application_name` and `description` are shown as not imported: Studio's profiles do
  not have them.

The file must be your own regular file, writable only by you, with one name only (no link, no second hard link), at
most 256 KiB, in a folder that belongs to you, is writable only by you and is given by its real path; it must not be
in one of Studio's own folders. On Windows Studio does not import or export profile files yet.

Every export and import is written to the audit log (`dbm.profile_export`, `dbm.profile_import`) with the profile
names and the file's name, never its folder, a host or a password reference. If the audit log cannot be written,
nothing is done.
