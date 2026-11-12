"""Problem JSON: the one error format of the Studio API (03 sections 4.5 and 6, STU-004).

Every error answer of Studio, from the access middleware, a route, a missing file or an unexpected exception, is a
problem document with the media type ``application/problem+json``::

    {"code": "access.session_required", "kind": "access", "status": 401,
     "title": "Not signed in", "detail": "...", "hint": "..."}

plus ``log_id`` for ``studio.internal_error``, ``issues`` for ``studio.profile_invalid`` and ``studio.settings_invalid``
(``[{"pointer": "profile.host", "code": "config.required"}]``: a fixed key name and a ``config.*`` code) and, from
STU-011 on, ``sqlstate`` and ``position`` for database
errors (``db.<kind>``).

**Nothing in a problem comes from an exception or from the request (threat T-05, SQ-04).** ``title``, ``detail`` and
``hint`` are fixed English texts of :data:`CATALOG`; the only variable parts are the log ID (random hex), a SQLSTATE
(five characters ``[0-9A-Z]``), a position (an integer) and issues (key paths of fixed names, ``config.*`` codes). No
path, no secret, no stack trace, no ``DETAIL`` text of a database server and no value of the request can reach the
page. The page shows its own de/en text for ``code``.

``kind`` names the error family: :class:`ProblemKind` for Studio's own errors; for ``db.<kind>`` it is a value of
``tabrion.errors.Kind`` (doc 04 section 1.1; :data:`DB_KINDS`). :func:`db_problem` builds a database problem from
``tabrion.errors.classify`` only: the kind picks fixed texts of :data:`DB_CATALOG`, and the SQLSTATE is the only part
taken from the error. ``tabrion.errors.safe_message`` is for the log, never for the answer (STU-009).
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import json
import re
import secrets
from typing import Any

from starlette.responses import Response
from starlette.types import Send
from tabrion.errors import Classification, Kind, TabrionError, classify

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemKind(enum.StrEnum):
    """The family of a Studio error (the ``kind`` of ``studio.*`` and ``access.*`` problems)."""

    ACCESS = "access"
    REQUEST = "request"
    NOT_FOUND = "not_found"
    CONFIG = "config"
    DATABASE = "database"
    INTERNAL = "internal"


#: The values of ``tabrion.errors.Kind`` (doc 04 section 1.1), the names of ``db.<kind>`` problems.
DB_KINDS = frozenset(kind.value for kind in Kind)

_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_LOG_ID = re.compile(r"[0-9a-f]{12}")
_CODE = re.compile(r"(?:studio|access|db)\.[a-z][a-z0-9_]*")
#: An issue of a rejected profile or settings form: a key path of fixed names and a config.* code (STU-008).
_ISSUE_POINTER = re.compile(r"(?:profile|settings)(?:\.[a-z_]{1,32}){0,2}")
_ISSUE_CODE = re.compile(r"config\.[a-z_]{1,48}")
MAX_ISSUES = 32


@dataclass(frozen=True)
class Entry:
    """One catalog entry: the fixed texts of a problem code."""

    status: int
    kind: ProblemKind
    title: str
    detail: str
    hint: str | None = None


_SIGN_IN_HINT = "Run tabrion-studio again; it opens Studio with a new sign-in link."

#: Every problem Studio sends. The texts are fixed: never formatted with request data or exception text.
CATALOG: dict[str, Entry] = {
    # Access protection (STU-003, docs/architecture/access-protection.md).
    "access.host_not_allowed": Entry(
        403,
        ProblemKind.ACCESS,
        "Host not allowed",
        "Studio answers only requests addressed to its own 127.0.0.1 address and port.",
        "Open Studio with the link that tabrion-studio opens or prints.",
    ),
    "access.forwarded_not_allowed": Entry(
        403,
        ProblemKind.ACCESS,
        "Forwarded request",
        "Studio does not answer requests forwarded by a proxy.",
        "Connect to Studio directly on 127.0.0.1.",
    ),
    "access.origin_not_allowed": Entry(
        403, ProblemKind.ACCESS, "Origin not allowed", "Studio does not answer requests from other web pages."
    ),
    "access.origin_required": Entry(
        403, ProblemKind.ACCESS, "Origin required", "Changes need the Origin header of a Studio page."
    ),
    "access.browser_not_allowed": Entry(
        403,
        ProblemKind.ACCESS,
        "Not for browsers",
        "The control endpoint is for the tabrion-studio command only.",
    ),
    "access.cross_site": Entry(
        403, ProblemKind.ACCESS, "Cross-site request", "Studio does not answer requests from other sites."
    ),
    "access.json_only": Entry(
        415,
        ProblemKind.ACCESS,
        "JSON only",
        "The API accepts JSON bodies only (Content-Type: application/json).",
    ),
    "access.session_required": Entry(
        401, ProblemKind.ACCESS, "Not signed in", "This request needs a Studio session.", _SIGN_IN_HINT
    ),
    "access.csrf_invalid": Entry(
        403,
        ProblemKind.ACCESS,
        "Session check failed",
        "The request lacks the session's X-Tabrion-CSRF header.",
        _SIGN_IN_HINT,
    ),
    "access.bad_request": Entry(
        400, ProblemKind.REQUEST, "Bad request", "The request body is not a small JSON object."
    ),
    "access.launch_used": Entry(
        401, ProblemKind.ACCESS, "Sign-in link used", "This sign-in link has been used already.", _SIGN_IN_HINT
    ),
    "access.launch_expired": Entry(
        401, ProblemKind.ACCESS, "Sign-in link expired", "This sign-in link has expired.", _SIGN_IN_HINT
    ),
    "access.launch_invalid": Entry(
        401, ProblemKind.ACCESS, "Sign-in link invalid", "This sign-in link is not valid.", _SIGN_IN_HINT
    ),
    "access.control_denied": Entry(
        401, ProblemKind.ACCESS, "Control token missing", "The control endpoint needs the instance's control token."
    ),
    # Studio's own errors.
    "studio.not_found": Entry(404, ProblemKind.NOT_FOUND, "Not found", "There is nothing at this address."),
    "studio.method_not_allowed": Entry(
        405, ProblemKind.REQUEST, "Method not allowed", "This address does not accept this method."
    ),
    "studio.bad_request": Entry(400, ProblemKind.REQUEST, "Bad request", "The request is not valid."),
    "studio.invalid_request": Entry(
        422, ProblemKind.REQUEST, "Invalid request", "The request does not match what this address expects."
    ),
    "studio.bad_nonce": Entry(400, ProblemKind.REQUEST, "Bad nonce", "The proof request needs a 32-digit hex nonce."),
    # Configuration and project trust (STU-006).
    "studio.no_project_file": Entry(
        404, ProblemKind.CONFIG, "No project profiles", "The project folder has no .tabrion/studio.toml."
    ),
    "studio.project_invalid": Entry(
        409,
        ProblemKind.CONFIG,
        "Project profiles not valid",
        "The project's profile file cannot be used, so it cannot be trusted.",
        "Fix the issues that Studio lists for the file, then review it again.",
    ),
    "studio.trust_stale": Entry(
        409,
        ProblemKind.CONFIG,
        "Project profiles changed",
        "The project's profile file changed after it was shown for review.",
        "Review the file again before you trust it.",
    ),
    "studio.project_untrusted": Entry(
        409,
        ProblemKind.CONFIG,
        "Project profiles not trusted",
        "The project's profiles are not trusted in their current form.",
        "Review the project profiles again.",
    ),
    "studio.profile_not_found": Entry(404, ProblemKind.CONFIG, "Profile not found", "There is no such profile."),
    "studio.database_outside": Entry(
        409,
        ProblemKind.CONFIG,
        "Database file outside the project",
        "The profile's database file now lies outside the project folder, which was not confirmed.",
        "Review the project profiles again.",
    ),
    # Secret management (STU-007): never a value, a path or a variable's content.
    "studio.secret_missing": Entry(
        409,
        ProblemKind.CONFIG,
        "No password",
        "There is no password for this profile.",
        "Enter the password, or store it in the system keyring.",
    ),
    "studio.secret_invalid": Entry(
        400, ProblemKind.REQUEST, "Password not accepted", "The password must be 1 to 1024 characters."
    ),
    "studio.secret_not_storable": Entry(
        409,
        ProblemKind.CONFIG,
        "Password source fixed",
        "This profile reads its password from an environment variable or a file.",
        "Change the profile to keyring: to store a password.",
    ),
    "studio.secret_env_missing": Entry(
        409,
        ProblemKind.CONFIG,
        "Environment variable not set",
        "The environment variable this profile reads its password from is not set.",
        "Set it before you start tabrion-studio.",
    ),
    "studio.secret_file_outside": Entry(
        409,
        ProblemKind.CONFIG,
        "Password file not allowed",
        "The password file lies outside the project folder and the configuration folder.",
    ),
    "studio.secret_file_unsafe": Entry(
        409,
        ProblemKind.CONFIG,
        "Password file not safe",
        "The password file is a link, not a regular file, has more than one name, or belongs to another user.",
    ),
    "studio.secret_file_not_private": Entry(
        409,
        ProblemKind.CONFIG,
        "Password file not private",
        "Other users can read the password file.",
        "Make it readable by you only (chmod 600).",
    ),
    "studio.secret_file_unreadable": Entry(
        409,
        ProblemKind.CONFIG,
        "Password file not usable",
        "The password file is missing, empty, larger than 4 KB or not UTF-8.",
    ),
    "studio.keyring_unavailable": Entry(
        409,
        ProblemKind.CONFIG,
        "No system keyring",
        "No usable system keyring was found, and Studio never stores passwords in a plain file.",
        "Enter the password for this session, or use an env: or file: reference.",
    ),
    "studio.keyring_failed": Entry(
        502, ProblemKind.CONFIG, "Keyring error", "The system keyring did not complete the request."
    ),
    "studio.trust_unavailable": Entry(
        500, ProblemKind.CONFIG, "Trust not saved", "Studio could not save the trust decision in its state folder."
    ),
    "studio.config_stale": Entry(
        409,
        ProblemKind.CONFIG,
        "File changed",
        "The configuration file changed after Studio showed it.",
        "Reload the page and make the change again.",
    ),
    "studio.config_invalid": Entry(
        409,
        ProblemKind.CONFIG,
        "File has issues",
        "Studio does not rewrite a configuration file that has issues, so nothing in it is lost.",
        "Fix the issues in the file first.",
    ),
    "studio.config_hand_written": Entry(
        409,
        ProblemKind.CONFIG,
        "File written by hand",
        "Saving rewrites the file; its comments and formatting are not kept.",
        "Confirm to rewrite the file.",
    ),
    "studio.config_dir_unsafe": Entry(
        409,
        ProblemKind.CONFIG,
        "Configuration folder not private",
        "Studio writes its files only into a private folder of your own that is not a link.",
        "Check the folder's owner and permissions (700).",
    ),
    "studio.config_unwritable": Entry(
        500, ProblemKind.CONFIG, "File not saved", "Studio could not write the configuration file."
    ),
    "studio.config_not_storable": Entry(
        422,
        ProblemKind.CONFIG,
        "Value not storable",
        "Studio could not store these values so that it reads them back unchanged. Nothing was written.",
        "Check the fields for unusual characters.",
    ),
    "studio.config_too_large": Entry(
        422,
        ProblemKind.CONFIG,
        "File too large",
        "The configuration file would be larger than Studio reads (256 KB).",
        "Remove profiles you no longer use.",
    ),
    "studio.profile_invalid": Entry(
        422, ProblemKind.CONFIG, "Profile not valid", "Some fields of the profile are missing or not valid."
    ),
    "studio.settings_invalid": Entry(
        422, ProblemKind.CONFIG, "Settings not valid", "Some settings are missing or not valid."
    ),
    "studio.profile_exists": Entry(
        409, ProblemKind.CONFIG, "Name in use", "Another of your profiles has this name.", "Choose another name."
    ),
    "studio.profile_read_only": Entry(
        409,
        ProblemKind.CONFIG,
        "Project profile",
        "Studio does not change the project's profile file.",
        "Edit .tabrion/studio.toml in the project instead.",
    ),
    # Connections (STU-009). A database's own error is a db.<kind> problem (DB_CATALOG).
    "studio.connection_not_found": Entry(
        404, ProblemKind.NOT_FOUND, "Connection not open", "There is no open connection with this ID."
    ),
    "studio.connection_stale": Entry(
        409,
        ProblemKind.DATABASE,
        "Connection closed",
        "The connection's profile changed, was removed or is no longer trusted, so Studio closed the connection.",
        "Connect again.",
    ),
    "studio.prod_confirmation_required": Entry(
        409,
        ProblemKind.DATABASE,
        "Production database not confirmed",
        "The profile is marked as PROD, and the request did not confirm a PROD connection.",
        "Confirm the production database, then connect again.",
    ),
    "studio.connection_limit": Entry(
        409,
        ProblemKind.DATABASE,
        "Too many connections",
        "Studio keeps at most 8 connections open.",
        "Close a connection first.",
    ),
    "studio.connection_busy": Entry(
        503,
        ProblemKind.DATABASE,
        "Connection busy",
        "The connection's two database sessions are in use, for example by two running queries.",
        "Try again when a query finishes, or cancel one.",
    ),
    "studio.connection_failed": Entry(
        502, ProblemKind.DATABASE, "Connection failed", "Studio could not reach the database."
    ),
    "studio.connection_tls": Entry(
        422,
        ProblemKind.DATABASE,
        "TLS mode not accepted",
        "The profile's TLS mode is weaker than the host needs.",
        "Use sslmode verify-full or require for a host outside this computer.",
    ),
    "studio.connection_config": Entry(
        422,
        ProblemKind.DATABASE,
        "Connection settings not valid",
        "The driver did not accept the profile's settings.",
    ),
    "studio.database_missing": Entry(
        404,
        ProblemKind.DATABASE,
        "Database file not found",
        "The profile's SQLite file does not exist. Studio opens files read-only and never creates one.",
    ),
    "studio.database_unsafe": Entry(
        409,
        ProblemKind.DATABASE,
        "Database file refused",
        "The profile's SQLite file is a link or changed while it was opened.",
    ),
    "studio.statement_empty": Entry(400, ProblemKind.REQUEST, "No statement", "The text holds no SQL statement."),
    "studio.statement_multiple": Entry(
        400,
        ProblemKind.REQUEST,
        "One statement at a time",
        "Studio runs one statement at a time in read-only mode.",
        "Run the statements one by one.",
    ),
    "studio.statement_transaction": Entry(
        400,
        ProblemKind.REQUEST,
        "Transaction control not allowed",
        "Studio runs every statement in its own read-only transaction; BEGIN, COMMIT, SET TRANSACTION and similar "
        "statements are refused.",
    ),
    "studio.statement_refused": Entry(
        400,
        ProblemKind.REQUEST,
        "Statement not allowed",
        "Studio does not run this kind of statement in read-only mode.",
    ),
    # Explorer (STU-010).
    "studio.object_not_found": Entry(
        404,
        ProblemKind.NOT_FOUND,
        "Object not found",
        "The connection's catalog has no table, view or sequence with this schema and name.",
        "Refresh the catalog.",
    ),
    "studio.explorer_engine": Entry(
        409, ProblemKind.REQUEST, "Not for this database", "This information exists only for the other engine."
    ),
    # Queries (STU-011).
    "studio.query_not_found": Entry(
        404,
        ProblemKind.NOT_FOUND,
        "Query not found",
        "There is no query with this ID. Studio keeps only the most recent results.",
        "Run the query again.",
    ),
    "studio.query_limit": Entry(
        429,
        ProblemKind.DATABASE,
        "Too many queries",
        "Studio runs at most 4 queries at a time, and 2 per connection.",
        "Wait for a query to finish or cancel one.",
    ),
    "studio.query_canceled": Entry(409, ProblemKind.DATABASE, "Query canceled", "The query was canceled."),
    "studio.query_param_missing": Entry(
        400,
        ProblemKind.REQUEST,
        "Parameter missing",
        "The statement has a :name placeholder without a value.",
        "Give every placeholder a value, or write a colon inside a literal.",
    ),
    "studio.query_params_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Parameters not valid",
        "A parameter value must be text, a number, true, false or null; at most 100 parameters.",
        "Send JSON as text and cast it in the statement, for example :doc::jsonb.",
    ),
    "studio.query_write_unsupported": Entry(
        403,
        ProblemKind.DATABASE,
        "Read-only",
        "Studio 1.0 runs queries only in read-only mode.",
    ),
    "studio.query_too_large": Entry(
        413, ProblemKind.REQUEST, "Query too large", "The statement or its parameters are too large."
    ),
    # Data viewer (STU-013).
    "studio.viewer_column_unknown": Entry(
        400,
        ProblemKind.REQUEST,
        "Column not found",
        "The table has no column with this name.",
        "Refresh the table; it may have changed.",
    ),
    "studio.viewer_filter_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Filter not valid",
        "A filter is a list of at most 20 entries with a column, an operator and a text, number, true, false or null.",
    ),
    "studio.viewer_sort_unsupported": Entry(
        400,
        ProblemKind.REQUEST,
        "Column cannot be sorted",
        "The column's type has no order (for example json, xml or a geometric type).",
        "Sort by another column.",
    ),
    "studio.viewer_page_unknown": Entry(
        404,
        ProblemKind.NOT_FOUND,
        "Page not found",
        "Studio keeps only the most recent pages of the viewer, or there is no page after this one.",
        "Load the table again.",
    ),
    "studio.viewer_cell_unavailable": Entry(
        409,
        ProblemKind.REQUEST,
        "Cell not available",
        "Rows of a view without a primary key, or rows with a very long key, cannot be found again, so the full "
        "value cannot be loaded.",
        "Open the value with a query in the SQL editor.",
    ),
    "studio.viewer_row_gone": Entry(
        409,
        ProblemKind.DATABASE,
        "Row changed",
        "The row is no longer in the table, or it changed.",
        "Load the table again.",
    ),
    # Migration status and code trust (STU-015, threat T-16).
    "studio.no_migrations": Entry(
        404, ProblemKind.CONFIG, "No Alembic project", "The project folder has no alembic.ini with a script_location."
    ),
    "studio.migrations_invalid": Entry(
        409,
        ProblemKind.CONFIG,
        "Migrations folder not valid",
        "The project's Alembic files cannot be checked, so their code cannot be trusted.",
        "Check that script_location lies inside the project and that its versions folder holds only plain files.",
    ),
    "studio.migrations_changed": Entry(
        409,
        ProblemKind.CONFIG,
        "Migration code changed",
        "The project's Alembic files changed after they were trusted, so their code did not run.",
        "Review the project's migration code again.",
    ),
    "studio.migrations_timeout": Entry(
        504,
        ProblemKind.DATABASE,
        "Migration status timed out",
        "Reading the revisions took too long and was stopped.",
    ),
    "studio.migrations_busy": Entry(
        429,
        ProblemKind.DATABASE,
        "Migration status busy",
        "Studio reads the revisions of one migration status at a time, and another one is running.",
        "Try again when it has finished.",
    ),
    "studio.migrations_revisions": Entry(
        422,
        ProblemKind.CONFIG,
        "Revisions not readable",
        "Alembic could not read the revision graph of the project's migrations folder.",
        "Run alembic heads in the project to see the error.",
    ),
    "studio.migrations_database": Entry(
        502,
        ProblemKind.DATABASE,
        "Version table not readable",
        "The database did not answer while Studio read the Alembic version table.",
    ),
    "studio.migrations_table": Entry(
        422,
        ProblemKind.CONFIG,
        "Version table not as expected",
        "A migration version table lacks a column Studio reads, or a column has an unexpected type.",
        "Check that the table was made by the platform's own migration runner.",
    ),
    "studio.migrations_failed": Entry(
        500,
        ProblemKind.INTERNAL,
        "Migration status failed",
        "The process that reads the project's revisions ended without an answer.",
        "Run alembic heads in the project to see whether its revision files load.",
    ),
    # Export (STU-021).
    "studio.export_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Export not valid",
        "An export needs a format (csv or json), either a query or a table, and at most 1 000 000 rows.",
    ),
    "studio.export_busy": Entry(
        429,
        ProblemKind.DATABASE,
        "Export running",
        "Studio runs one export per connection and at most 2 at a time.",
        "Wait for the running export to finish.",
    ),
    "studio.export_stopped": Entry(
        504,
        ProblemKind.DATABASE,
        "Export stopped",
        "The export took longer than 10 minutes and was stopped.",
        "Export fewer rows, or filter the query.",
    ),
    # Diagnosis report (STU-025).
    "studio.report_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Report not valid",
        "A diagnosis report needs a format: markdown or json.",
    ),
    "studio.report_unavailable": Entry(
        503,
        ProblemKind.DATABASE,
        "Report not made",
        "Studio could not ask the server for the names the report hides, so it made no report.",
        "Try again when the connection is free, or reconnect.",
    ),
    "studio.internal_error": Entry(
        500,
        ProblemKind.INTERNAL,
        "Internal error",
        "Studio could not complete the request.",
        "The log entry with this log ID has the details.",
    ),
}


@dataclass(frozen=True)
class DbEntry:
    """The fixed texts of one ``db.<kind>`` problem."""

    status: int
    title: str
    detail: str
    hint: str | None = None


#: One entry per ``tabrion.errors.Kind``; a kind tabrion adds later falls back to ``unknown``.
DB_CATALOG: dict[str, DbEntry] = {
    "connection": DbEntry(
        502,
        "Database not reachable",
        "Studio could not connect to the database, or the connection was lost.",
        "Check the host, the port, the user and the password.",
    ),
    "timeout": DbEntry(504, "Statement timed out", "The statement ran longer than the profile's timeout."),
    "canceled": DbEntry(409, "Statement canceled", "The statement was canceled."),
    "locked": DbEntry(409, "Database busy", "Another session holds a lock the statement needs.", "Try again later."),
    "deadlock": DbEntry(409, "Deadlock", "The database ended the statement to resolve a deadlock.", "Try again."),
    "serialization": DbEntry(409, "Concurrent change", "A concurrent change conflicted with the statement."),
    "unique": DbEntry(409, "Duplicate value", "A unique constraint was violated."),
    "foreign_key": DbEntry(409, "Reference violated", "A foreign key constraint was violated."),
    "not_null": DbEntry(409, "Missing value", "A NOT NULL constraint was violated."),
    "check": DbEntry(409, "Check failed", "A check constraint was violated."),
    "exclusion": DbEntry(409, "Exclusion violated", "An exclusion constraint was violated."),
    "data": DbEntry(422, "Value not valid", "A value does not fit its column or operation."),
    "undefined_object": DbEntry(404, "Object not found", "A table, column, function or other object does not exist."),
    "syntax": DbEntry(400, "SQL syntax error", "The database could not parse the statement."),
    "permission": DbEntry(
        403, "Permission denied", "The database role has no permission for this.", "Ask the database owner."
    ),
    "read_only": DbEntry(
        403,
        "Read-only connection",
        "Studio connections are read-only; the database refused a change.",
        "Studio 1.0 does not write to databases.",
    ),
    "corrupt": DbEntry(500, "Database damaged", "The database reported damaged data or a file that is no database."),
    "disk_full": DbEntry(507, "Disk full", "The database ran out of disk space."),
    "resource": DbEntry(503, "Database out of resources", "The database ran out of memory or another resource."),
    "unknown": DbEntry(500, "Database error", "The database reported an error Studio does not classify."),
}


def db_problem(error: BaseException | Classification, *, log_id: str | None = None) -> Problem:
    """The ``db.<kind>`` problem of a database error: fixed texts for the kind, plus the SQLSTATE (STU-009)."""
    if isinstance(error, Classification):
        classification: Classification | None = error
    elif isinstance(error, TabrionError) and error.classification is not None:
        classification = error.classification
    else:
        classification = classify(error)
    kind = classification.kind.value if classification is not None else "unknown"
    if kind not in DB_CATALOG:
        kind = "unknown"
    entry = DB_CATALOG[kind]
    sqlstate = None if classification is None else classification.sqlstate
    if sqlstate is not None and _SQLSTATE.fullmatch(sqlstate) is None:
        sqlstate = None
    if log_id is not None and _LOG_ID.fullmatch(log_id) is None:
        raise ValueError("malformed log ID")
    return Problem(f"db.{kind}", kind, entry.status, entry.title, entry.detail, entry.hint, log_id, sqlstate)


def db_kind_problem(kind: Kind) -> Problem:
    """The ``db.<kind>`` problem for a kind Studio itself found (a query it stopped at its deadline: ``timeout``)."""
    name = kind.value if kind.value in DB_CATALOG else "unknown"
    entry = DB_CATALOG[name]
    return Problem(f"db.{name}", name, entry.status, entry.title, entry.detail, entry.hint)


def db_problem_response(error: BaseException | Classification, *, log_id: str | None = None) -> Response:
    doc = db_problem(error, log_id=log_id)
    return Response(doc.body(), status_code=doc.status, media_type=PROBLEM_MEDIA_TYPE)


def db_kind_response(kind: Kind) -> Response:
    """The answer for :func:`db_kind_problem`."""
    doc = db_kind_problem(kind)
    return Response(doc.body(), status_code=doc.status, media_type=PROBLEM_MEDIA_TYPE)


def new_log_id() -> str:
    """A random ID that joins a problem answer to its log entry (12 hex digits, no meaning)."""
    return secrets.token_hex(6)


@dataclass(frozen=True)
class Problem:
    """A problem document. Build it with :func:`problem`; the texts come from :data:`CATALOG`."""

    code: str
    kind: str
    status: int
    title: str
    detail: str
    hint: str | None = None
    log_id: str | None = None
    sqlstate: str | None = None
    position: int | None = None
    issues: tuple[tuple[str, str], ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "code": self.code,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
            "hint": self.hint,
        }
        for key in ("log_id", "sqlstate", "position"):
            value = getattr(self, key)
            if value is not None:
                doc[key] = value
        if self.issues is not None:
            doc["issues"] = [{"pointer": pointer, "code": code} for pointer, code in self.issues]
        return doc

    def body(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":")).encode("ascii")


def problem(
    code: str,
    *,
    log_id: str | None = None,
    sqlstate: str | None = None,
    position: int | None = None,
    issues: list[tuple[str, str]] | None = None,
) -> Problem:
    """The problem for ``code`` with its catalog texts.

    Raises :class:`ValueError` for a code outside the catalog, a malformed log ID, SQLSTATE or position: a caller
    cannot smuggle free text into an answer through any of them.
    """
    entry = CATALOG.get(code)
    if entry is None:
        raise ValueError("unknown problem code")
    if log_id is not None and _LOG_ID.fullmatch(log_id) is None:
        raise ValueError("malformed log ID")
    if sqlstate is not None and _SQLSTATE.fullmatch(sqlstate) is None:
        raise ValueError("malformed SQLSTATE")
    if position is not None and (type(position) is not int or position < 0):
        raise ValueError("malformed position")
    if issues is not None and (
        len(issues) > MAX_ISSUES
        or not all(
            isinstance(p, str) and isinstance(c, str) and _ISSUE_POINTER.fullmatch(p) and _ISSUE_CODE.fullmatch(c)
            for p, c in issues
        )
    ):
        raise ValueError("malformed issues")
    return Problem(
        code,
        entry.kind.value,
        entry.status,
        entry.title,
        entry.detail,
        entry.hint,
        log_id,
        sqlstate,
        position,
        None if issues is None else tuple(issues),
    )


def is_problem_code(value: object) -> bool:
    """Whether ``value`` is a code of the catalog (used to log only known codes)."""
    return isinstance(value, str) and (value in CATALOG or (value.startswith("db.") and value[3:] in DB_CATALOG))


def code_for_status(status: int) -> str:
    """The catalog code for an HTTP error raised by the framework (a missing file, a wrong method, ...)."""
    if status == 404:
        return "studio.not_found"
    if status == 405:
        return "studio.method_not_allowed"
    if 400 <= status < 500:
        return "studio.bad_request"
    return "studio.internal_error"


def well_formed_code(value: str) -> bool:
    """Whether ``value`` has the form of a problem code (``studio.*``, ``access.*`` or ``db.<kind>``)."""
    if _CODE.fullmatch(value) is None:
        return False
    family, _, name = value.partition(".")
    return family != "db" or name in DB_KINDS


def problem_response(
    code: str,
    *,
    log_id: str | None = None,
    headers: dict[str, str] | None = None,
    issues: list[tuple[str, str]] | None = None,
) -> Response:
    """A Starlette response with the problem for ``code``."""
    doc = problem(code, log_id=log_id, issues=issues)
    return Response(doc.body(), status_code=doc.status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


async def send_problem(send: Send, doc: Problem) -> None:
    """Send ``doc`` as a complete answer on a raw ASGI ``send`` (for middleware that answers by itself)."""
    body = doc.body()
    await send(
        {
            "type": "http.response.start",
            "status": doc.status,
            "headers": [
                (b"content-type", PROBLEM_MEDIA_TYPE.encode()),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
