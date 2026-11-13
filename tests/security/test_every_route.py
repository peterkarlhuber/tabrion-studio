"""STU-016 (G-S1 preparation): the access rules hold on every API route of the full application, not only on the
routes STU-003 listed (``test_access.py``).

The application is built like ``server.serve`` builds it, with every service, and its routes are read from the app
itself, so a route added later is checked without editing this file. For every ``/api/`` route except the ones that
lead to a session (``/api/health``, ``/api/session``) and the CLI's control endpoints:

- STU-T-005 (T-03): no session cookie is ``401 access.session_required``;
- STU-T-004 (T-02, SQ-03): a session without the CSRF header is ``403 access.csrf_invalid``;
- STU-T-003 (T-02): an unsafe method from a foreign ``Origin`` or a cross-site fetch is refused, and a body that is
  not JSON is ``415 access.json_only``;
- STU-T-014 and STU-T-026 (T-07, T-15): every refusal carries the strict CSP and ``Cache-Control: no-store``.

Every refusal happens before the route runs: no profile file, database or state file is created or changed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re

from fastapi.routing import APIRoute
import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.user import UserConfig
from tabrion_studio.security.access import CONTENT_SECURITY_POLICY, SECURITY_HEADERS
from tabrion_studio.security.sessions import CSRF_HEADER
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import Diagnostics
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.exports import Exports
from tabrion_studio.services.migrations import Migrations
from tabrion_studio.services.queries import QueryEngine
from tabrion_studio.services.viewer import Viewer
from tests.support.access import ORIGIN, Studio, make_studio

pytestmark = pytest.mark.security

#: Routes that are reached before a session exists, each with its own checks in test_access.py and test_launch.py.
BEFORE_SESSION = {"/api/health", "/api/session"}
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
    return make_studio(
        static_dir,
        config=configuration,
        connections=registry,
        queries=QueryEngine(registry, configuration.limits),
        explorer=explorer,
        viewer=Viewer(explorer, configuration.limits),
        diagnostics=Diagnostics(registry),
        migrations=Migrations(registry, CodeTrust(project, state, "k1")),
        exports=Exports(registry, explorer),
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
    for name, value in SECURITY_HEADERS:
        assert headers.get(name) == value, (where, name)
    assert headers.get_list("content-security-policy") == [CONTENT_SECURITY_POLICY], where
    assert headers.get_list("cache-control") == ["no-store"], where


def test_the_full_app_has_every_route_family(studio: Studio) -> None:
    paths = {path for _, path in _api_routes(studio)}
    for family in FAMILIES:
        assert any(path == family or path.startswith(family + "/") for path in paths), family
    assert len(_api_routes(studio)) >= 40


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
