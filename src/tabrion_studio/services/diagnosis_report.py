"""The diagnosis report: one diagnosis with its backup hints as a Markdown or JSON file (03 section 2 G, STU-025).

``DiagnosisReports.build`` runs the diagnosis of an open connection (:meth:`.Diagnostics.run` with ``backup=True``,
under the connection's time limit and the cancel route) and writes it as a file meant to be passed on, for example
in an issue. 09's acceptance is "report without secrets (canary test)", so the report is built from an allowlist:

* **In it:** the report's time, Studio's and tabrion's versions, the profile's name, engine and environment; on
  PostgreSQL whether the profile expects a read-only role and its ``sslmode``; every finding and backup hint (code,
  severity, title, detail, remedy, source) with their counts, and the backup facts (counts, times, ``archive_mode``).
* **Never in it:** a password or other secret, a token of the process, a connection URL or DSN, the host, port, user
  or database name, a file path or file name, the connection ID and any SQL text or parameter value. Studio's own
  findings and hints name none of these. tabrion's may name schemas and extensions, and a schema is often named after
  the login role (the ``"$user"`` default of ``search_path``) or the database (C-1 of ``stu-025-review.md``); so on
  PostgreSQL the session user, the current user and the current database (asked from the server, plus the profile's
  ``user`` and ``database``) are replaced by ``[role]`` and ``[database]`` in tabrion's texts, the profile name and
  the facts, as whole names (not inside a longer identifier), longest first. Studio's own sentences never name them
  and are left as they are (N-1). When the server cannot be asked for the names, there is no report
  (``studio.report_unavailable``, D-1). Names of other database objects stay; the report says so.
* **Second line of defense:** every text of the report passes :func:`tabrion_studio.logs.scrub`, the log redaction:
  the process's registered secrets (the passwords of open connections, the tokens) and anything shaped like a URL
  password, a ``password=`` pair, a bearer token or the session cookie are replaced by ``[REDACTED]``.

**Markdown** (``text/markdown``): every text from a finding or a profile is one line (controls become spaces), never
starts a line, and has the characters that make links, HTML, emphasis, code, tables or headings escaped with a
backslash, so a database's text cannot become a link or markup where the report is rendered (T-07). **JSON**
(``application/json``): ``{"report", "format_version", "generated_at", "studio_version", "tabrion_version",
"connection", "diagnosis", "backup", "left_out"}``; times are ISO 8601 UTC text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime
import importlib.metadata
import json
import re
import time
from typing import Any

from tabrion_studio import __version__ as studio_version
from tabrion_studio.logs import scrub
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import Diagnostics

__all__ = [
    "FORMATS",
    "LEFT_OUT",
    "NAMES_SQL",
    "DiagnosisReports",
    "Report",
    "ReportError",
    "hide_names",
    "markdown_text",
]

#: The formats of 03 section 2 G: their media type and file suffix.
FORMATS = {"markdown": ("text/markdown; charset=utf-8", "md"), "json": ("application/json", "json")}
#: What a report never holds; the report says so.
LEFT_OUT = (
    "No password, token, connection URL, host, port, file path, SQL text or parameter value. The login role and the "
    "database are shown as [role] and [database]; names of other database objects (schemas, extensions) may appear."
)
ROLE, DATABASE = "[role]", "[database]"
#: Studio's own question for the names to hide (C-1); it reads and passes the statement check.
NAMES_SQL = "SELECT session_user, current_user, current_database()"
#: The fields of a finding or hint that hold text (``code``, ``severity`` and ``source`` are fixed words).
_TEXT_FIELDS = ("title", "detail", "remedy")
FORMAT_VERSION = 1
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]+")
#: CommonMark's inline markup (code, emphasis, links, images, HTML, entities), GFM tables and strikethrough, headings.
#: ``@`` too: GitHub mentions and e-mail autolinks (L-1 of ``stu-025-review.md``).
_MARKUP = re.compile(r"([\\`*_\[\]<>!|~&#@])")
_AUTOLINK = re.compile(r"(?i)(://|www\.)")
_FILE_PART = re.compile(r"[^a-z0-9]+")
_SEVERITY_ORDER = ("error", "warning", "info")


class ReportError(Exception):
    """A report that cannot be made; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Report:
    """One report file."""

    filename: str
    media_type: str
    body: bytes
    counts: dict[str, int]


