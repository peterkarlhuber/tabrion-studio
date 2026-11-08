# Accessibility audit (STU-053)

Target: WCAG 2.2 level AA (planning document 08 section 9). This page is the audit report for the shell as it is
now: start page, connection manager with its assistant and dialogs, settings, not found, the status displays of a
PROD workspace, the dialogs D-01, D-02, D-06 and D-08, the command palette and the shortcut help (STU-052) and the
sign-in notices. The explorer, the SQL editor, the result
grid and the data viewer do not exist yet. Their criteria are listed under *Waits for later packages* and are audited
again with STU-050 and STU-053's final report.

**Audit state:** 2026-11-08, master after #12, #14 and #16, Chromium 1243 (the CI build is 1248). No open AA violation in
the audited states; four findings were fixed in the STU-053 pull request (below).

## How it is checked

| Check | Tool | Where | Runs |
|---|---|---|---|
| axe (WCAG 2.0 to 2.2 A/AA and best practices), colour contrast included, light and dark, en and de | `@axe-core/playwright` | `frontend/e2e/a11y.spec.ts` | CI `e2e` |
| axe without colour contrast for every view, dialog and notice | `axe-core` in jsdom | `frontend/src/a11y.test.tsx` | CI `frontend unit` |
| Token contrast: text 4.5:1, focus ring, control borders and PROD frame 3:1, light and dark | own calculation | `frontend/src/styles/tokens.test.ts`, `e2e/contrast.spec.ts` | CI |
| Target size, reflow at 320 px (en and de), text spacing, focus visible and not obscured, reduced motion, landmarks | Playwright | `frontend/e2e/a11y-audit.spec.ts` | CI `e2e` |
| Dialog focus rules (initial focus on the safe option, trap, Escape, focus return, inert page) | Playwright, Vitest | `e2e/dialogs.spec.ts`, `src/components/dialogs/StudioDialogs.test.tsx` | CI |
| Screen-reader sample (VoiceOver on macOS, NVDA on Windows) | manual | this page | open (see below) |

## Checklist (WCAG 2.2 AA, the criteria that apply to the shell)

| Criterion | Result | Evidence |
|---|---|---|
| 1.1.1 Non-text content | pass | logo and mark have a name or `alt=""` next to the product name; icon buttons and navigation links are named (`aria-label`); icons are `aria-hidden`; axe |
| 1.3.1 Info and relationships | pass | landmarks (banner, named navigation, main, named status bar), one `h1` per view, `fieldset`/`legend` for choices, labels for every field, `role="tablist"`/`tab`/`tabpanel`; `a11y-audit.spec.ts`, axe |
| 1.3.2 Meaningful sequence, 1.3.3 Sensory characteristics | pass | DOM order is the visual order; no instruction relies on shape or position |
| 1.3.4 Orientation, 1.3.5 Identify input purpose | pass | no orientation lock; the fields are connection data, not personal data (no `autocomplete` purpose applies) |
| 1.4.1 Use of colour | pass | the environment badge always carries text (DEV, TEST, PROD); the connection dot has a text status; `status.spec.ts` |
| 1.4.3 Contrast (minimum), 1.4.11 Non-text contrast | pass | `tokens.test.ts`, `contrast.spec.ts`, axe with colour contrast in both themes |
| 1.4.4 Resize text, 1.4.10 Reflow | pass after F-1 | rem-based sizes; at 320 CSS px no view, dialog or the PROD header scrolls sideways, in English and German; `a11y-audit.spec.ts` |
| 1.4.12 Text spacing | pass | with the spacing of the criterion no button, link, label or heading cuts its text and the page does not scroll sideways; `a11y-audit.spec.ts` |
| 1.4.13 Content on hover or focus | pass after F-4 | the only hover content is the CSS tooltip of icon buttons and navigation links: it stays while hovered or focused, the pointer can move onto it, and Escape hides it; `a11y-audit.spec.ts` |
| 2.1.1 Keyboard, 2.1.2 No keyboard trap | pass | every control is a native button, link or input; dialogs trap Tab inside and release it on close; `dialogs.spec.ts` |
| 2.1.4 Character key shortcuts | pass | no single-key shortcut |
| 2.2.1 Timing adjustable | pass | the session ends after 30 minutes without a tab, which the user sets with `--idle-timeout`; an open tab keeps it alive (heartbeat) |
| 2.3.1 Three flashes | pass | nothing flashes |
| 2.3.3 Animation from interactions (AAA, required by 08 section 9) | pass | `prefers-reduced-motion: reduce` sets every duration to 0; `a11y-audit.spec.ts` |
| 2.4.1 Bypass blocks | pass | skip link to `#main` on every view |
| 2.4.2 Page titled | pass | `Page – Tabrion Studio`, `[PROD] name – Tabrion Studio` with a connection; `AppShell.test.tsx` |
| 2.4.3 Focus order | pass | DOM order; dialogs move the focus in and back |
| 2.4.4 Link purpose | pass | links have their target in their text or name |
| 2.4.6 Headings and labels | pass | descriptive headings and labels in both languages |
| 2.4.7 Focus visible, 2.4.11 Focus not obscured (minimum) | pass | one 2 px ring with at least 3:1; every Tab stop of the PROD shell shows it and is in view; `a11y-audit.spec.ts` |
| 2.5.3 Label in name | pass | visible labels are part of the accessible names (the badge reads "Environment: PROD") |
| 2.5.7 Dragging movements | n/a | no dragging |
| 2.5.8 Target size (minimum) | pass after F-2 | every target is at least 24 x 24 px, except links in a sentence and radio buttons or checkboxes inside their 32 px high label; `a11y-audit.spec.ts` |
| 3.1.1 Language of page, 3.1.2 Language of parts | pass | `<html lang>` follows the UI language; each language choice carries its own `lang` |
| 3.2.1 On focus, 3.2.2 On input | pass | focus changes nothing; choosing a language or theme applies it in place without moving the focus |
| 3.3.1 Error identification, 3.3.2 Labels or instructions, 3.3.3 Error suggestion | pass | invalid fields get `aria-invalid` and a message tied with `aria-describedby`; problems say what to change; `ConnectionsView.test.tsx` |
| 3.3.7 Redundant entry, 3.3.8 Accessible authentication | pass | the assistant keeps entered values between its steps; sign-in is a one-time link, no cognitive test |
| 4.1.2 Name, role, value | pass | native controls; the custom roles (tabs) have their states; axe |
| 4.1.3 Status messages | pass | the server status, the save results and the connection manager's messages are `role="status"` or in a polite live region |

