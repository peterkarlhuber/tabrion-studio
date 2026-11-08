# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Project skeleton: Python package `tabrion_studio` (hatchling, ruff, mypy strict, import-linter) and the React 19 /
  TypeScript 5.9 / Vite 8 frontend in `frontend/` (eslint, prettier, strict `tsc`, vitest) with a placeholder page
  (STU-001).
- `tabrion-studio` command and process model: loopback-only server on a free port, one instance per project folder
  (a second start reuses it), idle exit after 30 minutes, `--stop`, clean shutdown on Ctrl+C and SIGTERM (STU-002).
- CI (`ci.yml`, `ci-ok.yml` with the commit status `ci-ok`, `nightly.yml`) on Linux, macOS and Windows; the wheel
  build hook that requires the built frontend; a wheel smoke test without Node (STU-018).
- License policy check with negative probes and CycloneDX SBOMs for the Python runtime and the frontend (STU-031).
- Access protection (STU-003, `docs/architecture/access-protection.md`): one-time launch token in the URL fragment
  (`/launch#<token>`, 120 s, once), exchanged by the page through `POST /api/session` for an `HttpOnly;
  SameSite=Strict` session cookie (`tabrion_studio_session_<port>`) and a CSRF value kept in `sessionStorage` and sent
  as `X-Tabrion-CSRF`; `tabrion-studio --print-url` prints a one-time sign-in link. Every request passes the access
  middleware: Host allowlist `127.0.0.1:<port>` on every route, no forwarded headers, Origin and fetch-metadata
  checks, JSON-only API requests, session plus CSRF header on every API route except health and the launch exchange.
  Every answer carries a strict Content-Security-Policy, `Cache-Control: no-store`, `nosniff`, `no-referrer`,
  `X-Frame-Options: DENY` and same-origin opener and resource policies (STU-T-001..006, -014, -025, -026).

- App shell, design system and languages (STU-005, `docs/architecture/frontend.md`): header, activity bar, status
  bar and the views Start, Connections, Settings and Not found; `--tb-` design tokens with a complete light and dark
  theme (system default, selectable) and compact or normal density; components copied from axentra-studio with a
  provenance note; German and English (i18next), with a completeness test. Contrast test of the tokens in both themes
  and axe without serious or critical violations (jsdom in CI; Chromium with colour contrast in `frontend/e2e/`).
- Status displays and dialogs (STU-051): environment badge (DEV, TEST, PROD) in the header, on every tab and in the
  window title (`[PROD] name – Tabrion Studio`) with a red frame for PROD; access mode, transaction and connection
  displays; the dialogs D-01, D-02, D-06 and D-08 with tested focus rules (initial focus on the safe option, focus
  trap, Escape cancels, focus returns to the opener).
- Brand assets (`assets/branding/`): the Tabrion Studio lockup, marks and favicons derived from Peter Karlhuber's logo
  master with the shared brand kit (no redraw, no CSS filters), the social preview, and `TRADEMARKS.md` (the names and
  logos are not MIT). The committed masters carry no C2PA or eXIf metadata; a test proves the pixels unchanged.
- The command prints a hint when the default browser on Linux is a snap or a Flatpak, which cannot open the sign-in
  file in a hidden state directory (TD-002, review S-4 of `stu-003-review.md`).
- CI job `e2e` (mandatory check): the Playwright suites in Chromium on every pull request, namely axe with color
  contrast in light and dark and in English and German, rendered contrast, dialog focus rules, status displays, and
  `e2e/backend.spec.ts` against the built wheel in a real `tabrion-studio` under the CSP. It installs the Chromium build
  of the locked Playwright (`npm run e2e:install`) and fails if a required suite was skipped (review U-3 of
  `stu-005-051-review.md`). `npm run e2e` runs the suites locally.

