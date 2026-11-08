"""STU-003 access protection against web pages and other local programs (STU-T-001..-005, -014, -026).

Each test names the threat (03 section 7.3) or review finding it covers. The app runs in process with a fake runtime
on port 1234 (tests/support/access.py); the launch flow with real processes is in tests/security/test_launch.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any

import pytest

from tabrion_studio.security.access import CONTENT_SECURITY_POLICY, SECURITY_HEADERS, AccessMiddleware, session_cookie
from tabrion_studio.security.sessions import CSRF_HEADER, Sessions
from tests.support.access import CONTROL_AUTH, HOST, ORIGIN, PORT, Studio, make_studio

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
NONCE = "a" * 32

# Every route of the app, with a body where the route takes one (F-05: the allowlist applies to all of them).
ROUTES: list[tuple[str, str]] = [
    ("GET", "/"),
    ("GET", "/launch"),
    ("GET", "/assets/app.js"),
    ("GET", "/api/health"),
    ("GET", "/api/heartbeat"),
    ("GET", "/api/openapi.json"),
    ("GET", f"/api/control/proof?nonce={NONCE}"),
    ("POST", "/api/control/launch"),
    ("POST", "/api/control/shutdown"),
    ("POST", "/api/session"),
]
FOREIGN_HOSTS = [
    "evil.example",
    f"evil.example:{PORT}",
    f"127.0.0.1.nip.io:{PORT}",  # a DNS name that resolves to 127.0.0.1 (DNS rebinding)
    f"localhost:{PORT}",  # F-08: only 127.0.0.1 is answered
    f"[::1]:{PORT}",
    f"localhost.:{PORT}",
    "127.0.0.1",  # no port
    f"127.0.0.1:{PORT + 1}",
    f"127.0.0.2:{PORT}",
    f"0.0.0.0:{PORT}",
    f"127.0.0.1:{PORT}.evil.example",
    f"127.0.0.1:{PORT}@evil.example",
    f"2130706433:{PORT}",  # 127.0.0.1 as a single number
]


def _assert_refused(response: object, status: int, code: str) -> None:
    assert response.status_code == status, response.text  # type: ignore[attr-defined]
    assert response.json()["code"] == code  # type: ignore[attr-defined]
    _assert_security_headers(response)


def _assert_security_headers(response: object) -> None:
    headers = response.headers  # type: ignore[attr-defined]
    for name, value in SECURITY_HEADERS:
        assert headers.get(name) == value, name


def _signed_in_headers(studio: Studio) -> dict[str, str]:
    return {**studio.sign_in(), "Origin": ORIGIN}


def _request(studio: Studio, method: str, path: str, headers: dict[str, str]) -> object:
    kwargs: dict[str, object] = {"headers": headers}
    if method == "POST":
        kwargs["json"] = {"token": studio.launch_token()} if path == "/api/session" else {}
    return studio.client.request(method, path, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------------------------
# STU-T-001 (T-01, F-05, F-08): Host allowlist on every route, DNS rebinding
# --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("host", FOREIGN_HOSTS)
@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_host_allowlist_on_every_route(static_dir: Path, method: str, path: str, host: str) -> None:
    # Valid control token, valid session and the page's own Origin: only the Host is wrong.
    studio = make_studio(static_dir)
    headers = {**CONTROL_AUTH, **_signed_in_headers(studio)} if "/control/" not in path else dict(CONTROL_AUTH)
    sessions = len(studio.runtime.sessions)
    response = _request(studio, method, path, {**headers, "Host": host})
    _assert_refused(response, 403, "access.host_not_allowed")
    assert studio.shutdowns == []
    assert len(studio.runtime.sessions) == sessions


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_the_own_host_is_answered(static_dir: Path, method: str, path: str) -> None:
    studio = make_studio(static_dir)
    headers = dict(CONTROL_AUTH) if "/control/" in path else _signed_in_headers(studio)
    response = _request(studio, method, path, headers)
    assert response.status_code in (200, 202, 204), response.text  # type: ignore[attr-defined]
    _assert_security_headers(response)


def test_dns_rebinding_page_cannot_read_the_api(static_dir: Path) -> None:
    # A page of evil.example that rebinds its name to 127.0.0.1 is same-origin to itself and sends its own Host.
    studio = make_studio(static_dir)
    for path in ("/api/health", "/api/openapi.json", "/api/heartbeat", "/"):
        response = studio.client.get(path, headers={"Host": "evil.example:1234", "Sec-Fetch-Site": "same-origin"})
        _assert_refused(response, 403, "access.host_not_allowed")
        assert "abc123" not in response.text


def _asgi_status(headers: list[tuple[bytes, bytes]], path: str = "/api/health", method: str = "GET") -> int:
    """Run the middleware on a raw ASGI scope; 0 if the request reached the app, else the refusal's status."""
    reached: list[str] = []
    sent: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append("app")

    async def send(message: Any) -> None:
        sent.append(message)

    async def receive() -> Any:
        return {"type": "http.request", "body": b""}

    middleware = AccessMiddleware(app, port=PORT, sessions=Sessions())
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    asyncio.run(middleware(scope, receive, send))
    return 0 if reached else int(sent[0]["status"])


