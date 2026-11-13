# How Studio protects your data

Studio is a tool for one person on one computer. In short:

- **Only this computer.** The server listens on `127.0.0.1` and nowhere else; there is no option to change that.
  Studio answers only addresses of the form `http://127.0.0.1:<port>/`, not `localhost`, so a web page that points
  its own name at your computer cannot read Studio.
- **No login, but a sign-in per tab.** The `tabrion-studio` command opens the browser with a one-time link. The tab
  then holds a session cookie and a second value only that tab knows. Other web pages and the programs of other
  users of the computer have neither, and the server refuses them; a program running as you is not kept out (see
  [below](#what-studio-does-not-protect-against)). Each start, and each stop, ends every session.
- **Read-only towards databases.** Studio 0.1 has no write path: no view and no part of its server is made to
  change data, SQLite files are opened read-only, and statements run in read-only transactions (for the limits of
  that, see [below](#what-studio-does-not-protect-against)). Writing is a later, separate work package (STU-026) that needs its own
  approval first (G-S2), and it is never meant for a `PROD` profile.
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
  dialog, which shows where each password would go.
- **No passwords in files or logs.** A profile names where its password comes from (keyring, variable, file, this
  session); a plain password in a profile is refused. Passwords, sign-in tokens, parameter values and database error
  texts never reach the log or an error message.
- **Nothing leaves the computer.** The page loads nothing from other sites, there is no telemetry, and the browser
  page is protected by a strict Content Security Policy.

## What Studio does not protect against

- **Programs running as you.** Malware or any other program with your user's rights can read what you can: the
  keyring, Studio's folders, the browser's storage and the memory of the `tabrion-studio` process. Studio does not
  defend against it.
- **Functions with side effects on PostgreSQL.** Studio runs every statement in a read-only transaction, but a
  function can still have effects outside it (for example through `dblink` or an extension that calls out). Only the role's
  rights are a hard boundary: connect with a role that has no write privileges, at least for PROD. The diagnostics
  report a role that can write although the profile says it should not.
- **Warnings are not protection.** Refusing `BEGIN`, `SET` and similar statements, and every warning before a
  statement, help you avoid mistakes; they are not the security boundary.
- **Drafts in the browser.** The SQL editor keeps your tabs' text in this browser's storage, outside Studio's private
  folders. Do not leave secrets in a draft.
- **Not reviewed for publication yet.** The independent security review of the MVP (G-S1) and a human security review
  before a public release are still open; Studio is not released.

The technical details and their tests are in [Access protection](../architecture/access-protection.md),
[Configuration and project trust](../architecture/configuration.md), [Secret management](../architecture/secrets.md)
and [Errors and logging](../architecture/errors-and-logging.md). To report a vulnerability, follow `SECURITY.md` in the
repository; do not open a public issue.
