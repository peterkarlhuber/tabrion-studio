"""The plan, preview and confirmation framework of every write (STU-XW-3; D-09..D-11; STU-ADR-0006, threats TX-06,
TX-08, TX-09, TX-14, TX-16; review N-2 of PR #109).

Part of the write package: not imported while every write area is off (TX-02), never imported by a read path.

**The flow.** A change runs only as a server-side plan the user saw and confirmed:

1. **Create** (``POST /api/write/plans``): an executor (:class:`PlanExecutor`, chosen by name) turns the request
   into targets and steps (:class:`PlanContent`). Studio reads the targets' catalog on the unlock's write connection,
   computes their **fingerprint** (:func:`read_catalog`) and keeps the plan in this process. The answer is the
   **preview**: the plan hash, the targets with the **triggers and rules** on them (TX-16; review I-1 of tabrion
   PR #78: the row limits cover only the statement itself, never what a trigger or rule does), a summary of the
   steps (an action, a target, whether it is destructive, the number of rows it means to change; never a value),
   the risk notes and the text the user must type to confirm.
2. **Confirm** (``POST /api/write/plans/confirm``): the plan hash and the typed confirmation (D-09; D-10: for a
   destructive plan the name of its first destructive target, otherwise the write profile's name).
3. **Execute** (``POST /api/write/plans/execute``): the plan hash again. Studio takes the plan out of the store
   (valid for **one** attempt), checks the binding, the expiry, the confirmation and that the stored plan still
   hashes to its hash, then runs it through :meth:`Unlocks.audited_transaction` with the plan hash and the unlock ID:
   the unlock is re-checked (flag, expiry, PROD, profiles, SQLite file identity), the ``run.start`` record is
   written, the transaction begins, and **inside it, immediately before the first step,** the target's catalog is
   read again and compared with the fingerprint of the preview (**drift**; on PostgreSQL once more after the targets
   are locked in ``ROW EXCLUSIVE`` mode, so no schema, trigger or rule change can slip in before the steps run; a
   SQLite write transaction is ``BEGIN IMMEDIATE`` already). A difference aborts the run and rolls back
   (``studio.plan_drift``); the ``run.result`` record names the refusal.

**Binding (TX-08, N-2).** A plan is bound to the session (the digest the access middleware checked), the read
connection's ID and the unlock's own ID (:attr:`Unlock.unlock_id`), and its hash covers all three, a random nonce,
the executor, the targets, the steps and the fingerprint. Another session sees no such plan
(``studio.plan_unknown``); another connection or an earlier unlock is ``studio.plan_binding``; after
:data:`PLAN_TTL_SECONDS` it is ``studio.plan_expired``; a plan whose stored content no longer hashes to its hash is
``studio.plan_changed``; a second execute of the same hash is ``studio.plan_unknown``.

**Fingerprint.** SQLite: the file's identity (device and inode, or the Windows file ID) and every row of the file's
schema table (``type``, ``name``, ``tbl_name``, ``sql``), so any schema change of the file, and a file swapped for
one with the same schema, is drift. PostgreSQL, per target table: its oid and kind, row security, the columns (type,
nullability, default, identity, generated), its constraints and the foreign keys that reference it, its indexes,
triggers (``pg_get_triggerdef``), rules (``pg_get_ruledef``) and policies. Only the SHA-256 of it is kept; the
catalog texts (which may hold literals of a default or a ``WHEN`` clause) are never sent, logged or recorded.

**Executors.** Studio builds no write statement of its own (plans come from Tabrion). An executor validates its
request (:meth:`PlanExecutor.build`) and runs one step at a time in the given transaction
(:meth:`PlanExecutor.run_step`), schema-qualifying every name and counting rows with :meth:`AuditedRun.count`. The
one Studio registers is the Data Editor's (STU-026, :mod:`tabrion_studio.write.data_editor`, on Tabrion's guided
write transaction TAB-XW-6), and only while the Data Editor flag is on; with no executor, every plan request is
``studio.plan_executor_unknown``.

**Primary keys (STU-026).** The catalog read gives each target's columns and primary key
(:class:`TargetCatalog`). An executor whose ``requires_primary_key`` is True has every target without a primary key
refused (``studio.plan_target_no_primary_key``): a row is written by its primary key only, never by ``rowid`` or
``ctid``. An executor may also check its plan against the catalog (``check``, :class:`CatalogCheck`) before the plan
is kept; the primary key and the columns are part of the fingerprint, so the run checks them again (drift).

**Refusals** are catalog problems, 400 or 405 (W-9): they name no value, no statement and nothing of the request.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Protocol

import sqlalchemy
import tabrion

from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.connection import WriteConnection, WriteError, file_identity
from tabrion_studio.write.unlock import WRITE_ID_PREFIX, AuditedRun, Unlocks

__all__ = [
    "MAX_TARGETS",
    "PLAN_TTL_SECONDS",
    "CatalogCheck",
    "Plan",
    "PlanContent",
    "PlanExecutor",
    "Plans",
    "Rule",
    "Step",
    "Target",
    "TargetCatalog",
    "Trigger",
    "read_catalog",
]

logger = logging.getLogger("tabrion_studio.write.plan")

PLAN_VERSION = 1
#: A plan expires this long after its preview (shorter than an unlock's 15 minutes).
PLAN_TTL_SECONDS = 5 * 60
#: Limits of one plan (the audit log takes at most 64 object names, review N-3 of PR #114).
MAX_TARGETS = 16
MAX_STEPS = 1000
MAX_REQUEST_BYTES = 256 * 1024
MAX_DEPTH = 16
MAX_NAME = 128
MAX_RISKS = 16
#: Open plans per session and in all (older ones are dropped first).
MAX_PLANS_PER_SESSION = 16
MAX_PLANS = 256
#: An executor's name, a step's action and a risk note: lower-case dotted tokens (also audit tokens).
_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,31}(?:\.[a-z][a-z0-9_]{0,31}){0,3}")
_HASH = re.compile(r"[0-9a-f]{64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

#: The risk notes the framework adds itself (the page has a text for each).
RISK_DESTRUCTIVE = "plan.destructive"
RISK_TRIGGERS = "plan.triggers"
RISK_RULES = "plan.rules"
RISK_ROW_SECURITY = "plan.row_security"


# --- what an executor builds -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A table a plan changes: ``schema`` (SQLite: ``main``) and ``table``, as the database spells them."""

    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True)
