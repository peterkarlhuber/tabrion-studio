# App shell and design system (STU-005, STU-051)

The frontend in `frontend/` is a React 19 / TypeScript / Vite single-page app that the wheel serves from
`tabrion_studio/static/`. This page describes the shell, the design system, the languages, the status displays and
the dialogs, and how they are tested. The connection manager and the server settings are STU-008
(`docs/architecture/configuration.md`); the explorer and the SQL editor arrive later. Studio has **no write UI for
databases** (the Data Editor, STU-026, stays locked behind G-S2); the only files it writes are its own profiles and
settings.

## Shell

```
┌ header: mark · "Tabrion Studio" · connection switcher · [PROD] · Writing locked · No transaction ┐
├──┬────────────────────────────────────────────────────────────────────────────────────────────────┤
│⌂ │ [Query 1 PROD] [parts-dev DEV] [parts-pg TEST]      ← tabs, only with a workspace               │
│🗄│ main view: Start · Connections · Migrations · Diagnostics · Settings · Not found                 │
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
  the keyboard shortcuts, activity bar (Start, Connections, Migrations, Diagnostics, Settings), tabs, main area,
  status bar (server version or "does not answer", language).
- `src/router.ts`: a minimal history router for `/`, `/connections`, `/migrations`, `/diagnostics` and `/settings`.
  The backend serves `index.html` for exactly these paths (`CLIENT_ROUTES` in `app.py`; a test keeps both lists
  equal); every other path stays 404.
- `src/workspace.ts`: the active connection, its environment and the open tabs. Nothing fills it until the connection
  registry and manager arrive (STU-008, STU-009); the shell shows "No connection".

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
| Next / previous area | F6 / ⇧F6 | F6 / Shift+F6 | header, activity bar, tabs, main area, status bar, wrapping around |

- **Command palette** (`CommandPalette.tsx`): a modal native `<dialog>` like the other dialogs, with the WAI-ARIA
  combobox pattern: the focus stays in the search, `aria-activedescendant` names the active command, ↑ ↓ Home End
  move, Enter or a click runs, Escape closes, Tab stays in the search, and the focus returns to the opener. A status
  region announces the number of matches. The commands (`commands.ts`) go to the views of the activity bar (Start,
  Connections, Migrations, Diagnostics, Settings), change the theme, the density and the language, and open the help; the
  current page, theme, density and language are marked. The filter matches every word, without case and accents. No command writes to a database; theme, density and language are
  saved to `settings.toml` like in the settings view (`src/appearance.ts`, TD-021). The explorer, the SQL
  editor and the history add their commands when they arrive.
- **Areas** (`regions.ts`): each area carries `data-region`; F6 focuses its current item (the active navigation link,
  the selected tab), else its first control, else the area itself, which then shows the focus ring
  (`data-region-focus`, WCAG 2.4.7).
- While any modal dialog is open, the page shortcuts do nothing: the dialog owns the keyboard.
- The SQL editor's shortcuts (run, run script, cancel, editor tabs, save, explorer filter, history) arrive with
  STU-011 and STU-012, and the help lists them then (`docs/TODO.md`, TD-020).

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
| No request to another host (QA-9, SQ-08: no CDN font, telemetry, external image or script), in every view, dialog and notice of the harness and in the real server | `e2e/network.spec.ts`, `e2e/backend.spec.ts` (`foreignRequests` in `e2e/support.ts` records every request of the browser context, popups and workers included, and every WebSocket) | Playwright, Chromium (CI `e2e`) |

The Playwright tests run against the Vite development server and the e2e harness `frontend/harness.html`
(`src/harness/main.tsx`), which renders the real app with a fake local server and a workspace, so states that the
backend cannot produce yet (a PROD connection, the dialogs) are covered. `vite build` builds only `index.html`, so the
harness never reaches the wheel (the packaging test checks this). The CI job `e2e` (review U-3) runs every suite
except the screenshots in Chromium on each pull request, `e2e/backend.spec.ts` against the built wheel, and fails if
one of them is skipped. The platforms smoke, Firefox and WebKit belong to STU-050; they need CI changes and are
open (`docs/TODO.md`, TD-007). Local runs:
`docs/development/building.md`.
