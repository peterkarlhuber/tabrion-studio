# New database (in development)

!!! danger "In development, locked by G-S4"
    Creating a database is part of the Database Manager (the admin area), which is **not usable or supported**. It
    exists only behind the `database_manager` switch in `write-areas.toml`, which is **off by default**, and it stays
    locked until the security gate G-S4 is in effect. No Studio version turns the switch on, and nothing in this
    page is a release of the feature. This page describes what the wizard does when a developer turns the switch on
    in a test setup, so that its behavior and its limits can be reviewed.

The **New database** wizard on the **Connections** page creates an empty SQLite file or an empty PostgreSQL
database, then offers to save a connection profile for it. It creates nothing else: no tables, no roles, no grants.

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
and the `data_editor` and `schema_designer` switches do not open it (until 2026-11-18 the wizard was the Schema
Designer's; the owner moved database creation to the admin area). With the switch off, the wizard is not shown,
both of its routes answer `405` and their code is not even loaded.

## A SQLite file

1. Open **Connections**, choose **SQLite file** in **New database** and enter the **file path**: an absolute path
   whose name ends in `.db`, `.sqlite` or `.sqlite3`.
2. Type the file name (for example `inventory.db`) to confirm. **Create file** stays disabled until it matches.
3. Studio creates the file with the mode `0600` (only you may read and write it), writes the SQLite header and turns
   on WAL mode. The wizard shows the path.

Studio creates the file inside the folder it checked, which it holds open, and checks before and after it writes the
header that the path still leads to that folder and to the very file it created (by device and inode).

Studio refuses, and creates nothing, when:

- **the file exists**, or one of its side files (`-wal`, `-shm`, `-journal`) does: Studio never overwrites a file,
  and an existing file is left exactly as it was;
- **the folder is not private enough**: it does not exist, it is not a folder, it is not yours, others may write to
  it (a mode with a group or world write bit, such as `0770` or `1777`), or its path holds a **symbolic link**
  anywhere (give the real path: on macOS, `/private/tmp` instead of `/tmp`);
- **a folder above it is not safe**: every folder above the target folder must belong to you or to `root`, and
  neither group nor others may write it unless it has the sticky bit (as `/tmp` has), as OpenSSH's `StrictModes`
  requires for `~/.ssh`. So `/tmp/mine/inventory.db` works when `/tmp/mine` is your own `0700` folder, but not
  below a shared folder that every user may write without the sticky bit;
- **the folder is Studio's own**: the configuration, state, audit and log folders, the project's `.tabrion` folder,
  and the folders inside them. Studio compares the folders themselves (device and inode), not their names, so a
  second name of one of them (a bind mount, or another spelling of the name on a case-insensitive disk) is refused
  too;
- **the path is not plain**: relative, with `..`, with control characters or with a space other than the plain space
  (such as a no-break space), on a network share or a device path, or longer than 4096 characters.

The SQLite file needs no unlock: a new file has no database role and no connection that an admin profile could
check. Every check above still applies.

**Not on Windows.** On Windows Studio does not create SQLite files (`studio.schema_create_platform`): it does not yet
check a folder's access control list, so it cannot tell whether others may write the folder. Create the file with
another tool and add it as a connection profile; PostgreSQL databases can be created on Windows as elsewhere.

## A PostgreSQL database

A PostgreSQL database is created on the server of a connection you have open, through that connection's **admin
profile** ([Database Manager](database-manager.md)), never through a write profile:

1. Open a `DEV` or `TEST` PostgreSQL connection and couple an admin profile to its profile under **Settings**. The
   admin profile's role needs the `CREATEDB` attribute: a role that is a superuser, has `BYPASSRLS` or
   `REPLICATION`, reaches a server-file role or has `CREATEROLE` before PostgreSQL 16 is refused
   (`studio.admin_over_privileged`), as for every Database Manager action. See [CREATEDB below](#the-createdb-right).
2. In **New database**, choose **PostgreSQL database** and the connection, then **unlock** the admin profile by
   typing its name. The unlock is the Database Manager's and ends after 15 minutes without use. A write unlock of
   the same connection does not create databases.
3. Enter the **database name** and, if you want, the **owner** (empty: the admin profile's role), the **template**
   (`template1` or `template0`), the **encoding** (default `UTF8`) and the **locale** (empty: the template's).
4. Type the database name to confirm and press **Create database**.

Studio checks the role's rights again at the start of every creation, on the session that runs it: a role that
gained one of those rights since the unlock creates nothing, and the unlock ends. Studio checks then whether the
database exists and refuses then without running anything. The owner must be a role
the admin profile's role is a member of (a role it cannot act as, such as `postgres`, is refused). If the role has no `CREATEDB`,
the wizard shows the steps an administrator takes outside Studio to give it, without SQL to copy: Studio builds no
statement for that.

## The CREATEDB right

`CREATEDB` is a property of the database role, not of Studio: the role keeps it in every tool and on every server
where it exists. A role with `CREATEDB` can create databases of its own and, as their owner, do there what an owner
may, including installing **trusted extensions**, which PostgreSQL installs with elevated rights. It gains nothing in
the databases that exist already. So:

- **Give `CREATEDB` only to the admin profile's role**, and only on `DEV` and `TEST` servers. A write profile's role
  must not have it: Studio refuses to unlock a write profile whose role has or reaches `CREATEDB`
  (`studio.write_role_over_privileged`), and checks again at the start of every run.
- **Keep the server up to date.** Fixed security problems in extension scripts have let such a role gain more rights
  on servers that were not updated.
- For an admin profile on `DEV` or `TEST`, `CREATEDB` is a fact (creating databases needs it); outside `DEV` and
  `TEST` Studio shows it as the warning **`admin.createdb`**.

## Save a connection profile

After the creation, the wizard offers a connection profile for the new database: a name (from the file or database
name) and the environment. A PostgreSQL profile takes the server fields of the connection it was created on. The
profile is saved in your own `profiles.toml`, like one you add by hand, and you connect to it from the list.

## Limits

These are refused on purpose, by the server, whatever the page shows:

- **`PROD` is refused.** A database is created only through an admin profile of a `DEV` or `TEST` connection; Studio
  re-checks the stored environment on every use (`studio.write_prod_locked`). There is no code path for `PROD`.
- **Empty databases only.** The wizard creates no schema, table, role or grant and runs no migration. Designing tables is the Schema Designer's.
- **No overwrite and no link.** A SQLite file is created only as a new file, never through a link and never in a
  folder others may write to, or below a folder others may write to without the sticky bit.
- **No SQLite file on Windows** (see above).
- **Nothing secret in the logs.** The local audit log records the action, the profile, the environment and the file
  or database name, never the folder, a password or the server's message. A database error is shown as its kind
  (`db.*`), never as the server's text.

## Messages

| Message | What to do |
|---|---|
| A file or link with this name, or one of its side files (-wal, -shm, -journal), exists already. … | Choose another name, or move the file yourself first. |
| Studio does not create the file in this folder … | Use a folder of your own that others cannot write to, below folders of yours or of `root` that others cannot write to (or that have the sticky bit), by its real path without links. |
| Studio does not create databases in its own configuration, state, audit or log folders or in the project's .tabrion folder. | Choose another folder. |
| On Windows Studio does not create SQLite files yet. … | Create the file with another tool and add it as a connection profile. |
| To create the database, type its name exactly as shown. | Type the file or database name exactly. |
| The server has a database with this name already. | Choose another name. |
| The admin profile's role may not create databases (CREATEDB) on this server. | Follow the guided steps: an administrator gives the admin role `CREATEDB` outside Studio. |
| The admin profile's role may not act as the owner role … | Leave the owner empty, or name a role the admin profile's role is a member of. |
| The server has no role with the owner's name. | Check the name, or leave the owner empty. |
| The admin role has or reaches SUPERUSER, BYPASSRLS, REPLICATION, a server-file role, or CREATEROLE before PostgreSQL 16. … | Use a leaner admin role; the administrator removes the other rights outside Studio. |
| The write profile's role is a superuser, has BYPASSRLS, REPLICATION or CREATEDB … | A write profile never creates databases: remove `CREATEDB` from the write role outside Studio, and use an admin profile here. |
| The write mode is locked. Unlock it again. | The unlock ended (15 minutes without use, or the connection closed); unlock again. |

## Related pages

- [How Studio protects your data](security.md#write-areas-are-off-unless-you-turn-them-on): the write switches, write
  profiles and the unlock.
- [Database Manager threat model](../architecture/database-manager-threat-model.md) and
  [STU-ADR-0008 section 10](../adr/0008-database-manager-backup-restore-roles.md#10-creating-a-database-stu-cr-2-stu-cr-3):
  the rules database creation follows (TX-29 of the
  [Schema Designer threat model](../architecture/schema-designer-threat-model.md)).
