"""Running a Schema Designer plan directly (STU-CR-5; STU-ADR-0007 sections 4 to 11; TX-18, TX-21..TX-27; GS3-03..05,
GS3-09..20, GS3-27..29).

Imported only by :mod:`.designer`, which :mod:`tabrion_studio.write.routes` imports after it checked the
``schema_designer`` flag (GS3-01).

**The flow** (section 4). The preview (:func:`.designer.preview`) plans from a fresh level-2 snapshot on the write
connection to the draft and keeps a :class:`RunPlan` with the draft: the plan hash, the source fingerprint, the target
model **as it was previewed** (a later edit drops the plan), the engine version, the risk classes and what the
confirmation needs (:func:`requirements`). The page shows it with the preview warnings (:func:`warnings`) and the
reasons a direct run is not offered (:func:`blocked`).

1. **Confirm** (:func:`confirm`): the plan hash, the write profile's name typed (the STU-XW-3 confirmation) and, per
   risk class (section 6): the name of each table a ``destructive`` or ``rebuild`` step changes, typed (D-10); the
   index of each ``data_changing`` step, named in the dialog; ``locking`` acknowledged; and the backup choice. The
   server records the confirmed classes; a wrong part refuses (``studio.plan_confirmation_mismatch``) and the plan
   stays for another try.
2. **Run** (:func:`run`): the plan is taken out of the draft (one attempt: a replay finds nothing), the binding
   (session, connection, unlock), the expiry and the confirmation are checked; the unlock is re-checked (flag,
   expiry, the stored environment); the conditions of section 5 are checked again (:func:`blocked`); Studio plans
   **again** from a fresh snapshot with the same ``server_version`` and compares the new hash with the **recorded**
   one (never one from the request) and the new risk classes with the confirmed ones: a difference is
   ``studio.plan_drift`` before anything is recorded or sent. Then ``run.start`` (no record, no run), the backup
   (section 7), and ``tabrion.plan.execute_plan(database, target, plan_hash=<recorded>, expected_fingerprint=<the
   preview's source fingerprint>, environment=<stored, dev or test>, allow=<confirmed classes>, server_version=...,
   concurrent_indexes=False, schemas=[<the draft's schema>])``, which plans again inside its own transaction, locks
   the target tables under ``lock_timeout`` (PostgreSQL), checks the fingerprint again after the locks and runs every
   step or none. Its ``SchemaDrift`` and ``PlanMismatch`` are ``studio.plan_drift``. ``run.result`` follows.
3. **Dry run** (GS3-29): only when the request asks for it (``dry_run: true``), with the same confirmation, unlock and
   checks; ``execute_plan(dry_run=True)`` runs the steps and rolls them back. On PostgreSQL it takes the same
   ``ACCESS EXCLUSIVE`` locks and sequence values it uses stay consumed (the page says so before). It is audited like
   a run (action ``schema_designer.dry_run``), makes no backup, and uses the confirmation up like a run.

**Direct execution only** (section 5, :func:`blocked`): the stored environment is ``dev`` or ``test`` (STAGE may
preview, never run; PROD is never unlocked, E-X7); tabrion's ``migration_markers`` finds none; the project has no
``alembic.ini`` (any such file: stricter than ``script_location``) and the database no Levuvo version table
(Nodestro, Axentra, ``<prefix>orkexa_schema``); the plan has no non-transactional step; the plan has a step; the
audit record can name every table (at most 64, the audit log's cap); and on PostgreSQL the plan has no
``destructive``, ``rebuild`` or ``data_changing`` step unless both PostgreSQL tools are configured
(``database-tools.toml``, :func:`backup_possible`) on a POSIX system: its backup goes through the verified
``pg_dump`` runner of STU-ADR-0008 section 3 (:mod:`tabrion_studio.write.pgtools`, STU-XDM-2; the STU-CR-5
follow-up), and without the tools there is no backup and no such run (fail closed, ``backup_unavailable``).

**Decision 1A at each run (GS3-28).** On PostgreSQL, the privilege check of the unlock runs again on the very session
that runs the plan, after it is checked out and before ``BEGIN``; a role that gained superuser, BYPASSRLS, REPLICATION
or a server-file role refuses the run with ``405 studio.write_role_over_privileged`` and ends the unlock.

**SQLite (section 10).** The run uses the unlock's write connection, whose pool connection has ``trusted_schema``
off, defensive mode and Studio's write authorizer, which refuses every schema change. For the run only, inside
tabrion's schema transaction (after the checkout, before ``BEGIN``, on the one connection of the pool), Studio puts
the **schema authorizer** (:func:`schema_authorizer`) in its place: the write authorizer's rules plus the schema
statements a plan of tabrion makes on ``main`` (tables, indexes, triggers, ``ALTER TABLE``, the internal bookkeeping
of these in the schema table) and the two pragmas tabrion's executor sets and restores (``foreign_keys``,
``trusted_schema``); never views, temporary or virtual objects, ``ATTACH``, ``DETACH`` or another pragma. The write
authorizer is put back when the transaction ended. tabrion turns foreign keys off before ``BEGIN``, runs
``foreign_key_check`` and ``integrity_check`` before the commit and checks the trigger texts it re-creates again
(K-1); on Python 3.11 (no defensive mode) Studio opens no SQLite write connection at all.

**Backup (section 7, :func:`backup_sqlite`).** Before a real run of a plan with a ``destructive``, ``rebuild`` or
``data_changing`` step, ``tabrion.sqlite.backup`` copies the file through the checked write connection into
``backups/schema-designer/`` in the folder of ``write-areas.toml`` (the audit log's parent; never a folder an
environment variable chose): the folders 0700, the file 0600 with a name Studio makes from the time and random hex
(never a table or file name of the user). A failed backup stops the run (``studio.schema_backup_failed``). On a
``dev`` profile only, the user may leave it out by typing the database file's name; the run's ``run.start`` record
then says ``backup: opted_out``. Studio never deletes a backup (retention is the user's: an open point).

**PostgreSQL backup (section 7, the STU-CR-5 follow-up, :func:`backup_postgres`).** Before a real run of such a plan on
DEV or TEST (PROD is never unlocked; the opt-out is SQLite's only), Studio makes a ``pg_dump -Fc`` archive of the
unlocked database with the runner of STU-XDM-2 and every check of it: the tools only from ``database-tools.toml``, each
checked again now (path, owner, mode, folders, ``--version``, a major of at least 16 and at least the server's); the
write profile's credentials read again with every check of an unlock and only while the profiles are the ones the unlock
checked (:meth:`.Unlocks.unlock_credentials`); the password only in the run's temporary passfile; the environment built
from nothing; the archive published under its final name only after exit status 0 and a readable table of contents
(``pg_restore --list``). It goes to ``backups/schema-designer/<time>-<hex>.dump`` with its metadata next to it
(:func:`tabrion_studio.write.backup_store.metadata`, origin ``schema_designer``: the tool, its SHA-256, the archive's
size and SHA-256, the scope notice that a ``pg_dump`` archive is not a complete backup). The run goes on only when all
of that succeeded; anything else refuses it before ``execute_plan``: ``studio.backup_tool_unavailable`` (no tools, an
unsafe configuration or tool, Windows: ``tool.platform``), ``studio.backup_tool_failed`` (the tool failed, for example a
role that may not read every table, or ran out of time), ``studio.write_target_changed``,
``studio.schema_backup_failed`` (the folder or the metadata). The dump is of the whole database as the write role sees
it, taken before the run's locks: a row written by someone else between the dump and the run is not in it.

**Never a value** (section 11, TX-27). The audit records carry the plan hash (its hex part), the qualified table
names, the risk classes and the step count; the log carries event names and problem codes. No statement, default,
check text or value reaches a log, the audit log, the query history or a diagnostics report; the backup's path is in
the answer to the user only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from dataclasses import dataclass, field, replace
import datetime
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from typing import Any, cast

from tabrion.errors import (
    PlanFailed,
    PlanMismatch,
    PlanRefused,
    SchemaDrift,
    TabrionError,
    UsageError,
)
from tabrion.plan import RiskClass, execute_plan, migration_markers
from tabrion.schema import SchemaSnapshot, TableShape
from tabrion.sqlite import backup

from tabrion_studio.config.secrets import SecretError
from tabrion_studio.config.write_areas import Area
from tabrion_studio.paths import ensure_private_dir
from tabrion_studio.services.readonly import SQLITE_FUNCTIONS
from tabrion_studio.write.audit import AuditError
from tabrion_studio.write.backup_store import BackupStore, metadata
from tabrion_studio.write.connection import (
    WriteConnection,
    WriteError,
    refusing_findings_now,
    write_authorizer,
)
from tabrion_studio.write.pgtools import Connection, Runner, load_tools, verify_tool
from tabrion_studio.write.schema_designer.create import DriverFailure
from tabrion_studio.write.schema_designer.draft import refusal
from tabrion_studio.write.unlock import Unlock, Unlocks

logger = logging.getLogger("tabrion_studio.write")

#: A previewed plan may be confirmed and run this long after its preview (as a plan of STU-XW-3).
RUN_TTL_SECONDS = 5 * 60
#: The environments a plan runs directly in (E-X4): never STAGE, PROD or an unset environment.
RUN_ENVIRONMENTS = frozenset({"dev", "test"})
#: The only environment where the backup may be left out (section 7).
BACKUP_OPT_OUT_ENVIRONMENTS = frozenset({"dev"})
#: The classes that need a backup before a real run (section 7) and those whose tables are typed (D-10).
BACKUP_CLASSES = frozenset({RiskClass.DESTRUCTIVE, RiskClass.REBUILD, RiskClass.DATA_CHANGING})
TYPED_TABLE_CLASSES = frozenset({RiskClass.DESTRUCTIVE, RiskClass.REBUILD})
#: The ``lock_timeout`` of every run (seconds): tabrion's default, shown in the ``locking`` notice.
LOCK_TIMEOUT_S = 5.0
#: The most tables one run may change: the audit log takes at most 64 object names in a record (review N-3, #114).
MAX_RUN_TABLES = 64
#: The Levuvo version tables of section 5 (``migration_markers`` knows Nodestro and Axentra, not Orkexa's).
_LEVUVO_TABLES = (
    "SELECT pg_catalog.count(*) FROM pg_catalog.pg_class c"
    " JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"
    " WHERE c.relkind OPERATOR(pg_catalog.=) ANY (ARRAY['r', 'p', 'v', 'm', 'f']::pg_catalog.\"char\"[])"
    " AND pg_catalog.lower(c.relname::pg_catalog.text) OPERATOR(pg_catalog.~)"
    " '^(nodestro_schema_migrations|axentra_schema_migrations|([a-z][a-z0-9_]{0,30}_)?orkexa_schema)$'"
)
_ALEMBIC_INI = "alembic.ini"
#: Where the backups go, below the folder of ``write-areas.toml``.
BACKUP_FOLDER = ("backups", "schema-designer")
ACTION_RUN = "schema_designer.run"
ACTION_DRY_RUN = "schema_designer.dry_run"
_HASH_PREFIX = "sha256:"
_HEX = re.compile(r"[0-9a-f]{64}")
_POSIX = sys.platform != "win32"
#: The problems of the PostgreSQL backup that keep their own code (the rest is ``studio.schema_backup_failed``).
_BACKUP_CODES = frozenset(
    {
        "studio.backup_tool_unavailable",
        "studio.backup_tool_failed",
        "studio.write_target_changed",
        "studio.write_prod_locked",
        "studio.write_coupling_invalid",
        "studio.schema_backup_failed",
    }
)


# --- what the preview keeps --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StepInfo:
    index: int
    operation: str
    table: str  # schema.table, as tabrion names it
    risks: frozenset[str]
    transactional: bool


@dataclass(frozen=True)
class Confirmation:
    """What the user confirmed (section 4 item 2): the classes the executor may run and the backup choice."""

    classes: frozenset[RiskClass]
    backup: str  # "make", "skip" or "not_needed"


@dataclass(frozen=True)
class RunPlan:
    """A previewed plan, kept with the draft until it is run once, a new preview or an edit replaces it, the draft
    ends or it expires. Never sent whole to the page and never read from it."""

    session: str
    connection_id: str
    unlock_id: str
    engine: str
    environment: str
    profile: str
    schema: str
    database_name: str
    target: SchemaSnapshot
    source_fingerprint: str
    plan_hash: str
    server_version: tuple[int, ...]
    steps: tuple[StepInfo, ...]
    risks: frozenset[str]
    blocked: tuple[str, ...]
    expires_at: float
    confirmation: Confirmation | None = None

    @property
    def tables(self) -> list[str]:
        """The qualified names of the tables the steps change, for the audit record (sorted, each once)."""
        return sorted({step.table for step in self.steps})

    def bare(self, table: str) -> str:
        prefix = f"{self.schema}."
        return table[len(prefix) :] if table.startswith(prefix) else table

    @property
    def typed_tables(self) -> list[str]:
        """The tables of every ``destructive`` or ``rebuild`` step, by the name the user types (D-10)."""
        wanted = {c.value for c in TYPED_TABLE_CLASSES}
        return sorted({self.bare(step.table) for step in self.steps if step.risks & wanted})

    @property
    def data_changing_steps(self) -> list[int]:
        return [step.index for step in self.steps if RiskClass.DATA_CHANGING.value in step.risks]

    @property
    def locking(self) -> bool:
        return RiskClass.LOCKING.value in self.risks

    @property
    def backup_needed(self) -> bool:
        return bool(self.risks & {c.value for c in BACKUP_CLASSES})

    @property
    def backup_opt_out(self) -> bool:
        return self.backup_needed and self.engine == "sqlite" and self.environment in BACKUP_OPT_OUT_ENVIRONMENTS


def _same(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def database_name(connection: WriteConnection) -> str:
    """The database's own name: the SQLite file's name, or the PostgreSQL database's (read on the connection)."""
    if connection.engine == "sqlite":
        return connection.database_file.name if connection.database_file is not None else ""
    rows = connection.fetch("SELECT pg_catalog.current_database()::pg_catalog.text")
    return str(rows[0][0]) if rows else ""


