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
| TS-001 temporary pre-release pin of tabrion | P0 | Blocked (tabrion v0.1.0) |
| OA-001 make `ci-ok` a required check | P0 | Open (owner) |
| OA-002 independent review of the process model | P1 | Open |
| TD-001 layer contract for `api`/`services` | P3 | Open |

Plus the open Studio work packages of M6 and the cross-repository checks in the tables below.

**Release blockers:** nothing is released from this repository before G-R (every public version needs the owner's
approval, plus a human security review for Studio, E-QA2). There is no release workflow yet.

Priorities: **P0** blocks a gate, a merge or a release; **P1** on the critical path of M6; **P2** in scope, schedule
soon; **P3** minor gap or debt. Statuses: `Open`, `In progress` (PR linked), `Blocked` (names the blocker),
`Needs decision`, `Accepted risk`.

## Temporary measures

### TS-001 · Temporary pre-release pin of tabrion (TABRION STU-001, RC-8)

- **Priority:** P0 Critical · **Status:** Blocked (tabrion v0.1.0, TABRION TAB-040) · **Refs:** RC-8, E-VER, 05 §3.2
- **What:** Tabrion has no final tag yet. `pyproject.toml` therefore pins
  `tabrion[postgres,sqlalchemy,migrations] @ git+https://github.com/peterkarlhuber/tabrion.git@b0309c5987b1b2e06bf9c77a60735d315294e334`
  (tabrion master after its M1 merge), with `[tool.hatch.metadata] allow-direct-references = true`. Under RC-8 a
  pre-release pin is allowed on an unmerged branch only; master needs a final-only range.
- **Next step:** after tabrion `v0.1.0` is tagged, replace the pin with `"tabrion[postgres,sqlalchemy,migrations]>=0.1,<0.2"`,
  remove `allow-direct-references`, and let CI install tabrion from its tag (git URL of the tag SHA in CI, or PyPI
  after G-R, V-B). **This branch must not be merged to master with the git pin** unless the owner explicitly
  accepts that for the bootstrap.

## Owner actions

### OA-001 · Make `ci-ok` a required check (TABRION STU-018, GOV-3)

- **Priority:** P0 Critical · **Status:** Open · **Refs:** CI-FAST, 05 §1 (branch protection)
- **What:** `ci-ok.yml` runs from the default branch only (`workflow_run`), so it cannot judge the pull request
  that adds it. Once it is on master it posts the commit status `ci-ok` on every pull request head SHA.
- **Next step (owner):** after the merge, protect `master` (no force push, no deletion, no required reviews per
  the agent flow) and add the status check `ci-ok` as required. Optionally list the CI job names as required checks
  for the first pull request.

### OA-002 · Independent review of the process model (TABRION STU-002, 07 §6)

- **Priority:** P1 High · **Status:** Open · **Refs:** 07 §6 review model, STU-ADR-0002
- **What:** STU-002 opens a loopback HTTP server with a token-protected control endpoint
  (`POST /api/control/{launch,shutdown}`). Studio packages with a network or credential surface need an independent
  review agent. The full access protection (STU-003) and the G-S1 security review follow; this review should cover
  the lock and info files, the control token and the loopback bind.

## Open work packages (M6, Studio MVP 0.1)

| WP | Title | Priority | Depends on | Note |
|---|---|---|---|---|
| TABRION STU-003 | Access protection (launch token, cookie, Host/Origin, CSP, `no-store`) | P0 | STU-002 | adds the one-time launch token to the URL returned by `/api/control/launch`; security middleware in `create_app` |
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
