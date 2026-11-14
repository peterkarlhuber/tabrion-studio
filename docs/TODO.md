# TODO: tabrion-studio

> Open work for this repository: release blockers, owner actions, the open Studio work packages, temporary
> measures and technical debt. The program backlog is the source of truth for scope and acceptance criteria
> (planning documents 03, 05, 07, 08 and 09; program status in `tabrion/docs/program/STATUS.md`, K-12). "TABRION
> <WP-ID>" names a work package of that backlog.
>
> **Last full review:** 2026-11-08 against branch `claude/project-thread-rdo6g3-stu-conditions` (the conditions of
> `stu-004-review.md` and `stu-006-review.md`), before that against branch `claude/project-thread-rdo6g3-stu006` (TABRION STU-006:
> configuration, profiles and project trust, U-1) and `claude/project-thread-rdo6g3-stu004` (TABRION STU-004: Problem
> JSON and logging, PR #7); before that against branch `claude/project-thread-rdo6g3` (the review recommendations U-1..U-5
> of `stu-005-051-review.md`; PR #5: STU-005 and STU-051 with S-1 and S-4 of `stu-003-review.md`; PR #4: STU-003 and
> R-1..R-7 of `stu-002-018-review.md`; PR #1: STU-001, STU-002, STU-018, STU-031).
>
> CR-DBX planning (2026-11-13, branch `claude/project-thread-rdo6g3-crdbx`): the owner accepted change request CR-DBX
> (planning document 15) as planning, not as the start of implementation; section
> [CR-DBX](#cr-dbx-studio-11-to-13-planned-not-started) added, the "Later" note on the Schema Designer corrected (W-2),
> [STU-ADR-0005](adr/0005-crdbx-write-and-admin-gates.md) records the per-area locks.

## Summary

| Item | Priority | Status |
|---|---|---|
| TS-001 add the tabrion dependency with STU-009 | P1 | Done (PR #15: `tabrion[postgres,sqlalchemy]>=0.1,<0.2`, tabrion v0.1.0 `d5821fa` pinned by full SHA in `constraints/tabrion-integration.txt`, no PyPI; installing with the constraints file needs pip 26.1 or later; moved to tabrion 0.2.0 `52353da` and `>=0.2,<0.3` in the TD-013 PR) |
| OA-001 make `ci-ok` a required check (optional); check the ci-ok run link before every merge (stays) | P4 | Open (owner, optional) |
| OA-002 independent review of the process model | P1 | Accepted (re-review at 988bc9d) |
| OA-003 create the label `workflow-change-reviewed` | P1 | Open (owner) |
| OA-004 independent review of STU-003 (access protection) | P0 | Accepted (`stu-003-review.md`, PR #4 merged) |
| OA-005 independent review of STU-005/STU-051 (app shell, dialogs, S-1, S-4) | P1 | Accepted (`stu-005-051-review.md`, PR #5 merged) |
| OA-006 independent workflow-change review of the `e2e` CI job (U-3), then the label `workflow-change-reviewed` | P1 | Accepted (`stu-follow-up-6-review.md`, PR #6 merged) |
| OA-007 independent review of STU-004 (Problem JSON, log redaction) | P1 | Accepted (`stu-004-review.md`; C-1 done in PR #9, `stu-conditions-rereview.md`) |
| OA-009 independent review of STU-007 (secret management, C-3) | P0 | Accepted with conditions (`stu-007-review.md`, PR #10 merged); C-1 (M-1) and I-1..I-3 done in the STU-008 PR, I-4, I-5 and L-1 are TD-010 |
| OA-010 independent review of STU-008 (Studio writes its own profiles and settings) | P1 | Accepted (`stu-008-review.md`, PR #11 merged); L-1..L-3 and I-1..I-4 done in the STU-008 follow-up PR |
| OA-008 independent review of STU-006 (project trust, D-01, U-1) | P1 | Accepted (`stu-006-review.md`; C-1, C-2 done in PR #9, `stu-conditions-rereview.md`; C-3 done in the STU-007 PR) |
| STU-007 binding requirement from the review (C-3: the reader of `file:` secrets) | P0 | Done (STU-007 PR; `read_secret_file`) |
| STU-006 binding requirement from the review (U-1: trust dialog D-01 must not mislead) | P1 | Done (STU-006 PR; closes with OA-008) |
| STU-003 binding requirements from the review (F-02, F-05, F-08, F-09) | P0 | Done (PR #4) |
| TD-001 layer contract for `api`/`services` | P3 | Open |
| CR-DBX: Studio 1.1 Data Editor, 1.2 Schema Designer, 1.3 Database Manager (planning document 15) | P3 | Blocked: planned, not started; needs Studio 1.0 (G-R), the repository migration and the development-start approval G-CX; each area then stays locked until G-S2, G-S3 or G-S4 ([section](#cr-dbx-studio-11-to-13-planned-not-started)) |
| TD-002 sandboxed browsers cannot open the redirect file (review S-4) | P2 | In progress (hint done; fix open); a G-R precondition (owner's G-S1 decisions of 2026-11-13, decision 6) |
| TD-003 Windows: DACL check inside the profile (review R-6); the check on a real Windows machine of the refused device names and of OneDrive and other reparse-point folders (STU-020 D-4) | P3 | Open; accepted risk until G-R, both a G-R precondition (owner's G-S1 decisions of 2026-11-13, decision 7) |
| TD-004 fork pull requests in the workflow-change gate (review R-7) | P3 | Open |
| TD-005 spent launch URL in the browser history (review S-2) | P3 | Accepted risk (documented; confirmed in the owner's G-S1 decisions of 2026-11-13, decision 5) |
| TD-006 who may set the label `workflow-change-reviewed` (review S-3) | P3 | Open |
| TD-007 development proxy and real-browser launch checks (review S-5) | P3 | Open (documented; STU-050); the Firefox and WebKit launch check is a G-R precondition (owner's G-S1 decisions of 2026-11-13, decision 6) |
| TD-008 add a windows-latest CI job for the T-2 checks (review C-2) | P1 | Done (PR #76: job `T-2 path checks (windows-latest)`, required; workflow-change review accepted, `pr76-f6-lock-workflow-review.md`; G-S1 decision 7) |
| TD-009 STU-004 review: remaining Low and Info items (L-2 rest, I-1..I-5, re-review R-2) | P3 | Open |
| TD-010 STU-006 and STU-007 reviews: remaining Low and Info items (T-1 rest, I-1, I-3..I-5; STU-007 I-4, I-5; R-1, T-4 rest, T-5 and STU-007 L-1 done) | P2 | Open |
| TD-011 STU-008 parts that wait for a later package or a `.github` change (file dialog, `openapi in sync` job, renaming, favorites; the connection test, Connect and URL paste are done in the STU-008 connect PR #42) | P2 | Open |
| TD-012 STU-009 integration: the PostgreSQL CI job, the move to tabrion v0.1.0, the binding for STU-011 | P1 | Done (PR #15; the STU-011 binding stays a rule) |
| TD-013 STU-011 query engine on tabrion 0.1: the move to tabrion 0.2 (`Transaction.stream`/`cancel`, `tabrion.Column`) and its follow-ups | P1 | Partly done (TD-013 PR: pin on tabrion 0.2.0 `52353da`, `Database.inspector()`, `config.url_character`; stream, cancel, `Column`, `check` and the session reset stay as shims, see the entry) |
| TD-020 STU-052 parts that wait for the SQL editor and the explorer (editor shortcuts, keyboard-only E2E of every main flow; `keyboard.spec.ts` is a required e2e suite since the CI e2e-list PR) | P2 | Done (F6 over the panes and the keyboard-only E2E of every main flow in the STU-052 rest PR; format and history in the STU-020 page PR) |
| TD-021 language, theme and density were lost at every start (browser storage per port); now saved in `settings.toml` | P2 | Open (#17 merged as `061db9d`; I-5, I-6 left) |
| TD-022 STU-053 parts that wait for later packages or a person (screen-reader sample, ER diagram, STU-016 pages, Firefox/WebKit, the final MVP report) | P2 | Open |
| TD-023 user guide parts that wait for later packages (connecting, explorer, editor, data viewer, diagnostics, migrations; the clean-install test and the tag of STU-017) | P2 | Partly done (user-guide PR #27; the docs checklist and the clean-install test in the STU-017 docs PR; the 1.0 pages checked and pipx/uv installed on Linux in the STU-028 PR; open: the tag `v0.1.0` and the install section naming it, pipx/uv and the guide's commands on macOS and Windows, I-3 in `guide/connections.md`) |
| TD-024 STU-015 migration worker on Windows: children of trusted project code (job object and pipe closing done; `backend test (windows-latest)` green on `23fd8a6`, run 43201304911) | P2 | Done (PR #29) |
| TD-025 STU-015/STU-024 page: the Levuvo view shows state, version, open steps and the reason a table is unreadable, not the SQL preview or the files folder; formats the page does not know are only counted | P3 | Open (STU-015 page PR #32; C-1 of `stu-015-ui-review.md` done, delta items L-6..L-9 and I-6 done) |
| TD-026 STU-021 export: infinite and out-of-range dates, Unicode file names, the problem code of data-modifying CTEs (F-7, F-9, F-10) | P3 | Open (STU-021 done in PR #33) |
| TD-027 STU-009 registry: two concurrent opens of one profile can both connect (found in the #32 page delta review, F-2) | P3 | Open (2026-11-12) |
| TD-028 `backend test (windows-latest)`: `tests/security/test_explorer.py` took 2 to 12.5 minutes (a fixture committed 505 times; one transaction now) | P2 | Done (PR #36) |
| TD-035 STU-022 DDL: what pg_dump writes and Studio's DDL does not (owner, privileges, tablespace, access method, functions, types and domains, foreign-table column options, security labels; PostgreSQL 18 forms) | P3 | Open (STU-022 PR #38) |
| TD-039 STU-022 review: the queries made when a PostgreSQL connection is opened (SQLAlchemy dialect first-connect setup, the driver's type lookups) ran under the session's `search_path` | P1 | Done (PR #45) |
| TD-034 STU-008 connect: what the page does not do yet (no workspace overview after Connect, the status bar's role and database, a connection switcher; the "Connection lost" banner with "Reconnect" is done) | P2 | Partly done (TD-034 PR: the banner) |
| TD-038 STU-008 connect: limits of the D-02 gate and of the error states (`stu-008-connect-review.md` L-5, L-7). D-02 was enforced only in the page, from its loaded list, so a profile changed to `prod` in another tab or on disk after the list loaded was connected without D-02 (the connection manager too). Done in the D-02 server PR: `POST /api/connections` and `POST /api/connection-tests` take `confirmed_environment` and refuse a PROD profile that was not confirmed for PROD with 409 `studio.prod_confirmation_required` (missing means not confirmed); the manager reads the profile again and asks D-02, the migration status and diagnostics views read their list again (`docs/architecture/connections.md`, "D-02 on the server"). The explorer (#43) and the SQL editor (#41) send it too and read their list again after a 409; `confirmed_environment` is a required argument of the page's `openConnection` and `testConnection`, so a new caller cannot leave it out (#48 review C-2). For a SQLite user profile the server resolves the file from the same profile read that passed the check (#48 review C-1). The diagnostics view uses the shared D-02 gate since TD-036 (#47). The 08 section 4.2 states "sign-in refused" (SQLSTATE class 28) and "TLS certificate invalid" are reached only by mocks: against a real cluster, a wrong password, an unknown user or database and a TLS mismatch all come back as `db.connection` without `sqlstate`, so the page shows the one generic text (host, port, user, password, TLS mode); the fix is in tabrion's classification at connect time. The delta review's leftovers are fixed in the STU-008 follow-up PR: L-10 (the header could stay "connecting…" after a slow connect and a newer one that failed), a refused URL's `password` query parameter staying in the field (and `sslpassword`, from the #44 review), the other order of L-10 (the older request answers first, then the newer one fails; #44 review R-1), a connection that was open before two connects that both failed (#44 delta review R-2), tests for the two surviving mutants (the busy click guard, the open-list read after a failed connect), and the cause of a `backend.spec.ts` failure under load (the switcher did not appear after Connect): a list of the open connections read while the connect ran, and answered after it, cleared the header; the manager also read that list again on every header change. A list now clears only what the header showed when it was read (`headerVersion`), and the session's functions keep their identity | P2 | Open (STU-008 connect PR #42) |
| TD-041 STU-010 page: parts that wait for other packages (open in the SQL editor and `SELECT … LIMIT 100` with #41, the DDL and policies tab with #38/STU-022 (in the STU-022 page PR #54), the data tab with the STU-013 page, the route `/c/:conn/t/:schema/:name`, a virtualized tree, the reference-app E2E in CI: its SQLite part runs and is required since the CI e2e-list PR, its PostgreSQL part not yet) | P2 | Open (STU-010 page PR #43) |
| TD-042 STU-013 page (data viewer): what it leaves for later ("Open as SQL" with the sort and filters, the row estimate in the status line, sorting from the column header, typed filter values, the byte bound of a first page on SQLite columns whose declared type is not enforced; the viewer suites are required in CI since the CI e2e-list PR) | P2 | Open (STU-013 page PR #52) |
| TD-030 moderate npm advisory in development tools only (`minimatch` through eslint; QA-8) | P3 | Open (watch) |
| TD-031 build with the hashed backend pins in CI, and keep the two pins in step (QA-8); install and test against a hashed runtime lock (security review F-6) | P2 | Done (PR #76: `constraints/runtime-lock.txt`, `scripts/runtime_lock.py`; every CI install and build uses the hashed pins; workflow-change review accepted). The nightly non-blocking job `newest dependencies` without the lock (G-S1 decision 2): done in PR #90, its workflow-change review pending. Open: refreshing the lock (by hand, about monthly), the optional weekly lowest-direct run, the lock install on macOS and Windows only with Python 3.13 (review L-7, accepted) |
| TD-032 STU-014 page deviations from 08 section 4.9: the route `/diagnostics` (like `/migrations`), English details and remedies (blocked STU-054 for the `STU-DX-*` texts, E-S3; resolved by PR #85), no progress, no documentation link, no "OK" rows | P2 | Partly done (STU-014 page PR #39; `stu-014-ui-review.md` C-3; the English server text part done in the diagnostics-texts PR: Studio's `STU-DX-*` and `STU-BK-*` details and remedies in en/de from `params`); open: the route, progress, documentation link, "OK" rows |
| TD-033 STU-012 SQL editor MVP: what it leaves for later (the page/server splitter differences, error positions, column resizing, the shell's workspace tabs, the heartbeat stream; drafts in the state database done) | P2 | Open, usability only (STU-012 draft PR #41; review `stu-012-review.md` C-1..C-5 and L-1..L-8 done; drafts part done in PR #77, review F-5; G-S1 decision 1: the rest is ordinary P2 usability work, no security item) |
| TD-036 STU-025 parts outside the backend: the "Download report" button and the backup hints in the diagnostics view (STU-014 page, merged in #39), the report from the command line, the user-guide section | P2 | Done (TD-036 page PR: download, backup hints, page route `replace=True`, user guide; the diagnostics-texts PR: `tabrion-studio diagnose`) |
| TD-037 STU-012: `editor.spec.ts` and `editor-backend.spec.ts` in the e2e job's list of suites that must pass | P2 | Done (CI e2e-list PR: every spec in `frontend/e2e` must pass without a skip, `screenshots.spec.ts` excepted) |
| TD-043 STU-021 page: the export dialog; what it leaves to the data viewer (STU-013) and later (the file is held in the browser until it is saved, at most 1 GiB, no server-side size estimate; `export.spec.ts` is required in CI since the CI e2e-list PR) | P2 | Open (export dialog PR) |
| TD-047 STU-050: the e2e suites on Firefox and WebKit (08 section 11, 09 STU-050; a `.github/` change) | P1 | Done (PR #59 merged: jobs `e2e (firefox)` and `e2e (webkit)`; PR #63: both green, no product bug, the tests fixed; PR #67: both jobs required, after its workflow-change review) |
| TD-044 Joint regression JR-1: `tabrion-studio` ended with a `BrokenPipeError` traceback and a non-zero exit status (1, or 120 with buffered output) when its stdout reader was gone before a clean stop (the last "stopped" line); now that line is dropped and the exit stays 0, other output errors still raise | P3 | Done (PR #57; `joint-regression-2026-11-12.md`) |
| TD-045 SQL editor under load and cancel feedback (e2e-load PR #56). Failures with 3 workers at load average 14 on 4 CPUs: `editor-backend.spec.ts` "cancel stops an endless query within a second" failed because a cancel during the start request was lost (a product bug, fixed in #56; covered by Vitest, `editor.spec.ts` with the harness option `startDelay`, and `editor-backend.spec.ts` with the start answer held back on the real server); its bound is taken in the page (key to "Canceled" under 1 s, STU-T-020). Not reproduced: `editor.spec.ts` "wide rows stay within the memory limit too" passed in every run, also with the renderer slowed 4x and 8x: 20 row fetches, each chunk once, the heap 85 MB after GC (bound 140 MB), the slowest scroll step 2.8 s at 8x (bound 5 s per step); no bound was changed. Open: L-2 of `editor-cancel-start-review.md`, a cancel request that fails (`POST /api/queries/{id}/cancel` not 202) says nothing and the page keeps showing "Running …" (a notice or one retry); the M-2 test "while connecting …" assumes its keys land within the 800 ms connect delay and fails at load average 28 | P3 | Open |
| TD-046 STU-050 UX acceptance: what the MVP does not meet yet (start page project chooser, the copyable migration command, status bar and switcher facts, QA-S1, the editor's completion list, the explorer's layout; `docs/architecture/ux-acceptance.md` B-1..B-7) | P2 | Done (decided for 1.0 in the STU-054 PR: B-2, B-3, B-6 done; B-1, B-4, B-5, B-7 moved to TD-060) |
| TD-048 STU-016 review F-1..F-3 (read-only path, SQLite functions, PostgreSQL side-effect functions): the re-review, the owner's choices and the limits that stay | P0 | Done (PR #66; independent re-review accepted at `2a8a229`); G-S1 decisions 8 (side-effect functions the database defines: accepted residual risk, the role is the boundary), 9 (hexadecimal and underscore number forms stay refused) and 10 (a report to the PostgreSQL security team only as a confidential draft for the owner) |
| TD-054 STU-016 review F-7 (T-16): the code-trust digest covers the project modules under the `prepend_sys_path` folders (versioned digest, earlier trust renewed; caps, links and Windows paths fail closed; the worker runs only hashed modules, also those reached through Studio's environment, review C-1; the trust dialog lists them) | P2 | Done (PR #75; review accepted with condition C-1, delta review with condition C-2; C-1, C-2 and N-1 fixed; re-check accepted at `d33f89d`); the T-16 residual (trusted code runs with the user's rights) accepted, G-S1 decision 3 |
| TD-052 STU-023 ER diagram: what it leaves for later (the route `/c/:conn/schema` with the workspace, a diagram across schemas, column-to-column lines and cardinality marks, moving boxes, schemas of more than 1000 tables, the diagram in the reference-app E2E, Firefox and WebKit runs of the new specs only in CI) | P3 | Open (STU-023 PR) |
| TD-050 Joint regression JR-N2: the real-server suite `e2e/backend.spec.ts` left a `studio-e2e-*` directory in the temp folder after every run (its `afterAll` only stopped the server) | P3 | Done (PR #68) |
| TD-055 Test order: the canary `test_a_failing_lifespan_of_any_app_logs_no_traceback_text` failed after `tests/unit/test_server.py`, whose default `uvicorn.Config` left Uvicorn's own logging config (stderr handler, `propagate=False`) in the process; Studio's redaction now takes over Uvicorn's and asyncio's loggers, and every test restores their state; left: records of child loggers after a later `dictConfig` (not hit by `serve()`) | P2 | Done (PR #78) |
| TD-056 tabrion's `DbErrorRedactionFilter` strips `DETAIL:` (with `HINT:`, `CONTEXT:`) only where it starts a line: a one-line message with `DETAIL:` in the middle and no database exception object keeps its values (owner: tabrion; PR #78 review F-9) | P2 | Done for Studio (TD-013 PR: tabrion 0.2.0 has the fix, tabrion TB-144; Studio pins it and `test_a_database_detail_in_the_middle_of_a_line_is_redacted` checks it); G-R precondition GR-5 met |
| TD-057 Export slot race: a client that started the next export on a connection right after reading the previous one to its end could get `studio.export_busy` (the worker freed the slot only after its last log line) | P2 | Done (PR #80) |
| TD-058 STU-053 audit walk in WebKit: "explorer Policies tab: every Tab stop ... is in view" failed once in CI (PR #80, run 43405250149) with the selected tree row out of view; WebKit scrolls a newly focused element into view from a zero-delay timer, and the walk measured in the same task. Test race, no product bug; the walk now measures one task later | P2 | Done (PR #81) |
| TD-061 STU-028 release SBOM: the macOS and Windows license records and `scripts/release_bom.py --strict` as a CI step (a `.github/` change); the inherited SPDX parenthesis gap of `check_licenses.py` (review L-6); a license record of a package the lock does not pin is ignored by `release_bom.py` (re-check R-1) | P2 | Done in PR #90 (its workflow-change review pending): the nightly jobs `license records (<system>)` and `release SBOM (strict)`, in nightly.yml and not in a required job (reason in the entry); L-6 and R-1 fixed with tests. Open: the first nightly run's result, attaching the files to a release (G-5, after G-R) |
| TD-062 JR-N1: an e2e test of the PROD case of "Reconnect" after a failed reconnect (the behavior is accepted, G-S1 decision 4) | P3 | Done (PR #89: `e2e/connection-lost-prod-retry.spec.ts`) |
| TD-064 DC-TS-1 dependency confusion (owner decision of 2026-11-13 22:37Z: no PyPI placeholder for `tabrion` or `tabrion-studio`; every install hardened): `check-installed` verifies tabrion's source, a pip floor before the `-c` installs, the bare install in `docs/development/building.md`; and a negative CI job `install (poisoned index)` with the shared `scripts/check_tabrion_source.py` of nodestro-studio (checksummed copy): zero index requests for `tabrion` and the pinned commit installed | P2 | Partly done (DC-TS-1 PR: the source check with tests, the pip floor with a workflow test, the docs, and the job `install (poisoned index)` with the copy of nodestro-studio's script at `bf51022`, SHA-256 pinned; listed in `.github/ci-manifest.json`, reported by ci-ok). `direct_url.json` checks only detect an accidental index install; the zero-request assertion is the proof. Open: the workflow-change review; refresh the copy and its SHA-256 if nodestro-studio's script changes before its PR merges |
| TD-060 STU-054 UX acceptance 1.0: what stays open (the owner decisions on the "403" wording, D-07, tabrion's English finding texts and the start page are done, 2026-11-13; TD-032's dependency resolved by PR #85; open: TD-046 B-4, B-5 (QA-S1 for 1.0), B-7; the history as its own view) | P2 | Open (STU-054 PR #84; owner decisions 2026-11-13) |
| TD-053 A SQLite function denied by the authorizer surfaces as `db.unknown` ("not authorized") instead of `db.permission`, a vaguer message (re-review of STU-016, not a security issue) | P3 | Open |
| TD-063 Security assessment B-1 and B-2: the statement check's function denylist in every PostgreSQL call form, the pg_surgery and pg_visibility functions, dblink connections closed at check-in; what stays open (newer servers' functions, a diagnostics hint) | P1 | In progress (PR #92): B-1 and B-2 done; open: the PostgreSQL 17/18 candidates and the diagnostics hint (see the entry) |
| TD-065 Security assessment A-01: a fresh random loopback address `127.a.b.c` per start, so no later Studio reuses an earlier origin (service workers, storage); the macOS fallback to `127.0.0.1` and the `--port` advice; what stays open (real Windows and macOS browsers) | P1 | In progress (A-01 PR): the fix, the residual-risk note in STU-ADR-0002 and the regression tests are done; open: the real-device checks (see the entry) |
| TD-066 STU-055 vector data view: what it leaves for later (pgvector tests outside the CI PostgreSQL job, the sqlite-vec install status, `halfvec`/`sparsevec` on a pgvector 0.7+ server, an e2e test, other layouts) | P3 | Open (STU-055 PR) |
| STU-016 independent review F-4, F-8, F-9: request body caps enforced while the body streams in (`security/body.py`), every control request except GET/HEAD of the proof path needs the control token and no dot segments in `/api/*` paths, `security-review-prep.md` updated with the review's outcome and the pending owner decisions (F-5/TD-033, F-6/TD-031, F-7/T-16) | P1 | Done (PR #65); independent re-review accepted at `2a8a229` |

Plus the open Studio work packages of M6 and the cross-repository checks in the tables below.

**Release blockers:** nothing is released from this repository before G-R (every public version needs the owner's
approval, plus a human security review for Studio, E-QA2). There is no release workflow yet.

Priorities: **P0** blocks a gate, a merge or a release; **P1** on the critical path of M6; **P2** in scope, schedule
soon; **P3** minor gap or debt. Statuses: `Open`, `In progress` (PR linked), `Blocked` (names the blocker),
`Needs decision`, `Accepted risk`.

## Temporary measures

### TS-001 · Add the tabrion dependency with the first API use (TABRION STU-009, RC-8)

- **Priority:** P1 High · **Status:** Done (STU-009, PR #15: tabrion v0.1.0 pinned by full SHA) · **Refs:** RC-8,
  E-VER, 05 §3.2, TABRION TAB-040
- **What:** STU-009 is the first Studio code that uses the Tabrion API, so it adds the tabrion dependency.
- **Rule (RC-8, amended by ADR 0009 in tabrion PR #20: tabrion is not published on PyPI, it is used through Git SHA
  pins):** `project.dependencies` carries only the final range, with no git or URL reference and no pre-release
  specifier. The commit is pinned by its **full SHA** in a constraints file
  (`tabrion @ git+https://github.com/peterkarlhuber/tabrion@<40 hex>`; no branch, tag or short SHA). Both are
  enforced by `tests/security/test_dependencies.py` (`test_pyproject_follows_rc8`,
  `test_pin_files_pin_only_full_commits`, `test_the_constraints_file_pins_tabrion_0_2_0`).
- **Done:** `pyproject.toml` declares `tabrion[postgres,sqlalchemy]>=0.1,<0.2`, and
  `constraints/tabrion-integration.txt` pins tabrion v0.1.0, the merge commit of tabrion PR #56
  (`d5821faa8742dea012abda3b663549c038d76543`). Every install reads the constraints file (`-c`, or
  `PIP_CONSTRAINT` for the wheel smoke test in CI); a later tabrion version moves the pin in that one file. STU-015
  (PR #29) adds the `migrations` extra: `tabrion[migrations,postgres,sqlalchemy]>=0.1,<0.2`. The TD-013 PR moves
  to tabrion 0.2.0, the untagged merge commit of tabrion PR #65 (`52353daee6cd6e37a7efc45b74061a48beb2b280`), and
  the range `tabrion[migrations,postgres,sqlalchemy]>=0.2,<0.3`.
- **Also with STU-009 (binding, L-1 of `stu-008-review.md`):** every parameter Studio passes to the driver from a
  profile (later `sslrootcert`, `require_auth`, options) must be in `schema.TARGET_FIELDS`, the secret fingerprint,
  and a connection is built from `Profile` only, never from unknown keys. Options that are the same constant for
  every connection (`services.connections.driver_options`: `prepare_threshold=0`, `passfile`) are not targets.
- **Done in STU-009 (PR #15):** URLs only through `tabrion.safe_url()`; passwords only through the configuration's
  secrets when connecting (`Configuration.resolve_entry_secret`, the same entry as the URL: L-1 of `stu-009-review.md`);
  project SQLite files only through `database_file`, every driver parameter in the fingerprint, `DB_KINDS` from
  `tabrion.errors.Kind`, `db.<kind>` problems from `classify`, `DbErrorRedactionFilter` in the log handlers
  (`docs/architecture/connections.md`).
- **Also with STU-009 (from STU-007):** show connection URLs only through `tabrion.safe_url()`, resolve passwords
  only through `Configuration.resolve_secret(id)` when connecting, and open project SQLite files only through
  `ProjectConfig.database_file` (review T-1).
- **Also with STU-009 (from STU-004):** replace the mirror `tabrion_studio.problems.DB_KINDS` with
  `tabrion.errors.Kind`, build `db.<kind>` problems from `tabrion.errors.classify` and `safe_message` only, and add
  `tabrion.errors.DbErrorRedactionFilter` to the log handlers next to Studio's `RedactionFilter`
  (`docs/architecture/errors-and-logging.md`, "Database errors").

## Owner actions

### OA-001 · Make `ci-ok` a required check (TABRION STU-018, GOV-3)

- **Priority:** P4 Optional (governance change, tabrion PR #20) · **Status:** Open (owner, optional) · **Refs:**
  CI-FAST, 05 §1 (branch protection)
- **What:** `ci-ok.yml` runs from the default branch only (`workflow_run`), so it cannot judge the pull request
  that adds it. Once it is on master it posts the commit status `ci-ok` on every pull request head SHA.
- **Next step (owner, optional):** protect `master` (no force push, no deletion, no required reviews per
  the agent flow) and add the status check `ci-ok` as required. Optionally list the CI job names as required checks
  for the first pull request.
- **Before every merge (review R-1, TABRION STU-003 PR; this rule stays binding):** the latest `ci-ok` status on the head SHA must link
  (`target_url`) to a run of the ci-ok workflow for that SHA, and the run ID at the end of its description
  (`[ci-ok run <id>]`) must be that run. A workflow in any other branch could forge the status, so this applies to
  every pull request, not only to workflow changes (`docs/development/ci.md`).

### OA-002 · Independent review of the process model (TABRION STU-002, 07 §6)

- **Priority:** P1 High · **Status:** Accepted (re-review at 988bc9d) · **Refs:** 07 §6 review model, STU-ADR-0002,
  `tabrion-planung/reviews/stu-002-018-review.md`
- **What:** the independent review of PR #1 (at 4925486) covered the lock and info files, the control token, the
  loopback bind and the ci-ok gate. It came back ACCEPTED WITH CONDITIONS; the re-review at 988bc9d ACCEPTED, with
  condition C-3 (the STU-003 binding requirements) and the recommendations R-1..R-7.
- **Done in PR #1:** F-01 (server proof before the token is sent, launch URL check, stale info file removed when the
  lock is taken and at the start of a shutdown, regression tests with a fake server), F-04 (SIGHUP, CTRL_CLOSE, info
  file removed on every exit path), F-06 (only authenticated control requests and same-origin heartbeats reset the
  idle timer), F-07 (Windows: private ACL outside the profile, junctions refused), F-08 (`[::1]` held on the same
  port), F-10 (unsafe state directory and lock file refused), F-11 (status post retried, job fails loudly) and F-03
  (workflow-change gate with the label `workflow-change-reviewed`, CODEOWNERS as a recommendation).
- **Moved to STU-003:** F-02, F-05, F-08 (Host allowlist part) and F-09; see *STU-003 binding requirements*.
- **Recommendations of the re-review (TABRION STU-003 PR #4):** R-1 (check the ci-ok run link before every merge,
  `docs/development/ci.md`, OA-001), R-2 (the label counts only if set after the latest push), R-3 (all of
  `.github/` is guarded, minus the templates) and R-4 (proof and token on one keep-alive connection) are done; R-5
  is done with STU-003 (the heartbeat needs a session, the Host allowlist covers it, browser versions are in
  `docs/architecture/access-protection.md`); R-6 and R-7 are TD-003 and TD-004.

### OA-003 · Create the label `workflow-change-reviewed` (TABRION STU-018, review F-03)

- **Priority:** P1 High · **Status:** Open (owner) · **Refs:** `docs/development/ci.md` (workflow-change gate)
- **What:** ci-ok reports NOT OK for every pull request that changes a file under `.github/` (after PR #4; before
  it: `.github/workflows/**` and `.github/scripts/**`) without this label. After PR #4 the label counts only if it
  was set after the latest push (review R-2). Only writers can set labels.
- **Next step (owner):** create the label once (Issues, Labels). Set it only after the owner or an independent
  review has read the change under `.github/`, then re-run ci-ok. PR #4 itself changes `.github/scripts/**` and
  `.github/workflows/**`, so it stays NOT OK until the label is set. Optional: turn on "require review from code owners" for
  `.github/CODEOWNERS` once a second maintainer exists (it would block every agent pull request today).

### OA-004 · Independent review of STU-003 (TABRION STU-003, 07 §6, G-S1)

- **Priority:** P0 Critical · **Status:** Accepted (PR #4 merged) · **Refs:** 07 §6 (packages with a network or credential
  surface), condition C-3 of `stu-002-018-review.md`, `docs/architecture/access-protection.md`
- **What:** an independent review agent checks the access protection against the binding requirements below and the
  threat model (T-01, T-02, T-03, T-07, T-15), including the workflow-change gate changes (R-2, R-3), before the
  merge.
- **Result:** `stu-003-review.md` (planning folder, `reviews/`): ACCEPTED, no High or Medium finding; the `.github/`
  changes were accepted as a workflow-change review. Recommendations: S-1 is done with STU-005 (on 401 the page says
  "session ended, run `tabrion-studio` again" and stops the heartbeat; the cookie-tossing residual risk is in
  `docs/architecture/access-protection.md`), S-4 is TD-002 (raised to P2, hint done), S-2, S-3 and S-5 are TD-005,
  TD-006 and TD-007.

### OA-005 · Independent review of STU-005 and STU-051 (07 §6)

- **Priority:** P1 High · **Status:** Accepted · **Refs:** `docs/architecture/frontend.md`, review S-1 and S-4,
  `tabrion-planung/reviews/stu-005-051-review.md`
- **What:** the pull request adds the app shell, the design system, de/en, the status displays and the dialogs, and
  touches the access surface in three small places: the backend serves `index.html` for the view paths
  `/connections` and `/settings` (`CLIENT_ROUTES`, same middleware and headers), the page reacts to a lost session
  (S-1), and the command runs `xdg-settings` to detect a sandboxed browser (S-4, fixed arguments, 2 s timeout, no
  shell). No `.github/` file changes, so the workflow-change label is not needed.
- **Result:** `stu-005-051-review.md`: ACCEPTED (no security regression, 0 CSP violations in a real browser,
  untrusted strings render as text). Recommendations: U-1 is the STU-006 binding requirement below; U-2 (escape the
  action ID in the dialog's initial-focus selector), U-3 (the `e2e` CI job), U-4 (HEAD on the view paths) and U-5
  (the Chromium build mismatch, `docs/development/building.md`) are done in the follow-up pull request (OA-006).

### OA-006 · Independent workflow-change review of the `e2e` job (review U-3)

- **Priority:** P1 High · **Status:** Accepted (`stu-follow-up-6-review.md`, PR #6 merged) · **Refs:**
  `docs/development/ci.md` (workflow-change gate), `stu-005-051-review.md` U-3
- **What:** the follow-up pull request adds the job `e2e` to `.github/workflows/ci.yml` and to the mandatory checks in
  `.github/ci-manifest.json`. It builds the wheel, installs it into a fresh environment, installs the Chromium build of
  the locked Playwright (`npm run e2e:install`) and runs every Playwright suite except the screenshots; a final step
  fails the job if a required suite (`a11y`, `backend`, `contrast`, `dialogs`, `status`) did not pass or was skipped.
  Actions are pinned by SHA; `ci_ok.py` is unchanged. Because the pull request changes `.github/`, ci-ok reports
  NOT OK until the label is set.
- **Result:** `stu-follow-up-6-review.md`: ACCEPTED, including the workflow-change review of the `.github/` diff.
  Info notes W-1 (`retention-days` for the failure artifact) and W-2 (the browser download is not hash-pinned) need
  no change now.

### OA-007 · Independent review of STU-004 (TABRION STU-004, 07 §6)

- **Priority:** P1 High · **Status:** Accepted with conditions (`stu-004-review.md`, PR #7 merged) · **Refs:** 07 §6
  (Studio packages with a credential surface), T-05, SQ-04, `docs/architecture/errors-and-logging.md`
- **What:** the pull request turns every error answer into Problem JSON and adds the redacting, rotating logs and the
  request log. An independent review agent checks that no answer can carry an exception message, a path, a stack
  trace or request data, that the redaction holds for every logger (Studio, Uvicorn, asyncio) and every field, and
  the file handling of the log folder (0700, 0600, symlinks). No `.github/` file changes, so the workflow-change label
  is not needed.
- **Result:** ACCEPTED WITH CONDITIONS, no High finding. **C-1 (L-1, Medium) is done in the conditions PR**
  (PR #9, merged; re-review `stu-conditions-rereview.md`: accepted): records of the `asyncio` logger keep only a known fixed
  first line (no `repr` of a task, future or callback), a formatted traceback in a message is cut at
  `Traceback (most recent call last):`, Studio's lifespan logs its exception with `exc_info` (frames only), and an
  exception passed as a `%s`/`%r` argument formats as its type. Canary tests: `test_canary.py`
  (`test_asyncio_reprs_never_reach_a_log_line`, `test_a_failing_lifespan_of_any_app_logs_no_traceback_text`,
  `test_a_failing_studio_startup_is_logged_with_frames_only`) and the real process in `test_log_process.py`
  (`test_a_failing_startup_in_a_real_process_leaks_no_exception_text`). Also done: L-2 (key suffixes, `Authorization`,
  URL passwords up to the last `@`; all patterns bounded, linear time) and L-3 (20 + 20 frames per exception, 32 KB
  per record). The rest is TD-009.

### OA-008 · Independent review of STU-006 (TABRION STU-006, 07 §6)

- **Priority:** P1 High · **Status:** Accepted with conditions (`stu-006-review.md`, PR #8 merged) · **Refs:** 07 §6
  (credential surface: `env:`/`file:` references), T-08, `docs/architecture/configuration.md`, U-1 below
- **What:** project trust fails closed (links, `file:` outside, digest bound to the bytes on disk, damaged records),
  no value is echoed from a profile file, and D-01 meets U-1a..U-1d. No `.github/` file changes.
- **Result:** ACCEPTED WITH CONDITIONS, no High finding. Done in PR #9 (merged; re-review
  `stu-conditions-rereview.md`: accepted):
  - **C-1 (T-1):** `trusted` only if the digest matches **and** the current review equals the confirmed one;
    otherwise `changed` with the diff. `ProjectConfig.database_file(name)` re-checks a trusted SQLite profile when its
    file is opened; **STU-009 must call it and open the path it returns** (`test_project_trust.py`,
    `test_a_folder_that_becomes_a_link_out_of_the_project_needs_a_new_confirmation`,
    `test_the_database_file_is_rechecked_when_it_is_opened`).
  - **C-2 (T-2):** `config.network_path` for UNC, device, drive-relative, `file://host` and `/net`, `/Network` values;
    containment decided as text (`config/locations.py`) before any file-system access; no `Path.resolve()` on a value
    from the project file (`test_config_locations.py` with `PureWindowsPath` and `PurePosixPath`;
    `test_network_paths_are_refused_before_any_file_system_access`; on Windows
    `test_windows_never_resolves_a_unc_path_from_the_project_file`, which patches `nt._getfinalpathname` and runs in
    the `windows-latest` leg of the test matrix). The dedicated Windows CI job is TD-008 (`T-2 path checks (windows-latest)`).
  - **T-3:** deeply nested TOML is `config.too_deep`; a damaged or deeply nested trust record means untrusted; an
    unexpected load error makes the project `invalid` and `/api/profiles` still lists the user's profiles.
  - **T-4:** loopback by `ipaddress` plus the literal `localhost` (and socket folders), so `127.evil.example` gets
    `verify-full`.
  - **I-2:** D-01 shows "Unknown keys removed", says so after `studio.trust_stale`, and an `invalid` file gets a
    notice with its issues (de/en).
  - **C-3** is the STU-007 binding requirement below. The other Low and Info items are TD-010.

### OA-009 · Independent review of STU-007 (TABRION STU-007, 07 §6)

- **Priority:** P0 Critical · **Status:** Accepted with conditions (`stu-007-review.md`; PR #10 merged as master
  `0af1831`) · **Refs:** 07 §6 (credential surface), T-05, SQ-04,
  03 §4.4, `docs/architecture/secrets.md`, C-3 below
- **What:** an independent review agent checks that no secret reaches an answer, a log or an error, that there is no
  plain-text fallback without a keyring, that the `file:` reader meets C-3 at read time (no `expanduser`,
  containment, `O_NOFOLLOW` per component, owner, 0600, one link, no FIFO block), and the new routes behind the
  access middleware. No `.github/` file changes.
- **Result:** no High finding. Done in the STU-008 pull request, as its first commit:
  - **C-1 (M-1):** a stored secret (keyring or session) is bound to the connection target. The key is
    `user:<name>:<fingerprint>` or `project:<project key>:<name>:<fingerprint>`, where the fingerprint is 16 hex
    digits of the SHA-256 of engine, host, port (none = 5432), database, user and path
    (`schema.target_fingerprint`). A profile that keeps its name but points elsewhere has no stored secret
    (`studio.secret_missing`, the page asks again), and D-01 lists it under "Saved password not used"
    (`diff.connections.retargeted`). Test: `test_a_stored_password_does_not_follow_a_retargeted_project_profile`
    (the reproduced attack: `prod`, host changed, one re-trust).
  - **I-1:** secrets of 4 to 7 characters are registered too, in a set that is never evicted; shorter ones are not
    (redacting every "ab" would make the log unreadable). **I-3:** one leading BOM is removed from a `file:` secret.
  - **I-2:** the FIFO test fails after 5 s instead of hanging (`SIGALRM`); a deterministic link swap between the
    checks and the open (file and folder; kills K5 and K6); `env:`, session and short values are redacted; an API
    test resolves a project `file:` secret with the project and configuration folders as roots (K16); every Studio
    process a test starts uses the null keyring backend (`tests/support/studio.NO_KEYRING`).
  - **I-4, I-5 and L-1** are TD-010.

## STU-007 binding requirement (review C-3 of `stu-006-review.md`)

**Status: done in the STU-007 pull request** (`src/tabrion_studio/config/secrets.py`, `read_secret_file`; tests in
`tests/unit/test_secrets.py`; `docs/architecture/secrets.md`). TABRION STU-007 is accepted only if it meets it. The schema accepts `file:PATH` password references,
and today `file:~/...` passes, because it is read as project-relative (`<project>/~/...`): nothing calls
`expanduser`. The checks at load time (`config/locations.py`, `_check_secret_files`) only decide whether the
reference may be listed. The reader that STU-007 adds must, **at read time**:

- **never call `expanduser`** (or `expandvars`), **never URL-decode and never Unicode-normalize** the value
  (`%5C%5Chost`, `∕∕host` and `＼＼host` stay literal names inside the project; `stu-conditions-rereview.md`): the
  path is the text of the reference, relative to the project folder (or absolute);
- **re-check containment** with `config.locations.contained` (inside the project folder or the user's configuration
  folder, as text first, then the link walk), immediately before the open, and again on the exact path that is
  opened;
- **open with `O_NOFOLLOW`** (plus `O_NONBLOCK` until `S_ISREG` is checked, so a FIFO cannot block the server) and
  check the opened file with `fstat`: a regular file, `st_nlink == 1`, **owned by the current user and mode 0600**
  (no group or other bit; POSIX), the same device and inode as the checked path; on Windows, no reparse point and a
  private DACL (TD-003);
- read at most a small fixed size, strip one trailing newline, register the value with
  `tabrion_studio.logs.register_secret` and never log, return or echo it; every refusal is a code without the path's
  content.

Most `.env` and `.git/config` files are 0644, so the 0600 rule also blocks the case of review T-5 where a project
names the user's own secret inside the project folder.

## STU-006 binding requirement (review U-1 of PR #5)

**Status: done in the STU-006 pull request** (`docs/architecture/configuration.md`, section *The trust dialog D-01*):
U-1a list items with one `<code>` each, U-1b escapes `\uXXXX` (`frontend/src/lib/visible.ts`), U-1c 64 characters and
8 entries with "Show full value" and "and N more", U-1d the diff from the trust record. Tests: `StudioDialogs.test.tsx`
(one per point, hostile names), `e2e/dialogs.spec.ts` and `e2e/a11y.spec.ts` (`dialog-d01-hostile`),
`e2e/backend.spec.ts` (real server). It closes with the review OA-008.

TABRION STU-006 wires the trust dialog D-01 (`TrustProjectDialog`, `frontend/src/components/dialogs/StudioDialogs.tsx`)
to the project file. Today it joins the connection names, hosts and file references with `", "` into one sentence or
one `<code>`. All of them come from an untrusted project file (AT4, T-08), so a name such as `parts-dev, safe
(checked)`, or one with a newline or bidi control characters, can make the list look different from what it is. No
script runs (review P-2); the risk is UI spoofing of the trust decision. STU-006 is accepted only if:

- **U-1a, one item per entry.** Names, hosts and files are rendered as lists, each entry its own list item with its
  own `<code>`; nothing is joined into one string.
- **U-1b, control and bidi characters are visible.** C0 and C1 control characters (newlines and tabs included) and the
  bidi controls U+202A..U+202E, U+2066..U+2069 (plus U+200E, U+200F, U+061C) are shown as visible escapes (for
  example `\u202E`), never interpreted; the escaped form is also what the tests assert.
- **U-1c, length cap.** Each entry is cut to a fixed maximum length with a visible marker (and the full value
  available on request), and the number of entries shown is capped with "and N more"; the dialog's counts always
  report the real totals.
- **U-1d, the T-08 diff.** When the project file changed since it was trusted (`titleChanged`), the dialog shows the
  diff that T-08 requires (added, removed and changed connections, hosts and files) instead of only the new list.
- **Tests:** a Vitest test per point with hostile names (commas, newlines, U+202E, overlong values, many entries) and
  an e2e or axe check of the dialog with them.

## STU-003 binding requirements (review of PR #1)

TABRION STU-003 is accepted only if it meets these requirements of `stu-002-018-review.md` (condition C-3). PR #4
implements each of them with tests (`docs/architecture/access-protection.md`, section *Tests*); they close with the
merge after OA-004.

- **F-02, launch token not in a process argv.** Done: the browser opens the `file://` path of a 0600 redirect file
  (`<state_dir>/studio-<key>-launch-<random>.html`, meta refresh to `http://127.0.0.1:P/launch#TOKEN`), deleted when
  the token is redeemed, after 120 s, at shutdown and at the next start. `--print-url` is the explicit opt-in. Tests:
  `tests/security/test_launch.py::test_browser_argv_contains_no_token` (real process, fake `$BROWSER` that records
  its argv), `test_browser_is_opened_with_a_redirect_file`, `test_second_start_opens_a_new_redirect_file`,
  `test_print_url_is_the_explicit_opt_in`.
- **F-05, Host and Origin checks on every route.** Done: `AccessMiddleware` applies the Host allowlist to every route,
  including `/api/control/*`, `/api/control/proof`, `/api/heartbeat` and `/api/health`; the control endpoints refuse
  any request with `Origin` or a `Sec-Fetch-*` header. Tests: `tests/security/test_access.py::
  test_host_allowlist_on_every_route`, `test_control_rejects_browser_headers`, `test_foreign_origin_is_refused`.
- **F-08, `localhost` and `::1`.** Done: the allowlist accepts `127.0.0.1:<port>` only (`localhost` and `[::1]` are
  refused whether or not the `[::1]` guard is held; a failed IPv6 bind stays non-fatal). Test:
  `tests/security/test_launch.py::test_no_localhost_url_is_printed_or_opened`.
- **F-09, cookies are not port-scoped.** Done: every request with a session needs `X-Tabrion-CSRF`, whose value lives
  only in the page's `sessionStorage`; the cookie name carries the port. Tests:
  `test_cookie_alone_is_useless_without_csrf_header`, `test_csrf_value_never_travels_in_a_cookie`,
  `test_a_tossed_cookie_does_not_hide_the_real_session`.
- **F-06, follow-up (and R-5).** Done: every request with a valid session counts as activity; the heartbeat needs the
  session, so a forged `Sec-Fetch-Site` no longer keeps Studio alive. Tests: `tests/unit/test_app.py::
  test_requests_that_do_not_count_as_activity`, `test_heartbeat_with_a_session_counts_as_activity`.

## Open work packages (M6, Studio MVP 0.1)

| WP | Title | Priority | Depends on | Note |
|---|---|---|---|---|
| TABRION STU-003 | Access protection (launch token, cookie, Host/Origin, CSP, `no-store`) | P0 | STU-002 | Done (PR #4, review accepted) |
| TABRION STU-004 | Problem JSON and logging | P1 | STU-001, TAB-038 | Done (PR #7, OA-007 accepted; C-1 done in PR #9): Problem JSON for every error, redacting rotating logs, request log without query strings; canary tests STU-T-008/-009. No tabrion dependency (RC-8): database kinds mirrored until STU-009 (TS-001). TD-055 (PR #78): redaction takes over Uvicorn's and asyncio's loggers |
| TABRION STU-005 | App shell, design system, i18n de/en | P1 | STU-001 | Done (PR #5, review accepted): shell, `--tb-` tokens, de/en, logo; axe without serious/critical (jsdom in `frontend unit`, Chromium with contrast in `e2e`) and the token contrast test light/dark |
| TABRION QA-9 | E2E framework (Playwright + axe) | P1 | STU-001 | Acceptance met in the QA-9 PR: smoke and axe green, and the network log shows 0 foreign hosts (SQ-08; `e2e/network.spec.ts` over every harness view, dialog and notice, and the real server in `backend.spec.ts`). QA-9 is closed with this PR (`stu-pr12-14-review.md`: accepted with condition C-1, the merge of master with CI green). The listener sits on the browser context (review L-1). The platforms e2e smoke (05 §3.5, `platforms.yml`), Firefox and WebKit belong to STU-050 (doc 07, doc 09); both change `.github/` and need a workflow-change review (TD-007). Review I-1 (visit the wizard steps, the settings forms after a change and the delete dialog too) is open |
| TABRION STU-006 | Configuration, profiles, project trust | P1 | STU-004 | Done (PR #8, OA-008 accepted; C-1, C-2 done in PR #9): TOML schema v1, project trust (STU-T-016/-017), D-01 wired and meeting U-1. Reads only; writing profiles and settings comes with STU-008, `log_sql` takes effect with STU-011 |
| TABRION STU-007 | Secret management | P0 | STU-006 | Done (PR #10, OA-009 accepted with conditions; C-1, I-1..I-3 done in the STU-008 PR): keyring (no plain-text fallback, refused `fail`/`null`/`keyrings.alt`), `env:`, `file:` with C-3, session secrets bound to the connection target; `PUT`/`DELETE /api/profiles/{id}/secret`, `GET /api/secrets`; fake keyring in tests |
| TABRION STU-008 | Connection manager UI and connection test | P1 | STU-005, STU-007, STU-009 | Done (PR #11, OA-010 accepted; review items in the follow-up PR; the connection test and "Connect" in the STU-008 connect PR #42): the manager (list, details, three-step assistant, delete, password), writing the user's `profiles.toml` and `settings.toml` (digest-bound, atomic, 0600, private folder), the settings view's queries and security. Connect PR: "Test connection" with the facts of 03 section 2 A (`POST /api/connection-tests` now also reads role, superuser, bypass RLS, encoding, time zone and TLS, or foreign keys, journal mode and encoding for SQLite), "Test the connection after saving" in the assistant (on the saved profile, OA-010), "Connect"/"Disconnect" with D-02 once per page session for each profile and target (id, environment, engine, host, port, database, user, path; forgotten when the profile is saved or deleted and when the project is trusted again; shared with the migration status view), D-02 also before the connection test of a PROD profile (the button, the test after saving and the retry after the password dialog; `stu-008-connect-review.md` C-2), the header, PROD frame and window title from the open connection (only the newest connect request sets it), the password dialog when a password is missing, `db.<kind>` texts with the remedy (the separate SQLSTATE class 28 and TLS texts are reached only by mocks, TD-038), the server version as people read it (`16.15`, not `server_version_num`), pasting a `postgresql://` URL (a refused URL loses its password from the field); review `stu-008-connect-review.md` fix round done (C-1..C-3, L-1..L-6; L-5 and L-7 are TD-038); Vitest, `e2e/connections.spec.ts` (axe in light/dark, en/de) and `e2e/backend.spec.ts` against the real registry. Open: the file dialog and the `openapi in sync` job (TD-011), TD-034, TD-038 |
| TABRION STU-009 | Connection registry and read-only enforcement | P0 | STU-007, TAB-040 | Done (PR #15; tabrion v0.1.0 pinned by full SHA, RC-8): registry, read-only SQLite (mode=ro, query_only, authorizer) and PostgreSQL (read-only session, `read()`, one statement, no transaction control), routes, connection test backend, engines disposed on shutdown, project SQLite only through `ProjectConfig.database_file` (T-1); STU-T-010..013, -024 in `test_read_only.py`; the `backend integration (PostgreSQL 17)` CI job. Open: the page (TD-011) |
| TABRION STU-051 | Status indicators and dialogs | P1 | STU-005 | Done (PR #5, review accepted): environment badge in header, tabs and title, D-01, D-02, D-06, D-08 with tested focus rules; their owners arrive with STU-006/008/011/012 |
| TABRION STU-011 | Query engine (SSE, caps, cancel) | P0 | STU-009, TAB-042 | Done (PR #18), built on tabrion 0.1 behind `services/tabrion_compat.py` (TD-013): `POST /api/queries`, state, SSE events, row pages, cancel; row, byte and cell caps, server-side cursor, timeouts, 4 parallel queries; STU-T-018..020 in `test_queries.py` (SQLite always, PostgreSQL with `TABRION_STUDIO_TEST_POSTGRES`). The page's heartbeat stays as it is: the SSE stream is per query |
| TABRION STU-010 | Explorer backend (catalog, table detail, extensions, SQLite facts) | P1 | STU-009, TAB-042 | Done (PR #19), reflection through tabrion's `Database.inspector()` since the TD-013 PR (before: `tabrion_compat.inspector`); 500 tables < 1 s on SQLite and PostgreSQL (`test_explorer.py`); review `stu-010-review.md` fix round done (M-1 foreign keys from `pg_constraint`, L-1..L-3, I-1..I-7), delta re-review ACCEPTED with N-1, N-2, I-9 and the SQLite < 3.37 fallback test done. The page (`/explorer`: tree, search, detail tabs) is merged (PRs #43, #46); follow-up PR #51 makes the "past the first 5000" test cheaper to render (cause: rendering 500 rows on connect under load, not the filter; no timeout raised) and resets the page through `switchProfile` when Open finds its profile deleted (Low of `stu-010-fu-review.md`; review `stu-010-fu2-review.md` accepted). It ships with the E2E test against both reference apps (`e2e/explorer-reference.spec.ts`, needs `TABRION_STUDIO_E2E_EXAMPLES`). Open: the T-10 TLS finding (STU-014), DDL and policies (release 1, STU-022; the page part in the STU-022 page PR #54), and the parts of TD-041 |
| TABRION STU-013 | Data viewer backend (keyset pages, sort, filters, cell detail) | P1 | STU-010, STU-011 | Done (PR #24), on tabrion 0.1 like STU-011 (TD-013): `GET .../tables/{schema}/{name}/rows` and `.../cell`; every statement has a `LIMIT` (spy test), keyset paging by primary key, `rowid`/`ctid` without one, `OFFSET` for views with its hint, server-side page tokens; `test_viewer.py` on SQLite and PostgreSQL. Review `stu-013-review.md` fix round done (H-1, H-2, M-1..M-4, L-1, L-3..L-5, I-1..I-3; L-2 kept open, see "STU-013 viewer: review items kept open"); delta re-review of `4ad445a` fixed (N-1 typed row identity, H-2 residual `extra_float_digits=3` pin and progress guard, M-4 residual batch cap). The `ViewerTimeout` of `test_m4_a_small_first_row_does_not_size_the_next_batch` on Python 3.12 in CI is fixed in the test: tracemalloc plus coverage's line tracer on 3.12 made the statement check take about 2 s, past the `lite` profile's 800 ms, so the test now gives its statement a timeout it cannot reach (its assertions are unchanged; delta check `stu-013-delta-v010-review.md` accepted). Its condition C-1 is fixed the same way: `test_pg_r1_decoded_json_counts_toward_the_page_cap` timed out under the `pg` fixture's 1.5 s on 3.12 with coverage and now gets the same 300 s statement timeout, as do the two other tracemalloc tests, `test_bytes_are_capped_on_the_server` and `test_pg_bytes_are_capped_on_the_server`, which timed out the same way on a loaded machine. The page: the explorer's Data tab (STU-013 page PR #52; `docs/architecture/viewer.md`, "The page"), open items TD-042; editing stays STU-026 |
| TABRION STU-014 | Diagnostics (MVP) | P1 | STU-009 | Done (PR #25), on tabrion 0.1 (`tabrion.check` on the driver connection, TD-013): `POST /api/connections/{id}/diagnostics`, tabrion's findings without those about Studio's own session, Studio's `STU-DX-001..010` (prod TLS, T-10 / STU-T-021, read-only role that can write in any way or through any role it can become, owner in any schema, default privileges, role settings with their precedence, SQLite rows written with foreign keys off or a broken foreign key, the time limit); `POST .../diagnostics/cancel`; the catalog against prepared databases in `test_diagnostics.py` (FK off, 0644, superuser, no timeout, the write paths of M-2). Review `stu-014-review.md` fix round done (M-1..M-3, L-1..L-6, I-1..I-3); delta review of `a11c8bb` fixed (N-1 the cancel stops SQLite's `quick_check` alone, CI-1 role-making tests under the `postgres_superuser` marker plus owner-level tests, T-1 tests for the 7 surviving mutants, L-5 narrowed); the CI workflow change for CI-1 is in this PR (accepted by the workflow-change review). The diagnostics view in the page: `/diagnostics` (PR #39): run on a click with D-02 for PROD, run again on the shown connection, cancel (before and during the run, on a profile switch and on leaving the page), findings by severity with icon and word, Studio's titles in en/de, every server text escaped and capped (U-1), a fail-closed parser, stale answers dropped, buttons kept focusable (aria-disabled); Vitest, Playwright and axe in light/dark × en/de; review `stu-014-ui-review.md` C-1..C-3 and L-1..L-3 done. Open: the report export is STU-025 (backend in PR #40; the page's download button and backup hints are TD-036), the rest of 08 section 4.9 (TD-032: route, progress, documentation link, "OK" rows; Studio's details and remedies are en/de since the diagnostics-texts PR) |
| TABRION STU-015 | Migration status (Alembic, SQLite `user_version`) | P1 | STU-009 | Done (PR #29): `GET /api/connections/{id}/migrations`, the per-project code trust (`/api/project/code-trust`, off by default, bound to the digest of `alembic.ini` and the revision files) and the worker process for `tabrion.migrations.status` (only the connection's target, minimal environment, 30 s timeout); threat-model entry T-16 in `docs/architecture/migrations.md`. Review fix round (`stu-015-review.md`): H-1 the worker runs the hashed bytes and reads no bytecode of the project, M-1 the driver loads before the project's folders are on `sys.path`, M-2 bounded read and kill without waiting for the pipe (Windows: TD-024), M-3 tests for `-I`, the closed streams, the answer cap and `os._exit`, L-1 the T-16 wording, L-2 cap while reading, L-3 one worker at a time (`studio.migrations_busy`). The trust dialog and the status view in the page are in PR #32. F-7 of the STU-016 review (TD-054, PR #75): the digest also covers the project modules under the `prepend_sys_path` folders, versioned; review C-1 of the fix: the worker holds project modules reached through Studio's environment (editable installs) to the hashed bytes as well. Open: the T-16 row and the changed T-08 text in doc 03 section 7.3 (coordinating thread); tests against `examples/parts` of the tabrion repository once it has an Alembic project (the tests use their own fixture); the Levuvo formats are STU-024 (done) |
| TABRION STU-021 | Export (CSV, JSON) | P1 | STU-011, STU-013 | Done (PR #33; done before STU-017, the plan's dependency on it is not needed, accepted by the owner on 2026-11-12 15:45Z, tabrion program status D-123), on tabrion 0.1 like STU-011 (TD-013): `POST /api/exports` streams a query result or a table as CSV or JSON (refusals before the first byte, at most 1 000 000 rows, 10 minutes, one export per connection and 2 at a time, CSV formula escaping for text and bytes); 1 million rows under 100 MB (tracemalloc test); `docs/architecture/exports.md`. Review `stu-021-review.md` (accepted with conditions): F-1 fetches of at most 32 rows (RSS test of wide rows after narrow ones), F-2 tests for every guard, F-3 fullwidth formula starts, F-4 one spelling of non-finite values, F-5 large integers as text, F-6 ISO 8601 intervals, F-8 the response closes the export; F-7, F-9, F-10 are TD-026. Delta check ACCEPTED at `65b544f`; its notes R-1 (the memory bound grows with the widest row) and R-2 (large integers inside arrays and single-number json documents) are documented in `exports.md`. Open: the export dialog in the page (frontend lane, after STU-012); the export dialog PR adds it to the SQL editor, with a seam for the data viewer (TD-043). TD-057 (a finished export freed its slot too late) done in PR #80 |
| TABRION STU-024 | Migration status of the Levuvo formats (Nodestro, Axentra, Orkexa) | P2 | STU-015 | Done (PR #30; backend). The plan's STU-017 dependency was dropped on 2026-11-12: doc 03 section 10 needs only STU-015, the backend needs nothing from STU-017, and the page condition C-1 (`stu-015-ui-review.md`) is met in PR #32. The owner accepted this order on 2026-11-12 15:37Z (no rollback; tabrion program status D-122); it lifts no other gate (G-S1, G-S2 for STU-026, G-R and the human security review before a public release stay). Version tables in every schema compared with the project's migration files (also in a project `.venv`), checksums as the runners record them, states current/pending/newer/changed/no_files with an SQL preview of pending steps; nothing is imported or run; fixtures copied from the three repositories (`tests/security/levuvo_fixtures`). Review `stu-024-review.md`: ACCEPTED with conditions; F-1 (a version table the role may not read, with unexpected columns or types, or a failed read is reported as `state: unreadable` with its problem, `studio.migrations_table` is new; only Orkexa's own table names count) and F-2 (tests for the preview and file caps, identifier quoting, glob characters in the project path, the order of the states, a database without version tables) are fixed. Windows (`backend test (windows-latest)` at `281258a`, 2 failures): the preview is now the text the runner reads and the checksum covers (every line end `\n`), so a file written with `\r\n` has the same preview on every platform; the glob test uses `a[b]c` everywhere and `a[b]*c?` only where `*` and `?` are valid file name characters (not Windows). The view in the page is in PR #32 (minimal, TD-025). Open: Axentra product namespaces have no files (registered in code); the review's minor findings M-1 (duplicate versions dropped silently), M-2 (Orkexa file problems show as `no_files`), M-3 (`LIMIT 100` tables and 10 000 rows cut without a flag), M-4 (files read on every request), M-5 (`config.not_utf8` vs `config.encoding`), M-6 (`python3.10` sorts before `python3.9`), M-7 (`os.scandir` without `with`) |
| TABRION STU-022 | DDL, policies, row estimates | P2 | STU-010 (plan: STU-017) | Done (backend PR #38, page PR #54), started before STU-017: the owner allows the normal ordering decisions so that work runs in parallel, and the backend needs only the explorer backend (STU-010, PR #19). `GET .../tables/{schema}/{name}/ddl`, `.../policies` (PostgreSQL), `.../row-count` (SQLite, `count(*)` on request with a 10 s timeout) and `GET /api/connections/{id}/row-estimates` (PostgreSQL `reltuples`, partitioned tables summed); the table detail's estimate sums partitions too. PostgreSQL DDL is rebuilt from the catalog with `pg_get_*def` in `pg_dump --schema-only` form, SQLite's is the stored text; only checked `SELECT` statements on the read-only path (spy tests). Delta review 2 of `cfcb6bc` ACCEPTED (E-1: a view's column default added). Delta review of `dada71c` ACCEPTED (D-1: TD-039 corrected and raised to P1; D-2: a materialized view's column settings and `CLUSTER ON` added). Review fix round (`stu-022-review.md`, H-1, M-1, M-2, L-1..L-4): catalog reads now use a fixed search_path (hardening against object resolution through user schemas; the explorer's, migrations' and reflection's catalog reads too); the acceptance covers both apps in `public`; rules, child-only `NOT NULL`, replica identity, `CLUSTER ON`, column statistics, storage, compression and options, and the materialized view's `WITH` line added; SQLite names always quoted; a failing live pg_dump fails the test; more tests. Acceptance: `tests/security/test_definitions.py::test_pg_ddl_equals_pg_dump_schema_only` and `::test_pg_ddl_of_the_reference_apps_in_public_equals_pg_dump` (the reference apps `examples/parts` and `examples/measurements` of tabrion `f4228ec`, copied, plus an `extras` schema with RLS policies) equals pg_dump statement by statement, against recorded pg_dump 16.15 output always and a live `pg_dump` where one can dump the server; `docs/architecture/explorer.md`. The page (PR #54): the DDL tab, the policies tab and SQLite's row count on request in `/explorer` (`views/explorer/Definitions.tsx`, `e2e/explorer-definitions.spec.ts`, user guide `guide/explorer.md`); PostgreSQL's estimate stays in the table detail. Review `stu-022-page-review.md` ACCEPTED WITH CONDITIONS, done: L-1 (the parsers refuse a `USING`, `WITH CHECK`, owner, note or role name that is not text and role flags that are not booleans), L-2 (a JSON panel key, re-render tests), L-3 (tests for RLS off without policies and a non-text role), L-4 (ownership through membership, policies that reach no role), I-1 (tabs shown as tabs), I-4 (Copy DDL announced again), I-5. Open: the estimates in the tree (TD-041); the gaps in TD-035 |
| TABRION STU-023 | ER diagram (xyflow + dagre) | P2 | STU-017 (not needed: the diagram needs only the explorer of STU-010) | Done (PR #72), started before STU-017 under the owner's allowance of normal ordering decisions, like STU-022: the explorer's **ER diagram** view (`views/explorer/er/`) with @xyflow/react 12.12.0 and dagre (`@dagrejs/dagre` 3.1.3, MIT; no elkjs, E-S4), both named in doc 03 section 3.2 and doc 10; one new read route `GET /api/connections/{id}/diagram?schema=` (the tables, columns, primary keys and keys out of a schema in three catalog reads; `studio.diagram_too_large` past 1000 tables). Acceptance "150 Tabellen < 2 s Layout": `src/views/explorer/er/layout.perf.test.ts` (150 tables, 300 keys, measured on the layout, wall and CPU time, alone in one worker) and `e2e/er-diagram.spec.ts` (the page's own measurement of 150 tables, `data-layout-ms`); "SVG/PNG-Export": both drawn in the page (no remote resource, PNG through the canvas 2D API because the CSP refuses `blob:` images), checked in `er-diagram.spec.ts` and on a real server under the CSP in `backend.spec.ts`. A table alternative (08 section 4.7), keyboard access to every box, axe in light/dark and en/de. Open: TD-052 |
| TABRION STU-025 | Diagnosis report and backup hints | P2 | STU-014 (plan: STU-017) | Done (backend PR #40, page part TD-036 in PR #47, the command-line report `tabrion-studio diagnose` in the diagnostics-texts PR; started before STU-017 on 2026-11-12: the owner allows normal ordering decisions so work can run in parallel, and the backend needs only the diagnostics backend of STU-014, PR #25; nothing in it depends on STU-017's docs or tag. Doc 09 still lists STU-017; recording the changed dependency there is for the coordinating thread). `POST /api/connections/{id}/diagnostics/report` with `{"format": "markdown" | "json"}` answers the report as an attachment, built from an allowlist (no password, token, URL or DSN, host, port, user, database name, path or parameter value) and passed through the log redaction (`logs.scrub`); Markdown escapes database text (T-07). Backup hints `STU-BK-001..014` in every diagnosis (`backup: {hints, facts}`): SQLite backups next to the database (found, older than the data, readable by others), PostgreSQL `archive_mode` and `pg_stat_archiver` (off, failing, nothing archived, standby); tabrion's `TAB-PG-020` gives way only when `STU-BK-010` is given. Acceptance "report without secrets (canary test)": `tests/security/test_diagnosis_report.py` (canaries in the PostgreSQL password, the process's tokens, URLs and DSNs, parameter values, a role setting, stored values and the SQLite path; both formats and the log). `docs/architecture/diagnostics.md`. Review `stu-025-review.md`: ACCEPTED WITH CONDITIONS; C-1 (the login role and the database as `[role]`/`[database]` in every text, canary with schemas named exactly like both), C-2 (`TAB-PG-020` dropped only when `STU-BK-010` is given), L-1..L-6 fixed (16 targeted mutations killed); I-1 noted. Delta review of `b8756c2`: D-1 (no report when the server cannot tell the names, `studio.report_unavailable`), D-2 (the page route keeps `TAB-PG-020` until TD-036), N-1..N-5 fixed. TD-036 done: the page PR adds the download, the backup hints and the guide page `guide/diagnostics.md`, and the page route replaces `TAB-PG-020` too; the diagnostics-texts PR adds `tabrion-studio diagnose [PROJECT] --profile ... --format markdown\|json [--output FILE [--force]] [--confirm-prod]` (the same `DiagnosisReports` and allowlist, PROD only with `--confirm-prod` and the registry's D-02 check, project profiles only when trusted, `tests/security/test_diagnose_cli.py`), and the JSON report carries the `params` of Studio's entries (TD-032) |
| TABRION STU-012 | SQL editor | P1 | STU-010, STU-011 | Done (PR #41; the editor-adoption and D-02 follow-ups in PR #48, the export dialog in PR #50): the page `/sql` with CodeMirror 6, editor tabs with local drafts, run, run script, cancel, `:name` parameters, the virtualized result grid with the detail panel, D-02 and D-06; 100 000 rows within the memory limit, and wide rows at `max_result_mb = 200` too (`e2e/editor.spec.ts`, `e2e/editor-backend.spec.ts`). Review `stu-012-review.md` (accepted with conditions): C-1..C-4 (results cleared on a profile change, the run registered before connecting, one-decimal durations, a row cache bounded in bytes) and L-1..L-8 done. The rest is TD-033; the required-suite list is TD-037. The "openapi in sync" CI job planned with STU-008 is TD-011 |
| TABRION STU-052 | Keyboard and command palette | P2 | STU-012 | Done (first part in the STU-052 PR, the rest in the STU-052 rest PR): the command palette, the shortcut help, the editor shortcuts (STU-012), F6/Shift+F6 over the shell and over the panes of the explorer and the SQL editor, keyboard-only E2E of every main flow of the MVP (connect with D-02, explorer, query, cancel; `e2e/keyboard.spec.ts`). `keyboard.spec.ts` is required in CI since the CI e2e-list PR. Left for 1.0: format and history (STU-020, TD-020) |
| TABRION STU-020 | Favorites, history, scripts, format, autocomplete | P1 | STU-017 (plan), STU-012 | Done (backend PR #71, page PR #73, delta review 2 and E-1 in PR #82; started before STU-017 under the owner's allowance of normal ordering decisions; the backend needs only STU-011 and STU-012). Backend PR: the state database `state.sqlite` (0600 through `tabrion.sqlite.connect`, STU-T-007), the query history (`GET`/`DELETE /api/history`, per profile, `[history]` settings `enabled` and `retention_days`, never a parameter value: canary test), favorite and recently opened profiles (`GET`/`PUT /api/favorites`), `.sql` scripts in the project folder (`GET /api/scripts`, `GET`/`PUT /api/scripts/file`) and the cached-only catalog for completion on PROD (`?cached_only=true`); `docs/architecture/history-and-scripts.md`. Backend PR #71. The fixes of the independent review (`stu-020-review.md`: retention on disk, Windows names, descriptor-anchored access, hard links, byte order mark, save lock, file-system problem codes) are in #71; the page part (mixed line endings kept, escaped tab titles, the new problems, a hint for a cut history entry) is in the page PR #73. The delta review's D-1 to D-3 and D-5 (a checkpoint never waits for a reader and a partial one is retried on the next purge, the settings purge off the event loop with a short wait, blank-looking characters refused in script names and escaped on the page) are fixed in #71 and #73. **Owner item for G-S1 (D-4):** reparse-point folders, including OneDrive placeholders, are refused pending the owner's decision (listed in `security-review-prep.md`); that case and the device names still need a check on a real Windows machine. Page PR #73: the history view (`/history`, ⌘Y / Ctrl+H), favorites in the connection manager and favorite and recent connections on the start page, open and save project scripts in the SQL editor (line endings kept), Format with sql-formatter 15.9.0 (MIT, 03 section 3.2), completion from the catalog of the open connection (PROD: cached only or after "Load names"), the history settings; Vitest, `e2e/history.spec.ts` (keyboard, axe light/dark × en/de) and `e2e/history-backend.spec.ts` (the acceptance flows against a real server). Delta review 2, E-1 and the Info item (PR #82, no new TD): ZWNJ/ZWJ accepted between two letters or marks (ZWJ also in an emoji sequence) and U+3000 like U+0020 in script names, server and save dialog alike; the page keeps the joiners escaped in the Open… list and tab titles (decision recorded in `history-and-scripts.md`); the listing counts refused names (`skipped_invalid`) and the Open… dialog shows the count; a busy state database is logged at info level without a traceback. The reviews of #71, #73 and #82 are done; the owner item D-4 stays with G-S1 (`security-review-prep.md`). STU-054 checks the 1.0 flows again (`ux-acceptance.md`, UX-10-H1..H6) |
| TABRION STU-053 | Accessibility audit MVP | P2 | STU-050 | In progress: the shell (first PR #20, F-1..F-5) and the work pages (editor, grid, export, explorer, data viewer, DDL, Policies, row count, diagnostics, D-02; second PR #61, F-6) are audited in `docs/architecture/accessibility.md` and `e2e/a11y-audit.spec.ts`, with no open AA violation. The rest is TD-022. TD-058 (PR #81): the Tab-stop walk measures each stop after WebKit's deferred scroll into view. Open: the screen-reader sample (VoiceOver, NVDA) and the final report (TD-022); the 1.0 views pass axe in light/dark × en/de against a real server (STU-054, `ux-acceptance-1-0.spec.ts`) |
| TABRION STU-050 | UX acceptance | P1 | STU-008..STU-015, STU-051, QA-9 | Done (PR #62; the Firefox and WebKit jobs in PR #59, green in PR #63 and required since PR #67; the "Connection lost" banner in PR #64): the checklist `docs/architecture/ux-acceptance.md` (every criterion of 08 sections 3 to 6 and of the plan's acceptance, each with its test or manual check), run on 2026-11-13 against the built app with SQLite and PostgreSQL 16 (scram) in Chromium: met for what the MVP covers. New `e2e/ux-acceptance.spec.ts` (08 section 5 "First start" under 60 s and "Connect" with the remedy, against a real server). Fixed: the start page leads to the views (it said they "arrive with the next versions"), eight connection failures name the remedy, the migration view's empty status box. The acceptance ("Alle Flows E2E auf Chromium/Firefox/WebKit; axe ohne serious/critical") is met. STU-054 decided TD-046 for 1.0: B-3 done there, B-1 (a deviation for the owner to confirm), B-4, B-5 (QA-S1, the owner) and B-7 moved to TD-060 |
| TABRION STU-016 | MVP hardening and security review (G-S1) | P0 | all of the above | **G-S1 passed** for master `9a5d4faa69f1c974999d258b36258fbc10b3323f`: the owner granted it on the condition that all 21 required CI jobs are green on that SHA, which was met on 2026-11-13 (run 43457105100). G-R is **not** granted: the mandatory human security review before any public release (STU-028, E-QA2) remains, STU-026 stays locked behind G-S2, and nothing is tagged or published. Independent review done and accepted (re-review at `2a8a229`). On 2026-11-13 the owner accepted all 13 recommendations of the G-S1 decision package, each option A (recorded in `security-review-prep.md`, "Owner decisions on 2026-11-13": F-5/TD-033 rest, F-6/TD-031 with a nightly unlocked job, F-7/T-16 residual, JR-N1 with an e2e test (TD-062), TD-005 accepted, TD-002/TD-007 and TD-003 with the real-Windows check before G-R, side-effect functions the database defines accepted, hexadecimal and underscore number forms stay refused, the upstream report only as a confidential draft, D-4 and E-1 of STU-020 accepted, TD-056 before G-R); the formal G-S1 confirmation followed on master `9a5d4fa` (above). Preparation done (STU-016 prep PR): the G-S1 readiness checklist `docs/architecture/security-review-prep.md` and an internal pre-review pass (the access rules on every API route, the security headers pinned literally, a canary through the editor, viewer, export and diagnosis routes; review `stu-016-prep-review.md` C-1, C-2, L-1..L-3 done). Independent review done and accepted: the first round at `e186178` was not accepted, its fixes (PR #65, PR #66) were re-reviewed and the review accepted at `2a8a229`. F-7/T-16 fixed in PR #75 (TD-054, re-check accepted at `d33f89d`). The G-R preconditions from the decisions are in `docs/development/release-checklist.md` (GR-1..GR-5); a human security review before a public release (STU-028, G-R) stays mandatory, and STU-026 stays locked behind G-S2 |
| TABRION STU-054 | UX acceptance 1.0 (without the Data Editor) | P1 | STU-020..STU-025 | Owner decisions done; acceptance waits on QA-S1 (manual). STU-054 PR #84 merged: the 1.0 section of `docs/architecture/ux-acceptance.md` maps every 1.0 criterion of 08 (history, scripts, format, export, DDL, ER diagram, Levuvo status, diagnosis report, the locked writing, the 1.0 settings and dialogs, de/en) and 09's acceptance to its test or manual check, run on 2026-11-13 against the built app (SQLite, Chromium; Firefox and WebKit in CI). New `e2e/ux-acceptance-1-0.spec.ts` against a real server: the 1.0 tour in English and German with axe in light and dark at every step and no English catalog text or raw key on the German pages; the negative tests (no data editing in the page on DEV and PROD, the access chip is no control, every write refused by the API on DEV and PROD with the database file unchanged, every changing route of the documented API on a reviewed list, with the full list in `tests/security/test_every_route.py`); new `src/i18n/coverage.test.ts` (every key the source names exists, both plural forms, no visible text written into the markup). TD-046 B-3 done (the `alembic upgrade head` command with Copy). The owner decided the four TD-060 items on 2026-11-13 (each option A: refused writes stay 400/405, an error-semantics decision only and no acceptance of STU-026; D-07 after 1.0; tabrion's `TAB-*` texts stay English, marked `lang="en"`; no project chooser on the start page). TD-032's dependency is resolved by PR #85 (the German `STU-DX-*` and `STU-BK-*` texts). Not accepted yet: the manual QA-S1 session (TD-046 B-5) and the other remaining TD-060 prerequisites; STU-026 stays locked behind G-S2 |
| TABRION STU-017 | MVP docs and tag 0.1.0 (no publication) | P1 | STU-016 | In progress (first part merged in PR #27; the docs part in the STU-017 docs PR): `docs/guide/` for what exists (installation by Git SHA, start, connections and trust, settings and keyboard, security model, troubleshooting). The docs PR adds the documentation checklist `docs/development/docs-checklist.md` (every requirement of 03 §10 and 09 mapped to its page and check, D-1..D-23), the clean-install test (manual on Linux on 2026-11-13 from a locally built wheel of master `7093bea`: install with the Tabrion pin, start without Node.js, sign-in link, a SQLite profile saved, tested and connected read-only, the explorer and the data viewer, `--stop`; passed. Automated: `scripts/smoke_wheel.py` now installs with the pin and signs in, connects a SQLite profile and reads its rows, in the `packaging` job on Linux, macOS and Windows), install from a wheel, troubleshooting for installing, the views and the `--stop` failures (I-5), the residual risks on the security page, the README status and install command (it had no Tabrion pin and failed), and the settings page (the timeout applies on connect; `log_sql` is saved but not used, TD-013). Open: the tag `v0.1.0` (a separate step after G-S1; no publication, G-R) and the build-only release workflow; the rest is TD-023 |
| TABRION STU-028 | Docs 1.0, clean install, release preparation | P1 | STU-054 | In progress, **preparation only** (STU-028 PR; G-R not granted: no tag, no GitHub Release, no PyPI or npm upload, no announcement). `docs/development/release-checklist.md`: every step of a 1.0 release with its check and state, and the hard stops G-S1 (passed 2026-11-13 for master `9a5d4fa`), the human security review before any public release (E-QA2, mandatory) and G-R (per publication). `scripts/release_bom.py` (standard library): the release SBOM (CycloneDX 1.5) and the third-party license report (with the psycopg LGPL-3.0 notice and the frontend licenses) from `constraints/runtime-lock.txt`, the Tabrion pin and the non-dev packages of `frontend/package-lock.json`, judged by the STU-031 policy (copyleft, unknown, missing or stale records fail; `--strict` for the release run); same bytes for the same inputs; tested offline by `tests/supply_chain/test_release_bom.py`. `collect_licenses.py` also records the commit of a Git install (PEP 610), and the Tabrion record must be of the pinned commit (review M-2). Run on 2026-11-13 for master `d72a14f` with the license records of Linux (Python 3.13 and 3.11): policy ok, 103 components; `--strict` lists the two Windows-only pins. Clean install on Linux from a wheel of `d72a14f`: `scripts/smoke_wheel.py` passed; `uv tool install` (lock versions) and pipx with pip and the Tabrion pin only passed, neither checks the lock's hashes (the first pipx form with the lock failed with pip, review H-1). Review `pr83-stu028-review.md`: ACCEPTED WITH CONDITIONS; H-1, M-1, M-2, L-1..L-5 and I-4 fixed, L-6 tracked in TD-061; delta review at `730e19a` ACCEPTED WITH CONDITIONS, D-1 (every record of a name judged, in any file order), D-2 (no URL with user information recorded), D-3 (only input errors exit 2) and D-4 (shared npm license helpers) fixed. Docs 1.0: guide index and README list the 1.0 features, `guide/installation.md` gains "pipx or uv", the Git-URL wording of 03 §4.6 is corrected (also ADR 0001), docs checklist D-24..D-26. Open: STU-054 and the rest of A-1 (STU-020..STU-025 are merged; open are the Windows checks and owner decision D-4 of STU-020, TD-052 of STU-023 and the CLI report of TD-036), the result of the first nightly `release SBOM (strict)` run (TD-061: the macOS and Windows license records and the strict run are nightly CI jobs since PR #90), pipx/uv and the guide by hand on macOS and Windows (TD-023), and every owner step (the human security review, the trademark check, the tag, G-R) |
| TABRION STU-055 | Vector data view (read-only) | P2 | STU-010, STU-014 | In progress (STU-055 PR; owner approval for immediate implementation): the view **Vector data** (`/vectors`) and `GET /api/connections/{id}/vectors` (`services/vectors.py`, design note `docs/architecture/vector-inspection.md`, guide `docs/guide/vector-data.md`). For SQLite and PostgreSQL: vector columns and types (pgvector `vector`/`halfvec`/`sparsevec` by `pg_type` and extension membership, sqlite-vec `vec0` from `sqlite_master`, Orkexa's layouts by their documented structure, no Orkexa import), vector indexes with method and state (`pg_index`/`pg_am`, `indisvalid`/`indisready`/`indislive`), dimensions, Orkexa's embedding identities, bounded counts (`LIMIT 100001`, a 10 s timer, `reltuples` estimates past the bound, at most 50 counts) and pgvector's status through Tabrion's `tabrion.postgres.extension_status`; sqlite-vec's status is "not determinable". ANN only with a valid HNSW/IVFFlat/DiskANN index, everything else "exact search (no ANN index)". Same guards and read-only path as the explorer routes; de/en. Follow-ups in TD-066 |

Later: STU-020..STU-025 and STU-054 (Studio 1.0, without the Data Editor); STU-028 (release preparation) is prepared,
publication only after G-R; STU-021
and STU-024 no longer wait for STU-017 (doc 09, Phase B changes, accepted 2026-11-12). STU-025 was started before
STU-017 on 2026-11-12 under the owner's allowance of normal ordering decisions (its backend needs only STU-014); the
change of its dependency is not yet recorded in doc 09 (coordinating thread); the STU-022 backend was started before
STU-017 as well (see its row);
STU-026 (Data Editor) stays locked behind G-S2 and outside the 1.0 acceptance; STU-027 (more UI languages) is a
later extension. The Schema Designer (change request 14 §3.5) is planned as Studio **1.2** under change request
CR-DBX, gated by G-S3; the Data Editor becomes Studio 1.1 (G-S2) and the Database Manager Studio 1.3 (G-S4). None of
them has started: see [CR-DBX](#cr-dbx-studio-11-to-13-planned-not-started).

### OA-010 · Independent review of STU-008 (TABRION STU-008)

- **Priority:** P1 High · **Status:** Accepted (`stu-008-review.md`, PR #11 merged as master `e8b1d05`) · **Refs:**
  03 §4.3, §6, 08 §4.2, §4.10,
  `docs/architecture/configuration.md` ("Writing the user's files")
- **What:** Studio now writes files. An independent review agent checks that it writes only its own `profiles.toml`
  and `settings.toml` (never the project's file), atomically with 0600 and `O_NOFOLLOW`, only into a private folder,
  only with the digest the page read and never over a file with issues; that a form never echoes a value (issues
  name fixed keys); that a typed password reaches only the secret route; and the connection manager's dialogs
  (focus, axe). No `.github/` file changes.
- **Result:** no Medium or High finding. Done in the STU-008 follow-up PR: L-1 (`sslmode` in the secret
  fingerprint; binding for STU-009 below), L-2 (a lone surrogate is `config.encoding`; a file that would not read
  back is 422 `studio.config_not_storable`, never a 500), L-3 (a refused keyring delete is logged and reported,
  `"secret_removed": false`; the test that kills W11), I-1 (the e2e server uses the null keyring), I-2 (`env:` says
  "set in the environment"), I-3 (the file is read again just before the replace; no lock, TD-011) and I-4 (the 0755
  to 0700 change is documented, as for the state folder).

## CR-DBX: Studio 1.1 to 1.3 (planned, not started)

The owner accepted change request CR-DBX on 2026-11-13 (E-X1, E-X2 at 23:28Z; E-X3..E-X13 at 23:35Z as recommended,
with binding refinements of E-X4, E-X5, E-X6 and E-X9). This confirms the **planning, not the start of
implementation**. Nothing here starts before Studio 1.0 (G-R), the repository migration and a separate
development-start approval (G-CX). G-CX enables no write or admin feature: each area stays technically locked until
its own gate, and an independent human security review is mandatory before any public release (G-R stays locked).
The work packages are defined in planning document 15 section 9.2 and the program status
(`tabrion/docs/program/STATUS.md`, section "Change request CR-DBX"); the rules are in
[STU-ADR-0005](adr/0005-crdbx-write-and-admin-gates.md).

| Version | Area | Work packages | Gate | Status |
|---|---|---|---|---|
| 1.1 | Shared write foundation: threat model and the ADR successor of 0003 (STU-XW-5), write and admin profiles, unlocking, PROD lock, flags (STU-XW-1), audit log (STU-XW-2), plan, preview and confirmation framework (STU-XW-3), I-109 j confirmation (STU-XW-4) | TABRION STU-XW-1..STU-XW-5 | G-S2 | Blocked (planned, not started; G-CX) |
| 1.1 | Data Editor | TABRION STU-026 (stays locked behind G-S2), STU-XDE-1..STU-XDE-2 | G-S2 | Blocked (planned, not started; G-CX) |
| 1.2 | Schema Designer: wizards, designer, plan preview, Alembic revision text, runtime-role wizard, graphical mode, schema comparison | TABRION STU-CR-1..STU-CR-8, STU-XSD-1..STU-XSD-3 | G-S3 | Blocked (planned, not started; G-CX, Studio 1.1, tabrion 0.3) |
| 1.3 | Database Manager: profile pairs, create, backup and restore, delete, PostgreSQL roles | TABRION STU-XDM-1..STU-XDM-7 | G-S4 | Blocked (planned, not started; G-CX, Studio 1.2) |

Binding boundaries (owner, 2026-11-13; details in STU-ADR-0005):

- **E-X4:** direct schema changes only on DEV and TEST databases without existing migration management; for Alembic
  projects Studio by default only generates revisions and never runs `env.py`; preview, plan binding, drift check and
  explicit confirmation are mandatory.
- **E-X5:** PostgreSQL backup and restore only through verified `pg_dump`/`pg_restore` with safe process execution and
  secret handling; restore tests are mandatory; the docs say that `pg_dump` is not a complete backup of global roles
  and cluster objects.
- **E-X6:** role management only for PostgreSQL, only through a separate admin profile with minimal rights; Studio
  never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION; existing grants, memberships and indirect privileges
  are checked on every change; the admin rights are provisioned outside Studio.
- **E-X7:** PROD stays write-locked (backup only). **E-X8:** separate write and admin profiles.
- **W-9:** a refused write answers 400 or 405; only the write mode answers 403 (owner decision on STU-054).
- **W-10:** the multi-host `host = "db1,db2"` that schema v1 accepts and `tabrion.profilefile` refuses (tabrion
  TB-148) is decided with STU-CR-6 and the profile import and export of STU-XDM-5.

## Cross-repository checks still to add

| Ref | What | Priority |
|---|---|---|
| TABRION QA-18 | `tests/packaging/test_independent.py`: import every tabrion module while orkexa, axentra, nodestro_* and tabrion_studio are blocked (05 §3.2) | P2 |
| TABRION QA-21 | Done in this repository: `scripts/check_tabrion_api.py` (the same file as in axentra, orkexa and nodestro-runtime) also checks used names against the installed Tabrion's `__all__` (a used name without an installed Tabrion fails; a module whose extra is missing is reported with its import error) and, with `--surface`, Tabrion's `docs/api/surface.json` (stable `S` names only: experimental `X` names are refused, 02 §14 S-2), and exports them to `docs/api-consumers.md`, enforced by `tests/architecture/test_tabrion_api_export.py` (stale or missing export, a fake installed Tabrion for the `__all__` part, the script's SHA-256 so the four copies stay identical). The `__all__` and `--surface` parts first have a name to check with the first Tabrion pin (STU-009). Open: `--check-export docs/api-consumers.md` on the existing CI step (a `.github` change), and Tabrion collecting the consumer lists into `tabrion/docs/api-consumers.md` | P3 |
| TABRION QA-21 (`--surface`) | Done: enum members and dataclass fields (`tabrion.TLSMode.AUTO`) are checked against their parent's `members`/`fields` in `surface.json` instead of being reported as missing; `--surface` against tabrion v0.1.0 passes | P3 |
| TABRION QA-22 | convention audit against orkexa/axentra (pins, manifest, Dependabot, English, license, SECURITY.md; branch protection, private vulnerability reporting and the social preview are optional, owner decision 2026-11-08): done in review, `docs/development/conventions.md`, `tests/security/test_conventions.py`; `CODE_OF_CONDUCT.md` and `SUPPORT.md` added | P2 |

## Technical debt

### TD-001 · Layer contract for `api` → `services` → `profiles`/`security`

- **Priority:** P3 Low · **Status:** Open · **Refs:** 05 §3.2, AC-1
- **What:** the import-linter layer contract needs the `api`, `services` and `profiles` packages. STU-006 adds `api`
  and `config` (the profiles layer; `config` never imports `api`, the app, the server, the CLI or `security`).
  STU-009 adds `services` (the connection registry): it never imports `api`, the app, the
  server, the CLI or `security`, `config` never imports it, and only it imports SQLAlchemy and psycopg. Today the contracts forbid every Levuvo platform, keep the process model from importing the CLI,
  keep `tabrion_studio.security` (TABRION STU-003) below the app, the server, the process model and the CLI, and
  keep `tabrion_studio.problems` and `tabrion_studio.logs` (TABRION STU-004) below every other Studio module.
- **Status note:** every contract has a mutation probe in `tests/architecture/test_forbidden_probe.py` (TABRION
  QA-16); the layer contract, and any new contract, needs its own probe there. The dependency policy (QA-17, AC-2):
  `dependency-policy.json` and the vendored `scripts/check_dependency_policy.py` (a new Levuvo dependency needs a K-33
  edge in nodestro-studio first, then a new copy here).

### TD-002 · Sandboxed browsers cannot open the redirect file (TABRION STU-003)

- **Priority:** P2 Medium (raised by review S-4: the Firefox snap is the default browser of Ubuntu desktop) ·
  **Status:** In progress · **Refs:** review F-02, `stu-003-review.md` S-4, `docs/architecture/access-protection.md`
- **What:** the browser is opened with a `file://` redirect file in the state directory. A browser in a sandbox that
  cannot read that folder (the Firefox snap on Ubuntu cannot read hidden folders such as `~/.local/state`; Flatpak
  browsers similarly) shows an error instead of Studio. Workaround: `--print-url`, or `--state-dir` in a folder the
  browser can read.
- **Done (STU-005 PR):** on Linux without `$BROWSER`, the command asks `xdg-settings get default-web-browser`, looks
  up the desktop file and prints the `--print-url` / `--state-dir` advice when it is a snap or a Flatpak
  (`src/tabrion_studio/browser.py`, `tests/unit/test_browser.py`).
- **Next step:** the fix itself: place the redirect file (only that file) in a private folder the sandbox can read,
  for example a non-hidden 0700 folder below the user's home, with the same checks as the state directory.
- **Owner decision (G-S1, 2026-11-13, decision 6, option A):** accepted for now; the fix is a G-R precondition
  (GR-1 in `docs/development/release-checklist.md`).

### TD-003 · Windows: check the DACL inside the profile too (review R-6)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-002-018-review.md` R-6, `paths.py:_check_windows_dir`
- **What:** inside `%USERPROFILE%`/`%LOCALAPPDATA%` the state directory's DACL is not checked. A state directory in a
  synced or shared profile folder (for example `%USERPROFILE%\OneDrive\…`) would put the control token, and now the
  redirect files, there. The default (`%LOCALAPPDATA%`) is not synced.
- **Next step:** run `dacl_problem` inside the profile as well, or warn about known sync folders (STU-006 or STU-016).
- **Owner decision (G-S1, 2026-11-13, decision 7, option A):** accepted as a residual risk until G-R; the fix is a
  G-R precondition (GR-3). So is a check on a real Windows machine (GR-4) of what so far only CI and tests cover: the
  refused device names of script files (for example `CON.sql`) and OneDrive-synced and other reparse-point folders,
  which Studio refuses for scripts (STU-020 D-4, kept for 1.0 by decision 11). TD-008 is done with PR #76.

### TD-004 · Fork pull requests in the workflow-change gate (review R-7)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-002-018-review.md` R-7, `ci_ok_status.py:pull_numbers`
- **What:** for fork pull requests (no `pull_requests` in the `workflow_run` event) the gate relies on
  `GET /commits/{sha}/pulls` listing the open fork pull request. If GitHub does not list it, the gate fails closed
  ("no open pull request found"), which is safe but could block a fork pull request by mistake.
- **Next step:** check it on the first fork pull request; if needed, fall back to `GET /pulls?state=open` filtered by
  `head.sha`. Changing `ci_ok_status.py` needs the label (OA-003).

### TD-005 · Spent launch URL in the browser history (review S-2)

- **Priority:** P3 Low · **Status:** Accepted risk · **Refs:** `stu-003-review.md` S-2 (X-9),
  `docs/architecture/access-protection.md` (Launch)
- **What:** the global browser history (and a synced history) keeps `http://127.0.0.1:<port>/launch#<token>` and the
  redirect file's path. The token is worthless once redeemed; if the page's script never ran, it stays valid for up to
  120 s (`LAUNCH_TTL_SECONDS`).
- **Next step:** shorten `LAUNCH_TTL_SECONDS` once real-browser start times are known (QA-9 measures them).
- **Owner decision (G-S1, 2026-11-13, decision 5, option A):** stays an accepted risk; no work before G-S1 or G-R.

### TD-006 · Who may set the label `workflow-change-reviewed` (review S-3)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-003-review.md` S-3, `ci_ok_status.py:label_after_push`,
  OA-003
- **What:** the gate records the actor of the `labeled` event but does not restrict it: any user with triage or write
  access, including an agent token, satisfies it, and the label is not bound to a SHA. Until then the "who sets the
  label" rule in `docs/development/ci.md` is policy only.
- **Next step:** optionally keep an allowlist of logins whose `labeled` events count, and optionally require the
  label to follow a PR comment `reviewed <head sha>` by such a login. Changing `ci_ok_status.py` needs the label
  itself (OA-003) and an independent workflow-change review.

### TD-007 · Development proxy and real-browser launch checks (review S-5)

- **Priority:** P3 Low · **Status:** Open (documented) · **Refs:** `stu-003-review.md` S-5, `frontend/vite.config.ts`,
  `docs/architecture/access-protection.md` (Development server)
- **What:** the Vite development proxy rewrites `Origin` to the backend's origin. Fetch metadata, the CSRF header and
  `SameSite=Strict` still block cross-site use, and Vite checks `Host`, but the development setup is not for shared
  machines (now said in the docs and in `vite.config.ts`). The launch flow (`file://` meta refresh to http, the CSP)
  was checked in Chrome only.
- **Next step:** Firefox and WebKit runs of the launch flow against the built wheel, and the `platforms.yml` smoke on
  macOS and Windows (05 §3.5), both STU-050 (review I-2 of `stu-pr12-14-review.md`). Both add CI jobs (a
  workflow-change review, OA-003) and browser installs; the network check of QA-9 (0 foreign hosts) is done in
  Chromium.
- **Owner decision (G-S1, 2026-11-13, decision 6, option A):** accepted for now; the Firefox and WebKit launch
  check is a G-R precondition (GR-2).

### TD-008 · Add a windows-latest CI job for the T-2 checks (review C-2 of `stu-006-review.md`)

- **Priority:** P1 High (before STU-009 and before any build is published, G-R) · **Status:** Done (PR #76; its
  workflow-change review accepted, `pr76-f6-lock-workflow-review.md`; G-S1 decision 7) · **Refs:**
  `stu-006-review.md` T-2 and C-2, `config/locations.py`
- **What:** the review asks for a test in a Windows CI job that patches `nt._getfinalpathname` and asserts that it is
  never called for a UNC value from the project file. The test exists
  (`tests/security/test_project_trust.py::test_windows_never_resolves_a_unc_path_from_the_project_file`) and the pure
  path tests use `PureWindowsPath` on every platform; today the Windows test runs in the `windows-latest` leg of the
  test matrix. The conditions PR does not change `.github/`.
- **Done:** the CI job `T-2 path checks (windows-latest)` (required in `.github/ci-manifest.json`) installs from the
  hashed runtime lock (TD-031), runs `tests/unit/test_config_locations.py` and the two T-2 tests of
  `test_project_trust.py` by name, and its gate step reads the JUnit report: each of the eight T-2 tests
  (`test_windows_never_resolves_a_unc_path_from_the_project_file` among them) must have passed at least once, and
  nothing may fail or skip, except the link tests of `test_config_locations.py`, which skip on Windows with the reason
  "symlinks". A renamed test or a new skip condition fails the job.
- **Next step:** none; the review is done and PR #76 is merged.

### TD-009 · STU-004 review: remaining Low and Info items

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-004-review.md` L-2, I-1..I-5
- **L-2 (rest):** the scrub does not remove `passwd <value>` without `=` or `:`, the short key `pw=`, or URL-encoded
  `password%3D...`; matching those without false positives needs care (the regex is defence in depth only).
- **I-1:** above 512 registered secrets the oldest, the control token, is dropped first. Pin it (a set that is never
  evicted) or evict only session and launch values.
- **I-2:** a hard link at the log path is accepted and a FIFO would block the start. Refuse `st_nlink != 1`; open with
  `O_NONBLOCK`, check `S_ISREG`, then clear the flag.
- **I-3:** Windows has no `O_NOFOLLOW`: check the log file with `lstat` (reparse bit) before `os.open`.
- **I-4:** the refusal texts say "the state directory" also for the log folder; `warnings.warn` output goes to
  stderr outside the redacting handlers (`logging.captureWarnings` is not set).
- **I-5:** no test plants a log file owned by another user (mutant M11): `os.chown(path, 65534, 65534)` when running
  as root, otherwise skip.
- **R-2 of `stu-conditions-rereview.md`:** exception text still reaches the message in three ways: (a) the message
  *is* the exception (`logger.error(exc)`; Uvicorn does this on a startup `OSError`), (b) an exception nested in a list
  or tuple argument, (c) traceback text without the `Traceback (most recent call last):` line. None is used by Studio
  or reachable by a client today. Fix: replace a `BaseException` message by its type, apply the argument replacement
  recursively (depth-capped), and optionally cut non-Studio messages at `\n  File "`.
- **Observed in the conditions PR:** when the lifespan fails at startup, Uvicorn exits through `sys.exit(3)`, so
  `serve()` does not print "Tabrion Studio could not start." (its cleanup still runs). Catch `SystemExit` around
  `server.run` if the message matters.

### TD-010 · STU-006 review: remaining Low and Info items

- **Priority:** P2 Medium · **Status:** Open (T-4 rest, T-5 and STU-007 L-1 done in the D-01 flows PR) · **Refs:** `stu-006-review.md`
  T-1, T-4, T-5, I-1, I-3..I-5; `stu-007-review.md` L-1, I-4, I-5
- **T-1 (rest):** the trust POST binds to the bytes only. If only the file system changes between the dialog and the
  click, the new review is stored without having been shown (the next GET compares against it). Bind the POST to a
  digest of the review as well. The real target of a database path the user confirmed as *outside* is not recorded,
  so a link that later points elsewhere outside keeps it trusted; record the resolved path in the review.
- **T-4 (rest), done:** D-01 shows each connection's TLS mode and marks `weak_tls` (weaker than `verify-full` on a
  non-loopback host). The T-10 diagnostic finding for `disable` and `prefer` is `STU-DX-002` of STU-014 (I-5 of
  `stu-010-review.md`; STU-T-021 in `test_diagnostics.py`).
- **T-5, done:** D-01 shows one line per PostgreSQL connection with the secret's source, `host:port` and the TLS mode,
  and marks `remote_host`.
- **I-1:** escape `\p{Default_Ignorable_Code_Point}` (U+200B, U+FEFF, U+00AD, tag characters) in `visible.ts`.
- **I-3:** open `.tabrion` with `O_DIRECTORY|O_NOFOLLOW` and `studio.toml` with `dir_fd` (one path walk); test the
  dev/ino compare (mutant T2); on Windows compare `GetFinalPathNameByHandle` with the expected path.
- **I-4:** check the configuration folder like the state folder (owner, 0700, no link): `--config-dir /tmp/x` would
  accept another user's `settings.toml`.
- **I-5:** name STU-T-021 (T-10) in the `verify-full` test or note that it belongs to STU-010.
- **STU-007 L-1, done:** the D-01 line reads "password from `env:X` / `file:Y` is sent to `host:port`" and marks
  well-known credential variables (`sensitive_variable`) and `file:` passwords outside the project
  (`secret_outside`). Not done: limiting project `file:` references to a subfolder such as
  `<config>/project-secrets/`; the marks are warnings, the user decides.
- **STU-007 I-4:** `select_backend` refuses a denylist of backends; a third-party plain-text backend configured
  through `PYTHON_KEYRING_BACKEND` or `keyringrc.cfg` would be accepted (user configuration only). Consider an
  allowlist: `keyring.backends.macOS`, `Windows`, `SecretService`, `libsecret`, `kwallet`.
- **STU-007 I-5:** on Windows the `file:` reader checks the components with `lstat` and then opens by path, so a swap
  in between is followed; owner and mode are not checked (the folder ACL decides, TD-003). After the open, compare
  `GetFinalPathNameByHandle` with the expected path and check that the handle is not a reparse point.
- **R-1 of `stu-conditions-rereview.md` (done in the STU-007 PR):** a *relative* link target that climbs to `/net`
  was checked only as text, so the walk `lstat`ed `/net/<host>` (on macOS an automount, a beacon). `safe_realpath` now
  checks every component against the network and automount forms before `lstat`
  (`test_a_relative_link_that_climbs_to_net_is_refused_before_lstat`). R-3 (refuse a leading `\??\` as text) is done
  as well.
- **PR #14 review (`stu-pr12-14-review.md`, accepted; open follow-ups):**
  - **L-1:** the D-01 password line starts with the connection name, which may be up to 200 characters and contain
    `:` and `→`. A hostile name can push the real `secret → host:port` behind the 64-character cap ("Show full
    value"). The marks, the TLS mode and the hosts and variables lists stay visible. Show the name, the secret and
    the destination as three separately capped parts, or put the destination first.
  - **I-1:** `sensitive_variable` is a heuristic. It does not mark `DATABASE_URL`, `STRIPE_KEY`, `KUBECONFIG`,
    `SENTRY_DSN` or `*_KEY` in general; consider `_KEY`, `_PASSWORD` and `DATABASE_URL`. Every `env:` variable is
    listed in D-01 anyway.
  - **I-2:** after the upgrade, a project trusted before #14 shows every connection as "changed" without a reason.
    Show a one-time note ("Studio now shows more about each connection; nothing in the file changed"), so that
    users do not learn to click through a changed D-01.
  - **I-3:** pin the prefix list itself with a name like `AWS_REGION` (mutant P12 survives, because
    `AWS_SECRET_ACCESS_KEY` also matches `SECRET`).

### STU-014 diagnostics: review items and limits kept open

- **Priority:** P3 · **Status:** Open · **Refs:** `stu-014-review.md`, `docs/architecture/diagnostics.md`
- **TAB-PG-003 in tabrion (M-3):** tabrion's owner finding counts only tables in `current_schema()`, which hides an
  owner role from every tabrion user whose tables live elsewhere. Studio leaves `TAB-PG-003` out and uses its own
  `STU-DX-006` (any schema). Raise it as a tabrion issue (a `role_info(owner_scope="database")` or a wider default)
  through the coordinator; Studio takes it with tabrion 0.2 (TD-013).
- **Sequence `USAGE` (M-2):** `STU-DX-003` counts `UPDATE` on a sequence (`setval`), not `USAGE` (`nextval`): a
  read-only role often has `USAGE` for `currval`, and flagging it would be noise.
- **Schema `USAGE` (I-3):** `has_table_privilege` ignores `USAGE` on the schema, so a grant in a schema the role
  cannot enter still counts as writable. Conservative on purpose.
- **Server-wide settings:** `STU-DX-004` and `-010` read `pg_db_role_setting` only; `postgresql.conf` and
  `ALTER SYSTEM` values need `pg_read_all_settings`, which a runtime role should not have. The detail says so.
- **One time limit for the whole diagnosis:** the connection's statement timeout (30 s, 15 s on prod) bounds the
  sum of the checks, not each. A very large SQLite file can therefore stop in `quick_check` every time; the finding
  `STU-DX-008` says to give the profile a longer statement timeout. tabrion's other SQLite findings stay (N-1 of the
  delta review). On PostgreSQL a limit during tabrion's step skips the later Studio checks too (I-3, by design).
- **CI-1, the role-making tests in CI (workflow change, done in the STU-014 PR):** the 10 tests with the marker
  `postgres_superuser` (M-2 write paths, default privileges, owner outside the search path and through a group role,
  role-in-database precedence, superuser) need `TABRION_STUDIO_TEST_POSTGRES_SUPERUSER`. The PostgreSQL 17 job now
  runs `-m "postgres or postgres_superuser"` with the throwaway server's superuser URL in the pytest step's env only,
  and its "none skipped" check expects at least 50 tests (workflow-change review in `stu-014-review.md`: accepted;
  the label `workflow-change-reviewed` is set by the coordinating thread). The plain-owner checks stay
  (`test_pg_owner_settings_and_schemas_as_a_plain_owner`).
- **Finding 3 of `stu-014-delta-v010-review.md` (done):** `test_n1_a_stop_before_tabrions_step_still_runs_it_on_sqlite`
  requests the limit or the cancel before the step "tabrion's checks" starts, so turning `always=sqlite` into
  `always=False` fails it every time (10 of 10 runs; the race-based tests caught it only by chance).
- **Cancel scope (I-1 of the delta review):** a second signed-in session can cancel another session's diagnosis,
  as with `/api/queries/{id}/cancel` (single user).
- **Cancel per connection, not per run (I-1 of `stu-014-ui-review.md`):** the page cancels on Cancel, on a profile
  switch and on leaving the page, and `Diagnostics.cancel` stops every running diagnosis of that connection, which
  every tab shares for a profile. So another tab's run on the same profile can end with "you canceled it", and a
  late fire-and-forget cancel can hit a new run started right after a switch back. Both results are honest
  (`STU-DX-008`). Fix with a per-run token in the backend (run id in the answer, cancel by run id) at the next
  backend touch (STU-025 or later).

### STU-013 viewer: review items kept open

- **Priority:** P3 · **Status:** Open · **Refs:** `stu-013-review.md`, `docs/architecture/viewer.md`
- **L-2 (OFFSET pages of a view are not in a stable order without a sort):** not done on purpose. Ordering every
  page of a view by all its columns makes each page evaluate and sort the whole view, so a large or endless view
  would only reach the timeout (the SQLite test with an endless view shows it). The page keeps `hint:
  "unstable_order"`; a sort column gives a stable order for distinct sort values. Revisit with the grid tab
  (STU-012): it could offer "sort by all columns" for small views.
- **L-5 (one budget with the query engine):** the viewer now has its own limit with the same numbers (4 statements
  in Studio, 2 per connection, `studio.query_limit`), but it does not share the query engine's count, so 4 queries
  and 4 viewer statements can run at once. One shared budget needs a small slot API on `QueryEngine`; do it when the
  grid tab and the SQL editor run side by side (STU-012).
- **I-3 (filter type mismatches):** `id eq "abc"` on PostgreSQL stays a classified database problem (`db.data`,
  `db.undefined_object`), which is safe (no SQL, no values). Mapping it to `studio.viewer_filter_invalid` needs the
  column types in the filter check; with the grid tab.
- **SQLite text in another encoding:** the byte caps cut text as UTF-8; a database in UTF-16 is cut at the same
  number of bytes but decoded as UTF-8. No reference app uses UTF-16; check with STU-016.
- **Progress guard of the viewer (finding 3 of the re-review of `974b958`):** the guard only makes a walk end. With a
  sort key whose text does not read back as its value (float text without the `extra_float_digits=3` pin), rows can
  repeat before it trips (page 2 ends on a new lossy key) or be skipped silently (a descending page after a lossy
  anchor comes back empty, and the guard does not look at an empty page). The pin is the real fix and holds on every
  supported server; revisit if another type's text turns out lossy (compare the anchor on the server, or refuse).
- **Decoded size (R-1 of the re-review of `974b958`):** a page's first row and the cell detail are not bounded by the
  decoded size: one row of many large `json`/`jsonb`/array columns, or a 10 MB `jsonb` value in the cell detail, can
  take about 20 times its text in Python. The cell detail could return such values as text; with the grid tab.
- **Info items of the delta re-review of `4ad445a`:** a domain over a domain resolves one level only in
  `_PG_COLUMN_TYPES_SQL` (a `json` domain two levels deep is not refused up front, it fails on page 1 as a database
  problem; a `bytea` domain shows as hex text); `xid` and `cid` have no ordering, so a sort by them is a database
  error on page 1 (add them to the unordered types); `(tableoid, ctid)` is used for plain tables too, which rules out
  a TID range scan (use `tableoid` only for relkind `p` or tables with children). With the grid tab (STU-012).

### TD-012 · STU-009 integration: what remains before it can merge

- **Priority:** P1 High · **Status:** Done (PR #15: tabrion v0.1.0 pinned, master merged; the CI workflow commit
  was reviewed and labeled; the STU-011 binding below stays a rule) · **Refs:** RC-8, 03 §5.1, §8,
  `docs/architecture/connections.md`
- **Re-review 2 (accepted with conditions; workflow change approved for the label):** R2-L1 (`DO` and the
  query-in-text functions refused), R2-I2 (tests that kill T1 and T6) and R2-I1 (no pip option after a marker) are
  done; the Info on the service port is done in a separate `.github` commit, which needs the workflow-change review
  again before the label. The tabrion v0.1.0 pin is done; CI fully green, including the RC-8 guard, is the merge
  condition.
- **Conditions to leave draft (re-review):** 1. CI installs the pinned tabrion and runs the `postgres` tests (L-2,
  below); 2. R-L2, the strict pin rule (done: `tests/security/test_dependencies.py` allows only plain requirements
  and the full-SHA pin, in every requirements or constraints file at any depth); 3. R-L1 (done: token-based function
  check); 4. I-R2 (done: `test_i_r2_one_profile_read_per_connect` kills mutant N5).
- **tabrion v0.1.0 (TS-001):** done. The final range is in `pyproject.toml`, the v0.1.0 commit is pinned by SHA in
  `constraints/tabrion-integration.txt`, and master is merged in.
- **CI installs tabrion from the pin (L-2 of `stu-009-review.md`):** pip reads a constraints file only from `-c`,
  `PIP_CONSTRAINT` or a pip configuration file outside the repository. The workflow commit on this branch adds
  `-c constraints/tabrion-integration.txt` to every `pip install -e ".[dev]"` and sets `PIP_CONSTRAINT` for the wheel
  installs (e2e, `build and install`, the SBOM job). It passed the independent workflow-change review and carries the
  `workflow-change-reviewed` label (OA-003).
- **The CI job `backend integration (PostgreSQL 17)`** (same workflow commit, added to `.github/ci-manifest.json`):
  a `postgres:17.10` service pinned by digest, a non-superuser role that owns its database, `pytest -m postgres`
  with `TABRION_STUDIO_TEST_POSTGRES`, and a check that every PostgreSQL test ran and none was skipped.
- **Binding for STU-011 (kept in the STU-011 draft, `docs/architecture/queries.md`):** user SQL goes through `services.readonly.check_statement` and `OpenConnection.fetch`
  (prepared statements: the server refuses several commands, H-1), with one `Database.read()` per statement, and
  always through `ConnectionRegistry.get` (`fetch` does not check the handle again by itself; review I-5). SQLAlchemy
  `text()` treats `:name` as a bind parameter, also inside string literals: send user SQL with `exec_driver_sql` or
  escape colons (review I-2).
- **The page:** "Test connection" and "Connect" (TD-011) can now call `POST /api/connection-tests` and
  `POST /api/connections`; the frontend part is not in the STU-009 draft. D-01's "password asked when connecting"
  holds now: without a reference, libpq gets no password of its own (M-1).
- **The libpq environment:** `PGPASSWORD`, `PGPASSFILE`, `.pgpass` and a service file's password are off for every
  Studio connection (M-1). Other variables of the user's own environment (`PGHOSTADDR`, `PGSERVICE`,
  `PGSSLROOTCERT`, ...) still apply to what the URL leaves out; a project file cannot set them. Decide whether
  Studio should clear them too.

### TD-024 · STU-015 migration worker on Windows: children of trusted project code

- **Priority:** P2 Medium · **Status:** Done (PR #29, M-2 of `stu-015-review.md`) ·
  **Refs:** T-16, `docs/architecture/migrations.md`
- **Done:**
  - POSIX: Studio kills the migration worker's process group after the answer or after 30 s, reads the answer up to
    its line end, and never waits for the pipe to close.
  - Windows: the worker goes into a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before it gets its request
    (`migrations._windows_job`, through `ctypes`). Closing the job ends every process in it.
  - `test_children_end_with_the_worker_on_every_platform` checks the result with a heartbeat file.
  - Both pipes are closed on every platform. The first Windows run (`backend test (windows-latest)` of `ci.yml`, head
    `48cf346`) failed 15 tests in `test_migrations.py` with `ResourceWarning: unclosed file`: on Windows,
    `run_worker` closed only stdin, because closing the answer pipe while the reader thread reads it waits for that
    read. Now the reader thread owns the answer pipe and closes it when its read ends, and `run_worker` joins it
    (at most 5 s) after the worker and its job are gone. `test_the_worker_pipes_are_closed_on_every_platform` runs
    the Windows exchange on every platform (`migrations._POSIX`) and checks that both pipes are closed, the worker
    is reaped and no `ResourceWarning` is left for a finalizer.
  - `backend test (windows-latest)` of `ci.yml` is green on the STU-015 branch: CI run 43201304911 on `23fd8a6`,
    all 18 jobs green, 1500 passed on Windows, no faulthandler dump. Before that, on STU-015 head `425194e`, the job
    hit its time limit and left no log, so pytest dumps every thread's stack after 300 s in one test
    (`faulthandler_timeout`) and then ends the run (`faulthandler_exit_on_timeout`, I-1 of
    `stu-29-30-windows-delta-review.md`), so the step ends before the job's limit and its log is kept.
  - Tests for L-1 and L-2 of `stu-29-30-windows-delta-review.md`: every "windows" case asserts that the Windows
    exchange ran (`_POSIX` is not ignored), and `test_an_escaped_child_does_not_hold_the_windows_exchange` checks
    that `run_worker` returns within its bounds while a child that left its session holds the answer pipe.
- **Limit kept:** if Windows cannot make the job (nested jobs before Windows 8), only the worker is killed; a child
  that escaped then holds the reader thread (not `run_worker`) until it ends.
- **Also from the review (Info, accepted):** I-2 the answer can be forged by trusted code (`_revisions()` validates
  it); I-3 the digest is computed on every code-trust and status request (cache by mtime, size and inode if it
  shows up in profiles); I-5 the untrusted path reads at most 100 `version_num` rows without a length cap.
### TD-026 · STU-021 export: what `stu-021-review.md` left as info

- **Priority:** P3 Low · **Status:** Open (STU-021 done in PR #33; by 2026-12-15, with STU-012) · **Refs:**
  `docs/architecture/exports.md`
- **F-7:** psycopg's default loaders fail on PostgreSQL `infinity`/`-infinity` dates and timestamps, BC dates and
  years above 9999 (`db.data`). A table with `valid_to = 'infinity'` cannot be exported, viewed or queried. Fix it
  for the query engine, the viewer and the export together: custom loaders in `tabrion_compat` that give these values
  as text (`infinity`, `-infinity`, PostgreSQL's own text for BC and large years).
- **F-9:** add `filename*=UTF-8''<percent-encoded>` to `Content-Disposition`, so a table export keeps a non-ASCII
  table name.
- **F-10:** map PostgreSQL's "feature not supported" for a data-modifying CTE under `DECLARE CURSOR` to
  `db.read_only`, or refuse such a CTE in `check_statement`, so the problem says why.

### TD-025 · STU-015/STU-024 page: the Levuvo view is minimal

- **Priority:** P3 Low · **Status:** Open (STU-015 page done in PR #32; C-1 of `stu-015-ui-review.md` is done) ·
  **Refs:** STU-024, `docs/architecture/migrations.md` ("The page")
- **What:** the view Migrations shows each Nodestro, Axentra and Orkexa version table with its state, current
  version, the steps not applied (or not known, or changed) and its problem. It does not show the first 2 000
  characters of a pending step's SQL (`preview`), its checksum, the files folder or `files_issue`, nor Orkexa's
  expected version. A format the page does not know is counted in a fixed line ("N further formats, not shown yet").
- **Done (C-1 and the delta review of `stu-015-ui-review.md`):** the page renders every format value #30 sends
  (`nodestro`, `axentra`, `orkexa`, each state `current`/`pending`/`newer`/`changed`/`no_files`/`unreadable`, an
  entry from files only); L-8, an unreadable table names `db.permission`, `studio.migrations_table`,
  `studio.statement_refused` and `db.timeout` in its own en/de words, other codes fall back to the problem text;
  I-6, at most 20 open steps, then "and N more steps", names and the namespace cut at 64 characters; L-7, an
  unknown step status is escaped (test); L-6, the revoke path rereads on the shown connection (test); L-9, a status
  that arrives after a newer request or a profile switch is dropped. I-7 (the `no_files` wording) is unchanged.
- **Done after the page delta review (`stu-32-page-delta-review.md`, 2026-11-12):** F-1, trust and revoke reread
  only if no request or profile switch came while they ran (tests for both paths and for an overtaken reread);
  F-3, tests for the reread guard and counter, the exact step cap and an unreadable table without a problem; F-5,
  a cut unknown step status gets "…"; F-6 wording; F-7 rewrap. F-2 is TD-027. F-8 (harness) is open.
- **Next step:** with STU-024 merged into the stack, show the preview of a pending step on request (escaped, as
  text, never run) and the files folder; keep the count line for formats added later.

### TD-027 · STU-009 registry: two concurrent opens of one profile can both connect

- **Priority:** P3 Low · **Status:** Open (recorded 2026-11-12, F-2 of `stu-32-page-delta-review.md`) · **Refs:**
  STU-009, `src/tabrion_studio/services/connections.py` (`ConnectionRegistry.open`), STU-015 page (#32)
- **What:** `open` looks for a profile's open connection under the lock, connects outside it, and inserts the new
  connection under the lock again. Two concurrent `POST /api/connections` for the same profile can therefore both
  connect and both insert, leaving two open connections for one profile, one of them unused. The migrations page
  can trigger it since L-9 (a profile switch resets `busy`): show A on a slow host, switch to B and back, show A
  again. The count stays bounded by `max_open` (8, then `studio.connection_limit`), and the extra connection can be
  closed in the Connections view; nothing leaks past the limit.
- **Next step:** after `_connect`, look again under the lock for a connection of the same profile; if one exists,
  close the new one and return the existing one. Test with two threads opening one profile against a slow connect.
  Not fixed in #32, which is a frontend PR.

### TD-028 · Windows CI: the explorer tests' fixture committed 505 times

- **Priority:** P2 Medium · **Status:** Done (PR #36, recorded 2026-11-12) · **Refs:** STU-010, `tests/security/test_explorer.py` (fixture `lite`), CI job `backend test (windows-latest)`
- **What:** run 43225035767 (head `0c0d143`, #32, a frontend-only diff) hit the job's 20-minute timeout:
  `test_explorer.py` alone ran from 13:25:15Z to 13:37:42Z (12.5 minutes). On master and PR runs 43224526741,
  43224287302, 43222787553, 43222180402 and 43220996495 the same file took 2 to 5 minutes on Windows, seconds on
  Linux. `faulthandler_timeout` (300 s) never fired: no single test was slow, every test's setup was.
- **Cause:** the `lite` fixture built its 503 tables, an index and a view with one `executescript()` call. Python's
  `sqlite3` runs such a script in autocommit, so each of the 505 statements was its own transaction: a rollback
  journal created, written, flushed and deleted each time, about 2,000 flushes per fixture (`strace -c`:
  2,020 `fsync`/`fdatasync`, 505 journal `unlink`; Python 3.11 and 3.12 call `fdatasync`). The fixture runs for 14 of the file's 17 tests. On Linux that is under a second per
  fixture; on the Windows runner each `FlushFileBuffers` and journal create/delete costs tens of milliseconds, so
  the file took 2 to 12.5 minutes depending on the runner's disk. Locally, with an `LD_PRELOAD` shim that adds 20 ms
  to each `fsync`, the file took 620 s on master, 43 s of setup per test.
- **Fix:** the fixture wraps the script in `BEGIN; ... COMMIT;`: one transaction, 6 flushes and 1 journal `unlink`. The same shim
  run takes 4.9 s; without the shim the file went from 14 s to 2.6 s. No test, assertion or timeout changed; the
  explorer code was not the cause (the 500-table catalog read stays well under its one-second bound).
- **Check:** PR #36 is merged only after its own `backend test (windows-latest)` run shows `test_explorer.py` in
  seconds (independent review `stu-36-win-explorer-review.md`, ACCEPTED). Test fixtures that write many statements to a SQLite file use one transaction.

### TD-013 · STU-011 query engine on tabrion 0.1: what changes with tabrion 0.2

- **Priority:** P1 High · **Status:** Partly done (STU-011 done in PR #18; the pin move and the inspector in the
  TD-013 PR) · **Refs:** 03 §5.3, §6, §9, doc 04 §5.3 to §5.6, backlog TAB-033/TAB-042, `docs/architecture/queries.md`
- **Done in the TD-013 PR (tabrion 0.2.0):** `constraints/tabrion-integration.txt` pins tabrion 0.2.0, the untagged
  merge commit of tabrion PR #65 (`52353daee6cd6e37a7efc45b74061a48beb2b280`; no tag, no release), and
  `pyproject.toml` takes `>=0.2,<0.3`. tabrion 0.2.0 changes only its `dev` extra, so the runtime set and
  `constraints/runtime-lock.txt` are unchanged (`runtime_lock.py check` and `check-installed` pass on it).
  `scripts/check_tabrion_api.py` needs no change (the 37 names Studio uses are public and stable in 0.2.0's
  `surface.json`; `docs/api-consumers.md` is unchanged). `tabrion_compat.inspector` is gone: `OpenConnection.inspect`
  uses `Database.inspector()` and `tabrion_compat.pin_catalog_path` sets the fixed catalog search path on its
  connection first (`test_explorer.py` unchanged; `test_pg_h1_planted_operators_are_never_called` fails without the
  pin). tabrion 0.2's profile file reader refuses `?`, `#` and `%` in a SQLite `path` (`profilefile.value`, tabrion
  0c3fd7a, G-T2 B-1); Studio's validator now does the same (`config.url_character`), so a project file with such a
  path is invalid with that issue (`test_url_characters_in_a_sqlite_path_make_the_project_file_invalid`).
- **Not replaced yet, and why (checked against 0.2.0):** `stream`/`ResultCursor` -> `Transaction.stream`: 0.2 wraps a
  `str` in `text()`, which reads `:name` inside literals (I-2), so Studio would need to hand it an escaped
  `TextClause` built from its lexer; on PostgreSQL it always opens a server-side cursor, which `EXPLAIN` and `SHOW`
  cannot use (Studio picks a plain cursor for them); its batch size is fixed per stream, while Studio's 2 MB fetch
  budget changes the size per fetch (M-3, R-M1). `Cancel` -> `Transaction.cancel`: 0.2 cancels the whole transaction
  and keeps a PostgreSQL connection that got one out of the pool (H-1 holds), but Studio's `Cancelled` and
  `_discard` paths and their tests (`test_l2_*`, `test_*h1_*`) move with `stream`. `Column` -> `tabrion.Column`:
  0.2's type names come from `format_type` (`integer`), Studio's from psycopg (`int4`), a visible change of the result
  columns; it moves with `stream`. `check` -> `tabrion.check(db)`: unchanged in 0.2 (`check(Database)` exists since
  0.1); Studio keeps the driver-connection form for the cancel of `quick_check` (M-1 of `stu-014-review.md`).
  `reset_session`: 0.2 has no session reset of its own, so Studio's check-in reset stays (`test_pg_m2_*`). The
  extensions query and the SQLite facts: `tabrion.postgres.extension_status` and `tabrion.sqlite.diagnose` are the
  same in 0.1 and 0.2, so they are not part of this move. Open: Studio's PostgreSQL `database` field does not refuse
  the URL characters that tabrion's reader refuses there (`/?#%@`); Studio builds the URL with
  `sqlalchemy.URL.create`, so the name is not read as a URL; decide whether to align it too.
- **Built on tabrion 0.1:** STU-011 (and STU-010) depend on TAB-042, tabrion 0.2 with `Transaction.stream`,
  `Transaction.cancel`, `tabrion.Result`/`Column` and `Database.inspector()`. Until then
  `services/tabrion_compat.py` builds stream, cancel and column metadata from `Database.read()` and
  `Transaction.connection` (a psycopg server-side cursor, `cancel_safe()`, `sqlite3` `interrupt()`, the type name by
  OID). It is the only module that touches the driver (`tests/architecture/test_compat_boundary.py`).
- **With tabrion 0.2:** replace `tabrion_compat.stream` with `Transaction.stream`, `Cancel` with
  `Transaction.cancel`, `Column` with `tabrion.Column`, `tabrion_compat.inspector` with `Database.inspector()`
  (STU-010); keep the STU-011 tests (STU-T-018..020) unchanged as the
  acceptance of the swap. Check that 0.2's `stream` takes the driver's text without `text()` (I-2), or keep Studio's
  `sqltext.bind` in front of it, and that a canceled connection leaves the pool on both engines (H-1 of
  `tab-033-review.md`; Studio's `_discard` and its tests `test_l2_*`, `test_*h1_*` are the acceptance). Replace
  `tabrion_compat.reset_session` (check-in `RESET ALL` with Studio's settings as startup options, M-2 of
  `stu-011-review.md`) with tabrion's own session reset if 0.2 has one, and keep `test_pg_m2_*`.
- **Fix round of `stu-011-review.md` (done in the STU-011 draft):** M-1/M-2/I-2 allowlist of first words in
  `check_statement` and the check-in reset; M-3 fetches within a 2 MB budget (a single value still loads in full:
  only the database's limit bounds it; the data viewer's "load in full" is the way to large values); L-1
  `sqltext.is_bound` in `stream`; L-2 tests of both cancel guards; L-3 one cancel worker per query, which keeps
  asking once a second after 3 s (closing a running connection from another thread is safe in neither driver, so a
  statement that ignores the cancel still holds its slot until the driver returns); I-3 `max_result_mb` (1 to 200).
  Open from the review: I-1 (bind a query to its session if Studio ever has several sessions) and I-4 (the SSE
  generator polls every 50 ms; a condition would avoid it), both for STU-012, by 2026-12-15.
- **Delta re-review of `stu-011-review.md` (head 170aeaa, done in the STU-011 draft):** R-H1 the first word, the
  control checks and the cursor choice come from the lexer (`readonly._lex`) in three readings (PostgreSQL with and
  without backslash escapes, SQLite), which must agree; `--` ends at `\r` in PostgreSQL, SQLite block comments do not
  nest. The check-in reset also runs `CLOSE ALL` and `pg_advisory_unlock_all()`. R-M1 the fetch budget follows the
  largest row so far, grows at most twice per fetch and counts nested values. R-I1 a `search_path` or
  `transaction_timeout` in the session settings is refused by `startup_options` until it can quote the one and check
  the server version for the other like tabrion (with tabrion 0.2's session reset, TD-013). Open: R-I2 (`_expire`
  may report a user cancel shortly before the timeout as `db.timeout`; cosmetic, STU-012 by 2026-12-15).
- **Delta re-review 2 of `stu-011-review.md` (head 70ca2f9, accepted):** R2-L1 done (the lexer and the placeholder
  scan skip white space and `--` comments with one possessive regular expression; a timing test at the API cap),
  R2-I2 done (NUL refused), R2-I1 done (the check-in reset also runs `SET SESSION AUTHORIZATION DEFAULT` and
  `RESET ROLE`, which `RESET ALL` leaves alone; `test_pg_r2_i1_*`). R2-I3 and R2-I4 need nothing (the server rejects
  those texts).
- **Follow-ups:** the error position in `db.<kind>` problems (`position`, for the editor's marker; the server
  cursor's `DECLARE` and the `%(name)s` rewrite shift it, so it needs a mapping back); the `max_rows` setting is 10 000
  by default while 03 §5.3 names 100 000 as the default ceiling (decide with STU-012, which needs 100 000 rows within
  its memory limit); `log_sql` is not used (the engine never logs SQL); "load in full" for a cut cell of a query
  result (10 MB, 03 §5.3; the data viewer has it since STU-013) and the PostgreSQL `EXPLAIN (FORMAT JSON)` cost
  warning for prod profiles come with STU-012.
- **Explorer (STU-010) with tabrion 0.2:** replace `tabrion_compat.inspector` with `Database.inspector()`, the
  extensions query with `tabrion.postgres.extension_status` and the SQLite facts with `tabrion.sqlite.diagnose`
  (03 §6; I-4 of `stu-010-review.md`); keep `test_explorer.py` unchanged as the acceptance.
- **STU-014 diagnostics:** replace `tabrion_compat.check` (the 0.1 collectors on the driver connection) with
  `tabrion.check(db)`; keep `test_diagnostics.py` and the list of session findings Studio leaves out.
- **STU-013 data viewer:** it uses the same `tabrion_compat.stream` and `Cancel` (one statement per page, with the
  profile's timeout), so the swap covers it; keep `test_viewer.py` (the `LIMIT` spy) unchanged as its acceptance.
  The viewer's statements are SQLAlchemy Core compiled for the psycopg and pysqlite (named) dialects; check that
  0.2's `stream` takes such text and its parameters as they are.
- **Connections and running queries:** closing a connection (`DELETE /api/connections`, a stale profile) does not
  cancel its running queries; they end at their timeout or with the connection's error. A connection's pool has 2
  connections, shared by its queries (at most 2), the explorer and the health check; a third user waits up to the
  pool timeout (10 s). Decide with STU-012 whether closing should cancel first and whether the pool should grow.
  Such a query's session is closed when it comes back, not pooled (the unclosed-database ResourceWarning of
  `stu-011-leak-delta-review.md`; tabrion 0.2's close semantics close the last race of its F-5).
- **The page's heartbeat:** the earlier plan let STU-011 replace the polling heartbeat with SSE. The STU-011 stream
  is per query (behind the session, CSRF and Origin checks like every API route); moving the heartbeat onto a
  long-lived stream is for STU-012, when the page reads streams.

### TD-011 · STU-008 parts that wait for STU-009 or a later package

- **Priority:** P2 Medium (was P1 until the connection test and Connect were done) · **Status:** Open · **Refs:**
  08 §4.2, 03 §2 A, §6
- **Connection test and "Connect": done** in the STU-008 connect PR #42. The test is `POST /api/connection-tests`
  (STU-009's route, not doc 03's `POST /profiles/{id}/test`); its facts come from a catalog query of Studio's own,
  not from `tabrion.postgres.role_info`, which needs the driver connection (`docs/architecture/connections.md`, "The
  connection test"). Deviation from 08 §4.2: the assistant tests after saving ("Test the connection after saving",
  on by default), not before, so a typed password still reaches only the secret route (OA-010). D-02 is asked once
  per profile and page session; a reload asks again. Follow-ups are TD-034.
- **"Save in: Project"** of the assistant is not offered: Studio 1.0 writes only its own files. A project profile is
  edited in `.tabrion/studio.toml`; the manager says so.
- **Pasting a `postgresql://` URL: done** in the STU-008 connect PR #42, parsed in the page
  (`src/views/connections/postgresUrl.ts`): host, port, database, user and `sslmode` fill the form, the password moves
  to the password field of step 2 (keyring or session), the URL is cleared; anything else refuses the whole URL. The
  open connection's `url` (from `tabrion.safe_url`) is not shown in the page yet.
- **"Browse…" for a SQLite file** needs a file dialog in the backend; the path is typed until then.
- **Renaming a profile** (it would move its keyring entry) is refused (`config.rename_unsupported`); delete and
  create instead.
- **Favorites and recently opened** are a 1.0 feature (03 §2 A).
- **The `openapi in sync` CI job** (the first API types for the frontend) changes `.github/` and needs a
  workflow-change review (OA-003); the frontend parses the answers defensively (`src/api/profiles.ts`,
  `src/api/connections.ts`) until then. Recorded again in the STU-008 connect PR #42, not done there (no `.github`
  change in a feature PR).
- **Language and theme in `settings.toml`:** done in the appearance PR (TD-021).
- **PR #13 review (`stu-pr12-14-review.md`, accepted; open follow-ups):**
  - **L-1:** in the assistant, `secretKept` hides `secretProblem`. When a retarget could neither remove the old
    keyring entry nor store the new password, the page only says the old one was kept. Show both.
  - **I-1:** no test pins the `_encodable` check of `parse_secret_ref` for `file:` (mutant M6).
  - **I-2:** test gaps behind mutants M8 to M11: assert the keyring warning record, an unreadable file during the
    recheck, the settings recheck, and `secret_removed: true` for an update that keeps the target.
  - **I-3:** keyring entries saved before `sslmode` joined the fingerprint are no longer found (fails safe: the
    password is asked again, and the old entry stays). Mention it in the release notes if an STU-008 build was ever
    handed out.
- **Concurrent edits (I-3 of `stu-008-review.md`):** a save re-reads the file just before `os.replace`, but takes no
  lock; an edit within the replace itself is lost. A lock file would close it, if hand edits during a save matter.

### TD-020 · STU-052 parts that wait for the SQL editor and the explorer

- **Priority:** P2 Medium · **Status:** Done (STU-020 page PR) · **Refs:** 08 sections 7 and 11 (STU-052, depends on STU-012),
  `docs/architecture/frontend.md` ("Keyboard")
- **Done in the STU-052 PR:** the shortcut table with platform keys, the command palette, the shortcut help and
  F6/Shift+F6 over the shell's areas; Vitest and keyboard-only Playwright tests for the views that exist.
- **Editor shortcuts** (08 section 7): run (⌘↵ / Ctrl+Enter), run script, cancel (⌘. / Ctrl+.; Esc in the result),
  new editor tab (⌘T is the browser's: Alt+T, the palette shows the shortcut that works), close tab, save script,
  format (1.0), explorer filter (⌘P / Ctrl+P), history (1.0). Add them to `SHORTCUTS`, the help and the palette with
  STU-011, STU-012 and STU-020; F6 then also cycles explorer, editor and result.
- **Done in the STU-012 PR:** run (⌘↵ / Ctrl+Enter), run script (⇧⌘↵ / Shift+Ctrl+Enter), cancel (⌘. / Ctrl+.;
  Esc in the grid), new tab (Alt+T), close tab (Alt+W: ⌘W closes the browser tab and ⌥⌘W closes the other tabs in
  Safari, so 08's ⌥⌘W is not used), save script (⌘S / Ctrl+S) in `SHORTCUTS`, the help and the palette; keyboard-only
  query and cancel in `e2e/editor.spec.ts`. Still open: the explorer filter (STU-020), format and history (1.0), F6
  between explorer, editor and result (TD-033).
- **Acceptance "keyboard-only E2E for every main flow"** needs those flows (connect, explorer, query, cancel); query
  and cancel are covered since STU-012, connect and explorer follow with their pages.
- **Done in the STU-052 rest PR:** F6 and Shift+F6 step through the panes of the main area (`data-region` on the
  explorer's tree and details and on the SQL editor's text, parameters and result; F6 stops at the areas that hold no
  other area, and enters a pane on its `data-region-entry`), and `e2e/keyboard.spec.ts` runs every main flow keyboard
  only from the page's start, without a click: connect with the test and D-02, the explorer, a query with a
  parameter, a cancel with Escape in the result and with Ctrl+. in the SQL text. Still open: format and history
  (1.0).
- **Done in the STU-020 page PR:** Format (⌥⇧F / Alt+Shift+F) and the query history (⌘Y on a Mac, Ctrl+H elsewhere,
  `MAC_SHORTCUTS`) in `SHORTCUTS`, the help, the palette ("Go to Query history" shows its keys) and keyboard-only in
  `e2e/history.spec.ts`; the explorer filter came with STU-010.
- **Required e2e suite:** done in the CI e2e-list PR: the step "Every suite ran and none was skipped" requires every
  spec in `frontend/e2e` but `screenshots.spec.ts`, `keyboard.spec.ts` included.
- **Review of #16 (`stu-052-td021-review.md`, accepted):** L-1 (German palette says "Darstellung" like the settings
  view), L-2 (README: "no PyPI release is needed for now; a later publication needs a new decision and G-R") and L-3
  (tests for IME composition, a shortcut another handler took, Escape not reaching the page; mutants K9, K12, K19) are
  done in the STU-052 follow-up PR. Open Info items: I-1 (F6 replaces the browser's F6 inside Studio; Ctrl+L still
  works), I-2 (move the focus to `#main` after "Go to …", with STU-053), I-3 (the docs sentence on F6's "first
  control" applies to header and navigation only), I-4 ("Umschalt" instead of "Shift" in German, optional).

### TD-021 · Language, theme and density in `settings.toml` (from TD-011)

- **Priority:** P2 Medium · **Status:** Open (#17 merged as `061db9d`; only I-5 and I-6 left) · **Refs:** 03 section 4.3 (`settings.toml`: UI
  settings, language), 08 section 4.10, `docs/architecture/configuration.md`
- **Problem:** Studio listens on a free port, and a browser keeps localStorage per origin (port included). The
  language, theme and density chosen in the settings were therefore forgotten at the next start.
- **Done in the appearance PR:** `settings.toml` gains `density` (`compact`, `normal`; schema, writer and
  `PUT /api/settings`). The page saves each choice at once (only that key, a fresh digest, one retry when stale), never
  changes a file with issues or a hand-written one from there (the choice then stays in this browser and the view
  says why), and applies the file's non-default values when it opens. Tests: `frontend/src/appearance.test.tsx`, the
  real-server test in `frontend/e2e/backend.spec.ts`, and the schema, writer and API tests.
- **Done after the review (`stu-052-td021-review.md`):** C-1, the palette's theme, density and language commands go
  through `chooseTheme`, `chooseDensity` and `chooseLanguage`, so they are saved too (pinned by a Vitest); M-1, the
  settings form reads the new digest itself when an appearance choice was saved since it read the file, and keeps
  what was typed on `config_stale`; L-4, the note for a hand-written file says what to do; L-5, a fake server that
  checks the digest pins the save queue; L-6, the real-server test removes its `settings.toml` again.
- **D-1 (Low, `stu-052-td021-review.md`), done in the STU-053 PR:** the palette test ran only "density normal", so a
  theme command that skipped `chooseTheme` passed; it now runs the theme, density and language commands and checks
  each saved key.
- **Open (Info):** I-5 (a warnings-only file is refused by the server, so the note says "could not be saved"), I-6 (an
  older build reports `density` as an unknown key; a release-notes line).

### TD-022 · STU-053 parts that wait for later packages or a person

- **Priority:** P2 Medium · **Status:** Open · **Refs:** 08 section 9, 09 STU-053 (depends on STU-050),
  `docs/architecture/accessibility.md`
- **Done in the STU-053 PR:** the WCAG 2.2 AA checklist for the current shell (no open violation), the fixes F-1
  (reflow at 320 px of the PROD header and the tabs), F-2 (24 px targets in D-01), F-3 (the connection switcher at
  320 px, with titles for names cut with "…"), F-4 (tooltips hoverable and dismissible with Escape, and no click
  through a tooltip shown for the keyboard focus) and F-5 (the logo link's name below 720 px), the focus on the main area after "Go to …" in the
  palette (I-2 of `stu-052-td021-review.md`), and `frontend/e2e/a11y-audit.spec.ts` (target size, reflow in en and de, text spacing, focus visible and not
  obscured, reduced motion, landmarks, tooltips).
- **Review of the STU-053 PR (`stu-053-review.md`):** L-1 (a click beside a focused link followed it through its
  tooltip), L-2 (header wrap, mark size and switcher name pinned at 320 px), L-3 (the header icon-button tooltip's
  Escape and no-gap rules tested) and L-4 (`title` on cut names; the text-spacing comment replaced by a check) are
  done. The redundant rules `.brand { flex: none }` and `.switcher > * { min-width: 0 }` were removed, since no test
  could tell them apart (the header wraps before it would shrink the brand). I-1: refresh the report's
  "Chromium 1243" line when CI's Chromium runs it.
- **Screen-reader sample** (VoiceOver on macOS, NVDA on Windows): needs a person on those desktops (owner or QA-S1).
- **Done in the STU-012 PR:** the result grid (`role="grid"`, `aria-rowcount`, `aria-rowindex`), the live region for
  the query status and the SQL editor (CodeMirror; Tab leaves it, no tab trap), with the editor page in `a11y.spec.ts`
  (light/dark, en/de) and keyboard tests in `e2e/editor.spec.ts`.
- **Done in the second STU-053 PR (the work pages):** the explorer tree, the data viewer's grid, the DDL and
  Policies tabs, Count rows, the export dialog, the diagnostics report and D-02 in the connection manager. axe in
  light/dark and en/de runs in each page's spec; `a11y-audit.spec.ts` adds for each page state target size, reflow
  at 320 px and 200 % (640 px), the focus ring on every Tab stop (an open dialog keeps every stop), text spacing,
  reduced motion and a live region for each action's result. F-6 (the export dialog's 21 px choices, 2.5.8) is
  fixed; D-1 records why grid cells cut with "…" have no `title` (the cell detail shows the whole value).
- **With STU-023 and STU-016:** the ER diagram's table alternative and the STU-016 pages. Add their states to the
  pages' axe tests and to `WORK_PAGES` in `a11y-audit.spec.ts`. Done for STU-023 in the STU-023 PR: axe of the
  diagram, its choice and its table alternative in `er-diagram.spec.ts` (light/dark, en/de), and the diagram in
  `WORK_PAGES`.
- **Firefox and WebKit** runs of `a11y.spec.ts` and `a11y-audit.spec.ts`: done in the jobs `e2e (firefox)` and
  `e2e (webkit)` (PR #59, green with PR #63, required with PR #67; TD-047).
- **Required e2e suite:** done in the CI e2e-list PR: `a11y-audit.spec.ts` must pass without a skip, like every spec
  in `frontend/e2e` but `screenshots.spec.ts`.
- **Final report:** the STU-053 acceptance ("report without open WCAG 2.2 AA violations") is met after STU-050 over
  every MVP flow.

### TD-023 · User guide parts that wait for later packages (STU-017)

- **Priority:** P2 Medium · **Status:** Partly done (the tag and I-3 open) · **Refs:** 03 section 10 (STU-017:
  installation, start, security model, troubleshooting), 09 STU-017 (depends on STU-016), `docs/guide/`,
  `docs/development/docs-checklist.md`
- **Done in the user-guide PR:** `docs/guide/` for the features that exist: installation from a Git commit (no PyPI,
  owner decision 2026-11-08), start and stop with every option (pinned by `tests/unit/test_user_guide.py`),
  connections, the trust dialog and passwords, settings and keyboard, the security model in short, troubleshooting.
- **Done in the STU-012 PR:** `guide/sql-editor.md` (running, parameters, the grid, cancel, drafts, shortcuts).
- **Done in the STU-050 PR:** the pages that landed are checked against the views (explorer with DDL and policies,
  data viewer, SQL editor with export, diagnostics, connections with D-02); new `guide/migrations.md` (status, code
  trust, problems); `guide/security.md` says how PROD profiles are marked and confirmed (D-02); `guide/index.md`
  lists every page and no longer says that the explorer, data viewer and diagnostics are still to come.
- **With STU-009..STU-015:** connecting and the connection test, the explorer, the
  data viewer, diagnostics and migration status; update the "Development version" note on `guide/index.md`.
- **Troubleshooting:** the tables grow with the problem codes those packages add; the guide's command lines are
  checked by the STU-017 clean-install test.
- **With STU-017:** the documentation checklist, the clean-install test on Linux, macOS and Windows, the tag `v0.1.0`
  (no publication, G-R) and the install section then names that tag instead of a commit.
- **Done after the review (`stu-27-review.md`):** L-1, `--print-url` also opens the browser unless `--no-browser` is
  given, so the guide and the troubleshooting row say `--no-browser --print-url`; L-2, the README status names only
  what is on master and takes the PyPI sentence of the publication-policy PR (#22); L-3, the security page lists every
  file Studio writes (settings, profiles, trust records, instance files, sign-in files, logs); L-4, the test wants a
  row in the options table for every option and a real `nav` line (not a comment) for every guide page; I-2, I-4 and
  I-6 (`--version` after the `PATH` note, Node.js 22.22 or later, the final newline of this file).
- **Info items of `stu-27-review.md`:** I-1 needs nothing any more (connecting has arrived, so the present tense is
  right); I-5 done in the STU-017 docs PR (the `--stop` failures in troubleshooting); I-3 open (the keyring row of
  `guide/connections.md` names host, database, user and TLS mode, while the binding covers every target field:
  engine, host, port, database, user, path, sslmode; that page is being edited by the security-fix PRs #65 and #66).
- **Done in the STU-017 docs PR:** the documentation checklist `docs/development/docs-checklist.md`; the
  clean-install test on Linux, by hand from a locally built wheel (2026-11-13, master `7093bea`, passed) and
  automated in `scripts/smoke_wheel.py` (with the Tabrion pin; sign-in, a SQLite profile connected read-only, its
  catalog and rows), which CI runs in the `packaging` job on Linux, macOS and Windows; `guide/installation.md`
  builds a wheel; `guide/troubleshooting.md` covers installing, the views and the `--stop` failures;
  `guide/security.md` names the residual risks (03 §7.3); `guide/settings.md` says when the limits apply and that
  `log_sql` is not used yet (TD-013); the README's status and its install command (no Tabrion pin, so it failed);
  `tests/unit/test_user_guide.py` checks that every documented install command carries the pin.
- **Done in the STU-028 PR:** the 1.0 features checked against the guide (history, favorites, scripts, Format,
  completion, the server-kept drafts; D-24 of the docs checklist), the guide's index and the README name them;
  `guide/installation.md` has a "pipx or uv" section (`uv tool install` with the lock's versions, pipx with the
  Tabrion pin only; neither checks the lock's hashes; both passed on Linux on 2026-11-13) and says that no Git-URL install works (the wording of 03 §4.6, also noted in
  ADR 0001); the 1.0 release steps are in `docs/development/release-checklist.md`.
- **Open:** the tag `v0.1.0` (after G-S1, no publication, G-R), then the install section names the tag instead of a
  commit; a clean-install run by hand on macOS and Windows and in Firefox, Safari and Edge (TD-047); the guide's PowerShell
  commands run by hand on Windows; for 1.0 (03 §8), the clean install with pipx and uv on macOS and Windows (Linux
  done in the STU-028 PR).
- **Review `stu-017-docs-review.md` (accepted with conditions):** L-1 (npm only warns with `EBADENGINE`), L-2 (the
  minimum OS versions), L-3 (PowerShell blocks), L-4 (the smoke script hides the sign-in link on every path), L-5
  (the security page keeps programs running as you out of its promise), I-1, I-2, I-4 and I-5 done; I-3 is a stale
  comment in `.github/workflows/ci.yml` (separate workflow review); I-7 is the pipx/uv item above.

### TD-035 · STU-022 DDL: what pg_dump writes and Studio's DDL does not (TABRION STU-022)

- **Priority:** P3 Low · **Status:** Open (STU-022 PR #38) · **Refs:** 03 section 2 B ("DDL anzeigen
  (rekonstruiert über Inspector + `pg_get_*def`)"), `services/definitions.py`, `docs/architecture/explorer.md`
- **Left out on purpose** (like pg_dump's `--no-owner --no-privileges`): `ALTER ... OWNER TO` (the answer has
  `owner`) and `GRANT`/`REVOKE`. Functions and procedures (doc 03: release S), so a trigger's function is not in the
  table's DDL.
- **Done in the review fix round:** rules, `SET NOT NULL` on a child only, `REPLICA IDENTITY`, `CLUSTER ON`, column
  `SET STATISTICS`/`SET (...)`/`SET STORAGE`/`SET COMPRESSION`, the materialized view's `WITH` line; catalog reads use a
  fixed search_path, so expressions are qualified as pg_dump qualifies them and the acceptance covers `public` too; a
  policy's DDL lists its roles in catalog order like pg_dump (the structured list stays sorted).
- **Not yet:** types and domains (not objects of the catalog page); `SET default_tablespace` and a table access method
  other than `heap`; the per-column `OPTIONS` of a foreign table (a note says so); security labels; publication and
  statistics objects; PostgreSQL 18's virtual generated columns (written without `STORED`, untested) and its named
  `NOT NULL` constraints (contype `n`).
- **Done in the delta reviews:** a materialized view's column settings (`SET STATISTICS` and the others) and its
  `CLUSTER ON`; a view's column default (`ALTER TABLE ONLY v ALTER COLUMN c SET DEFAULT ...`, E-1).
- **CI:** the PostgreSQL 17 job compares with the recorded pg_dump 16.15 output (`tests/security/ddl_fixtures/`) and
  with the runner's own `pg_dump` (18 or later on `ubuntu-26.04`), whose `NOT NULL col` form for a child table (rejected by
  PostgreSQL 17) the test rewrites to the server's form. An exact live comparison with a pg_dump of the server's
  major version in CI needs a pg_dump 17 client in that job (a `.github/` change, not made here); it was run locally
  against PostgreSQL 17.10 with pg_dump 17.10. Done in the CI e2e-list PR: the integration job prints
  `pg_dump --version` and sets `TABRION_STUDIO_TEST_LIVE_PG_DUMP=1`, so the live comparison can never be skipped
  silently if the runner's client is missing or older than the server.

### TD-039 · Queries made when a PostgreSQL connection is opened (TABRION STU-022 review)

- **Priority:** P1 High · **Status:** Done (PR #45) · **Refs:** `stu-022-review.md` (off GitHub, delta
  review D-1), `services/connections.py`, `services/tabrion_compat.py`, `docs/architecture/connections.md`,
  `docs/architecture/explorer.md`
- **What:** the queries SQLAlchemy's dialect sends when a connection is opened (`current_schema()` among them) and
  the driver's hstore type lookup ran under the session's `search_path`, before Studio's own catalog reads
  (STU-022) pin theirs. This predated STU-022.
- **Done:** hardening against object resolution through user schemas: a first `connect` listener fixes
  `search_path` to `pg_catalog, pg_temp` for the setup, a last one restores the session's path (`RESET
  search_path`) and reads the dialect's default schema again; native hstore adaptation is off, so the type lookup is
  not sent. `tests/security/test_connect_path.py` covers it with a database setting that puts another role's schema
  ahead of `pg_catalog`, and checks that user SQL still sees the session's path, on two sessions at once (L-1 of
  `td-039-review.md`); an `hstore` value comes back as the server's text (L-2). Studio's own catalog reads and the
  connection setup share one path constant (`tabrion_compat.CATALOG_SEARCH_PATH`), and the STU-022 regression test
  for planted objects now places them before the connection is opened, so it covers the setup too (I-3); it plants
  the functions the setup calls unqualified as well, so a setup without its fixed path fails it (DL-1 of the delta
  review).
- **Not in scope:** tabrion's diagnostics collectors are fully qualified and stay on the session's path, since they
  report it. User SQL, the viewer and the exports keep the session's path on purpose, and so do the statements that
  Alembic and `tabrion.migrations` send in the migration worker after the setup (I-1), like the viewer's.

### TD-033 · STU-012 SQL editor MVP: what it leaves for later

- **Priority:** P2 Medium · **Status:** Open (STU-012 PR #41); usability only since the drafts part (F-5) is done:
  the owner's G-S1 decision 1 (2026-11-13, option A) keeps the rest (splitter, error positions, column widths, the
  workspace tabs) as ordinary P2 follow-up work · **Refs:** 03 section 2 C, 08 sections 4.4, 5 and 7,
  `docs/architecture/frontend.md` ("SQL editor (STU-012)"), `stu-012-review.md`
- **Done in the review fix round (`stu-012-review.md`, accepted with conditions):** C-1/M-1 and L-1, a profile change
  clears the tab's results, so PROD rows never show under another badge and "Load more" runs only on the profile the
  result came from; C-2/M-2, a run is registered before the connection opens (a second run starts nothing, cancel
  and closing the tab start no query); C-3/M-3, `elapsed_ms` with one decimal (the harness sends one too, the
  real-server tests reject "0 ms" and expect at least 0.4 s for a cancel after 0.5 s); C-4/M-4, the row cache is
  bounded in bytes (64 MB estimated, chunks of 50 rows, fetches sized by the rows seen) as well as in rows, with
  wide-row tests in the harness and against the real server at `max_result_mb = 200` that fail without the byte
  bound; L-2 the shortcut description inside the editor's shadow root; L-3 the placeholder rules of
  `services/sqltext.py` (below); L-4 D-02 names the profile's own timeout; L-5 drafts per project and merged by tab;
  L-6 row pages checked (offset, row count) and asked for again up to 3 times; L-7 a click keeps the focus in the
  grid; L-8 the `SHORTCUTS` comment. Tests for the review's surviving mutants: a script stopping at a failed
  statement, the draft size cap, the row index, the duration parser. Info: a Tab-exit e2e was added.
- **Done after delta review 2 (C-5):** before a new connection (the first one, and again after
  `studio.connection_stale`) the editor reads the profile from the server again. When its environment or target
  changed since the page read it, nothing is opened, the tab's results and connection for it go, the page takes the
  new profile and says so; the next run asks D-02 for a PROD profile through the shared guard. As a backstop, an
  open answer whose `environment` differs is closed again (`DELETE /api/connections`) and nothing runs. Vitest for
  both paths and the backstop; `e2e/editor-backend.spec.ts` changes profiles to PROD through `PUT /api/profiles`
  while the editor is open. A server-side check for every page comes from the STU-008 lane; whether the connection
  manager's own list has the same staleness is for that lane too (`stu-012-review.md`, "To check for #42").
- **Done after delta review 3 (L-9):** the backstop compares the server's answer with the profile that passed D-02
  (not the one read again), and an answer without an environment counts as a mismatch. When the profile list cannot
  be read again, nothing is opened: Vitest, and `e2e/editor-backend.spec.ts` with `/api/profiles` failing (no
  `POST /api/connections`). The test fake's connection answer names the profile's environment, like the server's.
- **Drafts in the state database (done, F-5 drafts PR; review F-5):** the tabs' drafts live in `state.sqlite`
  (schema version 2, table `drafts`, one document per project) behind `GET`/`PUT /api/drafts` with the session and
  CSRF checks; a tab with an unknown field is refused, so a parameter value has no place in a draft; caps of 32 tabs,
  512 K characters per text and 2 M in all. Pages of one project merge by tab on the server (`known`). Nothing is
  written to browser storage; drafts of earlier versions in `localStorage` are moved to the server once and every
  draft key of this project is removed afterwards (other projects' keys stay). `[history] retention_days` applies to
  drafts, `enabled = false` and "Delete history" do not (`docs/architecture/history-and-scripts.md`, "Editor
  drafts"). Review `f5-drafts-review.md` (accepted with conditions): finding 1 (lone surrogate refused, not a 500),
  2 (write-ahead log checkpointed after a save that removed text), 3 (only this project's keys removed, and only when
  all old drafts fit), 4 (structure scan before parsing), 5 (retention wording in the guide), 6 (failed migration and
  no downgrade documented) and 7 (failed-migration test, `expect.poll` for the e2e waits) done. Delta review on
  3feb437: N-1 (the structure scan was not linear in the worst case; now one linear pass, tested at the body
  cap within a process-time bound and against random documents) and N-2 (the fit check of old drafts covers both keys
  together) done. Tests: `tests/security/test_drafts.py`,
  `src/views/editor/drafts.test.ts`, `EditorView.test.tsx`, `e2e/editor.spec.ts`, `e2e/drafts-backend.spec.ts`.
- **Statement splitting:** the page splits scripts with its own scanner (`sqlScan.ts`) instead of
  `tabrion.sql.split_statements`; the server checks every statement again, so a different reading is refused, never
  run as something else. Replace it with a server route (or tabrion 0.2's splitter) when one exists. The page now
  finds placeholders with the server's rules (`services/sqltext.py`: names of at most 63 characters, not running on
  into a word character, which includes `$` and non-ASCII; `--` ends at `\n` or `\r`; a non-ASCII word followed by
  `$x$` is one word). Known differences that remain, each ending in a refusal: the server's splitter knows no
  dialect, so SQLite `[a;b]`, a backtick identifier with `;` and a SQLite comment that looks nested are split by the
  server (`studio.statement_multiple`) but not by the page; the page nests block comments only for PostgreSQL.
- **Error positions:** `db.<kind>` problems carry no position yet (TD-013 follow-up), so the editor marks no error
  location.
- **Grid:** no column resizing, sorting or "load in full" for a cut cell (03 section 5.3; the data viewer has it);
  TanStack Table was not needed for the MVP grid and is not added.
- **Shell:** the shell's workspace tabs and the connection in the header (TD-011) are not filled by the editor; a
  PROD tab shows its environment on the page only. F6 cycles editor, parameters and result since the STU-052 rest PR
  (TD-020).
- **From TD-013:** the `max_rows` default stays 10 000; the editor asks for 1 000 rows first and offers "Load up to
  N rows" with the setting (tests set 100 000). The `EXPLAIN` cost warning for prod profiles, closing a connection
  that has running queries, and the heartbeat on a stream are open.
- **Bundle:** the editor chunk is about 520 kB (Vite warns above 500 kB); it loads only on `/sql`.

### TD-037 · STU-012: the editor suites in the e2e job's required list

- **Priority:** P2 Medium · **Status:** Done (CI e2e-list PR, workflow-change review; review `stu-012-review.md`) ·
  **Refs:** `.github/workflows/ci.yml` (job `e2e`), OA-003
- **Done:** the step "Every suite ran and none was skipped" now requires every test file Playwright runs from
  `frontend/e2e` (its default `testMatch`, subfolders included) except `screenshots.spec.ts` (it takes screenshots
  only when `STUDIO_SCREENSHOTS` names a folder), so both editor suites, and any spec added later, must pass with no
  test skipped; `explorer-definitions.spec.ts` (STU-022 page, #54) is required that way since it reached master.
- **Problem:** `e2e/editor.spec.ts` and `e2e/editor-backend.spec.ts` run in the CI job `e2e`, but the step "Every
  suite ran and none was skipped" does not list them, so a skipped editor suite would not fail the job. Adding them
  changes `.github/workflows/ci.yml` and needs a workflow-change review (OA-003), like `keyboard.spec.ts` (TD-020) and
  `a11y-audit.spec.ts` (TD-022). The STU-012 PR does not touch `.github/`.
- **What runs already:** `npm run e2e` runs every spec, and the job fails on any unexpected result; the review saw
  both editor suites pass in CI at `7acfe6b`. Only the skip detection is missing.

### TD-046 · STU-050 UX acceptance: what the MVP does not meet yet

- **Priority:** P2 Medium · **Status:** Done (recorded 2026-11-13 with the STU-050 PR; each item decided for 1.0 in the
  STU-054 PR, the open ones moved to TD-060) · **Refs:** 08 sections 3,
  4.1, 4.3, 4.4, 4.8 and 11 (QA-S1), 09 STU-050, `docs/architecture/ux-acceptance.md` ("Open items")
- **B-1, 4.1:** no project chooser, recently opened list or "Open SQLite file" on the start page. Studio serves one
  project folder per process; "recently opened" is 1.0 (03 section 2 A). The start page now says what to do next.
- **B-3, 4.8:** the hint "Studio does not run migrations. Command: `alembic upgrade head`" with a copy button.
- **B-4, section 3:** the connection switcher can show a latency but showed none after Connect; the status bar's
  running queries (count, a list with cancel) and the last query's rows and time (role and database are TD-034).
- **B-5, QA-S1:** the 30-minute usability session with the owner on the reference apps; it also judges "without
  documentation" of the first-start flow (the automated part runs in about 4 s of the 60 s).
- **B-6, 4.4:** when a run stops at a missing parameter, the focus moves to the parameter field but CodeMirror's
  completion list stays open over the editor. Done in the STU-020 page PR: the list closes when the field takes the focus.
- **B-7, 4.3:** at 1280 × 800 the explorer's connection card fills the upper half of the page, so the tree and the
  detail start below the fold; 08 keeps the explorer as a sidebar beside the detail.
- **Not here:** the "Connection lost" banner (TD-034), Firefox and WebKit (TD-047), the
  screen-reader sample (TD-022).
- **Decided for 1.0 (STU-054 PR, `ux-acceptance.md` "Open items"):** B-2 done (TD-047) and B-6 done (STU-020 page
  PR). B-3 done: an Alembic status with pending revisions shows the command (`alembic upgrade head`, `heads` with more
  than one head) with **Copy command**, en/de, Vitest and axe. B-1 not built: a project chooser does not fit one
  project per process, "Open SQLite file" is the connection manager's SQLite path; a deviation from 08 section 4.1
  that the owner accepted on 2026-11-13 (TD-060, decision (d)). B-4 (the header's latency, the status bar's running queries and last query) and
  B-7 (the explorer's layout at 1280 × 800) are more than a fix: the first needs the connection watch to feed the
  header and a query state shared by the views, the second the workspace layout of TD-041 and TD-052; both moved to
  TD-060. B-5 (QA-S1) needs the owner and is now the 1.0 session (TD-060).

### TD-048 · STU-016 review F-1..F-3: read-only path, SQLite functions, side-effect functions

- **Priority:** P0 (G-S1) · **Status:** Done (PR #66; independent re-review accepted at `2a8a229`) · **Refs:**
  STU-016 independent security review (F-1 High, F-2 Medium, F-3 Low, F-9 in part; the report with its reproductions stays with the planning
  documents), SQ-05, SQ-07, T-06, T-13, `docs/architecture/connections.md`, `docs/architecture/diagnostics.md`
- **Done in the PR:** F-1: `check_statement` refuses any `INTO` outside literals, comments and quoted identifiers in
  every lexing, and a number directly followed by a word character;
  the PostgreSQL pool reset runs `DISCARD TEMP`; `STU-DX-003` reports schema `CREATE` (warning, error on PROD) and
  database `TEMP` (info on its own). F-2: the SQLite authorizer allows only SQLite's built-in reading functions
  (`fts3_tokenizer` and the rest denied), and every SQLite session sets `trusted_schema = OFF`. F-3: the statement
  check refuses the PostgreSQL functions with effects outside the transaction, plus `connectby`, `xpath_table` and
  the BRIN/GIN index maintenance functions (list and reasons in `connections.md`). The prep page's SQ-05, T-06 and T-13 rows and its residual-risk line say what the review
  found. Tests in `test_read_only.py`, `test_queries.py`, `test_exports.py` and `test_diagnostics.py`, the
  PostgreSQL ones in the PostgreSQL 17 CI job.
- **Re-review:** done on master `2a8a229` (the merge of PR #66) and accepted, with F-4, F-8 and F-9 of PR #65; the
  owner's decisions for G-S1 are listed in `security-review-prep.md`. The message for a denied SQLite function is
  TD-053.
- **Limits that stay:**
  the text check is best effort, so a function of the database or of an unknown extension with side effects is
  still limited only by the role; a SQLite column named `into` must be written `"into"` (`[into]` and backticks are
  refused, since the PostgreSQL lexings read them as the keyword); `TEMP` stays an info, not a warning, since
  PostgreSQL grants it to `PUBLIC` by default; hexadecimal, octal, binary and underscore number forms are refused
  (write numbers in decimal).
- **Owner decisions (G-S1, 2026-11-13, option A each):** decision 8, side-effect functions that the database itself
  defines (for example one a view calls) are an accepted residual risk: the database role is the boundary, the
  guide shows how to create a really read-only role (`guide/connections.md`, "A read-only role for PostgreSQL") and
  the diagnostics warn about a role that can write; no check of the database's function definitions. Decision 9,
  the hexadecimal and underscore number forms stay refused (a usability limit with a clear message). Decision 10,
  a non-public report to the PostgreSQL security team about the cause of F-1 is prepared only as a confidential
  draft for the owner, kept with the planning documents; nothing is sent without the owner's approval and under the
  owner's name or account, and no details go into this repository or GitHub.

### TD-047 · STU-050: the e2e suites on Firefox and WebKit

- **Priority:** P1 High · **Status:** Done (PR #59, PR #63, PR #67) · **Refs:** 08 sections 10 and 11 (STU-050: every flow green in
  E2E on Chromium, Firefox and WebKit), 09 STU-050, QA-9 row, TD-007, `ci-firefox-webkit-workflow-review.md`
- **Done in PR #59:** `playwright.config.ts` picks the browsers from `STUDIO_E2E_BROWSERS` (default `chromium`, so the
  job `e2e` is unchanged); the CI matrix job `e2e (firefox)` / `e2e (webkit)` runs the same suites against the same
  wheel with the browser of the locked Playwright version. Its gate is the e2e job's, except that a test may skip
  with a `chromium only:` reason (the five heap measurements through CDP) and never with a fixme or fail annotation.
  Both jobs were `continue-on-error` and listed as advisory in `.github/ci-manifest.json` until PR #67.
- **Done in PR #67 (a `.github/` change with its own workflow-change review):** both jobs are required. They have no
  `continue-on-error` any more and left the `advisory` list of `.github/ci-manifest.json`, so `ci-ok` counts them
  like `e2e`; timeouts and secrets are unchanged. The step summary shows the "Chromium only"
  count per suite (expected 2 in `editor.spec.ts`, 2 in `editor-backend.spec.ts`, 1 in `viewer-backend.spec.ts`).
- **Locally:** the agent sandbox has only a Chromium build, so these runs exist only in CI.
- **First CI run (`9741eb7`, run 43343286134):** Firefox 251 passed, 3 failed, 35 skipped (30 screenshots and the 5
  "chromium only" heap tests); WebKit 236 passed, 7 failed, 32 skipped (screenshots and "chromium only"), the rest of
  its serial suites did not run after a failure. The gate step did not run, because the test step failed first, so
  F-3 (the "chromium only" accounting from the annotations) still needs a run with every test passing. Root causes,
  each to fix in its own follow-up, without a skip unless the feature is Chromium-only:
  1. **WebKit, CSP:** "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear
     in the style-src directive" is logged once each time a `<select>` is created (the diagnostics, explorer, data
     tab, editor and parameter panel selects; trace times match each one). It fails four real-server tests
     (`backend.spec.ts:331`, `editor-backend.spec.ts:164`, `explorer-reference.spec.ts:132`,
     `viewer-backend.spec.ts:191`). Pages without a `<select>` have none, and Chromium and Firefox have none. The
     likely cause is WebKit's own user-agent styles of the select; confirm with a minimal page under the same CSP
     before a test treats the message as the browser's.
  2. **Firefox, keyboard:** `editor.spec.ts:125`. After Tab the editor keeps the focus and its text changes, so Tab
     in the SQL editor (CodeMirror in a shadow root, `indentWithTab: false`) seems to insert a tab instead of moving
     on. This is a possible keyboard trap (WCAG 2.1.2), so it is a product bug to confirm and fix, not a test change.
  3. **Firefox and WebKit, test:** `a11y-audit.spec.ts:334` reaches `:focus-visible` with `Shift` plus
     `element.focus()` after a mouse click; only Chromium then treats the focus as keyboard focus. Reach it with Tab.
  4. **Firefox and WebKit, test:** `explorer-definitions.spec.ts:64` calls `grantPermissions(['clipboard-read',
     'clipboard-write'])`, which only Chromium knows. Check Copy DDL another way there, for example through the
     page's copy result or a stub of `navigator.clipboard.writeText`.
  5. **WebKit, test:** `editor.spec.ts:327` expects the saved script as "Query 2.sql"; WebKit's download reports
     "Query_2.sql". Assert the name the page offers (the link's `download`) and accept the browser's spelling of it.
- **Done in PR #63** (each root cause above; no skip, no timeout raised, nothing quarantined):
  1. **WebKit, CSP: the browser's own, but its wording hides real reports.** `e2e/csp.spec.ts` loads a page with
     nothing but Studio's CSP (read from `security/access.py`) and adds one element at a time. In WebKit a plain
     `<select>` created by script logs the report once, with a `securitypolicyviolation` event `style-src-attr` whose
     target is the select itself, which has no `style` attribute: WebKit's own styling of the control. Studio's
     markup has none (its styles are CSS files and constructed style sheets), and Chromium and Firefox report nothing.
     The same run showed that WebKit words a refused `style` attribute or `<style>` of the page with exactly the same
     text, so a filter by text would have hidden real violations. The shared watcher `recordViolations`
     (`e2e/support.ts`, used by every real-server suite) therefore
     leaves a WebKit report out only when it is paired with such an event (a `style-src-attr` violation of a
     `<select>` without a `style` attribute, marked by an init script); `expectNoViolations` waits until the page's
     events have arrived. `csp.spec.ts` checks in every browser that a refused `<style>` and `style` attribute are
     heard and that the select's pair is the only exception; when a WebKit update stops reporting it, the test fails
     and the exception goes.
  2. **Firefox, keyboard: no keyboard trap.** The editor's text is unchanged after Tab (the trace of the first run),
     so Tab was not taken by the editor: in the harness nothing focusable follows the editor before a result exists,
     and past the last control Chromium moves to its own UI while headless Firefox has none and keeps the focus. The
     test now runs a query first, so the export button follows, and checks that Tab is not prevented, lands on that
     button, leaves the text as it was, and Shift+Tab returns (stronger than "not focused" before).
  3. **Tooltip test:** `:focus-visible` is reached with Tab (Start link to Connections) and with Shift+Tab from the
     help button back to the palette button, each checked to have the focus.
  4. **Copy DDL:** an init script records the text the page passes to `navigator.clipboard.writeText` on its way to
     the real clipboard (whose answer still decides the page's status); it must equal the exact DDL, once, in every
     browser. Chromium (the only browser with clipboard permissions) also reads the clipboard back and compares the
     exact text (before: a substring).
  5. **Saved script name:** the name the page offers (the link's `download`) must be "Query 2.sql"; WebKit saves it as
     "Query_2.sql", the others as offered; the file holds the tab's SQL.
  6. **Firefox, CSP watcher (L-5 of the PR #62 review):** the watchers matched "Content Security Policy" and "Refused
     to", but Firefox writes "Content-Security-Policy:", so in Firefox they never heard a report and those checks
     passed vacuously. `recordViolations` matches both spellings, and `csp.spec.ts` proves it hears Firefox's reports.
     `ux-acceptance.spec.ts` (PR #62, merged into this branch) uses the shared watcher too.
  7. **Firefox, `keyboard.spec.ts` (from PR #58, after merging master):** "connect from the start page" pressed Tab
     from "Test connection", the page's last control, to reach "Connect" right before it, which works only where the
     focus wraps around through the browser's UI (Chromium). The same headless-Firefox behavior as 2, no trap: the
     test reaches "Connect" with one Shift+Tab (`tabTo` takes the key).
- **After the review `td-047-review.md` (ACCEPTED WITH CONDITIONS):** F-1, `connection-lost-backend.spec.ts` (PR #64)
  had the old watcher (vacuous in Firefox, failing on WebKit's select report): it uses `recordViolations` and
  `expectNoViolations`, so every real-server suite does. F-2, the a11y line of TD-031 updated. F-3, the pairing is
  per element and no longer rests on the console alone: the init script of `recordViolations` keeps every
  `securitypolicyviolation` event of the page in the page, in every browser, except WebKit's own of a `<select>`
  without a `style` attribute, and that only once per select element; `expectNoViolations` requires that list empty
  too, so a violation counts whatever a browser writes to its console. Left: a `style` attribute of a select removed
  before its event is dispatched would pass as WebKit's own (contrived; Chromium and Firefox have no exception).
  `csp.spec.ts` adds a `<select>` with a refused `style` attribute, which must count in every browser. F-4, the Tab
  test also presses Tab without a result (the editor is then the page's last control) and checks it is not
  prevented and the text unchanged.
- **Follow-up (branch `td047b`): a product bug, low contrast for 120 ms after a theme switch.** `e2e (webkit)` of PR #69
  failed once in `editor-backend.spec.ts` "runs a query under the CSP…": axe right after "Theme: Dark" through the
  palette found color contrast on the editor toolbar's button labels. Cause: `.button` has
  `transition: background-color var(--tb-duration-fast)` (120 ms) and no transition on `color`, so on a theme switch
  the label took the new theme at once while the background faded from the old one; the real-server suites run with
  motion on (the harness emulates reduced motion), and whether axe's color rule ran within the 120 ms decided the
  result. Measured in Chromium right after the switch: the primary button at `rgb(140, 151, 209)` behind the dark
  theme's `rgb(14, 16, 19)` label, on its way from the light theme's `rgb(57, 73, 171)`. Fix in the product
  (`src/theme.ts`, `global.css`): the theme attribute changes while `data-theme-switching` turns every transition off
  and a forced style flush computes the new values, so no transition starts. Tests: `a11y-audit.spec.ts` "a theme
  switch starts no transition…" (motion on, both directions: no `transitionrun`, no serious axe violation; it fails
  without the fix) and a Vitest of the attribute sequence. The other axe checks after a theme switch run in the
  harness under reduced motion, and `editor-backend.spec.ts` is the only real-server spec that switches the theme.

### TD-054 · STU-016 review F-7: the code-trust digest covers the project modules

- **Priority:** P2 Medium · **Status:** Done (PR #75); review accepted with condition C-1, delta review with
  condition C-2; both fixed, re-check accepted at `d33f89d` · **Refs:** T-16, review F-7, review `f7-trust-review.md` (C-1, C-2, I-1..I-5), `docs/architecture/migrations.md`,
  `docs/architecture/security-review-prep.md`
- **What:** the migration code trust was bound to `alembic.ini` and the revision files only. Project modules that
  revision files import through `prepend_sys_path` were trusted with them, and a change to them did not ask again.
- **Done in the F-7 PR:**
  - `config/code_trust.py`: the digest also covers the import roots (the `prepend_sys_path` folders inside the
    project) and every `*.py` under them in identifier-named folders, by `/`-separated path relative to the project
    and bytes, sorted. `__pycache__`, bytecode, `node_modules`, `site-packages`, non-identifier folders and virtual
    environments are skipped. Fail closed (`invalid`): more than 5 000 modules, 32 MB of modules, a module over
    256 KB, 50 000 folder entries, or a link that points outside the project; links inside are not followed.
    `prepend_sys_path` with Windows drives is split correctly.
  - Versioned digest (`CODE_TRUST_VERSION` 2): a record of scheme 1 is `changed`, so earlier trust is renewed.
  - `services/migration_worker.py`: the roots are those of the worker's own scan; their finder compiles the hashed
    module bytes and refuses any module that was not hashed (also through a link).
  - API `import_roots` and `module_files`; the trust dialog lists the modules and says what is checked (en/de).
  - Tests in `tests/security/test_migrations.py`: a changed module and a new module take the trust back, skipped
    files do not, the caps, links out of the project, an inside link, the old record, the worker runs only hashed
    modules, Windows path handling; Vitest for the dialog and the parser.
- **Review C-1 (fixed):** the worker left folders that were already on `sys.path` (Studio's environment, also a
  project folder put there by an editable install) to the interpreter's finder. Now `_ProjectGuard`, first on
  `sys.meta_path`, loads any module in the project folder outside Studio's installation (`sys.prefix`,
  `sys.exec_prefix`, their base prefixes, the site folders, the folder that holds `tabrion_studio`) from its hashed
  bytes or refuses it, whichever finder found it; the path hook's own-folder check uses the same installation
  folders instead of every `sys.path` entry. The worker starts with `-S` and runs `site.main()` under the guard, so
  `.pth` import lines and `sitecustomize` are checked too. Studio from a source checkout inside the project: the
  worker does not run (fail closed). Tests with a throwaway virtual environment inside the project: extension
  modules and bytecode reached through a `.pth` folder (inside and outside the import roots), an unhashed package
  and startup imports in a folder the scan skips, an editable meta path finder, and the source checkout case.
- **Delta review C-2 and N-1 (fixed):** a `.pth` file can insert its finder in front of the guard (meson-python and
  scikit-build-core editables): the guard goes back to the front after `site.main()`, and the worker refuses to read
  the revisions when anything is in front of it then. A namespace package from Python's path finder (no loader, no
  origin) passes like one with a `NamespaceLoader`, so a hashed `nsp/mod.py` loads again. I-4 (a finder in Studio's
  environment that loads without a file is trusted as the environment) is documented in T-16. Tests: the editable
  finder appended and in front, a hashed namespace package.
- **Review I-1 (done):** `issue_file` names the file when the state is invalid because of one file (a module over
  256 KB, a link out of the project, a revision file name or link); the page shows it. I-2 (a cache for the scan)
  is not done: the scan stays bounded by its caps.
- **Re-check (accepted at `d33f89d`):** I-5, a finder that trusted code or Studio's environment puts in front of the
  guard while the revisions are read, is documented in T-16 as part of the trusted-code residual.
- **Open:** nothing for F-7. Residual (T-16): what trusted code runs by other means than an import is not
  checked.
- **Owner decision (G-S1, 2026-11-13, decision 3, option A):** the residual is accepted: trusted project code runs
  with the user's rights, as the trust dialog and the guide say; no sandbox (that would be a new work package after
  1.0).

### TD-062 · JR-N1: an e2e test of the PROD retry after a failed reconnect

- **Priority:** P3 Low · **Status:** Done (PR #89; recorded 2026-11-13 with the G-S1 decisions) · **Refs:**
  `joint-regression-2026-11-13.md` JR-N1, G-S1 decision 4, TD-034, TD-038, `docs/architecture/connections.md`
  ("Lost connections and 'Reconnect'")
- **What:** after a lost connection, "Reconnect" on a PROD profile asks D-02. When that attempt fails (the database
  is down), the next attempt in the same page session does not ask again: only a loss resets the confirmation, a
  failed attempt does not. The owner accepted this documented behavior (decision 4, option A).
- **To do:** an e2e test of the PROD case (D-02 on the first "Reconnect", none on the retry after a failed one, D-02
  again after a new loss), in light and dark, en and de like the other connection-lost specs.
- **Done:** `frontend/e2e/connection-lost-prod-retry.spec.ts`, in Chromium against the e2e harness, in light and
  dark, en and de, with axe: D-02 on the first "Reconnect" after the loss; that attempt fails
  (`window.harnessDatabaseDown(true)`: opening a connection answers `db.connection`) and shows "Reconnecting failed";
  the retry asks nothing and sends `confirmed_environment: "prod"` at once (it fails again); with the database back
  the next retry asks nothing either and the explorer follows the new connection; a new loss asks D-02 again. Every
  `POST /api/connections` body is checked (`window.connectionOpens`). Test only: the product behavior is unchanged,
  and the test found no deviation from the documented behavior.

### TD-055 · Test order: Uvicorn's logging config leaked from `test_server.py` into the lifespan canary

- **Priority:** P2 Medium · **Status:** Done (PR #78) · **Refs:** TABRION STU-004 (T-05, STU-T-009),
  `tests/security/test_canary.py`, `docs/architecture/errors-and-logging.md` ("Logging")
- **What:** `test_a_failing_lifespan_of_any_app_logs_no_traceback_text` passed alone and in CI's order but failed
  after `tests/unit` (1 failed / 528 passed on `36702c6`). Cause: `test_info_file_is_withdrawn_before_the_shutdown`
  in `tests/unit/test_server.py` built `uvicorn.Config(app=...)` with Uvicorn's default `log_config`, which runs
  `logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)`: the `uvicorn` logger got its own stderr handler and
  `propagate=False` (and `uvicorn.access` a stdout handler) for the rest of the process. Later Uvicorn records never
  reached the redacting handlers that `configure_logging` puts on the root logger, so the canary found no
  `[traceback text redacted]` in Studio's log, and Uvicorn's own handler printed the lifespan traceback with the
  canary to stderr.
- **Production:** the `tabrion-studio` command was not affected (`serve()` builds its `uvicorn.Config` with
  `log_config=None` and nothing else in the process configures logging), but its redaction depended on that: any
  `uvicorn.Config` with the default `log_config`, or a `dictConfig` of a program that embeds Studio, before or after
  `configure_logging`, would have let Uvicorn print a failed lifespan's traceback text unredacted.
- **Done:** `configure_logging` takes over `logs.OWNED_LOGGERS` (Uvicorn's and asyncio's loggers): removes their own
  handlers, turns propagation on and puts the redaction filters on the loggers themselves, so a record logged at an
  owned logger is redacted before any handler sees it, also one added later (records of child loggers and of other
  loggers: see "Limit"); `LogSetup.close()` restores the earlier state. New canaries
  `test_uvicorns_own_logging_config_from_earlier_does_not_bypass_redaction` and `..._from_later_...` fail without
  the change. Test isolation: the autouse fixture `restore_logging_state` in `tests/conftest.py` restores those
  loggers (handlers, filters, level, propagation, disabled), the root level and `logging.disable` after every test;
  `test_server.py` builds its `uvicorn.Config` with `log_config=None` like `serve()`. The canary passes after
  `tests/unit/test_server.py` and after all of `tests/unit`, and before them; the canary itself is unchanged.
- **Limit (PR #78 review F-1, F-8):** a logger filter runs only for records created at that logger. After a later
  `dictConfig`, a record of a child logger below an owned name (`uvicorn.error.x`, `uvicorn.other`) reaches
  Uvicorn's re-added handler unredacted (pinned by the strict xfail
  `test_a_child_loggers_record_after_a_later_dictconfig_is_not_redacted_by_uvicorns_handler`), and so does a handler
  attached later to a non-owned logger with `propagate=False`. In that scenario `uvicorn` has `propagate=False` again,
  so Uvicorn's records (redacted) no longer reach Studio's log file. Studio's runtime does not hit any of this today:
  `serve()` runs no `dictConfig`, Uvicorn 0.54 logs only at `uvicorn.error`, `uvicorn.access` and `uvicorn.asgi`, and
  asyncio only at `asyncio`. Before TD-055 every `uvicorn.*` record leaked in that scenario. Hardening (a record
  factory, or wrapping the owned loggers' handlers) would be a follow-up TD.

### TD-057 · Export: the next export on a connection could find the slot still taken

- **Priority:** P2 Medium · **Status:** Done (PR #80) · **Refs:** TABRION STU-021,
  `src/tabrion_studio/services/exports.py`, `tests/security/test_exports.py`, `docs/architecture/exports.md`
- **What:** `test_every_formula_start_is_escaped_in_values_and_names[\t]` failed once in a full run and passed
  alone (`studio.export_busy`). Cause: `Exports._produce` put the end marker in the queue, then logged "export
  finished", and only in its `finally` removed the connection from `_running`. A client that read the download to its
  end and at once started the next export on the same connection could land in that gap. With a 0.3 s delay on the
  log call all 10 parametrized cases failed; real users could hit it too (export, then export again at once). The
  error path had the same gap (the error was handed over before the slot was freed).
- **Done:** the worker frees its slot as soon as `OpenConnection.stream` has returned or raised: by then the
  cursor is closed and the `Database.read()` transaction has ended (commit or rollback), so the worker no longer uses
  the connection's session; only after that does the client get the end or the error. No wait in `start()` is
  needed. Each slot now records the export that holds it, and only that export can free it, so a late `finally` of
  an earlier worker never frees a later export's slot; the release in `finally` stays as a backstop for a failure
  before the statement ran, and `start()` frees the slot if the worker thread cannot start. Two exports running at
  once on one connection (and a third in Studio) are still refused. Tests: `test_td057_*` hold the worker at the
  start of its last step (a gated `Timer.cancel`), no sleeps; both failed with `studio.export_busy` before the fix.
- **Review F-1 (PR #80):** the export worker started its timeout timer before its `try`, so a timer thread that
  could not start (no more threads) left the slot taken until restart, never set `_done`, and the client's
  `chunks()` polled forever. The timer now starts inside the `try` (the slot is freed, then the client gets the
  error). The query engine had the same shape: a worker thread that could not start left a `running` query holding a
  slot, and a timer that could not start killed the worker before the query's state was set. `start()` now drops a
  query whose worker did not start, and `_run` starts its timer inside the `try` (the query ends as failed). Tests:
  `test_td057_f1_*` in `test_exports.py` and `test_queries.py` force `Thread.start` or `Timer.start` to fail.
- **Same pattern elsewhere (checked):** the query engine counts a query as running while its `state` is `running`,
  and the worker sets the final state (what the page sees) after `stream` has ended, so a query's slot and its end
  are one change. The data viewer (`Viewer`) and the diagnosis free their slots in `finally` before they return the
  answer, so the client never sees the end first. Studio has no server-side query history (the editor's history is
  in the page). Nothing else to fix.

### TD-056 · tabrion's `DbErrorRedactionFilter` keeps `DETAIL` values in the middle of a line

- **Priority:** P2 Medium · **Status:** Done for Studio (TD-013 PR) · **Owner:** tabrion
  (`tabrion.errors.DbErrorRedactionFilter`) · **Refs:** PR #78 review F-9, TS-001, STU-009, `docs/architecture/errors-and-logging.md`
- **What:** the filter removes `DETAIL:`, `HINT:`, `CONTEXT:` and `[parameters:` parts only where they start a line.
  A one-line message such as `... DETAIL: Key (email)=(...)` that carries no database exception object keeps the
  values; with a line break before `DETAIL:` they are removed. Pre-existing (the same on `e93c4f3`), not part of
  TD-055. Studio's own `RedactionFilter` still never formats an exception's message, so this concerns text that a
  caller puts into the message itself.
- **Next step:** in tabrion, also strip these parts in the middle of a line (with a test); Studio then takes the new
  tabrion pin. Not fixed in Studio.
- **Owner decision (G-S1, 2026-11-13, decision 13, option A):** fixed in tabrion, no second filter in Studio; the
  residual risk is accepted until then, and the fix with the pin update is a G-R precondition (GR-5).
- **Done (TD-013 PR):** tabrion 0.2.0 (`52353da`, tabrion TB-144) removes a `DETAIL:`, `HINT:`, `CONTEXT:` or
  `QUERY:` part in the middle of a line with one blank after the label, also in a record without a database
  exception. Studio pins that commit, and `tests/security/test_canary.py::
  test_a_database_detail_in_the_middle_of_a_line_is_redacted` sends such a one-line message (in the message and as
  an argument, each label) through Studio's log setup and requires the canary to be absent from the file and the
  console; it fails with tabrion 0.1.0. GR-5 is met; no second filter in Studio.

### TD-060 · STU-054 UX acceptance 1.0: what stays open

- **Priority:** P2 Medium · **Status:** Open (recorded 2026-11-13 with the STU-054 PR #84; owner decisions done
  2026-11-13) · **Refs:** 03 sections 5.2, 5.3 and 8, 08 sections 3, 4.1, 4.3, 4.4, 4.9, 5, 6 and 11, 09 STU-054,
  `docs/architecture/ux-acceptance.md` ("Studio 1.0", "Owner decisions on 2026-11-13 (STU-054)", "Open for 1.0"),
  TD-032, TD-046
- **Owner decisions (2026-11-13, all four recommendations accepted, option A):**
  - (a) 03 section 8 (the 1.0 row) asks for "alle Schreibversuche über API 403 (Negativtest)". Refused writes answer
    HTTP 400/405 rather than 403, everywhere: a writing statement is `400 studio.statement_*` before it runs (or a
    `db.*` failure in the read-only database when the text check lets it through), a changing method on a read route
    is `405`, and only the write mode answers 403 (`studio.query_write_unsupported`). Every write is refused and
    nothing is written (`e2e/ux-acceptance-1-0.spec.ts`). The decision is recorded for the STU-026 row of 03 section
    8 too. It is **only** a decision on the error semantics of refused write operations; it is **not** a release or
    security acceptance of STU-026, and the Data Editor stays fully locked behind G-S2.
  - (b) D-07 (EXPLAIN cost warning on PROD) and its setting: after 1.0 (03 section 5.3 calls it optional for 1.0).
  - (c) tabrion's own `TAB-*` finding texts stay English (marked `lang="en"`) until tabrion has a catalog
    (TD-032's decision, confirmed).
  - (d) 08 section 4.1: no project chooser on the start page (TD-046 B-1); the deviation is accepted.
- **Dependency resolved:** TD-032 (the `STU-DX-*` and `STU-BK-*` details and remedies in en/de) is done by PR #85.
- **Still open before STU-054 is accepted:** the manual QA-S1 session (TD-046 B-5) and the other items below.
- **From TD-046:** B-4 (the header's latency after Connect; the status bar's running queries and the last query's rows
  and time, with TD-034), B-5 (QA-S1 with the owner, on the 1.0 build; 08 section 11 asks for one session per
  release), B-7 (the explorer's layout at 1280 × 800, with the workspace routes of TD-041 and TD-052).
- **German-tour exclusion:** the German tour leaves out text marked `lang="en"`. With PR #85 (TD-032) the
  `STU-DX-*` and `STU-BK-*` texts are German without a `lang` attribute, and only tabrion's `TAB-*` findings keep
  `lang="en"` (decision (c)).
- **Source scan limits (review L-1 of PR #84):** `coverage.test.ts` does not see a module constant or a state value
  rendered later, text props of components under other names (`hint`, `description`, `tooltip`) or `t(variable)`;
  an extra scan of the current source found no such hard-coded text. Extend it when a cheap rule appears.
- **Deviation noted:** the query history is its own view (`/history`, Ctrl+H), not a tab beside the results as 08
  section 4.4 sketches it; the view filters by profile and opens a statement in the editor, which a result tab
  could not. Recorded, no change planned.

### TD-058 · STU-053 audit walk: WebKit scrolls a focused element into view one task later

- **Priority:** P2 Medium · **Status:** Done (PR #81) · **Refs:** 09 STU-053, TD-022, TD-047,
  `frontend/e2e/a11y-audit.spec.ts` (`walkFocus`)
- **What failed:** "explorer Policies tab (STU-022): every Tab stop shows the focus ring and is in view" in the CI job
  `e2e (webkit)` of PR #80 (backend export code only; run 43405250149), with `in view: LI:tree-row`. It passed in every
  other WebKit run since the test landed with the STU-053 work-pages PR.
- **Root cause:** a test race. Chromium and Firefox scroll a newly focused element into view inside `focus()`; WebKit's
  `Element::updateFocusAppearance` only calls `LocalFrameView::scheduleScrollToFocusedElement`, which scrolls from a
  zero-delay timer. The walk pressed Tab and measured the focused element in its next `evaluate`; whether that ran
  before or after WebKit's timer depended on the order of the IPC message and the timer on a loaded runner. The trace
  shows it: `#main` was at `scrollTop` 0 (the connection picker's stop had scrolled it up), the focused row was the
  selected `measurement` row, and the snapshot after the measurement still had `#main` at 0. Since STU-023 (PR #72)
  the explorer's "View" group (32 px plus the gap) sits above the workspace, which put that row at 723 px, just below
  the 720 px viewport; before it the row was in view without a scroll, so the walk never depended on the reveal.
  The page is correct: WebKit brings the row into view one task later.
- **Fix:** `walkFocus` waits one task (`setTimeout(…, 0)`) in the page before it measures a stop. A timer set after
  the focus moved runs after WebKit's (timers of equal delay run in the order they were set), so the walk measures the
  scroll position the user sees. No timeout raised, no test skipped. In Chromium the same order was checked by hand:
  a focus with `preventScroll` and a zero-delay `scrollIntoView` reads "out of view" in the same task and "in view"
  one task later.

### TD-053 · SQLite: a denied function surfaces as `db.unknown`, not `db.permission`

- **Priority:** P3 Low · **Status:** Open · **Refs:** re-review of the STU-016 security review at `2a8a229` (an info
  note, not a security issue), TD-048, F-2
- **What:** when the SQLite authorizer denies a function (since F-2 every function outside SQLite's built-in reading
  functions), SQLite reports "not authorized", which Studio maps to the problem `db.unknown`. The statement is
  refused as it should be, but the message is vaguer than the one for a denied table or pragma.
- **Next step:** map SQLite's "not authorized" error to `db.permission`, with a test for a denied function.

### TD-052 · STU-023 ER diagram: what it leaves for later

- **Priority:** P3 Low · **Status:** Open (STU-023 PR) · **Refs:** STU-023, 03 section 2 E, 08 sections 2 and 4.7,
  `docs/architecture/explorer.md` ("ER diagram data", "The page"), TD-011, TD-041, TD-047
- **What it does:** the explorer's **ER diagram** view of one schema: at most 150 tables (a choice under **Tables**),
  laid out by dagre (150 tables in under 2 s, measured in the page and in `layout.perf.test.ts`), shown with
  @xyflow/react 12 (pan, zoom, overview map), a table alternative, SVG and PNG (2×) drawn in the page; one GET
  `.../diagram?schema=` per schema on the explorer's open connection; read-only.
- **Open:**
  - The route `/c/:conn/schema` and the activity bar's "Schema" entry of 08 section 2 come with the workspace routes
    (TD-011, TD-041); until then the diagram is a view of `/explorer`, on the explorer's own profile picker.
  - One diagram shows one schema; keys into other schemas are listed in the table alternative, not drawn. A diagram
    across schemas, and a choice that adds the neighbors of a table, are not there.
  - Lines join tables, not columns, and carry no cardinality marks (08 section 4.7 sketches `1──*`); the key's name
    and columns are in the SVG's line titles and the table alternative.
  - Boxes cannot be moved by hand; **Lay out again** is the only arrangement. A box lists 20 columns.
  - A schema with more than 1000 tables is refused (`studio.diagram_too_large`); its tables are in the tree.
  - `e2e/explorer-reference.spec.ts` does not open the diagram yet (the reference apps' keys in the diagram).
  - Firefox and WebKit: `er-diagram.spec.ts` and the STU-023 test of `backend.spec.ts` run there only in CI (jobs
    `e2e (firefox)` and `e2e (webkit)`, TD-047); the agent sandbox has Chromium only.
- **Review fixes (C-1 to C-3 of the STU-023 review):**
  - C-1: `e2e (webkit)` failed with a `style-src-attr` event at the document, from React's code, when the diagram
    opened. Cause: WebKit's report of its own `<select>` style (TD-047) reaches the select only while it is in the
    document; the diagram, loaded with `React.lazy`, was rendered by Suspense's retry, which React commits a task
    after it makes the elements, so the event of the schema picker went to the document. (A first guess, the
    `<details>` of **Tables**, was wrong: a probe showed WebKit silent for it.) The diagram now loads with `import()`
    and a state update, rendered and committed in one task; `watchSelects` (`e2e/support.ts`) checks in every browser
    that each `<select>` is in the document a task after it is made (it fails with `React.lazy` in Chromium too).
    `expectNoViolations` shows the page's events with the reports when it fails. The CSP and the TD-047 exemption are
    unchanged. **Tables** is a button with `aria-expanded` whose list is rendered only while open (C-3).
  - C-2: the 2 s timing runs alone in one worker after the other unit tests (`layout.perf.test.ts`, Vitest project
    `perf`, both in `npm test`, which CI's `frontend unit` runs), and asserts CPU time under 2 s too.
  - C-3: the 150-table render does less: the choice list is not rendered while closed, **Lay out again** keeps the
    boxes when the layout is the same, and the boxes take their texts from the canvas instead of 150 translation and
    xyflow hooks. No timeout was raised.

### TD-050 · Joint regression JR-N2: `backend.spec.ts` left its temp directory behind

- **Priority:** P3 Low · **Status:** Done (PR #68) · **Refs:** joint regression JR-N2, TD-044 (JR-1)
- **What:** `frontend/e2e/backend.spec.ts` creates `<tmpdir>/studio-e2e-XXXXXX` (project, state, the test databases)
  in `beforeAll`, but its `afterAll` only sent SIGTERM to the server, so every run left that directory behind. The
  other real-server suites already removed theirs.
- **Done in PR #68:** `afterAll` sends SIGTERM, waits for the server to exit (at most 10 s, it may still write its
  state on the way out) and removes the directory. Checked locally (Chromium): 7 passed and no new `studio-e2e-*`
  directory; without the change the same run left one. Directories left by earlier runs are not touched.

### TD-043 · STU-021 page: the export dialog, and what it leaves for later

- **Priority:** P2 Medium · **Status:** Open (recorded 2026-11-12 with the export dialog PR) · **Refs:** 03 sections
  2 D and 6 (`POST /exports`), 08 section 5 ("Export"), 09 STU-021, `docs/architecture/exports.md` ("The page")
- **Done in the export dialog PR:** `frontend/src/api/exports.ts` (the client: a statement with its parameters or a
  table, the stream read chunk by chunk with an `AbortSignal`, the media type checked, a JSON file without its end
  refused, the cut marker read, the server's file name kept only in its form) and
  `frontend/src/components/export/ExportDialog.tsx` (format, the fetched rows or "up to" 1 to 1 000 000 rows, the
  CSV formula option, progress, cancel by button or Escape, closing and leaving abort, a double press starts one
  export, the cut hints, refusals in en/de). The SQL editor opens it from a finished result ("Export…"), with the
  statement and the parameters it ran with, on the result's connection, after the shared D-02 guard.
- **Review** `stu-021-export-ui-review.md` (accepted with conditions), done in the same PR: C-1/M-1 a test runs a
  statement with a parameter, changes the editor text and the parameter field, exports, and checks that the request
  holds the statement and value as they ran (Vitest and `e2e/export.spec.ts`; kills the mutant "export the editor's
  current text"); C-2/M-2 the page's byte ceiling of 1 GiB (`MAX_EXPORT_BYTES`, see "Open"); L-1 a stream that
  breaks after some bytes is "did not arrive whole", not "the local server does not answer"; L-2 (the redundant
  profile guard on "Export…") stays as defense in depth.
- **Seam for the data viewer (STU-013, built in parallel):** the viewer mounts `ExportDialog` with
  `source: {kind: "table", schema, name}`, its open connection, `fetchedRows: null` (or the rows it holds) and asks
  D-02 through `useConnect().guard` first, like the editor. Nothing in the dialog is editor-specific; the editor's
  own problem wording is passed in (`describeProblem`).
- **Open:**
  - The file is held in the browser's memory until it is saved (a `Blob`; `fetch` cannot hand a stream to the
    download). The server's memory bound of STU-021 holds; the browser's grows with the file up to the page's
    ceiling, `MAX_EXPORT_BYTES` = 1 GiB (M-2 of `stu-021-export-ui-review.md`): past it the page aborts the request,
    drops the bytes, saves nothing and says "export fewer rows or columns" (en/de). The server bounds rows and time,
    not bytes, so a larger file cannot be exported from the page today. A save straight to disk needs the File
    System Access API (Chromium only) or a download route without the CSRF header, which the access model of STU-003
    does not allow. Revisit with the desktop package.
  - Done: `e2e/export.spec.ts` and `e2e/editor-backend.spec.ts` (with its export test) must pass without a skip in
    the e2e job since the CI e2e-list PR (TD-037).
  - #48 (server-side D-02, `confirmed_environment` on opening a connection) is adopted with the next master merge;
    the dialog opens no connection itself, so only the callers' connect paths change.
  - No estimate of the file's size before it starts (the server streams and does not count first).

### TD-041 · STU-010 page: parts that wait for other packages

- **Priority:** P2 Medium · **Status:** Open · **Refs:** STU-010, 08 sections 2, 4.3, 7 and 8, `docs/architecture/explorer.md`
- **What the page does now:** `/explorer` with its own profile picker (D-02 for PROD), the WAI-ARIA tree, the
  filter (name prefix, type, ⌘P / Ctrl+P), the detail tabs (columns, constraints, indexes, relationships, SQLite),
  the extensions and the SQLite file facts; read-only, escaped and capped, fail-closed.
- **Open, with the package that brings it:**
  - Done with the STU-013 page: "SELECT … LIMIT 100 in the SQL editor" (a new editor tab on the profile; nothing
    runs by itself), "Copy name" and "Show data" are buttons above the detail tabs, and the Data tab shows the
    object's rows (TD-042 for what it leaves). Open: the tree's context menu that offers them there too (08 section
    4.3); "Open in editor" of 08 is the same statement.
  - The DDL tab and the policies (RLS, read-only): with STU-022 (#38). Done in the STU-022 page PR #54: a DDL tab for
    every object (a sequence shows its DDL alone), a Policies tab for a PostgreSQL table, and SQLite's "Count rows"
    on request; on the explorer's open connection, read-only, escaped, fail-closed. Still open from STU-022: the
    estimates of `row-estimates` in the tree (with the virtualized tree; the selected table's estimate is shown).
  - Done after #42: the page uses the shared D-02 confirmation of the page session (`useConnectionSession`, bound to the
    profile's target) and makes the opened connection the header's connection; a connection gone on Refresh leaves the
    header too. Open reads the profile list again before its PROD check, so a profile changed to `prod` on disk after
    the list loaded gets D-02, and so does a PROD profile whose target (host, database, path) changed (the stale-list
    case of TD-038, for this page). It fails closed: if the list cannot be read, Open shows the offline problem and
    opens nothing. A profile that is gone is `profile_not_found`; the picker moves to the first remaining profile and
    takes the focus (with none left, the problem does). Open: the page does not take over a connection the header
    already shows (it opens its own, which the registry returns for the same profile), nor does it follow a header
    switch (TD-034).
  - The route `/c/:conn/t/:schema/:name` (08 section 2) instead of `/explorer` with a picker: with the workspace
    routes (TD-011). The explorer as the always-present left bar of a connection workspace belongs there too.
  - A virtualized tree (08: TanStack Virtual; `@tanstack/react-virtual` arrives with #41). Until then a group renders
    at most 500 objects and says so. The page reads the whole catalog, with no cap on the number of objects
    (C-2 of `stu-010-ui-review.md`), so the filter finds every object, also past the first 5000. The rows are
    already a flat list for the virtualized tree.
  - The E2E test against the reference apps (`e2e/explorer-reference.spec.ts`, a spec of its own so that
    `backend.spec.ts`, which the CI gate requires to run without a skip, stays complete) runs where
    `TABRION_STUDIO_E2E_PYTHON` and `TABRION_STUDIO_E2E_EXAMPLES` (tabrion's `examples` folder) are set, and its
    PostgreSQL part where `TABRION_STUDIO_E2E_PG_PARTS`, `TABRION_STUDIO_E2E_PG_MEASUREMENTS` and
    `TABRION_STUDIO_E2E_PG_USER` are. Since the CI e2e-list PR the job `e2e` checks out tabrion's `examples` at the
    commit `constraints/tabrion-integration.txt` pins and sets `TABRION_STUDIO_E2E_EXAMPLES`, so the spec runs (its
    SQLite part) and must pass without a skip like every spec. Open: its PostgreSQL part, which needs a PostgreSQL
    service and the three URLs in that job (another `.github/` change). It is an `if` inside the test, not a skip,
    so the gate does not see that it did not run (L-2 of the CI e2e-list workflow review).
  - SQLite's row count "on request with a timeout" (03 section 2 B, release 1) and the "no privileges" state (08:
    columns from the catalog, data not available) belong to the data tab (TD-042).

### TD-042 · STU-013 page: what the data viewer leaves for later

- **Priority:** P2 Medium · **Status:** Open (STU-013 page PR #52) · **Refs:** 03 sections 2 D and 5.3, 08
  sections 4.3 and 4.5, `docs/architecture/viewer.md` ("The page"), TD-041
- **What the page does:** the explorer's Data tab for tables, views, materialized views and foreign tables: pages of
  50 to 1,000 rows through the server's keyset tokens (Next, Previous, Reload), sort by one column, up to 20 filters
  bound as parameters, the search in the current page, the grid and cell detail of the SQL editor with "Load in full"
  (10 MB), the paging hint without a primary key, read-only, fail-closed parsing, stale-answer guards, a connection
  that is gone handed back to the explorer; en and de.
- **Open:**
  - **"Open as SQL"** (08 section 4.5) with the current sort and filters: the filter values would have to become
    literals or editor parameters. Until then the explorer's "SELECT … LIMIT 100" opens the plain statement.
  - **The row estimate** ("no total count (≈ 12 400)", 08 section 4.5) is shown above the tabs for PostgreSQL, not in
    the tab's status line; SQLite's count on request with a timeout (03 section 2 B, release 1) is not there.
  - **Sorting from the column header** and column resizing (the grid has neither yet, TD-033); sorting by more than
    one column (the server takes one).
  - **Typed filter values:** every value is sent as text and the database compares it by the column's rules
    (`docs/architecture/viewer.md`); a value of the wrong type is the database's problem (`db.data`). There is no
    "= NULL" (use "is NULL").
  - **The byte bound of a first page on SQLite:** a page asks for no more rows than fit in 64 MB at the declared
    types' widest values. SQLite enforces declared types only in `STRICT` tables, so a column declared `INTEGER` may
    still hold long text; for such a table the first page of a walk is bounded only by the server's `max_result_mb`
    (at most 200 MB), and later walks by the widest row seen. A check of `STRICT` (the explorer knows it) could
    count non-strict columns as long, at the price of smaller pages.
  - **The "no privileges" state** of 08 section 4.3: a role that may not read the table gets the database's
    `db.permission` text in the tab, not the columns-only view.
  - **Export from the tab** (STU-021 has the backend), the route `/c/:conn/t/:schema/:name` (TD-011) and the tree's
    context menu (TD-041).
  - **D-02 on the server:** the tab never opens a connection; the explorer opens it through the shared page
    confirmation, and since #48 the server checks `confirmed_environment` on `POST /api/connections` too (adopted
    with the master merge of `ed24015`; nothing in the tab changed).
  - **CI (done):** `e2e/viewer.spec.ts` and `e2e/viewer-backend.spec.ts` must pass without a skip in the job `e2e`
    since the CI e2e-list PR (every spec but `screenshots.spec.ts`, TD-037).
  - **Filters in the query string:** the request-size bound (12 KB, M-1 of `stu-013-page-review.md`) keeps a rows
    request below h11's 16 KB head limit, so up to about 1,000 characters outside ASCII fit in all filters together.
    Longer values would need the filters in a body (a `POST` read route, or a server-side filter token).
- **Review:** `stu-013-page-review.md` (accepted with conditions) fix round: M-1, M-2, L-1, L-2 and L-3 fixed (L-3 by
  a 150 ms delay of the first read); I-1 to I-6 done. Delta review accepted; N-1 (the form counts the longest `next`
  token) and N-2 (a test pins the abort) fixed.

### TD-030 · Moderate npm advisory in development tools only (TABRION QA-8)

- **Priority:** P3 Low · **Status:** Open (watch) · **Refs:** TABRION QA-8, `docs/development/ci.md` (QA-8 evidence)
- **What:** `npm audit` over the whole tree reports GHSA-g3hv-75wg-m249 (moderate, a denial of service through
  glob patterns) in `minimatch` 7.0.0..11.0.0, used only by eslint, `@eslint/config-array` and typescript-eslint.
  Nothing of it ships: `npm audit --omit=dev --audit-level=high` finds nothing, and the wheel contains only the
  built frontend. The fix npm offers (`npm audit fix --force`) downgrades eslint to 9.39.5 and typescript-eslint
  to 8.36.0 (semver-major). `npm audit` lists **10** moderate entries: the one advisory through its dependency
  cascade. `eslint-plugin-jsx-a11y` uses `minimatch` 3.1.5, outside the range.
- **Next step:** take the eslint/typescript-eslint update that moves to a fixed `minimatch` when Dependabot proposes
  it; no forced downgrade. A `package.json` `overrides` of `minimatch` to `^11.0.1` is an alternative, but a major
  version for eslint's dependency: check eslint with it before using it.

### TD-031 · Build with the hashed backend pins in CI, and keep the two pins in step (TABRION QA-8)

- **Priority:** P2 Medium · **Status:** Done (PR #76; its workflow-change review accepted,
  `pr76-f6-lock-workflow-review.md`); the nightly job below done in PR #90 (workflow-change review pending) ·
  **Refs:** TABRION QA-8, security review F-6 (`security-review-prep.md`), `build-constraints.txt`,
  `constraints/runtime-lock.txt`, `docs/development/ci.md` ("Hashed runtime pins"), tabrion TODO TB-130 and TB-133
  (the same build pins)
- **What:** the wheel hash is reproducible only for one hatchling version (its `WHEEL` file names the generator).
  `pyproject.toml` pins `hatchling==1.33.0`, `build-constraints.txt` pins it and its dependencies with hashes,
  `tests/unit/test_build_constraints.py` checks that the two agree, and the reproducibility test builds with them.
  The CI jobs still build with the unhashed `pyproject.toml` pin.
- **Done (build):** the four `python -m build` steps (jobs `e2e`, `e2e (<browser>)`, `build and install`, `license
  and supply chain`) run with `PIP_CONSTRAINT` set to `build-constraints.txt` and `PIP_REQUIRE_HASHES: "1"`.
- **Done (runtime, F-6):** `constraints/runtime-lock.txt` pins the runtime closure of Studio and Tabrion (Python 3.11
  to 3.14, Linux, macOS, Windows) exactly, with the SHA-256 of every file; Tabrion stays a full-SHA Git pin. Every CI
  job that installs Studio installs the lock first (`pip install --require-hashes --no-deps --only-binary :all:`),
  then Studio and Tabrion; `pip check` and `scripts/runtime_lock.py check-installed` fail when the installed runtime
  closure differs from the lock. `scripts/runtime_lock.py check` (job `backend lint`, `tests/unit/test_runtime_lock.py`)
  keeps the lock inside the `pyproject.toml` ranges and in step with `build-constraints.txt`; `scripts/smoke_wheel.py`
  and the installation guide install the same way. The ranges in `pyproject.toml` are unchanged.
- **Next step:** after the first hatchling release, check that the Dependabot pull request updates both build pins;
  if not, regenerate `build-constraints.txt` by the recipe in its header in the same pull request. Refresh the runtime
  lock (`python scripts/runtime_lock.py update -- --upgrade`) about once a month and whenever `dependency audit`
  reports an advisory against a pin; Dependabot is not relied on for it. The `pip install --upgrade "pip>=26.1"` step
  of the guide and of `smoke_wheel.py` is not hashed, and pip builds Studio and Tabrion with build backends fetched
  without hashes (build time only; review L-1).
- **Owner decision (G-S1, 2026-11-13, decision 2, option A):** keep the lock and add the nightly, non-blocking
  "newest dependencies" job described below. It is a `.github/` change, so it comes as its own pull request with its
  own workflow-change review. Not a G-R precondition. The remaining install risks (the `uv tool` and pipx paths do
  not check the lock's hashes) are listed in `docs/development/release-checklist.md`.
- **Done (nightly, PR #90):** `nightly.yml` job `newest dependencies (Python <version>)`, Linux with Python 3.11 and
  3.14, schedule and manual only, `permissions: contents: read`, timeout 20 minutes like `backend test`: `pip install
  --upgrade --upgrade-strategy eager -e ".[dev]" -c constraints/tabrion-integration.txt` without the lock and without
  the pip cache, `pip check`, `pip freeze` into the log and the job summary, `pytest -m "not packaging"`. Not in
  `ci.yml` and not in `.github/ci-manifest.json`, so never a pull request check; a red run opens the "Nightly failed"
  issue (`report` needs it). Checked locally before the PR with Python 3.11 (fastapi 0.162.7 instead of the lock's
  0.162.5, among others): `pip check` clean, 2267 passed, 113 skipped. Still open: the optional weekly lowest-direct
  run.
- **Later (review of PR #76):** CI now tests only the locked set, so a newer release inside the `pyproject.toml`
  ranges that breaks Studio shows up only at the next lock update. Add a non-required scheduled job (nightly, Linux
  with Python 3.11 and 3.14) that installs without the lock, prints `pip freeze` and runs `pytest -m "not
  packaging"`; optionally a weekly lowest-direct run for the lower bounds. CI installs the lock on macOS and Windows
  only with Python 3.13 (L-7, accepted: only the `< 3.12` markers differ by version, and Linux 3.11 covers them).
- **Also (review L-2 of the Studio QA-8 PR):** the frontend's reproducibility is checked by hand
  (`scripts/check_frontend_reproducible.py`). A CI step could run it in `build and install` on each OS after
  `build_frontend.py` (`node_modules` is already there) and compare the printed listings across the OS matrix.

### TD-034 · STU-008 connect: what the page does not do yet

- **Priority:** P2 Medium · **Status:** Partly done (TD-034 PR, 2026-11-13: the banner) · **Refs:** 08 §3, §5 ("Connect",
  "error recovery"), `src/views/connections/useConnect.tsx`, `src/shell/ConnectionLost.tsx`
- **After "Connect"** 08 §5 goes on to the workspace overview. There is no workspace view yet (explorer page,
  STU-010/STU-012 frontend lane), so the page stays in the connection manager and the header shows the connection.
  Navigate there once the workspace route exists.
- **A lost connection: done** in the TD-034 PR (`docs/architecture/connections.md`, "Lost connections and
  'Reconnect'"). 08 §5's banner "Connection lost" with "Reconnect" shows on every page that holds a connection when a
  request on it answers `db.connection`, `studio.connection_not_found` or `studio.connection_stale`, or when the
  header's connection check (`GET /api/connections/{id}/health`, every 30 s while the tab is visible) finds it gone.
  "Reconnect" asks D-02 again for PROD; the views go on with the new connection. Vitest, `e2e/connection-lost.spec.ts`
  (harness, axe light/dark, en/de) and `e2e/connection-lost-backend.spec.ts` (real server; with a throwaway
  PostgreSQL, the database stops answering). Still open: the check covers the header's connection only (another
  view's connection is seen on its next request), and the banner is not yet tested in Firefox and WebKit (STU-050).
- **Review `td-034-review.md`** (C-1, C-2, L-1..L-6) done in the TD-034 PR. Kept as limits, documented in
  `docs/architecture/connections.md`:
  - "D-02 again" holds in the page that saw the loss; another tab keeps its confirmation, and its next open of the
    PROD profile replaces the lost connection without a dialog (the D-02 model of TD-038).
  - While both pool sessions are lent out and the database is down, the check says "busy"; the loss shows when
    those statements fail.
- **The PostgreSQL part of `e2e/connection-lost-backend.spec.ts`** (the database stops answering) runs only when
  `TABRION_STUDIO_TEST_POSTGRES` and `TABRION_STUDIO_TEST_POSTGRES_SUPERUSER` are set. It is an `if` inside the test,
  not a skip, so the CI job `e2e` (no PostgreSQL) passes it without running it, and the "none skipped" gate cannot
  see that (like TD-041). The same flow on the server is required in CI through `tests/security/test_connection_loss.py`
  (PostgreSQL job). Run it locally with a throwaway database, or give the e2e job a PostgreSQL service (a `.github/`
  change).
- **The status bar** (08 §3: server version, role, database or file) still shows only the local server. The test's
  facts are not kept for the open connection; `POST /api/connections` could answer them as well.
- **Several open connections:** the header shows the last one opened; the others stay open in the backend (the list
  marks them "open") until "Disconnect" or shutdown. A connection switcher with a list comes with the editor tabs.

### TD-032 · STU-014 page: deviations from 08 section 4.9 (route, English server text, progress, links, OK rows)

- **Priority:** P2 Medium · **Status:** Partly done (STU-014 page PR #39; C-3 of `stu-014-ui-review.md`; the English
  server text part done in the diagnostics-texts PR; open: the route, progress, the documentation link and the "OK"
  rows) · **Refs:**
  STU-014, STU-050, STU-054, 08 sections 1 and 4.9, E-S3 (08 preamble), `docs/architecture/diagnostics.md`
  ("The page")
- **Route:** 08 section 1 puts the view at `/c/:conn/diagnostics`; the page is `/diagnostics` with a profile picker,
  like `/migrations` (STU-015 page, the same deviation). There is no connection workspace in the page yet (TD-011),
  so a page-level picker is the only consistent choice. When the workspace arrives, move both views under the
  active connection (or keep the picker and record the decision in 08).
- **English server text (blocked STU-054 for the `STU-DX-*` texts; resolved by PR #85):** the page shows the titles of Studio's own
  findings (`STU-DX-001..010`) in English and German, but their details and remedies, and every text of tabrion's
  findings, are the server's English text (marked `lang="en"`, with a hint above the list). E-S3 says MVP and 1.0
  ship German and English complete, and STU-054 tests "de/en vollständig", so this is an acceptance gap for
  STU-050/STU-054, not a nicety. **Decision noted here:** the Studio-owned `STU-DX-*` details and remedies must be
  en/de before STU-054 is accepted (the route sends each finding's parameters, such as counts, setting names and
  the sslmode, as fields, and the page words them); tabrion's `TAB-*` texts stay English and labeled until tabrion
  has a localized catalog. The owner confirmed this decision on 2026-11-13 (TD-060, decision (c)).
- **Done in the diagnostics-texts PR (the English server text part):** every Studio finding and backup hint carries
  `params` (counts, booleans, fixed words, the profile's `sslmode`; no name, path or value), and the page words
  `STU-DX-001..010` and, under the same decision, `STU-BK-*` (Studio-owned as well) in en/de from
  `diagnostics.texts` with plural forms (`studioText.ts`); `params` that do not fit fall back to the server's English,
  marked `lang="en"`. The server keeps `detail` and `remedy` (English, made from the same `params` and templates in
  `services/diagnosis_texts.py`) for older clients, the report and the command line; a test keeps the page's English
  templates equal to the server's, and `studioTexts.cases.json` is worded alike by both sides (pytest and Vitest).
  The English wording now uses plurals ("2 rows" instead of "2 row(s)"). The hint above the list ("Details and
  remedies from tabrion are its own English text.") shows only when such a text is listed. The diagnosis report
  stays English (a file to pass on; the reader's language is unknown) and its JSON form carries the `params` of
  Studio's entries (an added field, `format_version` 1). tabrion's `TAB-*` texts stay English and labeled. Narrowest
  reading: no `lang` parameter for the report and no translation of tabrion's texts. Still needs the owner's
  confirmation at STU-054, as recorded above.
- **Progress:** 08 section 4.9 asks for progress and cancel for SQLite's integrity check. The backend reports no
  progress, so the page shows an indeterminate "Running…" with a note that the diagnosis ends at the connection's
  time limit; cancel works. A real progress display needs a progress event from the backend (with tabrion 0.2,
  TD-013).
- **Documentation link and "OK" rows:** 08 section 4.9 also sketches a documentation link per finding and "OK" rows
  for the checks that passed. The route sends findings only (adding passed checks is a route change and a UX
  decision), and the user guide has no page per finding code yet (TD-023); a link is added once it has one.

### TD-036 · STU-025 parts outside the backend

- **Priority:** P2 Medium · **Status:** Done (recorded 2026-11-12 with the STU-025 backend PR; the page part in PR
  #47, the command line in the diagnostics-texts PR) · **Refs:** 03 section 2 G ("Bericht exportieren (Markdown/JSON, ohne
  Secrets)"), 03 section 1 ("Diagnose-Bericht ist auch per CLI erzeugbar"), 09 STU-025,
  `docs/architecture/diagnostics.md` ("Diagnosis report")
- **Download button:** the diagnostics view gets a "Download report" action with the two formats. It belongs to the
  STU-014 page (merged in #39, `frontend/src/views/diagnostics`): `POST /api/connections/{id}/diagnostics/report` with `{"format"}` through
  `fetch` (session and CSRF header, like the export dialog), saved under the name of `Content-Disposition`; the view
  also shows `backup.hints` next to the findings. `studio.report_invalid` needs en/de texts.
- **The page and `TAB-PG-020` (C-2 and D-2 of `stu-025-review.md`):** the page merged in #39 does not show
  `backup.hints` yet, so the page route keeps tabrion's `TAB-PG-020` ("WAL archiving is off") in its findings
  (`run(..., replace=False)`); only the report replaces it with `STU-BK-010`. No regression of the STU-014 page.
  When the view shows the hints, switch the route to `replace=True` in the same pull request (the test
  `test_d2_the_page_keeps_tabrions_archive_finding_and_the_report_replaces_it` changes with it).
- **Command line:** 03 section 1 says agents and CI can make the report from the command line. A
  `tabrion-studio diagnose --profile <id> --format markdown|json` needs a decision on how the CLI opens a profile
  without the server (the registry, the secrets, project trust); it reuses `DiagnosisReports`. Done in the
  diagnostics-texts PR, see below.
- **User guide:** the diagnostics section of TD-023 describes the report and the backup hints.
- **Done in the TD-036 page PR:** the view lists `backup.hints` under "Backups" (en/de titles of `STU-BK-*`, fail
  closed on a broken `backup`), "Download Markdown report" and "Download JSON report" on the shown connection (saved
  under the server's file name when it has the expected form; `studio.report_unavailable` and
  `studio.report_invalid` in en/de; cancel, profile switch and unmount drop it), D-02 of the view through the shared
  `useConnect` gate and page session of STU-008, the page route with `replace=True` (the D-2 test now checks that the
  page and the report both replace `TAB-PG-020`), and `docs/guide/diagnostics.md`. Vitest, Playwright with axe in
  light and dark (`e2e/diagnostics.spec.ts`), and both downloads against a real `tabrion-studio`
  (`e2e/backend.spec.ts`).
- **Review `td-036-page-review.md` (ACCEPTED), fixed:** L-1 "Backups were not checked in this run" instead of "No
  backup hints" after a cancel or the time limit (`STU-DX-008`, or `searched: false` / `archive_mode: null`); N-1 the
  download guards a double press with refs; N-2 the report request is aborted on cancel, switch and leave, and
  "canceled" is said only when the server confirms it. Each with a test; the mutations of the three are killed.
- **Still open: the command line.** Not part of the page PR: `tabrion-studio diagnose` needs the decision above (how
  the CLI opens a profile without the server, its secrets and project trust), which is larger than the page work.
- **Done in the diagnostics-texts PR (the command line):** `tabrion-studio diagnose [PROJECT] --profile PROFILE
  [--format markdown|json] [--output FILE [--force]] [--confirm-prod]` (`diagnose_cli.py`). The decision: the command builds
  the server's own `Configuration` for PROJECT (user profiles and settings, the project file with its trust record in
  the state directory, the keyring and `env:`/`file:` secrets), opens the read-only connection with
  `ConnectionRegistry.open` and writes `DiagnosisReports.build`, so the allowlist (no password, token, URL/DSN, host,
  port, user or database name, path, value) and `studio.report_unavailable` are the route's. Project trust is given
  only in D-01 in the browser; an untrusted or changed project gives no project profile. D-02: until now the CLI
  opened no connection at all, so there was nothing to weaken; a PROD profile needs `--confirm-prod` on every run (no
  environment variable or setting), refused before any open without it, and the flag is the registry's
  `confirmed_environment`, checked on the read it connects to (TD-038), so a profile that becomes PROD meanwhile is
  refused too. No password prompt (a missing one is `studio.secret_missing`); refusals are the problem catalog's fixed
  texts; logs to standard error through the redaction; exit codes 0/1/2. No server, no instance lock. Documented in
  `docs/guide/diagnostics.md`, `docs/guide/starting.md`, `docs/architecture/diagnostics.md` and `process-model.md`
  (the guide is English only; it has no German part). Tests: `tests/security/test_diagnose_cli.py` (with a
  PostgreSQL canary test under the marker `postgres`) and the guide's option table check in
  `tests/unit/test_user_guide.py`.
- **Review `pr85-diag-review.md` (ACCEPTED WITH CONDITIONS), fixed:** M-1 `--output` writes atomically (an
  `O_EXCL|O_NOFOLLOW` 0600 temporary file in the target's folder, then link or `os.replace`), never follows a link,
  refuses an existing file unless `--force`, and always refuses the profile's database file and its `-wal`, `-shm`
  and `-journal` files (by real path and as the same file); M-2 the report goes to `stdout.buffer` as UTF-8 with LF,
  and a failing stream is a fixed error; L-1 the JSON report is ASCII (`ensure_ascii`), so no raw C1 or direction
  character; L-2 a `user:`/`project:` value matches ids only; L-3 the issues of `profiles.toml` are printed as key
  path and code when a profile is not found; L-4 the state directory must be private as for the server
  (`paths.check_private_dir`); L-5 the German details and remedies say "du" and "Sicherung" like the page; I-1
  "labeled"; I-2 the dead log call is gone; I-3 the guide says the keyring may ask to be unlocked and how CI avoids
  it. Each with a test in `tests/security/test_diagnose_cli.py` or `tests/unit/test_diagnostics_page.py`. I-4 is not
  taken.

### TD-061 · STU-028 release SBOM: the license records of macOS and Windows in CI

- **Priority:** P2 Medium · **Status:** Done in PR #90 (its workflow-change review pending); open: the result
  of the first nightly run, attaching the files to a release (G-5, after G-R) · **Refs:** 09 STU-028, 07 §5 G-R and
  TM-10, 03 T-11, `scripts/release_bom.py`, `docs/development/release-checklist.md` (C-6), `docs/development/ci.md`
  ("SBOM")
- **What:** `scripts/release_bom.py --strict` needs a license record (`scripts/collect_licenses.py`) of every pin
  of the runtime lock, and two pins apply on Windows only (`pywin32-ctypes`, `tzdata`), so the release run needs the
  records of a Windows (and a macOS) runtime environment. The CI job `license and supply chain` runs on Linux only
  and writes its SBOMs with `cyclonedx-py` and `npm sbom`.
- **To do:** collect the records in the `build and install` job on each system (it already has the fresh runtime
  environment), upload them, and run `release_bom.py --strict` over all of them in `license and supply chain`,
  uploading the SBOM and `third-party-licenses.md` with the `sbom` artifact. A `.github/` change: its own workflow
  review and the label. Until then the records are collected by hand on those systems for a release.
- **Inherited from STU-031 (review L-6 of `pr83-stu028-review.md`, not fixed in the STU-028 PR):**
  `scripts/check_licenses.py` flattens parentheses, so `(MIT OR GPL-3.0-only) AND LGPL-2.1-only` passes. The release
  report relies on it. Fix with a small recursive-descent parser for SPDX `OR`, `AND`, `WITH` and parentheses, with
  that expression as a negative test.
- **Note (review R-1 of the re-check on `d833d6f`, Low, not fixed in the STU-028 PR):** `release_bom.py` looks up
  license records only by the names of the pins, so a record of a package that the lock does not pin is ignored: no
  failure and no line in the report, even with `--strict`. The CI job's `check_licenses.py` run still judges every
  installed package, so only the release report is affected. Fix: fail on (or at least list as "not in the lock")
  every record whose canonical name is neither a pin nor `tabrion-studio`, with a test.
- **Done (PR #90):**
  - **CI step, in the nightly:** `nightly.yml` jobs `license records (<label>)` (`linux-py3.13`, `linux-py3.11`,
    `macos-py3.13`, `windows-py3.13`: a fresh environment with the hashed lock and the Tabrion pin, both `--no-deps`,
    `pip check`, `collect_licenses.py`, artifact `licenses-<label>`) and `release SBOM (strict)` (`release_bom.py
    --strict` over the four files, artifact `release-sbom`, report head in the job summary); `report` needs both.
    Not in `build and install` and `license and supply chain` as planned above: the Python 3.11 record (for the four
    `python_full_version < '3.12'` pins) needs a runner that no required job has, so the required job set would
    change, and `license and supply chain` would wait for the other systems' jobs and be skipped (NOT OK for `ci-ok`,
    which accepts only `success`) whenever one of them fails. Studio itself is not installed in those environments:
    its own record is not used, and the wheel needs the frontend build. Locally with Linux records of Python 3.13 and
    3.11 collected the same way, `--strict` lists exactly the two Windows-only pins; their wheels declare
    `BSD-3-Clause` (`pywin32-ctypes`) and `Apache-2.0` (`tzdata`), both allowed.
  - **L-6:** `check_licenses.py` parses SPDX expressions by recursive descent (`OR`, `AND` binding tighter, `WITH`,
    parentheses); a malformed expression is not allowed; a parenthesis right after a word of a name stays part of the
    name (trove classifier names such as `ISC License (ISCL)`). Tests: `(MIT OR GPL-3.0-only) AND LGPL-2.1-only` and
    other expressions in `tests/supply_chain/test_licenses.py`. Over the npm lockfile and a development environment's
    records the verdicts are unchanged.
  - **R-1:** `release_bom.py` fails (with or without `--strict`) on every license record whose canonical name is
    neither a pin nor `tabrion-studio`, and lists it in the report
    (`test_a_record_of_a_package_the_lock_does_not_pin_fails`).

### TD-063 · Security assessment B-1 and B-2: refused functions in every call form

- **Priority:** P1 · **Status:** In progress (PR #92) · **Refs:** the security assessment of 2026-11 (B-1 Medium,
  B-2 Low; the report and its reproductions stay with the planning documents), TD-048 (F-3),
  `docs/architecture/connections.md`
- **Done in the PR:** B-1: `readonly._calls_refused_function` counts a refused name as a call when the next token is
  `(` or the token before it is `.` (PostgreSQL's attribute notation), on the existing tokens (comments and white
  space dropped, quoted identifiers unquoted, both lexings of `'...'`); a column or table named like a refused
  function is refused after a `.`, the bare name stays allowed. The check-in reset (`tabrion_compat.reset_session`)
  closes the session's named and unnamed dblink connections when the database has dblink, and drops a connection
  where that fails. B-2: `heap_force_kill`, `heap_force_freeze` and `pg_truncate_visibility_map` are refused. Tests in
  `tests/security/test_function_call_forms.py` (every call form, reads that only mention a name, the reset with and
  without dblink; the PostgreSQL ones in the PostgreSQL CI job).
- **Open:** the assessment's untested candidates of newer servers (PostgreSQL 17 `pg_buffercache_evict*`, 18
  `pg_restore_*_stats` and `pg_clear_*_stats`): check whether they act in a read-only transaction and refuse them if
  so; a diagnostics hint when the profile's role may run dblink, adminpack or session-ending functions. The text
  check stays best effort, and the role's privileges stay the boundary.

### TD-065 · Security assessment A-01: a fresh loopback origin per start

- **Priority:** P1 · **Status:** In progress (A-01 PR) · **Refs:** the security assessment of 2026-11 (A-01 Medium;
  the report and its reproduction stay with the planning documents), STU-ADR-0002 ("Residual risk: origin reuse on
  loopback"), `docs/architecture/access-protection.md` ("Origin per start"), `docs/architecture/process-model.md`
- **Done in the PR:** `server.bind_loopback` listens on a random `127.a.b.c` (not `127.0.0.1`) chosen anew for every
  server, also with `--port` (another random address when the fixed port is taken on one, up to 8 tries); where the
  address is not configured (`EADDRNOTAVAIL`, macOS by default) it uses `127.0.0.1`, and with `--port` prints a note.
  The Host and Origin allowlist answers exactly that `<address>:<port>`; the instance info file records the address
  (`host`; a file without it means `127.0.0.1`, any value outside `127.0.0.0/8` makes the file invalid), and the
  control client connects there. The page removes any service worker registration on its origin at start
  (`frontend/src/serviceWorkers.ts`, cleanup only). Docs: the user guide's `--port` advice (starting, security,
  troubleshooting), the README, the development setup (`TABRION_STUDIO_BACKEND` now names the printed address).
  Tests: `tests/unit/test_server.py` (fresh address per start, the fixed port on a new address, the fallback; the
  Linux and Windows `backend test` jobs assert that a random address can be bound), `tests/unit/test_instance.py`,
  `tests/security/test_access.py`, `tests/process/test_process_model.py`, frontend `serviceWorkers.test.ts`, and the
  e2e regression `frontend/e2e/origin-backend.spec.ts` (two starts on one `--port` get two addresses, in every
  browser; in Chromium a service worker that another program registered on `127.0.0.1:<port>` does not reach a later
  Studio on that port). The assessment's `Clear-Site-Data` suggestion is not adopted: on `POST /api/session` it also
  clears the CSRF value in `sessionStorage` of every other open tab (checked in Chromium), and it cannot reach a page
  load that a worker answers; the browser makes no request when Studio stops.
- **Open:** on real devices before G-R (with TD-023): Edge, Chrome and Firefox on Windows open, sign in and copy on a
  `127.a.b.c` address (no proxy, a secure context); on macOS the fallback note with `--port`. Accepted residual risk
  for the owner to confirm (STU-ADR-0002): the macOS fallback with a fixed `--port`, and an old Studio tab that is
  reloaded after the stop on an address another local user has taken over (no session).
- **Review conditions** (independent review of the A-01 PR, accepted with conditions): C-2 before G-R, folded into
  GR-2: with a logging HTTP proxy configured, check that Firefox and WebKit/Safari do not send `127.a.b.c` requests
  through the proxy (Chromium bypasses the proxy for all of `127.0.0.0/8`; the others are unverified). C-3 before
  G-R: a real-Mac check of the `127.0.0.1` fallback and the `--port` note; the macOS residual risk stays an
  owner-known risk.

### TD-066 · STU-055 vector data view: what it leaves for later

- **Priority:** P3 · **Status:** Open · **Refs:** STU-055, `docs/architecture/vector-inspection.md`,
  `docs/guide/vector-data.md`
- **pgvector in CI:** the CI PostgreSQL job runs `postgres:17` without pgvector, and it must not skip a test. The
  tests that need pgvector (HNSW, an invalid IVFFlat, a B-tree on a vector column, undeclared dimensions, an expression
  HNSW index on Orkexa's `real[]`, dropping the extension) carry their own marker `pgvector` and run with
  `TABRION_STUDIO_TEST_PGVECTOR` (a superuser URL of a server with pgvector); they passed locally on PostgreSQL 16 with
  pgvector 0.6.0. Running them in CI needs a pgvector image in `ci.yml` (a `.github/` change with its review).
- **sqlite-vec install status:** not determinable from a database file (the module lives in the application's
  process), and Studio loads no extension; the view shows the `vec0` tables and their `CREATE_VERSION`. Tabrion has no
  SQLite counterpart of `extension_status`; one would not help either, for the same reason. Nothing to do unless the
  owner wants a different source (for example the project's own dependency pins).
- **`halfvec` and `sparsevec`:** recognized by type name and extension membership, not tested against a server
  (pgvector 0.6 has neither; 0.7 added them).
- **sqlite-vec ANN:** `vec0` tables are shown as exact search, since released sqlite-vec versions scan a `vec0` table's
  chunks. If a later sqlite-vec version adds an approximate index to `vec0`, this rule and its test change.
- **Other layouts:** libSQL's `F32_BLOB(n)` columns, LanceDB-style or application-specific metadata tables are not
  recognized; a float array or BLOB without confirming metadata is never called a vector column.
- **e2e:** the view has unit tests (Vitest, with the axe check of `a11y.test.tsx`) and backend tests; no Playwright
  spec yet (the e2e harness has no vector fixture).
- **Tabrion 0.2 (TD-013):** tabrion 0.2.0 offers `extension_status` only on a psycopg connection (no `Database` or
  `Transaction` method), so `tabrion_compat.extension_status` keeps running it on the driver connection of a
  `Database.read()`, like `check`; it goes when tabrion offers it on a `Database` or `Transaction`.
