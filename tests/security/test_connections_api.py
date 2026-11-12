"""STU-009 through the API: open, list, test and close read-only connections behind the session and CSRF checks.

The answers carry ``tabrion.safe_url`` (never a password) and fixed problem texts; a project's SQLite file is opened
only after trust, through ``ProjectConfig.database_file`` (review T-1). Engines are disposed on shutdown.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import logging
from pathlib import Path
import sqlite3
from typing import Any
import uuid

import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.secrets import KeyringStore, Secrets, SessionSecrets
from tabrion_studio.config.user import UserConfig
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.services.connections import ConnectionRegistry
from tests.support.access import ORIGIN, make_studio
from tests.support.fake_keyring import FakeKeyring

pytestmark = pytest.mark.security

CANARY = f"tabrion-canary-{uuid.uuid4()}"
PROJECT = """version = 1
[[profile]]
name = "proj"
engine = "sqlite"
path = "data/p.db"
environment = "prod"
"""


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE t (x)")
    connection.commit()
    connection.close()
    path.chmod(0o600)


class Env:
    def __init__(self, tmp_path: Path, static_dir: Path) -> None:
        self.project = tmp_path / "project"
        (self.project / ".tabrion").mkdir(parents=True)
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        self.config_dir = tmp_path / "config"
        self.fake = FakeKeyring()
        secrets = Secrets(KeyringStore(lambda: self.fake), SessionSecrets(), {})
        project = ProjectConfig(self.project, state, "k1", self.config_dir)
        self.config = Configuration(project, UserConfig(self.config_dir), secrets)
        self.registry = ConnectionRegistry(self.config)
        self.studio = make_studio(static_dir, config=self.config, connections=self.registry)
        self.headers = {**self.studio.sign_in(), "Origin": ORIGIN}
        self.db = tmp_path / "parts.db"
        _database(self.db)

    def send(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.studio.client.request(method, path, json=body, headers=self.headers if headers is None else headers)

    def create(self, profile: dict[str, Any]) -> None:
        digest = self.send("GET", "/api/profiles").json()["user_file"]["digest"]
        assert self.send("POST", "/api/profiles", {"profile": profile, "digest": digest}).status_code == 201


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[LogSetup]:
    setup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        yield setup
    finally:
        setup.close()
        forget_secrets()
        assert setup.file is not None
        assert CANARY not in setup.file.read_text(encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, logs: LogSetup) -> Iterator[Env]:
    environment = Env(tmp_path, static_dir)
    try:
        yield environment
    finally:
        environment.registry.close_all()


def test_open_list_test_and_close_a_connection(env: Env) -> None:
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    response = env.send("POST", "/api/connections", {"profile_id": "user:parts"})
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["read_only"] is True and doc["engine"] == "sqlite" and doc["environment"] == "dev"
    assert doc["profile_id"] == "user:parts" and doc["url"].startswith("sqlite+pysqlite:///")
    assert (
        env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
        == doc["connection_id"]
    )
    listed = env.send("GET", "/api/connections").json()["connections"]
    assert [c["connection_id"] for c in listed] == [doc["connection_id"]]
    tested = env.send("POST", "/api/connection-tests", {"profile_id": "user:parts"})
    assert tested.status_code == 200 and tested.json()["ok"] is True and tested.json()["read_only"] is True
    assert env.send("DELETE", "/api/connections", {"connection_id": doc["connection_id"]}).status_code == 204
    gone = env.send("DELETE", "/api/connections", {"connection_id": doc["connection_id"]})
    assert gone.status_code == 404 and gone.json()["code"] == "studio.connection_not_found"
    assert env.send("GET", "/api/connections").json()["connections"] == []


def test_a_changed_profile_closes_its_connection(env: Env) -> None:
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    assert env.send("POST", "/api/connections", {"profile_id": "user:parts"}).status_code == 201
    other = env.db.with_name("other.db")
    _database(other)
    digest = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    changed = {"name": "parts", "engine": "sqlite", "path": str(other), "environment": "dev"}
    assert (
        env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": changed, "digest": digest}).status_code == 200
    )
    assert env.send("GET", "/api/connections").json()["connections"] == []


def test_a_project_file_opens_only_after_trust(env: Env) -> None:
    (env.project / ".tabrion" / "studio.toml").write_text(PROJECT, encoding="utf-8")
    _database(env.project / "data" / "p.db")
    refused = env.send("POST", "/api/connections", {"profile_id": "project:proj"})
    assert refused.status_code == 404 and refused.json()["code"] == "studio.profile_not_found"
    digest = env.send("GET", "/api/project").json()["digest"]
    assert env.send("POST", "/api/project/trust", {"digest": digest}).status_code == 204
    opened = env.send("POST", "/api/connections", {"profile_id": "project:proj"})
    assert opened.status_code == 201 and opened.json()["environment"] == "prod"
    # The project file changes: the trust is gone, so is the connection.
    (env.project / ".tabrion" / "studio.toml").write_text(PROJECT.replace("p.db", "q.db"), encoding="utf-8")
    assert env.send("GET", "/api/connections").json()["connections"] == []


@pytest.mark.parametrize(
    ("profile", "code", "status"),
    [
        (
            {"name": "m", "engine": "sqlite", "path": "/nowhere/missing.db", "environment": "dev"},
            "studio.database_missing",
            404,
        ),
        (
            {
                "name": "pg",
                "engine": "postgresql",
                "host": "localhost",
                "database": "d",
                "password": "keyring:",
                "environment": "dev",
            },
            "studio.secret_missing",
            409,
        ),
        (
            {
                "name": "pg",
                "engine": "postgresql",
                "host": "localhost",
                "database": "d",
                "password": "env:STUDIO_NOPE",
                "environment": "dev",
            },
            "studio.secret_env_missing",
            409,
        ),
    ],
)
def test_refusals_are_fixed_problems(env: Env, profile: dict[str, Any], code: str, status: int) -> None:
    env.create(profile)
    response = env.send("POST", "/api/connections", {"profile_id": f"user:{profile['name']}"})
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert not Path("/nowhere/missing.db").exists()


def test_the_routes_need_a_session_csrf_and_json(env: Env) -> None:
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    no_csrf = {"Origin": ORIGIN, "Cookie": env.headers.get("Cookie", "")}
    for method, path in (
        ("POST", "/api/connections"),
        ("DELETE", "/api/connections"),
        ("POST", "/api/connection-tests"),
    ):
        response = env.studio.client.request(method, path, json={"profile_id": "user:parts"}, headers=no_csrf)
        assert response.status_code in (401, 403), (method, path)
    bad = env.send("POST", "/api/connections", {"profile_id": 3})
    assert bad.status_code == 400 and bad.json()["code"] == "access.bad_request"
    assert env.registry.list() == []


def test_shutdown_disposes_every_engine(tmp_path: Path, static_dir: Path) -> None:
    environment = Env(tmp_path, static_dir)
    environment.create({"name": "parts", "engine": "sqlite", "path": str(environment.db), "environment": "dev"})
    with environment.studio.client:
        assert environment.send("POST", "/api/connections", {"profile_id": "user:parts"}).status_code == 201
        assert len(environment.registry.list()) == 1
    assert environment.registry.list() == []
