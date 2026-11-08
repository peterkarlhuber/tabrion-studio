# Tabrion Studio

Tabrion Studio is a **local developer tool** for SQLite files and PostgreSQL databases, built on
[Tabrion](https://github.com/peterkarlhuber/tabrion). It runs on your machine, binds to `127.0.0.1` only and opens
in your browser. It focuses on understanding schemas, running queries safely (read-only by default) and checking
migration states and Tabrion profiles.

> **Status: pre-alpha skeleton.** This repository currently contains the project skeleton, the command-line
> process model and CI (work packages STU-001, STU-002, STU-018, STU-031). The page it serves is a placeholder.
> There is no release; nothing is published to PyPI. See [docs/TODO.md](docs/TODO.md) for what comes next.

**Non-goals:** no hosted service, no shared server, no multi-user login, no replacement for administration tools
such as DBeaver or pgAdmin, no engines besides SQLite and PostgreSQL.

## Installation (from source, before the first release)

Requirements: Python 3.11 or later. Node.js is needed only to **build** the frontend, never to run Studio.

```bash
git clone https://github.com/peterkarlhuber/tabrion-studio.git
cd tabrion-studio
python scripts/build_frontend.py        # npm ci + npm run build, copies the result into the package (needs Node 22)
python -m pip install .                 # the wheel contains the built frontend
```

## Usage

```bash
tabrion-studio [PROJECT] [--port N] [--no-browser] [--idle-timeout MINUTES]
tabrion-studio --stop [PROJECT]
tabrion-studio --version
```

- `PROJECT` is the project folder (default: the current folder). Studio serves it on `127.0.0.1` at a free port and
  opens the system browser.
- One instance per project folder: starting `tabrion-studio` again for the same folder opens the running instance
  instead of starting a second server.
- Studio stops on Ctrl+C, with `tabrion-studio --stop`, or after 30 minutes without an open tab
  (`--idle-timeout 0` turns that off).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE).