def make_plan(
    unlock: Unlock,
    *,
    schema: str,
    target: SchemaSnapshot,
    change: Any,
    server_version: tuple[int, ...],
    blocked_reasons: tuple[str, ...],
    now: float,
) -> RunPlan:
    steps = tuple(
        StepInfo(
            index=step.index,
            operation=step.operation,
            table=step.target,
            risks=frozenset(risk.value for risk in step.risks),
            transactional=step.transactional,
        )
        for step in change.steps
    )
    return RunPlan(
        session=unlock.session,
        connection_id=unlock.connection_id,
        unlock_id=unlock.unlock_id,
        engine=unlock.connection.engine,
        environment=unlock.environment,
        profile=unlock.write_name,
        schema=schema,
        database_name=database_name(unlock.connection),
        target=target,
        source_fingerprint=change.source_fingerprint,
        plan_hash=change.plan_hash,
        server_version=server_version,
        steps=steps,
        risks=frozenset(risk.value for risk in change.risks),
        blocked=blocked_reasons,
        expires_at=now + RUN_TTL_SECONDS,
    )


# --- section 5: may this plan run directly? ---------------------------------------------------------------------------


def management(connection: WriteConnection, project: Path | None) -> list[str]:
    """The migration management found for the connection (section 5), as reasons: ``migration_managed`` (tabrion's
    ``migration_markers``: ``alembic_version``, ``nodestro_schema_migrations``, ``axentra_schema_migrations`` in any
    schema, a SQLite ``user_version`` above 0), ``levuvo_managed`` (a Levuvo version table, Orkexa's included) and
    ``alembic_project`` (the project folder has an ``alembic.ini``: revisions only, also without a version table)."""
    found: list[str] = []
    if migration_markers(connection.database):
        found.append("migration_managed")
    if connection.engine == "postgresql" and int(connection.fetch(_LEVUVO_TABLES)[0][0] or 0) > 0:
        found.append("levuvo_managed")
    if project is None or os.path.lexists(project / _ALEMBIC_INI):
        found.append("alembic_project")  # no project folder known: fail closed
    return found


