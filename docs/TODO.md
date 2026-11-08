# TODO: tabrion-studio

> Open work for this repository: release blockers, owner actions, the open Studio work packages, temporary
> measures and technical debt. The program backlog is the source of truth for scope and acceptance criteria
> (planning documents 03, 05, 07, 08 and 09; program status in `tabrion/docs/program/STATUS.md`, K-12). "TABRION
> <WP-ID>" names a work package of that backlog.
>
> **Last full review:** 2026-11-08 against branch `claude/project-thread-rdo6g3` (first pull request: STU-001,
> STU-002, STU-018, STU-031).

## Summary

| Item | Priority | Status |
|---|---|---|
| TS-001 add the tabrion dependency with STU-009 | P1 | Blocked (tabrion v0.1.0) |
| OA-001 make `ci-ok` a required check | P0 | Open (owner) |
| OA-002 independent review of the process model | P1 | Accepted with conditions (fixed in PR #1) |
| OA-003 create the label `workflow-change-reviewed` | P1 | Open (owner) |
| STU-003 binding requirements from the review (F-02, F-05, F-08, F-09) | P0 | Open (with TABRION STU-003) |
| TD-001 layer contract for `api`/`services` | P3 | Open |

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

### OA-002 · Independent review of the process model (TABRION STU-002, 07 §6)

- **Priority:** P1 High · **Status:** Accepted with conditions · **Refs:** 07 §6 review model, STU-ADR-0002,
  `tabrion-planung/reviews/stu-002-018-review.md`
- **What:** the independent review of PR #1 (at 4925486) covered the lock and info files, the control token, the
  loopback bind and the ci-ok gate. It came back ACCEPTED WITH CONDITIONS.
- **Done in PR #1:** F-01 (server proof before the token is sent, launch URL check, stale info file removed when the
  lock is taken and at the start of a shutdown, regression tests with a fake server), F-04 (SIGHUP, CTRL_CLOSE, info
  file removed on every exit path), F-06 (only authenticated control requests and same-origin heartbeats reset the
  idle timer), F-07 (Windows: private ACL outside the profile, junctions refused), F-08 (`[::1]` held on the same
  port), F-10 (unsafe state directory and lock file refused), F-11 (status post retried, job fails loudly) and F-03
  (workflow-change gate with the label `workflow-change-reviewed`, CODEOWNERS as a recommendation).
- **Moved to STU-003:** F-02, F-05, F-08 (Host allowlist part) and F-09; see *STU-003 binding requirements*.
- **Next step:** the reviewer checks the fixes before the merge.

### OA-003 · Create the label `workflow-change-reviewed` (TABRION STU-018, review F-03)

- **Priority:** P1 High · **Status:** Open (owner) · **Refs:** `docs/development/ci.md` (workflow-change gate)
- **What:** after the merge, ci-ok reports NOT OK for every pull request that changes `.github/workflows/**` or
  `.github/scripts/**` without this label. Only writers can set labels.
- **Next step (owner):** create the label once (Issues, Labels). Set it only after the owner or an independent
  review has read the workflow change, then re-run ci-ok. Optional: turn on "require review from code owners" for
  `.github/CODEOWNERS` once a second maintainer exists (it would block every agent pull request today).

## STU-003 binding requirements (review of PR #1)

TABRION STU-003 is accepted only if it meets these requirements of `stu-002-018-review.md` (condition C-3). Each needs
a test in the STU-003 pull request.

- **F-02, launch token not in a process argv.** The browser is opened with a 0600 redirect file in the state directory
  (`<meta http-equiv="refresh" content="0;url=http://127.0.0.1:P/launch#TOKEN">`) through its `file://` path, never
  with a URL that carries the token, because the argv of `xdg-open`, `open` or the browser is readable by every local
  user. Delete the file after the token has been redeemed or after a short timeout. `--print-url` stays the explicit
  opt-in. Test (STU-T-005/-006): the argv of the started browser contains no token.
- **F-05, Host and Origin checks on every route.** Apply the Host allowlist (T-01) to every route, including
  `/api/control/*`, `/api/control/proof`, `/api/heartbeat` and `/api/health`. The control endpoints reject any request
  that carries `Origin`, `Sec-Fetch-Site` or `Sec-Fetch-Mode` (the CLI never sends them), so a browser can never
  drive them even if the token reaches a page.
- **F-08, `localhost` and `::1`.** Studio prints and opens only `http://127.0.0.1:<port>/` URLs, and it now holds
  `[::1]:<port>` (PR #1). The STU-003 Host allowlist either drops `localhost:<port>` or accepts it only while the
  `[::1]` guard is held; a failed IPv6 bind stays non-fatal. Test: no `localhost` URL is printed or opened.
- **F-09, cookies are not port-scoped.** Require the SQ-03 CSRF header on every request with a session. Its value
  comes from origin-scoped storage (JS memory or `sessionStorage`), never from a cookie, so a cookie stolen by a
  server on another local port is useless alone. Optionally bind the session to the `Host` value including the port.
- **F-06, follow-up.** Requests with a valid session count as activity for the idle timeout, as authenticated control
  requests and same-origin heartbeats do now.

## Open work packages (M6, Studio MVP 0.1)

| WP | Title | Priority | Depends on | Note |
|---|---|---|---|---|
| TABRION STU-003 | Access protection (launch token, cookie, Host/Origin, CSP, `no-store`) | P0 | STU-002 | adds the one-time launch token to the URL returned by `/api/control/launch`; security middleware in `create_app`; must meet the *STU-003 binding requirements* above (F-02, F-05, F-08, F-09) |
| TABRION STU-004 | Problem JSON and logging | P1 | STU-001, TAB-038 | request log without query strings (Uvicorn's access log is off) |
| TABRION STU-005 | App shell, design system, i18n de/en | P1 | STU-001 | replaces the placeholder page |
| TABRION QA-9 | E2E framework (Playwright + axe) | P1 | STU-001 | adds the `e2e` job and the platforms e2e smoke (05 §3.5) |
| TABRION STU-006 | Configuration, profiles, project trust | P1 | STU-004 | |
| TABRION STU-007 | Secret management | P0 | STU-006 | |
| TABRION STU-009 | Connection registry and read-only enforcement | P0 | STU-007, TAB-040 | adds `backend integration (PostgreSQL 17)`; engines are disposed on shutdown |
| TABRION STU-051 | Status indicators and dialogs | P1 | STU-005 | |
| TABRION STU-008, STU-010..STU-015 | Connection manager UI, explorer, query engine, SQL editor, data viewer, diagnostics, migration status | P1/P0 | STU-009, TAB-042 | STU-011 replaces the polling heartbeat with SSE |
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
- **What:** the import-linter layer contract needs those packages, which arrive with STU-003 and STU-006. Today the
  contracts forbid every Levuvo platform and keep the process model from importing the CLI.
