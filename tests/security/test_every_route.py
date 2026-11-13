"""STU-016 (G-S1 preparation): the access rules hold on every API route of the full application, not only on the
routes STU-003 listed (``test_access.py``).

The application is built like ``server.serve`` builds it, with every service, and its routes are read from the app
itself, so a route added later is checked without editing this file. For every ``/api/`` route except the ones that
lead to a session (``/api/health``, ``/api/session``) and the CLI's control endpoints:

- STU-T-005 (T-03): no session cookie is ``401 access.session_required``;
- STU-T-004 (T-02, SQ-03): a session without the CSRF header is ``403 access.csrf_invalid``;
- STU-T-003 (T-02): every method from a foreign ``Origin`` and every cross-site or same-site fetch is refused; an
  unsafe method without ``Origin`` is refused, and its body must be JSON (``415 access.json_only``);
- STU-T-014 and STU-T-026 (T-07, T-15): every refusal carries the strict CSP, ``Cache-Control: no-store`` and the other
  security headers, compared with values written out in ``tests/support/access.py``, not with the code's constant.

The control endpoints are checked too (STU-016 review F-8): every route under ``/api/control/`` except the proof
needs the control token in the access middleware, so a route added under that prefix later is never reachable without
it, and an ``/api/*`` path with a ``.`` or ``..`` segment never reaches a route.

Only FastAPI routes are checked here; the one other route under ``/api/`` is ``/api/openapi.json``, which
``test_access.py`` covers, and a test fails if another appears.

Every refusal happens before the route runs: no profile file, database or state file is created or changed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.user import UserConfig
from tabrion_studio.security.sessions import CSRF_HEADER, cookie_name
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import Diagnostics
from tabrion_studio.services.drafts import Drafts
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.exports import Exports
from tabrion_studio.services.favorites import Favorites
from tabrion_studio.services.history import History
from tabrion_studio.services.migrations import Migrations
from tabrion_studio.services.queries import QueryEngine
from tabrion_studio.services.scripts import Scripts
from tabrion_studio.services.state import StateStore
from tabrion_studio.services.viewer import Viewer
from tests.support.access import CONTROL_AUTH, EXPECTED_SECURITY_HEADERS, HOST, ORIGIN, PORT, TOKEN, Studio, make_studio

pytestmark = pytest.mark.security

#: Routes that are reached before a session exists, each with its own checks in test_access.py and test_launch.py.
BEFORE_SESSION = {"/api/health", "/api/session"}
#: Routes under ``/api/`` that are not FastAPI routes (each checked by hand in test_access.py).
OTHER_API_ROUTES = {"/api/openapi.json"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
#: Route families the full application must have; a missing one means the app was not built with every service.
FAMILIES = (
    "/api/profiles",
    "/api/secrets",
    "/api/settings",
    "/api/project",
    "/api/connections",
    "/api/connection-tests",
    "/api/queries",
    "/api/exports",
    "/api/project/code-trust",
    "/api/connections/x1/catalog",
    "/api/connections/x1/rows",
    "/api/connections/x1/diagnostics",
    "/api/connections/x1/migrations",
    "/api/history",
    "/api/favorites",
    "/api/scripts",
    "/api/drafts",
)


def _full_studio(static_dir: Path, tmp_path: Path) -> Studio:
    project = tmp_path / "project"
    state = tmp_path / "state"
    config_dir = tmp_path / "config"
    project.mkdir()
    state.mkdir(mode=0o700)
    configuration = Configuration(project=ProjectConfig(project, state, "k1", config_dir), user=UserConfig(config_dir))
    registry = ConnectionRegistry(configuration)
    explorer = Explorer(registry)
    store = StateStore(state)
    history = History(store, "k1", configuration.limits)
    return make_studio(
        static_dir,
        config=configuration,
        connections=registry,
        queries=QueryEngine(registry, configuration.limits, history=history),
        explorer=explorer,
        viewer=Viewer(explorer, configuration.limits),
        diagnostics=Diagnostics(registry),
        migrations=Migrations(registry, CodeTrust(project, state, "k1")),
        exports=Exports(registry, explorer),
        history=history,
        favorites=Favorites(store, "k1", lambda: {entry.id for entry in configuration.entries()}),
        scripts=Scripts(project),
        drafts=Drafts(store, "k1", configuration.limits),
    )


def _concrete(path: str) -> str:
    """The route's path with every parameter filled in (``{id}`` and ``{rest:path}`` alike)."""
    return re.sub(r"\{[^}]+\}", "x1", path)


