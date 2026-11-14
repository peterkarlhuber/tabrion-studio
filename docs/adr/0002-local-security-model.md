# STU-ADR-0002: Local security model

**Status:** proposed. E-QA2 decided: a human security review before any public Studio release (part of G-R).

## Decision

| Area | Rule |
|---|---|
| Network | Bind loopback only: a fresh random `127.a.b.c` for every start, `127.0.0.1` where only that exists (finding A-01). Host allowlist of exactly that `<address>:<port>` on every route (`localhost` is not accepted, review F-08); forwarded headers are rejected |
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

## Residual risk: origin reuse on loopback (finding A-01)

A browser keeps storage and service workers per origin. Before A-01 every instance used `http://127.0.0.1:<port>`,
so a service worker that another local user's program registered on that origin while Studio was not running would
answer the launch page of a later Studio on the same port and get a full session; a fixed `--port` made that
predictable. Since A-01 each start binds a fresh random `127.a.b.c` (Linux, Windows), so no earlier origin is reused.
What remains, accepted for a single-user loopback tool and documented in `docs/architecture/access-protection.md`
("Origin per start") and the user guide:

- Where only `127.0.0.1` is configured (macOS by default), Studio falls back to it: with a free port the attacker must
  hold the right port in advance; with a fixed `--port` the origin is predictable. The guide says to leave `--port`
  out on a computer shared with other users, and Studio says so at start in that case.
- Another local user can see the running instance's address and port and, after it stops, serve a page there that a
  still-open Studio tab shows when it is reloaded (no session; that origin is not used again). Close Studio tabs after
  stopping Studio.
- `Clear-Site-Data` is not used: it would sign out every other open tab of the instance (it clears `sessionStorage`)
  and cannot reach a page load that a service worker answers.

## State in this repository

In place: the loopback bind, the 0600 state files and the token-protected control endpoint of the process model
(STU-002), and the network, session, CSRF and browser rows (STU-003, `docs/architecture/access-protection.md`).
Everything else in the table comes with later work packages; G-S1 requires an independent security review agent.

## Tests

STU-T-001..006, -008/-009, -010..-013, -014, -016/-017, -018..-020, -021, -023, -024, -025, -026; QA-9.
