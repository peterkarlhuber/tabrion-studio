# Migration status and code trust

STU-015 (03 section 2 F) shows the migration status of an open connection: Alembic, and SQLite's `user_version`. The
other formats of 03 section 2 F (Nodestro, Axentra, Orkexa) are STU-024. Studio reads only and never migrates.

## Routes

| Route | Answer |
|---|---|
| `GET /api/connections/{id}/migrations` | `{connection_id, engine, formats: [...]}`, one entry per format found |
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

The trust is for the project's code as a whole. The dialog's text says so (see [The page](#the-page)). Doc 03 section 7.3 T-08 ("Alembic `env.py`
is not run: status via `ScriptDirectory`") needs this entry as a new row. The planning documents are kept by the
coordinating thread (TODO).

## The page

The view **Migrations** (`/migrations`, `frontend/src/views/migrations/MigrationsView.tsx`) shows the code trust and
the status. It only reads; there is no button that migrates.

- **Migration code.** The state of `GET /api/project/code-trust` in words: no Alembic configuration, not trusted,
  changed since it was trusted, trusted (with the number of revision files), or not valid (with the reason for the
  fixed `issue` code; an unknown code is shown escaped). An answer without a known state reads as "could not read".
- **Trust dialog** (`TrustCodeDialog` in `components/dialogs/StudioDialogs.tsx`). "Review files" opens it for the
  states `untrusted` and `changed`. It names what trusting means, in the page's own de/en text rather than the
  server's `notice`: the code runs as the user in a separate process for at most 30 seconds, it gets the open
  connection's address and password, it can change the database and read the user's files and Studio's settings,
  modules that revision files import are trusted with them but not checked for changes. Below the text it lists
  `alembic.ini`, the `script_location` and every revision file, each as its own escaped and capped entry like D-01
  (U-1). The focus starts on "Don't trust"; Escape and the close button reject. "Trust and run" sends the digest
  the dialog showed. On `studio.trust_stale` the dialog stays open with the new list and says that the files
  changed; trusting then sends the new digest. "Stop trusting" sends `DELETE` (with a JSON body like every write).
- **Status.** The user picks a profile and chooses "Show status". The page opens the profile's read-only connection
  (`POST /api/connections`; for a PROD profile D-02 first, Cancel opens nothing) and reads
  `GET /api/connections/{id}/migrations`. Per format: SQLite's `user_version`; for Alembic the version in the
  database, the heads and the revisions not yet applied (or "unknown" with the reason: code not trusted, folder not
  valid, or the problem of the worker such as `studio.migrations_busy`), and the revisions the database names that
  the project's files do not contain. With STU-024, each Nodestro, Axentra and Orkexa version table shows its
  state, current version, open steps (at most 20, then "and N more steps") and, for an unreadable table, the
  reason in the page's own words (`db.permission`, `studio.migrations_table`, `studio.statement_refused`,
  `db.timeout`; any other code as its problem text); a format the page does not know is counted in a fixed line,
  never dropped (TD-025). A status that arrives after a newer request or a profile switch is dropped. The result region names the profile's environment (badge) and is announced politely
  (`aria-live`). After a trust or a revoke, the shown status is read again on its open connection; the page never
  opens a connection for that, so D-02 cannot be skipped. Revision ids, table, step and file names come from the
  project or the database and are shown escaped (`lib/visible.ts`).

Tests: `MigrationsView.test.tsx` (trust with the shown digest, stale, refused, revoke, the states, D-02, escaping,
problems), the focus rules and U-1 cases of the dialog in `StudioDialogs.test.tsx`, axe in jsdom (`a11y.test.tsx`)
and in Chromium in light and dark, English and German (`e2e/migrations.spec.ts`, `e2e/a11y.spec.ts`,
`e2e/dialogs.spec.ts`).
