# STU-ADR-0002: Local security model

**Status:** proposed. E-QA2 decided: a human security review before any public Studio release (part of G-R).

## Decision

| Area | Rule |
|---|---|
| Network | Bind `127.0.0.1` only. Host allowlist `127.0.0.1:<port>` on every route (`localhost` is not accepted, review F-08); forwarded headers are rejected |
| Session | One-time launch token (32 bytes) in the URL fragment, handed to the browser through a 0600 redirect file and never through a process argv (review F-02), exchanged through `POST /api/session` for an `HttpOnly; SameSite=Strict; Path=/` cookie; a second use gives `access.launch_used`; control file 0600 |
| CSRF | strict `Origin` check on every unsafe method and on SSE; JSON only; a CSRF header on every request with a session, whose value lives in origin-scoped storage and never in a cookie (cookies are not port-scoped, review F-09); the control endpoint refuses browser requests (F-05) |
| Browser | CSP `default-src 'self'`, no `unsafe-eval`/`unsafe-inline`; React text output, no `dangerouslySetInnerHTML`, no HTML/Markdown rendering of database content; `Cache-Control: no-store` on `/api/*` |
| Secrets | `keyring:` then `env:` then `file:` (0600, no symlink) then session input; never a plaintext fallback; never in the frontend, logs or errors; URLs only through `tabrion.safe_url` |
| Read-only | enforced by the database: SQLite `mode=ro` + `query_only` + authorizer against `ATTACH`; PostgreSQL `default_transaction_read_only=on` + a read-only transaction per query; the parser pre-check is UX only |
| Production profiles | clearly marked, shorter timeouts, writing technically impossible in 1.0 |
| Limits | row, byte and cell caps; timeouts; cancel under 1 s; server-side cursors; 50 MB result ring |
| Logging | no parameters, result rows or passwords; SQL text only on opt-in at DEBUG, truncated |
| Project files | trust dialog with diff; `file:` references only inside the project or the user configuration |
| New remote profiles | `tls="verify-full"` by default (stricter than the core's `auto`, on purpose) |

## State in this repository

In place: the loopback bind, the 0600 state files and the token-protected control endpoint of the process model
(STU-002), and the network, session, CSRF and browser rows (STU-003, `docs/architecture/access-protection.md`).
Everything else in the table comes with later work packages; G-S1 requires an independent security review agent.

## Tests

STU-T-001..006, -008/-009, -010..-013, -014, -016/-017, -018..-020, -021, -023, -024, -025, -026; QA-9.
