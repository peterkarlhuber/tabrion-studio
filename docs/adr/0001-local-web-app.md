# STU-ADR-0001: Local web app with its own CLI; desktop packaging later

**Status:** needs E-S2 (Tauri only after 1.0 and review; recommended). T-E7 and K-10 are confirmed.

## Context

Studio is a developer tool, not a customer DBMS and not a cloud service. The Levuvo stack already has FastAPI +
React/TypeScript/Vite studios (Nodestro Studio, Axentra Studio) and a Tauri desktop shell (Nodestro Desktop).

## Decision

- Own repository `peterkarlhuber/tabrion-studio`: backend package `tabrion_studio` (FastAPI, Uvicorn, Pydantic 2,
  platformdirs, keyring) and frontend `frontend/` (React 19, TypeScript, Vite).
- Dependency: `tabrion[postgres,sqlalchemy,migrations]`, public API only.
- Start through the own CLI `tabrion-studio [PROJECT] [--port] [--no-browser]`; the Tabrion core gets no
  `tabrion studio` subcommand. One Uvicorn process on `127.0.0.1:<free port>`; REST/JSON, SSE for long queries.
- The frontend is built into the wheel (Hatch build hook). Installation with pipx or `uv tool`, before G-R from a
  git URL.
- Later: Tauri packaging like `nodestro-desktop/app` (STU-040), only after 1.0 and review.

## Consequences

Two toolchains (Python and Node) in one repository; Node is needed to build, never to run. For administration
tasks Studio points to DBeaver or pgAdmin. The bind is fixed to loopback; there is no `--host` option.

## Tests

STU-T-025 (no non-loopback bind; `tests/unit/test_cli.py`, `tests/process`), clean install without Node with
`static/index.html` in the wheel (STU-018, `scripts/smoke_wheel.py`).