def backup_possible(write_state: Path | None) -> bool:
    """Whether a PostgreSQL backup can be tried at all (the STU-CR-5 follow-up): a POSIX system, the folder of
    ``write-areas.toml`` and both tools in its ``database-tools.toml`` (read with that file's checks). Every check of
    the tools runs again at the backup itself (:func:`backup_postgres`); this only keeps a plan that cannot get one from
    being offered."""
    if write_state is None or not _POSIX:
        return False
    tools = load_tools(write_state)
    return tools.pg_dump is not None and tools.pg_restore is not None


def blocked(unlock: Unlock, change: Any, project: Path | None, write_state: Path | None = None) -> tuple[str, ...]:
    """Why this plan is not run directly (section 5); empty when it may be. Checked at the preview and again right
    before the run. ``write_state`` is the folder of ``write-areas.toml`` (the tools file); None: no PostgreSQL
    backup, so no such plan runs."""
    reasons: list[str] = []
    if not change.steps:
        reasons.append("nothing_to_run")
    if unlock.environment not in RUN_ENVIRONMENTS:
        reasons.append("environment")
    reasons += management(unlock.connection, project)
    if any(not step.transactional for step in change.steps):
        reasons.append("non_transactional")
    if len({step.target for step in change.steps}) > MAX_RUN_TABLES:
        reasons.append("too_many_tables")
    if (
        unlock.connection.engine == "postgresql"
        and set(change.risks) & BACKUP_CLASSES
        and not backup_possible(write_state)
    ):
        # STU-ADR-0008 section 3 (STU-CR-5 follow-up): no configured pg_dump and pg_restore, no backup, no such run.
        reasons.append("backup_unavailable")
    return tuple(reasons)


