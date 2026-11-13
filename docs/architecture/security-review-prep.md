# Security review preparation (STU-016, G-S1)

This page prepares the security gate **G-S1** of the Studio MVP. It is a readiness checklist and the result of an
internal pre-review pass, **not** the gate: G-S1 is decided by the owner after an independent security review and its
re-review, and a human security review before a public release stays mandatory (STU-028, gate G-R). Nothing here
replaces either review.

State: prepared at master `e9df576`; updated after the first independent review of master `e186178` (section
[Independent review](#independent-review-first-round)). Sensitive specifics of the pre-review and of the review, such
as reproduction steps, are kept outside the repository, with the planning documents.

## What G-S1 asks

From the security and quality plan (doc 07, gate table) and the backlog entry of STU-016 (doc 09):

| G-S1 criterion | State | Evidence |
|---|---|---|
| SQ-01..SQ-08 green, each with its STU-T tests | Ready for review | [SQ requirements](#sq-01sq-08) below |
| SQ-01..SQ-08 after the independent review | **Not met at `e186178`:** SQ-05 and SQ-07 (review F-1, F-2); SQ-06 met with F-4 | Fixes in the read-only hardening PR and the request-limits PR; re-review pending |
| Every threat-model measure (doc 03 section 7.3, T-01..T-16) proven by a test | **Not met at `e186178`** for T-06 and T-13 (review F-1, F-2); the write-mode rows are G-S2 | [Threat model](#threat-model-t-01t-16) below |
| Review report "accepted": a first review, the fixes, a re-review on merged SHAs | **Open:** the first independent review (`e186178`) is **not accepted**; fixes in progress, re-review pending | [Independent review](#independent-review-first-round) below |
| `npm audit --omit=dev` without High | Met (0 findings at `e9df576`) | CI job `dependency audit` |
| axe: 0 serious or critical | Met for the existing views (`e2e`, every view in light and dark, en and de) | `frontend/e2e/*.spec.ts`, `docs/architecture/accessibility.md`; the rest of STU-053 is TD-022 |

## SQ-01..SQ-08

| SQ | Requirement | Where it is enforced | Tests |
|---|---|---|---|
| SQ-01 | Listen on loopback only | `server.py` binds `127.0.0.1` (and holds `[::1]`); the CLI has no host option | STU-T-025: `tests/unit/test_cli.py::test_bad_arguments`, `tests/security/test_launch.py` |
| SQ-02 | Host and Origin allowlist (DNS rebinding) | `security/access.py`: `127.0.0.1:<port>` only, the own Origin and same-origin fetch metadata on every API request | STU-T-001..003: `tests/security/test_access.py`; every API route: `tests/security/test_every_route.py` |
| SQ-03 | Launch token, then an HttpOnly SameSite=Strict cookie and a CSRF header | `security/launch.py`, `security/sessions.py` | STU-T-004..006: `test_access.py`, `test_launch.py`; every API route: `test_every_route.py` |
| SQ-04 | Secrets only in the backend | `config/secrets.py` (keyring without plain-text fallback, `env:`, `file:`), `tabrion.safe_url()`, log redaction in `logs.py` | STU-T-008/-009: `test_canary.py`, `test_secrets_api.py`, `test_log_process.py`, `test_diagnosis_report.py`; through the routes of the editor, viewer, export and diagnosis: `test_canary_flows.py` |
| SQ-05 | Read-only enforced by the database | `services/connections.py`, `services/readonly.py` (SQLite `mode=ro`, `query_only`, `trusted_schema` off, authorizer with a function allowlist; PostgreSQL read-only session and transactions), text check as a second layer. **Not met at `e186178`** (review F-1, F-2); fixed in the read-only hardening PR (the text check refuses any `INTO`, the pool reset drops temporary tables, the authorizer allows only SQLite's built-in reading functions), re-review pending | STU-T-010..013, -024 and the F-1/F-2 tests: `tests/security/test_read_only.py` (PostgreSQL part in the `backend integration (PostgreSQL 17)` CI job), `test_queries.py` and `test_exports.py` (editor and export route) |
| SQ-06 | Limits and cancel | `services/queries.py`, `viewer.py`, `exports.py`; limits in the configuration; request body caps enforced while the body streams in, before any route reads it (`security/body.py`, review F-4) | STU-T-018..020: `test_queries.py`, `test_viewer.py`, `test_exports.py`, `frontend/e2e/editor-backend.spec.ts`; body caps: `tests/security/test_body_limits.py` |
| SQ-07 | PROD marking; no writes before G-S2 | Environment badge, frame and title (STU-051); D-02 on the server (`confirmed_environment`, TD-038); no write route exists (STU-026 is locked). **Not met at `e186178`** until F-1 is fixed (the server-side D-02 itself is met) | `test_read_only.py::test_td_038_*`, `test_connections_api.py`, frontend D-02 tests; `test_every_route.py` lists every route |
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
| T-06 Accidental writes | Read-only in the database, prod marking; the text check refuses `INTO` (F-1) and side-effect functions (F-3); `STU-DX-003` reports schema `CREATE` and database `TEMP` | STU-T-010..013 and the F-1/F-3 tests (`test_read_only.py`, `test_queries.py`, `test_exports.py`, `test_diagnostics.py`) | **Not met at `e186178`** (review F-1); fixed with tests in the read-only hardening PR, re-review pending. The write mode is G-S2 |
| T-07 XSS through database text | React text only, ESLint rule against `dangerouslySetInnerHTML`, CSP | STU-T-014; no STU-T-015 label, in substance `test_access.py::test_eslint_forbids_dangerously_set_inner_html` and hostile-value tests of the views | Covered |
| T-08 Hostile project | Project trust (D-01), bound to digests; script paths (STU-020) judged as text, then reached through folder descriptors with `O_NOFOLLOW` (07 TM-04), no device, 8.3 or format-character names, no hard links | STU-T-016, -017 (`test_project_trust.py`); `tests/security/test_scripts.py` | Covered on POSIX; on Windows a folder swapped between the check and a save's rename remains (documented in [History, favorites and scripts](history-and-scripts.md)) |
| T-09 Resource exhaustion | Row, byte and cell caps, timeouts, cancel, parallel limits; request body caps while streaming (review F-4) | STU-T-018..020; `test_body_limits.py` | Covered |
| T-10 TLS downgrade | `STU-DX-002` for PROD without verify-full | STU-T-021 (`test_diagnostics.py`) | Covered as a finding; Studio does not refuse such a profile |
| T-11 Supply chain | Locked dependencies, license policy, SBOM, audits | No STU-T-022 label; CI jobs `license and supply chain`, `dependency audit` | Covered; TD-030 (dev-only advisory), TD-031 (hashed pins in CI) |
| T-12 Change log | Writes logged | STU-T-023 | Not applicable before G-S2 (no write mode) |
| T-13 SQLite extensions and `ATTACH` | Authorizer as an allowlist, functions included (F-2); `trusted_schema` off | STU-T-024 and the F-2 tests (`test_read_only.py`) | **Not met at `e186178`** (review F-2); fixed with tests in the read-only hardening PR, re-review pending |
| T-14 Network exposure | No `--host` | STU-T-025 | Covered |
| T-15 Cached answers | `Cache-Control: no-store` | STU-T-026; every refusal in `test_every_route.py` | Covered |
| T-16 Trusted project code (Alembic) | Code trust bound to file digests, isolated worker | `test_migrations.py` | Covered; the digest does not cover project modules that revision files import (documented; review F-7, owner decision) |

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
- **Local-only binding:** `127.0.0.1` only, `localhost` and `[::1]` refused by the Host allowlist, no host option.
- **Packaging:** the wheel carries the built frontend only; the e2e harness never ships
  (`tests/packaging/test_artifacts.py`). Build and install are checked on three operating systems in CI.
- **Dependency audit at `e9df576`:** `pip-audit` clean; `npm audit --omit=dev` 0 findings; the full npm audit has 11
  moderate advisories in development tools only (TD-030).
- **Logs:** uvicorn's access log is off; the request log records the route template, never a query string.

## Open security-relevant TD rows

| Row | Priority | Subject |
|---|---|---|
| TD-002 | P2 | Sandboxed browsers cannot open the redirect file (in progress) |
| TD-003 | P3 | Windows: DACL check inside the profile |
| TD-004, TD-006 | P3 | Workflow-change gate: fork pull requests, who may set the label |
| TD-005 | P3 | Spent launch URL in the browser history (accepted risk) |
| TD-007 | P3 | Development proxy and real-browser launch checks |
| TD-008 | P1 | A `windows-latest` CI job for the T-2 checks |
| TD-027 | P3 | Two concurrent opens of one profile can both connect |
| TD-030, TD-031 | P3, P2 | Dev-only npm advisory; hashed pins in CI |
| TD-033 | P2 | SQL editor leftovers (splitter, error positions, column resizing); the drafts part (SQL text in browser storage, F-5) is done |
| TD-038 | P2 | Remaining limits of the D-02 gate and of the connect error states |

## Residual risks (doc 03 section 7.3)

- Malware running as the same user can read everything the user can; Studio does not defend against it.
- PostgreSQL `READ ONLY` is not a boundary against functions with side effects; the role's rights are. Diagnostics
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

The first independent security review (2026-11-13, master `e186178`, tabrion `d5821fa`) is **not accepted**. It
confirms the access layer (launch, session, CSRF, Host, Origin and fetch metadata, the security headers, D-02 on the
server, secret handling, problem documents) and found gaps the internal pre-review had missed. The report, with its
reproduction steps, stays outside the repository.

| Finding | Severity | Subject | State |
|---|---|---|---|
| F-1 | High | PostgreSQL: a statement form passed the text check and wrote despite the read-only transaction (SQ-05, SQ-07, T-06) | Fixed in the read-only hardening PR, with tests; re-review pending |
| F-2 | Medium | SQLite: the authorizer allowed a native function that must be refused (SQ-05, T-13) | Fixed in the read-only hardening PR, with tests; re-review pending |
| F-3 | Low | PostgreSQL side-effect functions outside the statement check's denylist | Fixed in the read-only hardening PR (refused); see the residual risks |
| F-4 | Low | Request bodies were read before their size was checked, also before a session exists (SQ-06) | Fixed in the request-limits PR: caps enforced while the body streams in (`security/body.py`, [access protection](access-protection.md#request-body-limits)) |
| F-5 | Low | Editor drafts in the browser storage of a port-scoped origin (TD-033) | Fixed in the F-5 drafts PR #77: drafts in `state.sqlite` (0600) behind `/api/drafts`, none in browser storage, this project's old keys migrated and removed (`test_drafts.py`, `drafts-backend.spec.ts`); review `f5-drafts-review.md` accepted with conditions, findings 1 to 7 fixed |
| F-6 | Low | Python runtime dependencies are ranges, not a hashed lock (TD-031) | **Owner decision:** hashed pins used in CI and documented, before G-R |
| F-7 | Info | The code-trust digest does not cover project modules that revision files import (T-16) | **Owner decision:** hash those modules too, or say so in the trust dialog |
| F-8 | Info | The access middleware exempted the whole control prefix from the session check | Fixed in the request-limits PR: the control token is checked in the middleware for every control path but the proof; dot segments in `/api/*` paths reach no route |
| F-9 | Info | This page said "covered" for SQ-05, T-06 and T-13 | Fixed: this page |

Re-review conditions: F-1 and F-2 fixed with tests, merged and re-reviewed; F-3 and F-4 fixed or accepted by the
owner; F-5, F-6 and F-7 recorded as owner decisions. G-S1 stays the owner's decision after the re-review, and the
human security review before a public release (STU-028, G-R) stays mandatory.

## For the human reviewer

The internal pre-review added two tests (`test_every_route.py`, `test_canary_flows.py`); the independent review then
found the gaps listed above. These points need a person's judgment:

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
9. The re-review of the independent review's fixes on merged SHAs.
