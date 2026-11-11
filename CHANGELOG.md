# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added
- TABRION QA-21 (AC-7): `scripts/check_tabrion_api.py` is now the check shared with axentra, orkexa and
  nodestro-runtime. Besides private Tabrion modules it refuses `_` attributes on Tabrion names, `getattr` and
  `import_module` with a private constant and star imports. Every used name must be in its module's `__all__` in the
  installed Tabrion (a used name without an installed Tabrion fails), and `--surface` compares with Tabrion's frozen
  API snapshot and refuses experimental (`X`) names. `--export` writes the used names to `docs/api-consumers.md` (in
  the docs under Development), and `tests/architecture/test_tabrion_api_export.py` fails when that file is stale or
  missing.
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
- Keyboard and command palette (STU-052, first part; `docs/architecture/frontend.md`, "Keyboard"): ⌘K / Ctrl+K opens
  a command palette (go to a view, theme, density, language, help) with the combobox pattern; ⌘/ / Ctrl+/ shows the
  keyboard shortcuts; F6 and Shift+F6 move between header, activity bar, tabs, main area and status bar. Shortcuts are
  shown with the keys of the platform (⌘ on macOS, Ctrl or Strg elsewhere); the header has buttons for both dialogs.
- Language, theme and density are saved in Studio's `settings.toml` as soon as they are chosen, and applied from it
  when the page opens. They were browser preferences only, which a browser keeps per port, so they were lost at every
  start (TD-021). `settings.toml` accepts `density = "compact" | "normal"`. A file with issues or one written by hand
  is not changed from the general settings; the choice then stays in the browser and the view says so. The command
  palette's theme, density and language commands are saved the same way.
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

- Configuration, profiles and project trust (STU-006, `docs/architecture/configuration.md`): TOML schema version 1 for
  the project's `.tabrion/studio.toml`, the user's `profiles.toml` (must be 0600) and `settings.toml`, with key-path
  issues that never repeat a value; `--config-dir` and `TABRION_STUDIO_CONFIG_DIR`. `GET /api/project`,
  `POST /api/project/trust`, `GET /api/profiles` and `GET /api/settings`. Project profiles are used only after the
  user confirmed the file's exact bytes in D-01; a changed file needs a new confirmation and shows the diff. D-01 now
  lists each connection, host, file reference, password variable and unknown key as its own item with control and
  bidi characters escaped, long values and lists capped, and the real totals (binding requirement U-1 of
  `stu-005-051-review.md`). The German D-01 text says "definiert" (review wording nit).

- Secret management (STU-007, `docs/architecture/secrets.md`): `keyring:` references use the OS keyring (service
  `tabrion-studio`, one entry per profile and project folder) with no plain-text fallback (the `fail`, `null` and
  `keyrings.alt` backends are refused); `env:NAME` reads the process environment; `file:PATH` is read with the checks
  of review C-3 at read time (no `~` expansion, containment, no link on the path, owner, 0600, one link, no FIFO,
  4 KB); a password can be entered for the current session only. `PUT` and `DELETE /api/profiles/{id}/secret` store
  and remove an entered password (204, never echoed), `GET /api/secrets` says whether a keyring is available, and
  `GET /api/profiles` shows only `secret: {source, has_secret}`. Every resolved or entered secret is redacted from the
  logs. `file:` values are never URL-decoded or Unicode-normalized.
