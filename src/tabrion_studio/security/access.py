"""The access middleware: every request passes these checks before any route sees it (STU-003).

Threats (03 section 7.3) and review findings, in the order of the checks:

1. **Host allowlist (T-01, DNS rebinding; F-05, F-08).** Exactly one ``Host`` header, equal to ``127.0.0.1:<port>``,
   on **every** route: the API, the control endpoints, the proof, the heartbeat, the health check and the static
   files. ``localhost`` and ``[::1]`` are not accepted: Studio prints and opens only ``127.0.0.1`` URLs, the
   ``[::1]`` guard (``server.bind_loopback``) may be missing on a host without IPv6, and a page that rebinds its own
   name to ``127.0.0.1`` sends that name. Refused: ``access.host_not_allowed`` (403).
2. **Forwarded headers (T-01).** ``Forwarded``, ``X-Forwarded-*`` and ``X-Real-IP`` are refused
   (``access.forwarded_not_allowed``): Studio is never meant to sit behind a proxy.
3. **Origin (T-02).** A request that carries an ``Origin`` other than ``http://127.0.0.1:<port>`` is refused
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

Right after the Origin check, an ``/api/*`` path with a ``.`` or ``..`` segment (also percent-encoded, as the
server decodes it) is refused as ``studio.not_found`` (404): no route has one, and a later mount or path
normalization must not turn ``/api/control/../x`` into a route outside the control prefix (F-8).

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
import hmac

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tabrion_studio.problems import problem, send_problem
from tabrion_studio.security.sessions import CSRF_HEADER, Sessions, cookie_name

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")
CONTROL_PREFIX = "/api/control/"
#: The one control path without the control token: a second start proves the server knows the token (review F-01).
CONTROL_PROOF_PATH = "/api/control/proof"
#: API routes that need no session: the health check (the CLI and the page use it) and the launch exchange.
OPEN_API_PATHS = frozenset({"/api/health", "/api/session"})

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CONTENT_SECURITY_POLICY),
    ("cache-control", "no-store"),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "no-referrer"),
    ("x-frame-options", "DENY"),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
)
_REPLACED = frozenset(name.encode() for name, _ in SECURITY_HEADERS) | {b"pragma", b"expires"}
_CSRF = CSRF_HEADER.lower()


def loopback_origin(port: int) -> str:
    """The only origin and ``Host`` Studio answers: ``http://127.0.0.1:<port>``."""
    return f"http://127.0.0.1:{port}"


def session_cookie(headers: list[tuple[str, str]], name: str) -> list[str]:
    """Every value of the cookie ``name`` in the ``Cookie`` headers (another local server may have set a second one)."""
    values = []
    for key, value in headers:
        if key != "cookie":
            continue
        for part in value.split(";"):
            cookie, sep, content = part.strip().partition("=")
            if sep and cookie == name:
                values.append(content.strip().strip('"'))
    return values


class AccessMiddleware:
    """Applies the checks of this module to every HTTP request and the security headers to every answer."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        port: int,
        sessions: Sessions,
        control_token: str,
        on_activity: Callable[[], None] = lambda: None,
    ) -> None:
        self.app = app
        # An empty token opens nothing: every control path but the proof is then refused.
        self.control_token = control_token.encode("utf-8")
        self.port = port
        self.origin = loopback_origin(port)
        self.host = self.origin.removeprefix("http://")
        self.sessions = sessions
        self.cookie = cookie_name(port)
        self.on_activity = on_activity

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # Studio serves no WebSocket (Uvicorn runs with ws="none"); refuse one that arrives anyway.
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        send = with_security_headers(send)
        refusal = self._refusal(scope)
        if refusal is not None:
            # A problem document with fixed texts (STU-004); the status comes from the catalog.
            await send_problem(send, problem(refusal))
            return
        await self.app(scope, receive, send)

    def _refusal(self, scope: Scope) -> str | None:
        headers = [(key.decode("latin-1").lower(), value.decode("latin-1")) for key, value in scope["headers"]]
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")
        hosts = [value.strip().lower() for key, value in headers if key == "host"]
        if hosts != [self.host]:
            return "access.host_not_allowed"
        if any(key in FORWARDING_HEADERS for key, _ in headers):
            return "access.forwarded_not_allowed"
        origins = [value.strip().lower() for key, value in headers if key == "origin"]
        if any(origin != self.origin for origin in origins):
            return "access.origin_not_allowed"
        api = path == "/api" or path.startswith("/api/")
        control = path.startswith(CONTROL_PREFIX)
        if api and any(segment in (".", "..") for segment in path.split("/")):
            return "studio.not_found"
        unsafe = method not in SAFE_METHODS
        if control:
            if origins or any(key.startswith("sec-fetch-") for key, _ in headers):
                return "access.browser_not_allowed"
        else:
            if api:
                sites = {value.strip().lower() for key, value in headers if key == "sec-fetch-site"}
                if sites - {"same-origin", "none"}:
                    return "access.cross_site"
            if unsafe and not origins:
                return "access.origin_required"
        if api and unsafe:
            content_types = [value.split(";", 1)[0].strip().lower() for key, value in headers if key == "content-type"]
            if content_types != ["application/json"]:
                return "access.json_only"
        proof = path == CONTROL_PROOF_PATH and method in ("GET", "HEAD")
        if control and not proof and not self._control_authorized(headers):
            return "access.control_denied"
        if api and not control and path not in OPEN_API_PATHS:
            candidates = session_cookie(headers, self.cookie)
            known = [value for value in candidates if self.sessions.known(value)]
            if not known:
                return "access.session_required"
            csrf = [value for key, value in headers if key == _CSRF]
            if len(csrf) != 1 or not any(self.sessions.check(value, csrf[0]) for value in known):
                return "access.csrf_invalid"
            self.on_activity()
        return None

    def _control_authorized(self, headers: list[tuple[str, str]]) -> bool:
        values = [value for key, value in headers if key == "authorization"]
        if len(values) != 1 or not self.control_token:
            return False
        scheme, _, token = values[0].partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(token.encode("utf-8"), self.control_token)


def with_security_headers(send: Send) -> Send:
    """``send`` that replaces the security headers of every answer with :data:`SECURITY_HEADERS`."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            kept = [(key, value) for key, value in message.get("headers", []) if key.lower() not in _REPLACED]
            added = [(name.encode(), value.encode()) for name, value in SECURITY_HEADERS]
            message = {**message, "headers": kept + added}
        await send(message)

    return wrapped
