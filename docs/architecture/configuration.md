# Configuration, profiles and project trust (STU-006, STU-008)

Studio reads three kinds of files (03 section 4.3). The code is in `src/tabrion_studio/config/`: `schema.py` (TOML
schema version 1), `files.py` (reading without following links), `project.py` (project profiles and trust) and
`user.py` (the user's files), `writer.py` (writing the user's files, STU-008). The routes are in
`src/tabrion_studio/api/config.py`.

| File | Content | Trust |
|---|---|---|
| `<config dir>/settings.toml` | language, theme, density, `log_sql`, limits, `[history]` (`enabled`, `retention_days`, STU-020); telemetry does not exist (`telemetry = true` is reported and ignored) | the user's own file; not a link |
| `<config dir>/profiles.toml` | the user's profiles | the user's own file; must be 0600 and owned by the user (POSIX), not a link |
| `<project>/.tabrion/studio.toml` | the project's profiles, checked into the repository | **untrusted** until the user confirms exactly these bytes (D-01) |
| `<state dir>/trust-<key>.json` | the trust record of one project folder: the SHA-256 of the confirmed file and what the dialog showed | written by Studio, 0600 |
| `<state dir>/state.sqlite` | the query history, the favorite and the recently opened profiles (STU-020, [History, favorites and scripts](history-and-scripts.md)) | written by Studio through `tabrion.sqlite.connect`, 0600, no link |

The configuration folder is `--config-dir`, else `$TABRION_STUDIO_CONFIG_DIR`, else `<state dir>/config` when the
state directory was given explicitly, else `platformdirs.user_config_dir("tabrion-studio")`.

## Writing the user's files (STU-008)

Studio 1.0 is read-only towards databases. The only files it writes are its own `profiles.toml` and `settings.toml`
in the configuration folder (and the trust records in the state folder); **it never writes the project's
`.tabrion/studio.toml`** (a project profile gives `studio.profile_read_only`). Every save:

1. **checks the folder** like the state folder (`ensure_private_dir`: created 0700, owned by the user, not a link or
   junction, not writable by others; `studio.config_dir_unsafe` otherwise). A folder that only others may read or
   search (0755, for example) is changed to 0700 on the first save, as the state folder is (I-4 of
   `stu-008-review.md`); a folder others may write to is refused;
2. **is bound to the bytes the page read:** the request names the SHA-256 the page got from `GET /api/profiles`
   (`user_file.digest`) or `GET /api/settings` (`file.digest`), `null` for a missing file. A file changed by hand or
   by another tab meanwhile gives `studio.config_stale` (409), and the page reads it again;
3. **is checked again just before the replace:** the file is read once more and must still have the digest the save
   started from, so a hand edit meanwhile wins (`studio.config_stale`). Studio takes no lock, so an edit within the
   replace itself can still be lost (I-3 of `stu-008-review.md`, TD-011);
4. **never drops what the user wrote:** a file with any issue, an unknown key included, is not rewritten
   (`studio.config_invalid`). A file that is valid but not exactly what Studio would write (comments, another order:
   `hand_written: true`) is rewritten only after the user confirmed it (`"overwrite": true`, else
   `studio.config_hand_written`); the dialog says that comments and formatting are not kept;
5. **serializes schema version 1** with a small strict writer (`writer.py`: basic strings with every control
   character, U+2028 and U+2029 escaped, so no value can start a key or a table), parses the result back and checks
   that it gives exactly the profiles or settings it was meant to (otherwise it writes nothing and answers 422
   `studio.config_not_storable`). A value that is not UTF-8 (a lone surrogate, valid in JSON) is a form issue,
   `config.encoding` (L-2 of `stu-008-review.md`);
6. **writes atomically:** a temporary file in the same folder created with `O_CREAT | O_EXCL | O_NOFOLLOW` and mode
   0600, `fsync`, `os.replace` (which replaces a link at the target instead of following it), and an `fsync` of the
   folder on POSIX. A link planted at the temporary name is removed, never followed.