class Step:
    """One step of a plan. ``action`` (a token), the index of its ``target``, whether it is ``destructive`` and the
    number of ``rows`` it is meant to change (None: not known) are shown in the preview. ``data`` is the executor's
    own (canonical JSON): it is part of the plan hash and never sent, logged or recorded."""

    action: str
    target: int
    destructive: bool = False
    rows: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanContent:
    """What :meth:`PlanExecutor.build` returns: the targets, the steps in order and the executor's risk notes."""

    targets: tuple[Target, ...]
    steps: tuple[Step, ...]
    risks: tuple[str, ...] = ()


class PlanExecutor(Protocol):
    """A kind of plan (pluggable; STU-026 registers the Data Editor's while its flag is on).

    ``name`` is a token (it is the audit record's ``action``); ``area`` is the area whose flag the plan needs. An
    executor may also have ``requires_primary_key = True`` (every target needs a primary key) and a ``check``
    (:class:`CatalogCheck`); see the module text."""

    name: str
    area: Area

    def build(self, request: Mapping[str, Any], engine: str) -> PlanContent:
        """Validate ``request`` (canonical JSON, at most :data:`MAX_REQUEST_BYTES`) for ``engine`` and return the plan.

        Raise :class:`WriteError` ``studio.plan_invalid`` for a request it does not take (no value in any message)."""
        ...

    def run_step(self, transaction: tabrion.Transaction, plan: Plan, step: Step, run: AuditedRun) -> None:
        """Run ``step`` in ``transaction`` (schema-qualified names, bound values) and count its rows with
        :meth:`AuditedRun.count`. Raise to roll back the whole plan."""
        ...


# --- the catalog of the targets: fingerprint and the preview's triggers and rules -----------------------------------


@dataclass(frozen=True)
class Trigger:
    """A trigger on a target (TX-16). ``timing`` and ``events`` are known on PostgreSQL only (None and ())."""

    name: str
    timing: str | None = None
    events: tuple[str, ...] = ()
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "timing": self.timing, "events": list(self.events), "enabled": self.enabled}


@dataclass(frozen=True)
class Rule:
    """A rule on a target (PostgreSQL): its event and whether it replaces the statement (``DO INSTEAD``)."""

    name: str
    event: str
    instead: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "event": self.event, "instead": self.instead}


@dataclass(frozen=True)
class TargetCatalog:
    """What the preview shows of one target's catalog, and its columns and primary key (STU-026; in table order and
    in key order, as the database spells them; the preview does not send them)."""

    target: Target
    triggers: tuple[Trigger, ...] = ()
    rules: tuple[Rule, ...] = ()
    row_security: bool = False
    columns: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()
    #: The columns whose type has no equality for an optimistic comparison (PostgreSQL; see ``_PG_INCOMPARABLE``).
    incomparable: tuple[str, ...] = ()


class CatalogCheck(Protocol):
    """The optional hook of an executor: check the plan against the targets' catalog before it is kept (STU-026).

    Raise :class:`WriteError` (no value in any message) for a plan the catalog does not allow."""

    def __call__(self, content: PlanContent, catalogs: tuple[TargetCatalog, ...]) -> None: ...


Fetch = Callable[[str, dict[str, Any] | None], list[tuple[Any, ...]]]

_SQLITE_SCHEMA = "SELECT type, name, tbl_name, sql FROM main.sqlite_master ORDER BY type, name"
#: The columns of one table (a generated column has ``hidden`` 2 or 3; 1 is a hidden column of a virtual table) and
#: their place in the primary key (0: not in it). Covered by the fingerprint: the schema rows hold the table's SQL.
_SQLITE_COLUMNS = "SELECT name, pk, hidden FROM pragma_table_xinfo(:table, 'main') ORDER BY cid"

