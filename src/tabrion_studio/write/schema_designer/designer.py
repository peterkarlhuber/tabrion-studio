"""The Schema Designer's routes for drafts, the plan preview and the confirmed run (STU-CR-4, STU-CR-5;
STU-ADR-0007 sections 2 to 11; TX-21, TX-26, TX-27; GS3-03..05, GS3-09..20, GS3-27..29).

Imported only by :mod:`tabrion_studio.write.routes` when the ``schema_designer`` flag is on (GS3-01). Every call
first re-checks this session's unlock of the connection for the Schema Designer (:meth:`Unlocks.require_unlock`: the
flag, the unlock and its expiry, the coupling, the stored environment, never the request's; PROD is never unlocked,
E-X7; decision 1A refused an over-privileged write role at the unlock), and a draft is used only under the unlock it
was started with (:class:`.draft.Drafts`).

* :func:`start`: a level-2 snapshot of one schema on the **write** connection (``main`` on SQLite; a PostgreSQL
  schema the role can see, not one of PostgreSQL's own) becomes both the draft's starting snapshot and its first
  model; the engine version for every plan is read on the same connection (:meth:`WriteConnection.engine_version`).
  Starting again replaces the draft.
* :func:`edit`: one edit operation (:mod:`.draft`), made on the revision the page names. An edit drops the plan of
  the last preview.
* :func:`preview`: a **fresh** level-2 snapshot on the write connection, then ``tabrion.plan.plan(fresh, draft,
  server_version=<read now on the write connection>)``: every step with its operation, table, risk classes,
  transactional flag and exact SQL, both fingerprints and the plan hash, the warnings of section 8, why the plan may
  not run directly (section 5) and what its confirmation needs (sections 6 and 7). The server keeps that plan with the
  draft (:class:`.execute.RunPlan`), bound to the session, the connection, the unlock and a short expiry.
* :func:`confirm`: the user's confirmation per risk class (:func:`.execute.check_confirmation`).
* :func:`run`: once per confirmation, the recorded plan is checked again (the unlock, the engine version, the
  migration markers, Studio's re-plan from a fresh snapshot with its hash and classes compared: the pre-check of
  section 4), then :func:`.execute.run` makes the backup and calls ``tabrion.plan.execute_plan``, which plans again
  inside its own transaction and is the binding check. A dry run happens only when the request asks for it.

The draft routes write nothing to the database, the project or the disk; a run is audited (section 11) and its backup
is written to Studio's backup folder. The log gets fixed event names and problem codes only: never a table or column
name, a type, a default or check text, a statement, a fingerprint or a hash (TX-27).
"""

from __future__ import annotations

import logging
import secrets
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
    locator,
    plan_for,
    refusal,
    table_names,
    validate,
)
from tabrion_studio.write.unlock import WRITE_ID_PREFIX, Unlock, Unlocks

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
        draft.plan = None  # the previewed plan is of the model before this edit
        return draft_view(draft)


def _statement(statement: Any) -> dict[str, Any]:
    return {"sql": statement.sql, "target": statement.target, "transactional": statement.transactional}


def preview(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """The plan from a fresh snapshot to the draft, for the user to read and, where section 5 allows it, to confirm
    and run (STU-CR-5). The plan is kept with the draft (:class:`.execute.RunPlan`); nothing runs here."""
    from tabrion_studio.write.schema_designer import execute

    unlock = _unlock(unlocks, session, doc.get("connection_id"))
    draft = drafts.get(session, unlock.connection_id, unlock.unlock_id)
    with draft.lock:
        draft.plan = None
        version = unlock.connection.engine_version()
        current = _snapshot(unlock.connection, draft.schema)
        try:
            change = plan_for(current, draft.model, version, locator(draft.model, draft.base, current))
        except WriteError as exc:
            logger.info("schema plan refused", extra={"event": "studio.schema_plan_refused", "code": exc.code})
            raise
        reasons = execute.blocked(unlock, change, drafts.project)
        kept = execute.make_plan(
            unlock,
            schema=draft.schema,
            target=draft.model,
            change=change,
            server_version=version,
            blocked_reasons=reasons,
            now=unlocks.clock(),
        )
        draft.plan = kept
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
            "environment": unlock.environment,
            "write_profile_id": f"{WRITE_ID_PREFIX}{unlock.write_name}",
            "executable": not reasons,
            "blocked": list(reasons),
            "warnings": execute.warnings(current, draft.model, change, draft.schema),
            "confirmation": execute.requirements(kept),
            "expires_in_s": execute.RUN_TTL_SECONDS,
        }


