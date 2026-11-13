from __future__ import annotations

from pathlib import Path
import re
import time

import pytest

from tabrion_studio import __version__
from tabrion_studio.app import CLIENT_ROUTES
from tabrion_studio.instance import control_proof
from tests.support.access import CONTROL_AUTH, ORIGIN, TOKEN, make_studio

ROOT = Path(__file__).resolve().parents[2]


def test_health(static_dir: Path) -> None:
    response = make_studio(static_dir).client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "instance": "abc123"}
    assert response.headers["cache-control"] == "no-store"


def test_static_frontend(static_dir: Path) -> None:
    client = make_studio(static_dir).client
    index = client.get("/")
    assert index.status_code == 200
    assert "<title>Tabrion Studio</title>" in index.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/missing.js").status_code == 404


def test_launch_serves_the_app(static_dir: Path) -> None:
    # The fragment with the token never reaches the server; /launch is the single-page app.
    response = make_studio(static_dir).client.get("/launch")
    assert response.status_code == 200
    assert "<title>Tabrion Studio</title>" in response.text


def test_client_routes_serve_the_app(static_dir: Path) -> None:
    """A reload on a view of the single-page app gets index.html with the security headers; nothing else does."""
    client = make_studio(static_dir).client
    for path in CLIENT_ROUTES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<title>Tabrion Studio</title>" in response.text
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
    for path in ("/settings/x", "/connections.json", "/nowhere"):
        assert client.get(path).status_code == 404, path
    # Another Host is still refused on these paths (DNS rebinding, STU-T-001).
    assert client.get("/settings", headers={"Host": "rebind.test"}).status_code == 403


def test_client_routes_answer_head(static_dir: Path) -> None:
    """HEAD on a view path answers like GET without a body, as HEAD on / and the static files do (review U-4)."""
    client = make_studio(static_dir).client
    for path in ("/", *CLIENT_ROUTES):
        get = client.get(path)
        head = client.head(path)
        assert head.status_code == 200, path
        assert head.content == b"", path
        assert head.headers["content-type"] == get.headers["content-type"], path
        assert head.headers["content-length"] == get.headers["content-length"], path
        assert "default-src 'self'" in head.headers["content-security-policy"], path
        assert head.headers["cache-control"] == "no-store", path
    for path in ("/settings/x", "/nowhere"):
        assert client.head(path).status_code == 404, path
    # The Host allowlist applies to HEAD as well (STU-T-001).
    assert client.head("/settings", headers={"Host": "rebind.test"}).status_code == 403
    # Only GET and HEAD: a POST is still refused before it reaches the route (no Origin).
    assert client.post("/settings").status_code == 403


def test_client_routes_match_the_frontend_router() -> None:
    router = (ROOT / "frontend" / "src" / "router.ts").read_text(encoding="utf-8")
    routes = re.search(r"ROUTES = \[([^\]]*)\]", router)
    assert routes is not None
    paths = set(re.findall(r"'(/[^']*)'", routes.group(1)))
    assert paths - {"/"} == set(CLIENT_ROUTES)


def test_without_a_built_frontend(tmp_path: Path) -> None:
    client = make_studio(tmp_path / "nothing").client
    for path in ("/", "/launch"):
        response = client.get(path)
        assert response.status_code == 200
        assert "frontend is not built" in response.text


def test_no_docs_pages(static_dir: Path) -> None:
    # Swagger/ReDoc would load scripts from a CDN (SQ-08); the schema itself needs a session.
    studio = make_studio(static_dir)
    assert studio.client.get("/docs").status_code == 404
    assert studio.client.get("/redoc").status_code == 404
    assert studio.client.get("/api/openapi.json").status_code == 401
    assert studio.client.get("/api/openapi.json", headers=studio.sign_in()).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}],
)
def test_control_needs_the_token(static_dir: Path, headers: dict[str, str]) -> None:
    studio = make_studio(static_dir)
    for action in ("launch", "shutdown", "other"):
        response = studio.client.post(f"/api/control/{action}", headers=headers, json={})
        assert response.status_code == 401
        assert response.json()["code"] == "access.control_denied"
        assert response.headers["cache-control"] == "no-store"
    assert studio.shutdowns == []
    assert studio.runtime.launches.pending == 0


