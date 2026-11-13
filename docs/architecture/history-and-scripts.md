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
- **When it fails.** The store opens on first use. If it cannot be opened (a refused link, a corrupt file, a full
  disk), the routes answer `studio.state_unavailable` (503), the log has one `studio.state_unavailable` record, and
  queries run on without a history entry.
- **Sharing.** Every Studio instance of the user (one per project folder) shares the file; SQLite's locking
  serializes the writers.
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

- `enabled` (default `true`): `false` records nothing. Entries already kept stay until they expire or are deleted.
- `retention_days` (default 30, 1 to 3650): older entries are not shown and are deleted at the next recording.

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
- At most 100 favorites (`studio.favorites_full`).
- A profile becomes "recent" when `POST /api/connections` answers a connection of it. The 10 newest per scope are
  kept.
- Opening a favorite is an ordinary connect: D-02 for a PROD profile, on the page and on the server (TD-038).

## Scripts

03 section 2 C ("SQL-Skripte öffnen/speichern (Projektordner, nur `.sql`)").

| Route | Answer |
|---|---|
| `GET /api/scripts` | `{scripts: [{path, size, modified_at}], truncated}` |
| `GET /api/scripts/file?path=` | `{path, content, digest}` |
| `PUT /api/scripts/file` | `{"path", "content", "digest": <what the page read> \| null, "overwrite"?}`; `{path, digest}` |

A path is relative to the project folder, with `/`. It is judged as text first, then on the file system, with the
helpers of the project's database paths (threat T-08, `config/locations.py`):

- **Text.** No drive, no root, no `\`, no `..` or `.`, no empty component, no control character, no component that
  starts with `.` (so `.git` and `.tabrion` are not script folders), no leading or trailing blank, at most 512
  characters. The last name ends in `.sql`, in any case.
- **File system.** The path must stay inside the project, and it must be its own real path: a link anywhere on the
  way is refused, even one that stays inside. `safe_realpath` never touches a network or device path.
- **Size and encoding.** A script is a regular file of at most 1 MB of UTF-8. A byte order mark is dropped when
  reading.
- **Line endings.** Files are read and written as bytes, never in text mode, so a script and its digest are
  byte-exact on every system: a CRLF script reads as CRLF, and what the page sends is written as it is. The page keeps
  a script's CRLF line endings when it saves it back.

**Listing.** The listing walks the project without following links. It skips hidden folders and tool folders
(`node_modules`, virtual environments, caches, `dist`, `build`) and goes 8 folders deep. It stops after 1,000
scripts or 20,000 entries (`truncated`).

**Saving.** A save is bound to what the page read:

- `digest` is the SHA-256 of the file the page read, or `null` for a new file;
- a file that changed meanwhile is `studio.script_stale`, and one that appeared is `studio.script_exists`. The page
  asks the user before it sends `overwrite: true`;
- the folder must exist (`studio.script_folder_missing`): Studio creates no folders;
- the write is atomic: a temporary file with `O_CREAT | O_EXCL | O_NOFOLLOW` in the same folder, `fsync`,
  `os.replace`, then `fsync` of the folder.

A new script gets the usual mode of a project file (0666 less the umask), because such files are meant to be checked
in. A replaced one keeps its mode. Nothing runs a script, and no log record names a path or holds a script's text.

The other refusals are `studio.script_invalid`, `studio.script_not_found`, `studio.script_unreadable` (not UTF-8) and
`studio.script_too_large`.

## The catalog for completion

The SQL editor completes keywords, and the tables and columns of the explorer's cached catalog. 03 section 5.4 says a
PROD profile never loads a large catalog without a click. So `GET /api/connections/{id}/catalog?cached_only=true`
answers only a catalog the explorer has already read, and otherwise `studio.catalog_not_loaded` (404). The page asks
this way for PROD and reads the catalog in full only after the user's click.

## The page

- **History** (`frontend/src/views/history/`, route `/history`, ⌘Y / Ctrl+H, 08 section 7): the entries with their
  profile, environment badge, state and parameter names, shown as text (`lib/visible`); profile filter and search on
  the server; "Open in editor" hands the statement to a new editor tab (`editor/handoff.ts`) and never runs it, so the
  parameter panel asks for the values again; delete one entry, or all of them or one profile's after the
  confirmation (the safe option has the focus). The settings view has the `[history]` settings and "Delete history".
- **Favorites**: the star in the connection manager's details (`aria-pressed`), and on the start page the favorites
  and recently opened profiles. "Connect" there only asks the connection manager (`connections/pending.ts`, page
  memory, never stored) to select the profile and run its own Connect: D-02 for PROD and the password dialog stay
  where they are. Without the state database the star and the lists are not shown.
- **Scripts** (`editor/ScriptDialogs.tsx`): "Open…" lists the scripts and opens one in a new tab; "Save" (⌘S /
  Ctrl+S, and D-06's Save) writes a tab back to its script with the digest it read, or asks for a path for a new one
  (checked on the page like on the server); `studio.script_stale` and `studio.script_exists` ask before
  `overwrite: true`; "Download instead" keeps STU-012's download. A tab remembers its script path and digest in its
  draft. CodeMirror works with `\n`: a script read with CRLF line endings is shown with `\n` and saved with CRLF again.
- **Format** (`editor/formatSql.ts`): sql-formatter (MIT, named in 03 section 3.2), loaded on first use, in the
  PostgreSQL or SQLite language, upper-case keywords, `:name` parameters kept. The selection or the whole text is
  replaced in one undoable change; a text the formatter cannot parse stays unchanged.
- **Completion** (`editor/completion.ts`): keywords of the dialect always; the schemas, tables and views of the
  catalog of the tab's open connection (the page opens no connection for it); the columns after `table.` from that
  table's detail, on DEV and TEST only. On PROD only `cached_only=true`, and "Load names" (through the D-02 guard)
  reads the catalog after the click. The completion list closes when a missing parameter takes the focus (B-6 of
  `ux-acceptance.md`).

## Tests

| Test | What it shows |
|---|---|
| `tests/security/test_history.py` | `state.sqlite` is 0600 and opened lazily; a link or a file others may write is refused (`studio.state_unavailable`, queries run on); the canary value of a parameter is in no answer, no file byte and no log record; off, retention, the entry and size caps; paging, search and the profile filter; project scopes; deletes leave no text in the file; favorites and recent profiles; `cached_only` |
| `tests/security/test_scripts.py` | only `.sql` files in the project; every refused path form (traversal, drive, UNC, automount, hidden folder, control character); no link is followed, inside or out; atomic save with the mode kept; stale, exists, folder missing, size and encoding |
| `tests/security/test_every_route.py` | the new routes need a session and the CSRF header, refuse other sites and need JSON |
| `frontend/src/views/history/HistoryView.test.tsx`, `src/views/editor/EditorView.test.tsx` (STU-020 block), `src/views/editor/completion.test.ts`, `src/api/history.test.ts`, `src/views/connections/ConnectionsView.test.tsx` | the page: history list, filters, open and delete; scripts open, save, overwrite, CRLF; format; completion and its PROD rule; favorites, start page, history settings |
| `frontend/e2e/history.spec.ts` | the flows in the harness, keyboard only where it matters (Ctrl+H, the dialogs), axe light and dark in English and German |
| `frontend/e2e/history-backend.spec.ts` | 09 STU-020's acceptance against a real server: favorite made and opened, history without the parameter value (page, answer and state folder), script saved and opened, completion from the catalog |
