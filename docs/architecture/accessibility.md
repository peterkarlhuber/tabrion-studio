# Accessibility audit (STU-053)

Target: WCAG 2.2 level AA (planning document 08 section 9). This page is the audit report for the shell as it is
now: start page, connection manager with its assistant and dialogs, settings, not found, the status displays of a
PROD workspace, the dialogs D-01, D-02, D-06 and D-08, the command palette and the shortcut help (STU-052) and the
sign-in notices. A second pass audited the work pages that landed after it: the SQL editor with its result grid,
cell detail and export dialog (STU-012, TD-043), the explorer with its tree, the Data tab (data viewer, STU-013), the
DDL and Policies tabs and Count rows (STU-022), the diagnostics report (STU-014) and the connection manager's D-02
confirmation. STU-053's final report follows STU-050 (below).

**Audit state:** 2026-11-13, master 50fd816 (after #55 and #57), Chromium 1243 (Chrome for Testing 153; the CI build is
1248). No open AA violation in the audited states. The first pass fixed F-1 to F-5, the second F-6 (below).

## How it is checked

| Check | Tool | Where | Runs |
|---|---|---|---|
| axe (WCAG 2.0 to 2.2 A/AA and best practices), colour contrast included, light and dark, en and de | `@axe-core/playwright` | `frontend/e2e/a11y.spec.ts` | CI `e2e` |
| axe without colour contrast for every view, dialog and notice | `axe-core` in jsdom | `frontend/src/a11y.test.tsx` | CI `frontend unit` |
| Token contrast: text 4.5:1, focus ring, control borders and PROD frame 3:1, light and dark | own calculation | `frontend/src/styles/tokens.test.ts`, `e2e/contrast.spec.ts` | CI |
| axe as above for the work pages: editor and grid, cell detail, export dialog, explorer tree and tabs, Data tab with filters and cell detail, DDL, Policies, Count rows, diagnostics, D-02 | `@axe-core/playwright` | `e2e/editor.spec.ts`, `export.spec.ts`, `explorer.spec.ts`, `explorer-definitions.spec.ts`, `viewer.spec.ts`, `diagnostics.spec.ts`, `connections.spec.ts` | CI `e2e` |
| Target size, reflow at 320 px (en and de), text spacing, focus visible and not obscured, reduced motion, landmarks | Playwright | `frontend/e2e/a11y-audit.spec.ts` | CI `e2e` |
| For each work page state: target size, reflow at 320 px and at 200 % (640 px), every Tab stop with its ring and in view (an open modal keeps every stop), text spacing, reduced motion; each action's result in a live region | Playwright | `frontend/e2e/a11y-audit.spec.ts` (`WORK_PAGES`) | CI `e2e` |
| Dialog focus rules (initial focus on the safe option, trap, Escape, focus return, inert page) | Playwright, Vitest | `e2e/dialogs.spec.ts`, `src/components/dialogs/StudioDialogs.test.tsx` | CI |
| Screen-reader sample (VoiceOver on macOS, NVDA on Windows) | manual | this page | open (see below) |

## Checklist (WCAG 2.2 AA, the criteria that apply to the shell and the work pages)

