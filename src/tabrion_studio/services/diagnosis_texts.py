"""The English details and remedies of Studio's own findings and backup hints (``STU-DX-*``, ``STU-BK-*``; TD-032).

Studio's findings carry their facts as ``params`` (counts, setting names, the ``sslmode``, which checks did not run,
...), and the page words them in English or German from its i18n files (``frontend/src/i18n/locales``,
``diagnostics.texts``). The server still sends an English ``detail`` and ``remedy`` built here from the same
``params`` and the same templates: older clients, the diagnosis report (STU-025) and the command line use them.

:data:`TEXTS` is exactly the page's English ``diagnostics.texts`` (``tests/unit/test_diagnostics_page.py`` compares
them), written in i18next's form: ``{{name}}`` placeholders, and a ``_one``/``_other`` pair for a text that depends on
``count``. :func:`studio_text` picks the template the page picks for the same ``params``; a shared list of cases
(``frontend/src/views/diagnostics/studioTexts.cases.json``) is checked on both sides, so the page's English is the
server's English.

``params`` hold no name, path, host, user, value or SQL text: only counts, fixed words of this module and the profile's
``sslmode`` (one of the values the profile schema allows).
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

__all__ = ["CAPPED_COUNT", "SKIPPED_STEPS", "TEXTS", "count_text", "seconds_text", "span", "studio_text"]

#: Studio counts at most this many objects or rows; a count that reaches it is shown as "1000+".
CAPPED_COUNT = 1000
#: The names of the checks a stopped diagnosis did not run (``STU-DX-008``), besides Studio's own codes.
SKIPPED_STEPS = ("tabrion", "tabrion_integrity", "backup")
_CODE = re.compile(r"^STU-DX-\d{3}$")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

#: Equal to ``diagnostics.texts`` of the page's ``en.json``.
TEXTS: dict[str, dict[str, str]] = {
    "STU-DX-001": {
        "detail": "The prod profile uses sslmode {{sslmode}}; the server's certificate is not verified.",
        "detail_unset": "The prod profile sets no sslmode; the server's certificate is not verified.",
        "remedy": "Set sslmode to verify-full (or verify-ca) in the profile and give the server's CA.",
    },
    "STU-DX-002": {
        "detail": "The profile uses sslmode {{sslmode}} for a host that is not loopback: traffic can be read or "
        "changed on the way (T-10).",
        "remedy": "Use sslmode verify-full.",
    },
    "STU-DX-003": {
        "detail": "The profile says read_only_role, but the role, or a role it can SET ROLE to, may {{parts}}. "
        "Studio's session is read-only; the role is not.",
        "write_one": "write in {{amount}} table, view or sequence (INSERT, UPDATE, DELETE, TRUNCATE, a column grant "
        "or ownership)",
        "write_other": "write in {{amount}} tables, views or sequences (INSERT, UPDATE, DELETE, TRUNCATE, a column "
        "grant or ownership)",
        "create_one": "create tables in {{amount}} schema (CREATE on the schema)",
        "create_other": "create tables in {{amount}} schemas (CREATE on the schema)",
        "temporary": "create temporary tables (TEMP on the database)",
        "and": "; and ",
        "remedy": "Connect with a role that has only SELECT (and USAGE on the schemas), no CREATE on any schema, no "
        "TEMP on the database, and no membership in a role that can write.",
        "detail_temporary": "The profile says read_only_role, but the role, or a role it can SET ROLE to, may create "
        "temporary tables (TEMP on the database; PostgreSQL grants it to PUBLIC by default). Studio refuses the "
        "statements that create them and drops temporary tables when a session goes back to its pool; the role "
        "itself is not limited.",
        "remedy_temporary": "REVOKE TEMP ON DATABASE ... FROM PUBLIC (and from the role), and GRANT it to the roles "
        "that need it.",
    },
    "STU-DX-004": {
        "detail": "Neither the role nor the database sets statement_timeout (or the most specific setting is 0), so "
        "an application that sets none runs statements without a limit (unless postgresql.conf sets one, which this "
        "role cannot read).",
        "remedy": "ALTER ROLE ... SET statement_timeout = '30s' (or ALTER DATABASE), or set it in the application "
        "(tabrion's SessionSettings does).",
    },
    "STU-DX-005": {
        "detail_one": "PRAGMA foreign_key_check finds {{amount}} row whose foreign key points nowhere: data was "
        "written with foreign_keys off.",
        "detail_other": "PRAGMA foreign_key_check finds {{amount}} rows whose foreign key points nowhere: data was "
        "written with foreign_keys off.",
        "remedy": "Open the database with foreign_keys on (tabrion.sqlite.connect does) and repair the rows.",
    },
    "STU-DX-006": {
        "detail_one": "The role owns, or can become the owner of, {{amount}} table or view in the database; an owner "
        "can change it and is not subject to row-level security unless FORCE ROW LEVEL SECURITY is set.",
        "detail_other": "The role owns, or can become the owner of, {{amount}} tables or views in the database; an "
        "owner can change them and is not subject to row-level security unless FORCE ROW LEVEL SECURITY is set.",
        "remedy": "Use an owner role for migrations and a separate runtime role for the application.",
        "remedy_prod": "Use an owner role for migrations and a separate runtime role for the application (03 section "
        "5.4: required for prod).",
    },
    "STU-DX-007": {
        "detail": "Default privileges (ALTER DEFAULT PRIVILEGES) grant the role, or a role it belongs to, write "
        "access to tables or sequences created later.",
        "remedy": "ALTER DEFAULT PRIVILEGES ... REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM the role.",
    },
    "STU-DX-008": {
        "detail_canceled": "The diagnosis stopped because you canceled it. Not run: {{steps}}.",
        "detail_time_limit": "The diagnosis stopped because it reached the time limit of {{seconds}} s. Not run: "
        "{{steps}}.",
        "tabrion": "tabrion's checks",
        "tabrion_integrity": "tabrion's integrity check (TAB-SQ-006)",
        "backup": "backup hints",
        "remedy": "Run the diagnosis again; for a large SQLite file, give the profile a longer statement timeout.",
    },
    "STU-DX-009": {
        "detail_definition": "PRAGMA foreign_key_check failed: a foreign key definition is broken, for example it "
        "points at columns without a primary key or unique index.",
        "remedy_definition": "Fix the foreign key definitions (the parent columns need a PRIMARY KEY or UNIQUE "
        "constraint).",
        "detail_unreadable": "PRAGMA foreign_key_check could not run: the database was busy, locked or not readable.",
        "remedy_unreadable": "Run the diagnosis again when no other program holds a lock on the file.",
    },
    "STU-DX-010": {
        "detail": "Neither the role nor the database sets {{settings}}; an application that sets none runs without "
        "them (unless postgresql.conf sets them, which this role cannot read).",
        "remedy": "ALTER ROLE ... SET (or ALTER DATABASE), or set them in the application (tabrion's SessionSettings "
        "does).",
    },
    "STU-BK-001": {
        "detail": "Studio found no SQLite file next to the database, or in its backups or backup subfolder, whose "
        "name starts with the database's name. Backups kept elsewhere are not visible to Studio.",
        "remedy": "Back the database up with tabrion.sqlite.backup (online, 0600, checked with quick_check) and keep "
        "the copy on another disk as well.",
    },
    "STU-BK-002": {
        "detail": "The newest backup is {{span}} older than the database's last change.",
        "minutes_one": "{{count}} minute",
        "minutes_other": "{{count}} minutes",
        "hours_one": "{{count}} hour",
        "hours_other": "{{count}} hours",
        "days_one": "{{count}} day",
        "days_other": "{{count}} days",
        "remedy": "Make a new backup with tabrion.sqlite.backup, for example before every migration.",
    },
    "STU-BK-003": {
        "detail_one": "{{count}} backup file can be read by the group or by others; a backup holds all the data.",
        "detail_other": "{{count}} backup files can be read by the group or by others; a backup holds all the data.",
        "remedy": "chmod 600 the backup files; tabrion.sqlite.backup creates them with 0600.",
    },
    "STU-BK-004": {
        "detail": "Studio could not list the database's folder or one of its backup subfolders.",
        "remedy": "Check the folder's permissions, or look for backups yourself.",
    },
    "STU-BK-005": {
        "detail": "A folder holds more than {{limit}} entries; Studio looked at the first {{limit}} of each (in no "
        "particular order), so a backup, even the newest one, may not have been seen.",
        "remedy": "Keep backups in a backups subfolder of their own, or fewer files next to the database.",
    },
    "STU-BK-010": {
        "detail": "archive_mode is off: the server keeps no WAL archive, so the database cannot be restored to a point "
        "in time. Studio cannot see backups made with pg_dump or with a tool that does not archive WAL.",
        "remedy": "Make sure the database is backed up (pg_dump, pg_basebackup or a backup tool); for point-in-time "
        "recovery set archive_mode and an archive_command or archive_library.",
    },
    "STU-BK-011": {
        "detail_one": "The archiver failed {{count}} time since its statistics were reset, and the last attempt "
        "failed: the WAL archive has a gap and the server keeps WAL until archiving works again.",
        "detail_other": "The archiver failed {{count}} times since its statistics were reset, and the last attempt "
        "failed: the WAL archive has a gap and the server keeps WAL until archiving works again.",
        "remedy": "Look at the server log for the archive_command or archive_library error and fix it.",
    },
    "STU-BK-012": {
        "detail": "archive_mode is on, but the archiver has not archived a WAL file since its statistics were reset.",
        "remedy": "Check that archive_command or archive_library is set; pg_switch_wal() forces a first file.",
    },
    "STU-BK-013": {
        "detail": "The server is a standby with archive_mode on, which archives only on the primary.",
        "remedy": "Check the archiving on the primary, or set archive_mode = always to archive on the standby too.",
    },
    "STU-BK-014": {
        "detail": "Studio could not read archive_mode or pg_stat_archiver with this role.",
        "remedy": "Ask the database administrator how the database is backed up.",
    },
}


def _text(code: str, key: str, values: Mapping[str, object] | None = None, *, count: int | None = None) -> str:
    """The template ``key`` of ``code`` with its placeholders filled (i18next's rules: ``count`` picks the ``_one``
    or ``_other`` form, which English and German both have)."""
    texts = TEXTS[code]
    if count is not None:
        key = f"{key}_one" if count == 1 else f"{key}_other"
    filled = {**(values or {}), **({} if count is None else {"count": count})}
    return _PLACEHOLDER.sub(lambda match: str(filled[match.group(1)]), texts[key])


def count_text(count: int) -> str:
    """A count as Studio shows it: ``1000+`` when it reached :data:`CAPPED_COUNT`."""
    return f"{CAPPED_COUNT}+" if count >= CAPPED_COUNT else str(count)


def seconds_text(seconds: float) -> str:
    """Seconds as JavaScript's ``String(number)`` writes them for the usual values (``15``, ``0.5``)."""
    return str(int(seconds)) if float(seconds).is_integer() else repr(float(seconds))


