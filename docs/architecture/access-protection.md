# Access protection (STU-003)

Studio is a single-user tool on loopback. It has no user login. Instead, the access protection keeps three kinds of
attacker away from the API (03 section 7.2): a web page in the user's browser (AT1), another local user or process
(AT2), and a page that rebinds its own DNS name to `127.0.0.1`. The code is in `src/tabrion_studio/security/`:
`launch.py` (launch tokens and redirect files), `sessions.py` (session cookie and CSRF value) and `access.py` (the
middleware that checks every request).

## Launch: from the command to a session

```
tabrion-studio                         server                                   browser
  |-- lock, bind 127.0.0.1:P -------->|
  |                                   |-- launch token T (32 bytes, 120 s, once)
  |                                   |-- 0600 redirect file in the state directory:
  |                                   |   <meta http-equiv="refresh" content="0;url=http://127.0.0.1:P/launch#T">
  |-- webbrowser.open(file:///…/studio-<key>-launch-<random>.html) ----------->|
  |                                   |<-- GET /launch (the fragment #T stays in the browser) --|
  |                                   |<-- POST /api/session {"token": T} (Origin, JSON) -------|
  |                                   |-- Set-Cookie: tabrion_studio_session_P=<id>; HttpOnly;
  |                                   |   SameSite=Strict; Path=/   +   {"csrf": C}  ----------->|
  |                                   |-- redirect file deleted                                 |
  |                                   |<-- GET /api/heartbeat, Cookie + X-Tabrion-CSRF: C ------|
```

- **No token in a process argv (review F-02).** The arguments of `xdg-open`, `open` or the browser are visible to
  every local user (`ps`, `/proc/<pid>/cmdline`). So the browser gets the `file://` path of the redirect file, which
  holds no secret. The file is 0600 in the 0700 state directory, and it is deleted when its token is redeemed, when
  the token expires (120 s, checked every 5 s), when the server stops and, if a crash left it behind, when the next
  server for the folder starts.
- **Second start.** `tabrion-studio` again (or from another terminal) asks the running server over the control
  channel for a new launch. The server writes a new redirect file. The CLI opens it only if it lies in the state
  directory, has this instance's file name, is a private file of this user and contains exactly the redirect page for
  the returned launch URL. The launch URL itself must be `http://127.0.0.1:<port>/launch#<43 characters>`.
- **`--print-url`** prints a one-time link in the terminal, as an explicit opt-in (for a browser started by hand).
  Without it, no output contains a token. `--no-browser` alone issues no token at all.
- **The token is used once** (`access.launch_used`) and expires (`access.launch_expired`). The page removes it from
  the address bar and the session history (`history.replaceState`) before it sends it.
- Only `http://127.0.0.1:<port>/` URLs are printed, opened or written into redirect files, never `localhost` ones
  (review F-08).

**Known limit:** a browser in a sandbox that cannot read the state directory (for example the Firefox snap on Ubuntu,
which cannot read hidden folders such as `~/.local/state`) cannot open the redirect file. Use `--print-url`, or set
the state directory to a folder the browser can read (`--state-dir`). See docs/TODO.md, TD-002.

## Session and CSRF value (SQ-03, review F-09)

A session has two random values (256 bits each). The **session ID** is in the cookie `tabrion_studio_session_<port>`
(`HttpOnly; SameSite=Strict; Path=/`, no `Secure` on http loopback, no `Domain`, no expiry: it ends with the browser
session). The port is part of the name because cookies are not port-scoped, so two Studio instances would otherwise
overwrite each other's cookie.

The **CSRF value** comes once in the answer to `POST /api/session`. The page keeps it in `sessionStorage`, which is
scoped to the origin including the port, and sends it as `X-Tabrion-CSRF` with every API request. Every request with a
session needs both values. Cookies are not port-scoped: a program on another local port that the browser visits
receives the Studio cookie, but it never sees the CSRF value, so the cookie alone opens nothing. A tab without the
value (a new tab, or a tab opened by typing the address) shows "Not signed in"; running `tabrion-studio` again opens a
signed-in tab. The server keeps only SHA-256 digests of session IDs (at most 32 sessions, the oldest is dropped), and
a restart ends every session.

## Checks on every request

`AccessMiddleware` runs before any route. In this order:

| # | Check | Refusal | Threat, finding |
|---|---|---|---|
| 1 | Exactly one `Host` header, equal to `127.0.0.1:<port>`, on every route, including `/api/control/*`, `/api/control/proof`, `/api/heartbeat`, `/api/health` and the static files. `localhost` and `[::1]` are not accepted | 403 `access.host_not_allowed` | T-01 (DNS rebinding), F-05, F-08 |
| 2 | No `Forwarded`, `X-Forwarded-*` or `X-Real-IP` | 403 `access.forwarded_not_allowed` | T-01 |
| 3 | An `Origin` header, if present, is `http://127.0.0.1:<port>`; unsafe methods outside the control endpoints must carry it | 403 `access.origin_not_allowed`, `access.origin_required` | T-02 |
| 4 | `/api/control/*` refuses any `Origin` and any `Sec-Fetch-*` header: the CLI never sends them, every current browser does | 403 `access.browser_not_allowed` | F-05 |
| 5 | Other `/api/*` requests with `Sec-Fetch-Site: cross-site` or `same-site` are refused (a page on another port of 127.0.0.1 is same-site) | 403 `access.cross_site` | T-02 |
| 6 | Unsafe `/api/*` requests need `Content-Type: application/json` | 415 `access.json_only` | T-02 |
| 7 | Every `/api/*` route except `/api/health`, `/api/session` and the control endpoints (bearer token) needs the session cookie and its CSRF header | 401 `access.session_required`, 403 `access.csrf_invalid` | T-03, SQ-03, F-09 |

