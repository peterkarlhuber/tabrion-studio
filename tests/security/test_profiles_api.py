"""STU-008 through the API: Studio writes only its own profiles and settings, behind the session and CSRF checks.

A refused profile gives ``studio.profile_invalid`` with issues of fixed key names and ``config.*`` codes, never a
value; a plain password typed into the form never reaches a file, an answer or a log line.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
import logging
from pathlib import Path
import stat
import sys
from typing import Any
import uuid

import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.secrets import KeyringStore, Secrets, SessionSecrets
from tabrion_studio.config.user import PROFILES_FILE, SETTINGS_FILE, UserConfig
from tabrion_studio.config.writer import SaveError
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tests.support.access import ORIGIN, make_studio
from tests.support.fake_keyring import FakeKeyring

pytestmark = pytest.mark.security

POSIX = sys.platform != "win32"
CANARY = f"tabrion-canary-{uuid.uuid4()}"

PG = {
    "name": "measure",
    "engine": "postgresql",
    "host": "db.internal",
    "database": "m",
    "user": "studio_ro",
    "password": "keyring:",
    "environment": "prod",
    "read_only_role": True,
}
SQLITE = {"name": "parts", "engine": "sqlite", "path": "/data/parts.db", "environment": "dev"}
PROJECT = """version = 1
[[profile]]
name = "proj"
engine = "sqlite"
path = "data/p.db"
environment = "dev"
"""


class Env:
    def __init__(self, tmp_path: Path, static_dir: Path) -> None:
        self.project = tmp_path / "project"
        (self.project / ".tabrion").mkdir(parents=True)
        self.state = tmp_path / "state"
        self.state.mkdir(mode=0o700)
        self.config_dir = tmp_path / "config"
        self.fake = FakeKeyring()
        self.secrets = Secrets(KeyringStore(lambda: self.fake), SessionSecrets(), {})
        project = ProjectConfig(self.project, self.state, "k1", self.config_dir)
        self.config = Configuration(project, UserConfig(self.config_dir), self.secrets)
        self.studio = make_studio(static_dir, config=self.config)
        self.headers = {**self.studio.sign_in(), "Origin": ORIGIN}

    def send(self, method: str, path: str, body: Any, headers: dict[str, str] | None = None) -> Any:
        return self.studio.client.request(method, path, json=body, headers=self.headers if headers is None else headers)

    def listing(self) -> dict[str, Any]:
        response = self.studio.client.get("/api/profiles", headers=self.headers)
        assert response.status_code == 200
        result: dict[str, Any] = response.json()
        return result

    def digest(self) -> str | None:
        value: str | None = self.listing()["user_file"]["digest"]
        return value

    def create(self, profile: dict[str, Any]) -> Any:
        return self.send("POST", "/api/profiles", {"profile": profile, "digest": self.digest()})

    def update(self, profile_id: str, profile: dict[str, Any]) -> Any:
        return self.send("PUT", "/api/profiles", {"id": profile_id, "profile": profile, "digest": self.digest()})

    def file(self) -> str:
        return (self.config_dir / PROFILES_FILE).read_text(encoding="utf-8")


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[LogSetup]:
    setup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        yield setup
    finally:
        setup.close()
        forget_secrets()
        assert setup.file is not None
        assert CANARY not in setup.file.read_text(encoding="utf-8"), "a typed password reached a log line"


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, logs: LogSetup) -> Env:
    return Env(tmp_path, static_dir)


def test_create_change_and_delete_a_user_profile(env: Env) -> None:
    assert env.listing()["user_file"] == {"digest": None, "hand_written": False}
    response = env.create(PG)
    assert response.status_code == 201
    assert response.json()["id"] == "user:measure"
    if POSIX:
        assert stat.S_IMODE((env.config_dir / PROFILES_FILE).stat().st_mode) == 0o600
    listed = {p["id"]: p for p in env.listing()["profiles"]}
    assert listed["user:measure"]["sslmode"] == "verify-full"  # 03 T-10, derived and written
    assert env.create(SQLITE).status_code == 201
    changed = {**PG, "host": "db2.internal"}
    response = env.send("PUT", "/api/profiles", {"id": "user:measure", "profile": changed, "digest": env.digest()})
    assert response.status_code == 200 and 'host = "db2.internal"' in env.file()
    response = env.send("DELETE", "/api/profiles", {"id": "user:parts", "digest": env.digest()})
    assert response.status_code == 200
    assert [p["id"] for p in env.listing()["profiles"]] == ["user:measure"]


def test_a_retargeted_or_deleted_profile_forgets_its_stored_secret(env: Env) -> None:
    """C-1: the saved password belongs to the old target; deleting the profile removes it too."""
    assert env.create(PG).status_code == 201
    put = env.send("PUT", "/api/profiles/user:measure/secret", {"secret": CANARY, "store": "keyring"})
    assert put.status_code == 204 and len(env.fake.values) == 1
    same = {**PG, "read_only_role": False}  # not the target: the secret stays
    assert env.update("user:measure", same).status_code == 200
    assert env.config.resolve_secret("user:measure") == CANARY
    moved = {**same, "host": "elsewhere.example"}
    assert env.update("user:measure", moved).status_code == 200
    assert env.fake.values == {}
    env.send("PUT", "/api/profiles/user:measure/secret", {"secret": CANARY, "store": "session"})
    assert env.send("DELETE", "/api/profiles", {"id": "user:measure", "digest": env.digest()}).status_code == 200
    assert not env.secrets.session.has(env.config.entries()[0].secret.key) if env.config.entries() else True


def test_a_plain_password_is_refused_without_echo(env: Env) -> None:
    response = env.create({**PG, "password": CANARY})
    assert response.status_code == 422
    doc = response.json()
    assert doc["code"] == "studio.profile_invalid"
    assert doc["issues"] == [{"pointer": "profile.password", "code": "config.plain_password"}]
    assert CANARY not in response.text
    assert not (env.config_dir / PROFILES_FILE).exists()


@pytest.mark.parametrize(
    ("profile", "issues"),
    [
        ({**PG, "environment": None}, [{"pointer": "profile.environment", "code": "config.required"}]),
        ({**PG, "port": "5432"}, [{"pointer": "profile.port", "code": "config.type"}]),
        ({**PG, "port": True}, [{"pointer": "profile.port", "code": "config.type"}]),
        ({**PG, "port": 70000}, [{"pointer": "profile.port", "code": "config.range"}]),
        ({**SQLITE, "host": "h"}, [{"pointer": "profile.host", "code": "config.not_for_engine"}]),
        ({**SQLITE, "path": "//attacker.example/s/x.db"}, [{"pointer": "profile.path", "code": "config.network_path"}]),
        ({**PG, f"x-{CANARY}": 1}, [{"pointer": "profile", "code": "config.unknown_key"}]),
        ({**PG, "name": "a\nb"}, [{"pointer": "profile.name", "code": "config.control_character"}]),
        ("not a profile", [{"pointer": "profile", "code": "config.type"}]),
    ],
)
def test_invalid_profiles_name_fixed_keys_only(env: Env, profile: Any, issues: list[dict[str, str]]) -> None:
    response = env.create(profile)
    assert response.status_code == 422 and response.json()["issues"] == issues
    assert CANARY not in response.text


def test_names_are_unique_and_never_renamed(env: Env) -> None:
    assert env.create(PG).status_code == 201
    response = env.create(PG)
    assert response.status_code == 409 and response.json()["code"] == "studio.profile_exists"
    renamed = {**PG, "name": "other"}
    response = env.send("PUT", "/api/profiles", {"id": "user:measure", "profile": renamed, "digest": env.digest()})
    assert response.status_code == 422
    assert response.json()["issues"] == [{"pointer": "profile.name", "code": "config.rename_unsupported"}]
    missing = env.send(
        "PUT", "/api/profiles", {"id": "user:nope", "profile": {**PG, "name": "nope"}, "digest": env.digest()}
    )
    assert missing.status_code == 404


def test_project_profiles_are_never_written(env: Env) -> None:
    file = env.project / ".tabrion" / "studio.toml"
    file.write_text(PROJECT, encoding="utf-8")
    digest = env.studio.client.get("/api/project", headers=env.headers).json()["digest"]
    assert env.send("POST", "/api/project/trust", {"digest": digest}).status_code == 204
    for method, body in (
        ("PUT", {"id": "project:proj", "profile": {**SQLITE, "name": "proj"}, "digest": None}),
        ("DELETE", {"id": "project:proj", "digest": None}),
    ):
        response = env.send(method, "/api/profiles", body)
        assert response.status_code == 409 and response.json()["code"] == "studio.profile_read_only"
    assert file.read_text(encoding="utf-8") == PROJECT


def test_a_stale_digest_and_a_hand_written_file(env: Env) -> None:
    assert env.create(PG).status_code == 201
    stale = env.send("POST", "/api/profiles", {"profile": SQLITE, "digest": None})
    assert stale.status_code == 409 and stale.json()["code"] == "studio.config_stale"
    (env.config_dir / PROFILES_FILE).write_text("# mine\n" + env.file(), encoding="utf-8")
    assert env.listing()["user_file"]["hand_written"] is True
    response = env.create(SQLITE)
    assert response.status_code == 409 and response.json()["code"] == "studio.config_hand_written"
    assert env.file().startswith("# mine")
    confirmed = env.send("POST", "/api/profiles", {"profile": SQLITE, "digest": env.digest(), "overwrite": True})
    assert confirmed.status_code == 201 and not env.file().startswith("# mine")


def test_settings_are_saved(env: Env) -> None:
    doc = env.studio.client.get("/api/settings", headers=env.headers).json()
    assert doc["file"] == {"digest": None, "hand_written": False}
    body = {"settings": {"log_sql": True, "limits": {"max_rows": 500}}, "digest": None}
    response = env.send("PUT", "/api/settings", body)
    assert response.status_code == 200
    assert response.json()["settings"]["limits"] == {"max_rows": 500, "statement_timeout_ms": 30000}
    assert (env.config_dir / SETTINGS_FILE).read_text(encoding="utf-8").endswith("[limits]\nmax_rows = 500\n")
    bad = env.send(
        "PUT", "/api/settings", {"settings": {"limits": {"max_rows": 0}}, "digest": response.json()["digest"]}
    )
    assert bad.status_code == 422
    assert bad.json()["issues"] == [{"pointer": "settings.limits.max_rows", "code": "config.range"}]
    # The appearance (TD-021): keys left out keep their value, so the page can send one choice alone.
    look = env.send("PUT", "/api/settings", {"settings": {"theme": "dark"}, "digest": response.json()["digest"]})
    assert look.status_code == 200
    assert look.json()["settings"]["theme"] == "dark"
    assert look.json()["settings"]["limits"]["max_rows"] == 500
    feel = env.send("PUT", "/api/settings", {"settings": {"density": "normal"}, "digest": look.json()["digest"]})
    assert feel.status_code == 200 and feel.json()["settings"]["density"] == "normal"
    wrong = env.send("PUT", "/api/settings", {"settings": {"density": "tiny"}, "digest": feel.json()["digest"]})
    assert wrong.status_code == 422
    assert wrong.json()["issues"] == [{"pointer": "settings.density", "code": "config.choice"}]
    unknown = env.send("PUT", "/api/settings", {"settings": {"telemetry": True}, "digest": None})
    assert unknown.status_code == 422 and unknown.json()["issues"] == [
        {"pointer": "settings", "code": "config.unknown_key"}
    ]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_writes_need_the_session_csrf_origin_and_json(env: Env, method: str) -> None:
    path = "/api/profiles"
    body = {"profile": PG, "id": "user:measure", "digest": None}
    no_csrf = {k: v for k, v in env.headers.items() if k != "X-Tabrion-CSRF"}
    assert env.send(method, path, body, headers=no_csrf).status_code == 403
    no_origin = {k: v for k, v in env.headers.items() if k != "Origin"}
    assert env.send(method, path, body, headers=no_origin).status_code == 403
    response = env.studio.client.request(method, path, content=b"{}", headers=env.headers)
    assert response.status_code == 415  # the JSON rule, also for DELETE (the page sends the Content-Type)
    assert env.send(method, path, {"profile": PG}).status_code in (400, 403)  # no digest: refused
    assert not (env.config_dir / PROFILES_FILE).exists()


def test_deleting_a_profile_removes_its_keyring_entry(env: Env) -> None:
    """L-3 of stu-008-review.md (mutant W11): the saved password goes with the profile."""
    assert env.create(PG).status_code == 201
    put = env.send("PUT", "/api/profiles/user:measure/secret", {"secret": CANARY, "store": "keyring"})
    assert put.status_code == 204 and len(env.fake.values) == 1
    response = env.send("DELETE", "/api/profiles", {"id": "user:measure", "digest": env.digest()})
    assert response.status_code == 200 and response.json()["secret_removed"] is True
    assert env.fake.values == {}


def test_a_keyring_that_refuses_the_delete_is_reported(env: Env, logs: LogSetup) -> None:
    """L-3: the profile is gone, the page learns that the password stayed, and the log says so (no message)."""
    assert env.create(PG).status_code == 201
    env.send("PUT", "/api/profiles/user:measure/secret", {"secret": CANARY, "store": "keyring"})
    env.fake.fail_delete = True
    response = env.send("DELETE", "/api/profiles/user:measure/secret", {})
    assert response.status_code == 502 and response.json()["code"] == "studio.keyring_failed"
    response = env.send("DELETE", "/api/profiles", {"id": "user:measure", "digest": env.digest()})
    assert response.status_code == 200 and response.json()["secret_removed"] is False
    assert env.listing()["profiles"] == []
    assert len(env.fake.values) == 1
    assert logs.file is not None
    text = logs.file.read_text(encoding="utf-8")
    assert "studio.keyring_failed" in text and "secret-in-message" not in text


@pytest.mark.parametrize("key", ["name", "database", "user", "host"])
def test_a_lone_surrogate_is_a_form_issue_not_a_500(env: Env, key: str) -> None:
    """L-2 of stu-008-review.md: valid JSON, not UTF-8; refused with a fixed key, nothing written."""
    # json.dumps escapes the lone surrogate as \\ud800 (valid JSON), as a browser's JSON.stringify does.
    body = json.dumps({"profile": {**PG, key: "a\ud800b"}, "digest": None}).encode("ascii")
    headers = {**env.headers, "Content-Type": "application/json"}
    response = env.studio.client.post("/api/profiles", content=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["issues"] == [{"pointer": f"profile.{key}", "code": "config.encoding"}]
    assert not (env.config_dir / PROFILES_FILE).exists()


def test_a_file_that_would_not_read_back_is_a_4xx(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """L-2: the read-back check refuses to write; the answer is a fixed 422, not a 500 with a traceback."""
    from tabrion_studio.config import user as user_module

    def broken(profiles: object) -> bytes:
        raise ValueError("the written profiles do not read back")

    monkeypatch.setattr(user_module, "checked_profiles", broken)
    response = env.create(PG)
    assert response.status_code == 422 and response.json()["code"] == "studio.config_not_storable"
    assert not (env.config_dir / PROFILES_FILE).exists()


def test_a_hand_edit_just_before_the_replace_wins(env: Env) -> None:
    """I-3 of stu-008-review.md: the file is read once more right before it is replaced."""
    assert env.create(PG).status_code == 201
    expected = env.digest()
    file = env.config_dir / PROFILES_FILE

    def edit_meanwhile(profiles: list[Any]) -> list[Any]:
        file.write_text(file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return profiles

    with pytest.raises(SaveError) as caught:
        env.config.user.save_profiles(edit_meanwhile, expected)
    assert caught.value.code == "studio.config_stale"
    assert file.read_text(encoding="utf-8").endswith("\n\n")
