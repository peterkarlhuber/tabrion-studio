"""The Schema Designer's schema comparison (STU-XSD-2; STU-ADR-0007 sections 1 to 3, 8, 9 and 11; TX-28; GS3-09,
GS3-12, GS3-13, GS3-16).

Imported only by :mod:`tabrion_studio.write.routes`, inside the handler of ``POST
/api/write/schema-designer/compare``, after it checked the ``schema_designer`` flag (GS3-01). It **only reads**: no
statement that changes anything, no plan kept, no audit record, nothing written to the disk.

**Two sides.** The request names an ``expected`` side (the reference) and an ``actual`` side (the schema looked at);
each is one of:

* ``{"source": "connection", "connection_id", "schema"}``: a level-2 snapshot of one schema read **now** on an open
  read-only connection (the read profile's connection, TX-03; section 1: reading a snapshot is a read path). Any
  open connection may be read, a PROD one included, because nothing is changed and the connection was opened with its
  PROD confirmation (STU-T-003); ``main`` on SQLite, on PostgreSQL a schema the read role sees that is not one of
  PostgreSQL's own.
* ``{"source": "draft", "connection_id"}``: this session's draft of that connection (:mod:`.draft`), under the unlock
  it was started with (the unlock is checked as for every draft route).
* ``{"source": "file", "text"}``: the text of a snapshot file (``SchemaSnapshot.to_json``, level 2), at most
  :data:`MAX_SNAPSHOT_BYTES` bytes. It is **untrusted data** (section 3, TX-28): read with
  ``SchemaSnapshot.from_json`` and the draft's limits, shown and compared, never the source or the target of a plan.
  At most one side is a file.

**The answer.** tabrion's ``compare(expected=..., actual=...)`` (keyword-only since tabrion 0.3): every difference
with its kind, table, column, constraint or index name and whether it is ``breaking``; both level-2 fingerprints
(with triggers, policies, row security and the functions they call directly, D-5.1); and the feature markers of each
side (D-5.2, D-5.3: partitions, inheritance, rules, domain columns, event triggers, the SQLite table options ...),
which the shape does not describe. The **verdict** fails closed: ``same`` only when there is no difference, the
fingerprints are equal and neither side has a marker; ``inconclusive`` when nothing differs but a marker means the
snapshot cannot see everything (a domain's definition, a partition's bounds); ``different`` otherwise (also when only
the fingerprints differ). A marker Studio does not know is shown as ``unknown`` and also makes the verdict
inconclusive at best.

**The plan preview** (only when ``"plan": true``): ``tabrion.plan.plan(current=<actual>, target=<expected>,
server_version=<read now on the actual connection>)`` (GS3-13), shown with its steps, risk classes, SQL, fingerprints
and the fixed warnings of section 8, and **never kept or run**: it has no plan hash, no route confirms or runs it,
and the designer's own preview is left as it was. It is made only when the actual side is a connection of one of the
user's own profiles whose environment is DEV or TEST, and the expected side is a connection or the draft, never a
file; otherwise its part of the answer is a refusal (``studio.schema_compare_plan_not_allowed``: ``plan_file``,
``plan_source``, ``plan_prod``, ``plan_environment``), and a refusal of tabrion is its catalog problem
(:func:`.draft.refusal`), while the comparison itself is still answered.

**Problems** are fixed catalog entries (section 2): ``studio.schema_compare_invalid`` (``compare.<field>`` issues),
``studio.schema_compare_schema``, ``studio.schema_snapshot_invalid``, ``studio.schema_snapshot_too_large`` and
``studio.schema_compare_mismatch``; a driver error is ``db.<kind>``. Names, schema texts and SQL go to this page only;
the log gets the event name (section 11, TX-27).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
from typing import Any, TypeVar

import sqlalchemy
from tabrion.errors import TabrionError, UsageError, classify, safe_message
from tabrion.schema import SchemaSnapshot, TableShape, compare, fingerprint, snapshot

from tabrion_studio.config.write_areas import Area
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection, RegistryError
from tabrion_studio.write.connection import DriverFailure, WriteError
from tabrion_studio.write.schema_designer.draft import (
    MAX_DRAFT_BYTES,
    Drafts,
    check_limits,
    check_schema,
    plan_for,
    refusal,
    table_names,
    warnings,
)
from tabrion_studio.write.unlock import Unlocks

logger = logging.getLogger("tabrion_studio.write")

_T = TypeVar("_T")

#: The most UTF-8 bytes of a snapshot file's text: the size limit of a draft's canonical JSON.
MAX_SNAPSHOT_BYTES = MAX_DRAFT_BYTES
SOURCES = ("connection", "draft", "file")
#: The keys each kind of side takes; any other key is refused (``compare.unknown_key``).
_SIDE_KEYS: dict[str, frozenset[str]] = {
    "connection": frozenset({"source", "connection_id", "schema"}),
    "draft": frozenset({"source", "connection_id"}),
    "file": frozenset({"source", "text"}),
}
#: The environments of an actual side a plan preview is made for: those a plan may run in (section 5).
PLAN_ENVIRONMENTS = frozenset({"dev", "test"})
#: The feature marker kinds of tabrion 0.3 (``TableShape.features``, ``SchemaSnapshot.features``; the part before
#: ``:``). Any other kind is shown as ``unknown`` and fails closed.
KNOWN_FEATURES = frozenset(
    {
        # PostgreSQL (ADR 0015 D-5.2, D-5.3)
        "partitioned",
        "partition",
        "inheritance",
        "foreign_table",
        "unlogged",
        "temporary",
        "typed_table",
        "storage_parameters",
        "replica_identity",
        "access_method",
        "rule",
        "domain",
        "no_inherit",
        "relkind",
        "persistence",
        "event_trigger",
        # SQLite (contract I-111 i)
        "virtual_table",
        "strict",
        "without_rowid",
        "autoincrement",
        "on_conflict",
        "collation",
        "deferrable_reference",
        "unparsed_table_sql",
    }
)
_SERVER_VERSION = "SELECT pg_catalog.current_setting('server_version_num')"
_DIGITS = re.compile(r"[0-9]{1,9}")


def _invalid(field_name: str, code: str) -> WriteError:
    return WriteError("studio.schema_compare_invalid", [(f"compare.{field_name}", f"compare.{code}")])


@dataclass(frozen=True)
class Side:
    """One side of a comparison. ``connection`` is set for a connection side only (the plan preview reads the engine
    version on it); ``trusted`` is False for a file: such a side never reaches :func:`plan_for`."""

    source: str
    snapshot: SchemaSnapshot
    trusted: bool
    schema: str | None = None
    environment: str | None = None
    own_profile: bool = False
    connection: OpenConnection | None = None


# --- reading a side --------------------------------------------------------------------------------------------------


def _reading(call: Callable[[], _T]) -> _T:
    """``call()`` with a driver error as a ``db.<kind>`` problem (:class:`DriverFailure`), logged with
    ``safe_message`` only; a usage error of tabrion is ``studio.schema_model_invalid`` with its code
    (:func:`.draft.refusal`)."""
    try:
        return call()
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


def _too_large(name: str) -> WriteError:
    return WriteError("studio.schema_snapshot_too_large", [(f"compare.{name}", "compare.too_large")])


def _within_limits(found: SchemaSnapshot, name: str) -> SchemaSnapshot:
    try:
        check_limits(found)
    except WriteError:
        raise _too_large(name) from None
    return found


def _connection_snapshot(connection: OpenConnection, schema: str, name: str) -> SchemaSnapshot:
    """The level-2 snapshot of ``schema``, read now on the read-only connection, within the draft's table limit."""

    def read(inspector: sqlalchemy.Inspector) -> SchemaSnapshot:
        if connection.engine != "sqlite" and schema not in inspector.get_schema_names():
            raise WriteError("studio.schema_compare_schema")
        try:
            table_names(inspector.get_table_names(schema=None if connection.engine == "sqlite" else schema))
        except WriteError:
            raise _too_large(name) from None
        return snapshot(inspector, schemas=[schema], level=2)

    return _within_limits(_reading(lambda: connection.inspect(read)), name)