def test_missing_or_duplicate_host_is_refused() -> None:
    own = (b"host", HOST.encode())
    assert _asgi_status([own]) == 0  # reaches the app
    assert _asgi_status([]) == 403
    assert _asgi_status([own, (b"host", b"evil.example")]) == 403
    assert _asgi_status([own, own]) == 403


def test_websockets_are_refused() -> None:
    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("reached the app")

    async def receive() -> Any:
        return {"type": "websocket.connect"}

    middleware = AccessMiddleware(app, port=PORT, sessions=Sessions())
    scope = {"type": "websocket", "path": "/api/events", "headers": [(b"host", HOST.encode())]}
    asyncio.run(middleware(scope, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


# --------------------------------------------------------------------------------------------------------------------
# STU-T-002 (T-01): forwarded requests; only 127.0.0.1 URLs (the CLI side is in test_launch.py)
# --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    ["Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP"],
)
@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_forwarded_requests_are_refused(static_dir: Path, method: str, path: str, header: str) -> None:
    studio = make_studio(static_dir)
    headers = dict(CONTROL_AUTH) if "/control/" in path else _signed_in_headers(studio)
    response = _request(studio, method, path, {**headers, header: "127.0.0.1"})
    _assert_refused(response, 403, "access.forwarded_not_allowed")
    assert studio.shutdowns == []


# --------------------------------------------------------------------------------------------------------------------
# STU-T-003 (T-02, F-05): Origin and fetch metadata
# --------------------------------------------------------------------------------------------------------------------

FOREIGN_ORIGINS = [
    "http://evil.example",
    "null",  # sandboxed frames and file:// pages
    f"http://127.0.0.1:{PORT + 1}",  # another program on this computer: same site, other port
    f"http://localhost:{PORT}",
    f"https://127.0.0.1:{PORT}",
    f"http://[::1]:{PORT}",
]


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if "/control/" not in r[1]])
def test_foreign_origin_is_refused(static_dir: Path, method: str, path: str, origin: str) -> None:
    studio = make_studio(static_dir)
    headers = {**_signed_in_headers(studio), "Origin": origin}
    sessions = len(studio.runtime.sessions)
    response = _request(studio, method, path, headers)
    _assert_refused(response, 403, "access.origin_not_allowed")
    assert len(studio.runtime.sessions) == sessions


def test_unsafe_requests_need_the_own_origin(static_dir: Path) -> None:
    # Browsers send Origin with every POST; a request without one did not come from a Studio page.
    studio = make_studio(static_dir)
    response = studio.client.post("/api/session", json={"token": studio.launch_token()})
    _assert_refused(response, 403, "access.origin_required")
    assert len(studio.runtime.sessions) == 0
    for method in ("PUT", "PATCH", "DELETE"):
        _assert_refused(studio.client.request(method, "/api/heartbeat"), 403, "access.origin_required")


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
@pytest.mark.parametrize(
    ("method", "path"), [r for r in ROUTES if r[1].startswith("/api/") and "/control/" not in r[1]]
)
def test_cross_site_api_requests_are_refused(static_dir: Path, method: str, path: str, site: str) -> None:
    # same-site matters: a page on another port of 127.0.0.1 is same-site with Studio.
    studio = make_studio(static_dir)
    headers = {**_signed_in_headers(studio), "Sec-Fetch-Site": site}
    response = _request(studio, method, path, headers)
    _assert_refused(response, 403, "access.cross_site")


def test_navigation_from_other_sites_reaches_the_pages(static_dir: Path) -> None:
    # The redirect file (file://) and links open /launch and / as a cross-site navigation: the pages hold no data.
    client = make_studio(static_dir).client
    for path in ("/", "/launch"):
        assert (
            client.get(path, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}).status_code == 200
        )


