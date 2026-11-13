# App shell and design system (STU-005, STU-051)

The frontend in `frontend/` is a React 19 / TypeScript / Vite single-page app that the wheel serves from
`tabrion_studio/static/`. This page describes the shell, the design system, the languages, the status displays and
the dialogs, and how they are tested. The connection manager and the server settings are STU-008
(`docs/architecture/configuration.md`); the SQL editor (STU-012) is described [below](#sql-editor-stu-012); the
explorer (STU-010) in `docs/architecture/explorer.md`, and its data tab (STU-013 page) in
`docs/architecture/viewer.md`. Studio has **no write UI for
databases** (the Data Editor, STU-026, stays locked behind G-S2); the only files it writes are its own profiles and
settings.

## Shell

```
┌ header: mark · "Tabrion Studio" · connection switcher · [PROD] · Writing locked · No transaction ┐
├──┬────────────────────────────────────────────────────────────────────────────────────────────────┤
│⌂ │ [Query 1 PROD] [parts-dev DEV] [parts-pg TEST]      ← tabs, only with a workspace               │
│🗄│ main view: Start · Connections · SQL editor · Migrations · Diagnostics · Settings · Not found    │
│  │                                                                                                 │
│⚙ │                                                                                                 │
├──┴────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ● Local server 0.1.0.dev0                                                        Language: English │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- `src/App.tsx` chooses between the shell (with a session) and the sign-in notice (`src/shell/SessionNotice.tsx`).
  With a session it sends the heartbeat; when the server answers 401 (or 403 `access.csrf_invalid`), the heartbeat
  stops and the page says "Your session has ended. Run `tabrion-studio` again to open Studio." (review S-1).
- `src/shell/AppShell.tsx`: skip link, header with the status displays and the buttons for the command palette and
  the keyboard shortcuts, activity bar (Start, Connections, Explorer, SQL editor, Migrations, Diagnostics,
  Settings), tabs, main area, status bar (server version or "does not answer", language).
- `src/router.ts`: a minimal history router for `/`, `/connections`, `/explorer`, `/sql`, `/migrations`,
  `/diagnostics` and `/settings`. The backend serves `index.html` for exactly these paths (`CLIENT_ROUTES` in
  `app.py`; a test keeps both lists equal); every other path stays 404.
- `src/workspace.ts`: the active connection, its environment and the open tabs. The connection manager and the
  migration status view fill the active connection when they open one (`ConnectionSession`, provided by `App`); it
  also remembers the PROD profiles confirmed with D-02 in this page session (a reload asks again). The SQL editor
  asks D-02 through the same confirmations (`useConnect().guard`); it does not fill the header or the tabs yet
  (TD-033). `src/views/connections/useConnect.tsx` is the "Connect" flow (D-02, `POST /api/connections`, the
  header's "connecting" state).

## Design system

- **Tokens** (`src/styles/tokens.css`, prefix `--tb-`): the structure of axentra-studio's tokens; semantic tokens
  only in components. The palette comes from the logo, which has no hue: graphite neutrals plus one indigo accent
  (the link colour of the Tabrion docs). Environment tokens `--tb-env-dev`, `--tb-env-test`, `--tb-env-prod` (DEV
  neutral grey, TEST amber, PROD red and filled), `--tb-null`, `--tb-diff-added/changed/removed`.
- **Themes:** light and dark, both complete. `src/theme.ts` writes the resolved theme to `<html data-theme>`: the
  system setting unless the user picks one in the settings. **Density:** compact (28 px rows, default) or normal
  (36 px), `<html data-density>`. Both, and the language, are saved to Studio's `settings.toml` when chosen and read
  from it when the page opens (`src/appearance.ts`, TD-021; `docs/architecture/configuration.md`).
- **Typography:** system UI stack; monospace for code and values (`ui-monospace, SFMono-Regular, Menlo, Consolas`).
- **Focus:** one 2 px focus ring for everything, at least 3:1 against every surface.
- **Components** (`src/components/ui/`): Button, IconButton, EmptyState and Dialog are copied from
  `axentra-studio/src/components/ui/` at `caa52b5` (MIT, same author) and adapted to the `--tb-` tokens; each file
  names its origin (planning decision E-S6: copies, no shared package). The status displays (`EnvBadge`,
  `AccessModeChip`, `TxChip`, `ConnectionSwitcher`) are new.
- **Logo:** `frontend/public/branding/` holds the symbol and the lockup in a light and an on-dark variant, derived by
  `assets/branding/build.py`; the page picks the variant that matches the resolved theme. No CSS filters (a test
  checks that the frontend sources contain none). The favicon shows the symbol only.

**Deviation: no Radix Dialog.** Planning document 08 names Radix Dialog. Its scroll lock injects a `<style>` element,
which the Content-Security-Policy of STU-003 (`style-src 'self'`) refuses. The dialog is therefore a native modal
`<dialog>` (`showModal()`: the page behind it is inert) with the focus rules below on top. Radix stays the choice for
popovers, menus and tooltips, which do not inject styles.

## Languages

English is the source language, German is complete (`src/i18n/locales/en.json`, `de.json`; i18next and
react-i18next). The language follows the choice in `settings.toml`, then the one stored in this browser, then the
browser's languages, then English, and sets
`<html lang>`. `src/i18n/i18n.test.ts` checks that both files have the same keys, the same placeholders and inline
tags, no empty strings and no untranslated text apart from names. German texts address the user informally ("du"),
like the planning documents. es, fr, it and pt-BR are a later extension (STU-027).

## Status displays (08 section 3)

| Display | States now | Where |
|---|---|---|
| Environment badge | DEV, TEST, PROD (always as text, WCAG 1.4.1) | header, every tab, window title `[PROD] name – Tabrion Studio`; PROD adds a 3 px red frame at the top |
| Access mode | "Read-only"; "Writing locked" on PROD | header chip, not a control (no write mode before G-S2) |
| Transaction | "No transaction" | header chip |
| Connection | none; connected (latency), connecting, disconnected, error | switcher button, opens the connection manager |
| Server | connecting, version, does not answer | status bar |

## Dialogs (08 section 6)

D-01 (trust project profiles), D-02 (connect to PROD), D-06 (unsaved script) and D-08 (quit with running queries)
are in `src/components/dialogs/StudioDialogs.tsx`; their owners arrive with STU-006, STU-008, STU-012 and STU-011.
D-03..D-05 belong to the Data Editor and are not built. Every dialog follows the same rules:

1. it is modal and named by its title (`aria-labelledby`) and its text (`aria-describedby`);
2. the initial focus is on the option that changes nothing ("Don't load", "Cancel"); a destructive action (Discard,
   Quit) is never the default;
3. Tab and Shift+Tab stay inside the dialog;
4. Escape and the close button cancel; a click outside does nothing;
5. the focus returns to the element that opened the dialog.

## Keyboard (STU-052, 08 section 7)

Studio is keyboard first (08 section 1). `src/keyboard/` holds one table of shortcuts (`shortcuts.ts`) that is used
for matching and for display, so the palette, the help and the header buttons always show the keys that really work:
⌘ on macOS, Ctrl elsewhere ("Strg" in German), detected from the browser (08 section 10). Browser-reserved shortcuts
(⌘W, ⌘T, ⌘N) are never taken.

| Shortcut | macOS | Windows/Linux | What |
|---|---|---|---|
| Command palette | ⌘K | Ctrl+K | opens the palette (also the header button) |
| Keyboard shortcuts | ⌘/ | Ctrl+/ | opens the help with this table (also the header button and the palette) |
| Next / previous area | F6 / ⇧F6 | F6 / Shift+F6 | header, activity bar, tabs, main area (or its panes), status bar, wrapping around |
| Run (SQL editor) | ⌘↵ | Ctrl+Enter | the selection, or the statement at the cursor |
| Run script (SQL editor) | ⇧⌘↵ | Shift+Ctrl+Enter | every statement of the tab, in turn |
| Cancel query (SQL editor) | ⌘. | Ctrl+. | also Escape in the results |
| New / close editor tab | ⌥T / ⌥W | Alt+T / Alt+W | ⌘T and ⌘W are the browser's; ⌥⌘W (08 section 7) closes other tabs in Safari |
| Save as .sql (SQL editor) | ⌘S | Ctrl+S | downloads the tab's text |

- **Command palette** (`CommandPalette.tsx`): a modal native `<dialog>` like the other dialogs, with the WAI-ARIA
  combobox pattern: the focus stays in the search, `aria-activedescendant` names the active command, ↑ ↓ Home End
  move, Enter or a click runs, Escape closes, Tab stays in the search, and the focus returns to the opener. A status
  region announces the number of matches. The commands (`commands.ts`) go to the views of the activity bar (Start,
  Connections, Explorer, SQL editor, Migrations, Diagnostics, Settings), change the theme, the density and the
  language, and open the help; the current page, theme, density and language are marked. The filter matches every
  word, without case and accents. No command writes to a database; theme, density and language are saved to
  `settings.toml` like in the settings view (`src/appearance.ts`, TD-021). The SQL editor adds its commands while its
  page is shown; the history adds its own when it arrives.
- **Areas** (`regions.ts`): each area carries `data-region`. A page can split the main area into panes with areas of
  their own (08 section 7, "Explorer/Editor/Ergebnis"): the explorer's `explorer` (the tree) and `detail`, the SQL
  editor's `editor`, `parameters` (only with `:name` placeholders) and `result`. F6 stops at the areas that hold no
  other area, so on those pages it steps through the panes instead of the main area. A focus outside every stop (the
  main area itself after "Go to …", a toolbar between two panes) moves to the next stop after it in the page's order,
  or with Shift+F6 to the last one before it. An area whose markup sets `tabindex="-1"` (the main area, the status
  bar) is focused itself. Any other area gets the focus on its entry (`data-region-entry`: the tree's current item,
  the result grid, the first parameter value, the SQL text inside the editor's shadow root), else its current item
  (the active navigation link, the selected tab), else its first control, else the area itself, which then shows the
  focus ring (`data-region-focus`, WCAG 2.4.7). An empty pane focused that way is focusable only until the focus
  leaves it, so the next F6 finds the grid or the tab that appeared meanwhile (C-1 of `stu-052b-review.md`).
- While any modal dialog is open, the page shortcuts do nothing: the dialog owns the keyboard.
- The SQL editor's shortcuts work on its page; the help lists them in a group of their own and the palette offers
  its commands (`src/keyboard/editorActions.ts`) while the page is shown. A shortcut with ⌥ matches the physical key
  too, because ⌥T types "†" on a Mac. The explorer filter (⌘P / Ctrl+P) works on the explorer page and has a help
  group of its own. Formatting and the history come with 1.0 (`docs/TODO.md`, TD-020).

## SQL editor (STU-012)

`src/views/editor/` (03 section 2 C, 08 sections 4.4, 5 and 7) is the page `/sql`. It is loaded on demand
(`React.lazy`), so CodeMirror is not part of the other views' bundle. It is read-only like the query engine it calls
(STU-011, [Query engine](queries.md)): the page never sends `mode`, and there is no write control. The Data Editor and
the editor's write mode (STU-026, D-03..D-05) stay locked behind G-S2.

| Part | File | What |
|---|---|---|
| Editor | `SqlEditor.tsx` | CodeMirror 6 with `@codemirror/lang-sql` (PostgreSQL or SQLite by the profile's engine) through `@uiw/react-codemirror`; syntax colors from the `--tb-` tokens; Tab leaves the editor (no tab trap) |
| Statements | `sqlScan.ts` | splits at `;` outside literals, quoted identifiers, dollar quotes and comments, and finds `:name` placeholders (`::` is a cast), with the backend's rules; the server checks every statement again |
| Parameters | `ParamPanel.tsx`, `params.ts` | text, number, boolean, null, JSON (sent as text, cast in the SQL); a missing or invalid value stops the run and gets the focus |
| Run | `EditorView.tsx` | the selection or the statement at the cursor; a script runs statement by statement and stops at the first error or a cancel; D-02 through the page's shared confirmations (`useConnect().guard` of STU-008: a PROD database confirmed in the connection manager is not asked again, a changed target asks again); the run reopens a connection the server closed once |
| Events | `api/queries.ts` | `POST /api/queries`, the SSE stream read with `fetch` (the CSRF header), `GET /api/queries/{id}` every 250 ms when the stream breaks, row pages, cancel |
| Grid | `ResultGrid.tsx`, `rowCache.ts` | TanStack Virtual in both directions; rows in chunks of 50, at most 10 000 rows and 64 MB (estimated) in the browser, least recently used dropped; a failed or wrong page is asked for again (3 times); WAI-ARIA grid with `aria-activedescendant` |
| Detail | `CellDetail.tsx`, `format.ts` | the whole value as plain text, JSON indented, bytes as hex; invisible and bidi characters shown as marks |
| Drafts | `drafts.ts` | title, profile and text of every tab in `localStorage` under a key per project (a hash of its folder), merged by tab with other pages (never parameter values or results); D-06 before closing a changed tab, "Save" downloads a `.sql` file |

**CSP and CodeMirror.** CodeMirror writes its styles through `style-mod`, which adds a `<style>` element to the
document; the CSP of STU-003 (`style-src 'self'`) refuses that, and Studio keeps its CSP. In a shadow root
`style-mod` uses a constructed style sheet (`adoptedStyleSheets`), which `style-src` does not govern, so the editor is
mounted in a shadow root of its own; its look comes from the `--tb-` tokens, which inherit into it. CodeMirror sets
inline styles only through the CSSOM (`element.style`), which the CSP allows. `e2e/editor-backend.spec.ts` runs the
built page in a real `tabrion-studio` and records no CSP violation.

**Memory (acceptance "100k rows within the memory limit").** A result stays in the backend (03 section 6). The grid
holds rows in chunks of 50 and asks for the chunk of a visible row together with the next missing ones, as many as
fit in about 4 MB by the size of the rows seen so far (one chunk first, at most 500 rows). It drops the least
recently used chunks while it holds more than 10 000 rows or more than 64 MB, estimated with text as UTF-16 (2 bytes
per code unit) plus an overhead per value and row: the server allows results of up to 200 MB (`max_result_mb`) and
cells of up to 64 KB of text, so a bound in rows alone would not hold (M-4 of `stu-012-review.md`). The limit of 03
section 10 is 300 MB of browser memory for 100 000 rows. Measured after a forced garbage collection:

| Test | JS heap used |
|---|---|
| 100 000 narrow rows, real server (`e2e/editor-backend.spec.ts`) | about 10 MB |
| 100 000 narrow rows, harness (`e2e/editor.spec.ts`) | about 27 MB |
| 1 000 rows of three 30 000-character "€" texts, real server at `max_result_mb = 200` | about 70 MB (about 220 MB without the byte bound) |
| the same in the harness | about 87 MB (about 200 MB without the byte bound) |

The tests fail above 300 MB of heap, and above 100 MB (narrow) or 140 MB (wide) used, so the wide-row tests fail
when the byte bound is removed. Fewer than 120 rows are in the DOM. `src/views/editor/rowCache.test.ts` checks the
row and byte bounds, the fetch sizes and the retries.

**Accessibility.** Editor tabs are a list of buttons (`aria-current`) with their close buttons, the result tabs a
WAI-ARIA tablist (arrows, Home, End). The grid is one tab stop: `role="grid"` with `aria-rowcount`/`aria-colcount`
and `aria-rowindex`/`aria-colindex` on every rendered row and cell (08 section 9); arrows, Home/End, Ctrl+Home/End,
Page Up/Down, Enter for the detail panel, Ctrl+C to copy. A status line shows the running time; a polite live region
reads out the final state once ("3 rows · 25 ms"). The editor's shortcut description is inside its shadow root,
because an `aria-describedby` id does not cross a shadow boundary; a click on a cell opens the detail panel and keeps
the focus in the grid, Enter moves it into the panel. The environment badge and the access chip ("Read-only", "Writing
locked") are in the toolbar; a PROD tab gets a red top border on the page until the shell shows the tab's connection
(TD-011, TD-033). A tab's results belong to the profile they came from: changing the tab's profile clears them, so
PROD rows never show under another environment's badge (M-1).

**Runs and connecting.** A run is registered before the connection is opened, so while it connects a second run
starts nothing, Cancel and ⌘. / Ctrl+. stop it before any query starts, and closing the tab starts none (M-2). The
status line says "Connecting…" meanwhile. Before a new connection (the first one, and again after
`studio.connection_stale`) the editor reads the profile from the server again: a profile whose environment or target
changed elsewhere since the page read it opens nothing, and the next run goes through D-02 with the new profile; an
open answer that names another environment is closed again (C-5). Durations come from the server with one decimal (`elapsed_ms`); the page
shows milliseconds with one decimal below 10 ms (C-3).

**Shared with the data viewer.** The grid reads rows through `GridRows` (`rowCache.ts`): the editor's `RowCache`, or
one page of the data viewer (`views/viewer/pageRows.ts`, STU-013 page), which sizes its pages by the same 64 MB
bound. The cell detail takes the viewer's "Load in full" (`cutAction`). The explorer's "SELECT … LIMIT 100 in the
SQL editor" hands its statement over in the page's memory (`editor/handoff.ts`); the editor opens it in a new tab on
that profile and runs nothing by itself.

## Tests

| What | Where | Runs |
|---|---|---|
| Token contrast, light and dark (text 4.5:1, focus ring, control borders and PROD frame 3:1; the dark theme redefines every colour) | `src/styles/tokens.test.ts` | `npm test` (CI) |
| axe, no serious or critical violation: every view, the PROD workspace, every dialog, every sign-in notice, en and de | `src/a11y.test.tsx` (jsdom, without colour contrast) | `npm test` (CI) |
| axe with colour contrast, light and dark, en and de (48 page states) | `e2e/a11y.spec.ts` | Playwright, Chromium (CI `e2e`) |
| Rendered contrast of the shell in light and dark | `e2e/contrast.spec.ts` | Playwright, Chromium (CI `e2e`) |
| PROD badge in header, tabs and title; red frame | `src/shell/AppShell.test.tsx`, `e2e/status.spec.ts` | CI (`frontend unit`, `e2e`) |
| Focus rules of D-01, D-02, D-06, D-08 | `src/components/dialogs/StudioDialogs.test.tsx`, `e2e/dialogs.spec.ts` (modality, inert page, focus trap in a real browser); `src/components/ui/Dialog.test.tsx` (initial focus with any action ID, U-2) | CI (`frontend unit`, `e2e`) |
| Session ended (S-1), heartbeat stops | `src/App.test.tsx`, `e2e/status.spec.ts` | CI (`frontend unit`, `e2e`) |
| Languages complete | `src/i18n/i18n.test.ts` | CI |
| Keyboard (STU-052): shortcut matching and display per platform, the palette's commands and filter, the palette (combobox, arrows, Enter, Escape, focus return, Tab, no match), the help, F6 order, no page shortcut while a dialog is open | `src/keyboard/*.test.ts(x)` | `npm test` (CI) |
| Keyboard only in Chromium: palette to Settings, F6 through every area with a visible ring, help and back; the palette is modal; axe for palette and help in light/dark, en/de | `e2e/keyboard.spec.ts` | Playwright, Chromium (CI `e2e`) |
| Accessibility audit (STU-053): target size, reflow at 320 px, text spacing, focus visible, reduced motion, landmarks | `e2e/a11y-audit.spec.ts`; report in `docs/architecture/accessibility.md` | Playwright, Chromium (CI `e2e`) |
| The built wheel in a real `tabrion-studio`: sign-in, no CSP violation, view path reload, axe light and dark | `e2e/backend.spec.ts` | Playwright, Chromium (CI `e2e`) |
| SQL editor (STU-012): statements and placeholders, parameter values, drafts, cell text, the event stream and its fallback, the row cache bound with 100 000 rows, the page with a fake engine (run, selection, parameters, script, cancel, D-02, D-06, Alt+T), the palette's editor commands, axe in jsdom | `src/views/editor/*.test.ts(x)`, `src/api/queries.test.ts`, `src/keyboard/*.test.ts`, `src/a11y.test.tsx` | `npm test` (CI) |
| SQL editor in Chromium with the harness's fake engine: run, grid keyboard, detail panel, parameters, script and messages, cancel (Ctrl+. and Escape), refused write, load more, tabs and drafts, D-06, D-02, run/cancel/close while connecting, PROD results cleared on a profile change, Tab out of the editor and its description, 100 000 rows and wide rows under the memory limit, axe light/dark x en/de | `e2e/editor.spec.ts`, `e2e/a11y.spec.ts` | Playwright, Chromium (CI `e2e`) |
| SQL editor in a real `tabrion-studio` with SQLite: no CSP violation, parameters, grid, detail, axe light/dark, a refused write, cancel of an endless query within a second (durations not zero), 100 000 rows and wide rows at `max_result_mb = 200` under the memory limit | `e2e/editor-backend.spec.ts` | Playwright, Chromium (CI `e2e`, not yet in the required list: TD-037) |
| ER diagram (STU-023): the scene and its 150-table layout under 2 s, the SVG and PNG exports, the view; in Chromium axe (light/dark, en/de), the measured layout of 150 tables, both downloads without a remote reference, the keyboard path; on a real server under the CSP | `src/views/explorer/er/*.test.ts(x)`, `e2e/er-diagram.spec.ts`, `e2e/backend.spec.ts` (`docs/architecture/explorer.md`) | CI (`frontend unit`, `e2e`) |
| Data viewer (STU-013 page): the explorer's Data tab with the harness's fake viewer and against a real server | `src/views/viewer/*.test.ts(x)`, `src/api/viewer.test.ts`, `e2e/viewer.spec.ts`, `e2e/viewer-backend.spec.ts`; details in `docs/architecture/viewer.md` | `npm test`, Playwright, Chromium (CI `e2e`) |
| Connection lost (TD-034): the fetch watch, the page's record of lost connections, the banner and "Reconnect" (D-02 again for PROD), the views following the new connection; in Chromium with the harness (the check and a request find the database gone, keyboard, axe light/dark, en/de, the editor) and a real server (a connection closed on the server, DEV and PROD; with a throwaway PostgreSQL the database stops answering) | `src/connectionWatch.test.ts`, `src/connectionLoss.test.ts`, `src/shell/ConnectionLost.test.tsx`, `src/views/editor/EditorView.test.tsx`, `e2e/connection-lost.spec.ts`, `e2e/connection-lost-backend.spec.ts` | `npm test`, Playwright, Chromium (CI `e2e`) |
| No request to another host (QA-9, SQ-08: no CDN font, telemetry, external image or script), in every view, dialog and notice of the harness and in the real server | `e2e/network.spec.ts`, `e2e/backend.spec.ts` (`foreignRequests` in `e2e/support.ts` records every request of the browser context, popups and workers included, and every WebSocket) | Playwright, Chromium (CI `e2e`) |

The Playwright tests run against the Vite development server and the e2e harness `frontend/harness.html`
(`src/harness/main.tsx`), which renders the real app with a fake local server and a workspace, so states that the
backend cannot produce yet (a PROD connection, the dialogs) are covered. `vite build` builds only `index.html`, so the
harness never reaches the wheel (the packaging test checks this). The CI job `e2e` (review U-3) runs every suite
except the screenshots in Chromium on each pull request, `e2e/backend.spec.ts` against the built wheel, and fails if
one of them is skipped. The platforms smoke, Firefox and WebKit belong to STU-050; they need CI changes and are
open (`docs/TODO.md`, TD-007). Local runs:
`docs/development/building.md`.