def span(seconds: float) -> tuple[int, str]:
    """``seconds`` as an amount and a unit: minutes below two hours, hours below two days, days above."""
    if seconds < 7200:
        return max(int(seconds // 60), 1), "minutes"
    if seconds < 2 * 86400:
        return int(seconds // 3600), "hours"
    return int(seconds // 86400), "days"


def _steps(skipped: list[str]) -> str:
    return ", ".join(step if _CODE.match(step) else _text("STU-DX-008", step) for step in skipped)


def studio_text(code: str, params: Mapping[str, Any]) -> tuple[str, str]:
    """The English ``(detail, remedy)`` of Studio's finding or hint ``code`` with its ``params``."""
    detail, remedy = "detail", "remedy"
    values: dict[str, object] = {}
    count: int | None = None
    if code == "STU-DX-001":
        if params.get("sslmode"):
            values["sslmode"] = params["sslmode"]
        else:
            detail = "detail_unset"
    elif code == "STU-DX-002":
        values["sslmode"] = params["sslmode"]
    elif code == "STU-DX-003":
        writable, creatable = int(params["writable"]), int(params["creatable"])
        if writable == 0 and creatable == 0:
            detail, remedy = "detail_temporary", "remedy_temporary"
        else:
            parts = []
            if writable > 0:
                parts.append(_text(code, "write", {"amount": count_text(writable)}, count=writable))
            if creatable > 0:
                parts.append(_text(code, "create", {"amount": count_text(creatable)}, count=creatable))
            if params.get("temporary"):
                parts.append(_text(code, "temporary"))
            values["parts"] = _text(code, "and").join(parts)
    elif code in ("STU-DX-005", "STU-DX-006"):
        count = int(params["rows" if code == "STU-DX-005" else "owned"])
        values["amount"] = count_text(count)
        if code == "STU-DX-006" and params.get("prod"):
            remedy = "remedy_prod"
    elif code == "STU-DX-008":
        detail = "detail_canceled" if params["reason"] == "canceled" else "detail_time_limit"
        values["seconds"] = seconds_text(float(params["time_limit_s"]))
        values["steps"] = _steps(list(params["skipped"]))
    elif code == "STU-DX-009":
        detail, remedy = f"detail_{params['cause']}", f"remedy_{params['cause']}"
    elif code == "STU-DX-010":
        values["settings"] = ", ".join(params["missing"])
    elif code == "STU-BK-002":
        values["span"] = _text(code, str(params["unit"]), count=int(params["amount"]))
    elif code in ("STU-BK-003", "STU-BK-011"):
        count = int(params["files" if code == "STU-BK-003" else "failures"])
    elif code == "STU-BK-005":
        values["limit"] = params["max_entries"]
    return _text(code, detail, values, count=count), _text(code, remedy, values)