| Route | Body | Answer |
|---|---|---|
| `POST /api/profiles` | `{"profile": {...}, "digest", "overwrite"?}` | 201 `{"id": "user:<name>", "digest"}` |
| `PUT /api/profiles` | `{"id": "user:<name>", "profile": {...}, "digest", "overwrite"?}` | 200 `{"id", "digest"}`; the name cannot change (`config.rename_unsupported`) |
| `DELETE /api/profiles` | `{"id": "user:<name>", "digest", "overwrite"?}` | 200 `{"digest"}`; also removes the profile's saved password (keyring and session) |
| `PUT /api/settings` | `{"settings": {"language"?, "theme"?, "density"?, "log_sql"?, "limits"?: {"max_rows"?, "statement_timeout_ms"?, "max_result_mb"?}, "history"?: {"enabled"?, "retention_days"?}}, "digest", "overwrite"?}` | 200 `{"digest", "settings"}`; keys left out keep their value |

The id is in the body, not the path (03 section 6 sketches `/profiles/{id}`): a profile name may contain `/`, and
`/api/profiles/{id}/secret` must never be read as a profile route. A profile has the keys of the file;
`password` is a reference (`"keyring:"`, `"env:NAME"`, `"file:PATH"`) or `null`, never a password
(`config.plain_password`). A refused form gives `studio.profile_invalid` or `studio.settings_invalid` (422) with
`issues`: `[{"pointer": "profile.host", "code": "config.required"}]`. The pointers name only fixed keys (an unknown
key gives `{"pointer": "profile", "code": "config.unknown_key"}`, without the key), so nothing of the request is
echoed. When a change of the password reference or of the connection target (engine, host, port, database, user,
path) makes a saved password belong to the old profile, it is removed (C-1 of `stu-007-review.md`).

The connection manager (`frontend/src/views/connections/`) uses these routes: a list grouped by project and user
with a filter and a detail panel, the three-step "New connection" assistant of 08 section 4.2 (database; password
source: keyring, variable, file, this session or none; name, the required environment and the read-only role), a
delete confirmation whose default is Cancel, and a password dialog (`PUT /api/profiles/{id}/secret`). The settings
view saves the query limits and the SQL logging opt-in (with its warning) to `settings.toml`. Language, theme and
density are saved there too, each as soon as it is chosen (TD-021, `frontend/src/appearance.ts`): Studio listens on a
free port and a browser keeps its storage per origin, so a browser-only preference would be gone at the next start.
The page sends only the changed key with a freshly read digest (a stale digest is retried once), never changes a file
with issues or one written by hand from there (the choice then stays in this browser, and the view says so), and
applies the file's non-default values when it opens. Testing and connecting need the connection registry (STU-009) and are shown
disabled.

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
  How a reference is resolved, and the checks of a `file:` secret at read time, are in
  [Secret management](secrets.md) (STU-007).
- A database `path` or a `file:` reference that names another host, a device or a drive-relative path is the error
  `config.network_path` (review T-2): `//host/...` and `\\host\...` (also mixed separators), `\\?\...`, `\??\...` and
  `\\.\...`, `C:foo`, `file://host/...` (also `file:///...`: write `file:/...`), and the automount folders `/net/...`
  and `/Network/...`. The check is text only and the same on every platform.
- A SQLite `path` with a `?`, `#` or `%` is the error `config.url_character` (TD-013), as in tabrion 0.2's
  `tabrion.profilefile` (`profilefile.value`, its G-T2 review B-1): in a SQLAlchemy URL `?` and `#` start the query
  and the fragment, and `%` is unquoted, so `%2F%2Fhost/share/x.db` would become a network path there. Studio's own
  connection never reads the path as a URL (it opens the resolved file); this aligns the SQLite `path` with
  Tabrion's reader. The PostgreSQL `database` is not aligned (Tabrion's reader also refuses `/?#%@` there; TD-013).
  The check runs after the network-path check.