# --- section 8: the preview warnings ------------------------------------------------------------------------------


def _integer_primary_key(table: TableShape) -> bool:
    """Whether the SQLite table keeps its ``rowid`` values through a rebuild: a single-column ``INTEGER PRIMARY KEY``
    (the rowid's alias). Anything else gets the warning (careful: a ``WITHOUT ROWID`` table has none to lose)."""
    if len(table.primary_key) != 1:
        return False
    column = next((c for c in table.columns if c.name == table.primary_key[0]), None)
    return column is not None and column.type.strip().upper() == "INTEGER"


def _type_changes(current: TableShape | None, target: TableShape | None) -> bool:
    if current is None or target is None:
        return False
    before = {c.name: c.type for c in current.columns}
    return any(c.name in before and before[c.name] != c.type for c in target.columns)


def warnings(current: SchemaSnapshot, target: SchemaSnapshot, change: Any, schema: str) -> list[dict[str, Any]]:
    """The fixed preview warnings (section 8, GS3-16; review I-1 of #132), as codes with the bare table name where one
    applies: the page has an English and a German text for each. Names go to this page only."""
    prefix = f"{schema}."

    def bare(name: str) -> str:
        return name[len(prefix) :] if name.startswith(prefix) else name

    tables = {t.name: t for t in current.tables}
    wanted = {t.name: t for t in target.tables}
    found: list[dict[str, Any]] = [{"code": "not_modelled", "table": None}]
    if change.dialect == "postgresql":
        named: set[str] = set()
        for step in change.steps:
            if step.operation in ("drop_column", "drop_table") or (
                step.operation == "alter_column" and _type_changes(tables.get(step.target), wanted.get(step.target))
            ):
                named.add(step.target)
        found += [{"code": "pg_dependent_code", "table": bare(name)} for name in sorted(named)]
        found.append({"code": "function_drift", "table": None})
    else:
        for step in change.steps:
            existing = tables.get(step.target)
            if step.operation == "rebuild_table" and existing is not None and not _integer_primary_key(existing):
                found.append({"code": "sqlite_rowid", "table": bare(step.target)})
    found.append({"code": "fingerprint_gaps", "table": None})
    if any(RiskClass.LOCKING in step.risks for step in change.steps):
        found.append({"code": "locking", "table": None, "lock_timeout_s": LOCK_TIMEOUT_S})
    return found