BROWSER_HEADERS = [
    {"Origin": ORIGIN},  # even the page's own origin
    {"Origin": "http://evil.example"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Sec-Fetch-Mode": "cors"},
    {"Sec-Fetch-Dest": "empty"},
    {"Sec-Fetch-User": "?1"},
]


@pytest.mark.parametrize("extra", BROWSER_HEADERS, ids=lambda h: "-".join(f"{k}={v}" for k, v in h.items()))
@pytest.mark.parametrize("path", ["/api/control/launch", "/api/control/shutdown", f"/api/control/proof?nonce={NONCE}"])
def test_control_rejects_browser_headers(static_dir: Path, path: str, extra: dict[str, str]) -> None:
    # F-05: with a valid token a browser still cannot drive the control endpoints; the CLI never sends these headers.
    studio = make_studio(static_dir)
    method = "GET" if "proof" in path else "POST"
    response = _request(studio, method, path, {**CONTROL_AUTH, **extra})
    code = "access.origin_not_allowed" if extra.get("Origin", ORIGIN) != ORIGIN else "access.browser_not_allowed"
    _assert_refused(response, 403, code)
    assert "proof" not in response.text  # type: ignore[attr-defined]
    assert studio.shutdowns == []
    assert studio.runtime.launches.pending == 0


# --------------------------------------------------------------------------------------------------------------------
# STU-T-004 (T-02, SQ-03, F-09): JSON only and the CSRF header
# --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [None, "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain", "application/jsonx"],
)
def test_api_accepts_json_only(static_dir: Path, content_type: str | None) -> None:
    # A form or a fetch without preflight can only send these types.
    studio = make_studio(static_dir)
    headers = {"Origin": ORIGIN} if content_type is None else {"Origin": ORIGIN, "Content-Type": content_type}
    body = f'{{"token": "{studio.launch_token()}"}}'.encode()
    _assert_refused(studio.client.post("/api/session", content=body, headers=headers), 415, "access.json_only")
    assert len(studio.runtime.sessions) == 0
    control_headers = {**CONTROL_AUTH} if content_type is None else {**CONTROL_AUTH, "Content-Type": content_type}
    response = studio.client.post("/api/control/shutdown", content=b"{}", headers=control_headers)
    _assert_refused(response, 415, "access.json_only")
    assert studio.shutdowns == []