- A file nested deeper than the TOML parser can follow is `config.too_deep` (review T-3).
- `sslmode` defaults to `prefer` only for a Unix socket folder (`/...`), the literal `localhost` and a loopback IP
  address (`127.0.0.0/8`, `::1`); any DNS name, even `127.evil.example`, gets `verify-full` (review T-4).
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
   are allowed but marked "outside the project folder" in the dialog. **Containment is decided as text first**
   (`config/locations.py`, review T-2): a network or device form is refused without any file-system access, a path
   that is lexically outside is "outside" without any file-system access, and only a path lexically inside a root is
   resolved, by a walk that `lstat`s one component at a time and refuses a link whose target text is a network or
   device form before following it. `Path.resolve()` is never called on a value from the project file (on Windows it
   opens the path, which for `//host/share` is an SMB connection with the user's NTLM response).
3. **Review.** `GET /api/project` returns the state (`none`, `untrusted`, `changed`, `trusted`, `invalid`), the
   digest and the review: every connection, every host (`host:port`), every file reference (database or password
   file, inside or outside), every `env:` variable and every unknown key.
4. **Trust.** `POST /api/project/trust {"digest": ...}` trusts the file only if the digest is the one of the file on
   disk now. A file that changed after the dialog was shown gives `studio.trust_stale` (409) and the dialog shows the
   new content.
5. **Use.** `GET /api/profiles` returns the project's profiles only while the state is `trusted`. Any change of the
   file, even a comment, makes it `changed`: the profiles are unused until the user confirms again, and the dialog
   shows the diff to the trusted version. **The state is `trusted` only if the digest matches and the review equals
   the one the user confirmed** (review T-1): the review also depends on the file system, so a database folder that
   a later checkout turns into a link out of the project makes the same bytes `changed`, and the diff shows the file
   as now "outside". `ProjectConfig.database_file(name)` re-checks a trusted SQLite profile right before its file is
   opened (STU-009 opens the path it returns): the trust state again, then the containment the user confirmed on the
   file system (`studio.database_outside` if a file confirmed inside now leads outside).
6. **Fail closed.** A trust record that is missing, unreadable, readable by others, malformed (also a review of the
   wrong shape or nested too deeply), of another project folder or of other bytes means `untrusted`. A file that
   cannot be read or validated means `invalid` (cannot be trusted), never an HTTP 500; an unexpected error while
   loading the project file makes it `invalid` too, and `GET /api/profiles` still lists the user's own profiles
   (review T-3). Nothing from a project file is executed.

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
- **Where passwords go (T-4, T-5 of `stu-006-review.md`, L-1 of `stu-007-review.md`):** one line per PostgreSQL
  connection, "name: `env:X` / `file:Y` / `keyring:` (or *password asked when connecting*) → `host:port`, TLS
  `sslmode`", built by the server (`flow` and `warnings` in each review connection). It is marked when the host is not
  loopback (`remote_host`), when TLS is weaker than `verify-full` on such a host (`weak_tls`), when an `env:`
  variable looks like another service's credential (`sensitive_variable`: `AWS_`, `AZURE_`, `GOOGLE_`, `GCP_`,
  `GITHUB_`, `GH_`, `GITLAB_`, `NPM_`, `PYPI_`, `DOCKER_`, `OPENAI_`, `ANTHROPIC_`, `SSH_` or a name containing
  `SECRET`, `TOKEN`, `API_KEY`, `PRIVATE_KEY`, `ACCESS_KEY`, `CREDENTIAL`) and when a `file:` password lies outside the
  project folder (`secret_outside`). The marks are warnings, not refusals. Because the review now holds these
  fields, a project trusted before this change shows as `changed` once and must be confirmed again.
