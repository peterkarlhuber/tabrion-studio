# How Studio protects your data

Studio is a tool for one person on one computer. In short:

- **Only this computer.** The server listens on `127.0.0.1` and nowhere else; there is no option to change that.
  Studio answers only addresses of the form `http://127.0.0.1:<port>/`, not `localhost`, so a web page that points
  its own name at your computer cannot read Studio.
- **No login, but a sign-in per tab.** The `tabrion-studio` command opens the browser with a one-time link. The tab
  then holds a session cookie and a second value only that tab knows. Other web pages, other local programs and
  other users of the computer have neither, and the server refuses them. Each start, and each stop, ends every
  session.
- **Read-only towards databases.** Studio 1.0 does not write to databases. SQLite files are opened read-only. Writing
  stays locked behind a separate gate; for a `PROD` profile it stays locked always.
- **Your files only.** Studio writes only its own `settings.toml` and `profiles.toml` (0600, in a private folder)
  and its trust records. It never writes a project's `.tabrion/studio.toml`, and never rewrites a file of yours that
  has issues or comments without asking.
- **Untrusted projects.** A project's profiles are used only after you confirmed exactly that file in the trust
  dialog, which shows where each password would go.
- **No passwords in files or logs.** A profile names where its password comes from (keyring, variable, file, this
  session); a plain password in a profile is refused. Passwords, sign-in tokens, parameter values and database error
  texts never reach the log or an error message.
- **Nothing leaves the computer.** The page loads nothing from other sites, there is no telemetry, and the browser
  page is protected by a strict Content Security Policy.

The technical details and their tests are in [Access protection](../architecture/access-protection.md),
[Configuration and project trust](../architecture/configuration.md), [Secret management](../architecture/secrets.md)
and [Errors and logging](../architecture/errors-and-logging.md). To report a vulnerability, follow `SECURITY.md` in the
repository; do not open a public issue.