def requirements(plan: RunPlan) -> dict[str, Any]:
    """What the confirmation needs (section 6) and the backup (section 7), for the page."""
    return {
        "profile": plan.profile,
        "tables": plan.typed_tables,
        "data_changing_steps": plan.data_changing_steps,
        "locking": plan.locking,
        "lock_timeout_s": LOCK_TIMEOUT_S,
        "backup": {
            "required": plan.backup_needed,
            "engine": plan.engine,
            "opt_out_allowed": plan.backup_opt_out,
            # The text to type to leave the backup out (dev only): the database file's name.
            "opt_out_text": plan.database_name if plan.backup_opt_out else None,
        },
    }


# --- confirm ------------------------------------------------------------------------------------------------------


def _own(plan: RunPlan | None, plan_hash: object) -> RunPlan:
    if plan is None or not isinstance(plan_hash, str) or not _same(plan.plan_hash, plan_hash):
        raise WriteError("studio.plan_unknown")
    return plan


def _check_binding(plan: RunPlan, unlock: Unlock, now: float) -> None:
    if not _same(plan.unlock_id, unlock.unlock_id) or not _same(plan.connection_id, unlock.connection_id):
        raise WriteError("studio.plan_binding")
    if now > plan.expires_at:
        raise WriteError("studio.plan_expired")


def _names(raw: object) -> list[str] | None:
    if not isinstance(raw, list) or len(raw) > MAX_RUN_TABLES or not all(isinstance(item, str) for item in raw):
        return None
    return list(raw)


def _indexes(raw: object) -> list[int] | None:
    if not isinstance(raw, list) or len(raw) > 1000 or not all(type(item) is int for item in raw):
        return None
    return list(raw)


def check_confirmation(plan: RunPlan, doc: Mapping[str, Any]) -> Confirmation:
    """The confirmation of ``doc`` against ``plan`` (sections 4 item 2, 6 and 7); :class:`WriteError` otherwise. A
    wrong text names nothing of what was expected (``studio.plan_confirmation_mismatch``)."""
    if plan.blocked:
        raise WriteError("studio.schema_run_not_allowed", [("plan.reason", f"plan.{plan.blocked[0]}")])
    typed = doc.get("typed")
    mismatch = WriteError("studio.plan_confirmation_mismatch")
    if not isinstance(typed, str) or not _same(typed, plan.profile):
        raise mismatch
    tables = _names(doc.get("tables", []))
    if tables is None or sorted(tables) != plan.typed_tables or len(set(tables)) != len(tables):
        raise mismatch
    steps = _indexes(doc.get("steps", []))
    if steps is None or sorted(steps) != plan.data_changing_steps or len(set(steps)) != len(steps):
        raise mismatch
    locking = doc.get("locking", False)
    if type(locking) is not bool or locking != plan.locking:
        raise mismatch
    choice = doc.get("backup", "make")
    if not plan.backup_needed:
        if choice not in ("make", None) or doc.get("backup_typed") not in (None, ""):
            raise mismatch
        backup_choice = "not_needed"
    elif choice == "make":
        backup_choice = "make"
    elif choice == "skip":
        if not plan.backup_opt_out:
            raise WriteError("studio.schema_backup_required")
        skip_text = doc.get("backup_typed")
        if not isinstance(skip_text, str) or not plan.database_name or not _same(skip_text, plan.database_name):
            raise mismatch
        backup_choice = "skip"
    else:
        raise mismatch
    classes = frozenset(RiskClass(risk) for risk in plan.risks)
    return Confirmation(classes=classes, backup=backup_choice)


def confirmed(plan: RunPlan, confirmation: Confirmation, now: float) -> tuple[RunPlan, dict[str, Any]]:
    logger.info("schema plan confirmed", extra={"event": "studio.schema_plan_confirmed"})
    return replace(plan, confirmation=confirmation), {
        "plan_hash": plan.plan_hash,
        "confirmed": True,
        "backup": confirmation.backup,
        "expires_in_s": max(0, round(plan.expires_at - now)),
    }


# --- SQLite: the schema authorizer of the run ---------------------------------------------------------------------