- **Notices (review I-2):** after `studio.trust_stale` the dialog says that the file changed and that it now shows
  the new content. An `invalid` file gets a notice "Project profiles not loaded" with its error issues (key paths and
  codes, escaped like the dialog's entries; never values).
- The initial focus is on "Don't load" (STU-051). "Don't load" sends nothing: the profiles stay unused until the
  page is loaded again.

## Tests

| Test | What it proves |
|---|---|
| `tests/security/test_project_trust.py` (STU-T-016, -017) | profiles unused until trusted; the review lists hosts, files, variables; a changed file needs a new confirmation and shows the diff; the digest must match the file on disk (TOCTOU); trust needs session, CSRF, Origin and JSON; `file:` outside project and config refused (4 forms); links not followed (file, folder, database path); too large, not UTF-8, syntax error, wrong version are invalid without echo; a plain password never leaves the file; damaged or foreign trust records and one readable by others mean untrusted; hostile names are data; `profiles.toml` must be private; settings; every route needs a session |
| `tests/security/test_project_trust.py` (reviews T-1..T-3) | a folder that becomes a link out of the project after trust makes the same bytes `changed` with the file in the diff; `database_file` re-checks at open time; network and device paths are refused with no `resolve` or `lstat` of the value (and, on Windows, no `nt._getfinalpathname`); deeply nested TOML is invalid and user profiles still show; trust records with a damaged or deeply nested review mean untrusted; an unexpected load error keeps the user's profiles |
| `tests/unit/test_config_locations.py` (review T-2) | the refused forms (UNC, device, drive-relative, `file://host`, `/net`), containment as text with `PureWindowsPath` and `PurePosixPath`, no file-system access for a refused or lexically outside value, the link walk (ordinary links, loops, links to network forms) |
| `tests/unit/test_config_schema.py` | schema version 1: the example of 03 section 4.3, 29 invalid cases, plain passwords refused without echo, secret references, duplicates and limits, unknown keys, the `verify-full` default, settings defaults and invalid settings, syntax errors keep only the line; `config.network_path`, `config.too_deep`, loopback detection by IP address (T-4) |
| `frontend/src/components/dialogs/StudioDialogs.test.tsx` | one Vitest test per U-1 point with hostile names (commas, newline, tab, U+202E, an overlong value, 20 entries) and the diff |
| `frontend/src/shell/ProjectTrust.test.tsx` | the page sends back the digest it showed with the CSRF header; diff; `studio.trust_stale` shows the new content and says so; an invalid file shows a notice with its escaped issues; "Don't load" sends nothing; no dialog for other states or unexpected answers |
| `tests/security/test_project_trust.py` (T-4, T-5, STU-007 L-1) | each PostgreSQL connection says where its password goes (source, `host:port`, TLS) with `remote_host`, `weak_tls`, `sensitive_variable` and `secret_outside` marks; SQLite connections have no flow; the list of sensitive variables |
| `frontend/e2e/dialogs.spec.ts`, `a11y.spec.ts` | the hostile, changed D-01 in Chromium: focus rules, escapes, caps, diff, the password lines; axe without serious or critical violations in light/dark and en/de, also for the stale D-01 with removed unknown keys and the invalid-file notice |
| `frontend/e2e/backend.spec.ts` | a real `tabrion-studio` with a project file: D-01 appears after sign-in, the project profiles are unused before "Trust" and used after it, and a reload asks no more; a profile created in the page lands in `profiles.toml` (0600) and is deleted again (STU-008) |
| `frontend/src/appearance.test.tsx` (TD-021) | the file's language, theme and density are applied at the start, defaults leave the browser's choice; a choice is saved alone with the fresh digest, CSRF and JSON, retried once when stale, kept local for a file with issues, a hand-written file or a failing server; saves run in order; the settings form keeps its input and saves with the new digest |
| `frontend/e2e/backend.spec.ts` (TD-021) | a real server writes the choices to `settings.toml` (0600) and a fresh browser context opens in German, dark and normal density |
| `tests/unit/test_config_writer.py` (STU-008) | hostile strings round-trip and never inject a key; profiles and settings read back exactly; created 0600 in a 0700 folder; a stale digest, a hand-written file (only with `overwrite`) and a file with issues (never); a link at the target or at the temporary name is not written through; an unsafe folder (link, shared, a file) is refused |
| `tests/security/test_profiles_api.py` (STU-008) | create, change and delete user profiles; a plain password is refused without echo and never written; issues name fixed keys only; names unique and never renamed; project profiles never written; stale digest and hand-written file; settings; session, CSRF, Origin and JSON (DELETE too) required; a retargeted or deleted profile forgets its saved password |
| `frontend/src/views/connections/ConnectionsView.test.tsx`, `frontend/e2e/connections.spec.ts` (STU-008) | groups and escaped names; the three steps with required fields; the password goes only to the secret route; issues at their fields; the hand-written confirmation; delete and password dialogs with Cancel as default; every write sends `Content-Type: application/json`, DELETE included; axe in light/dark and en/de for the list, every step and the dialogs |
