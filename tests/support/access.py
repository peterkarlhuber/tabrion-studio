"""An app with a fake runtime for in-process tests of the routes and the access middleware (STU-003)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

from tabrion_studio.api.config import Configuration
from tabrion_studio.app import Runtime, create_app
from tabrion_studio.idle import IdleMonitor
from tabrion_studio.security.access import loopback_origin
from tabrion_studio.security.launch import LaunchTokens
from tabrion_studio.security.sessions import CSRF_HEADER
from tabrion_studio.services.connections import ConnectionRegistry

TOKEN = "control-token-for-tests"
PORT = 1234
ORIGIN = loopback_origin(PORT)
HOST = f"127.0.0.1:{PORT}"
CONTROL_AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Studio:
    client: TestClient
    runtime: Runtime
    clock: Clock
    shutdowns: list[str] = field(default_factory=list)

    def launch_token(self) -> str:
        return self.runtime.launches.issue(redirect=False).url.split("#", 1)[1]

    def sign_in(self) -> dict[str, str]:
        """Redeem a launch token like the launch page; returns the headers of a signed-in page (CSRF, Origin)."""
        response = self.client.post("/api/session", json={"token": self.launch_token()}, headers={"Origin": ORIGIN})
        assert response.status_code == 200, response.text
        return {CSRF_HEADER: response.json()["csrf"]}

    def make_idle(self) -> None:
        self.runtime.idle._last -= 50
        assert self.runtime.idle.idle_for() >= 50


def make_studio(
    static_dir: Path,
    redirect_dir: Path | None = None,
    idle_seconds: float = 0,
    config: Configuration | None = None,
    connections: ConnectionRegistry | None = None,
) -> Studio:
    clock = Clock()
    shutdowns: list[str] = []
    runtime = Runtime(
        instance_id="abc123",
        control_token=TOKEN,
        idle=IdleMonitor(idle_seconds),
        port=PORT,
        launches=LaunchTokens(ORIGIN, redirect_dir=redirect_dir, prefix="studio-test", clock=clock),
        request_shutdown=shutdowns.append,
        config=config,
        connections=connections,
    )
    client = TestClient(create_app(runtime, static_dir), base_url=ORIGIN)
    return Studio(client, runtime, clock, shutdowns)