def test_control_launch_and_shutdown(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    doc = studio.client.post("/api/control/launch", headers=CONTROL_AUTH, json={}).json()
    assert doc["redirect"] is None
    assert doc["url"].startswith(f"{ORIGIN}/launch#")
    assert studio.runtime.launches.pending == 1
    assert studio.client.post("/api/control/unknown", headers=CONTROL_AUTH, json={}).status_code == 404
    response = studio.client.post("/api/control/shutdown", headers=CONTROL_AUTH, json={})
    assert response.status_code == 202
    assert studio.shutdowns == ["stop requested"]


def test_control_launch_with_a_redirect_file(static_dir: Path, tmp_path: Path) -> None:
    studio = make_studio(static_dir, redirect_dir=tmp_path)
    doc = studio.client.post("/api/control/launch", headers=CONTROL_AUTH, json={"redirect": True}).json()
    redirect = Path(doc["redirect"])
    assert redirect.parent == tmp_path
    assert doc["url"] in redirect.read_text(encoding="utf-8")


def test_control_launch_needs_a_json_object(static_dir: Path) -> None:
    studio = make_studio(static_dir)
    for body, status in ((b"[]", 400), (b"not json", 400), (b"x" * 5000, 413)):
        response = studio.client.post(
            "/api/control/launch", headers={**CONTROL_AUTH, "Content-Type": "application/json"}, content=body
        )
        assert response.status_code == status
    assert studio.runtime.launches.pending == 0


def test_control_is_not_reachable_with_get(static_dir: Path) -> None:
    client = make_studio(static_dir).client
    assert client.get("/api/control/shutdown", headers=CONTROL_AUTH).status_code in (404, 405)


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("GET", "/api/health", {}),
        ("GET", "/", {"Sec-Fetch-Site": "same-origin"}),
        ("GET", "/assets/app.js", {}),
        ("GET", "/api/heartbeat", {}),
        ("GET", "/api/heartbeat", {"Sec-Fetch-Site": "same-origin"}),
        ("GET", "/api/heartbeat", {"Sec-Fetch-Site": "cross-site"}),
        ("POST", "/api/control/launch", {"Authorization": "Bearer wrong"}),
        ("GET", "/api/control/proof?nonce=" + "a" * 32, {}),
        ("POST", "/api/session", {"Origin": ORIGIN}),
    ],
)
def test_requests_that_do_not_count_as_activity(
    static_dir: Path, method: str, path: str, headers: dict[str, str]
) -> None:
    # Review F-06/R-5: requests without a session or a valid control token never postpone the idle exit.
    studio = make_studio(static_dir, idle_seconds=60)
    studio.make_idle()
    studio.client.request(method, path, headers=headers, json={} if method == "POST" else None)
    assert studio.runtime.idle.idle_for() >= 50


def test_heartbeat_with_a_session_counts_as_activity(static_dir: Path) -> None:
    studio = make_studio(static_dir, idle_seconds=60)
    headers = studio.sign_in()
    studio.make_idle()
    response = studio.client.get("/api/heartbeat", headers=headers)
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert studio.runtime.idle.idle_for() < 5


def test_authenticated_control_counts_as_activity(static_dir: Path) -> None:
    studio = make_studio(static_dir, idle_seconds=60)
    studio.make_idle()
    assert studio.client.post("/api/control/launch", headers=CONTROL_AUTH, json={}).status_code == 200
    assert studio.runtime.idle.idle_for() < 5


def test_control_proof(static_dir: Path) -> None:
    # Review F-01: the server proves that it knows the token without the client sending it.
    client = make_studio(static_dir).client
    nonce = "0123456789abcdef" * 2
    response = client.get(f"/api/control/proof?nonce={nonce}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"proof": control_proof(TOKEN, nonce)}
    assert response.json()["proof"] != control_proof(TOKEN, "f" * 32)  # bound to the nonce
    assert TOKEN not in response.text


@pytest.mark.parametrize("nonce", ["", "short", "A" * 32, "g" * 32, "a" * 129, "a" * 31])
def test_control_proof_needs_a_proper_nonce(static_dir: Path, nonce: str) -> None:
    response = make_studio(static_dir).client.get("/api/control/proof", params={"nonce": nonce})
    assert response.status_code == 400
    assert "proof" not in response.json()


def test_idle_watcher_requests_shutdown(static_dir: Path) -> None:
    studio = make_studio(static_dir, idle_seconds=0.3)
    with studio.client:  # runs the lifespan, which starts the watcher
        deadline = time.monotonic() + 5
        while not studio.shutdowns and time.monotonic() < deadline:
            time.sleep(0.05)
    assert studio.shutdowns == ["no activity for 0.005 min"]


def test_launch_sweeper_expires_tokens(static_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio import app

    monkeypatch.setattr(app, "SWEEP_SECONDS", 0.05)
    studio = make_studio(static_dir, redirect_dir=tmp_path)
    launch = studio.runtime.launches.issue(redirect=True)
    assert launch.redirect is not None and launch.redirect.exists()
    studio.clock.now += 1000
    with studio.client:
        deadline = time.monotonic() + 5
        while launch.redirect.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    assert not launch.redirect.exists()
    assert studio.runtime.launches.pending == 0
