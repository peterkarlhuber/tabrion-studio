"""The access middleware: every request passes these checks before any route sees it (STU-003).

The checks are :class:`nodestro_session.SessionGuard` of the shared library (NS-003), configured with Studio's
prefixes, header, cookie and codes; this module maps the library's neutral refusal codes to Studio's ``access.*``
codes (``not_found`` to ``studio.not_found``).

Threats (03 section 7.3) and review findings, in the order of the checks:

1. **Host allowlist (T-01, DNS rebinding; F-05, F-08, A-01).** Exactly one ``Host`` header, equal to
   ``<address>:<port>`` of this instance (its random ``127.a.b.c``, or ``127.0.0.1`` where only that exists), on
   **every** route: the API, the control endpoints, the proof, the heartbeat, the health check and the static
   files. ``localhost``, ``[::1]`` and every other loopback address are not accepted: Studio prints and opens only
   URLs with its own address, the ``[::1]`` guard (``server.bind_loopback``) may be missing on a host without IPv6,
   and a page that rebinds its own name to a loopback address sends that name. Refused: ``access.host_not_allowed``
   (403).
2. **Forwarded headers (T-01).** ``Forwarded``, ``X-Forwarded-*`` and ``X-Real-IP`` are refused
   (``access.forwarded_not_allowed``): Studio is never meant to sit behind a proxy.
3. **Origin (T-02).** A request that carries an ``Origin`` other than ``http://<address>:<port>`` is refused
   (``access.origin_not_allowed``); every unsafe method outside the control endpoints must carry exactly that
   ``Origin`` (``access.origin_required``), which browsers send with every ``POST``, ``PUT``, ``PATCH`` and ``DELETE``.
4. **Browser headers on the control endpoints (F-05).** ``/api/control/*`` (including the proof) refuses any request
   with ``Origin`` or a ``Sec-Fetch-*`` header (``access.browser_not_allowed``). The CLI never sends them and every
   current browser does, so a page can never drive the control channel, even with the token.
5. **Fetch metadata.** Other ``/api/*`` requests with ``Sec-Fetch-Site: cross-site`` or ``same-site`` are refused
   (``access.cross_site``). ``same-site`` matters here: a page on another port of ``127.0.0.1`` is same-site.
6. **JSON only (T-02).** Unsafe ``/api/*`` requests need ``Content-Type: application/json`` (``access.json_only``,
   415), which a form or a ``fetch`` without preflight cannot send.
7. **Session and CSRF header (T-03, SQ-03, F-09).** Every ``/api/*`` route except :data:`OPEN_API_PATHS` and the
   control endpoints needs the session cookie (``access.session_required``, 401) **and** the session's CSRF value in
   ``X-Tabrion-CSRF`` (``access.csrf_invalid``, 403). A valid session counts as activity for the idle timeout (F-06).
8. **Control token (STU-016 review F-8).** Every request under ``/api/control/`` except ``GET`` and ``HEAD`` of
   exactly :data:`CONTROL_PROOF_PATH` needs the instance's control token as ``Authorization: Bearer``
   (``access.control_denied``, 401), checked here in constant time and again by the route. A route added under the
   prefix later is therefore never reachable without the token, and the prefix is never a way around the session.

Right after the Origin check, an unusual path is refused as ``studio.not_found`` (404): a ``.`` or ``..`` segment
(also percent-encoded, as the server decodes it), an empty segment, a backslash, a NUL or a still-encoded dot,
slash, backslash, percent sign or NUL, on every path. No route has one, and a later mount or path normalization must
not turn ``/api/control/../x`` into a route outside the control prefix (F-8). The ``/api`` and control prefixes are
matched case-insensitively (and after Unicode normalization), so such a path is only ever classified more strictly.

A refusal is a problem document (``application/problem+json``, :mod:`tabrion_studio.problems`, STU-004) with the
code named above and fixed texts. Every answer, including refusals and errors of the routes, gets the security
headers: a strict ``Content-Security-Policy`` (T-07, SQ-08), ``Cache-Control: no-store`` (T-15),
``X-Content-Type-Options: nosniff``, ``Referrer-Policy: no-referrer``, ``X-Frame-Options: DENY`` and same-origin
opener and resource policies.

This middleware is not a user login: it keeps other web pages, other local users and DNS rebinding away from a
single-user tool on loopback (STU-ADR-0002).
"""

from __future__ import annotations

from collections.abc import Callable

from nodestro_session import FORWARDING_HEADERS, Refusal, SessionGuard
from nodestro_session import SECURITY_HEADERS as _LIBRARY_SECURITY_HEADERS
from nodestro_session import cookie_values as _cookie_values
from nodestro_session import loopback_origin as _loopback_origin
from nodestro_session.guard import SAFE_METHODS
from nodestro_session.guard import with_security_headers as _with_security_headers
from starlette.types import ASGIApp, Scope, Send

from tabrion_studio.problems import problem, send_problem
from tabrion_studio.security.sessions import CSRF_HEADER, Sessions, cookie_name

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "CONTROL_PREFIX",
    "CONTROL_PROOF_PATH",
    "FORWARDING_HEADERS",
    "OPEN_API_PATHS",
    "REFUSAL_CODES",
    "SAFE_METHODS",
    "SECURITY_HEADERS",
    "AccessMiddleware",
    "loopback_origin",
    "session_cookie",
    "with_security_headers",
]

