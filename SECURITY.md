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
- per-instance state files live in the per-user state directory (directory 0700, files 0600, symlinks refused);
  the control endpoint used by a second start and by `--stop` needs a random token from that file;
- planned before the first release (STU-003 and later): one-time launch token in the URL fragment, `HttpOnly;
  SameSite=Strict` session cookie, Host and Origin allowlists, strict CSP, `no-store`, secrets only in the OS
  keyring (or `env:`/`file:` references), read-only enforced by the database, production profiles without writes.

In scope: anything that lets another website, another local user or a malicious database or project file read
credentials or data through Studio, change a database without the user's intent, or run code.

Out of scope (residual risks): malware running as the same user (it can read the keyring and the process memory);
PostgreSQL functions with side effects under `READ ONLY` (only role privileges are a hard boundary, which the
diagnostics will recommend).
