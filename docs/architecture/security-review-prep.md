# Security review preparation (STU-016, G-S1)

This page prepares the security gate **G-S1** of the Studio MVP. It is a readiness checklist and the result of an
internal pre-review pass, **not** the gate: G-S1 is decided by the owner after an independent security review and its
re-review, and a human security review before a public release stays mandatory (STU-028, gate G-R). Nothing here
replaces either review.

State: master `e9df576` plus this page's pull request. Sensitive specifics of the pre-review are kept outside the
repository, with the planning documents.

## What G-S1 asks

From the security and quality plan (doc 07, gate table) and the backlog entry of STU-016 (doc 09):

| G-S1 criterion | State | Evidence |
|---|---|---|
| SQ-01..SQ-08 green, each with its STU-T tests | Ready for review | [SQ requirements](#sq-01sq-08) below |
| Every threat-model measure (doc 03 section 7.3, T-01..T-16) proven by a test | Ready for review, except the write-mode rows (G-S2) | [Threat model](#threat-model-t-01t-16) below |
| Review report "accepted": a first review, the fixes, a re-review on merged SHAs | **Open** (the independent review has not started) | - |
| `npm audit --omit=dev` without High | Met (0 findings at `e9df576`) | CI job `dependency audit` |
| axe: 0 serious or critical | Met for the existing views (`e2e`, every view in light and dark, en and de) | `frontend/e2e/*.spec.ts`, `docs/architecture/accessibility.md`; the rest of STU-053 is TD-022 |

## SQ-01..SQ-08

| SQ | Requirement | Where it is enforced | Tests |
|---|---|---|---|
| SQ-01 | Listen on loopback only | `server.py` binds `127.0.0.1` (and holds `[::1]`); the CLI has no host option | STU-T-025: `tests/unit/test_cli.py::test_bad_arguments`, `tests/security/test_launch.py` |
| SQ-02 | Host and Origin allowlist (DNS rebinding) | `security/access.py`: `127.0.0.1:<port>` only, the own Origin and same-origin fetch metadata on every API request | STU-T-001..003: `tests/security/test_access.py`; every API route: `tests/security/test_every_route.py` |
| SQ-03 | Launch token, then an HttpOnly SameSite=Strict cookie and a CSRF header | `security/launch.py`, `security/sessions.py` | STU-T-004..006: `test_access.py`, `test_launch.py`; every API route: `test_every_route.py` |
| SQ-04 | Secrets only in the backend | `config/secrets.py` (keyring without plain-text fallback, `env:`, `file:`), `tabrion.safe_url()`, log redaction in `logs.py` | STU-T-008/-009: `test_canary.py`, `test_secrets_api.py`, `test_log_process.py`, `test_diagnosis_report.py`; through the routes of the editor, viewer, export and diagnosis: `test_canary_flows.py` |
| SQ-05 | Read-only enforced by the database | `services/connections.py`, `services/readonly.py` (SQLite `mode=ro`, `query_only`, authorizer; PostgreSQL read-only session and transactions), text check as a second layer | STU-T-010..013, -024: `tests/security/test_read_only.py` (PostgreSQL part in the `backend integration (PostgreSQL 17)` CI job) |
| SQ-06 | Limits and cancel | `services/queries.py`, `viewer.py`, `exports.py`; limits in the configuration | STU-T-018..020: `test_queries.py`, `test_viewer.py`, `test_exports.py`, `frontend/e2e/editor-backend.spec.ts` |
| SQ-07 | PROD marking; no writes before G-S2 | Environment badge, frame and title (STU-051); D-02 on the server (`confirmed_environment`, TD-038); no write route exists (STU-026 is locked) | `test_read_only.py::test_td_038_*`, `test_connections_api.py`, frontend D-02 tests; `test_every_route.py` lists every route |
| SQ-08 | Strict CSP, no foreign hosts | `CONTENT_SECURITY_POLICY` in `security/access.py`; no CDN, fonts or telemetry | STU-T-014: `test_access.py::test_csp_is_strict`; `frontend/e2e/network.spec.ts` (0 foreign hosts) |

## Threat model T-01..T-16

The measures are those of doc 03 section 7.3; T-16 is in `docs/architecture/migrations.md`.

| Threat | Measure | Tests | State |
|---|---|---|---|
| T-01 DNS rebinding | Host allowlist on every route | STU-T-001, -002 (`test_access.py`, `test_launch.py`) | Covered |
| T-02 CSRF from another site | Origin, fetch metadata, JSON only, CSRF header | STU-T-003, -004; every API route in `test_every_route.py` | Covered |
| T-03 Token theft | One-time launch token in the fragment, redirect file 0600, session cookie | STU-T-005, -006 | Covered; TD-005 (spent URL in history) is an accepted risk |
| T-04 Files read by other accounts | Files 0600, folders 0700, symlinks refused | No STU-T-007 label; in substance `tests/unit/test_paths.py`, `test_logs.py`, `test_secrets.py`, `test_config_writer.py`, `tests/security/test_profiles_api.py` | Covered on POSIX; Windows DACL inside the profile is TD-003. No state database or history yet (1.0); editor drafts are in browser storage (TD-033) |
| T-05 Secret leaks | Redaction, canaries, safe URLs, Problem JSON without server text | STU-T-008, -009; `test_canary_flows.py` | Covered |
| T-06 Accidental writes | Read-only in the database, prod marking | STU-T-010..013 (`test_read_only.py`) | Covered for the MVP; the write mode is G-S2 |
| T-07 XSS through database text | React text only, ESLint rule against `dangerouslySetInnerHTML`, CSP | STU-T-014; no STU-T-015 label, in substance `test_access.py::test_eslint_forbids_dangerously_set_inner_html` and hostile-value tests of the views | Covered |
| T-08 Hostile project | Project trust (D-01), bound to digests | STU-T-016, -017 (`test_project_trust.py`) | Covered |
| T-09 Resource exhaustion | Row, byte and cell caps, timeouts, cancel, parallel limits | STU-T-018..020 | Covered |
| T-10 TLS downgrade | `STU-DX-002` for PROD without verify-full | STU-T-021 (`test_diagnostics.py`) | Covered as a finding; Studio does not refuse such a profile |
| T-11 Supply chain | Locked dependencies, license policy, SBOM, audits | No STU-T-022 label; CI jobs `license and supply chain`, `dependency audit` | Covered; TD-030 (dev-only advisory), TD-031 (hashed pins in CI) |
| T-12 Change log | Writes logged | STU-T-023 | Not applicable before G-S2 (no write mode) |
| T-13 SQLite extensions and `ATTACH` | Authorizer as an allowlist | STU-T-024 (`test_read_only.py`) | Covered |
| T-14 Network exposure | No `--host` | STU-T-025 | Covered |
| T-15 Cached answers | `Cache-Control: no-store` | STU-T-026; every refusal in `test_every_route.py` | Covered |
| T-16 Trusted project code (Alembic) | Code trust bound to file digests, isolated worker | `test_migrations.py` | Covered |

## The pages and their API

Every API route except `/api/health`, `/api/session` and the CLI's control endpoints needs the session, the CSRF
header, refuses a foreign Origin and cross-site or same-site fetch metadata, and, for unsafe methods, needs the own
Origin and a JSON body. `test_every_route.py` reads the routes from the application built with every service, so a
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
| TD-033 | P2 | Editor drafts (SQL text) in browser storage, not in the 0600 state folder |
| TD-038 | P2 | Remaining limits of the D-02 gate and of the connect error states |

## Residual risks (doc 03 section 7.3)

- Malware running as the same user can read everything the user can; Studio does not defend against it.
- PostgreSQL `READ ONLY` is not a boundary against functions with side effects; the role's rights are. Diagnostics
  report a read-only role that can write (`STU-DX-003`).
- The warning before a destructive statement is UX, not protection.

## For the human reviewer

The internal pre-review found no gap in the product code. Its additions are two tests (`test_every_route.py`,
`test_canary_flows.py`). These points need a person's judgment:

1. TD-005: whether the spent launch URL in the browser history stays an accepted risk.
2. TD-002 and TD-007: the redirect-file launch path and the checks in a real browser.
3. TD-003 and TD-008: the file-permission model on Windows, which CI does not yet check.
4. The PostgreSQL read-only model: Studio relies on the role's rights for functions with side effects.
5. TD-033: SQL text in browser storage, outside the private state folder.
6. The keyring backends Studio accepts and refuses, per operating system.
7. The CI workflow-change gate (TD-004, TD-006) and the supply chain (TD-030, TD-031).
8. Code trust for migrations (T-16): the worker's isolation and what the digest binds.
9. The open independent review itself: first review, fixes and a re-review on merged SHAs.
