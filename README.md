<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/peterkarlhuber/tabrion-studio/master/assets/branding/tabrion-studio-logo-on-dark.png">
    <img src="https://raw.githubusercontent.com/peterkarlhuber/tabrion-studio/master/assets/branding/tabrion-studio-logo.png" alt="Tabrion Studio" width="440">
  </picture>
</p>

# Tabrion Studio

Tabrion Studio is a **local developer tool** for SQLite files and PostgreSQL databases, built on
[Tabrion](https://github.com/peterkarlhuber/tabrion). It runs on your machine, binds to `127.0.0.1` only and opens
in your browser. It focuses on understanding schemas, running queries safely (read-only by default) and checking
migration states and Tabrion profiles.

> **Status: MVP in development, not released.** On `master`: starting and signing in, connection profiles with the
> project's trust dialog and saved passwords, read-only connections to SQLite and PostgreSQL, the explorer with DDL
> and row-level security policies, the data viewer, the SQL editor (reading statements only) with CSV and JSON
> export, the migration status, the diagnostics with backup hints and the report, the settings, the keyboard and the
> command palette, in English and German. Studio never writes to a database. The independent security review of the
> MVP (G-S1) is still open.
> There is no release; nothing is published to PyPI, and no PyPI release is needed for now: distribution is by Git
> SHA pin (owner decision 2026-11-08). A later publication would need a new decision and the owner's approval G-R.
> See [docs/TODO.md](docs/TODO.md) for what comes next.

**Non-goals:** no hosted service, no shared server, no multi-user login, no replacement for administration tools
such as DBeaver or pgAdmin, no engines besides SQLite and PostgreSQL.

## Installation (from source, before the first release)

Requirements: Python 3.11 or later. Node.js is needed only to **build** the frontend, never to run Studio.

```bash
git clone https://github.com/peterkarlhuber/tabrion-studio.git
cd tabrion-studio
git checkout <commit SHA>               # the commit you pin
python scripts/build_frontend.py        # npm ci + npm run build, copies the result into the package (Node 22.22+)
python -m venv ~/.venvs/tabrion-studio
~/.venvs/tabrion-studio/bin/python -m pip install --upgrade "pip>=26.1"
~/.venvs/tabrion-studio/bin/python -m pip install . -c constraints/tabrion-integration.txt   # Tabrion's pin
```

Tabrion is not on PyPI either, so the install needs the constraints file; a plain `pip install .` fails. The
[installation guide](docs/guide/installation.md) also covers Windows, building a wheel, updating and uninstalling.

## Usage

The [user guide](docs/guide/index.md) explains installing, starting, connections, the explorer, the data viewer, the
SQL editor, the migration status, diagnostics, settings, the security model and troubleshooting.

```bash
tabrion-studio [PROJECT] [--port N] [--no-browser] [--print-url] [--idle-timeout MINUTES]
               [--state-dir DIR] [--log-dir DIR] [--config-dir DIR]
tabrion-studio --stop [PROJECT]
tabrion-studio --version
```

- `PROJECT` is the project folder (default: the current folder). Studio serves it on `127.0.0.1` at a free port and
  opens the system browser.
- Studio opens the browser with a one-time sign-in link (through a private file, so the link never shows up in the
  process list). `--no-browser --print-url` prints such a link instead, for a browser you start yourself; it works
  once.
- One instance per project folder: starting `tabrion-studio` again for the same folder opens the running instance
  instead of starting a second server.
- Project profiles in `PROJECT/.tabrion/studio.toml` are used only after you confirm them in the trust dialog, and
  again after every change of the file (`docs/architecture/configuration.md`). Your own settings and profiles live in
  the per-user configuration folder (`--config-dir` to change it).
- Studio logs to a rotating JSON file per project folder in the per-user log directory (`--log-dir` to change it);
  the command prints its path. Logs never contain passwords, tokens, parameter values or exception messages.
- Studio stops on Ctrl+C, with `tabrion-studio --stop`, or after 30 minutes without an open tab
  (`--idle-timeout 0` turns that off).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

The code and documentation are MIT, see [LICENSE](LICENSE). The names "Tabrion" and "Tabrion Studio" and the logos are
not covered by the MIT license; see [TRADEMARKS.md](TRADEMARKS.md).
