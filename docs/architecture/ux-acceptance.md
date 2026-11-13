# UX acceptance of the MVP (STU-050)

STU-050 accepts the MVP's user interface against planning document 08: the wireframes of sections 4.1 to 4.5, 4.8
and 4.9 are built, the flows of section 5 run as Playwright scenarios, and the acceptance is "every flow green in
E2E on Chromium, Firefox and WebKit; axe without serious or critical violations" (08 section 11, 09 STU-050). This
page turns that into a checklist: each criterion names the automated test that checks it, or the manual check and
who does it. A criterion that is not met names its open item.

**Acceptance run:** 2026-11-13, branch of the STU-050 pull request on master `e9df576`, the built frontend in a real
`tabrion-studio` (editable install with `scripts/build_frontend.py`), SQLite and a throwaway PostgreSQL 16.15
cluster with `scram-sha-256`, Chromium 1243 (headless shell; CI uses build 1248). The whole e2e run: 259 passed, 30
skipped (`screenshots.spec.ts`, which runs only with `STUDIO_SCREENSHOTS`), none failed, the PostgreSQL part of
`explorer-reference.spec.ts` included. Result: every criterion that the MVP covers is met in Chromium; Firefox and
WebKit are open (B-2), and so are the items listed under [Open items](#open-items).

## How to run it

```bash
python scripts/build_frontend.py && pip install -e ".[dev]" -c constraints/tabrion-integration.txt
cd frontend
TABRION_STUDIO_E2E_PYTHON=../.venv/bin/python \
TABRION_STUDIO_E2E_EXAMPLES=<tabrion checkout at the pinned commit>/examples \
npm run e2e
```

`e2e/ux-acceptance.spec.ts` holds the flows no other suite runs end to end; the other suites are named per
criterion below. The PostgreSQL part of `e2e/explorer-reference.spec.ts` also runs where
`TABRION_STUDIO_E2E_PG_PARTS`, `TABRION_STUDIO_E2E_PG_MEASUREMENTS` and, for a server with passwords,
`TABRION_STUDIO_E2E_PG_PASSWORD` are set (CI does not set them, TD-041). Backend criteria with PostgreSQL run in the
`backend integration` job (`pytest -m "postgres or postgres_superuser"`).

## Checklist

Result: **met** (the test passes, or the manual check was done), **open** (with its item), **later** (the plan puts it
after the MVP; marked ¹ in 08).

### Status displays and dialogs (08 sections 3 and 6)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-S1 | Environment badge as text in the header, on every tab and in the title; PROD adds the 3 px frame | `e2e/status.spec.ts`, `e2e/contrast.spec.ts` (frame 3:1), `e2e/backend.spec.ts` (real server) | met |
| UX-S2 | Access mode chip: "Read-only", "Writing locked" on PROD; no write control before G-S2 | `e2e/backend.spec.ts` (STU-008 Connect), `e2e/editor.spec.ts` ("the page offers no write control") | met |
| UX-S3 | Transaction chip | `e2e/status.spec.ts` ("No transaction" only; transactions come with STU-026) | met for the MVP |
| UX-S4 | Connection state in the switcher (connected, connecting, disconnected, error) | `src/shell/AppShell.test.tsx`, `e2e/connections.spec.ts` | met; after Connect the switcher shows no latency (B-4) |
| UX-S5 | Status bar: server version, role, database or file, last query's rows and time, running queries with cancel, language | `e2e/a11y-audit.spec.ts` (landmark), `src/App.test.tsx` | open: only server and language (TD-034, B-4) |
| UX-S6 | D-01, D-02, D-06, D-08: modal, named, safe option focused, Tab trapped, Escape cancels, focus returns | `e2e/dialogs.spec.ts`, `src/components/dialogs/StudioDialogs.test.tsx` | met |
| UX-S7 | D-02 before the first PROD connection of every view, and the server refuses a PROD open the page did not confirm | `e2e/connections.spec.ts`, `e2e/editor.spec.ts`, `e2e/explorer.spec.ts`, `e2e/diagnostics.spec.ts`, `e2e/viewer-backend.spec.ts`, `e2e/editor-backend.spec.ts` (TD-038) | met |

### Wireframes (08 section 4)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-W1 | 4.1 Start: welcome, what to do next, how Studio is started | `src/App.test.tsx` (STU-050 test), `e2e/ux-acceptance.spec.ts` | met (fixed in this PR: the page said the views "arrive with the next versions"); project chooser, recently opened, "Open SQLite file" are not in the MVP (B-1) |
| UX-W2 | 4.2 Connection manager: list by project and user, search, detail, Test / Connect / Edit / Delete | `e2e/connections.spec.ts`, `e2e/backend.spec.ts` (STU-008) | met |
| UX-W3 | 4.2 Assistant in 3 steps (SQLite path or PostgreSQL fields, pasted URL without password, password source, name and required environment, test) | `src/views/connections/*.test.tsx`, `e2e/connections.spec.ts`, `e2e/backend.spec.ts` | met; "Browse…" and "Save in: Project" are not offered (TD in the STU-008 section of TODO) |
| UX-W4 | 4.2 Error states of the test say what to check (host/port/firewall, sign-in, TLS, file missing, link, not readable, not a SQLite database) | `src/views/connections/Connect.test.tsx`, `e2e/ux-acceptance.spec.ts` ("Connect"), `e2e/backend.spec.ts` | met (fixed in this PR: seven texts named only the failure) |
| UX-W5 | 4.3 Explorer: tree by schema and type with keyboard (↑↓←→), filter (⌘P / Ctrl+P), detail tabs Columns, Constraints, Indexes, Relationships, Data, DDL; SQLite tab; states loading, empty, more than 2 000 objects | `e2e/explorer.spec.ts`, `e2e/explorer-reference.spec.ts` (both reference apps, SQLite and PostgreSQL), `src/views/explorer/*.test.tsx` | met; context menu, virtualized tree, route `/c/:conn/t/...` open (TD-041) |
| UX-W6 | 4.3 DDL and policies (STU-022) | `e2e/explorer-definitions.spec.ts`, `tests/security/test_definitions.py` | met |
| UX-W7 | 4.4 SQL editor: run the statement or selection (⌘↵), script (⇧⌘↵), cancel (⌘.) with "Canceled after x s", parameters, one result per statement, messages with "Go to statement", read-only refusal, NULL and cut values, cell detail, drafts, D-06 | `e2e/editor.spec.ts`, `e2e/editor-backend.spec.ts`, `src/views/editor/*.test.ts(x)` | met; error positions, column resizing, formatting¹, history¹ open (TD-033) |
| UX-W8 | 4.5 Data viewer: filters with =, ≠, <, >, contains, is NULL as parameters; pages by key without a total; hint without a primary key; page size | `e2e/viewer.spec.ts`, `e2e/viewer-backend.spec.ts` | met; "Open as SQL", the estimate in the status line, header sorting open (TD-042) |
| UX-W9 | 4.8 Migration status: format found, version in the database and heads, pending revisions, database newer than the code, changed checksums; Studio never migrates | `e2e/migrations.spec.ts`, `src/views/migrations/MigrationsView.test.tsx`, `tests/security/test_migrations.py`, `test_levuvo_migrations.py` | met (fixed in this PR: an empty status box); the copyable upgrade command open (B-3) |
| UX-W10 | 4.9 Diagnostics: summary by severity, each finding with code, severity, explanation and remedy; integrity check only on request with cancel; report download | `e2e/diagnostics.spec.ts`, `e2e/backend.spec.ts` (STU-014) | met; route, German texts, progress, documentation links and OK rows open (TD-032) |

### Flows (08 section 5)

| ID | Flow and its success criterion | Check | Result |
|---|---|---|---|
| UX-F1 | First start: launch link, D-01, profiles, first table **in under 60 s without documentation** | `e2e/ux-acceptance.spec.ts` ("First start", measured: about 4 s); without documentation: QA-S1 (manual) | met (automated part); QA-S1 open (B-5) |
| UX-F2 | Connect: profile, secret, implicit test, workspace; **an error shows the remedy** | `e2e/ux-acceptance.spec.ts` ("Connect"), `e2e/backend.spec.ts`, `e2e/connections.spec.ts` | met; the workspace overview after Connect is open (TD-034) |
| UX-F3 | Run a query: ⌘↵, the parameter panel takes the focus for a `:name` without value; **parameters never in the history** | `e2e/editor.spec.ts` ("parameters: a missing value stops the run and gets the focus"), `src/views/editor/drafts.test.ts` (drafts keep title, profile and text, nothing else) | met (there is no history before 1.0) |
| UX-F4 | Cancel: ⌘. or the button, "Canceled after x s", **effective in under 1 s** | `e2e/editor-backend.spec.ts` (STU-T-020), `e2e/editor.spec.ts` | met |
| UX-F5 | Export¹: CSV or JSON, all fetched rows or again up to a limit, **a note when cut** | `e2e/export.spec.ts`, `e2e/editor-backend.spec.ts` (TD-043) | met in the SQL editor; from the data viewer open (TD-043) |
| UX-F6 | Unlock writing¹: PROD shows ⛔ with a tooltip and no dialog | `e2e/backend.spec.ts` ("Writing locked"), `e2e/a11y-audit.spec.ts` (tooltip) | later (STU-026, after G-S2); the locked state is met |
| UX-F7 | Error recovery: "Connection lost" banner with "Reconnect"; **no silent data loss** | none yet | open (TD-034; built in a parallel lane) |

### Cross-cutting

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-X1 | axe without serious or critical violations, light and dark, English and German, in every suite | `e2e/a11y.spec.ts` and the axe call in every view suite, `e2e/ux-acceptance.spec.ts` | met |
| UX-X2 | Keyboard: palette, shortcuts, F6 areas, keyboard-only main flows | `e2e/keyboard.spec.ts`, `e2e/explorer.spec.ts`, `e2e/viewer.spec.ts`, `e2e/export.spec.ts` | met for the flows that exist; the rest is STU-052's (TD-020, PR #58) |
| UX-X3 | No request leaves the local server | `e2e/network.spec.ts`, `e2e/backend.spec.ts`, `e2e/ux-acceptance.spec.ts` | met |
| UX-X4 | Every flow green on **Chromium** | CI job `e2e` | met |
| UX-X5 | Every flow green on **Firefox and WebKit** | jobs `e2e (firefox)` and `e2e (webkit)`, workflow-change PR #59 (advisory until green) | open (B-2, TD-047) |
| UX-X6 | Screen-reader sample (VoiceOver, NVDA) and the final accessibility report | manual, STU-053 | open (TD-022) |

## Manual checks

| Check | Who | State |
|---|---|---|
| QA-S1: a 30-minute usability session with the owner on the reference apps (08 section 11); also judges "without documentation" of UX-F1 | the owner | open (B-5) |
| Screen-reader sample with VoiceOver and NVDA | a person with both | open (TD-022) |
| macOS: ⌘ and ⌥ symbols in tooltips, palette and help (08 section 10) | a person on macOS | open; the platform switch is unit-tested (`src/keyboard/shortcuts.test.ts`, `Keyboard.test.tsx`) |
| PostgreSQL walk-through of every view on a PROD profile with `scram-sha-256` (connect failure, D-02, explorer with DDL, policies and data, editor with a parameter, an error and a cancel, migrations, diagnostics) | STU-050 | done 2026-11-13; found the empty status box of the migration view (fixed) and the items B-6, B-7 |

## Open items

Found or confirmed by this acceptance; each is in TD-046 unless another item already holds it.

- **B-1** 4.1: no project chooser, recently opened list or "Open SQLite file" on the start page. Studio serves one
  project folder per process (`docs/architecture/process-model.md`); "recently opened" is 1.0 (03 section 2 A).
- **B-2** Firefox and WebKit are not run (TD-047, workflow-change draft PR #59).
- **B-3** 4.8: the hint "Studio does not run migrations. Command: `alembic upgrade head`" with a copy button.
- **B-4** 08 section 3: the switcher can show a latency, but showed none after Connect in the walk-through; the status
  bar's running queries (count, list with cancel) and the last query's rows and time (role and database are TD-034).
- **B-5** QA-S1 usability session (needs the owner).
- **B-6** SQL editor: after a run stops at a missing parameter, the focus moves to the parameter field, but
  CodeMirror's completion list stays open over the editor.
- **B-7** Explorer at 1280 × 800: the connection card takes the upper half of the page, so the tree and the detail
  start below the fold; 08 section 4.3 keeps the explorer as a sidebar beside the detail.
- TD-032, TD-033, TD-034, TD-041, TD-042, TD-043, TD-020, TD-022 as named in the tables.
