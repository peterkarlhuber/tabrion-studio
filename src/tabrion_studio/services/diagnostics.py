"""Diagnostics (MVP): tabrion's checks and Studio's own on one open connection (03 sections 2 G and 6, STU-014).

``Diagnostics.run`` gives a list of findings ``{code, severity, title, detail, remedy, source}``; ``severity`` is
``error``, ``warning`` or ``info`` (tabrion's scale), and ``source`` is ``tabrion`` or ``studio``.

**tabrion's findings** (``TAB-SQ-*``, ``TAB-PG-*``) come from ``tabrion.check`` on the connection's driver
connection, inside one ``Database.read()`` (:func:`.tabrion_compat.check`). Some of them describe the session they
run in, and Studio's session is not the application's: Studio sets its own timeouts, ``application_name``,
``foreign_keys``, ``busy_timeout`` and ``trusted_schema``. Those findings (:data:`SESSION_CODES`) are left out, and
Studio checks what the application gets instead (``STU-DX-004``, ``STU-DX-005``).

**Studio's findings** (``STU-DX-*``):

* ``001`` (error): a ``prod`` profile without ``sslmode`` ``verify-ca`` or ``verify-full`` (03 section 5.4);
* ``002`` (warning): ``sslmode`` ``disable``, ``allow`` or ``prefer`` to a host that is not loopback (T-10, STU-T-021);
* ``003``: ``read_only_role = true``, but the role may insert, update or delete in a table (error for ``prod``);
* ``004`` (warning): no ``statement_timeout`` for the role or the database (``ALTER ROLE``/``ALTER DATABASE ...
  SET``); an application that sets none runs without a limit, unless ``postgresql.conf`` sets one, which a role
  without superuser rights cannot read;
* ``005`` (warning, SQLite): rows that break a foreign key (``PRAGMA foreign_key_check``): data was written with
  ``foreign_keys`` off.

On a ``prod`` profile, a role that owns tables (``TAB-PG-003``) is an error, not a warning (03 section 5.4).

Everything here reads. Nothing is logged but the event and the counts; findings name no path, password or value.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Any

from tabrion.check import Finding, Severity

from tabrion_studio.config.schema import is_loopback
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection

__all__ = ["SESSION_CODES", "Diagnostics"]

#: tabrion findings about the session the check runs in, which is Studio's and not the application's.
SESSION_CODES = frozenset(
    {"TAB-PG-004", "TAB-PG-005", "TAB-PG-006", "TAB-PG-007", "TAB-SQ-001", "TAB-SQ-005", "TAB-SQ-009"}
)
_ORDER = {"error": 0, "warning": 1, "info": 2}
#: How many foreign-key violations ``STU-DX-005`` reads at most (it only counts them).
MAX_FK_ROWS = 1_000
_WRITABLE_SQL = (
    "SELECT count(*) FROM (SELECT 1 FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
    "AND (pg_catalog.has_table_privilege(c.oid, 'INSERT') OR pg_catalog.has_table_privilege(c.oid, 'UPDATE') "
    "OR pg_catalog.has_table_privilege(c.oid, 'DELETE')) LIMIT 1000) AS writable"
)
_ROLE_TIMEOUT_SQL = (
    "SELECT count(*) FROM pg_catalog.pg_db_role_setting s, unnest(s.setconfig) AS item "
    "WHERE (s.setrole = 0 OR s.setrole = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user)) "
    "AND (s.setdatabase = 0 OR s.setdatabase = (SELECT oid FROM pg_catalog.pg_database "
    "WHERE datname = current_database())) "
    "AND item ~ '^statement_timeout=' AND item !~ '^statement_timeout=0+(ms|s|min|h|d)?$'"
)
_SECURE_TLS = frozenset({"verify-ca", "verify-full"})
_WEAK_TLS = frozenset({"disable", "allow", "prefer"})


def _finding(code: str, severity: str, title: str, detail: str, remedy: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "remedy": remedy, "source": "studio"}


def _from_tabrion(finding: Finding, *, prod: bool) -> dict[str, str]:
    severity = str(finding.severity)
    if prod and finding.code == "TAB-PG-003" and finding.severity is Severity.WARNING:
        severity = "error"  # 03 section 5.4: a prod profile needs a role that is not the owner
    return {
        "code": finding.code,
        "severity": severity,
        "title": finding.title,
        "detail": finding.detail,
        "remedy": finding.remedy,
        "source": "tabrion",
    }


@dataclass
class Diagnostics:
    """Runs the diagnosis of one connection. Blocking (call it off the event loop)."""

    registry: ConnectionRegistry
    clock: Callable[[], float] = time.time

    def run(self, connection_id: object) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        profile = self.registry.source.entry(connection.profile_id).profile
        prod = connection.environment == "prod"
        findings = [
            _from_tabrion(finding, prod=prod)
            for finding in connection.tabrion_findings()
            if finding.code not in SESSION_CODES
        ]
        if connection.engine == "postgresql":
            findings += self._postgres(connection, profile, prod=prod)
        else:
            findings += self._sqlite(connection)
        findings.sort(key=lambda item: (_ORDER.get(item["severity"], 3), item["code"]))
        counts = {name: sum(item["severity"] == name for item in findings) for name in _ORDER}
        return {
            "connection_id": connection.id,
            "engine": connection.engine,
            "environment": connection.environment,
            "ran_at": self.clock(),
            "findings": findings,
            "counts": counts,
        }

    @staticmethod
    def _postgres(connection: OpenConnection, profile: Any, *, prod: bool) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        sslmode = str(profile.sslmode or "")
        host = str(profile.host or "")
        if prod and sslmode not in _SECURE_TLS:
            found.append(_finding(
                "STU-DX-001", "error", "Production connection without a verified certificate",
                f"The prod profile uses sslmode {sslmode or 'unset'}; the server's certificate is not verified.",
                "Set sslmode to verify-full (or verify-ca) in the profile and give the server's CA.",
            ))  # fmt: skip
        if sslmode in _WEAK_TLS and host and not is_loopback(host):
            found.append(_finding(
                "STU-DX-002", "warning", "Weak TLS to a remote host",
                f"The profile uses sslmode {sslmode} for a host that is not loopback: traffic can be read or changed "
                "on the way (T-10).",
                "Use sslmode verify-full.",
            ))  # fmt: skip
        if profile.read_only_role:
            writable = connection.fetch(_WRITABLE_SQL)[0][0]
            if isinstance(writable, int) and writable > 0:
                found.append(_finding(
                    "STU-DX-003", "error" if prod else "warning", "The read-only role can write",
                    f"The profile says read_only_role, but the role may insert, update or delete in {writable}"
                    f"{'+' if writable >= 1000 else ''} table(s). Studio's session is read-only; the role is not.",
                    "Connect with a role that has only SELECT (and USAGE on the schemas).",
                ))  # fmt: skip
        if connection.fetch(_ROLE_TIMEOUT_SQL)[0][0] == 0:
            found.append(_finding(
                "STU-DX-004", "warning", "No statement timeout for the role or database",
                "Neither the role nor the database sets statement_timeout, so an application that sets none runs "
                "statements without a limit (unless postgresql.conf sets one, which this role cannot read).",
                "ALTER ROLE ... SET statement_timeout = '30s' (or ALTER DATABASE), or set it in the application "
                "(tabrion's SessionSettings does).",
            ))  # fmt: skip
        return found

    @staticmethod
    def _sqlite(connection: OpenConnection) -> list[dict[str, str]]:
        statement = connection.prepare("PRAGMA foreign_key_check")
        violations = len(connection.stream(statement, lambda cursor: cursor.fetch(MAX_FK_ROWS)))
        if not violations:
            return []
        return [_finding(
            "STU-DX-005", "warning", "Rows break foreign keys",
            f"PRAGMA foreign_key_check finds {violations}{'+' if violations >= MAX_FK_ROWS else ''} row(s) whose "
            "foreign key points nowhere: data was written with foreign_keys off.",
            "Open the database with foreign_keys on (tabrion.sqlite.connect does) and repair the rows.",
        )]  # fmt: skip