_SCHEMA_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_REINDEX,
    }
)
#: The schema tables SQLite itself writes while it runs a schema statement (defensive mode refuses a direct write).
_SCHEMA_TABLES = frozenset({"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema"})
_ROW_CHANGES = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})
#: SQLite's internal functions of ``ALTER TABLE`` (not callable from a statement's own text).
_ALTER_FUNCTIONS = frozenset(
    {
        "sqlite_drop_column",
        "sqlite_rename_column",
        "sqlite_rename_table",
        "sqlite_rename_test",
        "sqlite_rename_quotefix",
    }
)
#: The pragmas tabrion's executor sets before ``BEGIN`` and restores afterwards.
_RUN_PRAGMAS = frozenset({"foreign_keys", "trusted_schema"})


def schema_authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, trigger: str | None) -> int:
    """The authorizer of a SQLite schema change run (see the module text): the write authorizer's rules plus the
    schema statements of a tabrion plan on ``main`` and the pragmas the executor sets."""
    if action in _SCHEMA_ACTIONS:
        return sqlite3.SQLITE_OK if db == "main" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_ALTER_TABLE:
        return sqlite3.SQLITE_OK if arg1 == "main" else sqlite3.SQLITE_DENY
    if action in _ROW_CHANGES and (arg1 or "").lower() in _SCHEMA_TABLES and db in ("main", "temp"):
        return sqlite3.SQLITE_OK  # SQLite's own bookkeeping of a schema statement
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _ALTER_FUNCTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in _RUN_PRAGMAS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (arg2 or "").lower() in SQLITE_FUNCTIONS else sqlite3.SQLITE_DENY
    return write_authorizer(action, arg1, arg2, db, trigger)


# --- the database the executor gets ------------------------------------------------------------------------------


class _RunDatabase:
    """The unlock's tabrion ``Database`` as ``execute_plan`` sees it, with Studio's step before ``BEGIN`` and after
    the transaction on the very connection that runs the plan: on PostgreSQL decision 1A again (GS3-28); on SQLite the
    schema authorizer in place of the write authorizer, and the write authorizer back afterwards."""

    def __init__(self, connection: WriteConnection) -> None:
        self._connection = connection
        self._db: Any = connection.database
        self.dialect = self._db.dialect

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return self._db.read(*args, **kwargs)

    def inspector(self, *args: Any, **kwargs: Any) -> Any:
        return self._db.inspector(*args, **kwargs)

    def _autocommit(self, *_args: Any, **_kwargs: Any) -> None:
        # Never needed: a plan with a non-transactional step is not run directly (section 5).
        raise WriteError("studio.schema_run_not_allowed", [("plan.reason", "plan.non_transactional")])

    @contextlib.contextmanager
    def _schema_transaction(
        self, *, timeout: float | None, prepare: Callable[[Any], None], restore: Callable[[Any], None]
    ) -> Iterator[Any]:
        def before(dbapi: Any) -> None:
            if self.dialect == "postgresql":
                _privileges_again(dbapi)
            else:
                dbapi.set_authorizer(schema_authorizer)
            prepare(dbapi)

        def after(dbapi: Any) -> None:
            try:
                restore(dbapi)
            finally:
                if self.dialect != "postgresql":
                    dbapi.set_authorizer(write_authorizer)

        with self._db._schema_transaction(timeout=timeout, prepare=before, restore=after) as transaction:
            yield transaction


def _privileges_again(dbapi: Any) -> None:
    """Decision 1A at the start of the run, on the session that runs it (GS3-28, review L-1 of PR #127). The check's
    read transaction is rolled back, so the run's own transaction starts afterwards."""
    import psycopg
    from psycopg.rows import tuple_row

    def fetch(sql: str) -> list[tuple[Any, ...]]:
        with psycopg.Cursor(dbapi, row_factory=tuple_row) as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    try:
        refusing = refusing_findings_now(fetch)
    finally:
        dbapi.rollback()
    if refusing:
        logger.warning(
            "the write role has superuser, BYPASSRLS, REPLICATION or server-file rights; nothing was changed",
            extra={"event": "studio.write_role_over_privileged", "code": "studio.write_role_over_privileged"},
        )
        raise WriteError("studio.write_role_over_privileged")


# --- the backup (section 7) ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupMade:
    path: Path
    size: int


def backup_folder(write_state: Path | None) -> Path | None:
    """``backups/schema-designer`` in the folder of ``write-areas.toml``; None without one (no backup: fail closed)."""
    return None if write_state is None else write_state.joinpath(*BACKUP_FOLDER)


def backup_sqlite(
    connection: WriteConnection, folder: Path | None, clock: Callable[[], datetime.datetime]
) -> BackupMade:
    """Copy the SQLite file through the checked write connection into ``folder`` (0700; the file 0600, never
    overwritten). Any failure is ``studio.schema_backup_failed``, and the run stops."""
    if folder is None:
        raise WriteError("studio.schema_backup_failed")
    try:
        ensure_private_dir(folder.parent)
        ensure_private_dir(folder)
        name = f"{clock().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.sqlite"
        with connection.raw_sqlite() as source:
            made = backup(source, folder / name, overwrite=False)
        connection.verify()  # still the file the unlock checked
    except WriteError:
        raise
    except Exception:
        logger.warning("schema change not run: the backup failed", extra={"event": "studio.schema_backup_failed"})
        raise WriteError("studio.schema_backup_failed") from None
    return BackupMade(path=made.path, size=made.bytes)