_PG_CLASS = (
    "SELECT c.oid::pg_catalog.int8, c.relkind::pg_catalog.text, c.relrowsecurity, c.relforcerowsecurity,"
    " c.relispartition FROM pg_catalog.pg_class c"
    " JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"
    " WHERE n.nspname OPERATOR(pg_catalog.=) CAST(:schema AS pg_catalog.name)"
    " AND c.relname OPERATOR(pg_catalog.=) CAST(:table AS pg_catalog.name)"
)
_PG_PARTS: tuple[tuple[str, str], ...] = (
    (
        "columns",
        "SELECT a.attnum::pg_catalog.int4, a.attname::pg_catalog.text,"
        " pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull, a.attidentity::pg_catalog.text,"
        " a.attgenerated::pg_catalog.text, pg_catalog.pg_get_expr(d.adbin, d.adrelid)"
        " FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d"
        " ON d.adrelid OPERATOR(pg_catalog.=) a.attrelid AND d.adnum OPERATOR(pg_catalog.=) a.attnum"
        " WHERE a.attrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) AND a.attnum OPERATOR(pg_catalog.>) 0"
        " AND NOT a.attisdropped ORDER BY a.attnum",
    ),
    (
        "constraints",
        "SELECT c.conname::pg_catalog.text, c.contype::pg_catalog.text, pg_catalog.pg_get_constraintdef(c.oid)"
        " FROM pg_catalog.pg_constraint c"
        " WHERE c.conrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) ORDER BY 1, 3",
    ),
    (
        "referenced_by",
        "SELECT c.conrelid::pg_catalog.int8, c.conname::pg_catalog.text, pg_catalog.pg_get_constraintdef(c.oid)"
        " FROM pg_catalog.pg_constraint c"
        " WHERE c.confrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) ORDER BY 1, 2, 3",
    ),
    (
        "indexes",
        "SELECT pg_catalog.pg_get_indexdef(i.indexrelid) FROM pg_catalog.pg_index i"
        " WHERE i.indrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) ORDER BY 1",
    ),
    (
        "triggers",
        "SELECT t.tgname::pg_catalog.text, t.tgtype::pg_catalog.int4, t.tgenabled::pg_catalog.text, t.tgisinternal,"
        " pg_catalog.pg_get_triggerdef(t.oid)"
        " FROM pg_catalog.pg_trigger t WHERE t.tgrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) ORDER BY 1",
    ),
    (
        "rules",
        "SELECT r.rulename::pg_catalog.text, r.ev_type::pg_catalog.text, r.is_instead,"
        " r.ev_enabled::pg_catalog.text, pg_catalog.pg_get_ruledef(r.oid)"
        " FROM pg_catalog.pg_rewrite r WHERE r.ev_class OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) ORDER BY 1",
    ),
    (
        "policies",
        "SELECT p.polname::pg_catalog.text, p.polcmd::pg_catalog.text, p.polpermissive,"
        " pg_catalog.pg_get_expr(p.polqual, p.polrelid), pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid)"
        " FROM pg_catalog.pg_policy p WHERE p.polrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) ORDER BY 1",
    ),
    (
        # Review M-1 of PR #115: the code the target runs, not only its name. Every function a trigger of the target
        # calls (tgfoid) and every function a column default, a CHECK or other constraint, a policy, a rule or an
        # index expression of the target depends on (pg_depend): its definition, settings, owner and security mode.
        # Hashed only; never sent, logged or recorded.
        "functions",
        "SELECT p.oid::pg_catalog.int8, p.proname::pg_catalog.text, p.pronamespace::pg_catalog.int8,"
        " p.prolang::pg_catalog.int8, p.prosrc, p.probin, p.proconfig::pg_catalog.text, p.prosecdef,"
        " p.proowner::pg_catalog.int8, p.provolatile::pg_catalog.text, p.proisstrict, p.proleakproof,"
        " p.prokind::pg_catalog.text, pg_catalog.pg_get_function_arguments(p.oid),"
        " pg_catalog.pg_get_function_result(p.oid)"
        " FROM pg_catalog.pg_proc p WHERE p.oid OPERATOR(pg_catalog.=) ANY (ARRAY("
        " SELECT t.tgfoid FROM pg_catalog.pg_trigger t"
        " WHERE t.tgrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid)"
        " UNION SELECT d.refobjid FROM pg_catalog.pg_depend d"
        " WHERE d.refclassid OPERATOR(pg_catalog.=) CAST('pg_catalog.pg_proc' AS pg_catalog.regclass) AND ("
        " (d.classid OPERATOR(pg_catalog.=) CAST('pg_catalog.pg_attrdef' AS pg_catalog.regclass)"
        " AND d.objid OPERATOR(pg_catalog.=) ANY (ARRAY(SELECT a.oid FROM pg_catalog.pg_attrdef a"
        " WHERE a.adrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid))))"
        " OR (d.classid OPERATOR(pg_catalog.=) CAST('pg_catalog.pg_constraint' AS pg_catalog.regclass)"
        " AND d.objid OPERATOR(pg_catalog.=) ANY (ARRAY(SELECT c.oid FROM pg_catalog.pg_constraint c"
        " WHERE c.conrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid))))"
        " OR (d.classid OPERATOR(pg_catalog.=) CAST('pg_catalog.pg_policy' AS pg_catalog.regclass)"
        " AND d.objid OPERATOR(pg_catalog.=) ANY (ARRAY(SELECT o.oid FROM pg_catalog.pg_policy o"
        " WHERE o.polrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid))))"
        " OR (d.classid OPERATOR(pg_catalog.=) CAST('pg_catalog.pg_rewrite' AS pg_catalog.regclass)"
        " AND d.objid OPERATOR(pg_catalog.=) ANY (ARRAY(SELECT r.oid FROM pg_catalog.pg_rewrite r"
        " WHERE r.ev_class OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid))))"
        " OR (d.classid OPERATOR(pg_catalog.=) CAST('pg_catalog.pg_class' AS pg_catalog.regclass)"
        " AND d.objid OPERATOR(pg_catalog.=) ANY (ARRAY(SELECT i.indexrelid FROM pg_catalog.pg_index i"
        " WHERE i.indrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid))))"
        " ))) ORDER BY 1",
    ),
)
#: The primary key's columns in key order (covered by the fingerprint's constraints and columns).
_PG_PRIMARY_KEY = (
    "SELECT a.attname::pg_catalog.text FROM pg_catalog.pg_index i"
    " CROSS JOIN LATERAL pg_catalog.unnest(i.indkey::pg_catalog.int2[]) WITH ORDINALITY AS k(attnum, n)"
    " JOIN pg_catalog.pg_attribute a ON a.attrelid OPERATOR(pg_catalog.=) i.indrelid"
    " AND a.attnum OPERATOR(pg_catalog.=) k.attnum"
    " WHERE i.indrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid) AND i.indisprimary ORDER BY k.n"
)
#: The columns whose type has no proper equality (STU-026, review I-1 of PR #119): the type, after unwrapping domains
#: (and an array's element type), has no default btree or hash operator class, directly or through an implicit
#: binary-coercible cast (``varchar`` to ``text``; ``xml`` to ``text`` is explicit only). Enums, ranges and
#: multiranges always have one; ``json`` is compared as ``jsonb`` by ``tabrion.rowedit``.
#: Examples: ``xml``, ``point``, ``box`` (whose ``=`` compares areas), composite types, ``json[]``. A row with such a
#: column cannot be matched by its old values, so the Data Editor refuses the table.
_PG_INCOMPARABLE = (
    "WITH RECURSIVE base(attname, attnum, typ, depth) AS ("
    " SELECT a.attname, a.attnum, a.atttypid, 0 FROM pg_catalog.pg_attribute a"
    " WHERE a.attrelid OPERATOR(pg_catalog.=) CAST(:oid AS pg_catalog.oid)"
    " AND a.attnum OPERATOR(pg_catalog.>) 0 AND NOT a.attisdropped"
    " UNION ALL SELECT b.attname, b.attnum, t.typbasetype, b.depth OPERATOR(pg_catalog.+) 1"
    " FROM base b JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) b.typ"
    " WHERE t.typtype OPERATOR(pg_catalog.=) 'd' AND b.depth OPERATOR(pg_catalog.<) 32),"
    " solved AS (SELECT b.attname, b.attnum, b.typ FROM base b"
    " JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) b.typ WHERE t.typtype OPERATOR(pg_catalog.<>) 'd'),"
    " compared AS (SELECT s.attname, s.attnum,"
    " t.typcategory OPERATOR(pg_catalog.=) 'A' AND t.typelem OPERATOR(pg_catalog.<>) 0 AS is_array,"
    " CASE WHEN t.typcategory OPERATOR(pg_catalog.=) 'A' AND t.typelem OPERATOR(pg_catalog.<>) 0"
    " THEN CASE WHEN e.typtype OPERATOR(pg_catalog.=) 'd' THEN e.typbasetype ELSE e.oid END ELSE s.typ END AS typ"
    " FROM solved s JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) s.typ"
    " LEFT JOIN pg_catalog.pg_type e ON e.oid OPERATOR(pg_catalog.=) t.typelem)"
    " SELECT c.attname::pg_catalog.text FROM compared c"
    " JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) c.typ"
    " WHERE NOT (t.typtype OPERATOR(pg_catalog.=) ANY (ARRAY['e', 'r', 'm']::pg_catalog.\"char\"[])"
    " OR (NOT c.is_array AND t.oid OPERATOR(pg_catalog.=) CAST('pg_catalog.json' AS pg_catalog.regtype))"
    " OR EXISTS (SELECT 1 FROM pg_catalog.pg_opclass o JOIN pg_catalog.pg_am m"
    " ON m.oid OPERATOR(pg_catalog.=) o.opcmethod WHERE o.opcdefault"
    " AND m.amname OPERATOR(pg_catalog.=) ANY (ARRAY['btree', 'hash']::pg_catalog.name[])"
    " AND (o.opcintype OPERATOR(pg_catalog.=) t.oid OR EXISTS (SELECT 1 FROM pg_catalog.pg_cast k"
    " WHERE k.castsource OPERATOR(pg_catalog.=) t.oid AND k.casttarget OPERATOR(pg_catalog.=) o.opcintype"
    " AND k.castmethod OPERATOR(pg_catalog.=) 'b' AND k.castcontext OPERATOR(pg_catalog.=) 'i'))))"
    " ORDER BY c.attnum"
)
#: ``pg_rewrite.ev_type``.
_RULE_EVENTS = {"1": "select", "2": "update", "3": "insert", "4": "delete"}
#: ``pg_trigger.tgtype`` bits (``TRIGGER_TYPE_*`` of PostgreSQL's ``pg_trigger.h``).
_TRIGGER_BEFORE = 1 << 1
_TRIGGER_INSTEAD = 1 << 6
_TRIGGER_EVENTS = ((1 << 2, "insert"), (1 << 3, "delete"), (1 << 4, "update"), (1 << 5, "truncate"))


