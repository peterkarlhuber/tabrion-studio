# QA-S1 checklist suite (Playwright)

QA-S1 is the manual usability session of Studio 1.0 with the owner (08 section 11; TD-046 B-5, TD-060,
[UX acceptance](../architecture/ux-acceptance.md)). The suite in `frontend/qa-s1/` automates what a browser can
judge of its checklist (v2, cases S1-01 to S1-38), so regressions show up **before** the manual pass. It does not
replace that pass: QA-S1 acceptance stays manual, and the suite never writes into the tester's checklist (TD-068).

## What it does

- One test per checklist case, titled with its id (`S1-09 ...`); `cases.ts` says per case whether it is fully or
  partly automated and what stays for the manual pass. The overall-impression questions have no test.
- Every test starts its own `tabrion-studio` on a fresh copy of the QA kit's demo project ("Beispielprojekt": the
  profiles "Shop (DEV)" and "Shop (PROD)", SQLite files with Alembic revisions, scripts), signs in through the
  one-time link Studio prints (`--print-url`), and trusts the project in dialog D-01. No case can affect another.
- The browser is a German Chromium (`de-DE`, Europe/Vienna). The tests find controls by the German labels the
  checklist quotes, so a renamed label fails a test and shows that checklist and UI drifted apart.

## Requirements

| Variable | Meaning |
|---|---|
| `TABRION_STUDIO_E2E_PYTHON` | A Python in which Studio is installed with its built frontend (the wheel, or `python scripts/build_frontend.py` plus `pip install -e .`), as for `e2e/backend.spec.ts` |
| `QA_S1_DEMO_BUILDER` | The QA kit's `build_demo.py`, which writes the demo project (standard library, deterministic). The kit is not part of this repository |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | Optional: a preinstalled Chromium, as in [Building and testing](building.md) |
| `QA_S1_OUTPUT` | Optional: the output folder (default `frontend/qa-s1-results`, ignored by Git) |

Without the first two variables every test is skipped. The suite is **not** part of CI: it lives outside
`frontend/e2e/`, the folder of the main Playwright config (`frontend/playwright.config.ts`) and of the CI check that
every spec there passed, and it has its own config. It is still type-checked (`tsconfig.e2e.json`) and linted with
the rest of the frontend.

## Running it

```bash
python scripts/build_frontend.py && pip install -e .     # once, in the Python named below
cd frontend
TABRION_STUDIO_E2E_PYTHON=../.venv/bin/python \
QA_S1_DEMO_BUILDER=/path/to/qa-kit/build_demo.py \
  npx playwright test -c qa-s1/playwright.config.ts                   # all cases
TABRION_STUDIO_E2E_PYTHON=../.venv/bin/python \
QA_S1_DEMO_BUILDER=/path/to/qa-kit/build_demo.py \
  npx playwright test -c qa-s1/playwright.config.ts -g "S1-09|S1-15"    # some cases
```

## Results

- The console list and the HTML report (`frontend/qa-s1-results/html`, or `QA_S1_REPORT`).
- `frontend/qa-s1-results/results.json` (or `QA_S1_RESULTS`): one entry per case id with `result` (`pass`, `fail`,
  `not_automated`), `automation`, a note, the run time and, for a failed case, `screenshots/<id>.png`.

The first full run (master `77e549c`, 2026-11-14) passed 36 of 38 cases. It found S1-09 (a mouse click on a table in
the ER diagram did nothing), S1-15 (opening a history entry right after a run could drop the editor tab, now and
then) and, in S1-32, a raw `access.csrf_invalid` message in a tab whose session a second start replaced; the fixes are
tracked in TD-069.
