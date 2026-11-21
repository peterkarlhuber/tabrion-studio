"""NS-003: Studio's local-session protection on the shared library ``nodestro-session``.

The modules ``security.launch``, ``security.sessions`` and ``security.access`` configure the library with Studio's
names; these tests pin what the configuration must keep: every neutral refusal code maps to a Studio problem code with
the same status, Studio's own order of checks still names the code of a refused odd path, the library's stricter
path rule stays in force, and the redirect page, the cookie and the state key are Studio's.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import nodestro_session
import pytest

from tabrion_studio.problems import CATALOG
from tabrion_studio.security import access, launch, sessions
from tabrion_studio.security.access import AccessMiddleware
from tabrion_studio.security.sessions import Sessions, cookie_name
from tests.support.access import HOST, PORT, TOKEN

pytestmark = pytest.mark.security


def _answer(
    headers: list[tuple[bytes, bytes]], path: str, method: str = "GET", store: Sessions | None = None
) -> tuple[int, str | None, dict[str, Any]]:
    """(status, problem code, scope state) of a raw ASGI request; status 0 and code None when it reached the app."""
    reached: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append(dict(scope.get("state", {})))

    async def send(message: Any) -> None:
        sent.append(message)

    async def receive() -> Any:
        return {"type": "http.request", "body": b""}

    middleware = AccessMiddleware(app, port=PORT, sessions=store or Sessions(), control_token=TOKEN)
    asyncio.run(middleware({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))
    if reached:
        return 0, None, reached[0]
    body = b"".join(message.get("body", b"") for message in sent[1:])
    return int(sent[0]["status"]), json.loads(body)["code"], {}


OWN = [(b"host", HOST.encode())]
JSON = [(b"content-type", b"application/json")]
BEARER = [(b"authorization", f"Bearer {TOKEN}".encode())]


def test_every_neutral_refusal_maps_to_a_studio_code_with_the_same_status() -> None:
    assert set(access.REFUSAL_CODES) == set(nodestro_session.REFUSALS)
    for neutral, code in access.REFUSAL_CODES.items():
        assert code in CATALOG, code
        assert CATALOG[code].status == nodestro_session.REFUSALS[neutral][0], code
    assert access.REFUSAL_CODES["not_found"] == "studio.not_found"


@pytest.mark.parametrize(
    ("path", "method", "headers", "status", "code"),
    [
        # Studio saw no control path here (case-sensitive prefix): an unsafe request without Origin is refused as
        # before, also with the control token, which the library would accept for its case-insensitive prefix.
        ("/Api/control/stop", "POST", JSON + BEARER, 403, "access.origin_required"),
        ("/api/CONTROL/stop", "GET", BEARER, 401, "access.session_required"),
        # Studio's control prefix with an empty segment: still needs the token first (STU-016 F-8).
        ("/api/control//proof", "GET", [], 401, "access.control_denied"),
        # With the token, the library's stricter path rule refuses it.
        ("/api/control//proof", "GET", BEARER, 404, "studio.not_found"),
        # Paths that reached the app before and are refused now (the library's rule, on every path).
        ("/a//b", "GET", [], 404, "studio.not_found"),
        ("/static/../api/x", "GET", [], 404, "studio.not_found"),
        ("/x/%2e%2e/api/x", "GET", [], 404, "studio.not_found"),
        ("/api%2fhealth", "GET", [], 404, "studio.not_found"),
        # Studio's own rule keeps its place, before the browser and token checks.
        ("/api/control/../stop", "POST", [*JSON, (b"origin", b"http://127.0.0.1:1234")], 404, "studio.not_found"),
    ],
)
def test_odd_paths_keep_studios_codes_and_stay_refused(
    path: str, method: str, headers: list[tuple[bytes, bytes]], status: int, code: str
) -> None:
    assert _answer(OWN + headers, path, method)[:2] == (status, code)


def test_plain_requests_still_pass() -> None:
    assert _answer(OWN, "/api/health")[0] == 0
    assert _answer(OWN, "/api/control/proof")[0] == 0
    assert _answer(OWN + BEARER + JSON, "/api/control/stop", "POST")[0] == 0
    assert _answer(OWN, "/assets/index-Ab12.js")[0] == 0


def test_a_session_request_carries_the_digest_under_studios_state_key() -> None:
    store = Sessions()
    session_id, csrf = store.create()
    headers = [*OWN, (b"cookie", f"{cookie_name(PORT)}={session_id}".encode()), (b"x-tabrion-csrf", csrf.encode())]
    status, _, state = _answer(headers, "/api/heartbeat", store=store)
    assert status == 0
    assert state == {"studio_session": nodestro_session.session_digest(session_id)}


def test_studios_names_are_kept() -> None:
    assert sessions.CSRF_HEADER == "X-Tabrion-CSRF"
    assert cookie_name(PORT) == f"tabrion_studio_session_{PORT}"
    assert launch.LaunchTokens("http://127.0.0.1:1").prefix == "studio"
    assert (
        launch.redirect_page('http://127.0.0.1:1/launch#a"<b')
        == (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="referrer" content="no-referrer">'
            '<meta http-equiv="refresh" content="0;url=http://127.0.0.1:1/launch#a&quot;&lt;b">'
            "<title>Tabrion Studio</title></head>"
            "<body><p>Opening Tabrion Studio…</p></body></html>\n"
        ).encode()
    )


def test_new_secrets_are_registered_for_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[str] = []
    monkeypatch.setattr(sessions, "register_secret", registered.append)
    monkeypatch.setattr(launch, "register_secret", registered.append)
    session_id, csrf = Sessions().create()
    issued = launch.LaunchTokens("http://127.0.0.1:1").issue(redirect=False)
    # Bound at construction: the module attribute is read when the store or the tokens are made.
    assert registered[:2] == [session_id, csrf]
    assert registered[2] == issued.url.rsplit("#", 1)[1]


def test_studios_security_headers_are_the_librarys_with_studios_policy() -> None:
    """The CSP stays spelled out in ``security/access.py`` (the e2e CSP probe reads it there); today it equals the
    library's, so every answer carries the same headers through the guard and through the request log."""
    assert access.SECURITY_HEADERS == nodestro_session.SECURITY_HEADERS
    assert dict(access.SECURITY_HEADERS)["content-security-policy"] == access.CONTENT_SECURITY_POLICY
