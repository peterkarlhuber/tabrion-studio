# STU-ADR-0002: Local security model

**Status:** proposed. E-QA2 decided: a human security review before any public Studio release (part of G-R).

## Decision

| Area | Rule |
|---|---|
| Network | Bind `127.0.0.1` only. Host allowlist `127.0.0.1:<port>`/`localhost:<port>`; forwarded headers are rejected |
| Session | One-time launch token (32 bytes) in the URL fragment, exchanged through `POST /api/session` for an `HttpOnly; SameSite=Strict; Path=/` cookie; a second use gives `access.launch_used`; control file 0600 |
| CSRF | strict `Origin` check on every unsafe method and on SSE; JSON only |
| Browser | CSP `default-src 'self'`, no `unsafe-eval`/`unsafe-inline`; React text output, no `dangerouslySetInnerHTML`, no HTML/Markdown rendering of database content; `Cache-Control: no-store` on `/api/*` |
| Secrets | `keyring:` then `env:` then `file:` (0600, no symlink) then session input; never a plaintext fallback; never in the frontend, logs or errors; URLs only through `tabrion.safe_url` |
| Read-only | enforced by the database: SQLite `mode=ro` + `query_only` + authorizer against `ATTACH`; PostgreSQL `default_transaction_read_only=on` + a read-only transaction per query; the parser pre-check is UX only |
| Production profiles | clearly marked, shorter timeouts, writing technically impossible in 1.0 |
| Limits | row, byte and cell caps; timeouts; cancel under 1 s; server-side cursors; 50 MB result ring |
| Logging | no parameters, result rows or passwords; SQL text only on opt-in at DEBUG, truncated |
| Project files | trust dialog with diff; `file:` references only inside the project or the user configuration |
| New remote profiles | `tls="verify-full"` by default (stricter than the core's `auto`, on purpose) |

## State in this repository

The skeleton implements the loopback bind, no forwarded headers, the 0600 state files and the token-protected
control endpoint of the process model. Everything else in the table is STU-003 and later; G-S1 requires an
independent security review agent.

## Tests

STU-T-001..006, -008/-009, -010..-013, -014, -016/-017, -018..-020, -021, -023, -024, -025, -026; QA-9.
