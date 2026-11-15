"""The routes of the shared write foundation (STU-XW-1, STU-ADR-0006, W-9).

* ``GET /api/write/areas``: which write areas are on, read only (``{"areas": {"data_editor": false, ...}}``). The
  page uses it to show or hide controls; hiding a control is UX, the server refuses regardless. No route reads,
  sets or toggles a flag (TX-01).
* ``GET``, ``POST``, ``PUT``, ``DELETE /api/write/profiles``: the user's write and admin profiles (``profiles.toml``
  only, K-1). Creating one needs ``"confirm_no_prod_credentials": true``: the page shows, at that moment, that Studio
  relies on the environment the user gives the profile and that write credentials of a production database do not
  belong in Studio (review N-6).
* ``PUT``, ``DELETE /api/write/profiles/{id}/secret``: the write or admin profile's own secret (keyring or session).
* ``GET``, ``POST``, ``DELETE /api/write/unlocks``: this session's unlocks; unlock with
  ``{"connection_id", "write_profile_id", "typed_name"}``, lock with ``{"connection_id"}``.
* ``GET /api/write/audit[?limit=N]``: the audit log of write and admin actions (STU-XW-2), read only: the newest
  records (no values: names, counts, codes and hashes), a fresh verification of its hash chain and the number of
  pending records (review N-4). No route changes or removes a record.
* ``POST /api/write/plans``: make a plan and get its preview (STU-XW-3, :mod:`tabrion_studio.write.plan`), with
  ``{"connection_id", "executor", "request"}``; ``DELETE /api/write/plans`` with ``{"plan_hash"}`` drops it.
* ``POST /api/write/plans/confirm``: ``{"connection_id", "plan_hash", "typed"}``, the typed confirmation (D-09, D-10).
* ``POST /api/write/plans/execute``: ``{"connection_id", "plan_hash"}``: runs the confirmed plan once, after the
  binding, expiry and drift checks, through the audited transaction.
* ``POST /api/write/secret-confirmations``: ``{"write_profile_id"}``: the preview of the I-109 j confirmation (D-15,
  STU-XW-4, :mod:`tabrion_studio.write.secret_confirm`): the pair of server and secret reference of a PostgreSQL
  write or admin profile and its confirmation hash. ``POST /api/write/secret-confirmations/confirm`` with
  ``{"confirmation_hash", "typed"}`` (the host, typed) confirms it; ``DELETE /api/write/secret-confirmations`` with
  ``{"write_profile_id"}`` revokes it. Without a confirmed pair no secret of the profile is resolved and no unlock
  happens.
* ``POST /api/write/data-editor/table``: ``{"connection_id", "schema", "table"}``: the Data Editor's table (STU-026,
  :mod:`tabrion_studio.write.data_editor`): its columns with their types (STU-XDE-1, ``column_types``), its primary
  key, its triggers, rules and row security, read on the unlock's write connection; a view, a partitioned table and a
  table without a primary key are refused. Its changesets run as plans of the executor ``data_editor.changeset``
  through the plan routes above; a value that does not fit its column is refused when the plan is made
  (``400 studio.data_editor_value_invalid`` with positions only). Only the Data
  Editor flag opens it and registers the executor; with that flag off it answers 405 like every write route.
* ``POST /api/write/schema-designer/create/sqlite``: ``{"path", "typed"}``: a new, private SQLite database file
  (STU-CR-2); ``POST /api/write/schema-designer/create/postgresql``: ``{"connection_id", "name", "owner",
  "template", "encoding", "locale", "typed"}``: a new PostgreSQL database on this session's unlocked write connection
  of a DEV or TEST server (STU-CR-3; :mod:`tabrion_studio.write.schema_designer.create`). Only the Schema Designer
  flag opens them; with it off they answer 405 and the Schema Designer's code is not imported.
* ``POST /api/write/schema-designer/draft``: ``{"connection_id", "schema"}``: start (or start again) this session's
  draft of the connection from a level-2 snapshot of one schema, read on the unlocked write connection (STU-CR-4,
  :mod:`tabrion_studio.write.schema_designer.designer`); ``DELETE`` with ``{"connection_id"}`` discards it.
  ``POST /api/write/schema-designer/draft/view``: ``{"connection_id"}``: the draft as it is.
  ``POST /api/write/schema-designer/draft/edits``: ``{"connection_id", "revision", "edit"}``: one edit operation
  (add, drop or rename a table or column, change a column, add or drop a constraint or an index), checked by the
  server against the level-2 model and by a tabrion plan. ``POST /api/write/schema-designer/draft/preview``:
  ``{"connection_id"}``: the read-only plan preview from a fresh snapshot to the draft (steps, risk classes, SQL,
  fingerprints, plan hash, the preview warnings, what the confirmation needs and whether the plan may run directly).
  The draft lives only on the server; no route takes a model, a snapshot or a plan from the page (GS3-09).
  ``POST /api/write/schema-designer/plan/confirm``: ``{"connection_id", "plan_hash", "typed", "tables", "steps",
  "locking", "backup", "backup_typed"}``: confirm the previewed plan per risk class (STU-CR-5);
  ``POST /api/write/schema-designer/plan/run``: ``{"connection_id", "plan_hash", "dry_run"}``: plan again, compare the
  hash with the confirmed one, make the backup and run the plan once through tabrion's ``execute_plan`` on a DEV or
  TEST database without migration management (:mod:`tabrion_studio.write.schema_designer.execute`). Schema Designer
  flag only, like the creation routes.

**Kill switch (TX-02).** :data:`WRITE_ROUTES` is the one table of these routes. When no write area is on, each of
them is a refusing handler that answers ``405 studio.method_not_allowed`` and neither imports, constructs nor calls
any of the write code (the package :mod:`tabrion_studio.write` is not even imported). When an area is on, the real
handlers come from :mod:`tabrion_studio.write.routes`, which registers exactly the same table; a route of a kind
whose area is off still answers 405 there. The routes are left out of the OpenAPI document (as ``/api/session``
is): the 1.0 acceptance check of the documented API stays as it was, and ``tests/security/test_every_route.py``
lists every one of them in ``CHANGING_ROUTES``.

Every refusal is a catalog problem: 400 or 405 for a refused write, 403 only for a write mode (W-9), never a value,
a name from the request, a secret or a database message.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.write_areas import ALL_OFF, WriteAreas
from tabrion_studio.problems import problem_response

if TYPE_CHECKING:
    from tabrion_studio.services.connections import ConnectionRegistry
    from tabrion_studio.write.unlock import Unlocks

#: Every route of the write foundation: (path, methods). The refusing and the real handlers serve exactly these.
WRITE_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/api/write/profiles", ("GET", "POST", "PUT", "DELETE")),
    ("/api/write/profiles/{profile_id:path}/secret", ("PUT", "DELETE")),
    ("/api/write/unlocks", ("GET", "POST", "DELETE")),
    ("/api/write/audit", ("GET",)),
    # STU-XW-3: the plan, preview and confirmation framework (D-09..D-11).
    ("/api/write/plans", ("POST", "DELETE")),
    ("/api/write/plans/confirm", ("POST",)),
    ("/api/write/plans/execute", ("POST",)),
    # STU-XW-4: the I-109 j confirmation of a write or admin secret's pair of server and reference (D-15).
    ("/api/write/secret-confirmations", ("POST", "DELETE")),
    ("/api/write/secret-confirmations/confirm", ("POST",)),
    # STU-026: the Data Editor's table (columns, primary key, triggers and rules) for its grid; Data Editor flag only.
    ("/api/write/data-editor/table", ("POST",)),
    # STU-CR-2, STU-CR-3: the Schema Designer's database creation; Schema Designer flag only (G-S3).
    ("/api/write/schema-designer/create/sqlite", ("POST",)),
    ("/api/write/schema-designer/create/postgresql", ("POST",)),
    # STU-CR-4: the Schema Designer's server-side draft, its edit operations and the read-only plan preview (G-S3).
    ("/api/write/schema-designer/draft", ("POST", "DELETE")),
    ("/api/write/schema-designer/draft/view", ("POST",)),
    ("/api/write/schema-designer/draft/edits", ("POST",)),
    ("/api/write/schema-designer/draft/preview", ("POST",)),
    # STU-CR-5: confirm the previewed plan per risk class, then run it once (or dry-run it) on DEV or TEST (G-S3).
    ("/api/write/schema-designer/plan/confirm", ("POST",)),
    ("/api/write/schema-designer/plan/run", ("POST",)),
)


#: The body cap of ``POST /api/write/plans`` (the app's body limit, :data:`tabrion_studio.app.BODY_LIMITS`): a plan's
#: request of at most ``write.plan.MAX_REQUEST_BYTES`` (256 KiB, which carries a Data Editor bytes value replaced by a
#: file, G-S2 6A) and the rest of the body. Here, not in the write package, so the app needs no write code for it.
MAX_PLAN_BODY = 256 * 1024 + 4096


async def _refused() -> Response:
    """The handler of every write route while its area is off: 405, nothing read, nothing imported (W-9, TX-02)."""
    return problem_response("studio.method_not_allowed")


def add_write_routes(
    app: FastAPI,
    areas: WriteAreas = ALL_OFF,
    config: Configuration | None = None,
    registry: ConnectionRegistry | None = None,
    audit_dir: Path | None = None,
    log_dir: Path | None = None,
) -> Unlocks | None:
    """Register the write foundation's routes; the unlocks when an area is on (closed by the app on shutdown).

    ``audit_dir`` is the audit log's folder (``audit`` in the folder of ``write-areas.toml``); without one, every unlock
    and write is refused (fail closed). It is neither created nor read while every area is off. ``log_dir`` is Studio's
    log folder, one of the folders the Schema Designer creates no database in."""
    snapshot = areas.to_dict()

    @app.get("/api/write/areas")
    async def get_write_areas() -> Response:
        # Read only: the state read at start. No query parameter, header or body is looked at.
        return JSONResponse({"areas": dict(snapshot)})

    if not areas.enabled or config is None or registry is None:
        for path, methods in WRITE_ROUTES:
            app.add_api_route(path, _refused, methods=list(methods), include_in_schema=False)
        return None
    # Imported only now: with every flag off, no write code is loaded.
    from tabrion_studio.write.routes import add_enabled_write_routes

    return add_enabled_write_routes(app, areas, config, registry, audit_dir, log_dir)
