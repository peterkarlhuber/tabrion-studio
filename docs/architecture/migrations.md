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
`python -I -c <boot> <package folder>`. The boot code loads the worker from the same `tabrion_studio` package as Studio.
`-I` keeps out the `PYTHON*` variables, the user site and the current folder.

- **Only this connection's target.** The worker gets the target on standard input, never in its arguments or its
  environment: the target fields, the timeouts, and the password or the database file, read again for this run
  (`ConnectionRegistry.worker_target`). Nothing else of Studio reaches it: no other profile, no session or control
  token, no keyring access through Studio.
- **A minimal environment.** The worker inherits only `PATH`, the home and temporary folders, `SYSTEMROOT`/`WINDIR`/
  `COMSPEC` and the locale. It gets no `env:` secret of another profile and no `PG*` variable. libpq's own password
  fallbacks are off, as on every Studio connection.
- **The same read-only connection.** The worker opens its connection through `connections.read_only_database`:
  `default_transaction_read_only`, Studio's timeouts, the SQLite authorizer, and `Database.read()` around the version
  table.
- **Checked again before import.** The worker computes the digest again and refuses to import anything if it
  differs (`studio.migrations_changed`).
- **Isolated output.** Its standard input, output and error are closed before any project code runs. The answer goes
  out on a copy of standard output taken before, and Studio reads at most 1 MB of it.
- **Bounded.** The worker runs in the project folder with `prepend_sys_path` folders inside the project, as Alembic
  does. It is killed with its process group after 30 seconds (`studio.migrations_timeout`), together with any child
  that the project's code started (POSIX).

## Threat model entry

| ID | STRIDE | Threat | Measure | Test |
|---|---|---|---|---|
| T-16 | E/I | AT4: a revision file of a cloned project runs code when Studio reads the Alembic revision graph (`ScriptDirectory` imports `versions/*.py`). That code could read Studio's secrets or act with them. | The revision graph is read only when the project's code is trusted, which is off by default and bound to the digest of `alembic.ini` and the revision files. It runs in a separate process that gets only the open connection's target on standard input, with a minimal environment and closed standard streams. The process is checked again before import and killed with its children after 30 s. Without trust, the revisions are `unknown (project not trusted)`, and `current` comes from plain SQL. | `tests/security/test_migrations.py` |

What the measure does not cover:

- A trusted revision file runs as the user, as `alembic heads` would. It can do whatever the user's account can,
  including reading the keyring directly.
- A revision file can import other modules of the project, which the digest does not cover.

The trust is for the project's code as a whole. The dialog's text says so. Doc 03 section 7.3 T-08 ("Alembic `env.py`
is not run: status via `ScriptDirectory`") needs this entry as a new row. The planning documents are kept by the
coordinating thread (TODO).