def _quote(name: str) -> str:
    """A PostgreSQL quoted identifier (every name quoted; a target name has no control character, NUL included)."""
    return '"' + name.replace('"', '""') + '"'


def _canonical(doc: object) -> bytes:
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str).encode("ascii")


def _digest(doc: object) -> str:
    return hashlib.sha256(_canonical(doc)).hexdigest()


def _sqlite_catalog(
    fetch: Fetch, targets: tuple[Target, ...], database_file: Path | None
) -> tuple[str, tuple[TargetCatalog, ...]]:
    if database_file is None:
        raise WriteError("studio.write_target_changed")
    identity = file_identity(database_file)  # TX-16: the file at the path, not through a link
    rows = [tuple(str(cell) if cell is not None else None for cell in row) for row in fetch(_SQLITE_SCHEMA, None)]
    catalogs = []
    for target in targets:
        table = target.table.lower()
        if not any(row[0] == "table" and (row[1] or "").lower() == table for row in rows):
            raise WriteError("studio.plan_target_invalid")
        triggers = tuple(
            Trigger(name=row[1] or "")
            for row in rows
            if row[0] == "trigger" and (row[2] or "").lower() == table and row[1]
        )
        described = [(str(name), int(pk)) for name, pk, hidden in fetch(_SQLITE_COLUMNS, {"table": target.table})
                     if hidden != 1]  # fmt: skip
        columns = tuple(name for name, _pk in described)
        primary_key = tuple(name for name, pk in sorted((d for d in described if d[1] > 0), key=lambda d: d[1]))
        catalogs.append(TargetCatalog(target, triggers=triggers, columns=columns, primary_key=primary_key))
    return _digest({"engine": "sqlite", "identity": list(identity), "schema": rows}), tuple(catalogs)


