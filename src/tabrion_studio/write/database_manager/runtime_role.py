"""The runtime-role wizard (STU-CR-6 part 2; STU-ADR-0007 section 12, STU-ADR-0008 section 6; TX-30, TX-40..TX-44;
GS3-22).

A runtime role is the role an application connects as: it may read and write the rows of one schema, never its
structure, and the migration bookkeeping stays read-only for it. The wizard creates such a role (or takes an existing
one again, for example after a migration added tables) and grants it its privileges through Tabrion:
``tabrion.postgres.create_role`` (``LOGIN``, nothing else) and ``tabrion.postgres.grant_runtime_role`` (``USAGE`` on
the schema; ``SELECT, INSERT, UPDATE, DELETE`` on its tables, ``USAGE, SELECT`` on its sequences, ``EXECUTE`` on its
functions, the same as the admin role's default privileges in the schema; ``REVOKE`` of every write on the protected
tables; ``CONNECT`` on the database). Studio builds no role statement of its own.

**Where it runs (ADR 0007 section 12).** Role DDL needs an admin profile, and an admin profile is unlocked only in the
Database Manager's area. So the wizard is part of the Database Manager's routes, runs on this session's **admin**
unlock of a PostgreSQL connection, and is open only while **both** the ``schema_designer`` and the
``database_manager`` flags are on (the route answers 405 otherwise and imports nothing). The Schema Designer has no
admin unlock of its own.

**The rules of ADR 0008 section 6, from** :mod:`tabrion_studio.write.database_manager.roles` **(never a second
copy):** :func:`~roles.server_checks` (the admin unlock, DEV or TEST, PostgreSQL 16 or later, no PROD alias of the
server, the admin role's own check, which ends the unlock), :func:`~roles.own_roles` (Studio never makes or widens one
of its own roles, nor a role one of them reaches: ``own_role``, ``own_role_reached``, ``own_role_unknown``),
:func:`~roles.read_privileges` (what the role can do, read by Tabrion; the role's reachable SUPERUSER, BYPASSRLS,
CREATEROLE, REPLICATION or server-access rights refuse), :func:`~roles.k2_scan` (an existing role's TRIGGER, EXECUTE
on a foreign SECURITY DEFINER function or CREATE on a schema in any database it reaches refuses), and
:func:`~roles.display_name` and :func:`~roles.refusal` for every name and every refusal of Tabrion.

**The wizard's own checks (fail closed):**

* the schema exists and is not a system schema (``pg_*``, ``information_schema``: ``role.system_object``);
* the admin role can act as the owner of the schema and of every table, sequence and function in it (``USAGE`` of
  their owners), or the grants would fail half-way (``role.not_owner``);
* no function of the schema is SECURITY DEFINER: ``grant_runtime_role`` grants ``EXECUTE`` on every function, and
  EXECUTE on a SECURITY DEFINER function is a K-2 escalation (``role.security_definer``);
* a protected table that does not exist in the schema is named as a warning (``protected_missing``); default
  privileges cover only tables the admin role creates, so the preview always says to run the wizard again after a
  migration (``rerun_after_migration``).

**Preview, binding and confirmation.** :func:`preview` changes nothing and returns ``state``, a hash of everything it
read; :func:`apply` reads it all again, refuses a difference (``studio.dbm_role_changed``) and needs the role's exact
name typed (``studio.dbm_role_name_mismatch``). Tabrion reads the role again inside its grant transaction and rolls
back a new finding. When the role was created by this run and the grant then fails, the wizard drops it again
(``undone`` in the audit record), so a failed run leaves no half-made role behind where it can.

**Audit (ADR 0008 section 9).** ``run.start`` (no record, no change) and ``run.result`` with the action
``dbm.role.runtime``, the escaped role, schema and protected-table names, whether the role was created and whether a
password was set; never a password. The password goes straight from the request into Tabrion (a SCRAM verifier made
on the client) and is never in a preview, an answer, the audit log, the log or a problem.

Imported only by :mod:`tabrion_studio.write.routes` after it checked both flags.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Any

from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.database_manager import backups, roles
from tabrion_studio.write.database_manager.backups import Manager
from tabrion_studio.write.unlock import Unlock

logger = logging.getLogger("tabrion_studio.write")

ACTION = "dbm.role.runtime"
#: Protected tables a request may name (the migration bookkeeping: ``alembic_version`` and the like).
MAX_PROTECTED = 16
#: What the wizard needs to know about the schema, read on the admin session (changes nothing): whether it exists,
#: whether the admin role reaches its owner, how many of its objects have an owner the admin role does not reach, how
#: many of its functions are SECURITY DEFINER, and how many tables it has.
_SCHEMA = (
    "SELECT pg_catalog.pg_has_role(CURRENT_USER, n.nspowner, 'USAGE'),"
    " (SELECT pg_catalog.count(*) FROM pg_catalog.pg_class c"
    "   WHERE c.relnamespace OPERATOR(pg_catalog.=) n.oid"
    "   AND c.relkind OPERATOR(pg_catalog.=) ANY(ARRAY['r','p','v','m','f','S']::pg_catalog.\"char\"[])"
    "   AND NOT pg_catalog.pg_has_role(CURRENT_USER, c.relowner, 'USAGE'))::pg_catalog.int8,"
    " (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc p"
    "   WHERE p.pronamespace OPERATOR(pg_catalog.=) n.oid"
    "   AND NOT pg_catalog.pg_has_role(CURRENT_USER, p.proowner, 'USAGE'))::pg_catalog.int8,"
    " (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc p"
    "   WHERE p.pronamespace OPERATOR(pg_catalog.=) n.oid AND p.prosecdef)::pg_catalog.int8,"
    " (SELECT pg_catalog.count(*) FROM pg_catalog.pg_class c"
    "   WHERE c.relnamespace OPERATOR(pg_catalog.=) n.oid"
    "   AND c.relkind OPERATOR(pg_catalog.=) ANY(ARRAY['r','p']::pg_catalog.\"char\"[]))::pg_catalog.int8"
    " FROM pg_catalog.pg_namespace n"
    " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) CAST(:schema AS pg_catalog.text)"
)
#: The protected tables that exist in the schema (by exact name, as Tabrion looks them up).
_PROTECTED = (
    "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n"
    " ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"
    " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) CAST(:schema AS pg_catalog.text)"
    " AND c.relname::pg_catalog.text OPERATOR(pg_catalog.=) ANY(CAST(:names AS pg_catalog.text[]))"
    " ORDER BY 1"
)


class Request:
    """One wizard run, checked as far as it can be without the server."""

    def __init__(self, doc: dict[str, Any], *, with_password: bool) -> None:
        self.role = roles._role_name(doc.get("role"))
        self.schema = roles._role_name(doc.get("schema"), "schema")
        create = doc.get("create")
        if type(create) is not bool:
            raise WriteError("studio.dbm_role_invalid", [("role.create", "role.create_invalid")])
        self.create = create
        raw = doc.get("protected_tables", [])
        if not isinstance(raw, list) or len(raw) > MAX_PROTECTED:
            raise WriteError("studio.dbm_role_invalid", [("role.protected_tables", "role.objects_invalid")])
        protected: list[str] = []
        for item in raw:
            name = roles._role_name(item, "protected_tables")
            if name not in protected:
                protected.append(name)
        self.protected = tuple(protected)
        self.password: str | None = None
        if with_password:
            self.password = roles._password(doc.get("password"))
            if self.password is not None and not create:
                # An existing role keeps its password; changing it is the role panel's "alter".
                raise WriteError("studio.dbm_role_invalid", [("role.password", "role.not_for_action")])

    def described(self) -> dict[str, Any]:
        """The request without the password, every name escaped: for the preview's hash and the answer."""
        return {
            "role": roles.display_name(self.role),
            "schema": roles.display_name(self.schema),
            "protected_tables": [roles.display_name(t) for t in self.protected],
            "create": self.create,
        }

    def audited(self, *, password_set: bool) -> dict[str, Any]:
        """The fields of the ``run.start`` record: names and flags only; never a password."""
        return {
            "objects": [roles.display_name(self.role)],
            "schema": roles.display_name(self.schema),
            "protected": [roles.display_name(t) for t in self.protected],
            "created": self.create,
            "password_set": password_set,
        }


