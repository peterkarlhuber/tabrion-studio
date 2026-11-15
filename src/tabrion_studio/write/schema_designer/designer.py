"""The Schema Designer's routes for drafts and the read-only plan preview (STU-CR-4; STU-ADR-0007 sections 2, 3, 4
item 1, 9 and 11; TX-21, TX-26, TX-27; GS3-09, GS3-13, GS3-20).

Imported only by :mod:`tabrion_studio.write.routes` when the ``schema_designer`` flag is on (GS3-01). Every call
first re-checks this session's unlock of the connection for the Schema Designer (:meth:`Unlocks.require_unlock`: the
flag, the unlock and its expiry, the coupling, the stored environment, never the request's; PROD is never unlocked,
E-X7; decision 1A refused an over-privileged write role at the unlock), and a draft is used only under the unlock it
was started with (:class:`.draft.Drafts`).

* :func:`start`: a level-2 snapshot of one schema on the **write** connection (``main`` on SQLite; a PostgreSQL
  schema the role can see, not one of PostgreSQL's own) becomes both the draft's starting snapshot and its first
  model; the engine version for every plan is read on the same connection (:meth:`WriteConnection.engine_version`).
  Starting again replaces the draft.
* :func:`edit`: one edit operation (:mod:`.draft`), made on the revision the page names.
* :func:`preview`: a **fresh** level-2 snapshot on the write connection, then ``tabrion.plan.plan(fresh, draft,
  server_version=<read now on the write connection>)``: every step with its operation, table, risk classes,
  transactional flag and exact SQL, both fingerprints and the plan hash. Nothing runs: there is no route that executes
  a plan of the Schema Designer (confirmation, re-plan, ``execute_plan`` and the backup are STU-CR-5). The plan is not
  kept either; it is the user's view of what tabrion would do.

**Nothing is written** to the database, the project or the disk by these routes, so they write no audit record
(STU-ADR-0007 section 11 audits runs and revisions). The log gets fixed event names and problem codes only: never a
table or column name, a type, a default or check text, a statement, a fingerprint or a hash (TX-27).
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy
from tabrion.errors import TabrionError, UsageError, classify, safe_message
from tabrion.schema import SchemaSnapshot, fingerprint, snapshot

from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.connection import WriteConnection, WriteError
from tabrion_studio.write.schema_designer.create import DriverFailure
from tabrion_studio.write.schema_designer.draft import (
    Draft,
    Drafts,
    apply_edit,
    check_limits,
    check_schema,
    draft_view,
    plan_for,
    refusal,
    table_names,
    validate,
)
from tabrion_studio.write.unlock import Unlock, Unlocks

logger = logging.getLogger("tabrion_studio.write")


def _unlock(unlocks: Unlocks, session: str, connection_id: object) -> Unlock:
    if not isinstance(connection_id, str):
        raise WriteError("studio.write_target_unknown")
    return unlocks.require_unlock(session, connection_id, Area.SCHEMA_DESIGNER)


def _snapshot(connection: WriteConnection, schema: str) -> SchemaSnapshot:
    """The level-2 snapshot of ``schema`` on the write connection, within the table limit. A driver error is a
    ``db.<kind>`` problem (:class:`DriverFailure`), logged with ``safe_message`` only."""

    def read(inspector: sqlalchemy.Inspector) -> SchemaSnapshot:
        if connection.engine != "sqlite" and schema not in inspector.get_schema_names():
            raise WriteError("studio.schema_draft_schema")
        argument = None if connection.engine == "sqlite" else schema
        table_names(inspector.get_table_names(schema=argument))
        return snapshot(inspector, schemas=[schema], level=2)

    try:
        found: SchemaSnapshot = connection.inspect(read)
    except WriteError:
        raise
    except UsageError as exc:
        raise refusal(exc) from None
    except TabrionError as exc:
        if exc.classification is None:
            raise refusal(UsageError("schema.invalid_snapshot")) from None
        logger.info("schema not read", extra={"event": "studio.schema_snapshot_failed", "detail": safe_message(exc)})
        raise DriverFailure(exc.classification) from None
    except sqlalchemy.exc.DBAPIError as exc:
        classification = classify(exc.orig if exc.orig is not None else exc)
        logger.info(
            "schema not read",
            extra={"event": "studio.schema_snapshot_failed", "detail": safe_message(classification)},
        )
        raise DriverFailure(classification) from None
    check_limits(found)
    return found


def start(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Start (or start again) the draft of this session and connection from a snapshot of ``doc["schema"]``."""
    unlock = _unlock(unlocks, session, doc.get("connection_id"))
    connection = unlock.connection
    schema = check_schema(doc.get("schema"), connection.engine)
    version = connection.engine_version()
    base = _snapshot(connection, schema)
    draft = Draft(
        session=session,
        connection_id=unlock.connection_id,
        unlock_id=unlock.unlock_id,
        engine=connection.engine,
        schema=schema,
        base=base,
        model=base,
        server_version=version,
    )
    drafts.put(draft)
    logger.info("schema draft started", extra={"event": "studio.schema_draft_started"})
    return draft_view(draft)


