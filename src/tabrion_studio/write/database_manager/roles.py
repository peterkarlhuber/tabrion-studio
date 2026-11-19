"""PostgreSQL role management in the Database Manager (STU-XDM-4; STU-ADR-0008 sections 2, 6, 7 and 9; TX-40..TX-44;
GS4-11..GS4-18, GS4-22).

Imported only by :mod:`tabrion_studio.write.routes` after it checked the ``database_manager`` flag (GS4-01).

**Who and where (E-X6, E-X7).** As every action of the area: this session's unlock of a PostgreSQL connection with an
**admin** profile (``kind = "admin"``; a write profile never serves this area), re-checked at every use, and the admin
role's privileges read again before anything is read or changed (section 6, ``studio.admin_over_privileged``, which
ends the unlock). Only when the unlock's stored environment is ``dev`` or ``test``, and never on a server that any
PROD profile of the user's store names (by address and port, or by ``system_identifier`` when that profile is open):
role changes are cluster-wide, so a PROD alias of the same server is refused too (``role.prod``; the server matching of
:mod:`tabrion_studio.write.prod_targets`, for any database). PostgreSQL 16 or
later only (``studio.dbm_role_requires_16``; before 16 CREATEROLE is superuser-equivalent, D-1). SQLite has no roles.

**Only through Tabrion.** Every change is one call of tabrion's stable role DDL (``create_role``, ``alter_role``,
``drop_role``, ``grant_membership``, ``revoke_membership``, ``grant_privileges``, ``revoke_privileges``), which has no
parameter for SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION, ``ADMIN OPTION`` or ``GRANT OPTION``, reads the target in
its own transaction right before and after the statement and rolls back a new finding (I-182). Studio builds no role
statement of its own and never concatenates SQL.

**Studio's own checks on top (fail closed).**

* **Own roles:** Studio never changes, drops, grants to or revokes from its own roles: the session's ``CURRENT_USER``
  and ``SESSION_USER`` (the admin role), every role a profile or write/admin profile names (for a profile without
  ``user`` the OS user libpq takes), and the roles of the open read connections (``role.own_role``; when a profile's
  role cannot be known, every change is refused: ``role.own_role_unknown``, review I-2 of #149). Nor a role one of
  them reaches through INHERIT or SET (a group above Studio's own role): every action but create is refused
  (``role.own_role_reached``, review L-1 of #149). Studio never sets up rights for itself (GS4-13).
* **Privileged roles:** a membership in a predefined role (``pg_*``, among them ``pg_read_server_files``,
  ``pg_write_server_files`` and ``pg_execute_server_program``), or in a role that has or reaches SUPERUSER, BYPASSRLS,
  CREATEROLE, REPLICATION, a server-access role or any predefined role, is refused before Tabrion is called
  (``role.privileged_role``); Tabrion refuses it again in its transaction.
* **K-2 in every database (P90-04):** before a membership is granted, the granted role's escalation grants (TRIGGER,
  EXECUTE on a SECURITY DEFINER function it does not own, CREATE on ``public`` or a foreign schema) are read in every
  database the admin profile may connect to; any of them refuses (``role.escalation_grant``). Databases it could
  not connect to are named in the preview: the check covers only the databases listed. It runs before the change,
  outside Tabrion's transaction: a K-2 grant made there by a concurrent privileged session in between is not seen
  (review I-1 of #149).
* **Names (P90-05):** every name Studio shows or audits is escaped (control, format, separator and other invisible
  characters as ``<U+XXXX>``); an action on a name that holds one is refused, since it cannot be confirmed by typing
  what the user sees (``studio.dbm_role_name_invalid``).
* **Preview, binding and confirmation (D-13, D-14):** :func:`preview` changes nothing and shows the target's (and the
  member's) privileges as Tabrion reads them, the refusals Studio can already see, the warnings (``public_definers``
  with ``superuser_owner`` first; CREATE on a database lets the grantee install trusted extensions, P90-03, P90-07)
  and the K-2 coverage, and returns ``state``, a hash of what it read. :func:`apply` reads the same again and
  refuses when it differs (``studio.dbm_role_changed``), and needs the exact typed name of the role whose rights
  change (``studio.dbm_role_name_mismatch``). Every change is confirmed so, not only the destructive ones.
* **After the commit (P90-06):** the role is read again on a fresh read and any difference to what Tabrion returned
  from inside its transaction is answered (``differences``): another session may have changed it in between.

**Passwords.** Only for ``create`` and ``alter``, straight from the request into Tabrion (a client-side SCRAM verifier;
the clear text never reaches the server, I-76). Never in the preview, an answer, the audit log, the log or a problem.

**Errors (section 7).** Tabrion's refusals become fixed problems with fixed reasons (``studio.dbm_role_unsafe`` with
``role.<reason>`` issues, ``studio.dbm_role_missing``, ``studio.dbm_role_exists``, ...); a driver error is a
``db.<kind>`` problem from ``classify`` with only ``safe_message`` in the log. Never ``str()`` of an exception or the
server's text.

**Audit (section 9).** ``run.start`` (fail closed: no record, no change) and ``run.result`` with the action
``dbm.role.<action>``, the escaped role and member names, the privileges, object kind and escaped objects, the option
fields and whether a password was set; never a password.

**Shared with STU-CR-6.** The runtime-role wizard (STU-CR-6, TB-148) runs under the same flag and admin profile
(STU-ADR-0007 section 12) and is to reuse :func:`display_name`, :func:`server_checks`, :func:`own_roles`,
:func:`read_privileges`, :func:`k2_scan` and :func:`refusal` of this module for its preview and its checks; only its
call of ``tabrion.postgres.grant_runtime_role`` is its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import datetime
import getpass
import hashlib
import json
import logging
import secrets
from typing import Any
import unicodedata

from tabrion.errors import PermissionDenied, RoleMissing, RoleUnsafe, TabrionError, UsageError, classify, safe_message

from tabrion_studio.config.schema import Profile
from tabrion_studio.write import prod_targets
from tabrion_studio.write.connection import WriteError, admin_refusing_findings, privileges_now
from tabrion_studio.write.database_manager import backups
from tabrion_studio.write.database_manager.backups import DriverFailure, Manager
from tabrion_studio.write.unlock import Unlock

logger = logging.getLogger("tabrion_studio.write")

#: The stored environments role management may act on (E-X7): nothing else.
ROLE_ENVIRONMENTS = frozenset({"dev", "test"})
#: PostgreSQL 16: role DDL before it is refused (D-1).
ROLE_MIN_VERSION = (16,)
ACTIONS = (
    "create",
    "alter",
    "drop",
    "grant_membership",
    "revoke_membership",
    "grant_privileges",
    "revoke_privileges",
)
#: The actions that add rights to a role: the target may not have or reach a banned attribute.
INCREASING = frozenset({"create", "alter", "grant_membership", "grant_privileges"})
OBJECT_KINDS = ("table", "sequence", "function", "schema", "database")
PRIVILEGES = {
    "table": ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "sequence": ("USAGE", "SELECT", "UPDATE"),
    "function": ("EXECUTE",),
    "schema": ("USAGE", "CREATE"),
    "database": ("CONNECT", "CREATE"),
}
#: Option fields of :class:`tabrion.postgres.RoleOptions` a request may set; nothing else.
OPTION_FIELDS = frozenset({"login", "inherit", "create_db", "connection_limit", "valid_until"})
#: Option names that ask for a banned attribute: refused with their own problem (E-X6).
BANNED_OPTIONS = frozenset(
    {"superuser", "bypassrls", "bypass_rls", "createrole", "create_role", "replication", "admin_option", "grant_option"}
)
#: The superuser-equivalent predefined roles (P90-02); every other ``pg_`` role is refused as predefined too.
SERVER_ACCESS_ROLES = ("pg_execute_server_program", "pg_read_server_files", "pg_write_server_files")
#: The reasons of tabrion's ``RoleUnsafe`` (and Studio's own) that become ``role.<reason>`` issues; others are
#: answered as ``role.refused``.
REASONS = frozenset(
    {
        "superuser",
        "bypass_rls",
        "createrole",
        "replication",
        "reachable_superuser",
        "reachable_bypass_rls",
        "reachable_createrole",
        "reachable_replication",
        "reachable_server_access",
        "server_access",
        "owns_objects",
        "current_user",
        "predefined_role",
        "trigger",
        "security_definer",
        "schema_create",
        "system_object",
        "object_owner",
        "own_role",
        "own_role_reached",
        "own_role_unknown",
        "privileged_role",
        "escalation_grant",
        "exists",
    }
)
_UNSAFE_KINDS = ("superuser", "bypass_rls", "createrole", "replication", "server_access")
_K2_KINDS = ("trigger", "security_definer", "schema_create")
_MAX_NAME_BYTES = 63
_MAX_PASSWORD_CHARS = 1024
_MAX_OBJECTS = 64
_MAX_OBJECT_CHARS = 512
_MAX_ROLES = 2000
_MAX_K2_DATABASES = 64
_FIRST_NORMAL_OID = 16384
_ROLES = (
    "SELECT r.rolname::pg_catalog.text, r.rolcanlogin, r.rolinherit, r.rolcreatedb, r.rolcreaterole,"
    " r.rolreplication, r.rolsuper, r.rolbypassrls, r.rolconnlimit, r.rolvaliduntil FROM pg_catalog.pg_roles r"
    " WHERE r.oid OPERATOR(pg_catalog.>=) 16384 ORDER BY 1 LIMIT 2001"
)
_SESSION_ROLES = "SELECT CURRENT_USER::pg_catalog.text, SESSION_USER::pg_catalog.text"
_DATABASES = (
    "SELECT d.datname::pg_catalog.text FROM pg_catalog.pg_database d"
    " WHERE d.datallowconn AND NOT d.datistemplate"
    " AND pg_catalog.has_database_privilege(d.oid, 'CONNECT') ORDER BY 1"
)
_OTHER_DATABASES = (
    "SELECT pg_catalog.count(*)::pg_catalog.int8 FROM pg_catalog.pg_database d"
    " WHERE d.datallowconn AND NOT d.datistemplate"
    " AND NOT pg_catalog.has_database_privilege(d.oid, 'CONNECT')"
)
_CURRENT_DATABASE = "SELECT pg_catalog.current_database()::pg_catalog.text"
#: Whether one of Studio's own roles reaches the target through any chain of memberships that passes its rights on
#: (INHERIT) or lets it switch to it (SET) (review L-1 of #149). A membership with only the ADMIN option, as
#: PostgreSQL 16 gives the admin role for every role it creates, passes nothing on.
_REACHERS = (
    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles o, pg_catalog.pg_roles t"
    " WHERE o.rolname OPERATOR(pg_catalog.=) ANY(CAST(:own AS pg_catalog.text[]))"
    " AND t.rolname OPERATOR(pg_catalog.=) CAST(:target AS pg_catalog.text)"
    " AND o.oid OPERATOR(pg_catalog.<>) t.oid"
    " AND (pg_catalog.pg_has_role(o.oid, t.oid, 'USAGE') OR pg_catalog.pg_has_role(o.oid, t.oid, 'SET')))"
)
_ATTRIBUTE_NAMES = ("login", "inherit", "createdb", "createrole", "replication", "superuser", "bypass_rls")


# --- names ----------------------------------------------------------------------------------------------------------


def _hidden(char: str) -> bool:
    category = unicodedata.category(char)
    return category in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") or (category == "Zs" and char != " ")


def display_name(name: str) -> str:
    """``name`` with every character the user cannot see as ``<U+XXXX>`` (P90-05): control, format (bidirectional
    overrides among them), surrogate, private-use and unassigned characters, line and paragraph separators and any
    space other than U+0020. For the page, the preview, the audit log and the log."""
    return "".join(f"<U+{ord(char):04X}>" if _hidden(char) else char for char in name)


def _role_name(raw: object, field: str = "role") -> str:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise WriteError("studio.dbm_role_name_invalid", [(f"role.{field}", "role.name_invalid")])
    if any(_hidden(char) for char in raw):
        raise WriteError("studio.dbm_role_name_invalid", [(f"role.{field}", "role.name_hidden")])
    if len(raw.encode("utf-8")) > _MAX_NAME_BYTES or raw != raw.strip():
        raise WriteError("studio.dbm_role_name_invalid", [(f"role.{field}", "role.name_invalid")])
    return raw


# --- the request ------------------------------------------------------------------------------------------------------


def _options(raw: object) -> Any:
    """``RoleOptions`` from the request's ``options`` (only for ``create`` and ``alter``)."""
    from tabrion.postgres import RoleOptions

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WriteError("studio.dbm_role_invalid", [("role.options", "role.options_invalid")])
    if any(key in BANNED_OPTIONS for key in raw):
        raise WriteError("studio.dbm_role_attribute_banned", [("role.options", "role.attribute_banned")])
    if not set(raw) <= OPTION_FIELDS:
        raise WriteError("studio.dbm_role_invalid", [("role.options", "role.options_invalid")])
    fields: dict[str, Any] = {}
    for key in ("login", "inherit", "create_db"):
        if key in raw:
            if type(raw[key]) is not bool:
                raise WriteError("studio.dbm_role_invalid", [("role.options", "role.options_invalid")])
            fields[key] = raw[key]
    limit = raw.get("connection_limit")
    if limit is not None:
        if type(limit) is not int or not 0 <= limit <= 2_147_483_647:
            raise WriteError("studio.dbm_role_invalid", [("role.options", "role.connection_limit")])
        fields["connection_limit"] = limit
    until = raw.get("valid_until")
    if until is not None:
        try:
            parsed = datetime.datetime.fromisoformat(until) if isinstance(until, str) and len(until) <= 40 else None
        except ValueError:
            parsed = None
        if parsed is None or parsed.utcoffset() is None:
            raise WriteError("studio.dbm_role_invalid", [("role.options", "role.valid_until")])
        fields["valid_until"] = parsed
    return RoleOptions(**fields)


