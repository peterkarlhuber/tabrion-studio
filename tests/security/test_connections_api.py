"""STU-009 through the API: open, list, test and close read-only connections behind the session and CSRF checks.

The answers carry ``tabrion.safe_url`` (never a password) and fixed problem texts; a project's SQLite file is opened
only after trust, through ``ProjectConfig.database_file`` (review T-1). Engines are disposed on shutdown.
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import io
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
import uuid

import psycopg
import pytest
import tabrion
from tabrion.errors import Kind, classify

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.project import ProjectConfig, TrustError
from tabrion_studio.config.secrets import KeyringStore, Secrets, SessionSecrets
from tabrion_studio.config.user import UserConfig
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
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
    opened = env.send("POST", "/api/connections", {"profile_id": "project:proj", "confirmed_environment": "prod"})
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
        ("GET", "/api/connections/c_x/health"),
    ):
        response = env.studio.client.request(method, path, json={"profile_id": "user:parts"}, headers=no_csrf)
        assert response.status_code in (401, 403), (method, path)
    bad = env.send("POST", "/api/connections", {"profile_id": 3})
    assert bad.status_code == 400 and bad.json()["code"] == "access.bad_request"
    assert env.registry.list() == []


def test_td_038_a_profile_changed_to_prod_elsewhere_needs_d02(env: Env) -> None:
    """D-02 on the server (TD-038): the page confirmed the profile as DEV, then it became PROD in another tab or in
    profiles.toml. Open and test are refused with studio.prod_confirmation_required until the page confirms PROD."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    digest = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    prod = {"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "prod"}
    assert env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": prod, "digest": digest}).status_code == 200
    for path in ("/api/connections", "/api/connection-tests"):
        for confirmed in ({"confirmed_environment": "dev"}, {"confirmed_environment": None}, {}):
            refused = env.send("POST", path, {"profile_id": "user:parts", **confirmed})
            assert refused.status_code == 409, (path, confirmed)
            doc = refused.json()
            assert doc["code"] == "studio.prod_confirmation_required" and doc["kind"] == "database"
            assert refused.headers["content-type"] == "application/problem+json"
    assert env.registry.list() == []
    # The page reads the profile again, asks D-02 and sends the request again.
    assert env.send("GET", "/api/profiles").json()["profiles"][0]["environment"] == "prod"
    tested = env.send("POST", "/api/connection-tests", {"profile_id": "user:parts", "confirmed_environment": "prod"})
    assert tested.status_code == 200 and tested.json()["environment"] == "prod"
    opened = env.send("POST", "/api/connections", {"profile_id": "user:parts", "confirmed_environment": "prod"})
    assert opened.status_code == 201 and opened.json()["environment"] == "prod"
    # The open PROD connection is not handed to a request that did not confirm PROD.
    again = env.send("POST", "/api/connections", {"profile_id": "user:parts"})
    assert again.status_code == 409 and again.json()["code"] == "studio.prod_confirmation_required"