- Re-review of PR #9 (`stu-conditions-rereview.md`): R-1, the link walk refuses a step to `/net/<host>` or another
  network form before it `lstat`s it (a relative link that climbs to `/net` triggered the macOS automount); R-3, a
  leading `\??\` is refused as text.
- Connection manager and server settings (STU-008, `docs/architecture/configuration.md`): the Connections view lists
  the project's and the user's profiles with a filter and a detail panel; the three-step "New connection" assistant
  (database, password source, name and the required environment) creates and edits the user's profiles; a profile
  can be deleted (Cancel is the default) and a password entered for the keyring or the session. The Settings view
  saves the query limits and the SQL logging opt-in. `POST`, `PUT` and `DELETE /api/profiles` and `PUT /api/settings`
  write only Studio's own `profiles.toml` and `settings.toml`: into a private folder, atomically with 0600 and
  `O_NOFOLLOW`, bound to the digest the page read (`studio.config_stale`), never over a file with issues, and over a
  hand-written file only after a confirmation. Refused forms name fixed keys and codes, never values. The project's
  file is never written. Testing and connecting wait for STU-009.
- Review of PR #10 (`stu-007-review.md`): C-1, a stored keyring or session secret is bound to a fingerprint of the
  connection target (engine, host, port, database, user, path), so a project profile that keeps its name but points
  to another host no longer gets the saved password, and D-01 lists it under "Saved password not used"; I-1, secrets
  of 4 to 7 characters are redacted too; I-3, one leading BOM is removed from a `file:` secret; I-2, the FIFO test
  fails instead of hanging, a deterministic link-swap test, and test servers use the null keyring backend.

- Review of PR #11 (`stu-008-review.md`): the effective TLS mode is part of the saved-password fingerprint (a TLS
  downgrade asks for the password again); a lone surrogate in a field is a form issue and a file that would not read
  back is a 422, not a 500; a keyring that refuses to delete a saved password is logged and reported; a save reads
  the file again just before it replaces it; the e2e server uses the null keyring; an `env:` password reads "set in
  the environment".

- Network log check (QA-9, SQ-08): `e2e/network.spec.ts` visits every view, dialog and notice of the e2e harness, and
  `e2e/backend.spec.ts` the real server, and fails if the page requests any other host (no CDN font, telemetry,
  external image or script). It listens on the browser context, so popups, new pages and workers count too.

### Security

- D-01 shows where each PostgreSQL connection's password goes (TD-010 T-5, T-4 rest; `stu-007-review.md` L-1): the
  secret's source, `host:port` and the TLS mode, marked for a host outside loopback, TLS weaker than `verify-full`, a
  variable that looks like another service's credential and a password file outside the project. Projects trusted
  before this change show as changed once and must be confirmed again.
- Conditions of the reviews of PR #7 (`stu-004-review.md`) and PR #8 (`stu-006-review.md`):
  - L-1 (C-1): exception text that reaches a log record as its *message* is removed too. Records of the `asyncio`
    logger keep only a known fixed first line (no `repr` of a task, future or callback with its exception or
    arguments), a formatted traceback in a message (Starlette's lifespan failure, which Uvicorn logs as text) is cut,
    Studio's lifespan logs its own exception with frames only (`studio.lifespan_failed`), and an exception passed as a
    `%s`/`%r` argument formats as its type. L-2: the scrub also removes `db_password=`, `PGPASSWORD=`, `*_token=`,
    `sslpassword=`, `auth_secret:`, `privatekey=`, `Authorization: Basic ...` and URL passwords that contain `@` or
    `/`; every pattern runs in linear time. L-3: a traceback keeps the first and last 20 frames of each exception and
    is capped at 32 KB per record.
  - T-1 (C-1): a trusted project file stays trusted only while the review equals the one the user confirmed, so a
    database folder that later becomes a link out of the project makes it `changed`; `ProjectConfig.database_file`
    re-checks containment when a database file is opened (for STU-009).
  - T-2 (C-2): UNC (`//host`, `\\host`), device (`\\?\`, `\\.\`), drive-relative (`C:foo`), `file://host` and
    `/net/<host>` paths in a profile file are refused as `config.network_path`, and containment is decided as text
    before any file-system access; no value from a project file is passed to `Path.resolve()` (on Windows that opened
    an SMB connection to the named host).
  - T-3: deeply nested TOML is `config.too_deep`, a damaged trust record means untrusted, and the user's own profiles
    still load when the project file cannot be read (no more HTTP 500). T-4: only `localhost`, a loopback IP address
    and a socket folder default to `sslmode = "prefer"`; `127.evil.example` gets `verify-full`.
  - I-2: D-01 shows "Unknown keys removed" and says when it shows a file that changed after it was opened
    (`studio.trust_stale`); an invalid project file gets a notice with its issues (English and German).

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
