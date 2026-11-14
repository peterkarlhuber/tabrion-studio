# Troubleshooting

## Installing

| You see | What to do |
|---|---|
| `No matching distribution found for tabrion` | Tabrion is not on PyPI: install with `-c constraints/tabrion-integration.txt`, see [Installation](installation.md) |
| `AssertionError: Candidate is not for this requirement` | your pip is older than 26.1; run `python -m pip install --upgrade "pip>=26.1"` in the virtual environment first |
| ``… static/index.html is missing: build the frontend first with `python scripts/build_frontend.py` (needs Node.js 22 and npm); the wheel must contain the built frontend (STU-018).`` | the frontend is not built; run `python scripts/build_frontend.py` (with Node.js 22.22 or later), then install again |
| `npm warn EBADENGINE Unsupported engine` during `python scripts/build_frontend.py`, then the build may fail with another error | Node.js is older than 22.22. npm only warns and goes on, so the build can fail later or succeed by chance; update Node.js and build again. Node is needed only for the build, never to run Studio |
| `tabrion-studio: command not found` | the virtual environment's `bin` folder (`Scripts` on Windows) is not on your `PATH`; call the command with its full path or add the folder |

## In the browser

| You see | Why | What to do |
|---|---|---|
| "Not signed in. Run `tabrion-studio` in your project folder to open Studio." | the tab was not opened by Studio (a new tab, a bookmark, a copied address) | run `tabrion-studio` again; it opens a signed-in tab |
| "This sign-in link was already used." or "… has expired." | a sign-in link works once, within 2 minutes | run `tabrion-studio` again |
| "Your session has ended." | Studio stopped or restarted, or another local server replaced Studio's cookie | run `tabrion-studio` again |
| "The local server does not answer." | Studio stopped (Ctrl+C, `--stop`, the idle timeout) | run `tabrion-studio` again |
| the browser does not open, or shows a "file not found" page | a browser in a sandbox (on Linux, for example the Firefox snap or a Flatpak) cannot read Studio's private sign-in file; Studio prints a hint when it detects one | start with `tabrion-studio --no-browser --print-url` and open the printed link yourself, or use `--state-dir` with a folder the browser can read |
| an address with `localhost`, `[::1]` or another `127.…` address is refused | Studio answers only the loopback address it printed, which is new for every start | use the address Studio printed, or run `tabrion-studio` again |
| "Kept in this browser only: your settings.toml has issues …" | Studio does not change a file with issues, or one written by hand, from the general settings | fix `settings.toml`, or rewrite it from the Settings view |
| "Your profiles.toml has issues, so Studio does not change it." | the file has an error or an unknown key | fix the listed issues in the file |
| the keyring choice says "not available on this computer" | no usable keyring (on Linux: no Secret Service or KWallet running) | use "Only this session", a variable or a file, or start a keyring service |
| "Connection lost" with **Reconnect** | the database stopped answering (a restart, the network), or the connection was closed in another tab or after a profile change | check that the database is up, then press **Reconnect**; a PROD profile asks "Production database" again |
| "Project profiles not loaded" | `.tabrion/studio.toml` has errors | fix the listed issues in the project file |

## In the views

Each view says what went wrong in its own words, and what to check. The most common ones:

| You see | What to do |
|---|---|
| "The profile's SQLite file does not exist." | the path in the profile is wrong, or the file moved; edit the profile under **Connections** |
| "The file is not a SQLite database, or the database is damaged." | the profile names another file (for example a `-wal` file or a backup archive); point it at the database file |
| "The profile's SQLite file is a link or changed while it was opened." | Studio opens only the file itself; point the profile at the real file, not at a symbolic link |
| "Studio could not connect. Check the host and the port …" | PostgreSQL: check host, port and firewall, the user and the password, and the TLS mode. Studio shows the same text for each of these, because the database server's own message is not shown |
| "There is no password for this profile." or "The environment variable … is not set." | store the password under **Connections**, or start `tabrion-studio` from a shell that has the variable |
| "Other users can read the password file." | `chmod 600` the file (see [Connections](connections.md#passwords)) |
| "This profile is marked as PROD now. Confirm the production database, then try again." | the profile was changed to PROD in another tab or in the file; confirm "Production database" and try again |
| "Studio keeps at most 8 connections open. Close one first." | **Disconnect** a connection you no longer need |
| "Studio runs only reading statements …" or "… BEGIN, COMMIT, SET and similar statements are refused." | Studio does not write to databases; run changes with your own tools |
| "The statement ran longer than the profile's time limit." | the limit is the profile's own statement timeout, 15 seconds for PROD, or the default from [Settings](settings.md); narrow the query |
| "This result is no longer kept on the server (Studio keeps the last 16)." | run the statement again |
| "Studio no longer keeps this page. Start again at the first page." | the data viewer keeps only the recent pages; use **Back to the first page** |
| "The project's Alembic files are not trusted." | open **Review files** in the **Migrations** view and trust them, if you trust the project |
| "Alembic could not read the revisions …" | run `alembic heads` in the project to see the error |
| "The file is larger than … too large to assemble in the browser" (export) | export fewer rows or columns, or filter the query |

## In the terminal

| Message | What to do |
|---|---|
| `tabrion-studio: not a folder: …` | name an existing project folder |
| `tabrion-studio: cannot use the state directory …` | the folder is not private (another user owns it, or others may write to it) or is a link; fix its owner and mode (`chmod 700`), or choose another one with `--state-dir` |
| `tabrion-studio: cannot listen on 127.0.0.1:<port>: …`, `cannot listen on 127.0.0.0/8 port <port>: …` or `port <port>: [::1] is used by another program` | the port given with `--port` is in use (also on `[::1]`); choose another one or leave `--port` out |
| `Note: only 127.0.0.1 is available here, so --port … gives the same address every time` | on this system (macOS by default) Studio cannot use a fresh address per start; on a computer shared with other users, leave `--port` out |
| `another Tabrion Studio holds the lock for … but does not answer` | a Studio for this folder is starting or hangs; wait a moment, or end that process |
| `the running Tabrion Studio did not answer` or a failed server proof | the program on the recorded port is not that Studio; Studio sends it nothing. Stop the old process and start again |
| `No Tabrion Studio is running for …` (after `--stop`) | nothing to stop |
| `the Tabrion Studio for … does not answer` (after `--stop`) | the instance holds the folder's lock but its control channel is gone (it hangs or is just starting); wait a moment and try again, or end the process |
| `the stop request failed: …` | the running Studio did not take the request, or the program on the recorded port is not that Studio (then Studio sends it nothing); end the old process by hand |
| `the Tabrion Studio for … did not stop within 15 s` | it is still shutting down; wait and run `--stop` again, or end the process |

## Logs

The log file's path is printed at the start (`studio-<key>.log` in the log folder, see
[Starting and stopping](starting.md#logs)). An error page that shows a log ID has an entry with the same ID in that
file. The log holds no passwords or data, so you can attach the matching lines to a bug report; check them first.
