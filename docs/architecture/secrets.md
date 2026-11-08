# Secret management (STU-007)

A profile never holds a password, only a reference to one (03 section 4.4). The backend resolves the reference when
it connects (STU-009). The frontend sees only where a secret comes from and, where that is known without reading it,
whether it is there. The code is in `src/tabrion_studio/config/secrets.py`; the routes are in
`src/tabrion_studio/api/config.py`.

| Reference | Where the value comes from | Notes |
|---|---|---|
| `keyring:` | the OS keyring: macOS Keychain, Windows Credential Manager, Secret Service or KWallet; service `tabrion-studio` | entry `user:<name>` for the user's profiles, `project:<project key>:<name>` for a project's (unique per project folder) |
| `env:NAME` | the variable of the `tabrion-studio` process | an empty variable counts as missing |
| `file:PATH` | a file, read with the checks below | relative to the project folder (project profiles) or the configuration folder (the user's `profiles.toml`) |
| none, or `keyring:` without an entry | a secret entered in the page for this session only | kept in memory, gone when Studio stops |

Every value that is resolved or entered is registered for log redaction (`tabrion_studio.logs.register_secret`). Every
refusal is a problem code without the path's content, the variable's value or the keyring's message.

## No plain-text fallback

`select_backend()` picks one keyring backend: the usable backend with the highest priority, never the chainer, which
could reach a backend Studio refuses. **Refused:** `keyring.backends.fail`, `keyring.backends.null` and the whole
`keyrings.alt` package, whose backends keep secrets in a file. Without a usable backend (for example Linux without a
Secret Service), Studio logs one warning (`studio.keyring_unavailable`), `GET /api/secrets` answers
`{"keyring": {"available": false}}`, storing in the keyring is refused with `studio.keyring_unavailable`, and a
`keyring:` profile can still use a secret entered for this session. `env:` and `file:` references work as usual
(R-S4). Nothing is ever written to a file.

## The `file:` reader (binding requirement C-3)

C-3 of `stu-006-review.md` requires these checks at read time (`read_secret_file`):

1. **No `expanduser`, no URL decoding, no Unicode normalization:** `file:~/x` is the file `<project>/~/x`, and
   `%5C%5Chost` or `＼＼host` is a literal name inside the project.
2. **Containment, now:** network and device forms are refused as text. For a project profile, the file must lie in
   the project folder or the configuration folder, first as text and then on the file system, without following a
   link to a network or device form (`config.locations.contained`, review T-2), and once more on the exact path
   that is opened. For the user's own profiles there is
   no folder rule, but the other checks apply.
3. **No link:** the file itself must not be a link. The real path is opened one component at a time with
   `O_NOFOLLOW` (`openat`), so a folder replaced by a link after the check fails. The final open uses `O_NONBLOCK`,
   so a FIFO cannot block the server. On Windows, every component must be free of reparse points.
4. **The opened file:** a regular file with exactly one link (no hard link), owned by the user, and with no group or
   other permission bit (0600 or 0400; POSIX). On Windows the folder ACL decides (TD-003).
5. **The content:** at most 4 KB of UTF-8, not empty, no NUL. Exactly one trailing line end is removed: `\r\n` or `\n`, nothing else (a lone `\r`
   stays).

Codes: `studio.secret_file_outside`, `studio.secret_file_unsafe`, `studio.secret_file_not_private`,
`studio.secret_file_unreadable`.

## API

| Route | What it does |
|---|---|
| `GET /api/profiles` | each profile has `secret: {source, has_secret}`. `has_secret` is `true` for a session secret or a set `env:` variable, `false` for an unset variable, and `null` when it is known only when connecting (`keyring:` and `file:`). Listing never reads the keyring, because a read can show a system dialog on macOS |
| `GET /api/secrets` | `{"keyring": {"available": true \| false}}` |
| `PUT /api/profiles/{id}/secret` | body `{"secret": "...", "store": "keyring" \| "session"}` (1 to 1024 characters); 204. `keyring` needs a `keyring:` profile; `session` also works for a profile without a reference. An `env:` or `file:` profile names its own source (`studio.secret_not_storable`) |
| `DELETE /api/profiles/{id}/secret` | removes the session secret and, for a `keyring:` profile, the keyring entry; 204 |

`{id}` is `user:<name>`, or `project:<name>` while the project file is trusted. The routes sit behind the access
middleware (session, CSRF header, Origin, JSON). The request log names the route template, never the path or the
body. `Configuration.resolve_secret(id)` is what STU-009 calls when it connects.

## Not yet

- Showing connection URLs through `tabrion.safe_url()` arrives with the first tabrion API use (STU-009, RC-8).
- The UI that asks for a password and offers "save in keyring" is STU-008.
- Importing `DATABASE_URL` from a project's `.env` into the keyring is a Studio 1.0 feature.

## Tests

| Test | What it proves |
|---|---|
| `tests/unit/test_secrets.py` | the `file:` reader: read and registered, one newline stripped, `~` never expanded and `expanduser` never called, URL-encoded and full-width separators read literally without `unquote` or `normalize`, outside and network forms refused, containment checked at read time, a link, mode 0640/0604/0644/0666, a hard link, a FIFO (without blocking), a folder and another user's file refused, empty, too large, not UTF-8 or NUL refused, a link swapped in after the check refused; refused keyring backends (`fail`, `null`, `keyrings.alt`) and the chainer never used; keyring errors without their message; no file written without a keyring; resolution order, `has_secret` without reading the keyring, validation of entered secrets |
| `tests/security/test_secrets_api.py` (STU-T-008) | a canary entered, in a variable and in a `file:` secret never appears in an answer or a log line; keyring and session storage, delete; without a keyring, 409 and no file anywhere; refusals without echo; session, CSRF, Origin and JSON required; a trusted project's `file:` secret with the C-3 checks; project keyring entries bound to the folder; a keyring error without its message |

`tests/conftest.py` replaces the keyring backend selection for every test, so no test reads or writes the
developer's or the CI runner's keyring; the tests use an in-memory backend (`tests/support/fake_keyring.py`).