- Problem JSON and logging (STU-004, `docs/architecture/errors-and-logging.md`): every error answer is
  `application/problem+json` with `code`, `kind`, `status` and fixed `title`, `detail` and `hint` texts from one
  catalog, including the access refusals, 404, 405 and validation errors (which no longer echo the input); an
  unexpected exception gives `studio.internal_error` with a log ID and is logged once, redacted. Logs go to a rotating
  JSON-lines file per project instance (5 x 5 MB, 0600 in a 0700 folder, symlinks refused) and WARNING and above to
  stderr; `--log-dir` and `TABRION_STUDIO_LOG_DIR` choose the folder (default: the per-user log directory, or
  `STATE_DIR/logs` with `--state-dir`). A request log names the method, route template, status, duration and problem
  code, never the query string. Uvicorn's loggers use the same handlers; its access log stays off. Canary tests
  (STU-T-008, -009) prove that secrets, parameter values, server `DETAIL` texts and tokens reach neither an answer nor
  a log line.

### Security

- STU-004: access refusals no longer repeat request data in their text (`access.forwarded_not_allowed` named the
  forwarded headers); the process's control token, launch tokens, session IDs and CSRF values are registered for log
  redaction when they are created.

- Review of PR #4 (`stu-003-review.md`), S-1: when the server no longer knows the tab's session (a restart, or a
  program on another local port that replaced the session cookie), the page stops the heartbeat and says "Your session
  has ended. Run `tabrion-studio` again to open Studio." The cookie-tossing residual risk is documented.

- Review of PR #1 (`stu-002-018-review.md`): the control client makes the server prove the control token (HMAC over a
  client nonce) before it sends the token, and opens only launch URLs of that instance; stale info files are removed
  when the lock is taken and at the start of a shutdown (F-01). SIGHUP and Windows CTRL_CLOSE stop Studio cleanly
  (F-04). Only authenticated control requests and same-origin heartbeats (`GET /api/heartbeat`) reset the idle timer
  (F-06). Unsafe state directories are refused: group- or world-writable or foreign-owned on POSIX (F-10), outside
  the profile without a private ACL or behind a junction on Windows (F-07). `[::1]` on the same port is held (F-08).
  ci-ok reports NOT OK for unreviewed changes to `.github/workflows/**` or `.github/scripts/**` (label
  `workflow-change-reviewed`, F-03) and retries the status post, failing loudly (F-11).
- STU-003 binding requirements of the review (`stu-002-018-review.md`, condition C-3): the browser is opened with the
  `file://` path of a 0600 redirect file and never with the token URL, so no process argv shows the token; the file
  is deleted when the token is used or expires, at shutdown and at the next start (F-02). The Host allowlist covers
  every route, and the control endpoints refuse requests with `Origin` or `Sec-Fetch-*` (F-05). `localhost` and
  `[::1]` are not answered (F-08). A session cookie alone opens nothing without its CSRF value (F-09). Requests with
  a valid session count as activity; the heartbeat needs the session (F-06, R-5).
- Re-review recommendations: the control client sends the proof request and the request with the token over one
  keep-alive connection and never reconnects in between (R-4). The workflow-change gate guards every file under
  `.github/` except the issue and pull request templates (R-3), accepts the label `workflow-change-reviewed` only if
  it was set after the latest push (R-2), and the `ci-ok` status description names its run; the merging party checks
  the ci-ok run link before every merge (R-1).

### Changed

- The view paths `/connections` and `/settings` answer `HEAD` as well as `GET`, like `/` and the static files
  (review U-4).
- The dialog's initial focus escapes the action ID in its selector (`CSS.escape`), so an ID with a quote or a
  backslash focuses its button instead of throwing (review U-2).
- `docs/development/building.md` explains the Chromium build that the locked Playwright expects and how to use a
  preinstalled browser through `PLAYWRIGHT_CHROMIUM_EXECUTABLE` (review U-5).

- `/api/heartbeat` and `/api/openapi.json` need a session. The control endpoint `POST /api/control/launch` takes
  `{"redirect": true|false}` and answers the launch URL and the redirect file. The Vite development proxy presents the
  backend's own Host and Origin.
