# Configuration, profiles and project trust (STU-006)

Studio reads three kinds of files (03 section 4.3). The code is in `src/tabrion_studio/config/`: `schema.py` (TOML
schema version 1), `files.py` (reading without following links), `project.py` (project profiles and trust) and
`user.py` (the user's files). The routes are in `src/tabrion_studio/api/config.py`.

| File | Content | Trust |
|---|---|---|
| `<config dir>/settings.toml` | language, theme, `log_sql`, limits; telemetry does not exist (`telemetry = true` is reported and ignored) | the user's own file; not a link |
| `<config dir>/profiles.toml` | the user's profiles | the user's own file; must be 0600 and owned by the user (POSIX), not a link |
| `<project>/.tabrion/studio.toml` | the project's profiles, checked into the repository | **untrusted** until the user confirms exactly these bytes (D-01) |
| `<state dir>/trust-<key>.json` | the trust record of one project folder: the SHA-256 of the confirmed file and what the dialog showed | written by Studio, 0600 |

The configuration folder is `--config-dir`, else `$TABRION_STUDIO_CONFIG_DIR`, else `<state dir>/config` when the
state directory was given explicitly, else `platformdirs.user_config_dir("tabrion-studio")`. Studio 1.0 only reads
these files; writing profiles and settings arrives with the connection manager and the settings view (STU-008).

## Schema version 1

```toml
version = 1
[[profile]]
name = "parts-dev"
engine = "sqlite"            # sqlite | postgresql
path = "data/parts.db"       # relative to the project folder
environment = "dev"          # dev | test | prod (required)

[[profile]]
name = "measurements-central"
engine = "postgresql"
host = "db.example.internal"
port = 5432
database = "measurements"
user = "studio_ro"
password = "keyring:"        # keyring:, env:NAME, file:PATH; never a plain password
sslmode = "verify-full"      # default for a remote host (T-10); "prefer" for loopback
environment = "prod"
read_only_role = true
statement_timeout_ms = 15000
```

- `version = 1` is required; any other version is refused.
- Required: `name` (1 to 200 characters, no control characters, unique), `engine`, `environment`; `path` for
  SQLite; `host` and `database` for PostgreSQL. Keys of the other engine are errors.
- `password` must be a reference: `keyring:`, `env:NAME` or `file:PATH`. Anything else is the error
  `config.plain_password`, and the value is never repeated, not in the API, not in a log.
- Unknown keys are warnings (`config.unknown_key`); D-01 lists them.
- An issue is `{pointer, code, severity}`: a key path such as `profile[2].port` and a code such as `config.range`.
  It never contains a value. A TOML syntax error keeps only its line number, not the parser's message.
- The format is Studio's own until Tabrion's profile format v1 (TAB-CR-1, change request 14) exists; it is meant to
  stay compatible with it.

## Project trust (threat T-08, AT4)

A project file comes from a repository that may be hostile. It could point Studio at an attacker's host, read a
password from a variable such as `AWS_SECRET_ACCESS_KEY` and send it there, or name `file:~/.ssh/id_rsa`. So:

1. **Reading.** `.tabrion` and `studio.toml` must be plain (no symlink, junction or reparse point), the file must be
   regular, at most 256 KB, and the file opened must be the file checked (device and inode). It must be UTF-8 and
   valid TOML of schema version 1.
2. **`file:` references** are allowed only inside the project folder or the user's configuration folder, after
   resolving links; anything else makes the file invalid (`config.file_outside`). Database paths outside the project
   are allowed but marked "outside the project folder" in the dialog.
3. **Review.** `GET /api/project` returns the state (`none`, `untrusted`, `changed`, `trusted`, `invalid`), the
   digest and the review: every connection, every host (`host:port`), every file reference (database or password
   file, inside or outside), every `env:` variable and every unknown key.
4. **Trust.** `POST /api/project/trust {"digest": ...}` trusts the file only if the digest is the one of the file on
   disk now. A file that changed after the dialog was shown gives `studio.trust_stale` (409) and the dialog shows the
   new content.
5. **Use.** `GET /api/profiles` returns the project's profiles only while the state is `trusted`. Any change of the
   file, even a comment, makes it `changed`: the profiles are unused until the user confirms again, and the dialog
   shows the diff to the trusted version.
6. **Fail closed.** A trust record that is missing, unreadable, readable by others, malformed, of another project
   folder or of other bytes means `untrusted`. A file that cannot be read or validated means `invalid` (cannot be
   trusted). Nothing from a project file is executed.

### The trust dialog D-01 (binding requirement U-1)

The dialog shows only data from the untrusted file, so it follows U-1 of `stu-005-051-review.md`:

- **U-1a:** names, hosts, files and variables are lists; each entry is its own list item with its own `<code>`.
- **U-1b:** C0 and C1 control characters (newline and tab included), U+2028/U+2029 and the bidi controls (U+061C,
  U+200E, U+200F, U+202A..U+202E, U+2066..U+2069) are shown as `\uXXXX`, and a backslash as `\\`, so an escape
  cannot be faked (`frontend/src/lib/visible.ts`).
- **U-1c:** an entry longer than 64 characters is cut with a marker and a "Show full value" button; a list shows 8
  entries and "and N more"; the headings show the real totals.
- **U-1d:** a changed file shows the added, removed and changed connections and the added and removed hosts, files,
  variables and unknown keys. If none of them changed, the dialog says that other content of the file did.
- The initial focus is on "Don't load" (STU-051). "Don't load" sends nothing: the profiles stay unused until the
  page is loaded again.

## Tests

| Test | What it proves |
|---|---|
| `tests/security/test_project_trust.py` (STU-T-016, -017) | profiles unused until trusted; the review lists hosts, files, variables; a changed file needs a new confirmation and shows the diff; the digest must match the file on disk (TOCTOU); trust needs session, CSRF, Origin and JSON; `file:` outside project and config refused (4 forms); links not followed (file, folder, database path); too large, not UTF-8, syntax error, wrong version are invalid without echo; a plain password never leaves the file; damaged or foreign trust records and one readable by others mean untrusted; hostile names are data; `profiles.toml` must be private; settings; every route needs a session |
| `tests/unit/test_config_schema.py` | schema version 1: the example of 03 section 4.3, 29 invalid cases, plain passwords refused without echo, secret references, duplicates and limits, unknown keys, the `verify-full` default, settings defaults and invalid settings, syntax errors keep only the line |
| `frontend/src/components/dialogs/StudioDialogs.test.tsx` | one Vitest test per U-1 point with hostile names (commas, newline, tab, U+202E, an overlong value, 20 entries) and the diff |
| `frontend/src/shell/ProjectTrust.test.tsx` | the page sends back the digest it showed with the CSRF header; diff; `studio.trust_stale` shows the new content; "Don't load" sends nothing; no dialog for other states or unexpected answers |
| `frontend/e2e/dialogs.spec.ts`, `a11y.spec.ts` | the hostile, changed D-01 in Chromium: focus rules, escapes, caps, diff; axe without serious or critical violations in light/dark and en/de |
| `frontend/e2e/backend.spec.ts` | a real `tabrion-studio` with a project file: D-01 appears after sign-in, the project profiles are unused before "Trust" and used after it, and a reload asks no more |
