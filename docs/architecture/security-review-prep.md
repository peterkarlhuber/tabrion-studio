# Security review preparation (STU-016, G-S1; G-S2 readiness; G-S3 and G-S4 definitions)

This page prepares the security gate **G-S1** of the Studio MVP. It is a readiness checklist and the result of an
internal pre-review pass, **not** the gate: G-S1 is decided by the owner after an independent security review and its
re-review, and a human security review before a public release stays mandatory (STU-028, gate G-R). Nothing here
replaces either review.

State: prepared at master `e9df576`; updated after the first independent review of master `e186178` (section
[Independent review](#independent-review-first-round)) and after its re-review on master `2a8a229`, which accepted
the review (section [Re-review](#re-review-on-2a8a229)). The accepted review is the input to G-S1, not the gate.
On 2026-11-13 the owner accepted every recommendation of the G-S1 decision package (13 points, each option A,
section [Owner decisions](#owner-decisions-on-2026-11-13)). **G-S1 passed** for master `9a5d4faa69f1c974999d258b36258fbc10b3323f`: the owner granted it
on the condition that all 21 required CI jobs are green on that SHA, which was met on 2026-11-13 (run 43457105100).
G-R is not granted. Sensitive specifics of the pre-review and of the review, such as reproduction steps, are kept outside the
repository, with the planning documents.

## What G-S1 asks

From the security and quality plan (doc 07, gate table) and the backlog entry of STU-016 (doc 09):

| G-S1 criterion | State | Evidence |
|---|---|---|
| SQ-01..SQ-08 green, each with its STU-T tests | Ready for review | [SQ requirements](#sq-01sq-08) below |
| SQ-01..SQ-08 after the independent review | Met in the review: SQ-05 and SQ-07 (review F-1, F-2) and SQ-06 (F-4) fixed (PR #65/#66), independent re-review accepted at `2a8a229` | [Re-review](#re-review-on-2a8a229) below |
| Every threat-model measure (doc 03 section 7.3, T-01..T-16) proven by a test | Met in the review: T-06 and T-13 (review F-1, F-2) fixed (PR #66), independent re-review accepted at `2a8a229`; T-16: F-7 fixed in PR #75 (TD-054), re-check accepted at `d33f89d`; the write-mode rows are G-S2 | [Threat model](#threat-model-t-01t-16) below |
| Review report "accepted": a first review, the fixes, a re-review on merged SHAs | Met: the first independent review (`e186178`) was not accepted; its findings were fixed (PR #65/#66), independent re-review accepted at `2a8a229`. G-S1 itself is the owner's decision: the 13 recommendations were accepted on 2026-11-13 ([Owner decisions](#owner-decisions-on-2026-11-13)); **G-S1 passed** for master `9a5d4faa69f1c974999d258b36258fbc10b3323f` (all 21 required CI jobs green, run 43457105100, 2026-11-13) | [Independent review](#independent-review-first-round), [re-review](#re-review-on-2a8a229) below |
| `npm audit --omit=dev` without High | Met (0 findings at `e9df576`) | CI job `dependency audit` |
| axe: 0 serious or critical | Met for the existing views (`e2e`, every view in light and dark, en and de) | `frontend/e2e/*.spec.ts`, `docs/architecture/accessibility.md`; the rest of STU-053 is TD-022 |

## Owner decisions on 2026-11-13

!!! warning "G-S1 status"
    **G-S1 passed** for master `9a5d4faa69f1c974999d258b36258fbc10b3323f`. The owner accepted the recommendations on 2026-11-13 and
    granted G-S1 for that SHA on the condition that all 21 required CI jobs are green on it; the condition was met
    on 2026-11-13 (run 43457105100). G-R is **not** granted: the mandatory human security review before any public
    release (STU-028, E-QA2) remains, STU-026 (Data Editor) stays locked behind G-S2, and nothing is tagged or
    published.

The G-S1 decision package (planning documents, `g-s1/g-s1-entscheidungspaket.md`, state master `d72a14f`) put 13
points to the owner. On 2026-11-13 (14:18 UTC) the owner accepted every recommendation, each **option A**. The table
records the decision, the residual risk that is accepted with it, the follow-up work and whether the follow-up is a
precondition of G-R (the owner's approval of a publication). The G-R preconditions are listed in the gate section of
the [Release checklist](../development/release-checklist.md#g-r-preconditions-from-g-s1).

| # | Point | Decision (option A) | Accepted residual risk | Follow-up | G-R precondition |
|---|---|---|---|---|---|
| 1 | F-5 / TD-033 rest: SQL editor drafts | Keep PR #77 (drafts in the state database, none in browser storage); the rest of TD-033 is ordinary follow-up work | Drafts are SQL text in Studio's private state database (0600); old draft keys of *other* projects stay in the browser until that project's Studio moves them (the reviewer's safer option) | TD-033 rest (splitter, error positions, column widths, workspace tabs) stays open as P2 usability work, no security item | No |
| 2 | F-6 / TD-031: pinned dependencies | Keep PR #76 (hashed runtime lock in CI and in the guide) and add a nightly, non-blocking "newest dependencies" job without the lock | Until that job exists, a newer release inside the `pyproject.toml` ranges that breaks Studio shows only at the next lock update; the pipx and `uv tool` paths of the guide do not check the lock's hashes (the pip path with `--require-hashes` is the secured one) | The nightly job, as its own pull request with a workflow-change review (TD-031) | No |
| 3 | F-7 / T-16 rest: trusted migration code | Keep PR #75 (the digest covers imported project modules); no sandbox | Trusted project code runs with the user's rights and can do more than import modules; this is the intended model ("trust it like `alembic heads`"), said in the trust dialog and the guide | None now; a sandbox would be a new work package after 1.0 | No |
| 4 | JR-N1: PROD retry after a failed reconnect | Keep the behavior: a lost connection resets the D-02 confirmation, a failed reconnect attempt does not | After a failed "Reconnect" on a PROD profile, the next attempt in the same page session does not ask D-02 again (the user confirmed already) | An e2e test of the PROD case (TD-062) | No |
| 5 | TD-005: launch URL in the browser history | Accepted risk stays | The spent one-time URL stays in the browser history; the token is worthless once redeemed and otherwise valid for at most 120 s | Shorten the lifetime once real browser start times are measured | No |
| 6 | TD-002 / TD-007: sandboxed browsers, real-browser launch | Accept now, fix both before G-R | Sandboxed browsers (Firefox snap, Flatpak) cannot open the redirect file (Studio prints a hint); the launch flow is checked in Chrome only; the development proxy is not for shared machines | TD-002: the redirect-file fix; TD-007: the launch check in Firefox and WebKit | **Yes** (both) |
| 7 | TD-003 / TD-008: Windows | TD-008 done with PR #76 (Windows CI job for the path checks); TD-003 accepted now, fixed before G-R | On Windows the state folder's permissions inside the user profile are not checked (for example a OneDrive-synced folder; the default is not synced) | TD-003 fix; a check on a **real Windows machine** of the refused device names and of OneDrive folders (so far CI and tests only) | **Yes** (TD-003 and the real-Windows check) |
| 8 | Side-effect functions the database itself defines | Accept the residual risk | A function defined in the database (for example used by a view) is invisible to Studio's statement check, and PostgreSQL's read-only mode does not stop its effects; the risk is bounded by the rights of the database role, which is the real boundary | None in code; the guide recommends a separate, really read-only role and the diagnostics warn about a role that can write ([Connections](../guide/connections.md#a-read-only-role-for-postgresql)) | No |
| 9 | Hexadecimal and underscore number literals refused | Keep refusing them | A usability limit only: numbers must be written in decimal, with a clear error message | None | No |
| 10 | Optional report to the PostgreSQL security team | Prepare a non-public report as a draft | None for Studio (F-1 is fixed in Studio) | A confidential draft for the owner only; nothing is sent without the owner's approval, under the owner's name or account, and no details go into this repository or GitHub | No |
| 11 | D-4: scripts in OneDrive and other reparse-point folders | Keep refusing them for 1.0; say so in the guide | None for security (refusing is the safe side); projects under a OneDrive-synced "Documents" folder must be moved to use scripts | None for 1.0; allowing OneDrive's cloud reparse tags only if users need it (a new work package with a real-Windows test and a review). The real-Windows check of item 7 covers the refusal | No (its real-Windows check is part of item 7) |
| 12 | E-1: script names with invisible characters | Keep the rules of PR #82 (ZWNJ/ZWJ only between two letters, U+3000 as a space, refused names counted) | A permitted joiner between Latin letters can make two names look alike; Studio shows it visibly masked | None | No |
| 13 | TD-056: log redaction in tabrion | Fix it in tabrion (small PR with a test), then take the new tabrion pin in Studio; accept the residual risk until then | Values from database errors could stay in local logs when a caller writes such a part in the middle of a one-line message | The tabrion PR (owner: tabrion) and the pin update in Studio (TD-056) | **Yes** |

## SQ-01..SQ-08

| SQ | Requirement | Where it is enforced | Tests |
|---|---|---|---|
| SQ-01 | Listen on loopback only | `server.py` binds a fresh random `127.a.b.c` per start, `127.0.0.1` where only that exists (finding A-01), and holds `[::1]`; the CLI has no host option | STU-T-025: `tests/unit/test_cli.py::test_bad_arguments`, `tests/security/test_launch.py`; A-01: `tests/unit/test_server.py`, `frontend/e2e/origin-backend.spec.ts` |
| SQ-02 | Host and Origin allowlist (DNS rebinding) | `security/access.py`: the instance's own `<address>:<port>` only, the own Origin and same-origin fetch metadata on every API request | STU-T-001..003: `tests/security/test_access.py`; every API route: `tests/security/test_every_route.py` |
| SQ-03 | Launch token, then an HttpOnly SameSite=Strict cookie and a CSRF header | `security/launch.py`, `security/sessions.py` | STU-T-004..006: `test_access.py`, `test_launch.py`; every API route: `test_every_route.py` |
| SQ-04 | Secrets only in the backend | `config/secrets.py` (keyring without plain-text fallback, `env:`, `file:`), `tabrion.safe_url()`, log redaction in `logs.py` | STU-T-008/-009: `test_canary.py`, `test_secrets_api.py`, `test_log_process.py`, `test_diagnosis_report.py`; through the routes of the editor, viewer, export and diagnosis: `test_canary_flows.py` |
| SQ-05 | Read-only enforced by the database | `services/connections.py`, `services/readonly.py` (SQLite `mode=ro`, `query_only`, `trusted_schema` off, authorizer with a function allowlist; PostgreSQL read-only session and transactions), text check as a second layer. Review F-1, F-2: fixed (PR #65/#66), independent re-review accepted at `2a8a229` (the text check refuses any `INTO`, the pool reset drops temporary tables, the authorizer allows only SQLite's built-in reading functions) | STU-T-010..013, -024 and the F-1/F-2 tests: `tests/security/test_read_only.py` (PostgreSQL part in the `backend integration (PostgreSQL 17)` CI job), `test_queries.py` and `test_exports.py` (editor and export route) |
| SQ-06 | Limits and cancel | `services/queries.py`, `viewer.py`, `exports.py`; limits in the configuration; request body caps enforced while the body streams in, before any route reads it (`security/body.py`, review F-4) | STU-T-018..020: `test_queries.py`, `test_viewer.py`, `test_exports.py`, `frontend/e2e/editor-backend.spec.ts`; body caps: `tests/security/test_body_limits.py` |
| SQ-07 | PROD marking; no writes before G-S2 | Environment badge, frame and title (STU-051); D-02 on the server (`confirmed_environment`, TD-038); no write route exists (STU-026 is locked). Review F-1: fixed (PR #65/#66), independent re-review accepted at `2a8a229` (the server-side D-02 was met already) | `test_read_only.py::test_td_038_*`, `test_connections_api.py`, frontend D-02 tests; `test_every_route.py` lists every route |
| SQ-08 | Strict CSP, no foreign hosts | `CONTENT_SECURITY_POLICY` in `security/access.py`; no CDN, fonts or telemetry | STU-T-014: `test_access.py::test_csp_is_strict`; `frontend/e2e/network.spec.ts` (0 foreign hosts) |

## Threat model T-01..T-16

The measures are those of doc 03 section 7.3; T-16 is in `docs/architecture/migrations.md`.

| Threat | Measure | Tests | State |
|---|---|---|---|
| T-01 DNS rebinding | Host allowlist on every route | STU-T-001, -002 (`test_access.py`, `test_launch.py`) | Covered |
| T-02 CSRF from another site | Origin, fetch metadata, JSON only, CSRF header | STU-T-003, -004; every API route in `test_every_route.py` | Covered |
| T-03 Token theft | One-time launch token in the fragment, redirect file 0600, session cookie | STU-T-005, -006 | Covered; TD-005 (spent URL in history) is an accepted risk |
| T-04 Files read by other accounts | Files 0600, folders 0700, symlinks refused | No STU-T-007 label; in substance `tests/unit/test_paths.py`, `test_logs.py`, `test_secrets.py`, `test_config_writer.py`, `tests/security/test_profiles_api.py` | Covered on POSIX; Windows DACL inside the profile is TD-003. The state database with the history (STU-020): `tests/security/test_history.py` (0600, link and shared-file refusal, no parameter value in any byte); the editor drafts too (TD-033, F-5): `tests/security/test_drafts.py` (only in the state database), `e2e/drafts-backend.spec.ts` (none in browser storage) |
| T-05 Secret leaks | Redaction, canaries, safe URLs, Problem JSON without server text | STU-T-008, -009; `test_canary_flows.py` | Covered |
| T-06 Accidental writes | Read-only in the database, prod marking; the text check refuses `INTO` (F-1) and side-effect functions (F-3); `STU-DX-003` reports schema `CREATE` and database `TEMP` | STU-T-010..013 and the F-1/F-3 tests (`test_read_only.py`, `test_queries.py`, `test_exports.py`, `test_diagnostics.py`) | Covered: review F-1 fixed (PR #65/#66), independent re-review accepted at `2a8a229`. Residual: side-effect functions the database itself defines (see the residual risks). The write mode is G-S2 ([G-S2 readiness](#g-s2-readiness-data-editor-and-the-shared-write-foundation)) |
| T-07 XSS through database text | React text only, ESLint rule against `dangerouslySetInnerHTML`, CSP | STU-T-014; no STU-T-015 label, in substance `test_access.py::test_eslint_forbids_dangerously_set_inner_html` and hostile-value tests of the views | Covered |
| T-08 Hostile project | Project trust (D-01), bound to digests; script paths (STU-020) judged as text, then reached through folder descriptors with `O_NOFOLLOW` (07 TM-04), no device, 8.3 or format-character names, no hard links | STU-T-016, -017 (`test_project_trust.py`); `tests/security/test_scripts.py` | Covered on POSIX; on Windows a folder swapped between the check and a save's rename remains (documented in [History, favorites and scripts](history-and-scripts.md)) |
| T-09 Resource exhaustion | Row, byte and cell caps, timeouts, cancel, parallel limits; request body caps while streaming (review F-4) | STU-T-018..020; `test_body_limits.py` | Covered |
| T-10 TLS downgrade | `STU-DX-002` for PROD without verify-full | STU-T-021 (`test_diagnostics.py`) | Covered as a finding; Studio does not refuse such a profile |
| T-11 Supply chain | Locked dependencies, license policy, SBOM, audits | No STU-T-022 label; CI jobs `license and supply chain`, `dependency audit` | Covered; TD-030 (dev-only advisory), TD-031 (hashed pins in CI) |
| T-12 Change log | Writes logged | STU-T-023 | Not applicable in 1.0 (no write mode); for G-S2 see [G-S2 readiness](#g-s2-readiness-data-editor-and-the-shared-write-foundation) |
| T-13 SQLite extensions and `ATTACH` | Authorizer as an allowlist, functions included (F-2); `trusted_schema` off | STU-T-024 and the F-2 tests (`test_read_only.py`) | Covered: review F-2 fixed (PR #65/#66), independent re-review accepted at `2a8a229` |
| T-14 Network exposure | No `--host` | STU-T-025 | Covered |
| T-15 Cached answers | `Cache-Control: no-store` | STU-T-026; every refusal in `test_every_route.py` | Covered |
| T-16 Trusted project code (Alembic) | Code trust bound to file digests (also the project modules on `prepend_sys_path`, F-7), isolated worker | `test_migrations.py` | Covered; residual: what trusted code runs by other means than an import (documented in T-16) |

## The pages and their API

Every API route except `/api/health`, `/api/session` and the CLI's control endpoints needs the session, the CSRF
header, refuses a foreign Origin and cross-site or same-site fetch metadata, and, for unsafe methods, needs the own
Origin and a JSON body. Every path under `/api/control/` except the proof needs the control token in the access
middleware, so a control route added later is never open (review F-8); every request body is capped while it streams
in (review F-4). `test_every_route.py` reads the routes from the application built with every service, so a
route added later is checked without a change to the test; at `e9df576` there are 45 routes. The only other route
under `/api/` is `/api/openapi.json` (checked in `test_access.py`); the test fails if another appears.

| Page | API families | Specific measures |
|---|---|---|
| Connections, settings | `/api/profiles`, `/api/secrets`, `/api/settings`, `/api/connections`, `/api/connection-tests` | Digest-bound atomic writes (0600), D-02 on the server, secrets never answered |
| Project trust | `/api/project`, `/api/project/code-trust` | D-01, digests of the trusted files |
| Explorer, DDL and policies | `/api/connections/{id}/catalog`, `.../tables/...`, `.../row-estimates` | Fixed `search_path` for catalog reads (TD-039), checked `SELECT` only |
| SQL editor | `/api/queries` | Text check plus database read-only, caps, cancel, SSE per query |
| Data viewer | `.../tables/{schema}/{name}/rows`, `.../cell` | Every statement has a `LIMIT`; filters bound as parameters; server-side page tokens |
| Export | `/api/exports` | Refusals before the first byte, caps, CSV formula escaping |
| Diagnostics and report | `.../diagnostics`, `.../diagnostics/report` | Report from an allowlist and redacted (`test_diagnosis_report.py`) |
| Migration status | `.../migrations`, `/api/project/code-trust` | Worker only after code trust (T-16) |

## Further checklist items

- **CSP and headers:** CSP, `no-store`, `nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and
  same-origin COOP and CORP on every answer and refusal (STU-T-014, -026, `test_access.py`, `test_every_route.py`).
  The tests compare with values written out in `tests/support/access.py`, and
  `test_access.py::test_security_headers_are_pinned` fails when the code's set changes. No inline script; the
  frontend has no `innerHTML`, `eval`, `window.open` or `srcdoc`, and downloads go through a blob and an anchor.
- **Secrets:** keyring without a plain-text fallback, `env:` and `file:` references (C-3 checks at read time),
  session secrets bound to the connection target; the page never receives a password (`docs/architecture/secrets.md`).
- **D-02:** enforced on the server since TD-038 (`confirmed_environment`, 409 `studio.prod_confirmation_required`);
  the page asks once per page session and target.
- **Read-only:** in the database (SQLite `mode=ro`, `query_only`, authorizer; PostgreSQL read-only session), with
  Studio's text check as a second layer (`docs/architecture/connections.md`).
- **Local-only binding:** a fresh random `127.a.b.c` per start (`127.0.0.1` where only that exists, finding A-01),
  other loopback addresses, `localhost` and `[::1]` refused by the Host allowlist, no host option.
- **Packaging:** the wheel carries the built frontend only; the e2e harness never ships
  (`tests/packaging/test_artifacts.py`). Build and install are checked on three operating systems in CI.
- **Dependency audit at `e9df576`:** `pip-audit` clean; `npm audit --omit=dev` 0 findings; the full npm audit has 11
  moderate advisories in development tools only (TD-030).
- **Logs:** uvicorn's access log is off; the request log records the route template, never a query string.

## Open security-relevant TD rows

| Row | Priority | Subject |
|---|---|---|
| TD-002 | P2 | Sandboxed browsers cannot open the redirect file (in progress; G-R precondition, decision 6) |
| TD-003 | P3 | Windows: DACL check inside the profile (G-R precondition, with the real-Windows check, decision 7) |
| TD-004, TD-006 | P3 | Workflow-change gate: fork pull requests, who may set the label |
| TD-005 | P3 | Spent launch URL in the browser history (accepted risk, decision 5) |
| TD-007 | P3 | Development proxy and real-browser launch checks (G-R precondition, decision 6) |
| TD-008 | P1 | A `windows-latest` CI job for the T-2 checks (done with PR #76, decision 7) |
| TD-027 | P3 | Two concurrent opens of one profile can both connect |
| TD-030, TD-031 | P3, P2 | Dev-only npm advisory; hashed pins in CI (done with PR #76; the nightly unlocked job is a follow-up, decision 2) |
| TD-033 | P2 | SQL editor leftovers (splitter, error positions, column resizing, workspace tabs): usability only; the drafts part (F-5) is done (decision 1) |
| TD-038 | P2 | Remaining limits of the D-02 gate and of the connect error states |
| TD-056 | P2 | tabrion's log redaction in the middle of a line (owner: tabrion; G-R precondition, decision 13) |
| TD-062 | P3 | JR-N1: an e2e test of the PROD retry after a failed reconnect (decision 4) |

## Residual risks (doc 03 section 7.3)

- Malware running as the same user can read everything the user can; Studio does not defend against it.
- PostgreSQL `READ ONLY` is not a boundary against functions with side effects; the role's rights are. Studio's
  read-only checks are defense in depth; a database role that is really read-only (no write privileges, no
  ownership, no functions with side effects it can call) is what makes write protection reliable (owner decision 8,
  accepted; how to create such a role: [Connections](../guide/connections.md#a-read-only-role-for-postgresql)). Diagnostics
  report a read-only role that can write (`STU-DX-003`, since F-1 also schema `CREATE` and database `TEMP`). Since
  F-3 of the STU-016 review, the text check refuses the side-effect functions it knows: `pg_terminate_backend`,
  `pg_cancel_backend`, `pg_logical_emit_message`, `txid_current`, `pg_current_xact_id`, the WAL, backup, replication
  slot and replication origin functions, `pg_reload_conf`, `pg_rotate_logfile`, `pg_promote`, the statistics resets,
  `lo_import`, `lo_export`, adminpack's file writers, the BRIN and GIN index maintenance functions, `connectby` and
  `xpath_table` (which run a query given as text) and every `dblink` function (the list and the reason for each are
  in [Connections](connections.md)). A function of the database or of an extension that Studio does not know is not
  visible in the text; the role stays the boundary for it.
- The warning before a destructive statement is UX, not protection.

## Independent review (first round)

The first independent security review (2026-11-13, master `e186178`, tabrion `d5821fa`) was **not accepted**; its
fixes were re-reviewed and the review accepted at `2a8a229` ([Re-review](#re-review-on-2a8a229)). It
confirms the access layer (launch, session, CSRF, Host, Origin and fetch metadata, the security headers, D-02 on the
server, secret handling, problem documents) and found gaps the internal pre-review had missed. The report, with its
reproduction steps, stays outside the repository.

| Finding | Severity | Subject | State |
|---|---|---|---|
| F-1 | High | PostgreSQL: a statement form passed the text check and wrote despite the read-only transaction (SQ-05, SQ-07, T-06) | Fixed (PR #66), with tests; independent re-review accepted at `2a8a229` |
| F-2 | Medium | SQLite: the authorizer allowed a native function that must be refused (SQ-05, T-13) | Fixed (PR #66), with tests; independent re-review accepted at `2a8a229` |
| F-3 | Low | PostgreSQL side-effect functions outside the statement check's denylist | Fixed (PR #66, refused); independent re-review accepted at `2a8a229`; see the residual risks |
| F-4 | Low | Request bodies were read before their size was checked, also before a session exists (SQ-06) | Fixed (PR #65): caps enforced while the body streams in (`security/body.py`, [access protection](access-protection.md#request-body-limits)); independent re-review accepted at `2a8a229` |
| F-5 | Low | Editor drafts in the browser storage of a port-scoped origin (TD-033) | Fixed in the F-5 drafts PR #77: drafts in `state.sqlite` (0600) behind `/api/drafts`, none in browser storage, this project's old keys migrated and removed (`test_drafts.py`, `drafts-backend.spec.ts`); review `f5-drafts-review.md` accepted with conditions, findings 1 to 7 fixed; delta review on 3feb437 not accepted for N-1 (Medium, the structure scan before parsing was not linear in the worst case), fixed with a linear scan tested at the body cap, and N-2 (Info, fit check of both old keys together) fixed |
| F-6 | Low | Python runtime dependencies are ranges, not a hashed lock (TD-031) | Fixed in the f6-lock PR: CI and the installation guide install from the hashed lock `constraints/runtime-lock.txt` (`--require-hashes`), checked against the `pyproject.toml` ranges; Tabrion stays a full-SHA pin |
| F-7 | Info | The code-trust digest does not cover project modules that revision files import (T-16) | Fixed in PR #75 (TD-054): the digest covers them, versioned (earlier trust is renewed), fail closed over its caps; the dialog lists them. Review of the fix accepted with condition C-1 (project folders on the interpreter's path); C-1 fixed in PR #75, its delta review accepted with condition C-2 (a finder in front of the import guard), C-2 and N-1 fixed in PR #75, re-check accepted at `d33f89d` |
| F-8 | Info | The access middleware exempted the whole control prefix from the session check | Fixed (PR #65): every control request except GET/HEAD of the proof path needs the token; dot segments in `/api/*` paths reach no route; independent re-review accepted at `2a8a229` |
| F-9 | Info | This page said "covered" for SQ-05, T-06 and T-13 | Fixed: this page (PR #65); the status after the re-review is recorded below |

Re-review conditions: F-1 and F-2 fixed with tests, merged and re-reviewed; F-3 and F-4 fixed or accepted by the
owner; F-5 and F-6 recorded as owner decisions; F-7 fixed (TD-054). G-S1 stays the owner's decision after the
re-review, and the human security review before a public release (STU-028, G-R) stays mandatory.

## Re-review on 2a8a229

The independent re-review (2026-11-13, master `2a8a229`, the merge of PR #66; tabrion `d5821fa`) **accepted** the
STU-016 security review: F-1, F-2, F-3, F-4, F-8 and F-9 are fixed on the merged code (PR #65 and PR #66), with no
regressions. F-5, F-6 and F-7 are not findings that block the review; they are open owner decisions. The accepted
review is the input to G-S1, **not** the gate: G-S1 is not passed until the owner decides it, and the human security
review before a public release (STU-028, G-R) is still required. Not covered by the re-review: real browsers,
Windows and macOS, PostgreSQL 14, 15 and 17 live (14 assessed by a lexer model, 17 in CI), and wheel
reproducibility. The report stays outside the repository.

### Open owner decisions for G-S1

Decided on 2026-11-13: the owner accepted the recommendation (option A) for each of these points; see
[Owner decisions](#owner-decisions-on-2026-11-13). The list below is kept as it stood before the decision.

- **F-5 / TD-033:** editor drafts (SQL text) in the browser storage of the port-scoped origin. Addressed by the F-5
  drafts PR #77 (drafts in the state database; table above); the owner confirms it with G-S1.
- **F-6 / TD-031:** Python runtime dependencies are ranges, not a hashed lock. Addressed by the f6-lock PR (hashed
  lock in CI and the installation guide; table above); the owner confirms it with G-S1.
- **F-7 / T-16:** the migration code-trust digest did not cover project modules that revision files import. Fixed
  in PR #75 (TD-054) by hashing them; the review of the fix is accepted with condition C-1, which is fixed in the
  same PR; its delta review is accepted with condition C-2, fixed in the same PR, and the re-check is accepted at
  `d33f89d`.
- **STU-020 D-4 / F-6 (scripts on Windows):** every reparse-point folder or file, including OneDrive "Files
  On-Demand" placeholders, is refused as a link (`studio.script_link`), so scripts in a OneDrive-synced project are
  probably unusable. Keep refusing (the safe reading, documented in
  [History, favorites and scripts](history-and-scripts.md)), or accept the cloud reparse tags. Either way it needs a
  check on a real Windows machine with OneDrive, together with the refused device names (F-2).
- Still open from this page: TD-005 (the spent launch URL in the browser history), TD-002 and TD-007 (the
  redirect-file launch path and the checks in a real browser), TD-003 and TD-008 (the file-permission model on
  Windows, which CI does not yet check), and the PostgreSQL read-only model for side-effect functions that the
  database itself defines (for example inside a view or a SQL function): the text check does not see them, the
  role's rights are the boundary.

## For the human reviewer

The internal pre-review added two tests (`test_every_route.py`, `test_canary_flows.py`); the independent review then
found the gaps listed above. These points need a person's judgment. The owner's decisions of 2026-11-13 (points 1
to 5, 7 and 8 below correspond to decisions 5, 6, 7, 8, 1, 2 and 3) settle what G-S1 accepts; they do not replace this
review before a publication:

1. TD-005: whether the spent launch URL in the browser history stays an accepted risk.
2. TD-002 and TD-007: the redirect-file launch path and the checks in a real browser.
3. TD-003 and TD-008: the file-permission model on Windows, which CI does not yet check.
4. The PostgreSQL read-only model: Studio relies on the role's rights for functions with side effects beyond the
   ones it refuses (review F-3).
5. TD-033 / review F-5 (fixed): the editor drafts moved from browser storage into the state database; other
   projects' old draft keys stay in the browser until that project's editor moves them (`f5-drafts-review.md`
   finding 3, the safer option).
6. The keyring backends Studio accepts and refuses, per operating system.
7. The CI workflow-change gate (TD-004, TD-006) and the supply chain (TD-030, TD-031; review F-6).
8. Code trust for migrations (T-16): the worker's isolation and what the digest binds (review F-7).
9. The re-review of the independent review's fixes on merged SHAs: done and accepted at `2a8a229` (see above).

## G-S2 readiness (Data Editor and the shared write foundation)

G-S2 is the owner's activation decision for the shared write foundation (STU-XW-1..4) and the Data Editor
(STU-026, STU-XDE-1, STU-XDE-2): the `data_editor` flag of `write-areas.toml` ([STU-ADR-0006](../adr/0006-per-area-write-kill-switches.md)).
This section is a readiness checklist, **not** the gate and not its review. **G-S2 was granted by the owner on
2026-11-15** (14:25 UTC) with decisions 1A to 7A ([Owner decisions](#owner-decisions-on-2026-11-15-g-s2)), for local
use on DEV, TEST and STAGE databases only. The flag stays off by default, nothing turns it on automatically, and no
default changes. G-S2 does not allow PROD writes (E-X7; PROD stays technically locked), a tag or a release; the human
security review (H-2) and G-R stay mandatory before any publication, and G-S3 and G-S4 stay locked. **Binding
condition:** before the `data_editor` flag is ever switched on, decision 1A must be implemented, independently
reviewed, merged and green in CI.

State: prepared at master `3af4b79` (2026-11-15). Sensitive specifics of the reviews stay with the planning
documents.

### What G-S2 asks

From planning document 15 (section 10: "write foundation, T-06, T-12, negative tests flag off"; section 7 item 8;
section 9.4), doc 07 (gate table and section 6: an independent review agent for G-S2) and ADR 10 ("G-S2 checks
threats T-06/T-12, test results and review findings"):

| Criterion | State | Evidence |
|---|---|---|
| The write foundation built and reviewed | Met: STU-XW-5 (PR #109), STU-XW-1 (#111, #112), STU-XW-2 (#114), STU-XW-3 (#115), STU-XW-4 (#116), hardening (#117, #118); each review accepted, its "before G-S2" findings fixed (L-1, L-2 of #111; M-1 of #117) | `reviews/pr109`..`pr118` (planning documents); [threat model](write-threat-model.md) TX-01..TX-17 |
| The Data Editor built and reviewed | Met: STU-026 (#119), STU-XDE-1 (#120), STU-XDE-2 (#122); reviews accepted, L-1 of #119 and L-2 of #120 fixed before the merge | `reviews/pr119`..`pr122`; [Data Editor guide](../guide/data-editor.md) |
| T-06 accidental writes: non-prod only, unlocked, time-limited, PROD technically locked | Met in code and tests | `tests/security/test_write_profiles.py`, `test_write_unlock.py` (PROD coupling refused, 15-minute idle expiry, typed name), `test_data_editor.py`, `test_data_editor_postgres.py`; E2E `data-editor-backend.spec.ts` (PROD: no write profile, no tab, no unlock) and `data-editor-states.spec.ts` |
| T-12 change log without values | Met: append-only audit log with a hash chain, no values (canary-tested), fail closed before a run or an unlock, pending marker after it, the recent records in Settings | `tests/security/test_write_audit.py`; `AuditStatus` in the page |
| Negative tests "flag off" for every endpoint (W-9: 400/405, 403 only for the write mode) | Met | `tests/security/test_write_kill_switch.py` (every write route 405 and nothing changed; no write module imported; no query, header or setting turns a flag on), `test_every_route.py` |
| Preview, confirmation, transaction and rollback shown end to end (doc 07) | Met: a two-step changeset whose second step conflicts rolls the first one back too, against a real server (added for G-S2) | `frontend/e2e/data-editor-backend.spec.ts`; pytest `test_data_editor.py` (conflict, row count, constraint error) |
| C-01 fixed before G-S2 (planning document 15 section 7 item 8) | Met: lasting secrets, write passwords included, are never evicted from the redaction (PR #99) | `tests/security/test_canary.py`; `write/unlock.py` registers each write password as lasting |
| An independent review agent for G-S2 (doc 07 section 6) | Done: verdict accepted on master `8311c4c` (grants nothing); its Medium finding G2-01 (libpq environment variables could redirect a connection) and the Low findings G2-02..G2-04 are fixed (branch `claude/project-thread-rdo6g3-gs2fix`), the Info findings G2-05..G2-07 are recorded in [docs/TODO.md](../TODO.md#g-s2-readiness), G2-08 is decision 6 below | `reviews/gs2-gate-review.md` (planning documents); `tests/security/test_libpq_environment.py`, `test_data_editor.py`, `test_data_editor_values.py`, `test_data_editor_postgres.py`, `test_write_flags.py` |
| The owner's decisions below | Met: granted 2026-11-15, decisions 1A..7A; 1A implemented in branch `claude/project-thread-rdo6g3-gs2-1a`, its review, merge and green CI are the binding condition before the flag is switched on | [Owner decisions](#owner-decisions-on-2026-11-15-g-s2) below; `tests/security/test_write_privileges.py`, `test_write_unlock.py`, `test_write_postgres.py` |

### Owner decisions on 2026-11-15 (G-S2)

The decision package (`g-s2/g-s2-bereitschaft.md`, planning documents) put seven points to the owner. On 2026-11-15
(14:25 UTC, 15:25 Europe/Vienna) the owner granted G-S2 and chose the recommendation, **option A**, for each. They are
recorded with their follow-ups in [docs/TODO.md](../TODO.md#g-s2-decisions-on-2026-11-15).

**Scope:** local DEV, TEST and STAGE use only; PROD stays technically write-locked; every kill switch stays off by
default and nothing switches one on automatically; G-S3, G-S4, H-2 and G-R stay open; no tag, no release.
**Binding condition:** the `data_editor` flag is not switched on before 1A is implemented, independently reviewed,
merged and green in CI.

| # | Point | Decision (option A) | Accepted residual risk | Follow-up | Deadline |
|---|---|---|---|---|---|
| 1 | I-3 of the review of PR #111: over-privileged write role | A write role (`kind = "write"`) with superuser, BYPASSRLS, REPLICATION or membership in `pg_read_server_files`, `pg_write_server_files` or `pg_execute_server_program`, directly or through a reachable role, is refused at unlock: `405 studio.write_role_over_privileged`, nothing unlocked, the connection closed. CREATEROLE (also before 16) and the other predefined roles stay warnings | A write role with CREATEROLE or a broad predefined role such as `pg_write_all_data` can be unlocked (with a warning); the role stays the boundary (residual risk 2 of the write threat model) | Implemented in branch `claude/project-thread-rdo6g3-gs2-1a` (`WRITE_REFUSING_FINDINGS`; tests without a server, through the route and against PostgreSQL 16/17, each refusing attribute and role held directly and through a group); independent review, merge, CI green | Before the flag is first switched on (binding) |
| 2 | Loopback TCP without TLS (residual risk 4, N-2 of PR #116) | Accept for G-S2 (non-PROD, local development only); the guide asks for a long random write password and recommends a socket with `requirepeer` | A process of another local user that takes the server's port first can guess a weak write password offline from a SCRAM exchange | Recheck: require TLS `verify-full` or a socket with `requirepeer` for loopback TCP with a password. Prepared: `loopback_tls = "require"` in `write-areas.toml` does it (default `"accept"`); the default is the owner's choice at G-R | Before G-R |
| 3 | Audit log anchor (N-1 of PR #114) and retention (N-4) | Accept; retention unlimited and documented in the security guide | Removing the whole `audit` folder starts a new chain unnoticed; the protection goal excludes malware running as the user, and the log never holds values | Keep the last `(seq, hash)` also in the keyring; report "log missing or reset" when a new chain starts while an anchor exists | Before G-R; built (branch `claude/project-thread-rdo6g3-gs2-3a`, `tests/security/test_write_audit_anchor.py`) |
| 4 | Audit of refusals before a run | Keep as built: refused plans (binding, expiry, confirmation) and unlocks of an area that is off are not recorded; every run, drift refusal and refused unlock of a stored profile with its area on is (also the 1A refusal, as an `unlock` record with its result code; no new event) | The log does not show attempts that never reached a run or an enabled unlock | Optional `plan.refused` / `unlock.refused` events if the log must serve as evidence for third parties | None (optional) |
| 5 | Partitioned tables | The refusal is confirmed (`studio.plan_target_partitioned`), as built and documented | None (refused) | Full support (fingerprint and show every partition) only as its own work package | None |
| 6 | Data Editor scope of 1.1 | G-S2 with the scope as built; enum labels (catalog names, not values, at most 1000) reaching the page are confirmed | Security-neutral: the database is the last instance and a failed changeset rolls back completely | Bytes "replace by file", `constraint.*` errors at the cell, paging beyond the first page (planning document 15 section 4; G2-08): implemented in branch `claude/project-thread-rdo6g3-gs2-6a` (files of at most 64 KiB, `400 studio.data_editor_constraint` at the cell or row, up to 1,000 rows loaded 100 at a time; the flag stays off); independent review and merge open | Before the Studio 1.1 release |
| 7 | Tabrion's experimental names | `tabrion.rowedit.*` and `tabrion.errors.RowCountMismatch` (`X` in tabrion 0.3.0.dev0) accepted for G-S2 (internal activation) | The names may change with a tabrion update; the pin is fixed | tabrion 0.3 stabilizes them to `S` (02 section 14 S-2), Studio takes the new pin | Before the Studio 1.1 release |

## G-S3 definition (Schema Designer)

G-S3 is the owner's activation decision for the Schema Designer of Studio 1.2 (STU-CR-2..STU-CR-8,
STU-XSD-1..STU-XSD-3): the `schema_designer` flag of `write-areas.toml`. This section **defines** the gate (STU-CR-1);
it is not the gate and not its review. **G-S3 is not granted** and nothing of the area is built: the flag stays off
by default, nothing turns it on, no default changes. G-S3 would not allow a schema change on PROD (E-X7), a tag or a
release; the human security review (H-2) and G-R stay mandatory before any publication. The rules are
[STU-ADR-0007](../adr/0007-schema-designer-plans-and-revisions.md), the threats
[Schema Designer threat model](schema-designer-threat-model.md) (TX-18..TX-31).

From planning document 15 (section 10: "TM-DB-08, plan binding, drift check, revision only for Alembic, SQLite
rebuild"; sections 3.1 item 1, 7 and 9.4), doc 07 (gate table; section 6: an independent review agent for every
package with a write reference and for each gate), tabrion ADR 0015 sections 2 to 4 and 10 (D-5) and tabrion
contract I-124. Each item names the test that proves it; the gate review re-runs them.

**Preconditions (outside Studio):**

| Id | Precondition | How it is shown |
|---|---|---|
| GS3-P1 | The pinned Tabrion has the executor `tabrion.plan.execute_plan` (TAB-XW-5: plan hash and fingerprint, locks then drift check, dry run, `lock_timeout`, SQLite foreign keys off and `foreign_key_check`, K-1 again at execution, `environment` required) | `constraints/tabrion-integration.txt` names a commit that contains TAB-XW-5 (`71370a6`, after tabrion #91 `7c0da1a`); its review accepted |
| GS3-P2 | D-5 implemented, independently reviewed and tested in Tabrion (tabrion ADR 0015 section 10: "G-S3 stays locked until then") | tabrion TB-149 records D-5.1..D-5.4 done with an accepted review |
| GS3-P3 | The 0.3 G-T2 review (TAB-XW-10), or the owner's accepted exception for the experimental names `tabrion.schema` level 2, `tabrion.ddl`, `tabrion.plan` | tabrion STATUS / TB-149 |
| GS3-P4 | G-S2 granted (the shared write foundation the area uses) | the owner's G-S2 record |

**Checklist (each item a test or a review finding):**

| Id | Criterion | Evidence to bring | WP |
|---|---|---|---|
| GS3-01 | Flag off by default; every Schema Designer route answers 405 with the flag off, imports none of `tabrion.ddl`, `tabrion.plan` or the area's executor, and changes nothing (database bytes or schema, project folder, audit log) | `tests/security/test_write_kill_switch.py` extended to every new route; `CHANGING_ROUTES`. STU-CR-5: the routes `.../plan/confirm` and `.../plan/run` in the kill-switch sweep and `test_every_route.py`; `tests/security/test_schema_designer_execution.py` (405 with every flag off and with only `data_editor` on, nothing changed and no backup folder; a subprocess shows that nothing of the area is imported) | every WP |
| GS3-02 | The flag cannot be turned on from the page, a project file, a settings save, a query parameter or a header; the `data_editor` and `database_manager` flags do not open the area | `tests/security/test_write_flags.py` cases for `schema_designer` | STU-CR-5 |
| GS3-03 | PROD: direct execution and revision output refused (database creation: GS4-27) for a PROD connection, from the stored environment, with the flag on | a PROD test per route. STU-CR-5: `tests/security/test_schema_designer_execution.py`::`test_a_plan_runs_on_test_and_never_outside_dev_or_test` (TEST runs; the executor's own gate refuses every other environment an unlock could carry; Studio has no STAGE, and PROD is never unlocked: `test_write_unlock.py`); STU-XSD-2: `tests/security/test_schema_designer_compare.py`::`test_prod_is_compared_read_only_and_never_gets_a_plan` (a PROD connection is compared read-only as either side; a plan preview for PROD is refused with `compare.plan_prod`, the comparison still answered, the file unchanged) and `test_schema_designer_compare_postgres.py` | STU-CR-2..5, STU-XSD-3, STU-XSD-2 |
| GS3-04 | Direct execution refused for an unset environment, each migration marker (`alembic_version` in any schema, `nodestro_schema_migrations`, `axentra_schema_migrations`, `<prefix>orkexa_schema`, SQLite `user_version` above 0), an Alembic project without a version table, a marker added between preview and run, a non-transactional step | `test_schema_designer_execution.py` (planned). STU-CR-5: `tests/security/test_schema_designer_execution.py` (`alembic_version`, `NODESTRO_SCHEMA_MIGRATIONS`, an `axentra_schema_migrations` view and `user_version` added between the confirmation and the run, and at the preview; an `alembic.ini` in the project); `test_schema_designer_execution_postgres.py` (`orkexa_schema`, `<prefix>_orkexa_schema`, `alembic_version` in another schema, added after the confirmation). A non-transactional step is refused by `blocked()` (no step of the current planner is one with `concurrent_indexes=False`) | STU-CR-5 |
| GS3-05 | `execute_plan` gets `environment` from the stored profile only; a request field cannot set it | a spy on the call; a request with `environment` refused. STU-CR-5: `tests/security/test_schema_designer_execution.py`::`test_the_environment_comes_from_the_stored_profile_only` (a spy on `execute_plan`: `environment` from the stored profile, the recorded hash, the preview's fingerprint, the confirmed classes, `server_version`, `concurrent_indexes=False`; an `environment` key in the request refused); the same spy in `test_schema_designer_execution_postgres.py` | STU-CR-5 |
| GS3-06 | No `env.py`, project module, revision module or Alembic command ever runs: planted files that would leave a mark stay untouched, in the server and in the migration worker | `tests/security/test_schema_designer_revision.py`::`test_the_revision_is_written_and_nothing_runs` (`env.py` canary, database unchanged), `test_a_file_planted_after_the_trust_never_runs`, `test_the_area_imports_no_alembic_command_or_environment` (static) | STU-XSD-3 |
| GS3-07 | Revision file: only in a trusted project, only in its revision folder resolved without links, name `[0-9a-f]{12}_[a-z0-9_]{1,40}.py`, exclusive create, no overwrite; refused with a link as folder or file, an existing name, an untrusted project, several heads; refused on Windows (no `dir_fd`, review L-1 of #146) | `tests/security/test_schema_designer_revision.py`: `test_the_revision_is_written_and_nothing_runs`, `test_without_dir_fd_the_revision_output_is_refused`, `test_a_link_as_the_revision_folder_refuses`, `test_a_link_of_the_new_name_or_as_the_folder_is_never_followed`, `test_an_existing_name_is_never_overwritten`, `test_untrusted_profiles_untrusted_code_or_no_alembic_project_refuse`, `test_several_heads_refuse`, `test_a_head_that_is_not_a_revision_id_refuses` | STU-XSD-3 |
| GS3-08 | Revision text injection: a message and table names with `"""`, `\`, line breaks and NUL give a file whose non-statement parts are literals only (`ast.parse`) | `test_schema_designer_revision.py`::`test_quotes_and_backslashes_stay_literals` (a message and a column name with `"""`, `\` and `'`: literals only, `ast`); line breaks, NUL and other control characters in the message are refused (`test_a_replay_a_drift_and_a_wrong_message_are_refused`); tabrion K-3 vectors in tabrion's own tests | STU-XSD-3 |
| GS3-09 | Plans server-side only: a plan or target model in a request is ignored or refused; `from_json` is not called on outside input; a snapshot file cannot become a plan | route tests; static check. STU-CR-4: `tests/security/test_schema_designer_draft.py` (a `model`, `target`, `snapshot`, `plan`, `plan_json`, `steps`, `tables` or `draft` key refused with `400 access.bad_request` on every designer route, and an unknown key of an edit with `studio.schema_edit_invalid`, the draft unchanged; a static check that no module of the area calls `from_json`; the draft lives only in memory: the canary name of a draft table is in no file under the test's folders and the database is unchanged); STU-XSD-2: `tests/security/test_schema_designer_compare.py` (a snapshot file is compared and shown only: a spy on tabrion's `plan` is never called for a file side, from or to; a malformed, level-1, too deep, oversized or over-limit file is refused with `studio.schema_snapshot_invalid` or `studio.schema_snapshot_too_large`; at most one file; a `model`, `target`, `snapshot`, `plan_json`, `steps`, `environment` or `plan_hash` key refused with `400 access.bad_request`; a static check that the only `from_json` of the area is `SchemaSnapshot.from_json` in `compare.py`, which imports neither the executor nor `execute_plan` nor `tabrion.ddl`; the preview has no plan hash and replaces no previewed plan of the designer) | STU-CR-4, STU-CR-5, STU-XSD-2 |
| GS3-10 | Re-plan before the run: a database change after the confirmation that changes the plan refuses the run (`studio.plan_drift`) before any statement; the hash compared is the server's record of the confirmation; the allowed classes are those confirmed and must cover the new plan | drift and swap tests (SQLite and PostgreSQL). STU-CR-5: `tests/security/test_schema_designer_execution.py` (`test_a_change_after_the_confirmation_refuses_the_run_before_any_statement`, `test_the_executors_own_drift_check_is_plan_drift`); `test_schema_designer_execution_postgres.py`::`test_a_change_made_after_the_confirmation_is_drift_or_a_refusal` | STU-CR-5 |
| GS3-11 | STU-XW-3 binding for the area: a hash from another session, connection or unlock, and a reused hash, are refused | existing plan tests extended. STU-CR-5: `tests/security/test_schema_designer_execution.py` (`test_the_hash_is_the_servers_record_and_binds_session_unlock_and_one_run`, `test_an_edit_after_the_preview_drops_the_plan`, `test_an_expired_plan_is_refused`) | STU-CR-5 |
| GS3-12 | D-5 at run time: a trigger, a policy, row security and a referenced function body changed after the preview are drift; partitioned tables, partitions, domain columns, rules, foreign tables, inheritance, event triggers and a C trigger function refuse the plan with the table and feature named, never more; plan-time refusals `dependent_object` (a view or another trigger names the column or table), `unsupported_feature` with feature `view`, and identity or generated columns are shown as catalog problems | PostgreSQL tests with TAB-XW-5. STU-CR-5: a trigger added after the confirmation on PostgreSQL is refused by tabrion (`unsupported_feature`, feature `trigger`) in `test_schema_designer_execution_postgres.py`; a refusal names the table only by its position on the page (`tables.<n>`, `dropped.<n>`): `tests/security/test_schema_designer_execution.py`::`test_a_refusal_names_the_table_by_its_position_only` and the PostgreSQL draft test. Policy and function-body drift rely on tabrion's D-5 tests; STU-XSD-2: the comparison shows each side's feature markers and its verdict fails closed (`inconclusive` for a marker, `unknown` for a marker Studio does not know; `test_schema_designer_compare.py`, and on PostgreSQL a domain column, a partitioned table and a partition flagged, a changed trigger function and a dropped policy named as differences, as a non-superuser role: `test_schema_designer_compare_postgres.py`); a preview of a change of a marked table is refused with the feature's kind only | STU-CR-5, STU-XSD-2 |
| GS3-13 | `server_version` passed to every `plan` call (SQLite library version, PostgreSQL `server_version_num`) | a spy on every call. STU-CR-4: a spy on `tabrion.plan.plan` in `tests/security/test_schema_designer_draft.py` (every call during edits and the preview gets `sqlite3.sqlite_version_info`) and `test_schema_designer_draft_postgres.py` (every call gets the tuple from `SHOW server_version_num`); a static check that every `plan(` call in the area names `server_version`; STU-XSD-2: the comparison's preview gets the actual connection's version (spies in `test_schema_designer_compare.py` and `test_schema_designer_compare_postgres.py`) | STU-CR-4, STU-CR-5, STU-XSD-2 |
| GS3-14 | Risk classes: each `destructive` and `rebuild` step needs the typed table names, `data_changing` the named step; a wrong name refuses; the destructive action never has the default focus; `locking` shows the notice | pytest per class; E2E focus and axe. STU-CR-5: `tests/security/test_schema_designer_execution.py`::`test_each_class_needs_its_own_confirmation` (typed profile and table names, each `data_changing` step, the `locking` acknowledgement, the backup choice); `test_schema_designer_execution_postgres.py`::`test_a_locking_step_runs_after_its_acknowledgement`; neither Run nor Dry run focused: `SchemaDesigner.test.tsx` and `e2e/schema-designer-backend.spec.ts` with axe | STU-CR-5, STU-CR-7 |
| GS3-15 | Backup before every `destructive`, `rebuild` or `data_changing` run; a failed backup stops the run; opt-out only on `dev` with a typed confirmation, audited | SQLite and PostgreSQL tests (PostgreSQL with the verified `pg_dump` of STU-ADR-0008). STU-CR-5 (SQLite): `tests/security/test_schema_designer_execution.py` (backup 0600 in a 0700 folder with the old content, a failed backup and a backup folder that is a file stop the run, opt-out on DEV only with the typed file name and audited as `opted_out`, TEST refuses the opt-out, an additive plan needs none). PostgreSQL (STU-CR-5 follow-up): the verified `pg_dump` runner of STU-XDM-2 makes the archive before the run, which continues only when it succeeded; no tools, an unsafe configuration or tool, a tool older than the server, a failing tool, a profile changed since the unlock and Windows (`tool.platform`) refuse it with nothing changed (`test_schema_designer_pg_backup.py` with fake tools; `test_schema_designer_backup_postgres.py` with the real tools on DEV and TEST: the archive holds the dropped column and its rows, a role that may not read every table refuses, a tools file made unsafe after the preview refuses, a dry run makes none); without the tools such a plan stays `backup_unavailable` (`test_schema_designer_execution_postgres.py`); no opt-out on PostgreSQL | STU-CR-5, STU-CR-5 follow-up |
| GS3-16 | Preview warnings: PL/pgSQL references for every `drop_column`, `drop_table` and type change on PostgreSQL (I-124 j 2); `rowid` for a SQLite rebuild without `INTEGER PRIMARY KEY` (j 4); the fingerprint gaps (j 3); the revision's source condition (j 5); function drift (D-5.1) | page tests and E2E. STU-CR-5: `tests/security/test_schema_designer_execution.py`::`test_the_preview_warnings` (`not_modelled`, `sqlite_rowid`, `fingerprint_gaps`); `test_schema_designer_execution_postgres.py` (`pg_dependent_code` for drop column, drop table and a type change; `function_drift`; `locking`); page texts in `SchemaDesigner.test.tsx`. The revision's source condition (j 5): STU-XSD-3 `test_the_preview_offers_the_revision_and_says_it_does_not_check_the_source`, page text in `SchemaDesigner.test.tsx` | STU-CR-5, STU-XSD-3 |
| GS3-17 | SQLite rebuild: with foreign keys on refused and no row lost; `foreign_key_check` and `integrity_check` before the commit; a hostile `writable_schema` file refused (K-1); rows, triggers, views and referencing tables kept | SQLite tests through the executor. STU-CR-5: `tests/security/test_schema_designer_execution.py` (`test_a_rebuild_keeps_rows_triggers_views_and_child_rows`, `test_a_failed_check_before_the_commit_rolls_back`, `test_the_schema_authorizer`, `test_the_write_authorizer_is_back_after_the_run`); a hostile `writable_schema` file stays refused by the write connection (K-1, STU-XW-1 tests) | STU-CR-5 |
| GS3-18 | PostgreSQL `lock_timeout`: a held lock makes the run fail after the timeout with nothing changed | PostgreSQL test. STU-CR-5: `test_schema_designer_execution_postgres.py`::`test_a_lock_the_run_cannot_get_stops_it_and_changes_nothing` (`409 db.locked` after 5 s, nothing changed, the result audited) | STU-CR-5 |
| GS3-19 | Canaries: a marked default text and a marked secret absent from logs, the audit log, the history, diagnostics; the revision holds no connection data or secret | canary tests. STU-CR-5: `tests/security/test_schema_designer_execution.py`::`test_the_run_is_audited_with_hash_tables_classes_and_steps` (a canary default text in no log record, audit record or history entry; the plan hash not in the log) | STU-CR-5, STU-XSD-3 |
| GS3-20 | Errors: every Tabrion refusal and driver error is a catalog problem (`studio.plan_*`, `studio.schema_*`, `db.<kind>` from `safe_message`), never the server's text | problem tests. STU-CR-5: the new problems `studio.schema_run_not_allowed`, `schema_backup_required`, `schema_backup_failed`, `schema_run_check_failed`, `schema_run_outcome_unknown`, `schema_run_failed` (issues `plan.reason`, `plan.check`), tabrion's lock timeout as `db.locked`; tests in `tests/security/test_schema_designer_execution.py` and `test_schema_designer_execution_postgres.py` | every WP |
| GS3-21 | Database creation (STU-CR-2, STU-CR-3; moved to the admin area G-S4 on 2026-11-18, evidence now GS4-27): SQLite file 0600 and never through a link, created relative to the held, checked folder and checked by device and inode before it is opened; every folder above it of root or the user and not writable by group or others unless sticky; Studio's own folders (configuration, state, audit, log, the project's `.tabrion`) refused by device and inode, also below them and under a second name; no space other than U+0020 in a file, database or owner name; on Windows no SQLite file (`studio.schema_create_platform`); PostgreSQL owner checks, PROD refused, a duplicate database without server text | `tests/security/test_dbm_create.py` (the folder above replaced by a link after the check, a folder above writable by others with and without the sticky bit, the file replaced before it is opened, the reserved folders by identity incl. a bind mount where the test may mount, the Windows refusal), `tests/security/test_dbm_create_postgres.py` | STU-CR-2, STU-CR-3; review of PR #128 (P128-01..03, open points 5 and 7) |
| GS3-22 | Runtime role and export (STU-CR-6): the role rules GS4-12..GS4-17 that apply; a multi-host export refused; no write or admin secret in an export | role and export tests | STU-CR-6 |
| GS3-23 | Graphical mode: the canvas uses the same edit routes as the table view; the server refuses each invalid edit from either; table alternative and axe 0 serious or critical | pytest and E2E. STU-XSD-1: `tests/security/test_schema_designer_canvas.py` (every Schema Designer route is the table view's, none the diagram's; `SchemaCanvas.tsx` and `schemaCanvasModel.ts` call no route and import only types from the API; a `view`, `source`, `mode` or `canvas` key refused in the request and in the edit; each invalid key the diagram can draw refused with the table view's catalog problem and the draft unchanged; a valid drawn key planned like any edit); `frontend/src/views/write/SchemaCanvas.test.tsx` (the drawn key only fills the form, Apply sends the table view's `add_constraint` to `.../draft/edits`, the server's refusal shown at the field; axe in jsdom); `frontend/e2e/schema-designer-backend.spec.ts` (the keyboard path to a table's card, a key drawn with the pointer and applied, the preview, nothing run, axe in light and dark and the CSP, in each browser) | STU-XSD-1 |
| GS3-24 | Docs: the guide states revision only for Alembic projects, the PROD lock, DEV/TEST only for direct execution, the backup rule and the preview warnings (doc 15 section 9.4) | docs checklist | STU-CR-7 |
| GS3-25 | An independent G-S3 review agent (doc 07 section 6) accepts the area; every High and Medium finding fixed and re-reviewed | `reviews/gs3-gate-review.md` (planning documents) | STU-CR-8 |
| GS3-26 | The owner's decisions for G-S3 recorded (below) | docs/TODO.md | - |
| GS3-27 | Audit: every run with `run.start` and `run.result` (direct execution, revision output, an opted-out backup; database creation: GS4-27); no value, no default or check text beyond the plan hash; an unwritable log stops the run (ADR 0007 section 11) | audit tests. STU-CR-5: `tests/security/test_schema_designer_execution.py` (`run.start` and `run.result` with action, environment, tables, hash, classes, steps, backup state and result; an unwritable audit log stops the run before the backup) | STU-CR-2..5, STU-XSD-3 |
| GS3-28 | G-S2 decision 1A applies to the Schema Designer unlock: a write role that has or reaches superuser, bypassrls, replication or a server-file role is refused with 405 `studio.write_role_over_privileged` | unlock test for `schema_designer`; database creation is no longer a Schema Designer route (GS4-27). CREATEDB (owner grant of 2026-11-18, G-S4, superseding open point 1 of the PR #128 review): a write role that has or reaches CREATEDB is refused like decision 1A (`admin.createdb`, `dangerous`), at the unlock and again at each run (`test_write_privileges.py`::`test_a_write_role_with_createdb_is_refused`, `test_a_write_role_that_gained_createdb_is_refused_at_the_run`; `test_dbm_create_postgres.py`::`test_the_admin_role_needs_createdb_and_a_write_role_may_not_hold_it`). STU-CR-5: `test_schema_designer_execution_postgres.py`::`test_the_write_role_is_checked_again_when_the_run_starts` (made superuser after the unlock: refused on the run's own session before `BEGIN`, nothing changed, the unlock ends) | STU-CR-3, STU-CR-5 |
| GS3-29 | A dry run (`execute_plan(dry_run=True)`) only on the user's explicit request, never automatic, with the run's confirmations and unlock, a notice of the `ACCESS EXCLUSIVE` locks and of sequence values that stay consumed, audited like a run (ADR 0007 section 4 item 5) | dry-run tests. STU-CR-5: `tests/security/test_schema_designer_execution.py`::`test_a_dry_run_only_on_request_rolls_back_and_is_audited` (`dry_run` must be a boolean; rolled back; audited as `schema_designer.dry_run`; no backup); the page's notice names the locks and the consumed sequence values; E2E dry run then run | STU-CR-5 |
| GS3-30 | The shared PostgreSQL runner (review L-2 a of STU-XDM-1): when the Schema Designer uses `tabrion_studio.write.pgtools` under its own flag for the backup before a destructive run, GS4-04, GS4-05, GS4-06, GS4-07 and GS4-22 are conditions of G-S3 too; read, write and admin profiles follow the same connection rules (SCRAM, `verify-full` remote) | the GS4 evidence; STU-XDM-2 builds the runner outside the Database Manager's package. STU-CR-5 follow-up uses it from `schema_designer.execute` (no import of the Database Manager's package): the same tool checks (GS4-04), process (GS4-05), passfile (GS4-06; stale run files removed when the area starts) and archive checks with metadata (GS4-07), with `run.start` before and `run.result` after (GS4-22); `test_schema_designer_pg_backup.py`, `test_schema_designer_backup_postgres.py` | STU-CR-5 follow-up |

**Open decisions for the owner at G-S3** (recorded in [docs/TODO.md](../TODO.md#g-s3-and-g-s4-definitions)):
whether a loaded snapshot may start a draft; whether revision output may be offered for a PROD connection (it writes only the project file); whether drafts may be saved to disk; whether direct execution may ever
include non-transactional steps; (decided by the grant of 2026-11-18: database creation is the admin area's, ADR 0008 section 10); that the runtime-role wizard needs the `database_manager` area and G-S4 (ADR 0007
section 12); the release exception for Tabrion's experimental names.

## G-S4 definition (Database Manager)

G-S4 is the owner's activation decision for the Database Manager of Studio 1.3 (STU-XDM-2..STU-XDM-7): the
`database_manager` flag of `write-areas.toml`. This section **defines** the gate (STU-XDM-1); it is not the gate and
not its review. **G-S4 is not granted:** the flag stays off by default, no default changes. STU-XDM-2 builds backup
and restore behind the flag (the evidence below); the review of STU-XDM-1 accepted the definition with conditions
(M-1, L-1..L-3, A-1), folded in by STU-XDM-2. G-S4 would not allow restore, delete or role changes on PROD (E-X7: backup only), a tag or a release; H-2
and G-R stay mandatory before any publication. The rules are
[STU-ADR-0008](../adr/0008-database-manager-backup-restore-roles.md), the threats
[Database Manager threat model](database-manager-threat-model.md) (TX-32..TX-46).

From planning document 15 (section 10: "backup, restore (restore tests), delete, role escalation (indirect
privileges, minimal admin profile)"; sections 3.1 items 2 and 3, 4, 7 and 9.4), doc 07, tabrion ADR 0015 sections 5
and 6, tabrion contract I-170, I-171, I-180..I-186, I-190..I-193 and the "Open for Studio before G-S4" list of tabrion
TODO TB-149.

**Preconditions (outside Studio):**

| Id | Precondition | How it is shown |
|---|---|---|
| GS4-P1 | The pinned Tabrion has TAB-XW-7, TAB-XW-8 and TAB-XW-9 with their reviews' conditions folded in | in the pin (tabrion PRs #88 and #90, reviews accepted with conditions); the pin is `3520b21` (`constraints/tabrion-integration.txt`) |
| GS4-P2 | The 0.3 G-T2 review (TAB-XW-10), or the owner's accepted exception for the experimental names used | tabrion STATUS / TB-149 |
| GS4-P3 | G-S2 granted (G-S3 too where the area shares its backup runner or creation wizards) | G-S2 granted 2026-11-15 (the owner's record); G-S3 open |
| GS4-P4 | CI runs `pg_dump` and `pg_restore` of at least the server's major version and a PostgreSQL 16 or later server | the `postgres` job; `tests/security/test_dbm_restore_roundtrip.py` finds the tools through `TABRION_STUDIO_TEST_PG_BIN`, `/usr/lib/postgresql/*/bin` (newest first) or `PATH` and skips without them; whether the job has them is checked on the STU-XDM-2 PR |

**Checklist:**

| Id | Criterion | Evidence to bring | WP |
|---|---|---|---|
| GS4-01 | Flag off by default; every Database Manager route answers 405 with the flag off, imports none of the area's modules or Tabrion's role, restore, delete or database functions, starts no process and changes nothing | `tests/security/test_write_kill_switch.py` extended; `CHANGING_ROUTES`. STU-XDM-2: the four routes in the sweep (31 calls) and `CHANGING_ROUTES`; `tests/security/test_dbm_backup_restore.py`::`test_with_the_flag_off_no_module_of_the_area_is_imported` (a subprocess: neither the area's package nor the runner imported, no backup folder) | every WP; STU-XDM-4: the three role routes in the sweep and `CHANGING_ROUTES`; `tests/security/test_dbm_roles.py`::`test_the_roles_are_off_by_default_and_with_another_areas_flag`, `test_with_the_flag_off_the_roles_module_is_not_imported` |
| GS4-02 | The flag cannot be turned on from outside the owner's configuration; the other areas' flags do not open it | `tests/security/test_write_flags.py` cases. STU-XDM-2: `tests/security/test_dbm_backup_restore.py`::`test_another_areas_flag_never_opens_the_database_manager`; an admin profile unlocks only under `database_manager` (`KIND_AREAS`) | STU-XDM-2 |
| GS4-03 | PROD: backup only (with the read profile); restore, delete and role changes refused, also through a non-PROD profile of a target that a PROD profile names | a PROD test per route. STU-XDM-2: restore into a PROD-named target refused: `tests/security/test_dbm_backup_restore.py`::`test_a_target_that_a_prod_profile_names_is_refused` (the same file under a hard link), `tests/security/test_dbm_restore_roundtrip.py`::`test_a_name_a_prod_profile_names_under_an_alias_is_refused` (`localhost` against `127.0.0.1`, review L-1); a PROD backup with the read profile is not built yet (every action needs an admin unlock, which PROD never gets: refused, an open point) | STU-XDM-2..4; STU-XDM-4: role routes refused on a server a PROD profile names under an alias (`tests/security/test_dbm_roles_postgres.py`::`test_a_server_a_prod_profile_names_under_an_alias_is_refused`) |
| GS4-04 | Tool path only from the owner's configuration folder; refused: relative, `PATH` only, a link, a group- or world-writable file or folder, a foreign owner, a wrong program, an older major than the server (and the archive for `pg_restore`); a project file or settings save cannot set it | `test_dbm_tools.py` (planned) with fake tools. STU-XDM-2: `tests/security/test_dbm_tools.py` (a configured private tool, `PATH` only, a tools file that is not private, relative, missing, a link, writable by others, a writable folder, a folder above it that others may write without the sticky bit (`test_every_folder_above_the_tool_is_checked`, review L-1 of PR #136), a foreign owner, a wrong program, older than the server or the archive, Windows); `tests/security/test_dbm_backup_restore.py`::`test_no_setting_or_profile_names_a_tool` | STU-XDM-2 |
| GS4-05 | Process: argument list, no shell, `--no-password`, timeout, `stdin` closed; the child's environment has no inherited `PG*` variable; planted `PGHOST`, `PGSERVICE` with a service file and `PGSSLMODE=disable` change nothing; `require_auth` SCRAM and `verify-full` for a remote host reach the tool | a recording fake tool; a real `pg_dump` in the `postgres` job. STU-XDM-2: `tests/security/test_dbm_tools.py`::`test_the_tool_gets_an_argument_list_a_clean_environment_and_the_passfile`, `test_a_planted_service_refuses_the_tool_as_it_refuses_studios_connections`, `test_a_timeout_kills_the_tools_process_group_and_removes_the_passfile`, `test_never_psql_and_never_row_security_or_a_script` (no shell, no `--enable-row-security`, review L-2 c); `tests/security/test_dbm_restore_roundtrip.py`::`test_planted_libpq_variables_do_not_redirect_the_tool` (a real `pg_dump`) | STU-XDM-2 |
| GS4-06 | Secret: canary password absent from `argv`, the child's environment, the log, the metadata and the answer; the passfile 0600 in a 0700 folder and gone after success, failure and timeout; a stale passfile removed at start | canary tests. STU-XDM-2: `tests/security/test_dbm_tools.py` (the canary password absent from `argv`, the environment, the log and the answer; the passfile 0600 in a 0700 folder and gone after success, failure and timeout; `test_a_stale_passfile_is_removed_at_start`); `tests/security/test_dbm_backup_restore.py`::`test_a_backup_is_private_described_and_audited` (no secret in the metadata) | STU-XDM-2 |
| GS4-07 | Backup files: folder 0700, files 0600 created exclusively, never through a link; metadata with SHA-256, size, tool and version, server version, fingerprint and the scope notice; no secret | file tests. STU-XDM-2: `tests/security/test_dbm_backup_restore.py`::`test_a_backup_is_private_described_and_audited`, `test_a_backup_that_is_a_link_is_refused`; `tests/security/test_dbm_tools.py`::`test_an_existing_backup_name_is_never_overwritten`, `test_a_failing_tool_leaves_nothing_and_answers_a_class_only`, `test_an_unreadable_table_of_contents_leaves_no_backup` (review L-3) | STU-XDM-2 |
| GS4-08 | Restore tests: automated round trips (backup, restore into a new database, integrity and level-2 fingerprint equal) on SQLite and PostgreSQL, in CI | `test_dbm_restore_roundtrip.py` (planned). STU-XDM-2: `tests/security/test_dbm_backup_restore.py`::`test_a_backup_restores_into_a_new_file`, `test_a_backup_restores_over_the_dev_file_after_its_own_backup`; `tests/security/test_dbm_restore_roundtrip.py`::`test_a_backup_restores_into_a_new_database_as_the_restore_role` (integrity, level-2 fingerprint equal, rows equal) | STU-XDM-2 |
| GS4-09 | Restore refusals: PROD, a PROD-labeled target, an archive from outside the backup folder, a plain-format file, an edited archive or metadata, a target swapped between preview and run; the target's backup taken first; `pg_restore` with `--single-transaction --exit-on-error --no-owner --no-privileges`; `psql` never started | restore tests. STU-XDM-2: `tests/security/test_dbm_backup_restore.py` (outside the folder, edited archive or metadata, a link, a swapped target, a wrong typed name, the target's backup first, connections to a replaced file closed: review I-4); `tests/security/test_dbm_tools.py`::`test_a_restore_streams_the_checked_archive_with_the_fixed_options`, `test_a_plain_sql_file_is_never_restored`, `test_never_psql_and_never_row_security_or_a_script`; `tests/security/test_dbm_restore_roundtrip.py`::`test_an_archive_function_cannot_create_a_role_during_the_restore` and `test_the_restore_role_must_be_a_separate_lean_role` (review M-1), `test_only_a_new_database_is_a_target_and_an_existing_name_is_refused`, `test_an_edited_archive_is_never_restored` | STU-XDM-2 |
| GS4-10 | Delete: SQLite through `delete_database_file`, Studio's own files and folders refused by device and inode; PostgreSQL protected names refused; a backup and the exact typed name before every delete; `force=True` only when chosen explicitly with its own confirmation, never a default or automatic retry | STU-XDM-3: `tests/security/test_dbm_delete.py`::`test_a_delete_backs_up_verifies_closes_and_deletes`, `test_a_file_in_one_of_studios_own_folders_is_refused`, `test_a_file_in_a_folder_others_may_write_is_refused`, `test_an_unverified_backup_stops_the_delete`, `test_dbm_delete_postgres.py`::`test_a_schema_changed_during_the_dump_stops_the_delete` and `test_an_unreadable_fingerprint_stops_the_delete` (review L-1 of PR #140: no fingerprint, no drop), `test_a_failed_backup_stops_the_delete`, `test_a_wrong_typed_name_deletes_nothing`, `test_force_and_a_name_from_the_request_are_refused_on_sqlite`, `test_a_sqlite_delete_is_refused_on_windows`; `tests/security/test_dbm_delete_postgres.py`::`test_a_database_is_deleted_after_its_verified_backup`, `test_maintenance_template_connected_and_unusable_names_are_refused`, `test_other_sessions_need_force_and_its_own_confirmation`, `test_force_is_not_offered_without_other_sessions`, `test_a_failed_backup_stops_the_delete`, `test_a_database_swapped_after_the_backup_is_refused` | STU-XDM-3 |
| GS4-11 | Over-privileged admin profile: SUPERUSER, BYPASSRLS, REPLICATION, a server-access role, CREATEROLE before 16 (on the role or a reachable role) refuse the admin unlock (`studio.admin_over_privileged`); CREATEROLE on 16 and later and the other findings are shown | PostgreSQL tests with prepared roles. STU-XDM-2: every Database Manager action refuses `admin_over_privileged` findings on its own session and ends the unlock: `tests/security/test_dbm_restore_roundtrip.py`::`test_an_admin_role_that_became_superuser_is_refused`; refusing the unlock itself waits for the owner's G-S4 decision | STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles_postgres.py`::`test_an_admin_role_that_became_superuser_is_refused`, `test_an_admin_role_with_a_server_access_role_is_refused` |
| GS4-12 | Role escalation refused through Studio's routes for each path: a banned attribute directly, through INHERIT, SET or ADMIN membership, a predefined role, a server-access role, TRIGGER, EXECUTE on a foreign SECURITY DEFINER function, CREATE on `public` or a foreign schema, a system object, an object of a superuser owner; nothing changed | PostgreSQL tests (Tabrion's matrix through the routes) | STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles_postgres.py`::`test_a_banned_attribute_is_refused`, `test_a_membership_in_a_server_access_role_is_refused`, `test_a_membership_in_a_role_with_a_dangerous_attribute_is_refused`, `test_a_group_with_trigger_in_another_database_is_refused`, `test_tabrions_privilege_refusals_reach_the_route` (security definer, CREATE on `public`, a system object); `tests/security/test_dbm_roles.py`::`test_a_banned_attribute_is_never_accepted`, `test_the_request_fits_its_action` |
| GS4-13 | Studio never changes its own admin role and never sets up admin rights; role DDL refused before PostgreSQL 16 | tests | STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles_postgres.py`::`test_studios_own_roles_are_never_changed` (the admin role and the read profile's user); PostgreSQL 16 checked first (`studio.dbm_role_requires_16`); review L-1 and I-2 of #149: `tests/security/test_dbm_roles_postgres.py`::`test_a_privilege_for_a_group_above_an_own_role_is_refused`, `test_a_membership_for_a_group_above_an_own_role_is_refused`, `test_changing_a_group_above_an_own_role_is_refused`, `test_a_profile_without_a_user_counts_its_os_user` |
| GS4-14 | Preview of every role change: `role_privileges` of the target (attributes, memberships, reachable roles, ownership, default privileges, findings); the warnings about `public_definers` (`superuser_owner` first) and CREATE on a database | page tests and E2E | STU-XDM-4; STU-XDM-4: the preview answers `role_privileges` of target and member, refusals, warnings and the K-2 coverage, and every change is bound to it (`tests/security/test_dbm_roles_postgres.py`::`test_a_changed_role_after_the_preview_is_refused`); page tests in `frontend/src/views/write/DatabaseManager.test.tsx` (the role management block, axe included) |
| GS4-15 | After every commit `role_privileges` is read again and a difference is shown (P90-06) | a test with a concurrent membership | STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles_postgres.py`::`test_a_change_after_tabrions_commit_is_shown_as_a_difference` |
| GS4-16 | Before a membership change the K-2 grants are checked in every database the admin profile can connect to, and the preview names those it could not (P90-04) | a test with a K-2 grant in a second database | STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles_postgres.py`::`test_a_group_with_trigger_in_another_database_is_refused` |
| GS4-17 | Names with Cc, Cf, Zl or Zp characters are shown escaped in the page, the preview, the audit log and the log; a destructive action on such a name is refused (P90-05) | tests with a right-to-left override, a zero-width space, a line separator. STU-XDM-3: a delete of a name with a hidden character is refused (`test_maintenance_template_connected_and_unusable_names_are_refused`); the escaped display waits for STU-XDM-4 | STU-XDM-3, STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles.py`::`test_every_invisible_character_is_shown_as_a_code_point`, `test_a_name_that_cannot_be_typed_is_refused`; `tests/security/test_dbm_roles_postgres.py`::`test_a_hidden_name_is_escaped_in_the_list_and_refused` |
| GS4-18 | Role passwords: canary absent from logs, the audit log, answers and a failing call's problem; only Tabrion sends it (as a verifier) | canary tests | STU-XDM-4; STU-XDM-4: `tests/security/test_dbm_roles_postgres.py`::`test_a_login_role_is_created_without_the_password_leaking`; `tests/security/test_dbm_roles.py`::`test_the_described_request_never_holds_the_password` |
| GS4-19 | Admin profile connection rules at least those of a write profile: SCRAM for a password, `verify-full` for a remote host, the I-109 j confirmation (P90-07); `pin_libpq_environment` applies to every admin connection, so no `PG*` variable reaches it (G2-01) | `test_write_secret_confirm.py` cases for `admin` | STU-XDM-4, STU-XDM-5 |
| GS4-20 | Driver and Tabrion errors only as catalog problems (`db.<kind>` from `safe_message`, `studio.dbm_*`): a duplicate database, a missing privilege, an object in use answered without server text | problem tests with a marked name in the server message. STU-XDM-3: `drop_database` refusals map to fixed `studio.dbm_delete_*` problems with fixed `delete.*` issues, driver errors to `db.<kind>` with `safe_message` only in the log; a failed `pg_dump` answers `studio.backup_tool_failed` with `tool.permission` (`test_a_failed_backup_stops_the_delete`) | STU-XDM-3, STU-XDM-4; STU-XDM-4: Tabrion's role errors as fixed `studio.dbm_role_*` problems with `role.*` issues, driver errors as `db.<kind>` |
| GS4-21 | Profile pairs: K-1 on import, the I-109 j confirmation for every imported pair, single-host only for import and export, no write or admin secret exported | `test_dbm_profiles.py` (planned) | STU-XDM-5 |
| GS4-22 | Audit: every action with `run.start` and `run.result` (backup, restore, delete with `force` as its own field, each role change, an opted-out backup); no value, no password; an unwritable log stops the action | audit tests. STU-XDM-2: `tests/security/test_dbm_backup_restore.py`::`test_a_backup_is_private_described_and_audited` (`run.start`, `run.result` with file, SHA-256 and result; no path or value), `test_an_unwritable_audit_log_stops_the_backup` (405, no backup). STU-XDM-3: `dbm.delete` with `target`, `pre_backup`, `sha256`, `force` and `sessions` (`test_a_delete_backs_up_verifies_closes_and_deletes`, `test_other_sessions_need_force_and_its_own_confirmation`), `tests/security/test_dbm_delete.py`::`test_an_unwritable_audit_log_stops_the_delete` | STU-XDM-2..5 |
| GS4-23 | Docs: the guide states that a `pg_dump` archive is not a complete backup, the PROD lock, the role rules and the admin profile's minimal rights provisioned outside Studio (doc 15 section 9.4) | docs checklist | STU-XDM-6 |
| GS4-24 | E2E in three browsers and axe for the area's dialogs (D-11..D-14) | E2E | STU-XDM-6 |
| GS4-25 | An independent G-S4 review agent accepts the area; every High and Medium finding fixed and re-reviewed | `reviews/gs4-gate-review.md` (planning documents) | STU-XDM-7 |
| GS4-26 | The owner's decisions for G-S4 recorded (below) | docs/TODO.md | - |
| GS4-27 | Database creation (STU-CR-2, STU-CR-3) in the admin area (owner grant of 2026-11-18): both routes behind `database_manager` (405 and `tabrion_studio.write.database_manager.create` not imported with every flag off and with `schema_designer` and/or `data_editor` on); PostgreSQL only on an admin unlock, a write unlock refused (`studio.method_not_allowed`), the admin check again before `CREATE DATABASE` (`studio.admin_over_privileged`, ends the unlock); SQLite without an admin unlock (no role exists), with the rules of GS3-21, the Windows refusal and PROD refused; audit `dbm.create_sqlite`, `dbm.create_postgresql` (area `database_manager`), an unwritable log stops it | `tests/security/test_dbm_create.py` (`test_flag_off_both_routes_are_405_and_change_nothing`, `test_flag_off_nothing_of_the_area_is_imported`, `test_the_areas_route_says_the_database_manager_is_off_by_default`, `test_a_write_profile_never_creates_a_database`, the folder, name, Windows and PROD tests, the audit), `tests/security/test_dbm_create_postgres.py` (`test_the_admin_role_needs_createdb_and_a_write_role_may_not_hold_it`, `test_a_right_gained_after_the_unlock_refuses_the_run_and_ends_the_unlock`, `test_the_admin_session_is_left_as_it_was`), `CreateDatabase.test.tsx`, `e2e/create-database-backend.spec.ts` | STU-CR-2, STU-CR-3 (moved) |

**Open decisions for the owner at G-S4** (recorded in [docs/TODO.md](../TODO.md#g-s3-and-g-s4-definitions)): the
admin-profile consequence proposed in STU-ADR-0008 section 6; the tool path from the owner's configuration rather
than the page's settings; PostgreSQL restore only into a new database in 1.3; backup retention; the release
exception for Tabrion's experimental names.
