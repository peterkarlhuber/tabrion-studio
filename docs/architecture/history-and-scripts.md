# History, favorites and scripts

STU-020 (03 sections 2 A, 2 C, 4.3 and 6; 08 sections 4.4, 4.10 and 5) adds four things:

- the query history;
- favorite and recently opened profiles;
- `.sql` scripts in the project folder;
- a cached-only catalog for the SQL editor's completion.

None of them reads or writes a database server. The code is in `src/tabrion_studio/services/` (`state.py`,
`history.py`, `favorites.py`, `scripts.py`), and the routes are in `src/tabrion_studio/api/history.py` and
`src/tabrion_studio/api/scripts.py`. Every route sits behind the access middleware: the session cookie, the CSRF
header and, for a change, the Origin check and JSON only.

## The state database

`<state dir>/state.sqlite` holds the history, the favorites and the recently opened profiles (03 section 4.3,
threat T-04, STU-T-007).

- **Opening.** The file is opened with `tabrion.sqlite.connect`:
  - a new file is created 0600 with `O_CREAT | O_EXCL | O_NOFOLLOW`, whatever the umask;
  - a link at the file, or at its `-wal`, `-shm` or `-journal` file, is refused;
  - a file that another user owns, or that others may write, is refused;
  - after opening, the path must still be the file that was checked.

  The CLI checks the state folder itself before the server starts: 0700, owned by the user, not a link.
- **Profile.** tabrion's defaults (WAL, `synchronous=FULL`, `trusted_schema=OFF`) plus `secure_delete=ON`, so a
  deleted entry is overwritten in the file.
- **Schema.** It is versioned with `PRAGMA user_version` and migrated with `tabrion.sqlite.migrate` in one write
  transaction. Every statement is a fixed text with bound values.
- **When it fails.** The store opens on first use. If it cannot be opened (a refused link, a corrupt file, a schema of
  a newer Studio, a full disk), the routes answer `studio.state_unavailable` (503) for the life of the process, the
  log has one `studio.state_unavailable` record, and queries run on without a history entry. To recover, stop Studio
  and move `state.sqlite` (with its `-wal` and `-shm` files) out of the state folder; the next start creates an empty
  one, and the history and the favorites start over.
- **Sharing.** Every Studio instance of the user (one per project folder) shares the file; SQLite's locking
  serializes the writers. A write waits up to 5 s for another instance's lock. Recording a query's entry and the
  retention purge wait only 250 ms, in Studio's own loop, and otherwise keep no entry or leave the purge to the next
  one, so a query's end and its cancel are never held up by the history.
- **Checkpoint.** After a delete or a purge, `PRAGMA wal_checkpoint(TRUNCATE)` moves the write-ahead log into the
  file and empties it. A checkpoint never waits for another connection: it runs with `busy_timeout = 0`, so it holds
  neither a query's end nor the store lock. When another instance (or any other program) is reading at that moment,
  the log stays in use: a delete from the history page tries three times, 50 ms apart; otherwise the checkpoint is
  *pending* (`studio.state_checkpoint_partial` in the log, once) and is tried again on every purge, that is on every
  read of the history and every recorded query, until it succeeds. The log is 0600 like the file, and closing the last
  connection removes it.
- **Scopes.** Rows of a project profile carry the project's key, so `project:parts` in one project is not
  `project:parts` in another. Rows of the user's own profiles hold in every project.

## Query history

The query engine hands every finished query to `History.record`. It passes:

- the statement as the user wrote it, with its `:name` placeholders;
- the placeholder **names**, read from that text again with `sqltext.placeholders`;
- the profile, the environment and the engine;
- the final state (`done`, `failed`, `canceled`), the row count, the duration and the problem code (a fixed
  `studio.*` or `db.<kind>` code, never a server message).

**Parameter values never reach the history** (08 section 5, threat T-05). `History.record` has no argument for them,
and the engine never reads `BoundStatement.values` for it. `tests/security/test_history.py` sends a canary value as a
parameter. The canary is in no history answer, in no byte of `state.sqlite` with its `-wal` and `-shm` files, and in
no log record.

| Route | Answer |
|---|---|
| `GET /api/history?profile_id=&q=&limit=&before=` | `{enabled, retention_days, entries: [{id, profile_id, environment, engine, sql, sql_truncated, param_names, state, row_count, elapsed_ms, code, finished_at}], next_before}`, newest first; `q` finds entries whose text contains it; `before` (an entry id) reads the next page; at most 500 per page |
| `DELETE /api/history` | `{}` deletes every entry shown here, `{"profile_id"}` deletes one profile's, `{"id"}` deletes one entry; `{deleted}` |