def _schema_facts(unlock: Unlock, request: Request) -> dict[str, Any] | None:
    """What the grant would touch in the schema (counts only); ``None`` when the schema does not exist."""
    rows = unlock.connection.fetch(_SCHEMA, {"schema": request.schema})
    if not rows or not rows[0]:
        return None
    reaches_owner, foreign_relations, foreign_functions, definers, tables = rows[0]
    found = unlock.connection.fetch(_PROTECTED, {"schema": request.schema, "names": list(request.protected)})
    present = {str(row[0]) for row in found if row and isinstance(row[0], str)}
    return {
        "owner_reached": bool(reaches_owner),
        "foreign_owned": int(foreign_relations or 0) + int(foreign_functions or 0),
        "security_definers": int(definers or 0),
        "tables": int(tables or 0),
        "protected_found": [roles.display_name(t) for t in request.protected if t in present],
        "protected_missing": [roles.display_name(t) for t in request.protected if t not in present],
    }


def _state(request: Request, target: Any, k2: Any, schema: Any) -> str:
    doc = {"request": request.described(), "target": target, "k2": k2, "schema": schema}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read(manager: Manager, unlock: Unlock, request: Request) -> dict[str, Any]:
    """Everything the preview shows and the apply compares, read now on the admin session (changes nothing)."""
    own = roles.own_roles(manager, unlock)
    refusals: list[str] = []
    warnings = ["rerun_after_migration"]
    if request.role in own:
        refusals.append("own_role")
    if own.unknown:
        refusals.append("own_role_unknown")
    if request.role.startswith("pg_"):
        refusals.append("predefined_role")
    if request.schema.startswith("pg_") or request.schema == "information_schema":
        refusals.append("system_object")
    with roles._session(unlock) as connection:
        target = roles.read_privileges(connection, request.role)
    k2: dict[str, Any] | None = None
    if request.create:
        if target is not None:
            refusals.append("exists")
    elif target is None:
        raise WriteError("studio.dbm_role_missing")
    else:
        if roles._reached_by_own(unlock, own, request.role):
            refusals.append("own_role_reached")
        refusals.extend(roles._unsafe(target, own=request.role))
        if target["broad"]:
            refusals.append("predefined_role")
        k2 = roles.k2_scan(manager, unlock, request.role)
        if k2["found"]:
            refusals.append("escalation_grant")
        if k2["unchecked"] or k2["no_connect"]:
            warnings.append("escalation_check_partial")
        if target["public_definers"]:
            warnings.append(
                "public_definers_superuser"
                if any(e.startswith("superuser_owner:") for e in target["public_definers"])
                else "public_definers"
            )
    schema = _schema_facts(unlock, request)
    if schema is None:
        raise WriteError("studio.dbm_role_object_missing", [("role.schema", "role.object_missing")])
    if not schema["owner_reached"] or schema["foreign_owned"]:
        refusals.append("not_owner")
    if schema["security_definers"]:
        refusals.append("security_definer")
    if schema["protected_missing"]:
        warnings.append("protected_missing")
    if not schema["tables"]:
        warnings.append("no_tables")
    database_rows = unlock.connection.fetch(roles._CURRENT_DATABASE)
    database = str(database_rows[0][0]) if database_rows and database_rows[0] else ""
    return {
        "action": "runtime",
        "request": request.described(),
        "environment": unlock.environment,
        "database": roles.display_name(database),
        "target": target,
        "k2": k2,
        "schema": schema,
        "refusals": sorted(set(refusals)),
        "warnings": sorted(set(warnings)),
        "typed": roles.display_name(request.role),
        "state": _state(request, target, k2, schema),
    }


