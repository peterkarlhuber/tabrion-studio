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

Browser tests (Playwright with axe; STU-005, STU-051). They start the Vite development server themselves and use the
e2e harness `harness.html` with a fake local server. `e2e/backend.spec.ts` also starts a real `tabrion-studio` when
`TABRION_STUDIO_E2E_PYTHON` names a Python in which Studio is installed with its built frontend (the wheel, or
`python scripts/build_frontend.py` plus `pip install -e .`); otherwise it is skipped. CI runs all of them in the job
`e2e` (`docs/development/ci.md`):

```bash
npm run e2e:install                                              # once: the Chromium build of the locked Playwright
npm run e2e                                                      # = npx playwright test
TABRION_STUDIO_E2E_PYTHON=../.venv/bin/python npm run e2e        # with the real-server test
STUDIO_SCREENSHOTS=/tmp/shots npx playwright test e2e/screenshots.spec.ts   # light and dark, 2x
```

**Browser build (review U-5).** Each Playwright release expects one Chromium build: `@playwright/test` 1.64.1 (pinned
in `package-lock.json`) looks for build 1248 in its browser folder (`PLAYWRIGHT_BROWSERS_PATH`, by default
`~/.cache/ms-playwright`). `npm run e2e:install` downloads exactly that build, which is what CI does. Where a different
build is preinstalled and downloads are off (`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`), every test fails with
"Executable doesn't exist". Do not reinstall there; point Playwright at the installed browser instead:

```bash
# for example an agent sandbox with Chromium 1243 in /opt/pw-browsers
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium npm run e2e
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/google-chrome npm run e2e  # or an installed Chrome
```

A neighboring Chromium build runs the suites unchanged (82 passed with 1243); CI always uses the expected build. When
`@playwright/test` is updated, update `playwright-core` to the same version (`package.json`); the CI job then installs
the new build by itself.

For `npm run dev`, start the backend on the proxied port: `tabrion-studio --port 8765 --no-browser --print-url`,
then open the printed one-time link with the port changed to 5173. The backend answers only its own Host and Origin
(STU-003); the Vite proxy presents both as the backend's.

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