def test_json_with_a_charset_is_accepted(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    body = f'{{"token": "{studio.launch_token()}"}}'.encode()
    headers = {"Origin": ORIGIN, "Content-Type": "application/json; charset=utf-8"}
    assert studio.client.post("/api/session", content=body, headers=headers).status_code == 200


def test_cookie_alone_is_useless_without_csrf_header(static_dir: Path) -> None:
    # F-09: cookies are not port-scoped. A server on another local port that the browser visits receives the Studio
    # cookie, but not the CSRF value, which only lives in the page's sessionStorage.
    studio = make_studio(static_dir, idle_seconds=60)
    csrf = studio.sign_in()[CSRF_HEADER]
    studio.make_idle()
    for headers in ({}, {CSRF_HEADER: ""}, {CSRF_HEADER: "wrong"}, {CSRF_HEADER: csrf[:-1]}, {"Cookie-CSRF": csrf}):
        for path in ("/api/heartbeat", "/api/openapi.json"):
            _assert_refused(studio.client.get(path, headers=headers), 403, "access.csrf_invalid")
    assert studio.runtime.idle.idle_for() >= 50  # refused requests are no activity
    assert studio.client.get("/api/heartbeat", headers={CSRF_HEADER: csrf}).status_code == 204


def test_csrf_value_is_bound_to_its_session(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    first = studio.sign_in()[CSRF_HEADER]
    first_cookie = studio.client.cookies.get(f"tabrion_studio_session_{PORT}")
    second = studio.sign_in()[CSRF_HEADER]
    assert first != second
    # The client now holds the second cookie; the first session's CSRF value does not fit it.
    _assert_refused(studio.client.get("/api/heartbeat", headers={CSRF_HEADER: first}), 403, "access.csrf_invalid")
    assert studio.client.get("/api/heartbeat", headers={CSRF_HEADER: second}).status_code == 204
    studio.client.cookies.set(f"tabrion_studio_session_{PORT}", first_cookie or "")
    assert studio.client.get("/api/heartbeat", headers={CSRF_HEADER: first}).status_code == 204


def test_csrf_value_never_travels_in_a_cookie(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    response = studio.client.post("/api/session", json={"token": studio.launch_token()}, headers={"Origin": ORIGIN})
    csrf = response.json()["csrf"]
    assert csrf not in response.headers.get("set-cookie", "")
    assert len(response.headers.get_list("set-cookie")) == 1


def test_csrf_header_without_a_session_is_refused(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    csrf = studio.sign_in()[CSRF_HEADER]
    studio.client.cookies.clear()
    _assert_refused(studio.client.get("/api/heartbeat", headers={CSRF_HEADER: csrf}), 401, "access.session_required")


def test_session_cookie_parsing() -> None:
    name = f"tabrion_studio_session_{PORT}"
    headers = [("cookie", f"a=1; {name}=one; b=2"), ("cookie", f'{name}="two"'), ("x", f"{name}=three")]
    assert session_cookie(headers, name) == ["one", "two"]
    assert session_cookie([("cookie", f"{name}x=1; {name}")], name) == []


def test_a_tossed_cookie_does_not_hide_the_real_session(static_dir: Path) -> None:
    # Another local server can set a second cookie with Studio's name for 127.0.0.1; the real one still counts,
    # and the tossed one alone opens nothing.
    studio = make_studio(static_dir)
    csrf = studio.sign_in()[CSRF_HEADER]
    real = studio.client.cookies.get(f"tabrion_studio_session_{PORT}")
    name = f"tabrion_studio_session_{PORT}"
    studio.client.cookies.clear()
    tossed = {"Cookie": f"{name}=attacker; {name}={real}", CSRF_HEADER: csrf}
    assert studio.client.get("/api/heartbeat", headers=tossed).status_code == 204
    only_tossed = {"Cookie": f"{name}=attacker", CSRF_HEADER: csrf}
    _assert_refused(studio.client.get("/api/heartbeat", headers=only_tossed), 401, "access.session_required")


# --------------------------------------------------------------------------------------------------------------------
# STU-T-005 (T-03): a session only through a one-time launch token; without the cookie 401
# --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/heartbeat", "/api/openapi.json", "/api/anything", "/api", "/api/heartbeat/"])
def test_api_needs_a_session(static_dir: Path, path: str) -> None:
    # AT2: another local program reaches the port, but has no launch token and so no cookie.
    studio = make_studio(static_dir)
    _assert_refused(studio.client.get(path, headers={CSRF_HEADER: "x"}), 401, "access.session_required")


def test_launch_token_gives_a_session_cookie(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    response = studio.client.post("/api/session", json={"token": studio.launch_token()}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert set(response.json()) == {"csrf"}
    cookie = response.headers["set-cookie"]
    name, _, rest = cookie.partition("=")
    assert name == f"tabrion_studio_session_{PORT}"  # per port: two instances never overwrite each other's cookie
    attributes = {part.strip().split("=", 1)[0].lower(): part.strip() for part in rest.split(";")[1:]}
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "samesite=strict"
    assert attributes["path"] == "Path=/"
    assert "secure" not in attributes  # http on loopback
    assert "domain" not in attributes  # host-only
    assert "max-age" not in attributes and "expires" not in attributes  # ends with the browser session


def test_launch_token_works_once(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    token = studio.launch_token()
    first = studio.client.post("/api/session", json={"token": token}, headers={"Origin": ORIGIN})
    assert first.status_code == 200
    studio.client.cookies.clear()
    second = studio.client.post("/api/session", json={"token": token}, headers={"Origin": ORIGIN})
    _assert_refused(second, 401, "access.launch_used")
    assert "set-cookie" not in second.headers
    assert len(studio.runtime.sessions) == 1


def test_launch_token_expires(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    token = studio.launch_token()
    studio.clock.now += studio.runtime.launches.ttl + 1
    response = studio.client.post("/api/session", json={"token": token}, headers={"Origin": ORIGIN})
    _assert_refused(response, 401, "access.launch_expired")
    assert len(studio.runtime.sessions) == 0


@pytest.mark.parametrize("token", ["", "x" * 43, "a" * 42, "a" * 44, None, 5, ["a" * 43], "../../etc/passwd"])
def test_unknown_launch_token_is_refused(static_dir: Path, token: object) -> None:
    studio = make_studio(static_dir)
    response = studio.client.post("/api/session", json={"token": token}, headers={"Origin": ORIGIN})
    _assert_refused(response, 401, "access.launch_invalid")
    assert len(studio.runtime.sessions) == 0


def test_session_request_needs_a_json_object(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    for body in (b"[]", b"{", b"x" * 5000):
        response = studio.client.post(
            "/api/session", content=body, headers={"Origin": ORIGIN, "Content-Type": "application/json"}
        )
        _assert_refused(response, 400, "access.bad_request")


def test_session_of_another_instance_does_not_count(static_dir: Path) -> None:
    # The same session ID under the cookie name of another port opens nothing here.
    studio = make_studio(static_dir)
    csrf = studio.sign_in()[CSRF_HEADER]
    session_id = studio.client.cookies.get(f"tabrion_studio_session_{PORT}")
    studio.client.cookies.clear()
    other = {"Cookie": f"tabrion_studio_session_{PORT + 1}={session_id}", CSRF_HEADER: csrf}
    _assert_refused(studio.client.get("/api/heartbeat", headers=other), 401, "access.session_required")


def test_sessions_are_bounded() -> None:
    sessions = Sessions(max_sessions=2)
    first = sessions.create()
    second = sessions.create()
    third = sessions.create()
    assert not sessions.check(*first)
    assert sessions.check(*second) and sessions.check(*third)
    assert not sessions.check(second[0], third[1])
    assert not sessions.check(None, None) and not sessions.check("x" * 200, second[1])


# --------------------------------------------------------------------------------------------------------------------
# STU-T-014 (T-07, SQ-08): strict CSP on every answer, no inline scripts
# --------------------------------------------------------------------------------------------------------------------


def test_csp_is_strict() -> None:
    directives = dict(part.strip().split(" ", 1) for part in CONTENT_SECURITY_POLICY.split(";"))
    assert directives["default-src"] == "'self'"
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert "unsafe" not in CONTENT_SECURITY_POLICY
    assert "data:" not in CONTENT_SECURITY_POLICY and "*" not in CONTENT_SECURITY_POLICY


def test_security_headers_on_every_answer(static_dir: Path, tmp_path: Path) -> None:
    studio = make_studio(static_dir)
    headers = _signed_in_headers(studio)
    answers = [
        studio.client.get("/"),
        studio.client.get("/launch"),
        studio.client.get("/assets/app.js"),
        studio.client.get("/missing.js"),  # 404 of the static files
        studio.client.get("/api/health"),
        studio.client.get("/api/heartbeat", headers=headers),
        studio.client.get("/api/heartbeat"),  # 401
        studio.client.get("/api/health", headers={"Host": "evil.example"}),  # 403
        studio.client.get("/api/control/proof?nonce=x"),  # 400
        studio.client.post("/api/control/launch", json={}),  # 401
        studio.client.request("PUT", "/api/health", headers={"Origin": ORIGIN}, json={}),  # 405
        make_studio(tmp_path / "unbuilt").client.get("/"),  # the "not built" page
    ]
    for response in answers:
        _assert_security_headers(response)
        assert response.headers.get_list("content-security-policy") == [CONTENT_SECURITY_POLICY]


def test_frontend_has_no_inline_script() -> None:
    # script-src 'self' without 'unsafe-inline': the page must load every script from a file.
    for page in (ROOT / "frontend" / "index.html", ROOT / "src" / "tabrion_studio" / "static" / "index.html"):
        if not page.is_file():
            continue  # the built copy exists only after scripts/build_frontend.py
        html = page.read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S | re.I)
        assert scripts, page
        for attributes, body in scripts:
            assert "src=" in attributes and body.strip() == "", page
        assert not re.search(r"\son[a-z]+\s*=", html, flags=re.I), f"inline event handler in {page}"
        assert "style=" not in html, page


def test_eslint_forbids_dangerously_set_inner_html() -> None:
    config = (ROOT / "frontend" / "eslint.config.js").read_text(encoding="utf-8")
    assert "JSXAttribute[name.name='dangerouslySetInnerHTML']" in config


# --------------------------------------------------------------------------------------------------------------------
# STU-T-026 (T-15): nothing is cached
# --------------------------------------------------------------------------------------------------------------------


def test_no_store_on_every_api_answer(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    headers = _signed_in_headers(studio)
    for method, path in ROUTES:
        hdrs = dict(CONTROL_AUTH) if "/control/" in path else headers
        response = _request(studio, method, path, hdrs)
        assert response.headers.get_list("cache-control") == ["no-store"], path  # type: ignore[attr-defined]
    # The static files' own caching headers are replaced, not added to.
    asset = studio.client.get("/assets/app.js")
    assert asset.headers.get_list("cache-control") == ["no-store"]