def preview(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """What the wizard would do and what refuses it, read now; changes nothing. Raises :class:`WriteError` or
    :class:`~tabrion_studio.write.database_manager.backups.DriverFailure`."""
    unlock = roles.server_checks(manager, session, doc)
    return _read(manager, unlock, Request(doc, with_password=False))


def _undo(connection: Any, role: str) -> bool:
    """Drop the role this run created after its grant failed; whether that worked (never raises)."""
    from tabrion import postgres

    try:
        roles._translate(lambda: postgres.drop_role(connection, role))
    except Exception:  # the role stays; the answer is the grant's refusal, the audit record says not undone
        logger.warning("runtime role not dropped after a failed grant", extra={"event": "studio.dbm_role_undo"})
        return False
    return True


def apply(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Create the role (when asked) and grant its runtime privileges through Tabrion, after the checks of
    :func:`preview`, the same ``state`` and the typed name; read it again after the commit."""
    from tabrion import postgres

    unlock = roles.server_checks(manager, session, doc)
    request = Request(doc, with_password=True)
    password = request.password
    request.password = None  # I-76: one reference, handed to Tabrion only
    roles._typed(doc.get("typed"), request.role)
    seen = _read(manager, unlock, request)
    if not isinstance(doc.get("state"), str) or not secrets.compare_digest(doc["state"], seen["state"]):
        raise WriteError("studio.dbm_role_changed")
    if seen["refusals"]:
        raise WriteError(
            "studio.dbm_role_not_allowed"
            if {"own_role", "own_role_reached", "own_role_unknown"} & set(seen["refusals"])
            else "studio.dbm_role_unsafe",
            [("role.target", f"role.{r}" if r in roles.REASONS else "role.refused") for r in seen["refusals"]][:16],
        )
    with backups._audited(manager, unlock, ACTION, request.audited(password_set=password is not None)) as outcome:
        with roles._session(unlock) as connection:
            roles.admin_session_check(connection)
            created = False
            try:
                if request.create:
                    options = postgres.RoleOptions(login=True)
                    roles._translate(
                        lambda: postgres.create_role(connection, request.role, options=options, password=password)
                    )
                    created = True
            finally:
                password = None
            try:
                report = roles._translate(
                    lambda: postgres.grant_runtime_role(
                        connection, request.role, schema=request.schema, protected_tables=request.protected
                    )
                )
            except BaseException:
                if created:
                    outcome["undone"] = _undo(connection, request.role)
                raise
            after = roles.read_privileges(connection, request.role)
        outcome.update(result="changed", connect=bool(report.connect))
    logger.info("runtime role granted", extra={"event": "studio.dbm_role", "action": "runtime"})
    return {
        "action": "runtime",
        "role": roles.display_name(request.role),
        "schema": roles.display_name(request.schema),
        "created": request.create,
        "connect": bool(report.connect),
        "can_create": bool(report.can_create),
        "tables": int(report.tables),
        "after": after,
        "audit_pending": outcome["audit_pending"],
    }