**Settings** (`[history]` in `settings.toml`, see [Configuration](configuration.md)):

- `enabled` (default `true`): `false` records nothing from then on. The entries already kept are not deleted by
  turning it off: they stay until their retention period ends, or until the user deletes them on the history page.
- `retention_days` (default 30, 1 to 3650): an older entry is deleted from the file, also while the history is off.
  The purge runs when Studio starts, whenever the history is read, when the settings are saved and when an entry is
  recorded. It deletes the expired entries of every project, with `secure_delete` and a checkpoint like a delete. The
  purge after a settings save runs off the server's event loop, so a busy state database never holds up another
  request.

At most 5,000 entries are kept, and the oldest go first. An entry keeps at most 64 KB of a statement
(`sql_truncated`). A delete is followed by `PRAGMA wal_checkpoint(TRUNCATE)`, so no deleted text stays in the
write-ahead log.

An entry is the user's own SQL text, which can hold sensitive literals (asset A5). It lives only in the state
database. The query engine logs no SQL text, and no route other than the history answers it.

## Favorite and recently opened profiles

03 section 2 A ("Favoriten, zuletzt geöffnet"). Both are profile ids in the state database, never a profile's target
or secret.

| Route | Answer |
|---|---|
| `GET /api/favorites` | `{favorites: [{profile_id, added_at}], recent: [{profile_id, opened_at}]}` |
| `PUT /api/favorites` | `{"profile_id", "favorite": true \| false}`; the same document |

- Only profiles that exist now are answered or accepted: the user's, and the project's while its file is trusted.
  An unknown id is `studio.profile_not_found`.
- At most 100 favorites per project (`studio.favorites_full`): the project's own profiles and the user's that exist
  now. Favorites of other projects and of profiles that are gone take no place.
- A profile becomes "recent" when `POST /api/connections` answers a connection of it. The 10 newest per scope are
  kept.
- Opening a favorite is an ordinary connect: D-02 for a PROD profile, on the page and on the server (TD-038).

## Scripts

03 section 2 C ("SQL-Skripte öffnen/speichern (Projektordner, nur `.sql`)").

| Route | Answer |
|---|---|
| `GET /api/scripts` | `{scripts: [{path, size, modified_at}], truncated, skipped_links}` |
| `GET /api/scripts/file?path=` | `{path, content, digest}` |
| `PUT /api/scripts/file` | `{"path", "content", "digest": <what the page read> \| null, "overwrite"?}`; `{path, digest}` |

A path is relative to the project folder, with `/`. It is judged as text first, then on the file system, with the
helpers of the project's database paths (threat T-08, `config/locations.py`):

