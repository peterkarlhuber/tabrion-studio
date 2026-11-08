# Contributing

Thank you for helping. Tabrion Studio is a local developer tool that holds credentials to real databases, so it has
strict rules about security and dependencies; please read this page before opening a pull request. Code,
documentation, commits and ADRs are in US English.

## Development setup

Python 3.11 or later with pip (uv works as well, but is not required), Node.js 22 with npm:

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
(cd frontend && npm ci)
```

Run the backend with `tabrion-studio --port 8765 --no-browser` and the frontend with `npm run dev` in `frontend/`
(Vite proxies `/api` to port 8765). Details: [docs/development/building.md](docs/development/building.md).

## Checks

Run everything CI runs before you push:

```bash
ruff check . && ruff format --check .
mypy --strict src tests scripts hatch_build.py
lint-imports
python scripts/check_tabrion_api.py
pytest -m "not packaging"
(cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test)
python scripts/build_frontend.py && python -m build && TABRION_STUDIO_DIST=dist pytest -m packaging
python scripts/smoke_wheel.py dist/tabrion_studio-*.whl
mkdocs build --strict
```

## Rules for changes

- **Tabrion API:** use only public Tabrion names (`scripts/check_tabrion_api.py`, AC-7). A Studio need for a new
  core API goes to the tabrion repository and is accepted only if a non-Studio application benefits as well.
- **No Levuvo platform:** Studio never imports orkexa, axentra or nodestro (`lint-imports`).
- **Dependencies:** runtime dependencies need a reason in the pull request and must pass the license policy
  (`scripts/check_licenses.py`); frontend packages follow the versions of axentra-studio (STU-ADR-0004).
- **Security:** the server binds loopback only; there is no option for another address. No secret, parameter value
  or result row in logs or error messages. No `dangerouslySetInnerHTML` (eslint enforces it).
- **Writes:** write paths stay behind the server-side flag until G-S2 (STU-ADR-0003).
- **CI:** actions are pinned by full commit SHA; no workflow publishes anything (`tests/security`).
- **Tests:** a bug fix comes with a test that fails without it. No new skips or xfails without a reason.

## Pull requests and merging

- One work package per pull request where possible; name it (for example `STU-003`) and list each acceptance
  criterion with its evidence (test, CI job, SHA).
- Update `CHANGELOG.md` (`Unreleased`) and `docs/TODO.md`.
- Pull requests are merged with merge commits (no squash or rebase). The required check is `ci-ok`.