def _schema(raw: object, engine: str) -> str:
    try:
        return check_schema(raw, engine)
    except WriteError:
        raise WriteError("studio.schema_compare_schema") from None


def _load_file(raw: object, name: str) -> SchemaSnapshot:
    """A snapshot file's text (section 3, TX-28): untrusted, read with tabrion's own parser and the draft's limits,
    level 2 only. The result is compared and shown, never planned."""
    if raw is None or raw == "":
        raise _invalid(f"{name}.text", "required")
    if not isinstance(raw, str):
        raise _invalid(f"{name}.text", "type")
    if len(raw) > MAX_SNAPSHOT_BYTES or len(raw.encode("utf-8", "surrogatepass")) > MAX_SNAPSHOT_BYTES:
        raise _too_large(name)
    try:
        found = SchemaSnapshot.from_json(raw)
    except (UsageError, RecursionError, ValueError):
        raise WriteError("studio.schema_snapshot_invalid", [(f"compare.{name}", "compare.format")]) from None
    if found.level != 2:
        raise WriteError("studio.schema_snapshot_invalid", [(f"compare.{name}", "compare.level")])
    return _within_limits(found, name)


def _side(raw: object, name: str, registry: ConnectionRegistry, unlocks: Unlocks, drafts: Drafts, session: str) -> Side:
    if raw is None:
        raise _invalid(name, "required")
    if not isinstance(raw, dict):
        raise _invalid(name, "type")
    source = raw.get("source")
    if not isinstance(source, str) or source not in SOURCES:
        raise _invalid(f"{name}.source", "choice")
    if set(raw) - _SIDE_KEYS[source]:
        raise _invalid(name, "unknown_key")
    if source == "file":
        return Side("file", _load_file(raw.get("text"), name), trusted=False)
    connection_id = raw.get("connection_id")
    if not isinstance(connection_id, str) or not connection_id:
        raise _invalid(f"{name}.connection_id", "required")
    if source == "draft":
        unlock = unlocks.require_unlock(session, connection_id, Area.SCHEMA_DESIGNER)
        draft = drafts.get(session, unlock.connection_id, unlock.unlock_id)
        with draft.lock:
            model = draft.model
        return Side("draft", model, trusted=True, schema=draft.schema, environment=unlock.environment)
    try:
        connection = registry.get(connection_id)
    except RegistryError:
        # W-9: a refused write route answers 400 or 405, never 404; a stale connection was closed by the registry.
        raise WriteError("studio.write_target_unknown") from None
    schema = _schema(raw.get("schema"), connection.engine)
    return Side(
        "connection",
        _connection_snapshot(connection, schema, name),
        trusted=True,
        schema=schema,
        environment=connection.environment,
        own_profile=connection.profile_id.startswith("user:"),
        connection=connection,
    )


