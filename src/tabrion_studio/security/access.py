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
   control endpoints (which check their own bearer token) needs the session cookie (``access.session_required``,
   401) **and** the session's CSRF value in ``X-Tabrion-CSRF`` (``access.csrf_invalid``, 403). A valid session
   counts as activity for the idle timeout (F-06).

Every answer, including refusals and errors of the routes, gets the security headers: a strict
``Content-Security-Policy`` (T-07, SQ-08), ``Cache-Control: no-store`` (T-15), ``X-Content-Type-Options: nosniff``,
``Referrer-Policy: no-referrer``, ``X-Frame-Options: DENY`` and same-origin opener and resource policies.

This middleware is not a user login: it keeps other web pages, other local users and DNS rebinding away from a
single-user tool on loopback (STU-ADR-0002).
"""

from __future__ import annotations

from collections.abc import Callable
import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tabrion_studio.security.sessions import CSRF_HEADER, Sessions, cookie_name

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")
CONTROL_PREFIX = "/api/control/"
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
        self, app: ASGIApp, *, port: int, sessions: Sessions, on_activity: Callable[[], None] = lambda: None
    ) -> None:
        self.app = app
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
        send = _with_security_headers(send)
        refusal = self._refusal(scope)
        if refusal is not None:
            status, code, detail = refusal
            await _refuse(send, status, code, detail)
            return
        await self.app(scope, receive, send)

    def _refusal(self, scope: Scope) -> tuple[int, str, str] | None:
        headers = [(key.decode("latin-1").lower(), value.decode("latin-1")) for key, value in scope["headers"]]
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")
        hosts = [value.strip().lower() for key, value in headers if key == "host"]
        if hosts != [self.host]:
            return 403, "access.host_not_allowed", f"only requests for {self.host} are answered"
        forwarded = sorted({key for key, _ in headers if key in FORWARDING_HEADERS})
        if forwarded:
            return 403, "access.forwarded_not_allowed", f"forwarded requests are not answered ({', '.join(forwarded)})"
        origins = [value.strip().lower() for key, value in headers if key == "origin"]
        if any(origin != self.origin for origin in origins):
            return 403, "access.origin_not_allowed", "requests from other web pages are not answered"
        api = path == "/api" or path.startswith("/api/")
        control = path.startswith(CONTROL_PREFIX)
        unsafe = method not in SAFE_METHODS
        if control:
            if origins or any(key.startswith("sec-fetch-") for key, _ in headers):
                return 403, "access.browser_not_allowed", "the control endpoint is for the tabrion-studio command only"
        else:
            if api:
                sites = {value.strip().lower() for key, value in headers if key == "sec-fetch-site"}
                if sites - {"same-origin", "none"}:
                    return 403, "access.cross_site", "requests from other sites are not answered"
            if unsafe and not origins:
                return 403, "access.origin_required", "changes need the Origin header of a Studio page"
        if api and unsafe:
            content_types = [value.split(";", 1)[0].strip().lower() for key, value in headers if key == "content-type"]
            if content_types != ["application/json"]:
                return 415, "access.json_only", "the API accepts JSON bodies only (Content-Type: application/json)"
        if api and not control and path not in OPEN_API_PATHS:
            candidates = session_cookie(headers, self.cookie)
            known = [value for value in candidates if self.sessions.known(value)]
            if not known:
                return 401, "access.session_required", "open Studio with the tabrion-studio command"
            csrf = [value for key, value in headers if key == _CSRF]
            if len(csrf) != 1 or not any(self.sessions.check(value, csrf[0]) for value in known):
                return 403, "access.csrf_invalid", f"the request lacks the session's {CSRF_HEADER} header"
            self.on_activity()
        return None


def _with_security_headers(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            kept = [(key, value) for key, value in message.get("headers", []) if key.lower() not in _REPLACED]
            added = [(name.encode(), value.encode()) for name, value in SECURITY_HEADERS]
            message = {**message, "headers": kept + added}
        await send(message)

    return wrapped


async def _refuse(send: Send, status: int, code: str, detail: str) -> None:
    body = json.dumps({"code": code, "detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