def _draft(unlocks: Unlocks, drafts: Drafts, session: str, connection_id: object) -> Draft:
    unlock = _unlock(unlocks, session, connection_id)
    return drafts.get(session, unlock.connection_id, unlock.unlock_id)


def view(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    draft = _draft(unlocks, drafts, session, doc.get("connection_id"))
    with draft.lock:
        return draft_view(draft)


def discard(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> None:
    """Drop the draft. Needs no unlock: a draft of an unlock that ended is gone already, and dropping never writes."""
    connection_id = doc.get("connection_id")
    if not isinstance(connection_id, str):
        raise WriteError("studio.write_target_unknown")
    if drafts.discard(session, connection_id):
        logger.info("schema draft discarded", extra={"event": "studio.schema_draft_discarded"})


def edit(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Apply one edit to the draft on the revision the page names; refused edits leave the draft unchanged."""
    draft = _draft(unlocks, drafts, session, doc.get("connection_id"))
    revision = doc.get("revision")
    if type(revision) is not int:
        raise WriteError("studio.schema_draft_stale")
    with draft.lock:
        if revision != draft.revision:
            raise WriteError("studio.schema_draft_stale")
        try:
            model = apply_edit(draft, doc.get("edit"))
            validate(draft, model)
        except WriteError as exc:
            logger.info("schema edit refused", extra={"event": "studio.schema_edit_refused", "code": exc.code})
            raise
        draft.model = model
        draft.revision += 1
        draft.edits += 1
        return draft_view(draft)


def _statement(statement: Any) -> dict[str, Any]:
    return {"sql": statement.sql, "target": statement.target, "transactional": statement.transactional}


def preview(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """The plan from a fresh snapshot to the draft, for the user to read (see the module text). Runs nothing."""
    unlock = _unlock(unlocks, session, doc.get("connection_id"))
    draft = drafts.get(session, unlock.connection_id, unlock.unlock_id)
    with draft.lock:
        version = unlock.connection.engine_version()
        current = _snapshot(unlock.connection, draft.schema)
        try:
            change = plan_for(current, draft.model, version)
        except WriteError as exc:
            logger.info("schema plan refused", extra={"event": "studio.schema_plan_refused", "code": exc.code})
            raise
        steps = [
            {
                "index": step.index,
                "operation": step.operation,
                "target": step.target,
                "risks": [risk.value for risk in step.risks],
                "transactional": step.transactional,
                "statements": [_statement(statement) for statement in step.statements],
            }
            for step in change.steps
        ]
        logger.info("schema plan previewed", extra={"event": "studio.schema_plan_previewed"})
        return {
            "connection_id": draft.connection_id,
            "revision": draft.revision,
            "dialect": change.dialect,
            "server_version": list(version),
            "source_fingerprint": change.source_fingerprint,
            "target_fingerprint": change.target_fingerprint,
            "plan_hash": change.plan_hash,
            "risks": [risk.value for risk in change.risks],
            "transactional": all(step.transactional for step in change.steps),
            # The database is not the one the draft started from: the plan also undoes what changed since.
            "base_changed": fingerprint(current) != fingerprint(draft.base),
            "steps": steps,
            "executable": False,
        }
