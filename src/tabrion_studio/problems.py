"""Problem JSON: the one error format of the Studio API (03 sections 4.5 and 6, STU-004).

Every error answer of Studio, from the access middleware, a route, a missing file or an unexpected exception, is a
problem document with the media type ``application/problem+json``::

    {"code": "access.session_required", "kind": "access", "status": 401,
     "title": "Not signed in", "detail": "...", "hint": "..."}

plus ``log_id`` for ``studio.internal_error``, ``issues`` for ``studio.profile_invalid`` and ``studio.settings_invalid``
(``[{"pointer": "profile.host", "code": "config.required"}]``: a fixed key name and a ``config.*`` code) and for
``studio.data_editor_value_invalid`` (``[{"pointer": "changes.2.5", "code": "value.too_long"}]``: positions in the
request and the table, and a ``value.*`` code; STU-XDE-1), for ``studio.data_editor_constraint``
(``[{"pointer": "changes.2", "code": "constraint.unique"}]``, the column's position too where the database named it;
G-S2 6A) and for ``studio.schema_create_invalid``
(``[{"pointer": "create.name", "code": "create.too_long"}]``: a field name and a ``create.*`` code; STU-CR-2,
STU-CR-3) and for the Schema Designer's draft (STU-CR-4): ``studio.schema_edit_invalid`` and
``studio.schema_draft_limit`` (``[{"pointer": "edit.column", "code": "edit.column_in_use"}]``: a field of the edit
and an ``edit.*`` code), ``studio.schema_plan_refused`` and ``studio.schema_model_invalid``
(``[{"pointer": "plan.reason", "code": "plan.unsupported_feature"}]``: tabrion's reason, feature kind or error code,
``plan.*``, ``ddl.*`` or ``schema.*``, never a name or a text; from STU-CR-5 on also the table tabrion named, by
its position on the page only: ``tables.<n>`` or ``dropped.<n>``), ``studio.schema_run_not_allowed``
(``plan.reason``: why a plan is not run directly), ``studio.schema_run_check_failed`` (``plan.check``: the check
before the commit that failed), the schema comparison's problems (STU-XSD-2: ``compare.<side>`` and
``compare.plan`` with a ``compare.*`` code), the profile export's (STU-CR-6: ``export.profiles`` or
``export.path`` with an ``export.*`` code) and, from STU-011 on, ``sqlstate`` and ``position`` for database errors
(``db.<kind>``).

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
import sqlite3
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
#: An issue of a rejected profile or settings form: a key path of fixed names and a config.* code (STU-008); or of a
#: refused Data Editor value (STU-XDE-1): the change's and the column's position and a value.* code, never a name.
#: Or a field of the Schema Designer's database creation (STU-CR-2, STU-CR-3): ``create.<field>`` and a ``create.*``
#: code, never the value. Or the Schema Designer's draft (STU-CR-4): ``edit.<field>`` with an ``edit.*`` code, and
#: ``plan.<part>`` with tabrion's fixed reason or code (``plan.*``, ``ddl.*``, ``schema.*``), never a name or a text.
_ISSUE_POINTER = re.compile(
    r"(?:profile|settings)(?:\.[a-z_]{1,32}){0,2}|changes\.\d{1,4}\.\d{1,4}|(?:create|edit|plan)\.[a-z_]{1,32}"
    r"|(?:tables|dropped)\.\d{1,4}|(?:tool|backup|restore|delete|role|export)\.[a-z_]{1,32}|compare(?:\.[a-z_]{1,32}){1,2}"
)
_ISSUE_CODE = re.compile(
    r"(?:config|value|create|edit|plan|ddl|schema|tool|backup|restore|delete|compare|role|export)\.[a-z_]{1,48}"
)
#: A Data Editor change the database refused for a constraint (G-S2 6A): the change's position, and the column's
#: where the database named it, and a constraint.* code.
_CONSTRAINT_POINTER = re.compile(r"changes\.\d{1,4}(?:\.\d{1,4})?")
_CONSTRAINT_CODE = re.compile(r"constraint\.(?:unique|foreign_key|not_null|check|exclusion)")
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
        "Studio answers only requests addressed to its own loopback address and port.",
        "Open Studio with the link that tabrion-studio opens or prints.",
    ),
    "access.forwarded_not_allowed": Entry(
        403,
        ProblemKind.ACCESS,
        "Forwarded request",
        "Studio does not answer requests forwarded by a proxy.",
        "Connect to Studio directly on its loopback address.",
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
    "access.body_too_large": Entry(
        413, ProblemKind.REQUEST, "Request too large", "The request body is larger than this route accepts."
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
    "studio.connection_environment": Entry(
        422,
        ProblemKind.DATABASE,
        "libpq environment variable set",
        "A libpq environment variable is set (such as PGSERVICE, or PGUSER for a profile without a user) that "
        "Studio cannot override, and that could choose another server, authentication or TLS than the profile "
        "names. Studio opens no connection.",
        "Start Studio without that variable and put the setting into the profile; the connection guide lists the "
        "variables.",
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
    # ER diagram (STU-023).
    "studio.diagram_too_large": Entry(
        422,
        ProblemKind.REQUEST,
        "Schema too large for a diagram",
        "The schema has more than 1000 tables, more than one diagram answer holds.",
        "Use the explorer's tree and the Relationships tab for this schema.",
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
    # The shared write foundation (STU-XW-1, STU-ADR-0006). W-9: a refused write is 400 or 405, never 403 or 404.
    "studio.write_profile_invalid": Entry(
        400,
        ProblemKind.CONFIG,
        "Write profile not valid",
        "Some fields of the write or admin profile are missing or not valid.",
        "A write or admin profile is coupled to one of your own profiles that is not PROD, with its own role.",
    ),
    "studio.write_profile_unknown": Entry(
        400, ProblemKind.CONFIG, "No such write profile", "There is no write or admin profile with this ID."
    ),
    "studio.write_profile_exists": Entry(
        400, ProblemKind.CONFIG, "Name in use", "A profile or write profile with this name exists already."
    ),
    "studio.write_warning_unconfirmed": Entry(
        400,
        ProblemKind.CONFIG,
        "Note not confirmed",
        "A write or admin profile is created only after the note on production credentials was confirmed.",
        "Studio relies on the environment you give the profile; write credentials of a production database do "
        "not belong in Studio.",
    ),
    "studio.write_profiles_ignored": Entry(
        409,
        ProblemKind.CONFIG,
        "Write profiles not used here",
        "Studio uses write and admin profiles only from the configuration folder named on the command line "
        "(--config-dir, --state-dir) or the platform's folder, never from one an environment variable chose.",
        "Start Studio with --config-dir for this folder, or unset TABRION_STUDIO_CONFIG_DIR and "
        "TABRION_STUDIO_STATE_DIR.",
    ),
    "studio.profile_coupled": Entry(
        409,
        ProblemKind.CONFIG,
        "Profile has a write profile",
        "A write or admin profile is coupled to this profile.",
        "Remove the write or admin profile first.",
    ),
    "studio.write_prod_locked": Entry(
        405,
        ProblemKind.REQUEST,
        "Production is write-locked",
        "Studio never writes to a database whose profile is PROD.",
    ),
    "studio.write_role_over_privileged": Entry(
        405,
        ProblemKind.REQUEST,
        "Write role has too many rights",
        "The write profile's role is a superuser, has BYPASSRLS, REPLICATION or CREATEDB, or is a member (directly or "
        "through another role) of pg_read_server_files, pg_write_server_files or pg_execute_server_program. Studio "
        "does not unlock the write mode with such a role; creating databases needs an admin profile.",
        "Use a role with only the table rights the Data Editor needs; the security guide shows how to check the role.",
    ),
    "studio.write_coupling_invalid": Entry(
        400,
        ProblemKind.CONFIG,
        "Not the coupled profile",
        "This write or admin profile is not coupled to the profile of this connection.",
    ),
    "studio.write_target_unknown": Entry(
        400, ProblemKind.REQUEST, "Connection not open", "There is no open connection with this ID."
    ),
    "studio.unlock_name_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Name does not match",
        "To unlock, type the name of the write or admin profile exactly.",
    ),
    "studio.write_locked": Entry(
        405,
        ProblemKind.REQUEST,
        "Write mode locked",
        "The write mode of this connection is not unlocked, or the unlock has ended.",
        "Unlock it again with the name of the write profile.",
    ),
    "studio.write_target_changed": Entry(
        405,
        ProblemKind.REQUEST,
        "Target changed",
        "The profile, its environment or the database file changed since the unlock; the unlock has ended.",
    ),
    "studio.write_unsupported": Entry(
        400,
        ProblemKind.DATABASE,
        "Write connection not possible",
        "Studio cannot open a write connection with its safeguards for this database.",
    ),
    # The audit log of write and admin actions (STU-XW-2, TX-12): no record before the action, no action.
    "studio.audit_unavailable": Entry(
        405,
        ProblemKind.REQUEST,
        "Audit log not writable",
        "Studio could not write the audit record of this action, so the action was not done.",
        "Check the audit log in the write profile section; if it failed verification, move the audit folder of "
        "Studio's state folder aside, keep it for inspection, and start Studio again.",
    ),
    "studio.audit_record_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Action not recordable",
        "The audit log does not take the names of this action (empty, too long or too many), so it was not done.",
    ),
    # The plan, preview and confirmation framework (STU-XW-3, TX-08, TX-09). W-9: 400, never 403 or 404.
    "studio.plan_executor_unknown": Entry(
        400,
        ProblemKind.REQUEST,
        "Kind of change not available",
        "Studio has no executor for this kind of change.",
    ),
    "studio.plan_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Change not valid",
        "The requested change is missing parts, has parts that are not valid, or is too large.",
    ),
    "studio.plan_target_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Target not found",
        "A target of the change is not a table of this database.",
        "Refresh the table; it may have changed.",
    ),
    "studio.plan_target_partitioned": Entry(
        400,
        ProblemKind.REQUEST,
        "Partitioned table",
        "Studio does not change partitioned tables or their partitions yet.",
    ),
    "studio.plan_unknown": Entry(
        400,
        ProblemKind.REQUEST,
        "No such plan",
        "The plan expired or was replaced: there is no open plan with this hash for this session. A plan runs once "
        "and is then gone, and a newer plan can replace an old one.",
        "Preview the change again.",
    ),
    "studio.plan_binding": Entry(
        400,
        ProblemKind.REQUEST,
        "Plan not valid here",
        "The plan was made for another connection or an earlier unlock, so it was discarded.",
        "Preview the change again.",
    ),
    "studio.plan_expired": Entry(
        400,
        ProblemKind.REQUEST,
        "Plan expired",
        "The plan was not run in time after its preview, so it was discarded.",
        "Preview the change again.",
    ),
    "studio.plan_not_confirmed": Entry(
        400,
        ProblemKind.REQUEST,
        "Plan not confirmed",
        "A plan runs only after its preview was confirmed; this one was discarded.",
        "Preview the change again and confirm it.",
    ),
    "studio.plan_confirmation_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Confirmation does not match",
        "To confirm, type exactly the text the preview asks for.",
    ),
    "studio.plan_changed": Entry(
        400,
        ProblemKind.REQUEST,
        "Plan changed",
        "The plan no longer matches the hash of its preview, so it was not run.",
        "Preview the change again.",
    ),
    "studio.plan_drift": Entry(
        400,
        ProblemKind.REQUEST,
        "Database changed",
        "The target changed since the preview (its schema, its triggers or rules, or the database file), so nothing "
        "was run.",
        "Preview the change again.",
    ),
    "studio.plan_target_no_primary_key": Entry(
        400,
        ProblemKind.REQUEST,
        "Table has no primary key",
        "Studio changes rows only of tables with a primary key, and never by an internal row ID.",
    ),
    # The Data Editor (STU-026, 03 section 5.2). The whole changeset is rolled back; no value is named (TX-14).
    "studio.data_editor_conflict": Entry(
        400,
        ProblemKind.REQUEST,
        "Row changed meanwhile",
        "A row you changed or deleted was changed or deleted by someone else since it was loaded. Nothing was "
        "saved: all changes were rolled back.",
        "Reload the rows, check them, and make your changes again.",
    ),
    "studio.data_editor_rowcount": Entry(
        400,
        ProblemKind.REQUEST,
        "Unexpected number of rows",
        "A change affected another number of rows than one. Nothing was saved: all changes were rolled back.",
        "Reload the rows and check the table's rules and triggers.",
    ),
    "studio.data_editor_column_incomparable": Entry(
        400,
        ProblemKind.REQUEST,
        "Table not editable",
        "A column of this table has a type that cannot be compared (for example xml, point or a composite type), so a "
        "change made by someone else could not be detected. The Data Editor does not edit this table.",
        "The whole table is refused, whichever columns are shown. Edit it with another tool.",
    ),
    "studio.data_editor_constraint": Entry(
        400,
        ProblemKind.DATABASE,
        "Rule of the table broken",
        "The database refused a change because it breaks a rule of the table (a unique value, a reference, NOT NULL "
        "or a check). Nothing was saved: all changes were rolled back.",
        "The row in question is marked, and its cell where the database named the column. Fix it and save again.",
    ),
    "studio.data_editor_value_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Value not valid",
        "A value does not fit its column (its type, length, range, NULL or allowed values), or a required value is "
        "missing. Nothing was saved.",
        "Check the values you entered; the cells in question are marked.",
    ),
    # The I-109 j confirmation and require_auth (STU-XW-4, D-15, TX-07). W-9: 400 or 405, never 403 or 404.
    "studio.secret_unconfirmed": Entry(
        400,
        ProblemKind.REQUEST,
        "Secret reference not confirmed",
        "Studio sends the secret of this write or admin profile only to a server you confirmed together with the "
        "secret's reference. The pair is not confirmed, or it changed since you confirmed it.",
        "Review and confirm the server and the secret reference of the write profile.",
    ),
    "studio.secret_confirmation_unknown": Entry(
        400,
        ProblemKind.REQUEST,
        "No such confirmation",
        "The confirmation expired or belongs to another session.",
        "Review the server and the secret reference again.",
    ),
    "studio.secret_confirmation_stale": Entry(
        400,
        ProblemKind.REQUEST,
        "Profile changed",
        "The server or the secret reference changed since the review, so nothing was confirmed.",
        "Review the server and the secret reference again.",
    ),
    "studio.secret_confirmation_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Confirmation does not match",
        "To confirm, type the server's host exactly as shown.",
    ),
    "studio.secret_confirmation_not_needed": Entry(
        400,
        ProblemKind.REQUEST,
        "Nothing to confirm",
        "A SQLite write profile has no secret.",
    ),
    "studio.secret_confirmation_unavailable": Entry(
        405,
        ProblemKind.REQUEST,
        "Confirmation not storable",
        "Studio could not keep the confirmation in its state folder, so nothing was confirmed.",
        "Check that Studio's state folder is writable.",
    ),
    "studio.write_tls_required": Entry(
        400,
        ProblemKind.REQUEST,
        "Verified TLS required",
        "Studio sends a write or admin secret to a server that is not on this computer only over TLS with a "
        "verified certificate (sslmode verify-ca or verify-full).",
        "Set the TLS mode of the coupled profile to verify-full or verify-ca.",
    ),
    "studio.require_auth_unavailable": Entry(
        405,
        ProblemKind.REQUEST,
        "libpq too old for write connections",
        "Write and admin connections need libpq 16 or newer, which can refuse a server that asks for the password "
        "in clear text or as an MD5 hash. The libpq Studio loaded is older, so no write connection is opened.",
        "Install psycopg with its binary package (psycopg[binary]) or a libpq 16 or newer.",
    ),
    # The Database Manager's database creation (STU-CR-2, STU-CR-3, TX-29, GS3-21, GS4-27). W-9: 400 or 405, never
    # 403 or 404; no path, name or server text from the request or the database.
    "studio.schema_create_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Entry not valid",
        "A field of the new database is missing or not valid. Nothing was created.",
        "Check the fields in question.",
    ),
    "studio.schema_create_unconfirmed": Entry(
        400,
        ProblemKind.REQUEST,
        "Confirmation does not match",
        "To create the database, type its name exactly as shown. Nothing was created.",
    ),
    "studio.schema_create_engine": Entry(
        400,
        ProblemKind.REQUEST,
        "Not a PostgreSQL connection",
        "A PostgreSQL database is created on an unlocked PostgreSQL connection of the same server.",
        "Connect to the PostgreSQL server, unlock its admin profile and try again.",
    ),
    "studio.schema_create_platform": Entry(
        400,
        ProblemKind.REQUEST,
        "Not on Windows",
        "On Windows Studio does not create SQLite files: it cannot yet check who may write the folder. Nothing was "
        "created.",
        "Create the file with another tool and add it as a connection profile.",
    ),
    "studio.schema_file_exists": Entry(
        400,
        ProblemKind.REQUEST,
        "File exists",
        "A file or link with this name, or a side file of it (-wal, -shm, -journal), exists already. Studio never "
        "overwrites a file; nothing was created.",
        "Choose another file name.",
    ),
    "studio.schema_folder_unsafe": Entry(
        400,
        ProblemKind.REQUEST,
        "Folder not usable",
        "The folder does not exist, is or goes through a link, belongs to another user, or others may write to it or "
        "to a folder above it (unless that folder has the sticky bit, as /tmp has). Nothing was created.",
        "Choose a folder of your own that only you may write, by its real path.",
    ),
    "studio.schema_folder_reserved": Entry(
        400,
        ProblemKind.REQUEST,
        "Studio's own folder",
        "Studio does not create databases in its own configuration, state, audit or log folders, in the project's "
        ".tabrion folder or in a folder inside one of them.",
        "Choose another folder.",
    ),
    "studio.schema_database_exists": Entry(
        400,
        ProblemKind.REQUEST,
        "Database exists",
        "The server has a database with this name already. Nothing was created.",
        "Choose another name.",
    ),
    "studio.schema_createdb_required": Entry(
        400,
        ProblemKind.REQUEST,
        "No right to create databases",
        "The admin profile's role has no CREATEDB right on this server. Nothing was created.",
        "Ask the server's administrator to create the database (or to grant CREATEDB), then add a connection "
        "profile for it.",
    ),
    "studio.schema_owner_missing": Entry(
        400,
        ProblemKind.REQUEST,
        "Owner not found",
        "The server has no role with the owner's name. Nothing was created.",
    ),
    "studio.schema_owner_not_allowed": Entry(
        400,
        ProblemKind.REQUEST,
        "Owner not allowed",
        "The admin profile's role may not act as the owner role (SET ROLE), so it cannot give it the new database. "
        "Nothing was created.",
        "Leave the owner empty (the admin profile's role owns the database) or ask the administrator.",
    ),
    # The Schema Designer's draft and plan preview (STU-CR-4, STU-ADR-0007 sections 2 and 3, GS3-09, GS3-20). W-9:
    # 400 or 405, never 403 or 404; no name, type, default, check text or statement from the request or tabrion.
    "studio.schema_draft_missing": Entry(
        400,
        ProblemKind.REQUEST,
        "No draft",
        "There is no draft for this connection: none was started, it was discarded, or the unlock it belonged to "
        "ended.",
        "Start a draft again.",
    ),
    "studio.schema_draft_stale": Entry(
        400,
        ProblemKind.REQUEST,
        "Draft changed",
        "The draft changed since this page last read it (another tab may have edited it), so the edit was not made.",
        "Reload the draft and make the edit again.",
    ),
    "studio.schema_draft_schema": Entry(
        400,
        ProblemKind.REQUEST,
        "Schema not usable",
        "A draft starts from one schema the write profile can see: main on SQLite, and on PostgreSQL a schema "
        "that exists and is not one of PostgreSQL's own.",
        "Check the schema name.",
    ),
    "studio.schema_draft_limit": Entry(
        400,
        ProblemKind.REQUEST,
        "Draft too large",
        "The draft would exceed one of the Schema Designer's limits (tables, columns, constraints and indexes, "
        "edits, or its size). Nothing was changed.",
        "Make the change in smaller steps, or start a new draft.",
    ),
    "studio.schema_edit_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Edit not valid",
        "A part of the edit is missing or not valid, or the draft does not allow it. The draft was not changed.",
        "Check the fields in question.",
    ),
    "studio.schema_plan_refused": Entry(
        400,
        ProblemKind.REQUEST,
        "Change refused",
        "Tabrion refuses to plan this change, for example for a table with a feature it cannot check (partitions, "
        "inheritance, rules, a domain column, a view that names it). There is no way around the refusal.",
        "Leave the table in question unchanged, or change it with your own migration tooling.",
    ),
    "studio.schema_model_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Model not usable",
        "Tabrion cannot make the change as designed, for example for an unknown type, a default or check text that "
        "is not one plain expression, a name that is too long, or a change it does not support.",
        "Check the type, the texts and the names of the change.",
    ),
    # The Schema Designer's schema comparison (STU-XSD-2, STU-ADR-0007 sections 1 to 3, TX-28). W-9: 400; issues name
    # a side or the plan and a fixed reason (``compare.*``), never a name, a text or a part of a snapshot file.
    "studio.schema_compare_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Comparison not valid",
        "A side of the comparison is missing or not valid: each side is a schema of an open connection, the draft or "
        "a snapshot file, and at most one side is a file.",
        "Check both sides of the comparison.",
    ),
    "studio.schema_compare_schema": Entry(
        400,
        ProblemKind.REQUEST,
        "Schema not usable",
        "A comparison reads one schema of a connection: main on SQLite, and on PostgreSQL a schema that exists, that "
        "the profile can see and that is not one of PostgreSQL's own.",
        "Check the schema name.",
    ),
    "studio.schema_snapshot_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Snapshot file not readable",
        "The file is not a level-2 schema snapshot that Tabrion can read (SchemaSnapshot.to_json at level 2). Nothing "
        "of it was used.",
        "Choose a snapshot file that Tabrion wrote at level 2.",
    ),
    "studio.schema_snapshot_too_large": Entry(
        400,
        ProblemKind.REQUEST,
        "Schema too large",
        "A side of the comparison exceeds the Schema Designer's limits (2 MiB of snapshot text, 500 tables, 250 "
        "columns or 100 constraints and indexes per table). Nothing was compared.",
        "Compare a smaller schema.",
    ),
    "studio.schema_compare_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Schemas not comparable",
        "The two sides are of different database engines (SQLite and PostgreSQL) or snapshot levels, so they cannot "
        "be compared.",
        "Compare two schemas of the same engine.",
    ),
    "studio.schema_compare_plan_not_allowed": Entry(
        400,
        ProblemKind.REQUEST,
        "No plan preview",
        "A plan preview is made only from a schema read now on a connection of one of your own DEV or TEST profiles "
        "to another connection or the draft; never from or to a snapshot file, and never for PROD. The comparison "
        "itself is shown.",
        "Compare without the plan preview, or choose a DEV or TEST connection as the compared side.",
    ),
    # Running a Schema Designer plan (STU-CR-5, STU-ADR-0007 sections 4 to 11, GS3-03..05, GS3-10..20). W-9: 400 or
    # 405; no name, statement, default or check text, no server text.
    "studio.schema_run_not_allowed": Entry(
        400,
        ProblemKind.REQUEST,
        "Not run directly",
        "Studio runs a schema change directly only on a DEV or TEST database without migration management (no "
        "Alembic project, no Alembic or Levuvo version table, a SQLite user_version of 0), for a plan whose steps all "
        "run in one transaction and, on PostgreSQL, with a step that needs a backup only when pg_dump and pg_restore "
        "are set in database-tools.toml. Nothing was run.",
        "Use your own migration tooling for this database, or change the plan.",
    ),
    # Writing a Schema Designer plan as an Alembic revision (STU-XSD-3, STU-ADR-0007 section 5, GS3-06..08). W-9: 400
    # or 409; no name, path, message or statement text, no server text.
    "studio.schema_revision_not_allowed": Entry(
        400,
        ProblemKind.REQUEST,
        "No revision written",
        "Studio writes a plan as an Alembic revision only for a DEV or TEST database, into a project whose "
        "profiles you trusted and whose Alembic setup (alembic.ini with a script_location) and code you trusted, for "
        "a plan with a step. Nothing was written.",
        "Trust the project and its code, or use the plan with your own migration tooling.",
    ),
    "studio.schema_revision_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Revision message not valid",
        "The revision message is a text of 1 to 200 characters on one line, without control characters.",
        "Enter a short message for the revision.",
    ),
    "studio.schema_revision_head_unknown": Entry(
        409,
        ProblemKind.REQUEST,
        "Revision head unknown",
        "The new revision follows the single head of the project's revisions, and Studio could not read exactly one: "
        "the project has several heads or none, its code is not trusted, or its revisions could not be read. "
        "Nothing was written.",
        "Merge the heads or trust the project's code, then preview again.",
    ),
    "studio.schema_revision_folder": Entry(
        409,
        ProblemKind.REQUEST,
        "Revision folder not usable",
        "The revision folder (versions under the script_location) must be a folder inside the project, reached "
        "without a link at any level. Nothing was written.",
        "Check the project's revision folder.",
    ),
    "studio.schema_revision_exists": Entry(
        409,
        ProblemKind.REQUEST,
        "Revision file exists",
        "A file of the new revision's name exists already; Studio never overwrites a file. Nothing was written.",
        "Preview again to get a new revision id.",
    ),
    "studio.schema_revision_failed": Entry(
        500,
        ProblemKind.INTERNAL,
        "Revision not written",
        "The revision file could not be created or written.",
        "Check that the revision folder is writable, then preview again.",
    ),
    # STU-CR-6 part 1: the profile export (read profiles to a new file; never a secret, never over a file).
    "studio.profile_export_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Export not valid",
        "The profiles or the file of the export are missing or not valid; the field says what. Nothing was written.",
        "Check the fields in question.",
    ),
    "studio.profile_export_unconfirmed": Entry(
        400,
        ProblemKind.REQUEST,
        "Confirmation does not match",
        "To export, type the new file's name exactly as shown. Nothing was written.",
    ),
    "studio.profile_export_multi_host": Entry(
        400,
        ProblemKind.REQUEST,
        "Profile with several hosts",
        "A profile that names several hosts cannot be exported: the profile file holds one host per profile, and "
        "Studio never shortens the list. Nothing was written.",
        "Leave this profile out, or export a profile with one host.",
    ),
    "studio.profile_export_folder": Entry(
        400,
        ProblemKind.REQUEST,
        "Folder not usable",
        "The folder does not exist, is or goes through a link, belongs to another user, or others may write to it or "
        "to a folder above it (unless that folder has the sticky bit, as /tmp has). Nothing was written.",
        "Choose a folder of your own that only you may write, by its real path.",
    ),
    "studio.profile_export_reserved": Entry(
        400,
        ProblemKind.REQUEST,
        "Studio's own folder",
        "Studio does not write exports into its own configuration, state, audit or log folders, the project's "
        ".tabrion folder or a folder inside one of them.",
        "Choose another folder.",
    ),
    "studio.profile_export_exists": Entry(
        400,
        ProblemKind.REQUEST,
        "File exists",
        "A file or link with this name exists already. Studio never overwrites a file; nothing was written.",
        "Choose another file name.",
    ),
    "studio.profile_export_platform": Entry(
        400,
        ProblemKind.REQUEST,
        "Not on Windows",
        "On Windows Studio does not write profile exports yet: it cannot check who may write the folder. Nothing was "
        "written.",
    ),
    "studio.profile_export_failed": Entry(
        500,
        ProblemKind.INTERNAL,
        "Export not written",
        "The export file could not be written.",
        "Check the free space and the folder, then try again.",
    ),
    "studio.schema_backup_required": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup required",
        "This change needs a backup before it runs. It may be left out only on a DEV database. Nothing was run.",
        "Confirm the change with the backup.",
    ),
    "studio.schema_backup_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup failed",
        "The backup before the change could not be made, so nothing was run.",
        "Check the free space and the backups folder next to Studio's write settings, then try again.",
    ),
    "studio.schema_run_check_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Check failed",
        "A check before the commit found a problem (foreign keys or the database's integrity), so the change was "
        "rolled back. Nothing was changed.",
        "Fix the data the check names in your own tools, then preview the change again.",
    ),
    "studio.schema_run_outcome_unknown": Entry(
        400,
        ProblemKind.REQUEST,
        "Outcome unknown",
        "The connection was lost while the change was committed, so Studio cannot tell whether it was applied.",
        "Reconnect and look at the schema before you do anything else.",
    ),
    "studio.schema_run_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Change not run",
        "The change could not be run. Nothing was changed.",
        "Preview the change again.",
    ),
    # The Database Manager's backup and restore (STU-XDM-2, STU-ADR-0008 sections 2 to 4 and 6, GS4-03..09). W-9: 400
    # or 405; issues name a tool, the backup or the restore and a fixed reason, never a path, a name or tool output.
    "studio.backup_tool_unavailable": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup tool not usable",
        "Studio runs pg_dump and pg_restore only from the absolute paths set in database-tools.toml next to its "
        "write settings, and only when the file is a program of the expected name and a version at least as new as "
        "the server (and 16 or newer), owned by you or root, not writable by others and not a link. Nothing was run.",
        "Set the paths of pg_dump and pg_restore of a matching PostgreSQL version in database-tools.toml.",
    ),
    "studio.backup_tool_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup tool failed",
        "pg_dump or pg_restore ended with an error or ran out of time. A backup that did not finish was removed, and "
        "nothing that depends on it was done.",
        "Check the connection and the rights of the role, then try again.",
    ),
    "studio.dbm_backup_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup failed",
        "The backup could not be written to Studio's backup folder, so nothing that depends on it was done.",
        "Check the free space and the backups folder next to Studio's write settings, then try again.",
    ),
    "studio.dbm_backup_unknown": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup not found",
        "Studio restores only backups from its own backup folder, named as Studio names them.",
        "Choose a backup from the list.",
    ),
    "studio.dbm_backup_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup not usable",
        "The backup or its description does not match: it was changed, it is not private, it is a link, its format "
        "is not the one Studio makes, or it is for another kind of database. Nothing was restored.",
        "Choose another backup. Studio never restores a changed backup.",
    ),
    "studio.dbm_restore_not_allowed": Entry(
        400,
        ProblemKind.REQUEST,
        "Restore not allowed",
        "Studio restores only into a DEV or TEST database or a new one, never into a database or file that a PROD "
        "profile names, and on PostgreSQL only into a new database. Nothing was restored.",
        "Choose another target.",
    ),
    "studio.dbm_restore_name_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Name not usable",
        "The name of the new database or file is empty, too long, has blanks at either end or holds characters that "
        "cannot be seen. A new SQLite file ends in .db, .sqlite or .sqlite3.",
        "Choose a plain name.",
    ),
    "studio.dbm_restore_name_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Name not confirmed",
        "The typed name does not match the target's name exactly. Nothing was restored.",
        "Type the target's name exactly as shown.",
    ),
    "studio.dbm_restore_role_unsafe": Entry(
        400,
        ProblemKind.REQUEST,
        "Restore role not usable",
        "A PostgreSQL restore runs as a separate restore profile whose role has none of SUPERUSER, BYPASSRLS, "
        "REPLICATION, CREATEROLE or CREATEDB and no predefined role, because the archive's functions run as that "
        "role. Nothing was restored.",
        "Create a lean role outside Studio and an admin profile for it on the same read profile.",
    ),
    "studio.dbm_restore_target_exists": Entry(
        400,
        ProblemKind.REQUEST,
        "Target exists",
        "A database or file of this name exists already. Nothing was restored.",
        "Choose a new name.",
    ),
    "studio.dbm_restore_target_changed": Entry(
        400,
        ProblemKind.REQUEST,
        "Target changed",
        "The target is no longer the database or file that was checked. Nothing more was done.",
        "Look at the target, then start the restore again.",
    ),
    "studio.dbm_restore_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Restore failed",
        "The restore could not be done. A PostgreSQL restore runs in one transaction, so the new database stays "
        "empty; it is left for you to inspect or drop.",
        "Check the restore role's rights and the target, then try again.",
    ),
    # Database Manager: delete a database (STU-XDM-3).
    "studio.dbm_delete_not_allowed": Entry(
        400,
        ProblemKind.REQUEST,
        "Delete not allowed",
        "Studio deletes only a DEV or TEST database: never a maintenance or template database, never the database "
        "the admin connection is connected to, never a database or file that a PROD profile names, and a SQLite file "
        "only in a safe folder of your own and not on Windows. Nothing was deleted.",
        "Choose another target.",
    ),
    "studio.dbm_delete_name_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Name not usable",
        "The database name is empty, too long, has blanks at either end or holds characters that cannot be seen, or "
        "the force option is not a plain yes or no. Nothing was deleted.",
        "Choose the database by its plain name.",
    ),
    "studio.dbm_delete_name_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Name not confirmed",
        "The typed name does not match the target's name exactly. Nothing was deleted.",
        "Type the target's name exactly as shown.",
    ),
    "studio.dbm_delete_target_missing": Entry(
        400,
        ProblemKind.REQUEST,
        "Database not found",
        "No database of this name exists on the server. Nothing was deleted.",
        "Check the name.",
    ),
    "studio.dbm_delete_target_changed": Entry(
        400,
        ProblemKind.REQUEST,
        "Target changed",
        "The target is no longer the database or file that was checked and backed up. Nothing was deleted.",
        "Look at the target, then start the delete again.",
    ),
    "studio.dbm_delete_in_use": Entry(
        409,
        ProblemKind.REQUEST,
        "Database in use",
        "Other sessions or programs use the database. Studio did not end them and nothing was deleted.",
        "Close the other sessions, or on PostgreSQL 13 or later choose to end them and confirm that separately.",
    ),
    "studio.dbm_delete_backup_unverified": Entry(
        400,
        ProblemKind.REQUEST,
        "Backup not verified",
        "The backup made before the delete could not be verified (for example, the schema changed while it was made),"
        " so nothing was deleted.",
        "Wait until nothing changes the database's schema, then try again.",
    ),
    "studio.dbm_delete_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Delete failed",
        "The database could not be deleted. Its backup from before the delete is kept in the backup folder.",
        "Check the target and its folder, then try again.",
    ),
    # Database Manager: PostgreSQL roles (STU-XDM-4, E-X6). W-9: 400 or 405, never 403; no name, no server text.
    "studio.dbm_role_not_allowed": Entry(
        405,
        ProblemKind.REQUEST,
        "Role change not allowed",
        "Studio manages roles only on a DEV or TEST PostgreSQL server that no PROD profile names, through an admin "
        "profile, and never changes its own roles (the admin role and every role one of your profiles uses). "
        "Nothing was changed.",
        "Choose another role or connection.",
    ),
    "studio.dbm_role_requires_16": Entry(
        405,
        ProblemKind.REQUEST,
        "PostgreSQL 16 needed",
        "Studio manages roles only on PostgreSQL 16 or later: before 16 the right to create roles reaches almost every "
        "other role. Nothing was changed.",
        "Manage the roles of this server outside Studio.",
    ),
    "studio.dbm_role_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Role change not valid",
        "The role action, an option, the privileges, the objects or the password are not valid for this action. "
        "Nothing was changed.",
        "Check the marked field.",
    ),
    "studio.dbm_role_attribute_banned": Entry(
        400,
        ProblemKind.REQUEST,
        "Attribute never granted",
        "Studio never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, nor the ADMIN or GRANT option. "
        "Nothing was changed.",
        "Leave these attributes out.",
    ),
    "studio.dbm_role_name_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Role name not usable",
        "The role name is empty, longer than 63 bytes, has blanks at either end or holds characters that cannot be "
        "seen. Studio does not act on such a role. Nothing was changed.",
        "Rename the role outside Studio, or choose another one.",
    ),
    "studio.dbm_role_name_mismatch": Entry(
        400,
        ProblemKind.REQUEST,
        "Name not confirmed",
        "The typed name does not match the name of the role whose rights change. Nothing was changed.",
        "Type the role's name exactly as shown.",
    ),
    "studio.dbm_role_changed": Entry(
        400,
        ProblemKind.REQUEST,
        "Role changed since the preview",
        "The role, the member or their rights are no longer what the preview showed. Nothing was changed.",
        "Show the preview again and check it.",
    ),
    "studio.dbm_role_unsafe": Entry(
        400,
        ProblemKind.REQUEST,
        "Role change refused",
        "The change would give a role a dangerous right, directly or through a membership (SUPERUSER, BYPASSRLS, "
        "CREATEROLE, REPLICATION, a server-file or other predefined role, or an escalation grant), or the role owns "
        "objects. Nothing was changed.",
        "Check the reasons shown; change the roles outside Studio if this is really needed.",
    ),
    "studio.dbm_role_missing": Entry(
        400,
        ProblemKind.REQUEST,
        "Role not found",
        "No role of this name exists on the server. Nothing was changed.",
        "Check the name.",
    ),
    "studio.dbm_role_exists": Entry(
        400,
        ProblemKind.REQUEST,
        "Role exists",
        "A role of this name exists already. Nothing was changed.",
        "Choose another name.",
    ),
    "studio.dbm_role_object_missing": Entry(
        400,
        ProblemKind.REQUEST,
        "Object not found",
        "One of the objects does not exist in the connected database, or its name is not written as SQL expects "
        "(schema-qualified for tables, sequences and functions). Nothing was changed.",
        "Check the object names.",
    ),
    "studio.dbm_role_denied": Entry(
        400,
        ProblemKind.REQUEST,
        "Privilege not grantable",
        "The admin role may not grant this privilege on one of the objects. Nothing was changed.",
        "Ask the objects' owner, or grant it outside Studio.",
    ),
    "studio.dbm_role_failed": Entry(
        400,
        ProblemKind.REQUEST,
        "Role change failed",
        "The role change could not be made. Nothing was changed.",
        "Check the role and try again.",
    ),
    "studio.admin_over_privileged": Entry(
        405,
        ProblemKind.REQUEST,
        "Admin role has too many rights",
        "The admin role has or reaches SUPERUSER, BYPASSRLS, REPLICATION, a server-file role, or CREATEROLE before "
        "PostgreSQL 16. The Database Manager does nothing with it, and the unlock ended.",
        "Use an admin role with only the rights the Database Manager needs, set up outside Studio.",
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
    # History, favorites, scripts and completion (STU-020).
    "studio.state_unavailable": Entry(
        503,
        ProblemKind.INTERNAL,
        "Studio state not available",
        "Studio cannot use its state database, which keeps the history, the favorites and the editor drafts.",
        "The log names the reason; queries still run without a history entry.",
    ),
    "studio.drafts_too_large": Entry(
        413,
        ProblemKind.REQUEST,
        "Drafts too large",
        "Studio keeps at most 32 editor tabs, 512 K characters per text and 2 M characters in all as drafts.",
    ),
    "studio.favorites_full": Entry(
        409, ProblemKind.REQUEST, "Too many favorites", "Studio keeps at most 100 favorite profiles."
    ),
    "studio.catalog_not_loaded": Entry(
        404,
        ProblemKind.NOT_FOUND,
        "Catalog not loaded",
        "The connection's catalog has not been read yet.",
        "Load the names for completion, or open the explorer.",
    ),
    "studio.script_invalid": Entry(
        400,
        ProblemKind.REQUEST,
        "Not a script path",
        "A script is a .sql file inside the project folder, named by a relative path of plain names: no hidden"
        " folder, no device name such as NUL or COM1, no short name such as GIT~1, no name that ends in a dot.",
    ),
    "studio.script_link": Entry(
        400,
        ProblemKind.REQUEST,
        "Script behind a link",
        "The script or a folder on its path is a link: a symbolic link, a junction, a hard-linked file or a cloud"
        " placeholder such as a OneDrive Files On-Demand folder. Studio opens and saves only plain files in plain"
        " folders of the project.",
        "Copy the script into a plain folder of the project, or move the project out of the synced folder.",
    ),
    "studio.script_not_found": Entry(404, ProblemKind.NOT_FOUND, "Script not found", "There is no such script."),
    "studio.script_unreadable": Entry(422, ProblemKind.REQUEST, "Script not readable", "The script is not UTF-8 text."),
    "studio.script_too_large": Entry(
        413, ProblemKind.REQUEST, "Script too large", "Studio opens and saves scripts of at most 1 MB."
    ),
    "studio.script_exists": Entry(
        409,
        ProblemKind.REQUEST,
        "Script exists",
        "A script with this name exists already.",
        "Choose another name, or confirm to replace it.",
    ),
    "studio.script_stale": Entry(
        409,
        ProblemKind.REQUEST,
        "Script changed",
        "The script changed on disk after Studio opened it.",
        "Open it again, or confirm to replace it.",
    ),
    "studio.script_folder_missing": Entry(
        404,
        ProblemKind.NOT_FOUND,
        "Folder not found",
        "The folder of the script does not exist; Studio creates no folders.",
    ),
    "studio.script_permission_denied": Entry(
        403,
        ProblemKind.REQUEST,
        "Script not accessible",
        "The system does not let Studio read or write this script or its folder.",
        "Check the permissions of the file and its folder, or whether the drive is read-only.",
    ),
    "studio.script_locked": Entry(
        409,
        ProblemKind.REQUEST,
        "Script in use",
        "Another program holds the script open, so Studio cannot replace it.",
        "Close the script in the other program and save again.",
    ),
    "studio.script_disk_full": Entry(
        507,
        ProblemKind.REQUEST,
        "Disk full",
        "There is no room left on the drive for the script.",
        "Free some space and save again; the script on disk is unchanged.",
    ),
    "studio.script_write_failed": Entry(
        500,
        ProblemKind.REQUEST,
        "Script not saved",
        "The system refused to write the script; the script on disk is unchanged.",
        "Try again, or save it somewhere else with Download.",
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
    if kind == "unknown" and _sqlite_function_denied(error, classification):
        kind = str(Kind.PERMISSION)  # a StrEnum: its value; `.value` is not a name the surface lists
    if kind not in DB_CATALOG:
        kind = "unknown"
    entry = DB_CATALOG[kind]
    sqlstate = None if classification is None else classification.sqlstate
    if sqlstate is not None and _SQLSTATE.fullmatch(sqlstate) is None:
        sqlstate = None
    if log_id is not None and _LOG_ID.fullmatch(log_id) is None:
        raise ValueError("malformed log ID")
    return Problem(f"db.{kind}", kind, entry.status, entry.title, entry.detail, entry.hint, log_id, sqlstate)


#: The start of SQLite's message for a function call that an authorizer denied (``resolve.c``). SQLite reports it with
#: the plain code ``SQLITE_ERROR``, not ``SQLITE_AUTH`` as for a denied table or pragma, so tabrion 0.2's ``classify``
#: gives ``Kind.UNKNOWN`` (TD-053; the classification belongs in tabrion, see the TODO entry).
_SQLITE_FUNCTION_DENIED = "not authorized to use function: "


def _sqlite_function_denied(error: BaseException | Classification, classification: Classification | None) -> bool:
    """Whether ``error`` is SQLite's refusal of a function by the authorizer of a Studio connection (TD-053).

    Only an error that tabrion classified as an unknown ``SQLITE_ERROR`` of the sqlite3 driver, and whose driver error
    (``TabrionError.original``, then SQLAlchemy's ``orig`` and ``__cause__``) is a ``sqlite3.Error`` whose message
    *starts* with SQLite's fixed text. A message that only contains it (``no such function: "not authorized to use
    function: x"``) does not match. The message decides the problem code only; it never reaches the answer.
    """
    if isinstance(error, Classification) or classification is None:
        return False
    if classification.driver != "sqlite3" or classification.sqlite_code != "SQLITE_ERROR":
        return False
    current: object = error.original if isinstance(error, TabrionError) else error
    for _ in range(5):
        if isinstance(current, sqlite3.Error):
            return str(current).startswith(_SQLITE_FUNCTION_DENIED)
        if not isinstance(current, BaseException):
            return False
        current = getattr(current, "orig", None) or current.__cause__
    return False


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
        or not all(isinstance(p, str) and isinstance(c, str) and _issue_ok(p, c) for p, c in issues)
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


def _issue_ok(pointer: str, code: str) -> bool:
    if _CONSTRAINT_CODE.fullmatch(code):
        return _CONSTRAINT_POINTER.fullmatch(pointer) is not None
    return _ISSUE_POINTER.fullmatch(pointer) is not None and _ISSUE_CODE.fullmatch(code) is not None


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


async def send_problem(send: Send, doc: Problem, *, close: bool = False) -> None:
    """Send ``doc`` as a complete answer on a raw ASGI ``send`` (for middleware that answers by itself).

    ``close`` adds ``Connection: close``: the server then closes the connection after the answer instead of reading
    the rest of a request body that was refused unread.
    """
    body = doc.body()
    headers = [(b"content-type", PROBLEM_MEDIA_TYPE.encode()), (b"content-length", str(len(body)).encode())]
    if close:
        headers.append((b"connection", b"close"))
    await send({"type": "http.response.start", "status": doc.status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