def _password(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw or len(raw) > _MAX_PASSWORD_CHARS or "\x00" in raw:
        raise WriteError("studio.dbm_role_invalid", [("role.password", "role.password_invalid")])
    return raw


def _privilege_list(raw: object, on: str) -> tuple[str, ...]:
    allowed = PRIVILEGES[on]
    if not isinstance(raw, list) or not raw or len(raw) > len(allowed):
        raise WriteError("studio.dbm_role_invalid", [("role.privileges", "role.privileges_invalid")])
    chosen: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in allowed or item in chosen:
            raise WriteError("studio.dbm_role_invalid", [("role.privileges", "role.privileges_invalid")])
        chosen.append(item)
    return tuple(name for name in allowed if name in chosen)


def _objects(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_OBJECTS:
        raise WriteError("studio.dbm_role_invalid", [("role.objects", "role.objects_invalid")])
    found: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or len(item) > _MAX_OBJECT_CHARS or any(_hidden(c) for c in item):
            raise WriteError("studio.dbm_role_invalid", [("role.objects", "role.objects_invalid")])
        found.append(item)
    return tuple(found)


class Request:
    """One role action, checked as far as it can be without the server."""

    def __init__(self, doc: dict[str, Any], *, with_password: bool) -> None:
        action = doc.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            raise WriteError("studio.dbm_role_invalid", [("role.action", "role.action_invalid")])
        self.action = action
        self.role = _role_name(doc.get("role"))
        self.member: str | None = None
        self.options: Any = None
        self.password: str | None = None
        self.on: str | None = None
        self.privileges: tuple[str, ...] = ()
        self.objects: tuple[str, ...] = ()
        self.options_set: tuple[str, ...] = ()
        allowed = {"action", "role"}
        if action in ("grant_membership", "revoke_membership"):
            self.member = _role_name(doc.get("member"), "member")
            if self.member == self.role:
                raise WriteError("studio.dbm_role_invalid", [("role.member", "role.member_is_role")])
            allowed.add("member")
        if action in ("create", "alter"):
            self.options = _options(doc.get("options"))
            if with_password:
                self.password = _password(doc.get("password"))
            if action == "alter" and self.options is None and doc.get("password") is None:
                raise WriteError("studio.dbm_role_invalid", [("role.options", "role.nothing_to_change")])
            if action == "alter" and self.options is not None and set(doc["options"]) != OPTION_FIELDS:
                # Tabrion's alter_role sets every field: a field left out would silently fall back to its default.
                raise WriteError("studio.dbm_role_invalid", [("role.options", "role.options_incomplete")])
            if self.options is not None:
                self.options_set = tuple(sorted(doc["options"]))
            allowed |= {"options", "password"}
        if action in ("grant_privileges", "revoke_privileges"):
            on = doc.get("on")
            if not isinstance(on, str) or on not in OBJECT_KINDS:
                raise WriteError("studio.dbm_role_invalid", [("role.on", "role.on_invalid")])
            self.on = on
            self.privileges = _privilege_list(doc.get("privileges"), on)
            if action == "grant_privileges" and "TRIGGER" in self.privileges:
                raise WriteError("studio.dbm_role_unsafe", [("role.privileges", "role.trigger")])
            self.objects = _objects(doc.get("objects"))
            allowed |= {"on", "privileges", "objects"}
        for key in ("member", "options", "password", "on", "privileges", "objects"):
            if key not in allowed and doc.get(key) is not None:
                raise WriteError("studio.dbm_role_invalid", [(f"role.{key}", "role.not_for_action")])

    @property
    def confirm(self) -> str:
        """The role whose rights change: the name the user types (a membership changes the member's rights)."""
        return self.member if self.member is not None else self.role

    def audited(self, *, password_set: bool) -> dict[str, Any]:
        """The fields of the ``run.start`` record: names, kinds and option names; never a password or option value."""
        doc: dict[str, Any] = {"objects": [display_name(self.role)], "password_set": password_set}
        if self.member is not None:
            doc["member"] = display_name(self.member)
        if self.options is not None:
            doc["options_set"] = list(self.options_set)
        if self.on is not None:
            doc |= {
                "on": self.on,
                "privileges": list(self.privileges),
                "grant_objects": [display_name(o) for o in self.objects],
            }
        return doc

    def described(self) -> dict[str, Any]:
        """The request without the password, every name escaped: for the preview's hash and the audit log."""
        doc: dict[str, Any] = {"action": self.action, "role": display_name(self.role)}
        if self.member is not None:
            doc["member"] = display_name(self.member)
        if self.options is not None:
            doc["options"] = {
                "login": self.options.login,
                "inherit": self.options.inherit,
                "create_db": self.options.create_db,
                "connection_limit": self.options.connection_limit,
                "valid_until": None if self.options.valid_until is None else self.options.valid_until.isoformat(),
            }
        if self.on is not None:
            doc |= {
                "on": self.on,
                "privileges": list(self.privileges),
                "objects": [display_name(o) for o in self.objects],
            }
        return doc


# --- the server -----------------------------------------------------------------------------------------------------


def _not_allowed(reason: str) -> WriteError:
    return WriteError("studio.dbm_role_not_allowed", [("role.target", f"role.{reason}")])


def _read_profile(manager: Manager, unlock: Unlock) -> Profile:
    _write, read, _password = manager.unlocks.credentials(unlock.write_name)
    return read


def _prod_server(manager: Manager, unlock: Unlock, read: Profile) -> bool:
    """Whether a PROD profile of the user's store names this server (any database: roles are the cluster's).

    The server matching of :mod:`tabrion_studio.write.prod_targets` (the same port and overlapping addresses, or the
    same ``system_identifier``), without its database-name check, which :func:`prod_targets.names_prod` adds."""
    here = prod_targets.addresses(read.host, read.port)
    port = read.port or 5432
    identifier = prod_targets.system_identifier(unlock.connection.fetch)
    open_ids = {c.profile_id: c for c in manager.registry.list()}
    for profile in backups._prod_profiles(manager, "postgresql"):
        if (profile.port or 5432) == port and bool(prod_targets.addresses(profile.host, profile.port) & here):
            return True
        opened = open_ids.get(f"user:{profile.name}")
        if identifier is not None and opened is not None and prod_targets.system_identifier(opened.fetch) == identifier:
            return True
    return False


def server_checks(manager: Manager, session: str, doc: dict[str, Any]) -> Unlock:
    """The unlock of this session (admin kind, the area's flag), DEV or TEST, PostgreSQL 16 or later, no PROD alias of
    the server, and the admin role's check of section 6 (which ends the unlock). Raises :class:`WriteError`."""
    unlock = backups._unlock(manager, session, doc.get("connection_id"))
    if unlock.connection.engine != "postgresql":
        raise _not_allowed("engine")
    if unlock.environment not in ROLE_ENVIRONMENTS:
        raise _not_allowed("environment")
    backups._check_admin(manager, unlock)
    if unlock.connection.engine_version() < ROLE_MIN_VERSION:
        raise WriteError("studio.dbm_role_requires_16")
    if _prod_server(manager, unlock, _read_profile(manager, unlock)):
        logger.warning("role change refused: a PROD profile names the server", extra={"event": "studio.dbm_prod"})
        raise _not_allowed("prod")
    return unlock


@dataclass(frozen=True)
class OwnRoles:
    """The roles Studio never changes or widens (GS4-13). ``unknown``: a profile's role could not be known (no user
    in it, no open connection to ask and no OS user name), so every target could be it: fail closed."""

    names: frozenset[str]
    unknown: bool = False

    def __contains__(self, name: object) -> bool:
        return name in self.names


def _os_user() -> str | None:
    """The OS user name libpq takes for a profile without ``user`` (Studio pins libpq's environment, G2-01)."""
    try:
        name = getpass.getuser()
    except Exception:  # no user name the process can find
        return None
    return name or None


def own_roles(manager: Manager, unlock: Unlock) -> OwnRoles:
    """``CURRENT_USER`` and ``SESSION_USER`` of the admin session; the role every profile and write/admin profile
    names, of the user's store and a trusted project; ``CURRENT_USER`` and ``SESSION_USER`` of every open read
    connection (review I-2 of #149); and, for a profile without ``user``, the OS user name libpq takes. A profile
    without ``user`` whose role cannot be known makes the result ``unknown``."""
    rows = unlock.connection.fetch(_SESSION_ROLES)
    found = {str(value) for value in (rows[0] if rows else ()) if isinstance(value, str)}
    unknown = False
    config = manager.unlocks.config
    store = config.user.profiles()
    profiles = [entry.profile for entry in config.entries()]
    found.update(p.user for p in profiles if p.user)
    found.update(w.user for w in store.write_profiles if w.user)
    if any(not p.user for p in profiles if p.engine == "postgresql") or any(not w.user for w in store.write_profiles):
        user = _os_user()
        if user is None:
            unknown = True
        else:
            found.add(user)
    for opened in manager.registry.list():
        if opened.engine == "sqlite":
            continue
        try:
            session_rows = opened.fetch(_SESSION_ROLES)
        except Exception:  # busy or gone: its profile's role is counted above (or the OS user's)
            logger.info("an open connection's role was not read", extra={"event": "studio.dbm_role_own"})
            continue
        found.update(str(v) for v in (session_rows[0] if session_rows else ()) if isinstance(v, str))
    return OwnRoles(frozenset(found), unknown)


def _reached_by_own(unlock: Unlock, own: OwnRoles, role: str) -> bool:
    """Whether a role of ``own`` reaches ``role`` (its rights through INHERIT, or SET ROLE to it), directly or through
    any chain of memberships (review L-1 of #149): a privilege or membership given to ``role`` would reach Studio's own
    role."""
    rows = unlock.connection.fetch(_REACHERS, {"own": sorted(own.names), "target": role})
    return bool(rows and rows[0] and rows[0][0])


@contextmanager
def _session(unlock: Unlock) -> Iterator[Any]:
    """The admin session as an idle ``psycopg.Connection`` in autocommit (Tabrion opens its own transaction)."""
    with unlock.connection.idle_autocommit() as connection:
        yield connection


def _translate(call: Callable[[], Any]) -> Any:
    """Run a Tabrion call; its refusals as fixed problems, a driver error as ``db.<kind>`` (safe message only)."""
    import psycopg

    try:
        return call()
    except RoleUnsafe as exc:
        raise refusal(exc) from None
    except RoleMissing:
        raise WriteError("studio.dbm_role_missing") from None
    except UsageError as exc:
        code = exc.code if isinstance(exc.code, str) else ""
        if code == "postgres.role_exists":
            raise WriteError("studio.dbm_role_exists") from None
        if code == "postgres.role_ddl_requires_16":
            raise WriteError("studio.dbm_role_requires_16") from None
        if code == "postgres.object_missing":
            raise WriteError("studio.dbm_role_object_missing", [("role.objects", "role.object_missing")]) from None
        raise WriteError("studio.dbm_role_invalid", [("role.request", "role.refused_by_tabrion")]) from None
    except PermissionDenied as exc:
        if exc.code == "postgres.grant_not_allowed":
            raise WriteError("studio.dbm_role_denied", [("role.objects", "role.grant_not_allowed")]) from None
        logger.info("role change refused", extra={"event": "studio.dbm_role_failed", "detail": safe_message(exc)})
        raise DriverFailure(exc.classification) from None
    except TabrionError as exc:
        if exc.classification is None:
            raise WriteError("studio.dbm_role_failed") from None
        logger.info("role change failed", extra={"event": "studio.dbm_role_failed", "detail": safe_message(exc)})
        raise DriverFailure(exc.classification) from None
    except psycopg.Error as exc:
        classification = classify(exc)
        logger.info(
            "role change failed",
            extra={"event": "studio.dbm_role_failed", "detail": safe_message(classification)},
        )
        raise DriverFailure(classification) from None


def refusal(exc: RoleUnsafe) -> WriteError:
    """``studio.dbm_role_unsafe`` with one ``role.<reason>`` issue per reason Tabrion gave (fixed words only)."""
    raw = exc.details.get("reasons")
    reasons = [r for r in raw.split(",") if r] if isinstance(raw, str) else []
    issues = [("role.target", f"role.{r}" if r in REASONS else "role.refused") for r in reasons] or [
        ("role.target", "role.refused")
    ]
    code = "studio.admin_over_privileged" if exc.code == "postgres.caller_unsafe" else "studio.dbm_role_unsafe"
    return WriteError(code, None if code == "studio.admin_over_privileged" else issues[:16])


def read_privileges(connection: Any, role: str) -> dict[str, Any] | None:
    """``tabrion.postgres.role_privileges`` of ``role`` as a dict with every name escaped; ``None`` when the role does
    not exist."""
    from tabrion.postgres import role_privileges

    try:
        found = _translate(lambda: role_privileges(connection, role))
    except WriteError as exc:
        if exc.code == "studio.dbm_role_missing":
            return None
        raise
    return _escaped(found)


def _unsafe(doc: dict[str, Any], *, own: str | None = None) -> list[str]:
    """The ``reachable_*`` (or own) reasons of a privileges dict, as Tabrion names them."""
    reasons: list[str] = []
    for entry in doc["dangerous"]:
        kind, _, name = entry.partition(":")
        if kind in _UNSAFE_KINDS:
            reasons.append(kind if own is not None and name == display_name(own) else f"reachable_{kind}")
    return reasons


def k2_scan(manager: Manager, unlock: Unlock, role: str) -> dict[str, Any]:
    """The granted role's K-2 grants in every database the admin profile may connect to (P90-04): ``found`` (the
    escaped entries with their database), ``checked`` and ``unchecked`` database names (escaped), and how many
    databases the admin role may not connect to at all."""
    from tabrion.postgres import role_privileges

    names = [str(row[0]) for row in unlock.connection.fetch(_DATABASES) if row and isinstance(row[0], str)]
    hidden_rows = unlock.connection.fetch(_OTHER_DATABASES)
    no_connect = int(hidden_rows[0][0]) if hidden_rows and type(hidden_rows[0][0]) is int else 0
    found: list[str] = []
    checked: list[str] = []
    unchecked: list[str] = []
    for index, name in enumerate(names):
        if index >= _MAX_K2_DATABASES:
            unchecked.append(display_name(name))
            continue
        try:
            connection = manager.unlocks.open_checked(unlock.write_name, name)
        except Exception:  # a database Studio cannot reach is named, never guessed (P90-04)
            unchecked.append(display_name(name))
            continue
        try:
            with connection.idle_autocommit() as session:
                privileges = role_privileges(session, role)
        except RoleMissing:
            raise WriteError("studio.dbm_role_missing") from None
        except Exception:  # as above
            unchecked.append(display_name(name))
            continue
        finally:
            connection.close()
        checked.append(display_name(name))
        found.extend(
            f"{display_name(name)}:{display_name(entry)}"
            for entry in privileges.dangerous
            if entry.partition(":")[0] in _K2_KINDS
        )
    return {"found": found, "checked": checked, "unchecked": unchecked, "no_connect": no_connect}


# --- preview ----------------------------------------------------------------------------------------------------------


def _state(request: Request, target: dict[str, Any] | None, member: dict[str, Any] | None, k2: Any) -> str:
    doc = {"request": request.described(), "target": target, "member": member, "k2": k2}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read(manager: Manager, unlock: Unlock, request: Request) -> dict[str, Any]:
    """Everything the preview shows and the apply compares, read now on the admin session (changes nothing)."""
    own = own_roles(manager, unlock)
    refusals: list[str] = []
    warnings: list[str] = []
    if request.role in own or (request.member is not None and request.member in own):
        refusals.append("own_role")
    if own.unknown:
        refusals.append("own_role_unknown")
    if request.role.startswith("pg_") and request.action != "grant_membership":
        refusals.append("predefined_role")
    if request.member is not None and request.member.startswith("pg_"):
        refusals.append("predefined_role")
    database_rows = unlock.connection.fetch(_CURRENT_DATABASE)
    with _session(unlock) as connection:
        target = read_privileges(connection, request.role)
        member = None if request.member is None else read_privileges(connection, request.member)
    database = str(database_rows[0][0]) if database_rows and database_rows[0] else ""
    if request.action == "create":
        if target is not None:
            refusals.append("exists")
    elif target is None:
        raise WriteError("studio.dbm_role_missing")
    if request.member is not None and member is None:
        raise WriteError("studio.dbm_role_missing")
    if request.action != "create" and _reached_by_own(unlock, own, request.confirm):
        # A privilege or membership given to it reaches Studio's own role; dropping, changing or revoking it changes
        # what Studio's own role can do. Refused for every action (review L-1 of #149).
        refusals.append("own_role_reached")
    k2: dict[str, Any] | None = None
    if request.action == "grant_membership" and target is not None and member is not None:
        if request.role.startswith("pg_") or request.role in SERVER_ACCESS_ROLES or target["broad"]:
            refusals.append("privileged_role")
        if _unsafe(target):
            refusals.append("privileged_role")
        refusals.extend(_unsafe(member, own=request.member))
        k2 = k2_scan(manager, unlock, request.role)
        if k2["found"]:
            refusals.append("escalation_grant")
        if k2["unchecked"] or k2["no_connect"]:
            warnings.append("escalation_check_partial")
    elif request.action in INCREASING and target is not None:
        refusals.extend(_unsafe(target, own=request.role))
    if request.action == "drop" and target is not None and (target["owned_objects"] or target["default_privileges"]):
        refusals.append("owns_objects")
    if request.action == "grant_privileges" and request.on == "database" and "CREATE" in request.privileges:
        warnings.append("create_on_database")
    if request.action == "grant_privileges" and request.on == "schema" and "CREATE" in request.privileges:
        warnings.append("create_on_schema")
    if request.action == "revoke_membership":
        warnings.append("revoke_grantor")
    if request.action == "revoke_privileges":
        warnings.append("revoke_grantor")
    if target is not None and any(e.startswith("superuser_owner:") for e in target["public_definers"]):
        warnings.append("public_definers_superuser")
    elif target is not None and target["public_definers"]:
        warnings.append("public_definers")
    if request.options is not None and request.options.create_db:
        warnings.append("createdb")
    return {
        "action": request.action,
        "request": request.described(),
        "environment": unlock.environment,
        "database": display_name(database),
        "target": target,
        "member": member,
        "k2": k2,
        "refusals": sorted(set(refusals)),
        "warnings": sorted(set(warnings)),
        "typed": display_name(request.confirm),
        "state": _state(request, target, member, k2),
    }


def preview(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """What the role action would change and what refuses it, read now; changes nothing (D-14). Raises
    :class:`WriteError` or :class:`DriverFailure`."""
    unlock = server_checks(manager, session, doc)
    request = Request(doc, with_password=False)
    return _read(manager, unlock, request)


def listing(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """The server's roles that are not predefined (names escaped), with their attributes and whether Studio treats
    one as its own (never changed). Changes nothing."""
    unlock = server_checks(manager, session, doc)
    own = own_roles(manager, unlock)
    rows = unlock.connection.fetch(_ROLES)
    roles = []
    for row in rows[:_MAX_ROLES]:
        name = row[0]
        if not isinstance(name, str):
            continue
        roles.append(
            {
                "name": name,
                "display": display_name(name),
                "hidden": any(_hidden(c) for c in name),
                "attributes": [attribute for attribute, flag in zip(_ATTRIBUTE_NAMES, row[1:8], strict=True) if flag],
                "own": name in own,
                "connection_limit": row[8] if type(row[8]) is int and row[8] >= 0 else None,
                "valid_until": row[9].isoformat() if isinstance(row[9], datetime.datetime) else None,
            }
        )
    return {"environment": unlock.environment, "roles": roles, "truncated": len(rows) > _MAX_ROLES}


# --- apply ------------------------------------------------------------------------------------------------------------


def _typed(raw: object, expected: str) -> None:
    if not isinstance(raw, str) or not secrets.compare_digest(raw.encode("utf-8"), expected.encode("utf-8")):
        raise WriteError("studio.dbm_role_name_mismatch")


def _call(request: Request, connection: Any, password: str | None) -> Any:
    from tabrion import postgres

    role = request.role
    if request.action == "create":
        options = request.options if request.options is not None else postgres.RoleOptions()
        return postgres.create_role(connection, role, options=options, password=password)
    if request.action == "alter":
        return postgres.alter_role(connection, role, options=request.options, password=password)
    if request.action == "drop":
        postgres.drop_role(connection, role)
        return None
    if request.action == "grant_membership":
        return postgres.grant_membership(connection, role, member=request.member or "")
    if request.action == "revoke_membership":
        return postgres.revoke_membership(connection, role, member=request.member or "")
    kind: Any = request.on
    privileges: Any = list(request.privileges)
    if request.action == "grant_privileges":
        return postgres.grant_privileges(connection, role, privileges=privileges, on=kind, objects=request.objects)
    return postgres.revoke_privileges(connection, role, privileges=privileges, on=kind, objects=request.objects)


def _escaped(found: Any) -> dict[str, Any] | None:
    if found is None:
        return None
    doc: dict[str, Any] = found.as_dict()
    for key in ("member_of", "reachable", "dangerous", "broad", "public_definers"):
        doc[key] = [display_name(item) for item in doc[key]]
    doc["role"] = display_name(str(doc["role"]))
    doc["public_definers"] = sorted(doc["public_definers"], key=lambda e: (not e.startswith("superuser_owner:"), e))
    return doc


def _differences(inside: dict[str, Any] | None, reread: dict[str, Any] | None) -> list[str]:
    """The fields that differ between Tabrion's result (read in its transaction) and the read after the commit."""
    if inside is None or reread is None:
        return [] if inside is reread else ["role"]
    return sorted(key for key in inside if key != "safe" and inside.get(key) != reread.get(key))


def apply(manager: Manager, session: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Run the role action through Tabrion after the checks of :func:`preview`, the same ``state`` and the typed name;
    read the role again after the commit. Raises :class:`WriteError` or :class:`DriverFailure`."""
    unlock = server_checks(manager, session, doc)
    request = Request(doc, with_password=True)
    password = request.password
    request.password = None  # I-76: one reference, handed to Tabrion only
    _typed(doc.get("typed"), request.confirm)
    seen = _read(manager, unlock, request)
    if not isinstance(doc.get("state"), str) or not secrets.compare_digest(doc["state"], seen["state"]):
        raise WriteError("studio.dbm_role_changed")
    if seen["refusals"]:
        raise WriteError(
            "studio.dbm_role_not_allowed"
            if {"own_role", "own_role_reached", "own_role_unknown"} & set(seen["refusals"])
            else "studio.dbm_role_unsafe",
            [("role.target", f"role.{r}" if r in REASONS else "role.refused") for r in seen["refusals"]][:16],
        )
    start = request.audited(password_set=password is not None)
    action = f"dbm.role.{request.action}"
    with backups._audited(manager, unlock, action, start) as outcome:
        with _session(unlock) as connection:

            def fetch(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
                import psycopg
                from psycopg.rows import tuple_row

                with psycopg.Cursor(connection, row_factory=tuple_row) as reading:
                    reading.execute(sql, params)
                    return reading.fetchall()

            _facts, findings = privileges_now(fetch)
            if admin_refusing_findings(findings):
                raise WriteError("studio.admin_over_privileged")
            try:
                result = _translate(lambda: _call(request, connection, password))
            finally:
                password = None
            inside = _escaped(result)
            watched = request.member if request.member is not None else request.role
            reread = None if request.action == "drop" else read_privileges(connection, watched)
        differences = _differences(inside, reread)
        outcome.update(result="changed", differences=differences)
    logger.info("role changed", extra={"event": "studio.dbm_role", "action": request.action})
    return {
        "action": request.action,
        "role": display_name(request.role),
        "member": None if request.member is None else display_name(request.member),
        "after": reread,
        "inside": inside,
        "differences": differences,
        "audit_pending": outcome["audit_pending"],
    }