def _pg_connection(write: Any, read: Any, password: str | None) -> Connection:
    """The tool's connection: the read profile's server, database and ``sslmode``, the write profile's role and
    ``requirepeer`` (as the unlock's own connection), the password for the passfile only."""
    if read.database is None or write.user is None:
        raise WriteError("studio.write_coupling_invalid")
    return Connection(
        host=read.host,
        port=read.port,
        dbname=read.database,
        user=write.user,
        sslmode=read.sslmode,
        password=password,
        requirepeer=write.requirepeer,
    )


def backup_postgres(
    unlocks: Unlocks, unlock: Unlock, write_state: Path | None, clock: Callable[[], datetime.datetime]
) -> BackupMade:
    """The verified ``pg_dump`` archive of the unlocked PostgreSQL database before a run (see the module text). The
    run goes on only when this returns; every failure raises :class:`WriteError` and nothing that depends on the
    backup happens. Never a path, a name or server text in a log."""
    store = BackupStore(write_state, area=BACKUP_FOLDER[1], clock=clock)
    runner = Runner(write_state)
    try:
        server = unlock.connection.engine_version()
        tools = runner.tools()
        with runner.private_folder() as (scratch, _passfile):
            pg_dump = verify_tool("pg_dump", tools, scratch, at_least=server[0])
            pg_restore = verify_tool("pg_restore", tools, scratch, at_least=server[0])
        write, read, password = unlocks.unlock_credentials(unlock)
        target = _pg_connection(write, read, password)
        folder = store.folder()
        name = store.new_name("postgresql")
        archive = runner.dump(pg_dump, pg_restore, target, folder / name)
        meta = metadata(
            name=name,
            created=store.now(),
            engine="postgresql",
            origin="schema_designer",
            size=archive.size,
            sha256=archive.sha256,
            source={"database": target.dbname, "profile": unlock.read_name, "environment": unlock.environment},
            server_version=".".join(str(part) for part in server),
            system_identifier=None,
            tool=pg_dump.metadata(),
            fingerprint=None,
            extensions=archive.extensions,
        )
        try:
            store.write_metadata(name, meta)
        except WriteError:
            store.discard(name)  # a backup without its metadata is never kept
            raise
    except WriteError as exc:
        code = exc.code if exc.code in _BACKUP_CODES else "studio.schema_backup_failed"
        logger.warning(
            "schema change not run: the backup failed", extra={"event": "studio.schema_backup_failed", "code": code}
        )
        if code == exc.code:
            raise
        raise WriteError(code) from None
    except SecretError:
        raise  # the profile's secret: its own problem, as at the unlock
    except Exception:
        logger.warning("schema change not run: the backup failed", extra={"event": "studio.schema_backup_failed"})
        raise WriteError("studio.schema_backup_failed") from None
    return BackupMade(path=archive.path, size=archive.size)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# --- the run ------------------------------------------------------------------------------------------------------


@dataclass
class RunOutcome:
    plan_hash: str
    dry_run: bool
    steps_run: int
    fingerprint_after: str | None
    backup: str
    backup_path: str | None
    audit_pending: bool
    #: Set when the unlock must end after the answer (decision 1A).
    lock: bool = field(default=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_hash": self.plan_hash,
            "result": "rolled_back" if self.dry_run else "committed",
            "dry_run": self.dry_run,
            "steps_run": self.steps_run,
            "fingerprint_after": self.fingerprint_after,
            "backup": self.backup,
            "backup_path": self.backup_path,
            "audit_pending": self.audit_pending,
        }


def _audit_hash(plan_hash: str) -> str:
    """The hex part of tabrion's ``sha256:<hex>`` (the audit log takes hex only)."""
    value = plan_hash[len(_HASH_PREFIX) :] if plan_hash.startswith(_HASH_PREFIX) else plan_hash
    if not _HEX.fullmatch(value):  # pragma: no cover - tabrion's hash is always sha256:<64 hex>
        raise WriteError("studio.audit_record_invalid")
    return value