def _trigger(tgtype: int, enabled: str, name: str) -> Trigger:
    if tgtype & _TRIGGER_INSTEAD:
        timing = "instead_of"
    elif tgtype & _TRIGGER_BEFORE:
        timing = "before"
    else:
        timing = "after"
    events = tuple(event for bit, event in _TRIGGER_EVENTS if tgtype & bit)
    return Trigger(name=name, timing=timing, events=events, enabled=enabled != "D")


def _postgres_catalog(fetch: Fetch, targets: tuple[Target, ...]) -> tuple[str, tuple[TargetCatalog, ...]]:
    docs = []
    catalogs = []
    for target in targets:
        found = fetch(_PG_CLASS, {"schema": target.schema, "table": target.table})
        if len(found) != 1 or found[0][1] not in ("r", "p"):
            raise WriteError("studio.plan_target_invalid")  # missing, or not a table (a view, a foreign table)
        oid, kind, row_security, forced, partition = found[0]
        if kind == "p" or partition:
            # Note N-1 of the PR #115 review: a write through a partitioned table fires the partitions' triggers,
            # rules and policies, which are not fingerprinted yet. Refused until STU-026 covers partitions.
            raise WriteError("studio.plan_target_partitioned")
        doc: dict[str, Any] = {"oid": int(oid), "kind": kind, "row_security": [bool(row_security), bool(forced)]}
        for part, sql in _PG_PARTS:
            doc[part] = [list(row) for row in fetch(sql, {"oid": int(oid)})]
        docs.append(doc)
        triggers = tuple(
            _trigger(int(tgtype), str(enabled), str(name))
            for name, tgtype, enabled, internal, _definition in doc["triggers"]
            if not internal
        )
        rules = tuple(
            Rule(name=str(name), event=_RULE_EVENTS.get(str(event), "unknown"), instead=bool(instead))
            for name, event, instead, _enabled, _definition in doc["rules"]
            if name != "_RETURN"
        )
        columns = tuple(str(row[1]) for row in doc["columns"])
        primary_key = tuple(str(row[0]) for row in fetch(_PG_PRIMARY_KEY, {"oid": int(oid)}))
        incomparable = tuple(str(row[0]) for row in fetch(_PG_INCOMPARABLE, {"oid": int(oid)}))
        catalogs.append(TargetCatalog(target, triggers, rules, bool(row_security), columns, primary_key, incomparable))
    return _digest({"engine": "postgresql", "targets": docs}), tuple(catalogs)


def read_catalog(
    fetch: Fetch, engine: str, targets: tuple[Target, ...], database_file: Path | None = None
) -> tuple[str, tuple[TargetCatalog, ...]]:
    """The fingerprint (a SHA-256 hex digest) of ``targets`` and what the preview shows of their catalog.

    ``fetch`` runs one of the fixed statements above with bound parameters (on the write connection, or in the run's
    transaction). Raises :class:`WriteError` ``studio.plan_target_invalid`` for a target that is not a table."""
    if engine == "sqlite":
        return _sqlite_catalog(fetch, targets, database_file)
    if engine == "postgresql":
        return _postgres_catalog(fetch, targets)
    raise WriteError("studio.write_unsupported")


def _connection_fetch(connection: WriteConnection) -> Fetch:
    return connection.fetch


def _transaction_fetch(transaction: tabrion.Transaction) -> Fetch:
    def fetch(sql: str, params: dict[str, Any] | None) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in transaction.fetch_all(sqlalchemy.text(sql), params)]

    return fetch


# --- validation --------------------------------------------------------------------------------------------------


