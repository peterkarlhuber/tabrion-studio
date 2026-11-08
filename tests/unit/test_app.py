from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from tabrion_studio import __version__
from tabrion_studio.app import Runtime, create_app
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.instance import control_proof

TOKEN = "control-token-for-tests"


class Recorder:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def __call__(self, reason: str) -> None:
        self.reasons.append(reason)


def make(static_dir: Path, idle_seconds: float = 0) -> tuple[TestClient, Runtime, Recorder]:
    recorder = Recorder()
    runtime = Runtime(
        instance_id="abc123",
        control_token=TOKEN,
        idle=IdleMonitor(idle_seconds),
        launch_url=lambda: "http://127.0.0.1:1234/",
        request_shutdown=recorder,
    )
    return TestClient(create_app(runtime, static_dir), base_url="http://127.0.0.1:1234"), runtime, recorder


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "static"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><title>Tabrion Studio</title><div id=root></div>")
    (folder / "assets" / "app.js").write_text("console.log(1)")
    return folder


def test_health(static_dir: Path) -> None:
    client, _, _ = make(static_dir)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "instance": "abc123"}
    assert response.headers["cache-control"] == "no-store"


def test_static_frontend(static_dir: Path) -> None:
    client, _, _ = make(static_dir)
    index = client.get("/")
    assert index.status_code == 200
    assert "<title>Tabrion Studio</title>" in index.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/missing.js").status_code == 404


def test_without_a_built_frontend(tmp_path: Path) -> None:
    client, _, _ = make(tmp_path / "nothing")
    response = client.get("/")
    assert response.status_code == 200
    assert "frontend is not built" in response.text


def test_no_docs_pages(static_dir: Path) -> None:
    # Swagger/ReDoc would load scripts from a CDN (SQ-08).
    client, _, _ = make(static_dir)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/api/openapi.json").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}],
)
def test_control_needs_the_token(static_dir: Path, headers: dict[str, str]) -> None:
    client, _, recorder = make(static_dir)
    for action in ("launch", "shutdown", "other"):
        response = client.post(f"/api/control/{action}", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"code": "access.control_denied"}
        assert response.headers["cache-control"] == "no-store"
    assert recorder.reasons == []


def test_control_launch_and_shutdown(static_dir: Path) -> None:
    client, _, recorder = make(static_dir)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/control/launch", headers=auth).json() == {"url": "http://127.0.0.1:1234/"}
    assert client.post("/api/control/unknown", headers=auth).status_code == 404
    response = client.post("/api/control/shutdown", headers=auth)
    assert response.status_code == 202
    assert recorder.reasons == ["stop requested"]


def test_control_is_not_reachable_with_get(static_dir: Path) -> None:
    client, _, _ = make(static_dir)
    assert client.get("/api/control/shutdown", headers={"Authorization": f"Bearer {TOKEN}"}).status_code in (404, 405)


def _make_idle(runtime: Runtime) -> None:
    runtime.idle._last -= 50
    assert runtime.idle.idle_for() >= 50


SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("GET", "/api/health", {}),
        ("GET", "/api/health", SAME_ORIGIN),
        ("GET", "/", SAME_ORIGIN),
        ("GET", "/assets/app.js", SAME_ORIGIN),
        ("GET", "/api/heartbeat", {}),
        ("GET", "/api/heartbeat", {"Sec-Fetch-Site": "cross-site"}),
        ("GET", "/api/heartbeat", {"Sec-Fetch-Site": "same-site"}),
        ("GET", "/api/heartbeat", {"Sec-Fetch-Site": "none"}),
        ("POST", "/api/control/launch", {"Authorization": "Bearer wrong"}),
        ("GET", "/api/control/proof?nonce=" + "a" * 32, {}),
    ],
)
def test_requests_that_do_not_count_as_activity(
    static_dir: Path, method: str, path: str, headers: dict[str, str]
) -> None:
    # Review F-06: unauthenticated requests and requests of other sites never postpone the idle exit.
    client, runtime, _ = make(static_dir, idle_seconds=60)
    _make_idle(runtime)
    client.request(method, path, headers=headers)
    assert runtime.idle.idle_for() >= 50


def test_same_origin_heartbeat_counts_as_activity(static_dir: Path) -> None:
    client, runtime, _ = make(static_dir, idle_seconds=60)
    _make_idle(runtime)
    response = client.get("/api/heartbeat", headers=SAME_ORIGIN)
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert runtime.idle.idle_for() < 5


def test_authenticated_control_counts_as_activity(static_dir: Path) -> None:
    client, runtime, _ = make(static_dir, idle_seconds=60)
    _make_idle(runtime)
    assert client.post("/api/control/launch", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert runtime.idle.idle_for() < 5


def test_control_proof(static_dir: Path) -> None:
    # Review F-01: the server proves that it knows the token without the client sending it.
    client, _, _ = make(static_dir)
    nonce = "0123456789abcdef" * 2
    response = client.get(f"/api/control/proof?nonce={nonce}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"proof": control_proof(TOKEN, nonce)}
    assert response.json()["proof"] != control_proof(TOKEN, "f" * 32)  # bound to the nonce
    assert TOKEN not in response.text


@pytest.mark.parametrize("nonce", ["", "short", "A" * 32, "g" * 32, "a" * 129, "a" * 31])
def test_control_proof_needs_a_proper_nonce(static_dir: Path, nonce: str) -> None:
    client, _, _ = make(static_dir)
    response = client.get("/api/control/proof", params={"nonce": nonce})
    assert response.status_code == 400
    assert "proof" not in response.json()


def test_idle_watcher_requests_shutdown(static_dir: Path) -> None:
    client, _runtime, recorder = make(static_dir, idle_seconds=0.3)
    with client:  # runs the lifespan, which starts the watcher
        import time

        deadline = time.monotonic() + 5
        while not recorder.reasons and time.monotonic() < deadline:
            time.sleep(0.05)
    assert recorder.reasons == ["no activity for 0.005 min"]
