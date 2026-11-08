# TODO: tabrion-studio

> Open work for this repository: release blockers, owner actions, the open Studio work packages, temporary
> measures and technical debt. The program backlog is the source of truth for scope and acceptance criteria
> (planning documents 03, 05, 07, 08 and 09; program status in `tabrion/docs/program/STATUS.md`, K-12). "TABRION
> <WP-ID>" names a work package of that backlog.
>
> **Last full review:** 2026-11-08 against branch `claude/project-thread-rdo6g3` (PR #4: STU-003 and the review
> recommendations R-1..R-7 of `stu-002-018-review.md`; PR #1: STU-001, STU-002, STU-018, STU-031).

## Summary

| Item | Priority | Status |
|---|---|---|
| TS-001 add the tabrion dependency with STU-009 | P1 | Blocked (tabrion v0.1.0) |
| OA-001 make `ci-ok` a required check; check the ci-ok run link before every merge | P0 | Open (owner) |
| OA-002 independent review of the process model | P1 | Accepted (re-review at 988bc9d) |
| OA-003 create the label `workflow-change-reviewed` | P1 | Open (owner) |
| OA-004 independent review of STU-003 (access protection) | P0 | Open (PR #4) |
| STU-003 binding requirements from the review (F-02, F-05, F-08, F-09) | P0 | In progress (PR #4, TABRION STU-003) |
| TD-001 layer contract for `api`/`services` | P3 | Open |
| TD-002 sandboxed browsers cannot open the redirect file | P3 | Open |
| TD-003 Windows: DACL check inside the profile (review R-6) | P3 | Open |
| TD-004 fork pull requests in the workflow-change gate (review R-7) | P3 | Open |

Plus the open Studio work packages of M6 and the cross-repository checks in the tables below.

**Release blockers:** nothing is released from this repository before G-R (every public version needs the owner's
approval, plus a human security review for Studio, E-QA2). There is no release workflow yet.

Priorities: **P0** blocks a gate, a merge or a release; **P1** on the critical path of M6; **P2** in scope, schedule
soon; **P3** minor gap or debt. Statuses: `Open`, `In progress` (PR linked), `Blocked` (names the blocker),
`Needs decision`, `Accepted risk`.

## Temporary measures

### TS-001 · Add the tabrion dependency with the first API use (TABRION STU-009, RC-8)

- **Priority:** P1 High · **Status:** Blocked (tabrion v0.1.0, TABRION TAB-040) · **Refs:** RC-8, E-VER, 05 §3.2
- **What:** nothing in Studio uses the Tabrion API yet, so `pyproject.toml` has no tabrion dependency (no git pin, no
  pre-release specifier on master, RC-8; enforced by `tests/security/test_dependencies.py`).
- **Next step:** add `tabrion[postgres,sqlalchemy,migrations]>=0.1,<0.2` with the first WP that uses the Tabrion API
  (STU-009), after tabrion v0.1.0.

## Owner actions

### OA-001 · Make `ci-ok` a required check (TABRION STU-018, GOV-3)

- **Priority:** P0 Critical · **Status:** Open · **Refs:** CI-FAST, 05 §1 (branch protection)
- **What:** `ci-ok.yml` runs from the default branch only (`workflow_run`), so it cannot judge the pull request
  that adds it. Once it is on master it posts the commit status `ci-ok` on every pull request head SHA.
- **Next step (owner):** after the merge, protect `master` (no force push, no deletion, no required reviews per
  the agent flow) and add the status check `ci-ok` as required. Optionally list the CI job names as required checks
  for the first pull request.
- **Before every merge (review R-1, TABRION STU-003 PR):** the latest `ci-ok` status on the head SHA must link
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

- **Priority:** P0 Critical · **Status:** Open (PR #4) · **Refs:** 07 §6 (packages with a network or credential
  surface), condition C-3 of `stu-002-018-review.md`, `docs/architecture/access-protection.md`
- **What:** an independent review agent checks the access protection against the binding requirements below and the
  threat model (T-01, T-02, T-03, T-07, T-15), including the workflow-change gate changes (R-2, R-3), before the
  merge.
- **Next step:** review PR #4; set the label `workflow-change-reviewed` after reading the `.github/` diff.

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
| TABRION STU-003 | Access protection (launch token, cookie, Host/Origin, CSP, `no-store`) | P0 | STU-002 | In progress (PR #4): STU-T-001..006, -014, -025, -026 and the *STU-003 binding requirements*; waits for OA-004 |
| TABRION STU-004 | Problem JSON and logging | P1 | STU-001, TAB-038 | request log without query strings (Uvicorn's access log is off) |
| TABRION STU-005 | App shell, design system, i18n de/en | P1 | STU-001 | replaces the placeholder page |
| TABRION QA-9 | E2E framework (Playwright + axe) | P1 | STU-001 | adds the `e2e` job and the platforms e2e smoke (05 §3.5) |
| TABRION STU-006 | Configuration, profiles, project trust | P1 | STU-004 | |
| TABRION STU-007 | Secret management | P0 | STU-006 | |
| TABRION STU-009 | Connection registry and read-only enforcement | P0 | STU-007, TAB-040 | adds `backend integration (PostgreSQL 17)`; engines are disposed on shutdown |
| TABRION STU-051 | Status indicators and dialogs | P1 | STU-005 | |
| TABRION STU-008, STU-010..STU-015 | Connection manager UI, explorer, query engine, SQL editor, data viewer, diagnostics, migration status | P1/P0 | STU-009, TAB-042 | STU-008 adds the "openapi in sync" CI job (the first API types for the frontend); STU-011 replaces the polling heartbeat with SSE and applies the Origin check to it |
| TABRION STU-050, STU-052, STU-053 | UX acceptance, keyboard, accessibility audit | P1/P2 | | |
| TABRION STU-016 | MVP hardening and security review (G-S1) | P0 | all of the above | |
| TABRION STU-017 | MVP docs and tag 0.1.0 (no publication) | P1 | STU-016 | adds the manual, build-only release workflow |

Later: STU-020..STU-025 and STU-054 (Studio 1.0, without the Data Editor), STU-028 (release preparation, G-R);
STU-026 (Data Editor) stays locked behind G-S2 and outside the 1.0 acceptance; STU-027 (more UI languages) is a
later extension. The Studio 1.1 Schema Designer (change request 14 §3.5) is not planned here; the process model
and the server-side write flag (STU-ADR-0003) leave room for it.

## Cross-repository checks still to add

| Ref | What | Priority |
|---|---|---|
| TABRION QA-18 | `tests/packaging/test_independent.py`: import every tabrion module while orkexa, axentra, nodestro_* and tabrion_studio are blocked (05 §3.2) | P2 |
| TABRION QA-21 | full AC-7 check against tabrion's `__all__`/API contract and export of the used names to `tabrion/docs/api-consumers.md` (the AST check for private names is in place) | P2 |
| TABRION QA-22 | convention audit against orkexa/axentra (pins, manifest, Dependabot, branch protection, English, license, SECURITY.md) | P2 |

## Technical debt

### TD-001 · Layer contract for `api` → `services` → `profiles`/`security`

- **Priority:** P3 Low · **Status:** Open · **Refs:** 05 §3.2, AC-1
- **What:** the import-linter layer contract needs the `api`, `services` and `profiles` packages, which arrive with
  STU-006 and later. Today the contracts forbid every Levuvo platform, keep the process model from importing the CLI
  and keep `tabrion_studio.security` (TABRION STU-003) below the app, the server, the process model and the CLI.

### TD-002 · Sandboxed browsers cannot open the redirect file (TABRION STU-003)

- **Priority:** P3 Low · **Status:** Open · **Refs:** review F-02, `docs/architecture/access-protection.md`
- **What:** the browser is opened with a `file://` redirect file in the state directory. A browser in a sandbox that
  cannot read that folder (the Firefox snap on Ubuntu cannot read hidden folders such as `~/.local/state`; Flatpak
  browsers similarly) shows an error instead of Studio. Workaround: `--print-url`, or `--state-dir` in a folder the
  browser can read.
- **Next step:** with STU-005 (app shell) or the first user feedback: detect a snap or Flatpak default browser and
  print a hint, or place the redirect file (only that file) in a readable private folder.

### TD-003 · Windows: check the DACL inside the profile too (review R-6)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-002-018-review.md` R-6, `paths.py:_check_windows_dir`
- **What:** inside `%USERPROFILE%`/`%LOCALAPPDATA%` the state directory's DACL is not checked. A state directory in a
  synced or shared profile folder (for example `%USERPROFILE%\OneDrive\…`) would put the control token, and now the
  redirect files, there. The default (`%LOCALAPPDATA%`) is not synced.
- **Next step:** run `dacl_problem` inside the profile as well, or warn about known sync folders (STU-006 or STU-016).

### TD-004 · Fork pull requests in the workflow-change gate (review R-7)

- **Priority:** P3 Low · **Status:** Open · **Refs:** `stu-002-018-review.md` R-7, `ci_ok_status.py:pull_numbers`
- **What:** for fork pull requests (no `pull_requests` in the `workflow_run` event) the gate relies on
  `GET /commits/{sha}/pulls` listing the open fork pull request. If GitHub does not list it, the gate fails closed
  ("no open pull request found"), which is safe but could block a fork pull request by mistake.
- **Next step:** check it on the first fork pull request; if needed, fall back to `GET /pulls?state=open` filtered by
  `head.sha`. Changing `ci_ok_status.py` needs the label (OA-003).
