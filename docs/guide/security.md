# How Studio protects your data

Studio is a tool for one person on one computer. In short:

- **Only this computer.** The server listens on a loopback address (`127.x.y.z`) and nowhere else; there is no
  option to change that. Studio answers only the exact address it printed, `http://127.x.y.z:<port>/`, not
  `localhost`, so a web page that points its own name at your computer cannot read Studio.
- **A new address for every start.** Each start picks a fresh random loopback address such as `127.83.41.29`, so
  nothing another user's program left in your browser for an earlier Studio address (for example a service worker)
  can take over a later Studio, also with `--port`. On macOS only `127.0.0.1` exists by default, and Studio uses it:
  there, on a computer shared with other users, leave `--port` out (see
  [below](#what-studio-does-not-protect-against)).
- **No login, but a sign-in per tab.** The `tabrion-studio` command opens the browser with a one-time link. The tab
  then holds a session cookie and a second value only that tab knows. Other web pages and the programs of other
  users of the computer have neither, and the server refuses them; a program running as you is not kept out (see
  [below](#what-studio-does-not-protect-against)). Each start, and each stop, ends every session.
- **Read-only towards databases.** Studio 0.1 has no write path: no view and no part of its server is made to
  change data, SQLite files are opened read-only, and statements run in read-only transactions (for the limits of
  that, see [below](#what-studio-does-not-protect-against)). Writing data, schema changes, running backups and role
  management are not goals of Studio 1.0. Later versions may add them only under change request CR-DBX (the Data
  Editor in 1.1, the Schema Designer in 1.2, the Database Manager in 1.3), each behind a server-side switch that is
  off by default, cannot be turned on from the page, and stays off until the area's own approval (G-S2, G-S3, G-S4).
  None of them can ever write to a `PROD` profile
  ([STU-ADR-0006](../adr/0006-per-area-write-kill-switches.md)). The shared foundation of these areas (STU-XW-1) is
  in the code, but every area is off unless you turn it on yourself: see
  [Write areas are off unless you turn them on](#write-areas-are-off-unless-you-turn-them-on).
- **Production databases are marked and confirmed.** A profile's environment (`DEV`, `TEST`, `PROD`) is always shown
  as text: in the header, on every tab and in the window title (`[PROD] name – Tabrion Studio`); a PROD connection
  also gets a red frame at the top of the window. Before Studio first connects to a PROD profile in a page session
  (Connect, the connection test, the explorer, the SQL editor, the migration status or the diagnostics), it asks
  "Production database"; the default is **Cancel**, which connects nothing. One confirmation counts for the whole
  page session; a reload, or a profile you edited, asks again, and the server refuses a PROD connection the page did
  not confirm. Queries on a PROD profile stop after 15 seconds, or after the profile's own statement timeout when
  it has one (the question names the limit).
- **Your files only.** Studio writes only in its own private folders: its `settings.toml` and `profiles.toml`
  (0600), its trust records, the instance files of a running Studio, the one-time sign-in files and its logs. It never writes a project's `.tabrion/studio.toml`, and never rewrites a file of yours that
  has issues or comments without asking.
- **Untrusted projects.** A project's profiles are used only after you confirmed exactly that file in the trust
  dialog, which shows where each password would go. A connection of a project profile never answers a server that
  asks for the password in clear text, so even a server the project names cannot read it that way (see
  [Connections](connections.md#the-projects-profiles-and-the-trust-dialog)).
- **No passwords in files or logs.** A profile names where its password comes from (keyring, variable, file, this
  session); a plain password in a profile is refused. Passwords, sign-in tokens, parameter values and database error
  texts never reach the log or an error message.
- **Nothing leaves the computer.** The page loads nothing from other sites, there is no telemetry, and the browser
  page is protected by a strict Content Security Policy.

!!! warning "On PostgreSQL, a read-only role is what makes write protection reliable"
    Studio's read-only checks are defense in depth, not the boundary. A database role that is really read-only (no
    write privileges, no ownership, no functions with side effects it can call) is what reliably keeps a database
    from being changed through Studio. Studio's diagnostics warn about a role that can write when the profile is
    marked **read-only role**. How to create such a role:
    [A read-only role for PostgreSQL](connections.md#a-read-only-role-for-postgresql).

## Write areas are off unless you turn them on

Studio 1.0 has no write feature. The write foundation of CR-DBX (STU-XW-1) only prepares the later areas, and each
of them stays off until you, the owner of the Studio process, turn it on. With every area off, every write route of
the server answers `405` and the write code is not even loaded.

- **One source, read once at start.** The areas are read from `write-areas.toml` in Studio's configuration folder,
  the folder that `--config-dir` (or `--state-dir`, then its `config` subfolder) names, otherwise the platform's
  configuration folder (`~/.config/tabrion-studio` on Linux, `~/Library/Application Support/tabrion-studio` on
  macOS, `%LOCALAPPDATA%\tabrion-studio` on Windows), which no environment variable moves: not `XDG_CONFIG_HOME` or
  `HOME`, whose home folder Studio reads from the system's account database. A user without an entry there (a
  container started with `--user 1234`) has no such folder: Studio starts read-only with every area off
  (`studio.write_areas_no_folder` in the start log); name a folder with `--config-dir`. Nothing else turns an area on: not an
  environment variable (also not `TABRION_STUDIO_CONFIG_DIR`, which moves Studio's other files but never this one), not a `.env` file (Studio reads
  none), not a project file, the settings page or `PUT /api/settings` (a flag key is an unknown key there), a URL,
  a header, a cookie, a profile or the database. A change needs a restart. The start log names each area as on or
  off (`studio.write_area_state`).
- **The file format.** Only a literal `true` turns an area on; anything else, a missing file, a malformed file or
  another version means off:

    ```toml
    version = 1

    [areas]
    data_editor = false      # G-S2, Studio 1.1
    schema_designer = false  # G-S3, Studio 1.2
    database_manager = false # G-S4, Studio 1.3
    ```

    The file gets the checks of Studio's own files: it must be a regular file (not a link), readable and writable by
    you only (`0600`), in a folder that only you may write. Otherwise every area is off.
- **Write and admin profiles.** A write profile (Data Editor, Schema Designer) or an admin profile (Database Manager)
  lives only in your own `profiles.toml` (`[[write_profile]]`), never in a project file: Studio refuses a project
  file that defines one. It is coupled to one of your own read profiles, uses its target and environment, and on
  PostgreSQL its own role, different from the read role. A `PROD` read profile cannot be coupled, a coupled profile
  cannot be changed to `PROD`, and changing the environment of a coupled profile ends every unlock. When you create
  one, Studio shows that it relies on the environment you gave the profile and that write credentials of a
  production database do not belong in Studio; it creates the profile only after you confirmed that. Its password
  is kept in the keyring or for this session only, and never appears in a diagnosis report, an export or a support
  bundle.
- **Write profiles come from the same folder as the switches.** While any area is on, Studio uses the write and
  admin profiles of `profiles.toml` only when its configuration folder is the one `--config-dir` or `--state-dir`
  named, or the platform's folder. If `TABRION_STUDIO_CONFIG_DIR` or `TABRION_STUDIO_STATE_DIR` chose the folder (a
  per-folder tool such as direnv can set them when you enter a project), its read profiles work as before, but its
  write and admin profiles are not listed or unlocked and none can be created there
  (`studio.write_profiles_ignored`); the start log says so with the same event. They stay in the file, unchanged.
  Start Studio with `--config-dir` for that folder to use them.
- **So do the audit log and the secret confirmations.** The audit log (`audit/`) and the confirmed pairs of server
  and secret reference (`write-secret-confirmations.json`) live in the folder of `write-areas.toml`: the folder
  `--config-dir` names, else `<--state-dir>/config`, else the platform's configuration folder. They are never in a
  state folder that `TABRION_STUDIO_STATE_DIR` chose: if the variable moved the state folder, Studio neither reads
  nor writes an `audit/` folder or a `write-secret-confirmations.json` there, so a file planted in a project folder
  cannot confirm a pair or stand in for the log. While an area is on, the start log says so
  (`studio.write_state_ignored`, without the path).
- **Unlocking.** A write or admin profile is used only after you unlock it for one open connection by typing its
  name. The unlock is kept on the server, for your page session only, and ends after 15 idle minutes, when the
  connection closes, when the profile or its read profile changes, and when Studio stops. There is no code path for
  `PROD`.
- **The write connection.** On SQLite it uses `trusted_schema = OFF`, Studio's own authorizer (no schema changes,
  no `ATTACH`, no functions or pragmas outside the read list) and SQLite's defensive mode, and it checks at unlock
  and before each later use that the file is still the one the read connection opened (device and inode). It never
  creates a database file: a missing file is refused (`file:...?mode=rw`), and nothing is left behind. On
  PostgreSQL its `search_path` is fixed to `pg_catalog, pg_temp` whatever the role sets, and the role of a write or
  admin profile is checked on connect. Reported as findings, each held directly or through a role it belongs to:
  `SUPERUSER`, `BYPASSRLS`, `CREATEROLE` or `REPLICATION`; `CREATEROLE` on PostgreSQL before 16 once more as
  dangerous (`admin.createrole_pre16`: there it can grant membership in almost any role); and membership in a
  predefined role (`admin.broad:<role>`: a `pg_*` role with an OID below 16384, never a role someone created under
  a `pg_` name), dangerous for `pg_execute_server_program`, `pg_read_server_files`
  and `pg_write_server_files`, which equal superuser rights, and a warning for the others, such as
  `pg_write_all_data` and `pg_read_all_data`. Use a role without any of these. **A write profile whose role is a
  superuser, has `BYPASSRLS` or `REPLICATION`, or is a member of `pg_read_server_files`, `pg_write_server_files` or
  `pg_execute_server_program`, directly or through a role it belongs to, is not unlocked**
  (`405 studio.write_role_over_privileged`, owner decision 1A at G-S2): Studio closes the connection, unlocks
  nothing and records the refused unlock in the audit log. `CREATEROLE` (also before PostgreSQL 16) and the other
  predefined roles, such as `pg_read_all_data`, stay warnings: the unlock goes ahead and the write state shows
  them. To see the role and every role it reaches with their attributes, run
  `SELECT r.rolname, r.rolsuper, r.rolbypassrls, r.rolreplication FROM pg_roles r WHERE pg_has_role('<role>', r.oid, 'MEMBER');`.
  For admin profiles, which finding blocks is decided with the Database Manager (G-S4); until then they are shown
  and logged.
- **Refusals echo nothing.** A refused write answers `400` or `405` with a fixed text (`403` only for the write mode
  of a query), never with a value or a secret of the request.
- **The audit log (STU-XW-2).** While an area is on, Studio records every write and admin action in a local,
  append-only log, `audit/` in the folder of `write-areas.toml` (see above; the folder `0700`, every file `0600`,
  never opened through a link, no file kept open between two records):
    - what is recorded: unlocking (also a refused unlock of a stored profile) and locking with its reason (you locked
      it, it expired, the connection closed, a profile changed, Studio stopped); creating, changing and removing a
      write or admin profile, with the read profile it is coupled to and the names of the changed keys; storing and
      removing its password; and every write run, once before it begins (`run.start`) and once after it committed
      or rolled back (`run.result`, with the row counts);
    - what a record holds: the time, profile names, kind, environment, engine, connection ID, area, action, the
      names of the changed objects, row counts, the plan hash and a result code. **Never a value**: no cell value,
      parameter, statement text, SQL literal, password or database message. A name that contains a password Studio
      knows is replaced by `[redacted]`;
    - **tamper evidence:** each record carries its sequence number, the SHA-256 hash of the record before it and its
      own hash, and `head.json` keeps the last one. Studio verifies the whole chain when it starts and whenever the
      log is listed, and finds a changed, removed, inserted, reordered or cut record and a removed anchor. Someone
      who can write your configuration folder can still rebuild a whole new chain: that is a program running as you
      (see below);
    - **no record, no action:** if the record before a write run or an unlock cannot be written, Studio does not do
      it (`405 studio.audit_unavailable`). If the record after a run fails, the write has already happened: Studio
      keeps the record as pending (in memory and in `audit/pending.json`), shows a warning in the write profile
      section and writes it, marked late, as soon as the log is writable again; until then no new write starts;
    - a log that fails verification is never repaired or appended to, so no write runs. Move the `audit` folder out
      of the folder of `write-areas.toml`, keep it for inspection, and start Studio again; it then begins a new
      chain;
    - old `<state>/audit` files are not migrated: the audit log and the confirmations now live in the folder of
      `write-areas.toml`; an older log and its confirmations stay where they were for inspection, and Studio starts a
      new chain (and asks for new confirmations) in the folder it uses now;
    - several Studio windows for different projects share one log: each record is written under a lock across
      processes (`audit/audit.lock`), after reading the end of the chain again, so their records form one chain;
    - the log grows by about 4 MB per segment (`audit-<first record>.jsonl`); Studio never deletes or overwrites a
      segment, and keeps every segment without limit (retention is unlimited, decision 3A at G-S2). To archive the
      log, stop Studio and move the whole `audit` folder aside as described above; Studio then begins a new chain.
      Do not remove single segments: the log would no longer verify. The chain has no key and no anchor outside its
      folder yet, so removing the whole folder starts a new chain without a warning (residual risk 3; an anchor in
      the keyring is planned before a public release). `GET /api/write/audit` lists the newest records and the verification, read only; no route changes the
      log, and with every area off it answers `405` like every other write route.
- **Plans, preview and confirmation (STU-XW-3).** While an area is on, a change runs only as a plan that Studio
  keeps on the server and that you saw and confirmed:
    - **preview:** Studio builds the plan, reads the target tables' catalog and answers with the plan hash, the
      targets, the triggers and rules on them, what each step does (without its values), risk notes and the text you
      type to confirm. **Triggers and rules run with the write role**, and Studio's row checks cover only the
      statement itself, not what a trigger or rule changes elsewhere: the preview names them for that reason;
    - **confirmation:** you send the plan hash and type the write profile's name, or for a destructive plan the name
      of its target table;
    - **one run, bound to you:** the plan hash covers your session, the connection, your unlock, the steps and the
      targets' fingerprint. It runs once; a second attempt, another browser tab, another connection, a plan made
      before you locked and unlocked again, and a plan older than 5 minutes are refused (`400 studio.plan_*`), and
      nothing runs;
    - **drift check:** inside the run's transaction, just before the first step, Studio reads the targets' catalog
      again (on SQLite the whole schema of the file and the file's identity; on PostgreSQL the target tables' columns,
      constraints, indexes, triggers, rules and policies and the definitions of the functions they call, again after
      locking the tables). A function replaced in the moment between that check and the steps is not seen; the
      write role's rights stay the boundary. Partitioned tables and their partitions are not changed yet
      (`studio.plan_target_partitioned`). If anything changed since
      the preview, or the SQLite file was swapped, nothing runs (`studio.plan_drift`, `studio.write_target_changed`)
      and you preview the change again;
    - the audit log's `run.start` record carries the plan hash. With every area off, the plan routes answer `405`
      like every other write route.
- **Where a write secret goes (STU-XW-4).** A profile file names both the server and where the password comes from,
  and it may not be yours (a copied file, a hand edit, later an imported profile). So Studio reads the password of a
  PostgreSQL write or admin profile only after you confirmed the **pair** (tabrion contract I-109 j, dialog D-15):
    - **every PostgreSQL write or admin profile needs this confirmation once before its first unlock**, also one you
      wrote yourself, and also when its password is in the keyring or entered for the session only. Until then the
      write profile section shows it as "not confirmed" and the unlock is refused;
    - in the write profile section, "Review" shows the server (host, port, database, TLS mode), the write role, the
      secret reference (keyring, `env:NAME`, `file:PATH` or none) and, if the profile names one, the server's
      operating system user (`requirepeer`). You confirm by typing the host. Studio reads the pair from your
      `profiles.toml` on the server, never from the page, and keeps the confirmed pair's SHA-256 in the folder of
      `write-areas.toml` (`write-secret-confirmations.json`, `0600`; see above);
    - an unlock of an unconfirmed pair is refused before any password is read, also from an environment variable or
      a file (`400 studio.secret_unconfirmed`). **A changed pair needs a new confirmation:** another host, port,
      database, TLS mode, write role, secret reference or `requirepeer` is a new pair, also when you change the
      coupled read profile rather than the write profile, so its old confirmation no longer counts and the next
      unlock is refused until you confirm again. Removing a write profile forgets its confirmation, and a new profile
      of the same name needs one of its own. Every later use of an unlock, the plan
      confirmation and run included, checks the confirmation again, so revoking it ends the unlock;
    - a server that is not on this computer (anything but a socket folder, `localhost` or a loopback address) gets a
      write or admin password only with the TLS mode `verify-ca` or `verify-full` (`400 studio.write_tls_required`);
    - a write or admin password goes to any server, this computer's included, only through SCRAM
      (`require_auth=scram-sha-256`). A write role with a password therefore needs `scram-sha-256` in
      `pg_hba.conf`, also for `localhost` and the socket; a server that offers `trust`, `peer` or another method to a
      profile with a password is refused. For `peer` or `trust` leave the write profile's password out;
    - **a local socket (`requirepeer`).** If the read profile's host is a socket folder (for example
      `/var/run/postgresql`), you can give the write profile the operating system user the server runs as, for
      example `postgres` (`requirepeer = "postgres"` in `profiles.toml`, or the optional field when you create it).
      libpq then refuses a socket that a process of another user holds, before any authentication. Studio accepts
      the key only for a socket folder (`config.requirepeer_socket_only`) and refuses an unlock that would drop it.
      Over loopback TCP there is no such check: a program that took the server's port before it started could
      receive the SCRAM exchange, which reveals no password but lets it try to guess a weak one offline. Use a long
      random password for a write role, and prefer the socket with `requirepeer` or TLS with `verify-full` on this
      computer too;
    - confirming and revoking are recorded in the audit log, with the pair's hash and the kind of reference, never the
      host, the variable name, the path or the password. SQLite write profiles have no password and need no
      confirmation.
- **No clear-text or MD5 passwords for writes (STU-XW-4).** Every PostgreSQL write and admin connection, and the
  privilege check that runs on it, tells libpq `require_auth`: `scram-sha-256` when it sends a password (see above),
  else `!password,!md5`. libpq refuses a server that asks for the password in clear text (the `password` method,
  and LDAP, PAM, RADIUS and BSD authentication) or as an MD5 hash, so a write role with a password needs SCRAM, and
  one without a password a certificate, GSSAPI or `peer`. This needs libpq 16 or newer; with an older libpq
  Studio opens no write or admin connection and refuses the unlock (`405 studio.require_auth_unavailable`).
  `psycopg[binary]` brings a recent libpq. Read connections are unchanged: project profiles refuse a clear-text
  password (`require_auth=!password`), your own read profiles keep every method.
- **libpq's environment variables do not choose the server.** libpq, the PostgreSQL client library, reads `PG*`
  environment variables for what a connection leaves out. Studio gives every such setting itself, so a variable like
  `PGHOSTADDR` or `PGSSLROOTCERT` (which a per-folder tool could set) changes neither the server, nor the
  authentication, nor TLS of any read, test, write or admin connection; `requirepeer` and the rules for this
  computer hold for the server the profile names. Where Studio cannot override a variable (a service with
  `PGSERVICE`, `PGSERVICEFILE` or `PGSYSCONFDIR`, the session settings `PGTZ`, `PGDATESTYLE` and `PGGEQO`, or for
  example `PGUSER` for a profile without a user), it opens no connection while it is set
  (`422 studio.connection_environment`): put the setting into the profile and start Studio without the variable.
  `PGCONNECT_TIMEOUT` and `PGCLIENTENCODING` still apply.
- **The Data Editor (STU-026, in development, locked by G-S2).** The Data Editor is not usable or supported: it
  exists only behind `data_editor` in `write-areas.toml`, which is off by default, and stays locked until G-S2 is
  granted. Its steps and limits are in the [Data Editor](data-editor.md) page. What it does when that flag is on in a
  test setup:
    - the page shows an "Edit data" tab on a table only when the server says the flag is on, and never on a `PROD`
      connection; the page never decides it, and with the flag off its routes answer `405` and its code is not
      loaded;
    - a changeset (cells changed, rows added and deleted) runs only as a plan (above): preview, the typed
      confirmation (the table's name when a row is deleted) and one run in one transaction;
    - every column the page showed is sent as the row's old value and must still match: a row that someone changed
      or deleted in the meantime (`studio.data_editor_conflict`), or a statement that changed not exactly one row
      (`studio.data_editor_rowcount`), rolls back the whole changeset, and the message names no value. Rows that
      triggers or rules change elsewhere are not counted. A table with triggers or rules gets a notice, not a block
      (owner decision 2026-11-15): the tab names them above the grid, and the preview names them again;
    - tables without a primary key, views, partitioned tables and `PROD` are refused, and so is a PostgreSQL table
      with a column whose type cannot be compared (`xml`, `point`, `box`, a composite type, `json[]`;
      `studio.data_editor_column_incomparable`): its value could not be checked, so a change made by someone else
      could go unnoticed. The whole table is refused, whichever columns the page shows. Text is compared exactly,
      whatever collation the column has. A `json` column is compared as `jsonb`, so a change made meanwhile only to
      the key order, the whitespace or a duplicate key (`jsonb` keeps the last) is not a conflict: the old and the
      new text count as the same value;
    - every cell has the editor of its column's type (STU-XDE-1): a choice for booleans and enums, a text field
      for the rest, with the expected format in the column header; generated columns are read only. A bytes value
      is only replaced by the content of a file the user picks (G-S2 6A), at most 64 KiB (`value.too_large`, checked
      on the page before the file is read and again by the server); it is shown by its size, never typed or shown,
      and never reaches a log, the audit log or an answer.
      Studio checks each changed value for its column (type, length, range, NULL, an enum's labels, a NOT NULL
      column a new row leaves out) and marks the cell; the preview waits until no issue is left. The server checks
      every value again before it makes the plan and refuses one that does not fit
      (`400 studio.data_editor_value_invalid`; the answer names positions, never a value), and the database checks
      the rest (CHECK constraints, uniqueness, foreign keys). A change the database refuses for such a rule rolls back
      the whole changeset (`400 studio.data_editor_constraint`, G-S2 6A); the answer names the change's position, the
      column's position where the database named a single column, and the kind of rule (`constraint.unique`,
      `constraint.foreign_key`, `constraint.not_null`, `constraint.check`, `constraint.exclusion`), never a value,
      the constraint's name or the database's message. NULL is never the empty text; a `timestamptz` needs an
      offset (`+02:00` or `Z`) and a `timestamp` takes none, so no value is shifted to another time zone on the way.
      On SQLite only `STRICT` tables have column types; in an ordinary table any value is taken as SQLite takes it;
    - before the preview and in the review, the change overview lists every changed row with its columns before
      and after, the new rows with their values, and the deleted rows by their key;
    - the grid loads up to 1,000 rows, 100 at a time; a page is appended below the rows loaded before, so a
      pending change stays bound to its row (its old value, every shown column, is what the row had when it was
      loaded), and a reload drops every pending change;
    - the changes live in the page's memory only: never in the query history, the drafts, browser storage, the logs
      or the audit log (it records the plan hash and row counts).

!!! note "Python 3.11"
    Python's `sqlite3` module can turn on SQLite's defensive mode only from Python 3.12. On Python 3.11 Studio
    refuses to unlock a SQLite write profile (`studio.write_unsupported`) instead of opening a write connection
    without it.

## What Studio does not protect against

- **Programs running as you.** Malware or any other program with your user's rights can read what you can: the
  keyring, Studio's folders, the browser's storage and the memory of the `tabrion-studio` process. Studio does not
  defend against it.
- **Functions with side effects on PostgreSQL.** Studio runs every statement in a read-only transaction, but a
  function can still have effects outside it (for example through `dblink` or an extension that calls out), and a
  function the database itself defines, for example one a view calls, is invisible to Studio's statement check.
  Only the role's rights are a hard boundary: connect with a really read-only role, at least for PROD (see the box
  above). The diagnostics report a role that can write although the profile says it should not.
- **Migration code you trust.** When you trust a project's migration files, Studio runs them as you, with your
  rights: such code can do more than Studio checks (read files, open its own database connection). Studio asks
  again whenever a trusted file changes, but trust only projects whose code you would also run with `alembic` yourself.
- **A fixed port on macOS, and old Studio tabs.** Where only `127.0.0.1` exists (macOS by default), Studio's address
  is `http://127.0.0.1:<port>/`; with `--port` it is the same every time, and another user of the computer could
  prepare that address in your browser while Studio is not running. Leave `--port` out on a shared computer. On every
  system, close Studio's tabs after you stop it: a reloaded old tab may show a page of another program that now uses
  the address (it gets no session).
- **The sign-in link in the browser history.** The one-time link that opens Studio stays in the browser's history.
  It is worthless once used, and otherwise expires within 120 seconds.
- **Windows: Studio's folders in a synced profile folder.** On Windows, Studio does not check the permissions of its
  state folder inside your user profile. The default (`%LOCALAPPDATA%`) is not synced; do not move Studio's folders
  into OneDrive or another synced folder.
- **Other local users can slow Studio down.** Any program on the computer can open connections to Studio's port
  without signing in. Studio closes a connection that does not send a complete request within 10 seconds and limits
  how often the same server error is logged, but a program that keeps opening connections can still make Studio slow
  or unreachable while it runs; it gets no access to your data that way.
- **Warnings are not protection.** Refusing `BEGIN`, `SET` and similar statements, and every warning before a
  statement, help you avoid mistakes; they are not the security boundary.
- **Drafts are SQL text on disk.** The SQL editor keeps your tabs' text in Studio's private state database (0600, in
  the state folder), never in the browser's storage. It is kept until you close the tab, or until the project's
  editor has not been opened or changed for the history's retention days. Do not type secrets into the SQL itself; give them as parameters.
- **Not reviewed for publication yet.** The independent security review of the MVP is accepted, and the security
  gate G-S1 passed on 2026-11-13 (master `9a5d4fa`). That is not a release approval: the mandatory human security
  review before any public release is still open, and the Data Editor stays locked. Studio is not released.

The technical details and their tests are in [Access protection](../architecture/access-protection.md),
[Configuration and project trust](../architecture/configuration.md), [Secret management](../architecture/secrets.md)
and [Errors and logging](../architecture/errors-and-logging.md). To report a vulnerability, follow `SECURITY.md` in the
repository; do not open a public issue.