| Criterion | Result | Evidence |
|---|---|---|
| 1.1.1 Non-text content | pass | logo and mark have a name or `alt=""` next to the product name; icon buttons and navigation links are named (`aria-label`); icons are `aria-hidden`; axe |
| 1.3.1 Info and relationships | pass | landmarks (banner, named navigation, main, named status bar), one `h1` per view, `fieldset`/`legend` for choices, labels for every field, `role="tablist"`/`tab`/`tabpanel`; the result grid is a WAI-ARIA grid (`aria-rowcount`, `aria-colcount`, `aria-rowindex`, `aria-colindex`, `aria-readonly`) and the explorer a WAI-ARIA tree (`aria-level`, `aria-setsize`, `aria-posinset`, `aria-expanded`); `a11y-audit.spec.ts`, `ExplorerView.test.tsx`, axe |
| 1.3.2 Meaningful sequence, 1.3.3 Sensory characteristics | pass | DOM order is the visual order; no instruction relies on shape or position |
| 1.3.4 Orientation, 1.3.5 Identify input purpose | pass | no orientation lock; the fields are connection data, not personal data (no `autocomplete` purpose applies) |
| 1.4.1 Use of colour | pass | the environment badge always carries text (DEV, TEST, PROD); the connection dot has a text status; `status.spec.ts` |
| 1.4.3 Contrast (minimum), 1.4.11 Non-text contrast | pass | `tokens.test.ts`, `contrast.spec.ts`, axe with colour contrast in both themes |
| 1.4.4 Resize text, 1.4.10 Reflow | pass after F-1 | rem-based sizes; at 320 CSS px no view, dialog or the PROD header scrolls sideways, in English and German, and no work page at 320 px or at 200 % zoom (640 px); the grid and the DDL scroll inside their own frame, which is allowed for two-dimensional content; `a11y-audit.spec.ts` |
| 1.4.12 Text spacing | pass | with the spacing of the criterion no button, link, label, heading, tab or column header cuts its text and the page does not scroll sideways; a grid cell cut with "…" shows its whole value in the cell detail (D-1); `a11y-audit.spec.ts` |
| 1.4.13 Content on hover or focus | pass after F-4 | the only hover content is the CSS tooltip of icon buttons and navigation links: it stays while hovered or focused, the pointer can move onto it, and Escape hides it; a tooltip shown only for the keyboard focus takes no clicks; `a11y-audit.spec.ts` (activity bar and header buttons) |
| 2.1.1 Keyboard, 2.1.2 No keyboard trap | pass | every control is a native button, link or input; dialogs trap Tab inside and release it on close; the SQL editor lets Tab move on (no tab trap), the grid is one Tab stop with arrow keys, Home, End, Page Up/Down and Enter for the cell detail, the tree takes the arrow keys; `dialogs.spec.ts`, `editor.spec.ts`, `explorer.spec.ts`, `a11y-audit.spec.ts` |
| 2.1.4 Character key shortcuts | pass | no single-key shortcut |
| 2.2.1 Timing adjustable | pass | the session ends after 30 minutes without a tab, which the user sets with `--idle-timeout`; an open tab keeps it alive (heartbeat) |
| 2.3.1 Three flashes | pass | nothing flashes |
| 2.3.3 Animation from interactions (AAA, required by 08 section 9) | pass | `prefers-reduced-motion: reduce` sets every duration to 0; no element of a work page animates or moves with it; `a11y-audit.spec.ts` |
| 2.4.1 Bypass blocks | pass | skip link to `#main` on every view |
| 2.4.2 Page titled | pass | `Page – Tabrion Studio`, `[PROD] name – Tabrion Studio` with a connection; `AppShell.test.tsx` |
| 2.4.3 Focus order | pass | DOM order; dialogs move the focus in and back; after "Go to …" in the command palette the focus moves to the new page's main area (I-2 of `stu-052-td021-review.md`; `Keyboard.test.tsx`, `keyboard.spec.ts`); an open cell detail, export dialog or D-02 keeps every Tab stop inside it; `a11y-audit.spec.ts` |
| 2.4.4 Link purpose | pass | links have their target in their text or name |
| 2.4.6 Headings and labels | pass | descriptive headings and labels in both languages |
| 2.4.7 Focus visible, 2.4.11 Focus not obscured (minimum) | pass | one 2 px ring with at least 3:1; every Tab stop of the PROD shell and of each work page shows it and is in view (the SQL editor lives in a shadow root, and its frame draws the ring with `:focus-within`; the grid marks its active cell); `a11y-audit.spec.ts` |
| 2.5.3 Label in name | pass | visible labels are part of the accessible names (the badge reads "Environment: PROD") |
| 2.5.7 Dragging movements | n/a | no dragging |
| 2.5.8 Target size (minimum) | pass after F-2 and F-6 | every target is at least 24 x 24 px, except links in a sentence and radio buttons or checkboxes inside their 32 px high label; `a11y-audit.spec.ts` (every work page state too) |
| 3.1.1 Language of page, 3.1.2 Language of parts | pass | `<html lang>` follows the UI language; each language choice carries its own `lang` |
| 3.2.1 On focus, 3.2.2 On input | pass | focus changes nothing; choosing a language or theme applies it in place without moving the focus |
| 3.3.1 Error identification, 3.3.2 Labels or instructions, 3.3.3 Error suggestion | pass | invalid fields get `aria-invalid` and a message tied with `aria-describedby`; problems say what to change; `ConnectionsView.test.tsx` |
| 3.3.7 Redundant entry, 3.3.8 Accessible authentication | pass | the assistant keeps entered values between its steps; sign-in is a one-time link, no cognitive test |
| 4.1.2 Name, role, value | pass after F-5 | native controls; the custom roles (tabs, grid, tree) have their states; the SQL editor's text is named after its tab and described by its shortcuts; axe; below 720 px the logo link keeps its name |
| 4.1.3 Status messages | pass | the server status, the save results and the connection manager's messages are `role="status"` or in a polite live region; so are a query's result ("3 rows"), an export's progress and end, a page of the data viewer ("Rows 1–100"), the row count and a diagnosis; errors are `role="alert"`; `a11y-audit.spec.ts` ("the result of each action is said in a live region") |