## Findings fixed in the STU-053 pull request

| ID | Criterion | Finding | Fix |
|---|---|---|---|
| F-1 | 1.4.10 Reflow | At 320 px a PROD workspace scrolled the whole page sideways by 78 px: the header did not wrap its status displays, and the absolutely positioned screen-reader texts in the tabs escaped the scrolling tab list (`.tb-sr-only` without a positioned ancestor). The logo mark was squeezed to 11 px. | The header wraps and grows (`minmax(48px, auto)` row), the brand does not shrink, the tab list is the containing block of its screen-reader texts, and the connection switcher is at most as wide as the header |
| F-2 | 2.5.8 Target size | "Show full value" and "and N more" in D-01 were 20 px high | at least 24 px high |
| F-4 | 1.4.13 Content on hover or focus | The tooltips of the activity bar and the icon buttons could not be dismissed without moving the pointer or the focus, and vanished when the pointer moved onto them (a gap and `pointer-events: none`) | Escape hides them until the pointer moves or the focus changes (`src/lib/tooltips.ts`); the tooltip touches its trigger, so it can be hovered |
| F-3 | 1.4.10 Reflow | The connection switcher (a long profile name) was wider than the header at 320 px | `max-width: min(22rem, 100%)`, the name is cut with "…" (the full name stays in the accessible name) |

## Waits for later packages

Recorded in `docs/TODO.md` (TD-022):

- **Screen-reader sample** (08 section 9: VoiceOver, NVDA): needs a macOS and a Windows desktop with a person; the
  container has neither. Scope: sign in, D-01, the connection manager and its assistant, settings, the status bar
  announcements.
- **Explorer tree** (WAI-ARIA tree, arrow keys; STU-010), **result grid** (`role="grid"`, `aria-rowcount`,
  `aria-rowindex` with virtual rows; STU-012, STU-013), **live region for the query status** ("412 rows in 38 ms";
  STU-011, STU-012), **SQL editor** (CodeMirror's accessibility, Escape out of the editor; STU-012), **ER diagram**
  table alternative (STU-023, 1.0).
- **Firefox and WebKit** runs of the axe and audit suites (QA-9, TD-007): a CI change.
- **The audit report of the MVP** (the STU-053 acceptance "report without open WCAG 2.2 AA violations") is final only
  after STU-050, over every MVP flow. This page is its first part.