def test_c1_a_sqlite_file_comes_from_the_read_that_passed_the_check(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-1 of the #48 review, against the real ProfileSource (``Configuration``): profiles.toml is rewritten between
    the read that passed the D-02 check (DEV) and the open, to a PROD profile with another file. The connection opens
    the DEV file of the checked read, never the PROD file of a second read."""
    prod_db = env.db.with_name("prod.db")
    _database(prod_db)
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    file = env.config_dir / "profiles.toml"
    dev_bytes = file.read_bytes()
    digest = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    prod = {"name": "parts", "engine": "sqlite", "path": str(prod_db), "environment": "prod"}
    assert env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": prod, "digest": digest}).status_code == 200
    prod_bytes = file.read_bytes()
    file.write_bytes(dev_bytes)
    read = env.config.entry
    reads: list[str] = []

    def entry_then_rewrite(profile_id: str) -> Any:
        found = read(profile_id)
        reads.append(found.profile.environment)
        file.write_bytes(prod_bytes)  # another tab or an editor saves the file right after this read
        return found

    monkeypatch.setattr(env.config, "entry", entry_then_rewrite)
    connection = env.registry.open("user:parts", "dev")
    assert reads == ["dev"]
    assert connection.environment == "dev"
    assert connection.database_file == env.db.resolve() != prod_db.resolve()
    monkeypatch.undo()
    # The file is PROD now: the DEV connection is stale, and a new open needs D-02.
    assert env.registry.list() == []
    refused = env.send("POST", "/api/connections", {"profile_id": "user:parts", "confirmed_environment": "dev"})
    assert refused.status_code == 409 and refused.json()["code"] == "studio.prod_confirmation_required"


def test_c1_a_project_file_refuses_when_the_trusted_profile_is_not_the_checked_read(env: Env) -> None:
    """C-1 for a project profile: ``ProjectConfig.database_file`` reads the trusted file again; when that profile is
    not the read the caller checked (here re-trusted as DEV with another file), the open fails closed."""
    (env.project / ".tabrion" / "studio.toml").write_text(PROJECT, encoding="utf-8")
    _database(env.project / "data" / "p.db")
    _database(env.project / "data" / "q.db")
    digest = env.send("GET", "/api/project").json()["digest"]
    assert env.send("POST", "/api/project/trust", {"digest": digest}).status_code == 204
    checked = env.config.entry("project:proj")
    assert env.config.database_file(checked) == (env.project / "data" / "p.db").resolve()
    changed = PROJECT.replace("p.db", "q.db").replace('"prod"', '"dev"')
    (env.project / ".tabrion" / "studio.toml").write_text(changed, encoding="utf-8")
    digest = env.send("GET", "/api/project").json()["digest"]
    assert env.send("POST", "/api/project/trust", {"digest": digest}).status_code == 204
    with pytest.raises(TrustError) as refused:
        env.config.database_file(checked)
    assert refused.value.code == "studio.project_untrusted"
    # A fresh read is the trusted profile and opens its own file.
    assert env.config.database_file(env.config.entry("project:proj")) == (env.project / "data" / "q.db").resolve()


@pytest.mark.parametrize("value", ["PROD", "production", "", 3, True, ["prod"], {"prod": True}])
def test_td_038_a_confirmed_environment_outside_the_three_is_a_bad_request(env: Env, value: object) -> None:
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    for path in ("/api/connections", "/api/connection-tests"):
        response = env.send("POST", path, {"profile_id": "user:parts", "confirmed_environment": value})
        assert (response.status_code, response.json()["code"]) == (400, "access.bad_request")
    assert env.registry.list() == []


def test_shutdown_disposes_every_engine(tmp_path: Path, static_dir: Path) -> None:
    environment = Env(tmp_path, static_dir)
    environment.create({"name": "parts", "engine": "sqlite", "path": str(environment.db), "environment": "dev"})
    with environment.studio.client:
        assert environment.send("POST", "/api/connections", {"profile_id": "user:parts"}).status_code == 201
        assert len(environment.registry.list()) == 1
    assert environment.registry.list() == []


def _lost_health() -> tabrion.Health:
    """What tabrion's health check says when the database ended the session (a restart, pg_terminate_backend)."""
    classification = classify(psycopg.OperationalError("server closed the connection unexpectedly"))
    return tabrion.Health(False, "sqlite", None, None, {"checked_out": 0, "size": 2, "overflow": -2}, classification)


def test_td034_health_says_open_with_the_latency(env: Env) -> None:
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    opened = env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
    response = env.send("GET", f"/api/connections/{opened}/health")
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["state"] == "open" and isinstance(doc["latency_ms"], float) and set(doc) == {"state", "latency_ms"}


def test_td034_health_of_a_gone_or_stale_connection(env: Env) -> None:
    gone = env.send("GET", "/api/connections/c_nothing/health")
    assert gone.status_code == 404 and gone.json()["code"] == "studio.connection_not_found"
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    opened = env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
    other = env.db.with_name("moved.db")
    _database(other)
    # Changed elsewhere (another tab saved it): the connection is closed only when it is used next. Through the API,
    # so the stored path really changes on every OS (C-1 of td-034-review.md: a text edit of profiles.toml missed
    # the TOML-escaped Windows path and changed nothing).
    digest = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    moved = {"name": "parts", "engine": "sqlite", "path": str(other), "environment": "dev"}
    saved = env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": moved, "digest": digest})
    assert saved.status_code == 200, saved.text
    assert env.send("GET", "/api/profiles").json()["profiles"][0]["path"] == str(other)
    stale = env.send("GET", f"/api/connections/{opened}/health")
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "studio.connection_stale"
    assert env.registry.list() == []


def test_td034_a_lost_database_is_a_db_problem_and_open_replaces_the_connection(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    first = env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
    lost = env.registry.get(first)
    monkeypatch.setattr(lost, "health", _lost_health)
    response = env.send("GET", f"/api/connections/{first}/health")
    assert response.status_code == 502 and response.json()["code"] == "db.connection"
    # The connection stays registered until the page reconnects: the banner names it.
    assert [c.id for c in env.registry.list()] == [first]
    again = env.send("POST", "/api/connections", {"profile_id": "user:parts"})
    assert again.status_code == 201, again.text
    second = again.json()["connection_id"]
    assert second != first
    assert [c.id for c in env.registry.list()] == [second]
    assert env.send("GET", f"/api/connections/{first}/health").json()["code"] == "studio.connection_not_found"
    assert env.send("GET", f"/api/connections/{second}/health").json()["state"] == "open"


def test_td034_a_full_pool_is_busy_not_lost(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every session lent out (a long query, an export): the check would only wait for a session (the pool timeout,
    10 s) and learn nothing about the database. It does not run: the answer is "busy" at once, the page offers no
    "Reconnect", and "open" returns the connection without waiting (L-3 of td-034-review.md)."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    first = env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
    busy = env.registry.get(first)

    def no_check() -> tabrion.Health:
        raise AssertionError("the check must not wait for a session")

    monkeypatch.setattr(busy, "health", no_check)
    engine = busy._db.engine
    lent = [engine.raw_connection() for _ in range(2)]  # the pool's two sessions, as two running statements
    try:
        started = time.monotonic()
        assert env.send("GET", f"/api/connections/{first}/health").json() == {"state": "busy", "latency_ms": None}
        assert env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"] == first
        assert time.monotonic() - started < 2.0
    finally:
        for session in lent:
            session.close()


def test_td034_a_failed_check_with_a_free_session_is_lost_even_if_it_counts_itself(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-2 of td-034-review.md: a check that got a session and then failed reports a pool whose count includes its
    own session. With a free session before the check, that is a lost database, not a busy pool."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    first = env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
    counted = dataclasses.replace(_lost_health(), pool={"checked_out": 2, "size": 2, "overflow": 0})
    monkeypatch.setattr(env.registry.get(first), "health", lambda: counted)
    response = env.send("GET", f"/api/connections/{first}/health")
    assert response.status_code == 502 and response.json()["code"] == "db.connection"


def test_td034_a_check_that_fails_otherwise_keeps_the_connection(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-2 of td-034-review.md: only a database that does not answer is lost. A check that fails otherwise (its
    statement timeout on a loaded server) is reported, and "open" returns the connection instead of closing it under
    the queries and exports of other views."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    first = env.send("POST", "/api/connections", {"profile_id": "user:parts"}).json()["connection_id"]
    lost = _lost_health()
    assert lost.classification is not None
    slow = dataclasses.replace(lost, classification=dataclasses.replace(lost.classification, kind=Kind.TIMEOUT))
    monkeypatch.setattr(env.registry.get(first), "health", lambda: slow)
    response = env.send("GET", f"/api/connections/{first}/health")
    assert response.status_code == 504 and response.json()["code"] == "db.timeout", response.text
    again = env.send("POST", "/api/connections", {"profile_id": "user:parts"})
    assert again.status_code == 201 and again.json()["connection_id"] == first
    assert [c.id for c in env.registry.list()] == [first]


def test_td034_two_opens_at_once_leave_one_connection(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """L-6 of td-034-review.md: "Reconnect" and another view's open at once both open a connection; the one that
    registers second is closed and the first returned, so none stays open unlisted and counts against the limit."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    registry = env.registry
    connect = registry._connect
    raced: list[str] = []
    entered = threading.Event()

    def racing(profile_id: str, confirmed: str | None) -> OpenConnection:
        mine = connect(profile_id, confirmed)
        if not entered.is_set():
            entered.set()
            raced.append(registry.open(profile_id, confirmed).id)  # the other request, registered first
        return mine

    monkeypatch.setattr(registry, "_connect", racing)
    winner = registry.open("user:parts", None)
    assert raced == [winner.id]
    assert [c.id for c in registry.list()] == [winner.id]


def test_td034_a_raced_open_is_returned_only_with_d02(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-1 of td-034-review.md (delta d73f6c2): while a request confirmed as DEV connects, the profile becomes PROD
    and a page that confirmed PROD registers its connection first. The DEV request must not get that PROD connection:
    409, as for any open of a PROD profile without D-02, and its own connection is closed."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    registry = env.registry
    connect = registry._connect
    mine: list[OpenConnection] = []

    def racing(profile_id: str, confirmed: str | None) -> OpenConnection:
        connection = connect(profile_id, confirmed)
        if not mine:
            mine.append(connection)
            digest = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
            prod = {"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "prod"}
            saved = env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": prod, "digest": digest})
            assert saved.status_code == 200, saved.text
            assert registry.open(profile_id, "prod").environment == "prod"  # the confirmed page, registered first
        return connection

    monkeypatch.setattr(registry, "_connect", racing)
    refused = env.send("POST", "/api/connections", {"profile_id": "user:parts", "confirmed_environment": "dev"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "studio.prod_confirmation_required"
    assert [c.environment for c in registry.list()] == ["prod"]
    assert mine[0].id not in [c.id for c in registry.list()]


class _WatchedLock:
    """A profile lock of the registry (TD-027) that says when a thread starts to wait for it."""

    def __init__(self, waits: list[threading.Event]) -> None:
        self._lock = threading.RLock()
        self._waits = waits

    def __enter__(self) -> None:
        for wait in self._waits:  # the first open's acquire sets the first event, the second open's the second
            if not wait.is_set():
                wait.set()
                break
        self._lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


def test_td027_two_concurrent_opens_of_one_profile_connect_once(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-2 of stu-32-page-delta-review.md: two opens of one profile at once (the migrations page after a profile
    switch) both looked for an open connection, found none and both connected. The second one now waits for the
    first and gets its connection: one connect, one connection. Synchronized by events, no sleeps: the first open is
    held inside its connect until the second has started to wait for the profile's lock."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    registry = env.registry
    connect = registry._connect
    waits = [threading.Event(), threading.Event()]
    connecting, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow(profile_id: str, confirmed: str | None) -> OpenConnection:
        calls.append(profile_id)
        connecting.set()
        assert release.wait(timeout=30), "never released"
        return connect(profile_id, confirmed)

    monkeypatch.setattr(connections_module, "_profile_lock", lambda: _WatchedLock(waits))
    monkeypatch.setattr(registry, "_connect", slow)
    opened: dict[str, str] = {}

    def opener(name: str) -> None:
        opened[name] = registry.open("user:parts", None).id

    first = threading.Thread(target=opener, args=("first",))
    second = threading.Thread(target=opener, args=("second",))
    try:
        first.start()
        assert connecting.wait(timeout=30), "the first open never connected"
        second.start()
        assert waits[1].wait(timeout=30), "the second open never waited for the profile's lock"
    finally:
        release.set()
        first.join(timeout=30)
        second.join(timeout=30)
    assert not first.is_alive() and not second.is_alive()
    assert calls == ["user:parts"]
    assert opened["first"] == opened["second"]
    assert [c.id for c in registry.list()] == [opened["first"]]
    assert registry._gates == {}


def test_td027_an_open_of_another_profile_does_not_wait(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lock is per profile: while one profile's open hangs in its connect, another profile opens at once."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    env.create({"name": "other", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    registry = env.registry
    connect = registry._connect
    connecting, release = threading.Event(), threading.Event()

    def slow(profile_id: str, confirmed: str | None) -> OpenConnection:
        if profile_id == "user:parts":
            connecting.set()
            assert release.wait(timeout=30), "never released"
        return connect(profile_id, confirmed)

    monkeypatch.setattr(registry, "_connect", slow)
    hanging = threading.Thread(target=registry.open, args=("user:parts", None))
    try:
        hanging.start()
        assert connecting.wait(timeout=30)
        other = registry.open("user:other", None)  # would block until release if the lock were global
        assert [c.id for c in registry.list()] == [other.id]
    finally:
        release.set()
        hanging.join(timeout=30)
    assert not hanging.is_alive()
    assert sorted(c.profile_id for c in registry.list()) == ["user:other", "user:parts"]
    assert registry._gates == {}


def test_td027_a_failed_connect_lets_the_waiting_open_try_its_own(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A first open whose connect fails releases the lock; the open that waited connects itself."""
    env.create({"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev"})
    registry = env.registry
    connect = registry._connect
    attempts: list[int] = []

    def flaky(profile_id: str, confirmed: str | None) -> OpenConnection:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("the first connect fails")
        return connect(profile_id, confirmed)

    monkeypatch.setattr(registry, "_connect", flaky)
    with pytest.raises(RuntimeError):
        registry.open("user:parts", None)
    assert registry._gates == {}
    assert registry.open("user:parts", None).profile_id == "user:parts"
    assert len(attempts) == 2


def test_td034_a_lost_prod_connection_is_replaced_only_with_d02(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.create({"name": "live", "engine": "sqlite", "path": str(env.db), "environment": "prod"})
    body = {"profile_id": "user:live", "confirmed_environment": "prod"}
    first = env.send("POST", "/api/connections", body).json()["connection_id"]
    monkeypatch.setattr(env.registry.get(first), "health", _lost_health)
    refused = env.send("POST", "/api/connections", {"profile_id": "user:live"})
    assert refused.status_code == 409 and refused.json()["code"] == "studio.prod_confirmation_required"
    assert [c.id for c in env.registry.list()] == [first]  # nothing closed or opened without D-02
    assert env.send("POST", "/api/connections", body).json()["connection_id"] != first