def _api_routes(studio: Studio) -> list[tuple[str, str]]:
    routes = []
    for route in studio.client.app.routes:  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        if route.path in BEFORE_SESSION or route.path.startswith("/api/control/"):
            continue
        for method in sorted((route.methods or set()) - {"HEAD"}):
            routes.append((method, _concrete(route.path)))
    return routes


@pytest.fixture
def studio(static_dir: Path, tmp_path: Path) -> Iterator[Studio]:
    studio = _full_studio(static_dir, tmp_path)
    try:
        yield studio
    finally:
        studio.runtime.connections.close_all()  # type: ignore[union-attr]


def _snapshot(root: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in root.rglob("*")}


def _assert_refused(response: object, status: int, code: str, where: tuple[str, str]) -> None:
    assert response.status_code == status, (where, response.text)  # type: ignore[attr-defined]
    assert response.json()["code"] == code, where  # type: ignore[attr-defined]
    headers = response.headers  # type: ignore[attr-defined]
    for name, value in EXPECTED_SECURITY_HEADERS.items():
        assert headers.get_list(name) == [value], (where, name)


def test_the_full_app_has_every_route_family(studio: Studio) -> None:
    paths = {path for _, path in _api_routes(studio)}
    for family in FAMILIES:
        assert any(path == family or path.startswith(family + "/") for path in paths), family
    assert len(_api_routes(studio)) >= 40


def test_every_other_api_route_is_known(studio: Studio) -> None:
    """A route or mount under ``/api/`` that is not a FastAPI route would be missed by the tests below (L-2 of
    stu-016-prep-review.md)."""
    others = {
        route.path
        for route in studio.client.app.routes  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute)
        and (getattr(route, "path", "") == "/api" or getattr(route, "path", "").startswith("/api/"))
    }
    assert others == OTHER_API_ROUTES


def test_every_api_route_needs_a_session(studio: Studio, tmp_path: Path) -> None:
    before = _snapshot(tmp_path)
    for method, path in _api_routes(studio):
        response = studio.client.request(method, path, headers={CSRF_HEADER: "x", "Origin": ORIGIN}, json={})
        _assert_refused(response, 401, "access.session_required", (method, path))
    assert _snapshot(tmp_path) == before


def test_every_api_route_needs_the_csrf_header(studio: Studio, tmp_path: Path) -> None:
    studio.sign_in()
    before = _snapshot(tmp_path)
    for method, path in _api_routes(studio):
        for headers in ({"Origin": ORIGIN}, {"Origin": ORIGIN, CSRF_HEADER: "wrong"}):
            response = studio.client.request(method, path, headers=headers, json={})
            _assert_refused(response, 403, "access.csrf_invalid", (method, path))
    assert _snapshot(tmp_path) == before


def test_every_api_route_refuses_other_sites(studio: Studio, tmp_path: Path) -> None:
    """Safe methods too: a foreign ``Origin`` and fetch metadata other than same-origin are refused before the session
    is looked at (L-1 of stu-016-prep-review.md)."""
    csrf = studio.sign_in()
    before = _snapshot(tmp_path)
    for method, path in _api_routes(studio):
        where = (method, path)
        foreign = studio.client.request(method, path, headers={**csrf, "Origin": "http://evil.example"}, json={})
        _assert_refused(foreign, 403, "access.origin_not_allowed", where)
        for site in ("cross-site", "same-site"):
            fetched = studio.client.request(
                method, path, headers={**csrf, "Origin": ORIGIN, "Sec-Fetch-Site": site}, json={}
            )
            _assert_refused(fetched, 403, "access.cross_site", where)
    assert _snapshot(tmp_path) == before


def test_every_unsafe_api_route_refuses_other_sites_and_non_json(studio: Studio, tmp_path: Path) -> None:
    csrf = studio.sign_in()
    before = _snapshot(tmp_path)
    unsafe = [(method, path) for method, path in _api_routes(studio) if method in UNSAFE]
    assert unsafe
    for method, path in unsafe:
        where = (method, path)
        foreign = studio.client.request(method, path, headers={**csrf, "Origin": "http://evil.example"}, json={})
        _assert_refused(foreign, 403, "access.origin_not_allowed", where)
        no_origin = studio.client.request(method, path, headers=csrf, json={})
        _assert_refused(no_origin, 403, "access.origin_required", where)
        cross = studio.client.request(
            method, path, headers={**csrf, "Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"}, json={}
        )
        _assert_refused(cross, 403, "access.cross_site", where)
        form = studio.client.request(
            method,
            path,
            headers={**csrf, "Origin": ORIGIN, "Content-Type": "text/plain"},
            content=b"{}",
        )
        _assert_refused(form, 415, "access.json_only", where)
    assert _snapshot(tmp_path) == before


# --------------------------------------------------------------------------------------------------------------------
# STU-016 review F-8: the control prefix is no way around the session
# --------------------------------------------------------------------------------------------------------------------