def _json_value(value: object, depth: int = 0) -> None:
    """Canonical JSON only: objects with text keys, lists, text, integers, finite numbers, booleans and null."""
    if depth > MAX_DEPTH:
        raise WriteError("studio.plan_invalid")
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WriteError("studio.plan_invalid")
        return
    if isinstance(value, list | tuple):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WriteError("studio.plan_invalid")
            _json_value(item, depth + 1)
        return
    raise WriteError("studio.plan_invalid")


def _name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_NAME or _CONTROL.search(value):
        raise WriteError("studio.plan_invalid")
    return value


def _check_content(content: object, engine: str) -> PlanContent:
    """The executor's plan, checked: 1..16 targets with valid names, 1..1000 steps with tokens, JSON step data."""
    if not isinstance(content, PlanContent):
        raise WriteError("studio.plan_invalid")
    targets = content.targets
    if not isinstance(targets, tuple) or not 1 <= len(targets) <= MAX_TARGETS:
        raise WriteError("studio.plan_invalid")
    seen: set[tuple[str, str]] = set()
    for target in targets:
        if not isinstance(target, Target):
            raise WriteError("studio.plan_invalid")
        _name(target.schema)
        _name(target.table)
        if engine == "sqlite" and target.schema != "main":
            raise WriteError("studio.plan_invalid")  # one file, its own tables only (no ATTACH)
        key = (target.schema, target.table.lower() if engine == "sqlite" else target.table)
        if key in seen:
            raise WriteError("studio.plan_invalid")
        seen.add(key)
    steps = content.steps
    if not isinstance(steps, tuple) or not 1 <= len(steps) <= MAX_STEPS:
        raise WriteError("studio.plan_invalid")
    for step in steps:
        if (
            not isinstance(step, Step)
            or not isinstance(step.action, str)
            or not _TOKEN.fullmatch(step.action)
            or type(step.target) is not int
            or not 0 <= step.target < len(targets)
            or type(step.destructive) is not bool
            or not (step.rows is None or (type(step.rows) is int and step.rows >= 0))
            or not isinstance(step.data, Mapping)
        ):
            raise WriteError("studio.plan_invalid")
        _json_value(step.data)
    risks = content.risks
    if not isinstance(risks, tuple) or len(risks) > MAX_RISKS:
        raise WriteError("studio.plan_invalid")
    for risk in risks:
        if not isinstance(risk, str) or not _TOKEN.fullmatch(risk):
            raise WriteError("studio.plan_invalid")
    return content


def _hash_of(value: object) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise WriteError("studio.plan_unknown")
    return value


