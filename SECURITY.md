# Security policy

## Supported versions

Tabrion Studio is pre-alpha and has no release. Security fixes are made on the default branch (`master`).

| Version | Supported |
| --- | --- |
| `master` (unreleased 0.1.0) | yes |

## Reporting a vulnerability

Please do not report vulnerabilities in public issues, pull requests or discussions.

1. Use **Report a vulnerability** in the repository's **Security** tab (GitHub private vulnerability reporting).
   Only the maintainers can see the report.
2. If that option is not available, open an issue titled "Security contact request" **without any details**, so
   that a private channel can be arranged.

Include the affected version or commit, the impact, and steps to reproduce. Never include real passwords,
connection strings or data; use placeholders such as `SECRET-4711`.

The target is a first response within 7 days. Fixes are coordinated with the reporter before details are
published, through a GitHub security advisory.

## Security model (summary)

Studio is a local, single-user tool (STU-ADR-0002, `docs/adr/0002-local-security-model.md`):

- the server binds `127.0.0.1` only, on a free port; there is no option to bind another address, and forwarded
  headers are not trusted;
- per-instance state files live in the per-user state directory (directory 0700, files 0600, symlinks and
  junctions refused). A state directory that other users own or may write to, or on Windows one outside the user
  profile without a private ACL, is refused;
- the control endpoint used by a second start and by `--stop` needs a random token from that file. The client
  sends the token only after the server has proved that it knows it (HMAC over a client nonce), on the same
  connection, and opens only launch URLs and redirect files of that instance; `[::1]` on the same port is held so
  that no other program answers for `localhost`; browsers cannot drive the control endpoint (`Origin` and
  `Sec-Fetch-*` are refused);
- access protection (STU-003, `docs/architecture/access-protection.md`): a one-time launch token in the URL fragment,
  handed to the browser through a 0600 redirect file and never through a process argv; an `HttpOnly;
  SameSite=Strict` session cookie plus a CSRF header whose value lives only in the page's `sessionStorage`; a Host
  allowlist (`127.0.0.1:<port>` only) and an Origin check on every route; JSON-only API requests; a strict CSP and
  `Cache-Control: no-store` on every answer;
- only requests with a valid session and authenticated control requests postpone the idle exit;
- errors and logs (STU-004, `docs/architecture/errors-and-logging.md`): every error answer is a problem document
  with a code and fixed texts, never an exception message, a path, a stack trace or request data (unexpected errors
  give `studio.internal_error` with a log ID); log records are redacted before they are written (the process's
  tokens, URL passwords, credential key values, bearer tokens and cookies removed; exceptions logged with frames
  only, without messages), the request log names route templates without query strings, and log files are 0600 in a
  0700 folder, rotated at 5 x 5 MB;
- planned before the first release: secrets only in the OS keyring (or `env:`/`file:` references), read-only
  enforced by the database, production profiles without writes.

In scope: anything that lets another website, another local user or a malicious database or project file read
credentials or data through Studio, change a database without the user's intent, or run code.

Out of scope (residual risks): malware running as the same user (it can read the keyring and the process memory);
PostgreSQL functions with side effects under `READ ONLY` (only role privileges are a hard boundary, which the
diagnostics will recommend).