#: Studio's Content-Security-Policy (T-07, SQ-08), spelled out here: Studio owns its policy, and the e2e CSP probe
#: (frontend/e2e/csp.spec.ts) reads it from this file. It equals the library's default today.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
#: The security headers of every answer: the library's set, with Studio's Content-Security-Policy.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = tuple(
    (name, CONTENT_SECURITY_POLICY if name == "content-security-policy" else value)
    for name, value in _LIBRARY_SECURITY_HEADERS
)
CONTROL_PREFIX = "/api/control/"
#: The one control path without the control token: a second start proves the server knows the token (review F-01).
CONTROL_PROOF_PATH = "/api/control/proof"
#: API routes that need no session: the health check (the CLI and the page use it) and the launch exchange.
OPEN_API_PATHS = frozenset({"/api/health", "/api/session"})
#: The library's neutral refusal codes and Studio's problem codes for them (:mod:`tabrion_studio.problems`).
REFUSAL_CODES: dict[str, str] = {
    "host_not_allowed": "access.host_not_allowed",
    "forwarded_not_allowed": "access.forwarded_not_allowed",
    "origin_not_allowed": "access.origin_not_allowed",
    "not_found": "studio.not_found",
    "browser_not_allowed": "access.browser_not_allowed",
    "cross_site": "access.cross_site",
    "origin_required": "access.origin_required",
    "json_only": "access.json_only",
    "control_denied": "access.control_denied",
    "session_required": "access.session_required",
    "csrf_invalid": "access.csrf_invalid",
}
#: Where the write unlocks (STU-XW-1) find the session's digest: routes get it, never the cookie value.
SESSION_STATE_KEY = "studio_session"


def loopback_origin(port: int, host: str = "127.0.0.1") -> str:
    """The only origin and ``Host`` Studio answers: ``http://<host>:<port>``, ``host`` a ``127.a.b.c`` (A-01)."""
    return _loopback_origin(port, host)


def session_cookie(headers: list[tuple[str, str]], name: str) -> list[str]:
    """Every value of the cookie ``name`` in the ``Cookie`` headers (another local server may have set a second one)."""
    return _cookie_values(headers, name)


def with_security_headers(send: Send) -> Send:
    """``send`` that replaces the security headers of every answer with :data:`SECURITY_HEADERS`."""
    return _with_security_headers(send, SECURITY_HEADERS)


async def _refuse(scope: Scope, send: Send, refusal: Refusal) -> None:
    # A problem document with fixed texts (STU-004); the status comes from Studio's catalog.
    await send_problem(send, problem(REFUSAL_CODES[refusal.code]))


class AccessMiddleware(SessionGuard):
    """Applies the checks of this module to every HTTP request and the security headers to every answer."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        port: int,
        host: str = "127.0.0.1",
        sessions: Sessions,
        control_token: str,
        on_activity: Callable[[], None] = lambda: None,
    ) -> None:
        # An empty token opens nothing: every control path but the proof is then refused.
        super().__init__(
            app,
            origin=loopback_origin(port, host),
            sessions=sessions,
            cookie=cookie_name(port),
            csrf_header=CSRF_HEADER,
            api_prefix="/api",
            open_paths=OPEN_API_PATHS,
            control_prefix=CONTROL_PREFIX,
            control_token=control_token,
            control_open_paths={CONTROL_PROOF_PATH},
            security_headers=SECURITY_HEADERS,
            refuse=_refuse,
            on_activity=on_activity,
            state_key=SESSION_STATE_KEY,
        )
        self.port = port

    def refusal(self, scope: Scope) -> str | None:
        """The library's verdict; for an odd path, refused with the code of Studio's own checks where they refuse it.

        Studio checked one path rule of its own, right after the Origin: a ``.`` or ``..`` segment under ``/api``
        (``studio.not_found``), and it matched ``/api`` and ``/api/control/`` case-sensitively. The library refuses
        more paths (an empty segment, a backslash, a NUL, a still-encoded dot or slash, also outside ``/api``) and
        matches the prefixes case-insensitively and after Unicode normalization. For such an odd path (refused as
        unusual, or with an upper-case or non-ASCII character) Studio's checks are run as well, on a plain path of the
        area Studio gave it (a control path needs the control token, an API path the session): when they refuse, their
        code answers, as before; otherwise the library's verdict stands (``not_found`` for an unusual path). So a
        request that Studio refused keeps its code, and every request that the library refuses stays refused.
        """
        code = super().refusal(scope)
        path: str = scope.get("path", "")
        if _studio_not_found(path) or not (code == "not_found" or not path.isascii() or path != path.lower()):
            return code
        studio = self._studio_refusal(scope, path)
        return studio if studio is not None else code

    def _studio_refusal(self, scope: Scope, path: str) -> str | None:
        """Studio's checks for ``path``, on a plain path of its area, without counting activity or touching state."""
        on_activity, self.on_activity = self.on_activity, lambda: None
        try:
            return super().refusal({**scope, "path": _plain_path(path), "state": {}})
        finally:
            self.on_activity = on_activity


def _studio_not_found(path: str) -> bool:
    """Studio's own path rule (F-8): an ``/api`` path with a ``.`` or ``..`` segment."""
    api = path == "/api" or path.startswith("/api/")
    return api and any(segment in (".", "..") for segment in path.split("/"))


def _plain_path(path: str) -> str:
    """A plain path of the area Studio gives ``path`` (case-sensitive prefixes), that is no route and is not open."""
    if path.startswith(CONTROL_PREFIX):
        return CONTROL_PREFIX + "-"
    if path == "/api" or path.startswith("/api/"):
        return "/api/-"
    return "/-"
