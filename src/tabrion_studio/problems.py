"""Problem JSON: the one error format of the Studio API (03 sections 4.5 and 6, STU-004).

Every error answer of Studio, from the access middleware, a route, a missing file or an unexpected exception, is a
problem document with the media type ``application/problem+json``::

    {"code": "access.session_required", "kind": "access", "status": 401,
     "title": "Not signed in", "detail": "...", "hint": "..."}

plus ``log_id`` for ``studio.internal_error`` and, from STU-011 on, ``sqlstate`` and ``position`` for database
errors (``db.<kind>``).

**Nothing in a problem comes from an exception or from the request (threat T-05, SQ-04).** ``title``, ``detail`` and
``hint`` are fixed English texts of :data:`CATALOG`; the only variable parts are the log ID (random hex), a SQLSTATE
(five characters ``[0-9A-Z]``) and a position (an integer). No path, no secret, no stack trace, no ``DETAIL`` text of
a database server and no value of the request can reach the page. The page shows its own de/en text for ``code``.

``kind`` names the error family: :class:`ProblemKind` for Studio's own errors; for ``db.<kind>`` it is one of
:data:`DB_KINDS`, which mirrors ``tabrion.errors.Kind`` (doc 04 section 1.1, tabrion 0.1.0a1). Studio has no tabrion
dependency before STU-009 (RC-8, ``docs/TODO.md`` TS-001); STU-009 replaces the mirror with ``tabrion.errors.Kind``
and builds database problems from ``tabrion.errors.classify`` and ``safe_message`` only.
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

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemKind(enum.StrEnum):
    """The family of a Studio error (the ``kind`` of ``studio.*`` and ``access.*`` problems)."""

    ACCESS = "access"
    REQUEST = "request"
    NOT_FOUND = "not_found"
    CONFIG = "config"
    INTERNAL = "internal"


#: The values of ``tabrion.errors.Kind`` (doc 04 section 1.1, tabrion a463422 = 0.1.0a1). STU-009 replaces this
#: mirror with the enum itself (docs/TODO.md TS-001).
DB_KINDS = frozenset(
    {
        "connection",
        "timeout",
        "canceled",
        "locked",
        "deadlock",
        "serialization",
        "unique",
        "foreign_key",
        "not_null",
        "check",
        "exclusion",
        "data",
        "undefined_object",
        "syntax",
        "permission",
        "read_only",
        "corrupt",
        "disk_full",
        "resource",
        "unknown",
    }
)

_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_LOG_ID = re.compile(r"[0-9a-f]{12}")
_CODE = re.compile(r"(?:studio|access|db)\.[a-z][a-z0-9_]*")


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
    "studio.trust_unavailable": Entry(
        500, ProblemKind.CONFIG, "Trust not saved", "Studio could not save the trust decision in its state folder."
    ),
    "studio.internal_error": Entry(
        500,
        ProblemKind.INTERNAL,
        "Internal error",
        "Studio could not complete the request.",
        "The log entry with this log ID has the details.",
    ),
}


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
        return doc

    def body(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":")).encode("ascii")


def problem(
    code: str,
    *,
    log_id: str | None = None,
    sqlstate: str | None = None,
    position: int | None = None,
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
    return Problem(
        code, entry.kind.value, entry.status, entry.title, entry.detail, entry.hint, log_id, sqlstate, position
    )


def is_problem_code(value: object) -> bool:
    """Whether ``value`` is a code of the catalog (used to log only known codes)."""
    return isinstance(value, str) and value in CATALOG


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


def problem_response(code: str, *, log_id: str | None = None, headers: dict[str, str] | None = None) -> Response:
    """A Starlette response with the problem for ``code``."""
    doc = problem(code, log_id=log_id)
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