A request that passes check 7 counts as activity for the idle timeout (review F-06, R-5). Navigations to `/` and
`/launch` from other sites are allowed: the pages hold no data, and the redirect file is such a navigation.

Every answer, including refusals and errors, gets these headers:

| Header | Value | Threat |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` | T-07, SQ-08 |
| `Cache-Control` | `no-store` (replaces the static files' own caching headers) | T-15 |
| `X-Content-Type-Options` | `nosniff` | T-07 |
| `Referrer-Policy` | `no-referrer` | T-03 |
| `X-Frame-Options` | `DENY` | T-07 |
| `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy` | `same-origin` | T-07 |

The frontend has no inline scripts or styles (the CSP has no `unsafe-inline` or `unsafe-eval`), and an ESLint rule
forbids `dangerouslySetInnerHTML`.

**Browsers.** The checks do not depend on fetch metadata: a browser that does not send `Sec-Fetch-*` (Safari before
16.4) is still held off by the Host allowlist, the Origin check, `SameSite=Strict` and the CSRF value, and its
heartbeat counts because it carries the session. Supported are current Chrome, Edge, Firefox and Safari.

## Development server

`npm run dev` (Vite on `127.0.0.1:5173`) proxies `/api` to a backend on port 8765. The backend has no development
exception: the proxy presents the backend's own `Host` and `Origin` (`frontend/vite.config.ts`). Start the backend
with `tabrion-studio --port 8765 --no-browser --print-url` and open the printed link with the port changed to 5173.

## Tests

| Test ID | What | Tests |
|---|---|---|
| STU-T-001 | Host allowlist on every route; DNS rebinding; missing or duplicate `Host` | `tests/security/test_access.py`: `test_host_allowlist_on_every_route`, `test_dns_rebinding_page_cannot_read_the_api`, `test_missing_or_duplicate_host_is_refused`, `test_websockets_are_refused` |
| STU-T-002 | Forwarded requests; only `127.0.0.1` URLs printed or opened (F-08) | `test_forwarded_requests_are_refused`; `tests/security/test_launch.py::test_no_localhost_url_is_printed_or_opened`; `tests/process/test_process_model.py::test_ipv6_loopback_port_is_held` |
| STU-T-003 | Origin, fetch metadata, control endpoint refuses browser headers (F-05) | `test_foreign_origin_is_refused`, `test_unsafe_requests_need_the_own_origin`, `test_cross_site_api_requests_are_refused`, `test_control_rejects_browser_headers` |
| STU-T-004 | JSON only; CSRF header from origin-scoped storage (F-09) | `test_api_accepts_json_only`, `test_cookie_alone_is_useless_without_csrf_header`, `test_csrf_value_is_bound_to_its_session`, `test_csrf_value_never_travels_in_a_cookie`, `test_a_tossed_cookie_does_not_hide_the_real_session`; frontend `session.test.ts` |
| STU-T-005 | Session only through a one-time launch token; without cookie 401; cookie flags | `test_api_needs_a_session`, `test_launch_token_gives_a_session_cookie`, `test_launch_token_works_once`, `test_launch_token_expires`, `test_unknown_launch_token_is_refused` |
| STU-T-005/-006 | Token in the fragment, never in a process argv (F-02); redirect file 0600, deleted after use, timeout and stop; `--print-url` opt-in | `tests/security/test_launch.py`: `test_browser_argv_contains_no_token` (a real process and a fake `$BROWSER` that records its argv, POSIX), `test_browser_is_opened_with_a_redirect_file`, `test_second_start_opens_a_new_redirect_file`, `test_print_url_is_the_explicit_opt_in`, `test_launch_tokens_and_redirect_files`, `test_validate_redirect_file`, `test_second_start_refuses_a_foreign_redirect_file` |
| STU-T-014 | CSP on every answer; no inline scripts; no `dangerouslySetInnerHTML` | `test_csp_is_strict`, `test_security_headers_on_every_answer`, `test_frontend_has_no_inline_script`, `test_eslint_forbids_dangerously_set_inner_html` |
| STU-T-025 | No option to bind another address; loopback only | `tests/unit/test_cli.py::test_bad_arguments`, `tests/process/test_process_model.py::test_binds_loopback_only` |
| STU-T-026 | `Cache-Control: no-store` on every API answer and refusal | `test_no_store_on_every_api_answer`, `test_security_headers_on_every_answer` |
| F-06, R-5 | Only a valid session or control token counts as activity | `tests/unit/test_app.py`: `test_requests_that_do_not_count_as_activity`, `test_heartbeat_with_a_session_counts_as_activity`; `tests/process/test_process_model.py::test_activity_postpones_the_idle_exit` |
| R-4 | Proof and token on one connection | `tests/security/test_impostor.py`: `test_proof_and_token_travel_on_one_connection`, `test_server_that_closes_after_the_proof_never_gets_the_token` |