def failure(exc: TabrionError) -> Exception:
    """A tabrion error of the run as Studio's problem (section 11, GS3-20): drift and a different plan are
    ``studio.plan_drift``; refusals and usage errors fixed problems; a driver error ``db.<kind>`` (classification
    only); a failed check before the commit ``studio.schema_run_check_failed``; a lost ``COMMIT``
    ``studio.schema_run_outcome_unknown``. Never the server's text."""
    if isinstance(exc, SchemaDrift | PlanMismatch):
        return WriteError("studio.plan_drift")
    if isinstance(exc, PlanRefused):
        reason = exc.details.get("reason")
        if reason == "migration_managed":
            return WriteError("studio.schema_run_not_allowed", [("plan.reason", "plan.migration_managed")])
        if reason == "environment":
            return WriteError("studio.schema_run_not_allowed", [("plan.reason", "plan.environment")])
        if reason == "risk_not_allowed":
            return WriteError("studio.plan_drift")
        return refusal(exc)
    if isinstance(exc, PlanFailed):
        if exc.details.get("state") == "unknown":
            return WriteError("studio.schema_run_outcome_unknown")
        check = exc.details.get("check")
        if exc.classification is None:
            code = f"plan.{check}" if check in ("foreign_key_check", "integrity_check") else "plan.failed"
            return WriteError("studio.schema_run_check_failed", [("plan.check", code)])
        return DriverFailure(exc.classification)
    if isinstance(exc, UsageError):
        return refusal(exc)
    if exc.classification is not None:
        return DriverFailure(exc.classification)
    return WriteError("studio.schema_run_failed")


def run(
    unlocks: Unlocks,
    unlock: Unlock,
    plan: RunPlan,
    *,
    dry_run: bool,
    write_state: Path | None,
    clock: Callable[[], datetime.datetime] = _utc_now,
) -> RunOutcome:
    """Record ``run.start``, make the backup, run the plan through ``execute_plan`` and record ``run.result`` (the
    caller checked the binding, the confirmation, the conditions and the re-plan; see the module text)."""
    confirmation = plan.confirmation
    if confirmation is None:  # pragma: no cover - the caller checks
        raise WriteError("studio.plan_not_confirmed")
    if dry_run:
        backup_state = "dry_run"
    elif confirmation.backup == "skip":
        backup_state = "opted_out"
    elif confirmation.backup == "make":
        backup_state = "made"
    else:
        backup_state = "not_needed"
    common: dict[str, object] = {
        "run": secrets.token_hex(8),
        "area": Area.SCHEMA_DESIGNER.value,
        "action": ACTION_DRY_RUN if dry_run else ACTION_RUN,
        "profile": unlock.write_name,
    }
    try:
        unlocks.audit.append(
            "run.start",
            **common,
            kind=unlock.kind,
            environment=unlock.environment,
            engine=unlock.connection.engine,
            connection=unlock.connection_id,
            objects=plan.tables,
            plan_hash=_audit_hash(plan.plan_hash),
            risks=sorted(plan.risks),
            steps=len(plan.steps),
            backup=backup_state,
            dry_run=dry_run,
        )
    except AuditError:
        logger.warning("schema change not run: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    except ValueError:
        logger.warning("schema change not run: audit record not valid", extra={"event": "studio.audit_record_invalid"})
        raise WriteError("studio.audit_record_invalid") from None
    result = "failed"
    outcome: RunOutcome | None = None
    try:
        made: BackupMade | None = None
        if backup_state == "made" and unlock.connection.engine == "postgresql":
            made = backup_postgres(unlocks, unlock, write_state, clock)
        elif backup_state == "made":
            made = backup_sqlite(unlock.connection, backup_folder(write_state), clock)
        try:
            found = execute_plan(
                cast(Any, _RunDatabase(unlock.connection)),
                plan.target,
                plan_hash=plan.plan_hash,
                expected_fingerprint=plan.source_fingerprint,
                environment=cast(Any, unlock.environment),
                allow=confirmation.classes,
                schemas=[plan.schema],
                server_version=plan.server_version,
                concurrent_indexes=False,
                dry_run=dry_run,
                lock_timeout=LOCK_TIMEOUT_S,
                timeout=unlock.connection.timeout_s,
            )
        except TabrionError as exc:
            problem = failure(exc)
            kind = exc.classification.kind.value if exc.classification is not None else None
            logger.info(
                "schema change not run",
                extra={"event": "studio.schema_run_failed", "code": getattr(problem, "code", None), "kind": kind},
            )
            raise problem from None
        outcome = RunOutcome(
            plan_hash=plan.plan_hash,
            dry_run=dry_run,
            steps_run=found.steps_run,
            fingerprint_after=found.fingerprint_after,
            backup=backup_state,
            backup_path=None if made is None else str(made.path),
            audit_pending=False,
        )
        result = "dry_run" if dry_run else "committed"
    except WriteError as exc:
        result = exc.code
        raise
    except DriverFailure as exc:
        result = f"db.{exc.classification.kind.value}"
        raise
    finally:
        pending = not unlocks.audit.record(
            "run.result", **common, result=result, steps=0 if outcome is None else outcome.steps_run
        )
        if result == "studio.write_role_over_privileged":
            unlocks.lock(unlock.session, unlock.connection_id)  # 1A: the unlock ends with the refusal
    if outcome is None:  # pragma: no cover - every failure raised above
        raise WriteError("studio.schema_run_failed")
    outcome.audit_pending = pending
    logger.info(
        "schema change dry run" if dry_run else "schema change run",
        extra={"event": "studio.schema_dry_run" if dry_run else "studio.schema_run"},
    )
    return outcome
