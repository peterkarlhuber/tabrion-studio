# UX acceptance of the MVP (STU-050) and of 1.0 (STU-054)

STU-050 accepts the MVP's user interface against planning document 08: the wireframes of sections 4.1 to 4.5, 4.8
and 4.9 are built, the flows of section 5 run as Playwright scenarios, and the acceptance is "every flow green in
E2E on Chromium, Firefox and WebKit; axe without serious or critical violations" (08 section 11, 09 STU-050). This
page turns that into a checklist: each criterion names the automated test that checks it, or the manual check and
who does it. A criterion that is not met names its open item. The section [Studio 1.0](#studio-10-stu-054) does the
same for STU-054, the acceptance of Studio 1.0 without the Data Editor.

**Acceptance run:** 2026-11-13, branch of the STU-050 pull request started on master `e9df576`, the built frontend in
a real `tabrion-studio` (editable install with `scripts/build_frontend.py`), SQLite and a throwaway PostgreSQL 16.15
cluster with `scram-sha-256`, Chromium 1243 (headless shell; CI uses build 1248). The whole e2e run after merging
master `432f6b3`: 261 passed, 30 skipped (`screenshots.spec.ts`, which runs only with `STUDIO_SCREENSHOTS`), none
failed, the PostgreSQL part of `explorer-reference.spec.ts` included. Result: every criterion that the MVP covers is
met in Chromium; Firefox and WebKit are met since PR #63 (B-2) and required since PR #67; the items listed under
[Open items](#open-items) stay open.

## How to run it

```bash
python scripts/build_frontend.py && pip install -e ".[dev]" -c constraints/tabrion-integration.txt
cd frontend
TABRION_STUDIO_E2E_PYTHON=../.venv/bin/python \
TABRION_STUDIO_E2E_EXAMPLES=<tabrion checkout at the pinned commit>/examples \
npm run e2e
```

`e2e/ux-acceptance.spec.ts` holds the MVP flows no other suite runs end to end, `e2e/ux-acceptance-1-0.spec.ts`
the 1.0 flows and the negative tests of STU-054; the other suites are named per
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
| UX-S3 | Transaction chip | `src/shell/AppShell.test.tsx` (`tx-state`: "No transaction" only; transactions come with STU-026) | met for the MVP |
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
| UX-W4 | 4.2 Error states of the test say what to check (host/port/firewall, sign-in, TLS, file missing, link, not readable, not a SQLite database) | `src/views/connections/Connect.test.tsx`, `e2e/ux-acceptance.spec.ts` ("Connect"), `e2e/backend.spec.ts` | met (fixed in this PR: eight texts named only the failure) |
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
| UX-F7 | Error recovery: "Connection lost" banner with "Reconnect"; **no silent data loss** | `e2e/connection-lost.spec.ts`, `e2e/connection-lost-backend.spec.ts`, `tests/security/test_connection_loss.py` | met in Chromium (TD-034 PR: the banner, Reconnect with D-02 again for PROD); the rest of TD-034 is open |

### Cross-cutting

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-X1 | axe without serious or critical violations: every view, dialog and notice in light and dark, English and German; and in every view suite | `e2e/a11y.spec.ts` (the light/dark × en/de matrix), the axe call in every view suite (most in light and English; several suites run the matrix too), `e2e/ux-acceptance.spec.ts` | met |
| UX-X2 | Keyboard: palette, shortcuts, F6 areas, keyboard-only main flows | `e2e/keyboard.spec.ts`, `e2e/explorer.spec.ts`, `e2e/viewer.spec.ts`, `e2e/export.spec.ts` | met for the flows that exist; the rest is STU-052's (TD-020, PR #58) |
| UX-X3 | No request leaves the local server | `e2e/network.spec.ts`, `e2e/backend.spec.ts`, `e2e/ux-acceptance.spec.ts` | met |
| UX-X4 | Every flow green on **Chromium** | CI job `e2e` | met |
| UX-X5 | Every flow green on **Firefox and WebKit** | jobs `e2e (firefox)` and `e2e (webkit)`, added in PR #59, green since PR #63, required since PR #67 | met (B-2, TD-047 done) |
| UX-X6 | Screen-reader sample (VoiceOver, NVDA) and the final accessibility report | manual, STU-053 | open (TD-022) |

## Manual checks

| Check | Who | State |
|---|---|---|
| QA-S1: a 30-minute usability session with the owner on the reference apps (08 section 11); also judges "without documentation" of UX-F1 | the owner | open (B-5) |
| Screen-reader sample with VoiceOver and NVDA | a person with both | open (TD-022) |
| macOS: ⌘ and ⌥ symbols in tooltips, palette and help (08 section 10) | a person on macOS | open; the platform switch is unit-tested (`src/keyboard/shortcuts.test.ts`, `Keyboard.test.tsx`) |
| PostgreSQL walk-through of every view on a PROD profile with `scram-sha-256` (connect failure, D-02, explorer with DDL, policies and data, editor with a parameter, an error and a cancel, migrations, diagnostics) | STU-050 | done 2026-11-13; found the empty status box of the migration view (fixed) and the items B-6, B-7 |

## Open items

Found or confirmed by this acceptance; each is in TD-046 unless another item already holds it. STU-054 decided each
B item for 1.0; what stays open moved to TD-060 (see [Open for 1.0](#open-for-10)).

- **B-1** 4.1: no project chooser, recently opened list or "Open SQLite file" on the start page. Studio serves one
  project folder per process (`docs/architecture/process-model.md`); "recently opened" is 1.0 (03 section 2 A).
  The STU-020 page PR adds favorite and recently opened *connections* to the start page; a project chooser and
  "Open SQLite file" stay open. **1.0 decision:** not built. A project chooser does not fit one project per process;
  "Open SQLite file" is the connection manager's SQLite path (New connection, File), which the start page links to.
  Recorded as a deviation from the 4.1 wireframe in TD-060 for the owner to confirm.
- **B-2** Firefox and WebKit (TD-047; done: the jobs came with PR #59, the tests were fixed in PR #63 and the jobs are
  required since PR #67). First CI run on `9741eb7`: Firefox 251
  passed, 3 failed; WebKit 236 passed, 7 failed (the rest did not run or are the screenshots and the five "chromium
  only" heap tests). Root causes as far as known: (1) WebKit logs "Refused to apply a stylesheet" under the CSP each
  time a `<select>` is created (5 real-server tests; none in Chromium or Firefox), probably the select's own
  user-agent styles, to be confirmed with a minimal page; (2) Firefox: Tab in the SQL editor appears to stay in the
  editor (the text changes after Tab), a possible keyboard trap (WCAG 2.1.2) to be confirmed and fixed; (3) the
  tooltip test reaches `:focus-visible` with `Shift` plus `focus()`, which only Chromium treats as keyboard focus; (4)
  `grantPermissions` for the clipboard exists only in Chromium; (5) WebKit saves "Query 2.sql" as "Query_2.sql".
  **1.0:** done; `ux-acceptance-1-0.spec.ts` runs in both jobs like every suite under `frontend/e2e`.
- **B-3** 4.8: the hint "Studio does not run migrations. Command: `alembic upgrade head`" with a copy button.
  **Done in the STU-054 PR:** an Alembic status with pending revisions shows "Studio does not run migrations. To apply
  the pending revisions, run this command in the project folder:" with `alembic upgrade head` (`heads` when the
  project has more than one head) and **Copy command** (en/de; `MigrationsView.test.tsx`, axe in light/dark × en/de
  in `e2e/migrations.spec.ts`). Levuvo formats get no command: their runners are the platforms' own. No command
  while the database names a revision the project does not know (08 section 4.8, "DB neuer als Code"): Alembic stops
  there, so the command would fail (review M-2 of PR #84).
- **B-4** 08 section 3: the switcher can show a latency, but showed none after Connect in the walk-through; the status
  bar's running queries (count, list with cancel) and the last query's rows and time (role and database are TD-034).
  **1.0 decision:** open (TD-060). The connection watch of TD-034 reads the latency but does not hand it to the header,
  and the status bar facts need a workspace-wide query state that the views do not share yet; both are more than a
  fix in this PR.
- **B-5** QA-S1 usability session (needs the owner). **1.0:** open; 08 section 11 asks for a session per release
  (STU-050 and STU-054), so one session with the owner on the 1.0 build covers both (TD-060).
- **B-6** SQL editor: after a run stops at a missing parameter, the focus moves to the parameter field, but
  CodeMirror's completion list stays open over the editor. Done in the STU-020 page PR: the list closes when the
  field takes the focus (`EditorView.test.tsx`).
- **B-7** Explorer at 1280 × 800: the connection card takes the upper half of the page, so the tree and the detail
  start below the fold; 08 section 4.3 keeps the explorer as a sidebar beside the detail. **1.0 decision:** open
  (TD-060); it belongs to the workspace layout with the routes `/c/:conn/...` (TD-041, TD-052), not to a CSS fix
  that would move the profile picker the other views share.
- TD-032, TD-033, TD-034, TD-041, TD-042, TD-043, TD-020, TD-022 as named in the tables.

## Studio 1.0 (STU-054)

STU-054 accepts Studio 1.0 **without the Data Editor**: "Flows 1.0 E2E ohne Data Editor; Schreibfunktionen gesperrt
(Negativtest); de/en vollständig" (09 STU-054; 08 section 11 adds section 4.7, D-07, the 1.0 settings and the
languages). Its prerequisites STU-020 to STU-025 are merged. The Data Editor (STU-026, 08 section 4.6, D-03 to D-05)
stays locked behind G-S2 and is outside this acceptance; the checks below prove that it stays locked.

**Acceptance run:** 2026-11-13, the STU-054 branch started on master `d72a14f`, the built frontend in a real
`tabrion-studio` (`scripts/build_frontend.py`, the package from the branch's `src`), SQLite, Chromium 1243 (headless
shell); Firefox and WebKit run the same suites in CI (jobs `e2e (firefox)` and `e2e (webkit)`). The whole e2e run:
359 passed, 31 skipped (`screenshots.spec.ts`; `explorer-reference.spec.ts` without the reference apps), 2 failed
because the shared machine's disk was full during the run (`ENOSPC` when the browser context closed), and both
passed alone afterwards; Vitest 1 993 passed. PostgreSQL criteria
(Levuvo formats, DDL against `pg_dump`) run in the `backend integration` job. Result: every 1.0 criterion that 1.0
builds is met except the German diagnosis texts (UX-10-L4), which wait on TD-032; STU-054 is accepted once TD-032
is merged and the owner has decided the items under [Open for 1.0](#open-for-10).

`e2e/ux-acceptance-1-0.spec.ts` runs:

- **the 1.0 tour, in English and in German:** the DDL of a table (read from the real database), the ER diagram with
  its table alternative, a query with a `:name` parameter, Format, the export dialog, the query history (the
  parameter's name, never its value), the migration status and a diagnosis with its backup hints and report buttons;
  axe (WCAG 2.0 to 2.2 A and AA, color contrast included) in light and dark at every step, so the 1.0 views pass in
  light/dark × en/de against the real server; on the German pages no text of the English catalog and no raw i18n key
  (text marked `lang="en"`, the findings' own texts, is left out: TD-032; once PR #85 merges, only tabrion's
  `TAB-*` texts keep that mark and the exclusion narrows to them, TD-060);
- **the page offers no data editing (STU-026 locked):** on a DEV and on a PROD connection the data viewer's grid has
  no field and no editable cell, a double click edits nothing, no control is named edit, insert, delete row, save
  changes, commit, roll back, discard, unlock or write mode; the access chip is text (no role, no tab stop), says
  "Read-only" or, on PROD, "Writing locked" with its tooltip, and a click on it opens no dialog; on PROD the SQL
  editor refuses a write before it runs;
- **the API refuses every write, on DEV and on PROD (03 section 8):** a write mode (`mode: "write"`) is `403
  studio.query_write_unsupported`; 16 writing statements (DML, DDL, `ATTACH`, `VACUUM`, `REINDEX`, `ANALYZE`,
  `SELECT … INTO`, transaction control) are refused by the query and the export route before they run (`400
  studio.statement_*`); the statements the text check lets through (`WITH … DELETE`, writing `PRAGMA`s) fail in the
  database, which is read-only for every Studio connection (`db.*`); every changing method on the data viewer's routes
  and on the Data Editor's planned `changesets` route is `405 studio.method_not_allowed`; afterwards the database file
  is byte for byte the same, no journal or write-ahead file appears next to it, and its schema, rows and
  `user_version` are unchanged;
- **no route writes to a database:** every changing route of the server's OpenAPI document is on a list with the
  reason it never writes to a database (Studio's own files and state, opening or closing read-only connections,
  read-only work). The complete list, over every route of the app including those left out of the document, is
  `CHANGING_ROUTES` in `tests/security/test_every_route.py`; a new changing route fails there until it is reviewed
  and listed.

### History, favorites, scripts, format, completion (08 sections 2, 4.4, 4.10 and 7; STU-020)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-H1 | 09 STU-020: "E2E: Favorit anlegen/öffnen, Historie ohne Parameterwerte, Skript speichern/öffnen, Autocomplete aus Katalog" | `e2e/history-backend.spec.ts` (real server: favorite from the manager to the start page, the history without the parameter value in the page, the API and every file of the state folder, a script saved and opened, completion of columns from the catalog), `e2e/history.spec.ts` (keyboard, axe light/dark × en/de), `tests/security/test_history.py` (canary) | met |
| UX-10-H2 | 08 section 2 "Historie¹" `/history`, 08 section 7 ⌘Y / Ctrl+H | `e2e/history.spec.ts`, `e2e/history-backend.spec.ts`, `e2e/ux-acceptance-1-0.spec.ts` (Ctrl+H in both languages) | met; 08 section 4.4 sketches the history as a tab beside the results, Studio has it as its own view opened from the editor (TD-060 notes the deviation) |
| UX-10-H3 | 08 section 4.10 "Historie¹ (an/aus, Aufbewahrung, löschen)" | `src/views/connections/ConnectionsView.test.tsx` ("history settings (STU-020)": off, retention, delete after the confirmation), `e2e/history.spec.ts` (delete dialog with the safe focus), `tests/security/test_history.py` (retention) | met |
| UX-10-H4 | 08 section 7 "Formatieren¹" ⌥⇧F / Alt+Shift+F | `e2e/history.spec.ts` (format, undo), `e2e/history-backend.spec.ts` (under the strict CSP), `e2e/ux-acceptance-1-0.spec.ts` | met |
| UX-10-H5 | Drafts of the editor tabs in the state database (08 section 4.4, F-5) | `e2e/drafts-backend.spec.ts`, `tests/security/test_drafts.py` | met (PR #77) |
| UX-10-H6 | 08 section 4.1 "zuletzt geöffnet" | `e2e/history-backend.spec.ts` (recent after Connect) | met for connections; the project chooser is B-1 |

### Export (08 sections 4.4 and 5; STU-021)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-E1 | 09 STU-021: "Export 1 Mio. Zeilen ohne RAM-Spitze > 100 MB" | `tests/security/test_exports.py` (tracemalloc) | met |
| UX-10-E2 | 08 section 5 "Export¹": CSV or JSON, the fetched rows or again up to a limit, a note when cut | `e2e/editor-backend.spec.ts` (TD-043: CSV and JSON through the real route, cut at a limit), `e2e/export.spec.ts` (keyboard, cancel, a broken stream, axe), `e2e/ux-acceptance-1-0.spec.ts` (the dialog in en/de, light/dark) | met in the SQL editor; from the data viewer open (TD-043) |

### DDL, policies, row estimates (08 sections 2 and 4.3; STU-022)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-D1 | 09 STU-022: "DDL-Vergleich mit `pg_dump --schema-only` für Referenz-Apps" | `tests/security/test_definitions.py` (recorded and live `pg_dump`, both reference apps; PostgreSQL job) | met (TD-035 lists what Studio's DDL leaves out) |
| UX-10-D2 | 08 section 4.3 "DDL¹" tab, policies, row estimate | `e2e/explorer-definitions.spec.ts` (focus, escaping, Copy DDL), `e2e/a11y-audit.spec.ts` (DDL, Policies, row count), `e2e/ux-acceptance-1-0.spec.ts` (the DDL of a real SQLite table in en/de) | met |

### ER diagram (08 sections 2, 4.7, 9 and 10; STU-023)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-R1 | 09 STU-023: "150 Tabellen < 2 s Layout; SVG/PNG-Export" | `src/views/explorer/er/layout.perf.test.ts`, `e2e/er-diagram.spec.ts`, `e2e/backend.spec.ts` (STU-023, real server under the CSP) | met |
| UX-10-R2 | 08 section 4.7: choice of tables, layout again, SVG, overview map, zoom, a click opens the table; the table alternative (section 9, mandatory) | `src/views/explorer/er/ErDiagram.test.tsx`, `e2e/er-diagram.spec.ts` (keyboard), `e2e/ux-acceptance-1-0.spec.ts` (axe light/dark × en/de with the table alternative) | met; the route `/c/:conn/schema`, column-to-column lines and cardinality marks are open (TD-052) |
| UX-10-R3 | 08 section 10: PNG at 2× | `src/views/explorer/er/exportImage.test.ts` (`pngScale`) | met |

### Migration status of the Levuvo formats (08 section 4.8; STU-024)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-M1 | 09 STU-024: "Fixtures der drei Repos (kopiert, keine Abhängigkeit) korrekt erkannt" | `tests/security/test_levuvo_migrations.py` with `tests/security/levuvo_fixtures` (PostgreSQL job) | met |
| UX-10-M2 | 08 section 4.8 for every format: state, pending steps, database newer than the code, changed checksums; Studio never migrates | `src/views/migrations/MigrationsView.test.tsx`, `e2e/migrations.spec.ts` (axe light/dark × en/de), `e2e/ux-acceptance-1-0.spec.ts` (SQLite's `user_version` from the real server in en/de) | met; the Levuvo formats need PostgreSQL, which the e2e jobs do not run (TD-041), so the page shows them from the harness and the backend is checked in the PostgreSQL job |
| UX-10-M3 | 08 section 4.8 "Befehl: alembic upgrade head [⧉]" | `src/views/migrations/MigrationsView.test.tsx` (TD-046 B-3; none with an unknown revision), `e2e/migrations.spec.ts` (axe light/dark × en/de with the command; none beside an unknown revision) | met (fixed in this PR) |

### Diagnosis report and backup hints (08 section 4.9; STU-025)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-G1 | 09 STU-025: "Bericht ohne Secrets (Kanarientest)" | `tests/security/test_diagnosis_report.py` | met |
| UX-10-G2 | 08 section 4.9 "⤓ Bericht¹" and the backup hints | `e2e/backend.spec.ts` (STU-014: both reports from the real route, without the file's name or path; the backup hint), `e2e/diagnostics.spec.ts` (keyboard download, refused report, axe), `e2e/ux-acceptance-1-0.spec.ts` (en/de, light/dark) | met; the report from the command line is open (TD-036) |

### Writing stays locked (03 sections 5.2 and 8, 08 sections 3, 4.6 and 5; STU-054 negative test)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-W1 | No data editing in the page: no editable grid, no editing control, no write mode, no D-03, D-04, D-05 | `e2e/ux-acceptance-1-0.spec.ts` (DEV and PROD), `e2e/editor.spec.ts` ("the page offers no write control"), `src/components/dialogs/StudioDialogs.tsx` (D-03 to D-05 and D-07 not built) | met |
| UX-10-W2 | 08 section 5 "Schreibmodus entsperren¹": "PROD: Chip zeigt ⛔ mit Tooltip, kein Dialog"; 08 section 3: the unlock dialog only after G-S2 | `e2e/ux-acceptance-1-0.spec.ts` (the chip is text with its tooltip; a click opens nothing; DEV says "Read-only" and has no unlock either), `e2e/backend.spec.ts` ("Writing locked"), `e2e/a11y-audit.spec.ts` (tooltip) | met |
| UX-10-W3 | 03 section 8, the 1.0 row: "Schreibfunktionen gesperrt: alle Schreibversuche über API 403 (Negativtest)" | `e2e/ux-acceptance-1-0.spec.ts` (DEV and PROD, the file unchanged), `tests/security/test_queries.py` (line 400: write mode refused, `studio.query_write_unsupported`), `tests/security/test_read_only.py` (STU-T-010 to STU-T-012: the database refuses a write that passes the check), `tests/security/test_exports.py` | met in substance: every write is refused and nothing changes. The status is 403 only for the write mode; a writing statement is 400 (`studio.statement_*`) and a changing method on a read route 405. The owner confirms that this meets "403" (TD-060) |
| UX-10-W4 | 03 section 5.2: "Für `prod` ist Schreiben in 1.0 technisch nicht möglich (Backend verweigert, kein UI-Pfad)" | `e2e/ux-acceptance-1-0.spec.ts` (PROD), `e2e/connections.spec.ts`, `e2e/editor-backend.spec.ts` (TD-038: no PROD connection without D-02, on the server) | met |
| UX-10-W5 | No changing API route writes to a database | `tests/security/test_every_route.py` (`CHANGING_ROUTES`: every route of the app with a changing method, hidden ones included, such as `POST /api/session` and `POST /api/control/{action}`, each with its reason; a hidden route added in the test is caught), `e2e/ux-acceptance-1-0.spec.ts` (the documented API of the running server against the same list) | met (review M-1 of PR #84: the list first read only the OpenAPI document) |

### Languages: de/en complete (08 section 0, E-S3; STU-054)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-L1 | en.json and de.json have the same keys, no empty value, the same placeholders and tags, every text translated that is not a name, German typography | `src/i18n/i18n.test.ts` | met |
| UX-10-L2 | Every key the page names exists; every plural has both forms in both languages; no visible text is written into the markup outside the catalog | `src/i18n/coverage.test.ts` (new: the page source parsed with the TypeScript compiler; `t()`, `<Trans i18nKey>` and key tables; JSX text and the fixed text of JSX expressions and of `aria-label`, `aria-description`, `aria-roledescription`, `aria-valuetext`, `title`, `placeholder`, `alt`, `label`: strings, static parts of templates, both sides of conditions, `??` and `||`; a short list of names that are the same in every language: SQL keywords, pragma names, `NULL`, the key badges, a JSON example) | met. Not seen by the scan: a module constant or a state value rendered later, component text props under other names, and `t(variable)` (TD-060) |
| UX-10-L3 | A German page shows no English catalog text and no raw key, in every 1.0 view, against the real server | `e2e/ux-acceptance-1-0.spec.ts` (the German tour; it compares phrases, catalog texts with a space and at least 12 characters, so a single English word such as a button label is left to the key parity of UX-10-L1) | met |
| UX-10-L4 | The diagnosis' findings in German | depends on TD-032 (German details and remedies of the `STU-DX-*` checks, localized in another lane); the titles, details and remedies of tabrion's own `TAB-*` checks come from tabrion in English and are marked `lang="en"` | open: **dependency TD-032**, whose decision says the `STU-DX-*` texts must be en/de before STU-054 is accepted; that tabrion's `TAB-*` texts stay English and labeled until tabrion has a catalog needs the owner's confirmation (TD-032, TD-060) |
| UX-10-L5 | Problem texts of the server in both languages | the page shows its own en/de text per problem code (`src/views/connections/messages.ts`; `i18n.test.ts`) | met |

### Settings and dialogs of 1.0 (08 sections 4.10 and 6)

| ID | Criterion | Check | Result |
|---|---|---|---|
| UX-10-S1 | D-07 "EXPLAIN-Kostenwarnung prod¹" and the setting "EXPLAIN-Warnung prod an/aus¹" | not built; 03 section 5.3 calls the warning optional for 1.0 | open: the owner decides whether 1.0 needs it (TD-060) |
| UX-10-S2 | "Schreibmodus-Ablauf¹" (write mode expiry) | belongs to STU-026 (G-S2) | not in 1.0 |
| UX-10-S3 | Every flow green on Chromium, Firefox and WebKit | CI jobs `e2e`, `e2e (firefox)`, `e2e (webkit)`; every spec under `frontend/e2e` is required and may not skip (the CI step "Every suite ran and none was skipped" finds new specs by itself, so `ux-acceptance-1-0.spec.ts` needs no change in `.github/`) | met in Chromium locally; Firefox and WebKit by the CI run of the STU-054 pull request |
| UX-10-S4 | QA-S1 for 1.0 (08 section 11) | manual, the owner | open (TD-060, B-5) |

### Open for 1.0

All in TD-060.

- **Owner decisions:** whether 400/405 for refused writes meets 03 section 8's "403" (UX-10-W3; recommended: yes,
  the substance is "every write refused, nothing written"); D-07 for 1.0 or later (UX-10-S1; recommended: later, as
  03 section 5.3 calls it optional); tabrion's own finding texts in English in 1.0 (UX-10-L4); the B-1 deviation.
- **Dependency:** TD-032 (German `STU-DX-*` details and remedies), in another lane (PR #85); then the German tour's
  exclusion narrows to tabrion's `TAB-*` texts.
- **Owner decision for STU-026 too:** 03 section 8 asks for 403 for PROD writes in the Data Editor row as well; the
  decision on UX-10-W3 is recorded for STU-026.
- **Source scan limits:** UX-10-L2 (module constants, state, other text props, `t(variable)`).
- **Open from the MVP:** B-4, B-5 (QA-S1, now for 1.0), B-7; TD-043 (export from the data viewer), TD-052 (the ER
  route and line details), TD-036 (the report from the command line), TD-034 (status bar facts).
- **Deviation noted:** the query history is its own view, not a tab beside the results (UX-10-H2).
