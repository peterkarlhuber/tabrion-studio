"""Diagnostics (MVP): tabrion's checks and Studio's own on one open connection (03 sections 2 G and 6, STU-014).

``Diagnostics.run`` gives a list of findings ``{code, severity, title, detail, remedy, source}``; ``severity`` is
``error``, ``warning`` or ``info`` (tabrion's scale), and ``source`` is ``tabrion`` or ``studio``.

**tabrion's findings** (``TAB-SQ-*``, ``TAB-PG-*``) come from ``tabrion.check`` on the driver connection of one
``Database.read()`` (:func:`.tabrion_compat.check`; on PostgreSQL in tabrion's own read-only transactions, I-1 of
``stu-014-review.md``). Some of them describe the session they run in, and Studio's session is not the
application's: Studio sets its own timeouts, ``application_name``, ``default_transaction_read_only``,
``foreign_keys``, ``busy_timeout`` and ``trusted_schema``. Those findings (:data:`SESSION_CODES`) are left out, and
Studio checks what the application gets instead (``STU-DX-004``, ``-005``, ``-010``). ``TAB-PG-003`` looks only at
the current schema; Studio's ``STU-DX-006`` replaces it (:data:`REPLACED_CODES`, M-3 of the review).

**Studio's findings** (``STU-DX-*``):

* ``001`` (error): a ``prod`` profile without ``sslmode`` ``verify-ca`` or ``verify-full`` (03 section 5.4); not for
  a Unix socket, which has no TLS;
* ``002`` (warning): ``sslmode`` ``disable``, ``allow`` or ``prefer`` to a host that is not loopback (T-10,
  STU-T-021), when tabrion did not already report the connection's TLS (``TAB-PG-010``, ``-011``);
* ``003``: ``read_only_role = true``, but the role, or a role it can ``SET ROLE`` to, may write in a table, view,
  foreign table or sequence: ``INSERT``, ``UPDATE``, ``DELETE``, ``TRUNCATE``, a column grant, ownership, superuser
  (error for ``prod``);
* ``004`` (warning): no ``statement_timeout`` for the role or the database, after the precedence of
  ``pg_db_role_setting`` (role in database, then role, then database); ``postgresql.conf`` cannot be read;
* ``005`` (warning, SQLite): rows that break a foreign key (``PRAGMA foreign_key_check``);
* ``006`` (warning, error for ``prod``): the role owns, or can become the owner of, tables in any schema;
* ``007`` (info): ``read_only_role = true``, and default privileges grant the role write access to future tables;
* ``008`` (info): not every check ran: the connection's time limit ended the diagnosis, or the user canceled it;
* ``009`` (warning, SQLite): ``PRAGMA foreign_key_check`` could not run, for example because a foreign key points
  at columns without a unique index;
* ``010`` (warning if ``idle_in_transaction_session_timeout`` is missing, else info): no ``lock_timeout``,
  ``idle_in_transaction_session_timeout`` or ``application_name`` for the role or the database.

**Backup hints** (``STU-BK-*``, STU-025): ``run(..., backup=True)`` adds ``backup: {hints, facts}`` from
:mod:`.backup_hints`, under the same time limit and cancel. They are not findings and not in ``counts``. When the
hints hold ``STU-BK-010`` (archiving off, read with the archiver's state), tabrion's ``TAB-PG-020`` is left out of the
findings (:data:`BACKUP_CODES`); after a cancel, the time limit or ``STU-BK-014`` it stays (C-2 of the review). The page
route passes ``replace=False`` and keeps it until its view shows the hints (D-2, TD-036); the report replaces it.

**Time limit (M-1):** a diagnosis runs at most the connection's statement timeout; then every statement is canceled
like a query (:class:`.tabrion_compat.Cancel`: SQLite interrupt, psycopg cancel), and the checks that did not
run are named in ``STU-DX-008``. :meth:`Diagnostics.cancel` stops a running diagnosis the same way. On SQLite,
tabrion's step runs even past the limit and the cancel stops only its ``quick_check`` (N-1 of the delta review), so
tabrion's other findings stay; the stopped integrity check is named in ``STU-DX-008``.

Everything here reads. Nothing is logged but the event and the counts; findings name no path, password or value.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import re
import sqlite3
import threading
import time
from typing import Any, TypeVar

from tabrion import TabrionError
from tabrion.check import Finding

from tabrion_studio.config.schema import is_loopback
from tabrion_studio.services import backup_hints
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.queries import CANCEL_ATTEMPTS, CANCEL_INTERVAL_S
from tabrion_studio.services.tabrion_compat import Cancel

__all__ = ["BACKUP_CODES", "REPLACED_CODES", "SESSION_CODES", "Diagnostics"]

_T = TypeVar("_T")

#: tabrion findings about the session the check runs in, which is Studio's and not the application's. TAB-PG-021
#: ("standby with a write session") reads ``default_transaction_read_only``, which is always on in Studio (I-2).
SESSION_CODES = frozenset(
    {
        "TAB-PG-004",
        "TAB-PG-005",
        "TAB-PG-006",
        "TAB-PG-007",
        "TAB-PG-021",
        "TAB-SQ-001",
        "TAB-SQ-005",
        "TAB-SQ-009",
    }
)
#: tabrion findings that a Studio check replaces: TAB-PG-003 counts only the current schema (STU-DX-006).
REPLACED_CODES = frozenset({"TAB-PG-003"})
#: tabrion findings that a backup hint replaces when that hint is given (STU-025, C-2 of ``stu-025-review.md``):
#: TAB-PG-020 ("WAL archiving is off") gives way to STU-BK-010, which reads ``archive_mode`` with ``pg_stat_archiver``.
BACKUP_CODES = {"TAB-PG-020": "STU-BK-010"}
_TABRION_TLS_CODES = frozenset({"TAB-PG-010", "TAB-PG-011"})
_ORDER = {"error": 0, "warning": 1, "info": 2}
#: The SQLite errors of ``PRAGMA foreign_key_check`` that mean a broken foreign key definition (L-5).
_BROKEN_FOREIGN_KEY = ("foreign key mismatch", "no such table")
#: How many foreign-key violations ``STU-DX-005`` reads at most (it only counts them).
MAX_FK_ROWS = 1_000
#: STU-DX-003 checks every role the session can become: itself and every role it is a member of, INHERIT or not
#: (one SET ROLE away; ``pg_has_role(..., 'MEMBER')``), as tabrion's role checks count "can become" (I-20).
_WRITABLE_SQL = (
    "SELECT count(*) FROM (SELECT 1 FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'f', 'S') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
    "AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles b "
    "WHERE pg_catalog.pg_has_role(current_user, b.oid, 'MEMBER') AND CASE WHEN c.relkind = 'S' "
    "THEN pg_catalog.has_sequence_privilege(b.oid, c.oid, 'UPDATE') "
    "ELSE pg_catalog.has_table_privilege(b.oid, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE') "
    "OR pg_catalog.has_any_column_privilege(b.oid, c.oid, 'INSERT, UPDATE') END) "
    "LIMIT 1000) AS writable"
)
_DEFAULT_WRITE_SQL = (
    "SELECT count(*) FROM pg_catalog.pg_default_acl d, pg_catalog.aclexplode(d.defaclacl) AS a "
    "WHERE d.defaclobjtype IN ('r', 'S') AND a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') "
    "AND (a.grantee = 0 OR pg_catalog.pg_has_role(current_user, a.grantee, 'MEMBER'))"
)
_OWNER_SQL = (
    "SELECT count(*) FROM (SELECT 1 FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
    "AND pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER') LIMIT 1000) AS owned"
)
#: The settings the login role gets from ``ALTER ROLE``/``ALTER DATABASE ... SET``, with their scope.
_ROLE_SETTINGS_SQL = (
    "SELECT s.setrole <> 0, s.setdatabase <> 0, item "
    "FROM pg_catalog.pg_db_role_setting s, unnest(s.setconfig) AS item "
    "WHERE (s.setrole = 0 OR s.setrole = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = session_user)) "
    "AND (s.setdatabase = 0 OR s.setdatabase = (SELECT oid FROM pg_catalog.pg_database "
    "WHERE datname = current_database()))"
)
_ZERO_TIME = re.compile(r"^0+(ms|s|min|h|d)?$")
#: The session settings STU-DX-010 checks, besides statement_timeout (STU-DX-004); L-1 of the review.
_OTHER_SETTINGS = ("lock_timeout", "idle_in_transaction_session_timeout", "application_name")
_SECURE_TLS = frozenset({"verify-ca", "verify-full"})
_WEAK_TLS = frozenset({"disable", "allow", "prefer"})


def _finding(code: str, severity: str, title: str, detail: str, remedy: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "remedy": remedy, "source": "studio"}


def _from_tabrion(finding: Finding) -> dict[str, str]:
    return {
        "code": finding.code,
        "severity": str(finding.severity),
        "title": finding.title,
        "detail": finding.detail,
        "remedy": finding.remedy,
        "source": "tabrion",
    }


def effective_settings(rows: list[tuple[Any, ...]]) -> dict[str, str]:
    """The value each setting has for the login role in this database (L-4 of the review): a role-in-database
    setting wins over a role setting, which wins over a database setting (PostgreSQL's order)."""
    ranked: dict[str, tuple[int, str]] = {}
    for for_role, for_database, item in rows:
        name, _, value = str(item).partition("=")
        rank = 2 * bool(for_role) + bool(for_database)
        if name not in ranked or rank > ranked[name][0]:
            ranked[name] = (rank, value)
    return {name: value for name, (_, value) in ranked.items()}


def _not_checked(finding: Finding, code: str) -> bool:
    """tabrion's "not checked" form of ``code`` (an info finding; the damage finding is an error)."""
    return finding.code == code and str(finding.severity) == "info"


def _unset(settings: dict[str, str], name: str) -> bool:
    value = settings.get(name, "").strip().strip('"')
    if name == "application_name":
        return value == ""
    return value == "" or bool(_ZERO_TIME.match(value))


def _expire(hook: Cancel, finished: threading.Event) -> None:
    """Cancel, and repeat it every 0.1 s (for 3 s) until the running statement stops (as the viewer does)."""
    for _ in range(CANCEL_ATTEMPTS):
        hook.cancel()
        if finished.wait(CANCEL_INTERVAL_S):
            return


@dataclass
class _Run:
    """One running diagnosis: its cancel hook and which checks it could not run."""

    hook: Cancel = field(default_factory=Cancel)
    finished: threading.Event = field(default_factory=threading.Event)
    canceled: bool = False
    skipped: list[str] = field(default_factory=list)

    def step(self, name: str, work: Callable[[], _T], default: _T, *, always: bool = False) -> _T:
        """``work()``, or ``default`` with ``name`` noted when the time limit or a cancel stopped it. ``always``: run
        ``work`` even after the limit or a cancel (it stops its own long part, see :func:`.tabrion_compat.check`)."""
        if self.hook.requested and not always:
            self.skipped.append(name)
            return default
        try:
            return work()
        except Exception:
            if not self.hook.requested:
                raise
            self.skipped.append(name)
            return default


@dataclass
class Diagnostics:
    """Runs the diagnosis of one connection. Blocking (call it off the event loop)."""

    registry: ConnectionRegistry
    clock: Callable[[], float] = time.time
    _running: dict[str, list[_Run]] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def cancel(self, connection_id: object) -> bool:
        """Stop the running diagnoses of a connection (each ends with ``STU-DX-008``); whether one was running."""
        with self._lock:
            runs = list(self._running.get(connection_id, [])) if isinstance(connection_id, str) else []
        for run in runs:
            run.canceled = True
            threading.Thread(target=_expire, args=(run.hook, run.finished), daemon=True).start()
        return bool(runs)

    def run(self, connection_id: object, *, backup: bool = False, replace: bool = True) -> dict[str, Any]:
        """The diagnosis of an open connection; with ``backup``, also its backup hints (STU-025). ``replace=False``
        keeps the tabrion findings that a given hint would replace (:data:`BACKUP_CODES`): the page route, whose view
        does not show the hints yet (D-2 of the delta review, TD-036)."""
        connection = self.registry.get(connection_id)
        profile = self.registry.source.entry(connection.profile_id).profile
        prod = connection.environment == "prod"
        run = _Run()
        timer = threading.Timer(connection.timeout_s, _expire, args=(run.hook, run.finished))
        timer.daemon = True
        with self._lock:
            self._running.setdefault(connection.id, []).append(run)
        timer.start()
        try:
            empty: list[Finding] = []
            # N-1 of the delta review: on SQLite the cancel stops only tabrion's quick_check, so its other findings
            # (file mode, journal) come back even past the limit; they take milliseconds.
            sqlite = connection.engine == "sqlite"
            tabrion = run.step(
                "tabrion's checks", lambda: connection.tabrion_findings(cancel=run.hook), empty, always=sqlite
            )
            if sqlite and run.hook.requested and any(_not_checked(finding, "TAB-SQ-006") for finding in tabrion):
                run.skipped.append("tabrion's integrity check (TAB-SQ-006)")
            findings = [
                _from_tabrion(finding)
                for finding in tabrion
                if finding.code not in SESSION_CODES and finding.code not in REPLACED_CODES
            ]
            tabrion_tls = any(finding.code in _TABRION_TLS_CODES for finding in tabrion)
            if connection.engine == "postgresql":
                findings += self._postgres(connection, profile, run, prod=prod, tabrion_tls=tabrion_tls)
            else:
                findings += self._sqlite(connection, run)
            hints = self._backup(connection, run, prod=prod) if backup else None
            if hints is not None and replace:
                given = {item["code"] for item in hints["hints"]}
                replaced = {code for code, hint in BACKUP_CODES.items() if hint in given}
                findings = [item for item in findings if item["code"] not in replaced]
        finally:
            run.finished.set()
            timer.cancel()
            with self._lock:
                runs = self._running.get(connection.id, [])
                if run in runs:
                    runs.remove(run)
                if not runs:
                    self._running.pop(connection.id, None)
        if run.skipped:
            why = "you canceled it" if run.canceled else f"it reached the time limit of {connection.timeout_s:g} s"
            findings.append(_finding(
                "STU-DX-008", "info", "Not every check ran",
                f"The diagnosis stopped because {why}. Not run: {', '.join(run.skipped)}.",
                "Run the diagnosis again; for a large SQLite file, give the profile a longer statement timeout.",
            ))  # fmt: skip
        findings.sort(key=lambda item: (_ORDER.get(item["severity"], 3), item["code"]))
        counts = {name: sum(item["severity"] == name for item in findings) for name in _ORDER}
        doc: dict[str, Any] = {
            "connection_id": connection.id,
            "engine": connection.engine,
            "environment": connection.environment,
            "ran_at": self.clock(),
            "findings": findings,
            "counts": counts,
        }
        if hints is not None:
            hints["hints"].sort(key=lambda item: (_ORDER.get(item["severity"], 3), item["code"]))
            doc["backup"] = hints
        return doc

    def _backup(self, connection: OpenConnection, run: _Run, *, prod: bool) -> dict[str, Any]:
        """The backup hints (:mod:`.backup_hints`); a check the limit stopped is named in ``STU-DX-008``."""
        if connection.engine != "postgresql":
            empty: dict[str, Any] = {"hints": [], "facts": {"engine": connection.engine, "searched": False}}
            return run.step(
                "backup hints",
                lambda: backup_hints.sqlite_hints(
                    connection.database_file, clock=self.clock, stop=lambda: run.hook.requested
                ),
                empty,
            )

        def archiver() -> tuple[Any, ...] | None:
            try:
                rows = connection.fetch(backup_hints.ARCHIVER_SQL, cancel=run.hook)
            except TabrionError:
                if run.hook.requested:
                    raise
                return None  # a role that may not read it, a server without the view: a hint, not a failure
            return tuple(rows[0]) if rows else None

        missing = object()
        row = run.step("backup hints", archiver, missing)
        if row is missing:
            return {"hints": [], "facts": {"engine": "postgresql", "archive_mode": None}}
        return backup_hints.postgres_hints(row if isinstance(row, tuple) else None, prod=prod)

    @staticmethod
    def _postgres(
        connection: OpenConnection, profile: Any, run: _Run, *, prod: bool, tabrion_tls: bool
    ) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        sslmode = str(profile.sslmode or "")
        host = str(profile.host or "")
        socket = host.startswith("/")  # a Unix socket folder has no TLS at all (L-3 of the review)
        if prod and not socket and sslmode not in _SECURE_TLS:
            found.append(_finding(
                "STU-DX-001", "error", "Production connection without a verified certificate",
                f"The prod profile uses sslmode {sslmode or 'unset'}; the server's certificate is not verified.",
                "Set sslmode to verify-full (or verify-ca) in the profile and give the server's CA.",
            ))  # fmt: skip
        if sslmode in _WEAK_TLS and host and not is_loopback(host) and not tabrion_tls:
            found.append(_finding(
                "STU-DX-002", "warning", "Weak TLS to a remote host",
                f"The profile uses sslmode {sslmode} for a host that is not loopback: traffic can be read or changed "
                "on the way (T-10).",
                "Use sslmode verify-full.",
            ))  # fmt: skip

        def count(sql: str) -> int:
            value = connection.fetch(sql, cancel=run.hook)[0][0]
            return value if isinstance(value, int) else 0

        if profile.read_only_role:
            writable = run.step("STU-DX-003", lambda: count(_WRITABLE_SQL), 0)
            if writable > 0:
                found.append(_finding(
                    "STU-DX-003", "error" if prod else "warning", "The read-only role can write",
                    "The profile says read_only_role, but the role, or a role it can SET ROLE to, may write in "
                    f"{writable}{'+' if writable >= 1000 else ''} table(s), view(s) or sequence(s) (INSERT, UPDATE, "
                    "DELETE, TRUNCATE, a column grant or ownership). Studio's session is read-only; the role is not.",
                    "Connect with a role that has only SELECT (and USAGE on the schemas), and no membership in a "
                    "role that can write.",
                ))  # fmt: skip
            if run.step("STU-DX-007", lambda: count(_DEFAULT_WRITE_SQL), 0) > 0:
                found.append(_finding(
                    "STU-DX-007", "info", "Future tables will be writable",
                    "Default privileges (ALTER DEFAULT PRIVILEGES) grant the role, or a role it belongs to, write "
                    "access to tables or sequences created later.",
                    "ALTER DEFAULT PRIVILEGES ... REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM the role.",
                ))  # fmt: skip
        owned = run.step("STU-DX-006", lambda: count(_OWNER_SQL), 0)
        if owned > 0:
            found.append(_finding(
                "STU-DX-006", "error" if prod else "warning", "Role owns tables",
                f"The role owns, or can become the owner of, {owned}{'+' if owned >= 1000 else ''} table(s) or "
                "view(s) in the database; an owner can change them and is not subject to row-level security unless "
                "FORCE ROW LEVEL SECURITY is set.",
                "Use an owner role for migrations and a separate runtime role for the application"
                f"{' (03 section 5.4: required for prod)' if prod else ''}.",
            ))  # fmt: skip
        rows = run.step("STU-DX-004", lambda: connection.fetch(_ROLE_SETTINGS_SQL, cancel=run.hook), None)
        if rows is not None:
            settings = effective_settings(rows)
            if _unset(settings, "statement_timeout"):
                found.append(_finding(
                    "STU-DX-004", "warning", "No statement timeout for the role or database",
                    "Neither the role nor the database sets statement_timeout (or the most specific setting is 0), "
                    "so an application that sets none runs statements without a limit (unless postgresql.conf sets "
                    "one, which this role cannot read).",
                    "ALTER ROLE ... SET statement_timeout = '30s' (or ALTER DATABASE), or set it in the application "
                    "(tabrion's SessionSettings does).",
                ))  # fmt: skip
            missing = [name for name in _OTHER_SETTINGS if _unset(settings, name)]
            if missing:
                found.append(_finding(
                    "STU-DX-010",
                    "warning" if "idle_in_transaction_session_timeout" in missing else "info",
                    "Session settings missing for the role or database",
                    f"Neither the role nor the database sets {', '.join(missing)}; an application that sets none "
                    "runs without them (unless postgresql.conf sets them, which this role cannot read).",
                    "ALTER ROLE ... SET (or ALTER DATABASE), or set them in the application (tabrion's "
                    "SessionSettings does).",
                ))  # fmt: skip
        return found

    @staticmethod
    def _sqlite(connection: OpenConnection, run: _Run) -> list[dict[str, str]]:
        def violations() -> int:
            statement = connection.prepare("PRAGMA foreign_key_check")
            return len(connection.stream(statement, lambda cursor: cursor.fetch(MAX_FK_ROWS), cancel=run.hook))

        try:
            found = run.step("STU-DX-005", violations, 0)
        except TabrionError as exc:
            # L-5: a database error of the check, not one of Studio's; the other findings stay. Only "foreign key
            # mismatch" (parent columns without a unique index) and "no such table" (a missing parent on old SQLite)
            # say that a definition is broken; a busy, locked or unreadable file says nothing about the keys.
            cause = str(exc.original) if isinstance(exc.original, sqlite3.Error) else ""
            if cause.startswith(_BROKEN_FOREIGN_KEY):
                return [_finding(
                    "STU-DX-009", "warning", "Foreign keys could not be checked",
                    "PRAGMA foreign_key_check failed: a foreign key definition is broken, for example it points at "
                    "columns without a primary key or unique index.",
                    "Fix the foreign key definitions (the parent columns need a PRIMARY KEY or UNIQUE constraint).",
                )]  # fmt: skip
            return [_finding(
                "STU-DX-009", "warning", "Foreign keys could not be checked",
                "PRAGMA foreign_key_check could not run: the database was busy, locked or not readable.",
                "Run the diagnosis again when no other program holds a lock on the file.",
            )]  # fmt: skip
        if not found:
            return []
        return [_finding(
            "STU-DX-005", "warning", "Rows break foreign keys",
            f"PRAGMA foreign_key_check finds {found}{'+' if found >= MAX_FK_ROWS else ''} row(s) whose "
            "foreign key points nowhere: data was written with foreign_keys off.",
            "Open the database with foreign_keys on (tabrion.sqlite.connect does) and repair the rows.",
        )]  # fmt: skip
