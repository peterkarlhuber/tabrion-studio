# Connections

The **Connections** view lists the connection profiles Studio knows, in two groups: the profiles of the project
folder and your own profiles. Select a profile to see its details, test it, or connect to it.

A profile never contains a password, only where the password comes from.

## Your profiles

Your profiles are saved in `profiles.toml` in Studio's configuration folder, readable only by you (0600). Use
**New connection** for the three-step assistant:

1. **Database:** a SQLite file (the full path, without `?`, `#` or `%`; Studio always opens SQLite read-only) or a
   PostgreSQL server (host, port, database, user, TLS mode). The TLS mode "Automatic" means `verify-full` for any
   remote host and `prefer` only for this computer. **Or paste a URL** takes a `postgresql://user:password@host:port/database?sslmode=...`
   URL apart: its fields fill the form, its password moves to the password field of the next step, and the URL itself
   is cleared. A URL with anything a profile cannot hold (several hosts, a socket folder, other parameters) is
   refused as a whole.
2. **Sign-in:** where the password comes from:
   - **System keyring:** Studio saves it in your operating system's keyring;
   - **Environment variable:** read from a variable of the `tabrion-studio` process, for example `env:PGPASSWORD`;
   - **File:** read from a file that only you can read (0600);
   - **Only this session:** kept in memory until Studio stops;
   - **No password.**
3. **Name and environment:** a unique name and the environment `DEV`, `TEST` or `PROD` (required). For a
   PostgreSQL profile, you can say that its role should not be able to write; the diagnostics will check that.
   **Test the connection after saving** (on by default) runs the connection test on the saved profile (a PROD
   profile asks "Production database" first).

## Testing and connecting

**Test connection** connects, reads a few facts and closes again. The result stays in the profile's details: the
time it took and the server version, and for PostgreSQL the role, whether it is a superuser, TLS, the encoding and
the time zone; for SQLite foreign keys, the journal mode and the encoding. A superuser role, or one that bypasses
row-level security, gets a warning: Studio stays read-only, but only a role without write privileges is a hard
boundary.

For a PostgreSQL profile, and above all a PROD one, use a role of its own that can only read; see
[A read-only role for PostgreSQL](#a-read-only-role-for-postgresql) below. With **read-only role** set on the
profile, the diagnostics report what the role can still write or create (`STU-DX-003`).

**Connect** opens the profile's read-only connection; the header then shows it with its environment, and a PROD
connection marks the whole window. The first PROD connection of a profile in a browser session asks first
("Production database"; the default is Cancel), and so does the first connection test of a PROD profile, because
the test connects too. A reload asks again, and so does a profile you edited or created again, or a project file you
trusted again. **Disconnect** closes the connection, and Studio keeps it closed: **Run** in the SQL editor, **Show status** in
the migrations, **Run diagnosis** and **Inspect** in the vector data view open nothing on that profile and show the
banner "Disconnected" with **Reconnect** instead. **Reconnect** opens the connection again (a PROD profile asks
"Production database" again first) and then does what you asked for. **Connect** here, or **Open** in the explorer,
connects the profile again too. A reload of the page forgets the disconnect; no connection is open after it.

When the database stops answering, or the connection was closed (another tab's **Disconnect**, a profile you
changed), a banner "Connection lost" with **Reconnect** shows above the page you are on. Nothing was lost: Studio
only reads. **Reconnect** reads the profile again and opens a new connection, and the page goes on with it. For a
PROD profile it asks "Production database" again first. While the database is still down, the banner says why
reconnecting failed; try again once it is back.

When the profile's password is not stored yet, the password dialog opens, and the test or the connection runs again
after you saved it. A failure says what to check (host, port and firewall; user and password; the TLS mode; a file
that is no SQLite database), never the database server's own message.

**Favorite** (the star) marks a profile: the start page lists your favorites and the connections you opened last,
each with **Connect** ([Query history and favorites](history.md)).

**Edit** changes a profile (its name stays), **Delete** removes it and its saved password (the dialog's default is
Cancel), **Enter password** and **Remove saved password** manage the password.

If you edit `profiles.toml` by hand, Studio keeps your file as it is: a file with issues is listed with its issues
and never rewritten, and a valid file with comments is rewritten only after you confirm it ("Rewrite file and save";
the comments are not kept). If the file changed since the page read it, Studio shows the current state and you make
the change again.

## A read-only role for PostgreSQL

!!! warning "The database role is the boundary, not Studio"
    Studio's read-only checks (a read-only session and transaction, one statement at a time, and a statement check
    that refuses writing statements and the side-effect functions it knows) are **defense in depth**. What makes
    write protection reliable is a database role that is really read-only: no write privileges, no ownership of
    tables or schemas, and no function with side effects that it can call. Studio's diagnostics warn about a role
    that can write when the profile says **read-only role** (`STU-DX-003`, and `STU-DX-006` for ownership).

Functions that the database itself defines can have side effects that Studio cannot see. When a view calls such a
function, your SQL names only the view, so Studio's statement check has nothing to refuse, and PostgreSQL's
read-only mode does not stop every effect of a function (for example one that calls out of the database or writes
through an extension). Only the role's rights limit what such a function can do.

A role of its own, separate from the role your application uses (a role can end its own other sessions), with only
`SELECT` and `USAGE`. A generic example; replace `appdb`, `app` and `app_owner` with your database, your schema and
the role that owns the schema's tables. The example has two parts, because each part needs a different role.

First, as a superuser, or as a role that has `CREATEROLE` and owns the database (`CREATE ROLE` needs the first, the
database grants need the second): create the role, let it connect, and take back the rights PostgreSQL gives every
role:

```sql
CREATE ROLE studio_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
-- in psql, set the password without writing it into the SQL text (and your shell history):
\password studio_reader

GRANT CONNECT ON DATABASE appdb TO studio_reader;
REVOKE TEMP ON DATABASE appdb FROM PUBLIC;       -- then GRANT TEMP ON DATABASE appdb to the roles that need it
-- PostgreSQL 14 and lower only, run as a superuser (the owner of schema public; a non-owner's REVOKE has no effect):
-- REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

Then as `app_owner` (the role that owns the schema and its tables) or a superuser. Granting on the existing tables
needs their owner, and `ALTER DEFAULT PRIVILEGES FOR ROLE app_owner` needs membership in `app_owner`; the database
owner alone gets "permission denied":

```sql
GRANT USAGE ON SCHEMA app TO studio_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA app TO studio_reader;      -- the tables and views that exist now

-- tables that app_owner creates later in that schema:
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA app GRANT SELECT ON TABLES TO studio_reader;
-- functions that app_owner creates later are not executable by every role:
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

- **Nothing else:** no `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` or sequence `UPDATE`, no ownership, and no
  membership in another role (`GRANT other_role TO studio_reader`): a role it is a member of counts too.
  `NOINHERIT` is not enough, because a member can still `SET ROLE` to that role; the reading role must be a member
  of no other role.
- **Default privileges:** `GRANT ... ON ALL TABLES` covers only the tables that exist now; `ALTER DEFAULT PRIVILEGES`
  covers those the named role creates later. Check that no default privilege grants write access to the role or to
  `PUBLIC` (the diagnostics report one, `STU-DX-007`).
- **`PUBLIC`:** PostgreSQL grants some rights to every role. `TEMP` on the database is granted to `PUBLIC` by default,
  and before PostgreSQL 15 so is `CREATE` on the schema `public`; the first part of the example takes both back.
  Without that, the diagnosis reports the role (`STU-DX-003`).
- **Functions:** `EXECUTE` on functions is granted to `PUBLIC` by default, and a function declared
  `SECURITY DEFINER` runs with its owner's rights. The `REVOKE EXECUTE ON FUNCTIONS` default above covers only
  functions `app_owner` creates later (grant `EXECUTE` again to the roles that need it). For the functions that
  exist now: know which ones have side effects, and revoke `EXECUTE` on them from `PUBLIC` where the reading role
  must not call them. Studio does not check this.

Run the diagnosis on the profile with **read-only role** set afterwards; a role that can still write or create is
reported.

## The project's profiles and the trust dialog

A project can bring its own profiles in `.tabrion/studio.toml`, checked into its repository. Studio never writes that
file; edit it in the project to change those profiles.

A project file may come from someone else, and a hostile one could send a password to another host. So Studio uses
the project's profiles only after you confirmed **exactly this content** of the file. When you open Studio for the
project, the dialog **Trust the project profiles?** lists:

- every connection, and **where its password goes** ("name: source → host:port, TLS mode"), with warnings when it is
  sent to a host outside this computer, when TLS is weaker than `verify-full`, when an environment variable usually
  holds another service's credential, or when a password file lies outside the project folder;
- every host, every file reference (database and password files, marked when they are outside the project folder),
  every environment variable read as a password, and every unknown key.

A project profile never sends its password in clear text: when the server asks for it that way (the PostgreSQL
`password` method, and LDAP, PAM, RADIUS and BSD authentication, which use the same exchange), the connection fails
with "Studio could not connect". SCRAM (`scram-sha-256`, PostgreSQL's default), `md5`, certificates and Kerberos work. If your
server needs one of those clear-text methods, define the profile in your own `profiles.toml` instead: your own
profiles keep every method. This needs libpq 16 or newer, which the `psycopg[binary]` package Studio installs brings;
with an older libpq Studio logs `studio.require_auth_unavailable` and cannot refuse.

Choose **Trust** to use the profiles, or **Don't load** (the default) to leave them unused until the page is loaded
again. After any change of the file, even a comment, Studio asks again and shows what changed since you trusted it.
A file with errors is not loaded at all; Studio says so and lists the issues. One such error: a SQLite `path` with a
`?`, `#` or `%` (`config.url_character`), which Tabrion's profile file format refuses too; rename the database file.
The same holds for your own profiles: since this check, a profile in `profiles.toml` whose SQLite path has one of
these characters is skipped, and the connections page lists it with that issue until you change the path.

## Passwords

| Source | Where the password is | Notes |
|---|---|---|
| System keyring | your operating system's keyring, service `tabrion-studio` | the entry belongs to the connection target, which is the profile's engine, host, port, database, user, path (SQLite) and `sslmode`: if any of them changes later (another host, database or user, or another TLS mode, for example), the saved password is not used and Studio asks again |
| Environment variable | the variable of the `tabrion-studio` process | start Studio from a shell that has it; an empty variable counts as missing |
| File | a file you own with mode 0600 or 0400, at most 4 KB, no link | a project profile's file must lie in the project folder or the configuration folder; `~` is not expanded |
| This session | Studio's memory | gone when Studio stops |

Studio uses only the keyring package's own system keyrings: macOS Keychain, Windows Credential Manager, Secret
Service (GNOME Keyring, KeePassXC and others) and KWallet. A keyring backend that another Python package brings is not
used, since Studio cannot tell whether it keeps passwords in a plain file. To use one anyway, name its class in the
variable `TABRION_STUDIO_KEYRING_BACKEND` (for example `some_package.backend.Keyring`); the backends of
`keyrings.alt`, which keep passwords in a file, are never used. The log names the keyring Studio uses
(`studio.keyring_backend`).

Without a usable keyring (for example on Linux without a Secret Service), the keyring choice is shown as not
available; a session password, a variable or a file still work. Studio never writes a password to a file.