# --- the plan ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """A plan as the server keeps it. Immutable: confirming replaces it; its hash covers everything but ``confirmed``.

    ``session`` is the session's digest, never the cookie."""

    nonce: str
    session: str
    connection_id: str
    unlock_id: str
    executor: str
    area: Area
    engine: str
    environment: str
    profile: str
    content: PlanContent
    fingerprint: str
    catalog: tuple[TargetCatalog, ...]
    confirm_text: str
    created_at: float
    expires_at: float
    plan_hash: str = ""
    confirmed: bool = False

    def canonical(self) -> bytes:
        """The canonical JSON the plan hash is the SHA-256 of (server side only: it holds the steps' data)."""
        return _canonical(
            {
                "v": PLAN_VERSION,
                "nonce": self.nonce,
                "session": self.session,
                "connection": self.connection_id,
                "unlock": self.unlock_id,
                "executor": self.executor,
                "area": self.area.value,
                "engine": self.engine,
                "environment": self.environment,
                "profile": self.profile,
                "targets": [[t.schema, t.table] for t in self.content.targets],
                "steps": [[s.action, s.target, s.destructive, s.rows, s.data] for s in self.content.steps],
                "risks": list(self.content.risks),
                "fingerprint": self.fingerprint,
                "confirm_text": self.confirm_text,
                "created": round(self.created_at, 3),
                "expires": round(self.expires_at, 3),
            }
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    @property
    def objects(self) -> list[str]:
        """The targets' qualified names, for the audit record."""
        return [target.qualified for target in self.content.targets]

    def risks(self) -> list[str]:
        notes = list(self.content.risks)
        if any(step.destructive for step in self.content.steps):
            notes.insert(0, RISK_DESTRUCTIVE)
        if any(catalog.triggers for catalog in self.catalog):
            notes.append(RISK_TRIGGERS)
        if any(catalog.rules for catalog in self.catalog):
            notes.append(RISK_RULES)
        if any(catalog.row_security for catalog in self.catalog):
            notes.append(RISK_ROW_SECURITY)
        return list(dict.fromkeys(notes))

    def preview(self, now: float) -> dict[str, Any]:
        """What the page shows (D-09, D-10): names, counts, codes and the hash; never a step's data or a value."""
        targets = self.content.targets
        return {
            "plan_hash": self.plan_hash,
            "executor": self.executor,
            "area": self.area.value,
            "connection_id": self.connection_id,
            "write_profile_id": f"{WRITE_ID_PREFIX}{self.profile}",
            "engine": self.engine,
            "environment": self.environment,
            "targets": [
                {
                    "schema": catalog.target.schema,
                    "table": catalog.target.table,
                    "triggers": [trigger.to_dict() for trigger in catalog.triggers],
                    "rules": [rule.to_dict() for rule in catalog.rules],
                    "row_security": catalog.row_security,
                }
                for catalog in self.catalog
            ],
            "steps": [
                {
                    "action": step.action,
                    "target": targets[step.target].qualified,
                    "destructive": step.destructive,
                    "rows": step.rows,
                }
                for step in self.content.steps
            ],
            "destructive": any(step.destructive for step in self.content.steps),
            "risks": self.risks(),
            "confirm_text": self.confirm_text,
            "confirmed": self.confirmed,
            "expires_in_s": max(0, round(self.expires_at - now)),
        }


def _confirm_text(content: PlanContent, profile: str) -> str:
    """D-10: a destructive plan is confirmed with the name of its first destructive target; any other with the write
    profile's name (D-09), which the user typed to unlock as well."""
    for step in content.steps:
        if step.destructive:
            return content.targets[step.target].table
    return profile


def _same(given: str, expected: str) -> bool:
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


class Plans:
    """The open plans of this process, by plan hash. Thread-safe. Lives as long as the server; a restart ends all."""

    def __init__(self, unlocks: Unlocks, clock: Callable[[], float] | None = None) -> None:
        self.unlocks = unlocks
        self._clock = clock
        self.executors: dict[str, PlanExecutor] = {}
        self._plans: dict[str, Plan] = {}
        self._lock = threading.Lock()

    def clock(self) -> float:
        return self._clock() if self._clock is not None else self.unlocks.clock()

    def register(self, executor: PlanExecutor) -> None:
        """Make a kind of plan available (by its ``name``, a token). A name is registered once."""
        if not isinstance(executor.name, str) or not _TOKEN.fullmatch(executor.name):
            raise ValueError("an executor's name is a token")
        if not isinstance(executor.area, Area):
            raise ValueError("an executor names its area")
        if executor.name in self.executors:
            raise ValueError("this executor name is registered already")
        self.executors[executor.name] = executor

    # --- create ------------------------------------------------------------------------------------------------------

    def create(self, session: str, connection_id: object, executor_name: object, request: object) -> dict[str, Any]:
        """Build, fingerprint and keep a plan; its preview. Raises :class:`WriteError` (or a tabrion error)."""
        executor = self.executors.get(executor_name) if isinstance(executor_name, str) else None
        if executor is None:
            raise WriteError("studio.plan_executor_unknown")
        if not isinstance(connection_id, str):
            raise WriteError("studio.write_target_unknown")
        unlock = self.unlocks.require_unlock(session, connection_id, executor.area)
        if not isinstance(request, Mapping):
            raise WriteError("studio.plan_invalid")
        _json_value(request)
        if len(_canonical(request)) > MAX_REQUEST_BYTES:
            raise WriteError("studio.plan_invalid")
        engine = unlock.connection.engine
        try:
            content = _check_content(executor.build(request, engine), engine)
        except WriteError:
            raise
        except (ValueError, TypeError, KeyError, IndexError):
            raise WriteError("studio.plan_invalid") from None
        fingerprint, catalog = read_catalog(
            _connection_fetch(unlock.connection), engine, content.targets, unlock.connection.database_file
        )
        if getattr(executor, "requires_primary_key", False) is True and any(not c.primary_key for c in catalog):
            # STU-026 (03 section 2 D): rows are written by their primary key only, never by rowid or ctid.
            raise WriteError("studio.plan_target_no_primary_key")
        check: CatalogCheck | None = getattr(executor, "check", None)
        if check is not None:
            try:
                check(content, catalog)
            except WriteError:
                raise
            except (ValueError, TypeError, KeyError, IndexError):
                raise WriteError("studio.plan_invalid") from None
        now = self.clock()
        plan = Plan(
            nonce=secrets.token_hex(16),
            session=session,
            connection_id=unlock.connection_id,
            unlock_id=unlock.unlock_id,
            executor=executor.name,
            area=executor.area,
            engine=engine,
            environment=unlock.environment,
            profile=unlock.write_name,
            content=content,
            fingerprint=fingerprint,
            catalog=catalog,
            confirm_text=_confirm_text(content, unlock.write_name),
            created_at=now,
            expires_at=now + PLAN_TTL_SECONDS,
        )
        plan = replace(plan, plan_hash=plan.compute_hash())
        with self._lock:
            self._evict(session, now)
            self._plans[plan.plan_hash] = plan
        logger.info("write plan made", extra={"event": "studio.plan_created"})
        return plan.preview(now)

    def _evict(self, session: str, now: float) -> None:
        """Drop expired plans, then the oldest ones (the store keeps the order they were made in) beyond the limits.
        The caller holds the lock."""
        for key in [key for key, plan in self._plans.items() if plan.expires_at < now]:
            del self._plans[key]
        own = [key for key, plan in self._plans.items() if plan.session == session]
        for key in own[: max(0, len(own) - MAX_PLANS_PER_SESSION + 1)]:
            del self._plans[key]
        every = list(self._plans)
        for key in every[: max(0, len(every) - MAX_PLANS + 1)]:
            del self._plans[key]

    # --- the checks of confirm and execute --------------------------------------------------------------------------

    def _own(self, session: str, plan_hash: object, *, take: bool) -> Plan:
        """This session's plan with ``plan_hash`` (taken out of the store when ``take``); another session's plan is
        the same answer as none (``studio.plan_unknown``)."""
        key = _hash_of(plan_hash)
        with self._lock:
            plan = self._plans.get(key)
            if plan is None or not _same(plan.session, session):
                raise WriteError("studio.plan_unknown")
            if take:
                del self._plans[key]
        return plan

    def _discard(self, plan: Plan) -> None:
        with self._lock:
            if self._plans.get(plan.plan_hash) is plan:
                del self._plans[plan.plan_hash]

    def _check(self, plan: Plan, connection_id: object) -> None:
        """Binding, expiry and integrity; the unlock is re-checked by the caller."""
        if not isinstance(connection_id, str) or not _same(plan.connection_id, connection_id):
            raise WriteError("studio.plan_binding")
        if self.clock() > plan.expires_at:
            raise WriteError("studio.plan_expired")
        if not _same(plan.compute_hash(), plan.plan_hash):
            # The stored plan was changed after its preview: it never runs (TX-08).
            raise WriteError("studio.plan_changed")

    # --- confirm -----------------------------------------------------------------------------------------------------

    def confirm(self, session: str, plan_hash: object, connection_id: object, typed: object) -> dict[str, Any]:
        """Confirm the plan with its hash and the typed text (D-09, D-10). A wrong text may be typed again; a wrong
        binding, an expired or changed plan or an ended unlock discards the plan."""
        plan = self._own(session, plan_hash, take=False)
        try:
            self._check(plan, connection_id)
            unlock = self.unlocks.require_unlock(session, plan.connection_id, plan.area)
            if not _same(unlock.unlock_id, plan.unlock_id):
                raise WriteError("studio.plan_binding")
        except WriteError:
            self._discard(plan)
            raise
        if not isinstance(typed, str) or not _same(typed, plan.confirm_text):
            raise WriteError("studio.plan_confirmation_mismatch")
        confirmed = replace(plan, confirmed=True)
        with self._lock:
            if self._plans.get(plan.plan_hash) is not plan:
                raise WriteError("studio.plan_unknown")  # executed or discarded meanwhile
            self._plans[plan.plan_hash] = confirmed
        logger.info("write plan confirmed", extra={"event": "studio.plan_confirmed"})
        return {
            "plan_hash": plan.plan_hash,
            "confirmed": True,
            "expires_in_s": max(0, round(plan.expires_at - self.clock())),
        }

    # --- execute -----------------------------------------------------------------------------------------------------

    def execute(self, session: str, plan_hash: object, connection_id: object) -> dict[str, Any]:
        """Run the confirmed plan once (see the module text). Raises :class:`WriteError` or a tabrion error."""
        plan = self._own(session, plan_hash, take=True)  # one attempt: a replay finds nothing
        self._check(plan, connection_id)
        if not plan.confirmed:
            raise WriteError("studio.plan_not_confirmed")
        executor = self.executors.get(plan.executor)
        if executor is None:
            raise WriteError("studio.plan_executor_unknown")
        with self.unlocks.audited_transaction(
            session,
            plan.connection_id,
            plan.area,
            action=plan.executor,
            objects=plan.objects,
            plan_hash=plan.plan_hash,
            unlock_id=plan.unlock_id,
        ) as run:
            transaction, connection = run.transaction, run.connection
            if transaction is None or connection is None:  # pragma: no cover - audited_transaction sets both
                raise WriteError("studio.write_locked")
            self._drift(plan, transaction, connection)
            for step in plan.content.steps:
                executor.run_step(transaction, plan, step, run)
        logger.info("write plan ran", extra={"event": "studio.plan_executed"})
        return {
            "plan_hash": plan.plan_hash,
            "result": "committed",
            "rows": dict(run.rows),
            "audit_pending": run.audit_pending,
        }

    def _drift(self, plan: Plan, transaction: tabrion.Transaction, connection: WriteConnection) -> None:
        """Inside the run's transaction, immediately before the first step: the file is still the unlocked one
        (TX-16) and the targets' catalog still has the preview's fingerprint (TX-09); on PostgreSQL again after the
        targets are locked, so nothing can change them until the transaction ends."""
        connection.verify()
        fetch = _transaction_fetch(transaction)

        def compare() -> None:
            try:
                fingerprint, _ = read_catalog(fetch, plan.engine, plan.content.targets, connection.database_file)
            except WriteError as exc:
                if exc.code in ("studio.plan_target_invalid", "studio.plan_target_partitioned"):
                    raise WriteError("studio.plan_drift") from None  # the table is gone or no longer a table
                raise
            if not _same(fingerprint, plan.fingerprint):
                logger.warning("write plan refused: the target changed", extra={"event": "studio.plan_drift"})
                raise WriteError("studio.plan_drift")

        compare()
        if plan.engine == "postgresql":
            names = sorted(f"{_quote(t.schema)}.{_quote(t.table)}" for t in plan.content.targets)
            # The lock an UPDATE takes anyway; it conflicts with every DDL, CREATE TRIGGER and CREATE RULE.
            transaction.execute(sqlalchemy.text(f"LOCK TABLE {', '.join(names)} IN ROW EXCLUSIVE MODE"))
            compare()

    # --- discard -----------------------------------------------------------------------------------------------------

    def discard(self, session: str, plan_hash: object) -> None:
        """Drop this session's plan (the page closed the preview). Unknown hashes are ignored (no oracle)."""
        with contextlib.suppress(WriteError):
            self._own(session, plan_hash, take=True)

    def open_count(self, session: str) -> int:
        with self._lock:
            return sum(1 for plan in self._plans.values() if plan.session == session)
