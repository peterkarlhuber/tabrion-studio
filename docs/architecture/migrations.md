# Migration status and code trust

STU-015 (03 section 2 F) shows the migration status of an open connection: Alembic, and SQLite's `user_version`.
STU-024 adds the Levuvo formats Nodestro, Axentra and Orkexa on PostgreSQL. Studio only reads and never migrates.

## Routes

| Route | Answer |
|---|---|
| `GET /api/connections/{id}/migrations` | `{connection_id, engine, formats: [...]}`, one entry per format found (and per schema and namespace for the Levuvo formats) |
| `GET /api/project/code-trust` | `{state, trusted, format, config_file, digest, script_location, revision_files, issue, notice}` |
| `POST /api/project/code-trust` | `{"digest": "<the digest the dialog showed>"}`: 204, or `studio.trust_stale`, `studio.migrations_invalid`, `studio.no_migrations` |
| `DELETE /api/project/code-trust` | 204: the project's code is not trusted any more |

The formats:

- `sqlite_user_version` (SQLite): `{current}` from `PRAGMA user_version`, the version that tabrion's `sqlite.migrate`
  keeps. tabrion's SQLite migrations are Python callables of the application, so tabrion 0.1 has no migration folder
  to compare with.
- `alembic` (the project folder has an `alembic.ini` with a `[alembic]` `script_location`): `{code_trust,
  code_issue, script_location, current, heads, pending, is_current, unknown, revisions, problem}`.
  - `current` comes from the version table `alembic_version` on the search path, read with plain SQL. It is `null`
    when the table does not exist. A version table that `env.py` renames is not found, because Studio never runs
    `env.py`.
  - `heads`, `pending`, `is_current` and `unknown` need the revision graph. They are read only when the project's code
    is trusted. Otherwise they are `null`, and `revisions` is `unknown (project not trusted)`, or `unknown
    (migrations folder not valid)` when the files cannot be checked.
  - If the worker fails, `problem` holds the problem (`studio.migrations_timeout`, `studio.migrations_revisions`,
    `studio.migrations_changed`, `studio.migrations_failed`), and `revisions` is `unknown (could not be read)`.
    Studio runs one worker at a time; a second status request meanwhile gets `studio.migrations_busy`.

## Levuvo formats (STU-024)

`services/levuvo_migrations.py` reads the platforms' version tables in every schema of the database, and the
migration files of the project folder. It runs nothing.

| Format | Version table | Files | Compared by |
|---|---|---|---|
| `nodestro` | `nodestro_schema_migrations` (`version`, `name` = file name, `checksum`) | `nodestro_runtime/migrations/postgres/NNNN_name.sql` | version, file name, checksum (an old table without `checksum` compares by name) |
| `axentra` | `axentra_schema_migrations` (`namespace`, `version`, `name`, `checksum`) | `axentra/data/sql/NNNN_name.sql` (namespace `axentra`) | per namespace: version, name, checksum |
| `orkexa` | `<prefix>orkexa_schema` (`version`) | none (the schema is code) | the newest version against `SCHEMA_VERSION` in `orkexa/backends/postgres/schema.py` |

- **The checksum.** It is the one both runners record: SHA-256 of the file's text as `Path.read_text` gives it
  (UTF-8, every line end `\n`).
- **Where the files are found.** Studio uses the first folder found inside the project folder: `src/<package
  path>`, `<package path>`, or the `site-packages` of a `.venv` or `venv` in the project.
- **Axentra product namespaces** (`crm`, ...) are registered in code (`database.extra_migrations`), so Studio has no
  files for them. They show what the database recorded, with `state: no_files`.
- **Orkexa's version** is read as text with a regular expression. The module is never imported.
- **Orkexa's version tables** are only the names Orkexa can create: its table prefix (`PREFIX` in
  `orkexa/backends/postgres/_sql.py`: a lower-case letter, up to 30 of `[a-z0-9_]`, ending in `_`) followed by
  `orkexa_schema`. Other tables whose names end in `orkexa_schema` are ignored.
- **Limits.** At most 100 version tables (sorted by schema and name) and 10 000 rows per table are read. Nothing flags
  a cut yet (M-3 of `stu-024-review.md`, in `docs/TODO.md`).

Each entry has a `state`:

- `current`: everything applied.
- `pending`: files the database has not applied. Each pending step comes with its file name, checksum and the first
  2 000 characters of its SQL, as the runner reads it (every line end `\n`, also for a file written with `\r\n`).
- `newer`: the database has versions that the files do not know.
- `changed`: an applied file's name or checksum differs.
- `no_files`: Studio found no files to compare with.
- `unreadable`: Studio could not read this version table. `problem` holds the reason, `current` is `null` and
  `steps` is empty. The other tables and formats are read as usual, so one such table never fails the answer.
  - `db.permission`: the role has no `USAGE` on the schema or no `SELECT` on the table. The catalog query checks this
    first, and Studio does not query the table.
  - `studio.migrations_table`: a column Studio reads is missing, `version` is not `smallint`, `integer` or `bigint`,
    or a value cannot be converted.
  - another `db.<kind>` or `studio.statement_refused` when reading the table failed.

Every Levuvo entry has `problem`, which is `null` unless the state is `unreadable`. Each step has a `status`:
`applied`, `pending`, `unknown` or `changed`. When several apply, the entry's state is the first of `changed`,
`newer`, `pending`.

**Trust.** These files are SQL and text that Studio only reads, so threat T-16 does not apply and they need no code
trust. They follow the same file rules as the Alembic files:

- the folder must stay inside the project after links are followed;
- each file must be a regular file and not a link;
- at most 2 000 files, and 256 KB per file.

The tests use files copied from the three repositories (`tests/security/levuvo_fixtures/SOURCE.txt`).

## Why the revisions need trust

`tabrion.migrations.status` never runs `env.py`. However, Alembic's `ScriptDirectory` builds the revision graph by
importing every file in `<script_location>/versions`, and importing a Python file runs it. 03 section 7.3 T-08 counted
on "no code from project files". That still holds for the profiles, but not for the revision graph. A cloned
repository (AT4) could put any code into a revision file. So:

- **Off by default.** Without a trust record, the state is `untrusted`, and no project code runs.
- **Trust is per project and bound to the files.** The page shows `notice`, `script_location` and the list of revision
  files, and sends back the digest it showed. The digest is SHA-256 over `alembic.ini` and every `*.py` directly in
  `versions` (name and bytes). The record is `<state_dir>/code-trust-<key>.json` (0600). Any change to these files
  makes the state `changed`, and nothing runs until the user trusts the new digest. `DELETE` takes the trust back.
- **Checked on the file system.** The `script_location` must lie inside the project, also after links are followed.
  A revision file that is a link or not a regular file, more than 5 000 revision files, or a file over the
  configuration size cap (256 KB) makes the state `invalid`, which cannot be trusted.

## The worker

When the code is trusted, `services/migrations.py` starts `services/migration_worker.py` as its own process:
`python -I -B -c <boot> <package folder>`. The boot code loads the worker from the same `tabrion_studio` package as
Studio. `-I` keeps out the `PYTHON*` variables, the user site and the current folder (the project folder, where the
worker runs), so no file of the project is imported while the worker starts.

- **Only this connection's target.** The worker gets the target on standard input, never in its arguments or its
  environment: the target fields, the timeouts, and the password or the database file, read again for this run
  (`ConnectionRegistry.worker_target`). Studio passes nothing else, no other profile and no session or control token.
  This keeps Studio's other secrets out of the worker's input; it does not keep trusted code away from them (see
  below).
- **A minimal environment.** The worker inherits only `PATH`, the home and temporary folders, `SYSTEMROOT`/`WINDIR`/
  `COMSPEC` and the locale. It gets no `env:` secret of another profile and no `PG*` variable. libpq's own password
  fallbacks are off, as on every Studio connection.
- **The same read-only connection.** The worker opens its connection through `connections.read_only_database`:
  `default_transaction_read_only`, Studio's timeouts, the SQLite authorizer, and `Database.read()` around the version
  table.
- **Checked again, and run as checked.** The worker computes the digest again and refuses to import anything if it
  differs (`studio.migrations_changed`). It keeps the bytes it hashed, and Alembic's loader for the revision files is
  replaced by one that compiles exactly those bytes. A revision file that changes on disk afterwards, or a file that
  was not hashed, is not run.