def _tabrion_version() -> str:
    try:
        return importlib.metadata.version("tabrion")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _iso(seconds: object) -> str | None:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    moment = datetime.datetime.fromtimestamp(float(seconds), tz=datetime.UTC)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(value: object) -> Any:
    """``value`` with every text scrubbed (:func:`tabrion_studio.logs.scrub`), for the JSON report."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(str(key)): _clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    return value


def markdown_text(value: object) -> str:
    """``value`` as inline Markdown text: scrubbed, on one line, with markup and autolinks escaped."""
    text = _CONTROL.sub(" ", scrub(str(value))).strip()
    text = _MARKUP.sub(r"\\\1", text)
    return _AUTOLINK.sub(lambda match: match.group(0).replace("/", "\\/").replace(".", "\\."), text)


def hide_names(text: str, names: dict[str, str]) -> str:
    """``text`` with each name of ``names`` (name to placeholder) replaced where it stands as a whole identifier:
    not inside a longer one (``app`` stays in ``application_name``). Longer names first."""
    for name in sorted(names, key=len, reverse=True):
        if name:
            text = re.sub(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", names[name], text)
    return text


def _item(entry: dict[str, Any]) -> dict[str, str]:
    keys = ("code", "severity", "title", "detail", "remedy", "source")
    return {key: str(entry.get(key, "")) for key in keys}


@dataclass
class DiagnosisReports:
    """Makes diagnosis reports. Blocking (call it off the event loop)."""

    registry: ConnectionRegistry
    diagnostics: Diagnostics
    clock: Callable[[], float] = time.time

    def build(self, connection_id: object, report_format: object) -> Report:
        if not isinstance(report_format, str) or report_format not in FORMATS:
            raise ReportError("studio.report_invalid")
        connection = self.registry.get(connection_id)
        profile = self.registry.source.entry(connection.profile_id).profile
        # D-1 of the delta review: the names to hide are read first, and a report that cannot hide them is not made.
        names = self._names(connection, profile) if connection.engine == "postgresql" else {}
        doc = self.diagnostics.run(connection.id, backup=True)
        generated = self.clock()
        connection_part: dict[str, Any] = {
            "profile": profile.name,
            "engine": connection.engine,
            "environment": connection.environment,
            "read_only": True,
        }
        if connection.engine == "postgresql":
            connection_part["read_only_role"] = profile.read_only_role
            connection_part["sslmode"] = profile.sslmode
        backup = doc.get("backup") or {"hints": [], "facts": {}}
        facts = dict(backup.get("facts", {}))
        for key in [key for key in facts if key.endswith("_at")]:
            facts[key] = _iso(facts[key])
        report: dict[str, Any] = {
            "report": "tabrion-studio-diagnosis",
            "format_version": FORMAT_VERSION,
            "generated_at": _iso(generated),
            "studio_version": studio_version,
            "tabrion_version": _tabrion_version(),
            "connection": connection_part,
            "diagnosis": {
                "ran_at": _iso(doc["ran_at"]),
                "counts": dict(doc["counts"]),
                "findings": [_item(entry) for entry in doc["findings"]],
            },
            "backup": {"hints": [_item(entry) for entry in backup.get("hints", [])], "facts": facts},
            "left_out": LEFT_OUT,
        }
        connection_part["profile"] = hide_names(str(profile.name), names)
        for entry in (*report["diagnosis"]["findings"], *report["backup"]["hints"]):
            if entry["source"] == "studio":
                continue  # N-1: Studio's own sentences never name the role or the database; keep them readable
            for key in _TEXT_FIELDS:
                entry[key] = hide_names(entry[key], names)
        for key, value in facts.items():
            if isinstance(value, str) and not key.endswith("_at"):
                facts[key] = hide_names(value, names)
        report = _clean(report)
        media_type, suffix = FORMATS[report_format]
        if report_format == "json":
            body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        else:
            body = _markdown(report).encode("utf-8")
        stamp = datetime.datetime.fromtimestamp(generated, tz=datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        parts = [_FILE_PART.sub("-", str(part).lower()).strip("-") or "x" for part in (connection.engine,
                 connection.environment)]  # fmt: skip
        filename = f"tabrion-studio-diagnosis-{parts[0]}-{parts[1]}-{stamp}.{suffix}"
        return Report(filename=filename, media_type=media_type, body=body, counts=dict(doc["counts"]))

    @staticmethod
    def _names(connection: Any, profile: Any) -> dict[str, str]:
        """The login role's and the database's names (C-1): asked from the server, plus the profile's.

        Fails closed (D-1 of the delta review): without ``user`` the profile connects as the OS login name, which only
        the server can tell, so when the server cannot be asked the report is refused (``studio.report_unavailable``)
        instead of going out with nothing to hide."""
        roles = {str(profile.user)} if profile.user else set()
        databases = {str(profile.database)} if profile.database else set()
        try:
            rows = connection.fetch(NAMES_SQL)
        except Exception as exc:
            raise ReportError("studio.report_unavailable") from exc
        if not rows or len(rows[0]) != 3 or not all(rows[0]):
            raise ReportError("studio.report_unavailable")
        row = rows[0]
        roles |= {str(row[0]), str(row[1])}
        databases.add(str(row[2]))
        return {**{name: DATABASE for name in databases}, **{name: ROLE for name in roles}}


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _entries(lines: list[str], entries: list[dict[str, str]], empty: str) -> None:
    if not entries:
        lines += [empty, ""]
        return
    for entry in entries:
        severity = entry["severity"].capitalize()
        lines += [
            f"### {markdown_text(severity)} · {markdown_text(entry['code'])} · {markdown_text(entry['title'])}",
            "",
            f"**Detail:** {markdown_text(entry['detail'])}",
            "",
            f"**Remedy:** {markdown_text(entry['remedy'])}",
            "",
            f"**Source:** {markdown_text(entry['source'])}",
            "",
        ]


def _fact(value: object) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.0f}"
    if isinstance(value, list):
        return ", ".join(markdown_text(item) for item in value)
    return markdown_text(value)


def _markdown(report: dict[str, Any]) -> str:
    connection = report["connection"]
    diagnosis = report["diagnosis"]
    counts = diagnosis["counts"]
    summary = ", ".join(_plural(int(counts.get(name, 0)), name) for name in _SEVERITY_ORDER)
    lines = [
        "# Tabrion Studio diagnosis report",
        "",
        f"- **Generated:** {markdown_text(report['generated_at'])}",
        f"- **Diagnosis ran:** {markdown_text(diagnosis['ran_at'])}",
        f"- **Versions:** Tabrion Studio {markdown_text(report['studio_version'])}, "
        f"tabrion {markdown_text(report['tabrion_version'])}",
        f"- **Profile:** {markdown_text(connection['profile'])}",
        f"- **Engine:** {markdown_text(connection['engine'])}",
        f"- **Environment:** {markdown_text(connection['environment'])}",
    ]
    if connection["engine"] == "postgresql":
        lines.append(f"- **Read-only role expected:** {_fact(connection.get('read_only_role'))}")
        lines.append(f"- **sslmode:** {_fact(connection.get('sslmode'))}")
    lines += [f"- **Findings:** {summary}", "", "## Findings", ""]
    _entries(lines, diagnosis["findings"], "No findings.")
    lines += ["## Backup hints", ""]
    _entries(lines, report["backup"]["hints"], "No backup hints.")
    facts = report["backup"]["facts"]
    if facts:
        lines += ["## Backup facts", "", "| Fact | Value |", "|---|---|"]
        lines += [f"| {markdown_text(key)} | {_fact(value)} |" for key, value in facts.items()]
        lines.append("")
    lines += ["---", "", f"Left out of this report: {markdown_text(report['left_out'])}", ""]
    return "\n".join(lines)
