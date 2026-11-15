# New database (in development)

!!! danger "In development, locked by G-S3"
    Creating a database is part of the Schema Designer, which is **not usable or supported**. It exists only behind
    the `schema_designer` switch in `write-areas.toml`, which is **off by default**, and it stays locked until the
    security gate G-S3 is granted. G-S3 is not granted: no Studio version turns the switch on, and nothing in this
    page is a release of the feature. This page describes what the wizard does when a developer turns the switch on
    in a test setup, so that its behavior and its limits can be reviewed.

The **New database** wizard on the **Connections** page creates an empty SQLite file or an empty PostgreSQL
database, then offers to save a connection profile for it. It creates nothing else: no tables, no roles, no grants.

## Turn it on for development

The switch is read once, when Studio starts, from `write-areas.toml` in Studio's configuration folder, as for the
[Data Editor](data-editor.md#turn-it-on-for-development); only the key differs:

```bash
mkdir -m 700 -p /tmp/studio-dev/state/config
printf 'version = 1\n[areas]\nschema_designer = true\n' > /tmp/studio-dev/state/config/write-areas.toml
chmod 600 /tmp/studio-dev/state/config/write-areas.toml
tabrion-studio ./my-project --state-dir /tmp/studio-dev/state
```

Nothing else turns it on: no environment variable, project file, setting, URL, query parameter, header or profile,
and the `data_editor` and `database_manager` switches do not open it. With the switch off, the wizard is not shown,
both of its routes answer `405` and their code is not even loaded.

## A SQLite file

1. Open **Connections**, choose **SQLite file** in **New database** and enter the **file path**: an absolute path
   whose name ends in `.db`, `.sqlite` or `.sqlite3`.
2. Type the file name (for example `inventory.db`) to confirm. **Create file** stays disabled until it matches.
3. Studio creates the file with the mode `0600` (only you may read and write it), writes the SQLite header and turns
   on WAL mode. The wizard shows the path.

Studio refuses, and creates nothing, when:

- **the file exists**, or one of its side files (`-wal`, `-shm`, `-journal`) does: Studio never overwrites a file,
  and an existing file is left exactly as it was;
- **the folder is not private enough**: it does not exist, it is not a folder, it is not yours, others may write to
  it (on POSIX a mode with a group or world write bit, such as `0770` or `1777`), or its path holds a **symbolic
  link** anywhere (give the real path: on macOS, `/private/tmp` instead of `/tmp`);
- **the folder is Studio's own**: the configuration, state and audit folders and the folders inside them;
- **the path is not plain**: relative, with `..`, with control characters, on a network share or a device path, or
  longer than 4096 characters.

On Windows the folder's access list decides who may use the file; Studio checks only that the path holds no link.

## A PostgreSQL database

A PostgreSQL database is created on the server of a connection you have open, through that connection's **write
profile** ([Data Editor: before the first edit](data-editor.md#before-the-first-edit-a-write-profile)):

1. Open a `DEV` or `TEST` PostgreSQL connection and couple a write profile to its profile under **Settings**. The
   write profile's role needs the `CREATEDB` attribute, and nothing more: a role that is a superuser, has
   `BYPASSRLS` or `REPLICATION`, or reaches a server-file role is refused at the unlock
   (`studio.write_role_over_privileged`), as for the Data Editor.
2. In **New database**, choose **PostgreSQL database** and the connection, then **unlock** the write mode by typing
   the write profile's name. The unlock is the Schema Designer's and ends after 15 minutes without use.
3. Enter the **database name** and, if you want, the **owner** (empty: the write profile's role), the **template**
   (`template1` or `template0`), the **encoding** (default `UTF8`) and the **locale** (empty: the template's).
4. Type the database name to confirm and press **Create database**.

Studio checks the role's rights again at the start of every creation, on the session that runs it: a role that
gained one of those rights since the unlock creates nothing, and the unlock ends. Studio checks then whether the
database exists and refuses then without running anything. The owner must be a role
the write profile's role is a member of (a role it cannot act as, such as `postgres`, is refused). If the role has no `CREATEDB`,
the wizard shows the steps an administrator takes outside Studio to give it, without SQL to copy: Studio builds no
statement for that.

## Save a connection profile

After the creation, the wizard offers a connection profile for the new database: a name (from the file or database
name) and the environment. A PostgreSQL profile takes the server fields of the connection it was created on. The
profile is saved in your own `profiles.toml`, like one you add by hand, and you connect to it from the list.

## Limits

These are refused on purpose, by the server, whatever the page shows:

- **`PROD` is refused.** A database is created only through a write profile of a `DEV` or `TEST` connection; Studio
  re-checks the stored environment on every use (`studio.write_prod_locked`). There is no code path for `PROD`.
- **Empty databases only.** The wizard creates no schema, table, role or grant and runs no migration. The rest of
  the Schema Designer is not built yet.
- **No overwrite and no link.** A SQLite file is created only as a new file, never through a link and never in a
  folder others may write to.
- **Nothing secret in the logs.** The local audit log records the action, the profile, the environment and the file
  or database name, never the folder, a password or the server's message. A database error is shown as its kind
  (`db.*`), never as the server's text.

## Messages

| Message | What to do |
|---|---|
| A file or link with this name, or one of its side files (-wal, -shm, -journal), exists already. … | Choose another name, or move the file yourself first. |
| Studio does not create the file in this folder … | Use a folder of your own that others cannot write to, by its real path without links. |
| Studio does not create databases in its own configuration, state or audit folders. | Choose another folder. |
| To create the database, type its name exactly as shown. | Type the file or database name exactly. |
| The server has a database with this name already. | Choose another name. |
| The write profile's role may not create databases (CREATEDB) on this server. | Follow the guided steps: an administrator gives the role `CREATEDB` outside Studio. |
| The write profile's role may not act as the owner role … | Leave the owner empty, or name a role the write profile's role is a member of. |
| The server has no role with the owner's name. | Check the name, or leave the owner empty. |
| The write role is a superuser, has BYPASSRLS or REPLICATION, or is a member of a server-file role … | Use a role with only `CREATEDB` (and the owner roles it needs); the administrator removes the other rights outside Studio. |
| The write mode is locked. Unlock it again. | The unlock ended (15 minutes without use, or the connection closed); unlock again. |

## Related pages

- [How Studio protects your data](security.md#write-areas-are-off-unless-you-turn-them-on): the write switches, write
  profiles and the unlock.
- [Schema Designer threat model](../architecture/schema-designer-threat-model.md) and
  [STU-ADR-0007](../adr/0007-schema-designer-plans-and-revisions.md): the rules the Schema Designer follows.