- **No bytecode of the project.** The worker writes no bytecode, and from the moment the project's folders are on
  `sys.path` it reads none: `sys.pycache_prefix` points to an empty private folder, so no `__pycache__` file is used,
  and the `prepend_sys_path` folders get an import finder for `.py` sources only (no `.pyc` or extension module).
  Folders that were on `sys.path` before (Studio's own environment) keep the usual finder.
- **Studio's modules first.** The worker opens the connection once before the `prepend_sys_path` folders are put on
  `sys.path`. The database driver, the dialect and what they import are
  therefore loaded from Studio's environment, never from a module of the same name in the project.
- **Isolated output.** Its standard input, output and error are closed before any project code runs. The answer goes
  out on a copy of standard output taken before. Studio reads it up to the first line end and at most 1 MB, and does
  not wait for the pipe to close. After the answer, the worker ends with `os._exit`, so no exit handler of the
  project's code runs.
- **Bounded.** The worker runs in the project folder with `prepend_sys_path` folders inside the project, as Alembic
  does. Once the answer is in, or after 30 seconds (`studio.migrations_timeout`), Studio kills the worker's process
  group (POSIX), which also ends the children the project's code started in it. Studio then waits at most 5 seconds
  for the killed worker and never for the pipe, so a child that left the process group (its own session) cannot hold
  the request. Such a child itself keeps running until it ends.
- **Windows.** There is no process group. Instead, the worker goes into a job object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before it gets its request. Closing the job after the answer or the timeout
  ends the worker and every process it started, and they cannot leave the job. If the job cannot be made (an old
  Windows that does not allow nested jobs), only the worker is killed. The answer is read by a thread, which owns the
  answer pipe and closes it when its read ends; Studio waits at most 5 seconds for it after the worker is gone.
  `test_children_end_with_the_worker_on_every_platform` checks this on every platform; on Windows it runs in the
  `backend test (windows-latest)` job of `ci.yml` (TD-024).

## Threat model entry

| ID | STRIDE | Threat | Measure | Test |
|---|---|---|---|---|
| T-16 | E/I | AT4: a revision file of a cloned project runs code when Studio reads the Alembic revision graph (`ScriptDirectory` imports `versions/*.py`). That code could read Studio's secrets or act with them. | The revision graph is read only when the project's code is trusted, which is off by default and bound to the digest of `alembic.ini` and the revision files; no other project code runs before the user trusts it. The worker runs the hashed bytes, reads no bytecode of the project and loads the driver before the project's folders are on `sys.path`. It is a separate process that gets only the open connection's target on standard input, with a minimal environment and closed standard streams, and its process group is killed after the answer or after 30 s (POSIX). Without trust, the revisions are `unknown (project not trusted)`, and `current` comes from plain SQL. Once trusted, the code runs as the user: the separate process and the read-only connection protect against mistakes, not against the trusted code (see below). | `tests/security/test_migrations.py` |

What the measure does not cover. Trusting the code means trusting it as you would trust `alembic heads`:

- **Trusted code runs as the user.** It can do whatever the user's account can: read the keyring, Studio's state
  folder (session and control tokens), Studio's own environment (for example through `/proc` on Linux, with the
  `env:` secrets of every profile and any `PG*` variable), and the password of the connection, which the worker
  holds in memory.
- **The read-only connection is not a boundary for trusted code.** The code can open its own connection with that
  password and write to the database, or write to a SQLite file directly. The read-only settings, the minimal
  environment and the separate process keep Studio's own reads from writing and keep secrets out of the worker by
  accident. They do not stop code that sets out to do otherwise. The worker runs no user SQL, so Studio's SQL checks
  (STU-011) play no part here.
- **Modules on `prepend_sys_path` are not covered by the digest.** A revision file can import other modules of the
  project, and those modules are part of the trusted code. A change to them does not ask for trust again; only
  `alembic.ini` and the revision files do.
- **Windows:** children of the project's code are bounded by the job object only when Windows lets Studio make one
  (see the worker).

The trust is for the project's code as a whole. The dialog's text says so. Doc 03 section 7.3 T-08 ("Alembic `env.py`
is not run: status via `ScriptDirectory`") needs this entry as a new row. The planning documents are kept by the
coordinating thread (TODO).
