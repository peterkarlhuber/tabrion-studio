# Building and testing

## Setup

Python 3.11+ with pip, and Node.js 22 with npm for the frontend:

```bash
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
(cd frontend && npm ci)
```

## Frontend

```bash
cd frontend
npm run dev            # Vite on 127.0.0.1:5173, proxies /api to TABRION_STUDIO_BACKEND (default 127.0.0.1:8765)
npm run build          # tsc -b && vite build -> frontend/dist
npm run lint           # eslint (typescript-eslint strict, jsx-a11y, react-hooks)
npm run format:check   # prettier
npm run typecheck      # tsc -b (strict)
npm test               # vitest
```

For `npm run dev`, start the backend on the proxied port: `tabrion-studio --port 8765 --no-browser`.

## Wheel

```bash
python scripts/build_frontend.py     # npm ci + npm run build, copies frontend/dist to src/tabrion_studio/static
python -m build                      # sdist + wheel; hatch_build.py refuses a wheel without static/index.html
python scripts/smoke_wheel.py dist/tabrion_studio-*.whl   # fresh venv, runs without Node on PATH
```

`src/tabrion_studio/static` is not in git; the `artifacts` setting in `pyproject.toml` puts it into the wheel and the
sdist. An editable install (`pip install -e .`) does not need it; the server then shows a "frontend not built" page.

## Python checks

```bash
ruff check . && ruff format --check .
mypy --strict src tests scripts hatch_build.py
lint-imports                                      # import contracts (no Levuvo platform)
python scripts/check_tabrion_api.py               # only public Tabrion names (AC-7)
pytest -m "not packaging"                         # unit, process-model, security and supply-chain tests
TABRION_STUDIO_DIST=dist pytest -m packaging      # checks the built wheel and sdist
mkdocs build --strict
```