## Findings fixed in the STU-053 pull requests

F-1 to F-5 come from the first pass (the shell), F-6 from the second (the work pages).

| ID | Criterion | Finding | Fix |
|---|---|---|---|
| F-1 | 1.4.10 Reflow | At 320 px a PROD workspace scrolled the whole page sideways by 78 px: the header did not wrap its status displays, and the absolutely positioned screen-reader texts in the tabs escaped the scrolling tab list (`.tb-sr-only` without a positioned ancestor). The logo mark was squeezed to 11 px. | The header wraps and grows (`minmax(48px, auto)` row), so no part of it is squeezed (the mark keeps 24 px), the tab list is the containing block of its screen-reader texts, and the connection switcher is at most as wide as the header |
| F-2 | 2.5.8 Target size | "Show full value" and "and N more" in D-01 were 20 px high | at least 24 px high |
| F-4 | 1.4.13 Content on hover or focus | The tooltips of the activity bar and the icon buttons could not be dismissed without moving the pointer or the focus, and vanished when the pointer moved onto them (a gap and `pointer-events: none`) | Escape hides them until the pointer moves or the focus changes (`src/lib/tooltips.ts`); the tooltip touches its trigger, so it can be hovered. It takes the pointer only while its trigger is hovered: a tooltip shown for the keyboard focus lets a click beside the link reach the content below (review L-1) |
| F-3 | 1.4.10 Reflow | The connection switcher (a long profile name) was wider than the header at 320 px | `max-width: min(22rem, 100%)`; the name is cut with "…" and shown in full in its `title` and the accessible name, and the state ("Connected · 12 ms") is never cut, so it cannot run out of the button (review L-4). Tab titles cut with "…" have a `title` too; the audit fails on any text cut with "…" that no title shows |
| F-5 | 4.1.2 Name, role, value | Below 720 px the header hid the "Tabrion Studio" text with `display: none`; the mark's `alt` is empty, so the logo link had no accessible name (found while pinning F-1, review L-2) | the text is hidden visually only, like `.tb-sr-only` |
| F-6 | 2.5.8 Target size | The radio buttons and checkboxes of the export dialog (format, columns, options) sat in labels 21 px high, below the 32 px the F-2 exception assumes | the labels take the control height (`--tb-control-height`), and the whole label is the target |

## Decisions

| ID | Decision |
|---|---|
| D-1 | A cell of the result grid (SQL editor, Data tab) is cut with "…" without a `title`. The audit's "no text cut with '…' without a title" rule skips grid cells: a click or Enter on the cell opens the cell detail with the whole value (and "Load in full" for a value the server cut), and the cell's text is its preview, not a shortened name. A `title` on every cell would read each value twice in a screen reader. |

## Waits for later packages

Recorded in `docs/TODO.md` (TD-022):

- **Screen-reader sample** (08 section 9: VoiceOver, NVDA): needs a macOS and a Windows desktop with a person; the
  container has neither. Scope: sign in, D-01, the connection manager and its assistant, settings, the status bar
  announcements.
- **Done in the second pass:** the explorer tree, the result grid in the editor and the data viewer, the live
  regions of the query status, the export, the data viewer, the row count and a diagnosis, the SQL editor (Tab moves
  on) and D-02 in the connection manager.
- **ER diagram** table alternative (STU-023, 1.0) and the **STU-016** pages when they land.
- **Firefox and WebKit** runs of the axe and audit suites (QA-9, TD-007): a CI change.
- **The audit report of the MVP** (the STU-053 acceptance "report without open WCAG 2.2 AA violations") is final only
  after STU-050, over every MVP flow, and with the screen-reader sample. This page covers every MVP page that exists now.
