# Connections

The **Connections** view lists the connection profiles Studio knows, in two groups: the profiles of the project
folder and your own profiles. Select a profile to see its details, test it, or connect to it.

A profile never contains a password, only where the password comes from.

## Your profiles

Your profiles are saved in `profiles.toml` in Studio's configuration folder, readable only by you (0600). Use
**New connection** for the three-step assistant:

1. **Database:** a SQLite file (the full path; Studio always opens SQLite read-only) or a PostgreSQL server (host,
   port, database, user, TLS mode). The TLS mode "Automatic" means `verify-full` for any remote host and `prefer`
   only for this computer. **Or paste a URL** takes a `postgresql://user:password@host:port/database?sslmode=...`
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

For a PostgreSQL profile, and above all a PROD one, use a role of its own that has only `SELECT` (and `USAGE` on
the schemas): no write privileges, no `CREATE` on any schema, no `TEMP` on the database (PostgreSQL grants `TEMP` to
`PUBLIC` by default; `REVOKE TEMP ON DATABASE ... FROM PUBLIC` takes it back), and not the role your application uses,
since a role can end its own other sessions. With **read-only role** set on the profile, the diagnostics report what
the role can still write or create (`STU-DX-003`).

**Connect** opens the profile's read-only connection; the header then shows it with its environment, and a PROD
connection marks the whole window. The first PROD connection of a profile in a browser session asks first
("Production database"; the default is Cancel), and so does the first connection test of a PROD profile, because
the test connects too. A reload asks again, and so does a profile you edited or created again, or a project file you
trusted again. **Disconnect** closes the connection.

When the database stops answering, or the connection was closed (another tab's **Disconnect**, a profile you
changed), a banner "Connection lost" with **Reconnect** shows above the page you are on. Nothing was lost: Studio
only reads. **Reconnect** reads the profile again and opens a new connection, and the page goes on with it. For a
PROD profile it asks "Production database" again first. While the database is still down, the banner says why
reconnecting failed; try again once it is back.

When the profile's password is not stored yet, the password dialog opens, and the test or the connection runs again
after you saved it. A failure says what to check (host, port and firewall; user and password; the TLS mode; a file
that is no SQLite database), never the database server's own message.

**Edit** changes a profile (its name stays), **Delete** removes it and its saved password (the dialog's default is
Cancel), **Enter password** and **Remove saved password** manage the password.

If you edit `profiles.toml` by hand, Studio keeps your file as it is: a file with issues is listed with its issues
and never rewritten, and a valid file with comments is rewritten only after you confirm it ("Rewrite file and save";
the comments are not kept). If the file changed since the page read it, Studio shows the current state and you make
the change again.

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

Choose **Trust** to use the profiles, or **Don't load** (the default) to leave them unused until the page is loaded
again. After any change of the file, even a comment, Studio asks again and shows what changed since you trusted it.
A file with errors is not loaded at all; Studio says so and lists the issues.

## Passwords

| Source | Where the password is | Notes |
|---|---|---|
| System keyring | your operating system's keyring, service `tabrion-studio` | the entry belongs to the connection target, which is the profile's engine, host, port, database, user, path (SQLite) and `sslmode`: if any of them changes later (another host, database or user, or another TLS mode, for example), the saved password is not used and Studio asks again |
| Environment variable | the variable of the `tabrion-studio` process | start Studio from a shell that has it; an empty variable counts as missing |
| File | a file you own with mode 0600 or 0400, at most 4 KB, no link | a project profile's file must lie in the project folder or the configuration folder; `~` is not expanded |
| This session | Studio's memory | gone when Studio stops |

Without a usable keyring (for example on Linux without a Secret Service), the keyring choice is shown as not
available; a session password, a variable or a file still work. Studio never writes a password to a file.