- **Text.** No drive, no root, no `\`, no `..` or `.`, no empty component, no control character, no format
  character (bidi overrides and isolates, zero-width characters: a name must read as what it is), no blank-looking
  character (a space other than U+0020, the Hangul fillers U+115F, U+1160, U+3164 and U+FFA0, the braille blank
  U+2800, the combining grapheme joiner U+034F, the Khmer inherent vowels U+17B4 and U+17B5), no component that
  starts with `.` (so `.git` and `.tabrion` are not script folders), no leading or trailing blank, at most 512
  characters. The last name ends in `.sql`, in any case.
- **Names Windows reads as something else.** Refused on every system alike, because the same project is opened on
  all of them: device names (`CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`, `LPT0`-`LPT9`, also with the superscript
  digits ¹²³, `CONIN$`, `CONOUT$`), also with an extension (`nul.sql`) and as a folder; a component that ends in `.`
  (Win32 drops it, so `sub.` names `sub`); and a `~` followed by a digit (an 8.3 short name such as `GIT~1` can name
  `.git`). The listing leaves such names out as well.
- **File system.** The path must stay inside the project, and it must be its own real path: a link anywhere on the
  way is refused, even one that stays inside (`studio.script_link`). `safe_realpath` never touches a network or device
  path. A hard-linked file (more than one name) is refused for reading and for saving, because its other name may lie
  outside the project.
- **No check-then-use (07 TM-04).** On POSIX the file is reached from the project folder one name at a time through
  folder descriptors (`openat`-style, `dir_fd`): each folder is opened with `O_DIRECTORY | O_NOFOLLOW`, the file with
  `O_NOFOLLOW`, and the temporary file, the rename and the clean-up use the same folder descriptor. A folder swapped
  for a link after the text checks is never followed. On Windows, Python has no such calls: the folders are checked
  with `lstat` (no link, no reparse point) and, after the file is opened, the handle must be the file that the checked
  path names (same volume and file index). **Residual on Windows:** a folder swapped for a junction between that check
  and the rename of a save. It needs a program of the user's own that writes in the project folder at that moment,
  which has the user's rights anyway.
- **Reparse-point folders (Windows): refused, pending an owner decision (G-S1).** Every folder (and file) with a
  reparse point is refused as a link (`studio.script_link`), and the listing leaves it out and counts it
  (`skipped_links`, which the page shows). That includes OneDrive "Files On-Demand" placeholder folders (Documents
  under Known Folder Move on many company machines), and cloud files carry the attribute too: in such a project,
  scripts are probably unusable, possibly also at the project root. The stated way out is to keep the project in a
  folder that OneDrive does not sync. Whether Studio keeps refusing every reparse point (the safe reading) or accepts
  the cloud tags like `config/locations._is_link` does is an **open owner decision for G-S1**; it also still needs a
  check on a real Windows machine with OneDrive, like the device names. Tests cover this on Linux only.
- **Size and encoding.** A script is a regular file of at most 1 MB of UTF-8.
- **Byte-exact.** Files are read and written as bytes, never in text mode, so a script and its digest are byte-exact
  on every system: CRLF, LF, CR and mixed line endings are kept as they are, and what the page sends is written as it
  is. A UTF-8 byte order mark is left out of the content the page gets (the digest covers it) and is written again when
  the page saves over that file; a new file gets none. On the page, a CRLF script is edited with LF and saved with CRLF
  again; a script with mixed line endings is edited as it is (a CR shows as a marked character), so it is saved byte
  for byte.

**Listing.** The listing walks the project without following links. It skips hidden folders and tool folders
(`node_modules`, virtual environments, caches, `dist`, `build`) and goes 8 folders deep. It stops after 1,000
scripts or 20,000 entries (`truncated`).

**Saving.** A save is bound to what the page read:

- `digest` is the SHA-256 of the file the page read, or `null` for a new file;
- a file that changed meanwhile is `studio.script_stale`, and one that appeared is `studio.script_exists`. The page
  asks the user before it sends `overwrite: true`;
- the digest check and the replace run under one lock, so two saves of the same Studio (two tabs, a double click)
  never both pass the check: the second one is `studio.script_stale`;
- the folder must exist (`studio.script_folder_missing`): Studio creates no folders;
- the write is atomic: a temporary file with `O_CREAT | O_EXCL | O_NOFOLLOW` in the same folder, `fsync`, a rename
  over the script, then `fsync` of the folder.

A new script gets the usual mode of a project file (0666 less the umask), because such files are meant to be checked
in. A replaced one keeps its mode. Nothing runs a script, and no log record names a path or holds a script's text.

The other refusals are `studio.script_invalid`, `studio.script_link`, `studio.script_not_found`,
`studio.script_unreadable` (not UTF-8) and `studio.script_too_large`. A refusal of the file system has its own code,
never `studio.internal_error`: `studio.script_permission_denied` (also a read-only drive),
`studio.script_locked` (another program holds the file open, Windows), `studio.script_disk_full`, and otherwise
`studio.script_write_failed`.

## The catalog for completion

The SQL editor completes keywords, and the tables and columns of the explorer's cached catalog. 03 section 5.4 says a
PROD profile never loads a large catalog without a click. So `GET /api/connections/{id}/catalog?cached_only=true`
answers only a catalog the explorer has already read, and otherwise `studio.catalog_not_loaded` (404). The page asks
this way for PROD and reads the catalog in full only after the user's click.

## Tests

| Test | What it shows |
|---|---|
| `tests/security/test_history.py` | `state.sqlite` is 0600 and opened lazily; a link or a file others may write is refused (`studio.state_unavailable`, queries run on); the canary value of a parameter is in no answer, no file byte and no log record; off, retention on disk (also while off: at start, on read, on a settings save), the entry and size caps; recording waits only briefly for another writer; the checkpoint result; the favorites limit per project; paging, search and the profile filter; project scopes; deletes leave no text in the file; favorites and recent profiles; `cached_only` |
| `tests/security/test_scripts.py` | only `.sql` files in the project; every refused path form (traversal, drive, UNC, automount, hidden folder, control and format characters, device names, 8.3 names, trailing dots); no link is followed, inside or out, also when a folder is swapped after the check; hard links; atomic save with the mode kept; the byte order mark and mixed line endings kept; two saves under one lock; stale, exists, folder missing, size and encoding; file-system refusals with their own codes |
| `tests/security/test_every_route.py` | the new routes need a session and the CSRF header, refuse other sites and need JSON |