# --- what the page sees ----------------------------------------------------------------------------------------------


def _marker(feature: str) -> dict[str, Any]:
    kind, _, detail = feature.partition(":")
    known = kind in KNOWN_FEATURES
    return {"kind": kind if known else "unknown", "name": detail or None, "known": known, "marker": feature}


def _flagged(table: TableShape) -> dict[str, Any]:
    return {"table": table.name, "features": [_marker(feature) for feature in table.features]}


def _summary(side: Side) -> dict[str, Any]:
    found = side.snapshot
    return {
        "source": side.source,
        "engine": found.dialect,
        "schema": side.schema,
        "environment": side.environment,
        "fingerprint": fingerprint(found),
        "tables": len(found.tables),
        "views": len(found.views),
        "triggers": sum(len(table.triggers) for table in found.tables),
        "policies": sum(len(table.policies) for table in found.tables),
        "row_security": sum(1 for table in found.tables if table.row_security),
        "features": [_marker(feature) for feature in found.features],
        "flagged": [_flagged(table) for table in found.tables if table.features],
    }


def _engine_version(connection: OpenConnection) -> tuple[int, ...]:
    """The engine version the preview is planned for (section 9, GS3-13), read now on the actual connection: on
    SQLite ``sqlite_version_info`` of the library the connection's engine loaded, on PostgreSQL the server's
    ``server_version_num``. Never None: an unreadable version refuses (``studio.write_unsupported``)."""
    if connection.engine == "sqlite":

        def library(found: sqlalchemy.Inspector) -> object:
            return getattr(found.dialect.dbapi, "sqlite_version_info", None)

        info = _reading(lambda: connection.inspect(library))
        if not isinstance(info, tuple) or not info or not all(type(p) is int and p >= 0 for p in info):
            raise WriteError("studio.write_unsupported")
        return tuple(info)
    rows = _reading(lambda: connection.fetch(_SERVER_VERSION))
    text = str(rows[0][0]) if rows and rows[0] else ""
    number = int(text) if _DIGITS.fullmatch(text) else 0
    if number <= 0:
        raise WriteError("studio.write_unsupported")
    if number >= 100000:
        return (number // 10000, number % 10000)
    return (number // 10000, number // 100 % 100, number % 100)


def _not_allowed(expected: Side, actual: Side) -> str | None:
    """Why no plan preview is made for these sides (TX-28, section 5), or None."""
    if not expected.trusted or not actual.trusted:
        return "plan_file"
    if actual.source != "connection" or actual.connection is None:
        return "plan_source"
    if actual.environment == "prod":
        return "plan_prod"
    if actual.environment not in PLAN_ENVIRONMENTS or not actual.own_profile:
        return "plan_environment"
    return None


def _refused(exc: WriteError) -> dict[str, Any]:
    return {
        "status": "refused",
        "problem": {
            "code": exc.code,
            "issues": [{"pointer": pointer, "code": code} for pointer, code in exc.issues or []],
        },
    }


def _preview(expected: Side, actual: Side) -> dict[str, Any]:
    """The read-only plan from the actual side to the expected one: shown, never kept or run."""
    reason = _not_allowed(expected, actual)
    if reason is not None or actual.connection is None or actual.schema is None:
        code = f"compare.{reason or 'plan_source'}"
        return _refused(WriteError("studio.schema_compare_plan_not_allowed", [("compare.plan", code)]))
    version = _engine_version(actual.connection)
    try:
        change = plan_for(actual.snapshot, expected.snapshot, version)
    except WriteError as exc:
        logger.info("schema comparison plan refused", extra={"event": "studio.schema_plan_refused", "code": exc.code})
        return _refused(exc)
    return {
        "status": "ready",
        "dialect": change.dialect,
        "server_version": list(version),
        "source_fingerprint": change.source_fingerprint,
        "target_fingerprint": change.target_fingerprint,
        "risks": [risk.value for risk in change.risks],
        "transactional": all(step.transactional for step in change.steps),
        "steps": [
            {
                "index": step.index,
                "operation": step.operation,
                "target": step.target,
                "risks": [risk.value for risk in step.risks],
                "transactional": step.transactional,
                "statements": [
                    {"sql": s.sql, "target": s.target, "transactional": s.transactional} for s in step.statements
                ],
            }
            for step in change.steps
        ],
        "warnings": warnings(actual.snapshot, expected.snapshot, change, actual.schema),
    }


def compare_schemas(
    registry: ConnectionRegistry, unlocks: Unlocks, drafts: Drafts, session: str, doc: dict[str, Any]
) -> dict[str, Any]:
    """Compare the two sides of ``doc`` (see the module text) and, on request, preview the plan between them."""
    wants_plan = doc.get("plan", False)
    if type(wants_plan) is not bool:
        raise _invalid("plan", "type")
    raw_expected, raw_actual = doc.get("expected"), doc.get("actual")
    sources = [raw.get("source") if isinstance(raw, dict) else None for raw in (raw_expected, raw_actual)]
    if sources == ["file", "file"]:
        raise _invalid("actual.source", "two_files")
    expected = _side(raw_expected, "expected", registry, unlocks, drafts, session)
    actual = _side(raw_actual, "actual", registry, unlocks, drafts, session)
    if expected.snapshot.dialect != actual.snapshot.dialect:
        raise WriteError("studio.schema_compare_mismatch", [("compare.actual", "compare.dialect")])
    try:
        differences = compare(expected=expected.snapshot, actual=actual.snapshot)
    except UsageError:
        raise WriteError("studio.schema_compare_mismatch", [("compare.actual", "compare.level")]) from None
    left, right = _summary(expected), _summary(actual)
    markers = [*left["features"], *right["features"]]
    markers += [m for side in (left, right) for table in side["flagged"] for m in table["features"]]
    same_fingerprint = left["fingerprint"] == right["fingerprint"]
    if differences or not same_fingerprint:
        verdict = "different"
    elif markers:
        verdict = "inconclusive"
    else:
        verdict = "same"
    answer: dict[str, Any] = {
        "expected": left,
        "actual": right,
        "verdict": verdict,
        "same_fingerprint": same_fingerprint,
        "fingerprint_only": not differences and not same_fingerprint,
        "flagged": bool(markers),
        "unknown_features": any(not marker["known"] for marker in markers),
        "differences": [
            {
                "kind": d.kind,
                "table": d.table,
                "column": d.column,
                "name": d.name,
                "breaking": d.breaking,
            }
            for d in differences
        ],
        "breaking": sum(1 for d in differences if d.breaking),
        "plan": _preview(expected, actual) if wants_plan else None,
    }
    logger.info("schemas compared", extra={"event": "studio.schema_compared"})
    return answer