def _run_draft(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> tuple[Unlock, Draft]:
    unlock = _unlock(unlocks, session, doc.get("connection_id"))
    try:
        draft = drafts.get(session, unlock.connection_id, unlock.unlock_id)
    except WriteError:
        raise WriteError("studio.plan_unknown") from None
    return unlock, draft


def confirm(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Confirm the previewed plan (STU-CR-5, STU-ADR-0007 sections 4 item 2, 6, 7): the plan hash, the typed write
    profile name and what each risk class asks. A wrong text may be typed again; a wrong binding or an expired plan
    drops the plan."""
    from tabrion_studio.write.schema_designer import execute

    unlock, draft = _run_draft(unlocks, drafts, session, doc)
    with draft.lock:
        plan = execute._own(draft.plan, doc.get("plan_hash"))
        now = unlocks.clock()
        try:
            execute._check_binding(plan, unlock, now)
        except WriteError:
            draft.plan = None
            raise
        try:
            confirmation = execute.check_confirmation(plan, doc)
        except WriteError as exc:
            logger.info("schema plan not confirmed", extra={"event": "studio.schema_plan_refused", "code": exc.code})
            raise
        draft.plan, answer_doc = execute.confirmed(plan, confirmation, now)
        return answer_doc


def run(unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Run the confirmed plan once, or dry-run it when the request asks (STU-CR-5; see :mod:`.execute`)."""
    from tabrion_studio.write.schema_designer import execute

    dry_run = doc.get("dry_run", False)
    if type(dry_run) is not bool:
        raise WriteError("studio.plan_invalid")
    unlock, draft = _run_draft(unlocks, drafts, session, doc)
    with draft.lock:
        plan = execute._own(draft.plan, doc.get("plan_hash"))
        draft.plan = None  # one attempt: a replay finds nothing
        execute._check_binding(plan, unlock, unlocks.clock())
        if plan.confirmation is None:
            raise WriteError("studio.plan_not_confirmed")
        # Again right before the run (section 5): the unlock and the stored environment as they are now ...
        unlock = _unlock(unlocks, session, unlock.connection_id)
        if not secrets.compare_digest(unlock.unlock_id, plan.unlock_id):
            raise WriteError("studio.plan_binding")
        # ... the migration markers and the project's Alembic setup first, so that a marker added since the
        # preview is named as such rather than as a changed schema ...
        managed = execute.management(unlock.connection, drafts.project)
        if managed:
            raise WriteError("studio.schema_run_not_allowed", [("plan.reason", f"plan.{managed[0]}")])
        # ... Studio's re-plan from a fresh snapshot with the same engine version (the pre-check of section 4) ...
        version = unlock.connection.engine_version()
        if version != plan.server_version:
            raise WriteError("studio.plan_drift")
        current = _snapshot(unlock.connection, plan.schema)
        try:
            change = plan_for(current, plan.target, version, locator(draft.model, draft.base, current))
        except WriteError as exc:
            logger.info("schema run refused", extra={"event": "studio.schema_plan_refused", "code": exc.code})
            raise
        if not secrets.compare_digest(change.plan_hash, plan.plan_hash) or not {
            risk.value for risk in change.risks
        } <= {risk.value for risk in plan.confirmation.classes}:
            logger.warning("schema run refused: the plan changed", extra={"event": "studio.plan_drift"})
            raise WriteError("studio.plan_drift")
        reasons = execute.blocked(unlock, change, drafts.project)
        if reasons:
            raise WriteError("studio.schema_run_not_allowed", [("plan.reason", f"plan.{reasons[0]}")])
        outcome = execute.run(unlocks, unlock, plan, dry_run=dry_run, write_state=drafts.write_state)
        answer_doc = outcome.to_dict()
        if not dry_run:
            # The draft starts again from the database as the run left it: nothing is left to change. If that
            # schema cannot be read, the draft ends (the run has happened either way).
            try:
                draft.base = draft.model = _snapshot(unlock.connection, draft.schema)
                draft.revision += 1
            except (WriteError, DriverFailure):
                drafts.discard(session, unlock.connection_id)
                answer_doc["draft_ended"] = True
        return {**answer_doc, "revision": draft.revision}