#: The one control route without the control token: a second start checks the server's proof before sending it.
CONTROL_WITHOUT_TOKEN = {"/api/control/proof"}
WRONG_TOKENS: list[dict[str, str]] = [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": TOKEN},
    {"Authorization": f"Basic {TOKEN}"},
    {"Authorization": f"Bearer {TOKEN[:-1]}"},
]


def _control_routes(studio: Studio) -> list[tuple[str, str]]:
    routes = []
    for route in studio.client.app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.path.startswith("/api/control/"):
            for method in sorted((route.methods or set()) - {"HEAD"}):
                routes.append((method, route.path))
    return routes


def _add_first(app: FastAPI, path: str) -> None:
    """A route without any check of its own, ahead of the static files: what a careless later route would be."""

    async def endpoint() -> JSONResponse:
        return JSONResponse({"reached": True})

    app.add_api_route(path, endpoint, methods=["GET", "POST"])
    app.router.routes.insert(0, app.router.routes.pop())


def _asgi(app: Any, method: str, path: str, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """One request with ``path`` exactly as given (an HTTP client would remove dot segments first)."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    all_headers = {"Host": HOST, "Content-Type": "application/json", "Content-Length": "2", **headers}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in all_headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
    }
    asyncio.run(app(scope, receive, send))
    body = b"".join(message.get("body", b"") for message in sent[1:])
    return int(sent[0]["status"]), json.loads(body)


def test_every_control_route_needs_the_control_token(studio: Studio, tmp_path: Path) -> None:
    """Even with a valid session (the cookie is sent, the CSRF header too): a session is no control token."""
    csrf = studio.sign_in()
    routes = [(method, path) for method, path in _control_routes(studio) if path not in CONTROL_WITHOUT_TOKEN]
    assert ("POST", "/api/control/{action}") in routes
    before = _snapshot(tmp_path)
    for method, template in routes:
        for path in {_concrete(template), template.replace("{action}", "launch"), "/api/control/proof/x"}:
            for headers in WRONG_TOKENS:
                response = studio.client.request(method, path, headers={**csrf, **headers}, json={})
                _assert_refused(response, 401, "access.control_denied", (method, path))
    assert _snapshot(tmp_path) == before
    assert studio.shutdowns == [] and studio.runtime.launches.pending == 0


def test_a_later_route_under_the_control_prefix_needs_the_token(studio: Studio) -> None:
    """The prefix used to skip the session check and rely on each route's own bearer check; a route that forgot its
    check would have been open to every local process. The access middleware now checks the token for it."""
    app = studio.client.app
    assert isinstance(app, FastAPI)
    _add_first(app, "/api/control/later")
    csrf = studio.sign_in()
    for method in ("GET", "POST"):
        for headers in WRONG_TOKENS:
            for extra in ({}, csrf):
                response = studio.client.request(method, "/api/control/later", headers={**extra, **headers}, json={})
                _assert_refused(response, 401, "access.control_denied", (method, str(headers)))
        reached = studio.client.request(method, "/api/control/later", headers=CONTROL_AUTH, json={})
        assert reached.json() == {"reached": True}


@pytest.mark.parametrize(
    "path",
    [
        "/api/control/../profiles",
        "/api/control/../control/launch",
        "/api/control/./launch",
        "/api/control/x/../launch",
        "/api/./heartbeat",
        "/api/x/../heartbeat",
        "/api/..",
        "/api/files/../../etc/passwd",
    ],
)
def test_dot_segments_in_api_paths_never_reach_a_route(studio: Studio, path: str) -> None:
    """A route with a ``path`` parameter would match these; a later mount or path normalization could map them to
    another route. They are 404 with a valid session and a valid control token alike (the server has decoded
    ``%2e%2e`` to ``..`` before the middleware sees the path)."""
    csrf = studio.sign_in()[CSRF_HEADER]
    app = studio.client.app
    assert isinstance(app, FastAPI)
    _add_first(app, "/api/control/{rest:path}")
    _add_first(app, "/api/files/{rest:path}")
    _add_first(app, "/api/{rest:path}")
    session = studio.client.cookies[cookie_name(PORT)]
    signed_in = {"Cookie": f"{cookie_name(PORT)}={session}", CSRF_HEADER: csrf, "Origin": ORIGIN}
    for method in ("GET", "POST"):
        for headers in ({}, signed_in, CONTROL_AUTH):
            status, doc = _asgi(app, method, path, headers)
            assert (status, doc["code"]) == (404, "studio.not_found"), (method, path, headers.keys())
    assert studio.shutdowns == [] and studio.runtime.launches.pending == 0
