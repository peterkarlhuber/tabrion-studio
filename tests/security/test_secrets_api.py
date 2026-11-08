"""STU-007 through the API: secrets never leave the backend (T-05, SQ-04; STU-T-008) and have no plain-text fallback.

A canary (``tabrion-canary-<uuid>``) is entered as a password, put into an environment variable and into a ``file:``
secret; the answers of every configuration route and both log outputs must not contain it. Without a usable keyring,
storing in the keyring is refused and nothing is written to any file.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import logging
from pathlib import Path
import sys
from typing import Any
import uuid

import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.secrets import SERVICE, KeyringStore, SecretError, Secrets, SessionSecrets
from tabrion_studio.config.user import UserConfig
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tests.support.access import ORIGIN, Studio, make_studio
from tests.support.fake_keyring import FakeKeyring

pytestmark = pytest.mark.security

POSIX = sys.platform != "win32"
CANARY = f"tabrion-canary-{uuid.uuid4()}"

USER_PROFILES = """version = 1
[[profile]]
name = "mine"
engine = "postgresql"
host = "db.internal"
database = "d"
password = "keyring:"
environment = "dev"

[[profile]]
name = "from-env"
engine = "postgresql"
host = "db.internal"
database = "d"
password = "env:STUDIO_TEST_PASSWORD"
environment = "dev"
"""

PROJECT = """version = 1
[[profile]]
name = "proj"
engine = "postgresql"
host = "db.internal"
database = "d"
password = "file:secrets/db"
environment = "dev"
"""


class Env:
    def __init__(self, tmp_path: Path, static_dir: Path, *, keyring_available: bool = True) -> None:
        self.project = tmp_path / "project"
        (self.project / ".tabrion").mkdir(parents=True)
        (self.project / "secrets").mkdir()
        self.state = tmp_path / "state"
        self.state.mkdir(mode=0o700)
        self.config_dir = tmp_path / "config"
        self.config_dir.mkdir(mode=0o700)
        profiles = self.config_dir / "profiles.toml"
        profiles.write_text(USER_PROFILES, encoding="utf-8")
        profiles.chmod(0o600)
        self.fake = FakeKeyring()
        backend = self.fake if keyring_available else None
        self.secrets = Secrets(KeyringStore(lambda: backend), SessionSecrets(), {"STUDIO_TEST_PASSWORD": CANARY})
        self.project_config = ProjectConfig(self.project, self.state, "k1", self.config_dir)
        self.config = Configuration(self.project_config, UserConfig(self.config_dir), self.secrets)
        self.studio: Studio = make_studio(static_dir, config=self.config)
        self.headers = {**self.studio.sign_in(), "Origin": ORIGIN}

    def put(self, profile_id: str, body: Any, headers: dict[str, str] | None = None) -> Any:
        return self.studio.client.put(
            f"/api/profiles/{profile_id}/secret", json=body, headers=self.headers if headers is None else headers
        )

    def delete(self, profile_id: str) -> Any:
        return self.studio.client.request("DELETE", f"/api/profiles/{profile_id}/secret", json={}, headers=self.headers)

    def profiles(self) -> dict[str, dict[str, Any]]:
        response = self.studio.client.get("/api/profiles", headers=self.headers)
        assert response.status_code == 200 and CANARY not in response.text
        return {p["id"]: p for p in response.json()["profiles"]}

    def trust_project(self) -> None:
        file = self.project / ".tabrion" / "studio.toml"
        file.write_text(PROJECT, encoding="utf-8")
        digest = self.studio.client.get("/api/project", headers=self.headers).json()["digest"]
        response = self.studio.client.post("/api/project/trust", json={"digest": digest}, headers=self.headers)
        assert response.status_code == 204


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[tuple[LogSetup, io.StringIO]]:
    console = io.StringIO()
    setup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=console)
    try:
        yield setup, console
    finally:
        setup.close()
        forget_secrets()
        assert setup.file is not None
        text = setup.file.read_text(encoding="utf-8") + console.getvalue()
        assert CANARY not in text, "a secret reached a log line"


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, logs: object) -> Env:
    return Env(tmp_path, static_dir)


def test_profiles_show_where_a_secret_comes_from_never_the_value(env: Env) -> None:
    profiles = env.profiles()
    assert profiles["user:mine"]["secret"] == {"source": "keyring", "has_secret": None}
    assert profiles["user:from-env"]["secret"] == {"source": "env", "has_secret": True}
    assert profiles["user:mine"]["password"] == {"source": "keyring", "target": ""}
    assert env.fake.reads == 0  # listing never reads the keyring (a system dialog on macOS)


def test_an_entered_password_goes_to_the_keyring_and_never_comes_back(env: Env) -> None:
    response = env.put("user:mine", {"secret": CANARY, "store": "keyring"})
    assert response.status_code == 204 and response.content == b""
    [(service, key)] = env.fake.values
    assert service == SERVICE and key.startswith("user:mine:") and env.fake.values[(service, key)] == CANARY
    assert env.config.resolve_secret("user:mine") == CANARY
    for path in ("/api/profiles", "/api/project", "/api/settings", "/api/secrets"):
        response = env.studio.client.get(path, headers=env.headers)
        assert response.status_code == 200 and CANARY not in response.text
    logging.getLogger("tabrion_studio.test").warning("by mistake: %s", CANARY)  # registered: redacted
    assert env.delete("user:mine").status_code == 204
    assert env.fake.values == {}


def test_a_session_password_lives_in_memory_only(env: Env, tmp_path: Path) -> None:
    assert env.put("user:mine", {"secret": CANARY, "store": "session"}).status_code == 204
    assert env.fake.values == {}
    assert env.profiles()["user:mine"]["secret"]["has_secret"] is True
    assert env.config.resolve_secret("user:mine") == CANARY
    for path in tmp_path.rglob("*"):
        assert not path.is_file() or CANARY not in path.read_text(errors="replace")
    assert env.delete("user:mine").status_code == 204
    with pytest.raises(SecretError) as caught:
        env.config.resolve_secret("user:mine")
    assert caught.value.code == "studio.secret_missing"


def test_without_a_keyring_there_is_no_plain_text_fallback(tmp_path: Path, static_dir: Path, logs: object) -> None:
    """09 (STU-007): the negative test. The page learns that there is no keyring; a session secret still works."""
    env = Env(tmp_path, static_dir, keyring_available=False)
    assert env.studio.client.get("/api/secrets", headers=env.headers).json() == {"keyring": {"available": False}}
    response = env.put("user:mine", {"secret": CANARY, "store": "keyring"})
    assert response.status_code == 409 and response.json()["code"] == "studio.keyring_unavailable"
    assert CANARY not in response.text
    for path in tmp_path.rglob("*"):
        assert not path.is_file() or CANARY not in path.read_text(errors="replace")
    assert env.put("user:mine", {"secret": CANARY, "store": "session"}).status_code == 204
    assert env.config.resolve_secret("user:mine") == CANARY


@pytest.mark.parametrize(
    ("profile_id", "body", "status", "code"),
    [
        ("user:nobody", {"secret": "x", "store": "keyring"}, 404, "studio.profile_not_found"),
        ("project:proj", {"secret": "x", "store": "keyring"}, 404, "studio.profile_not_found"),  # untrusted
        ("user:from-env", {"secret": "x", "store": "keyring"}, 409, "studio.secret_not_storable"),
        ("user:mine", {"secret": "", "store": "keyring"}, 400, "studio.secret_invalid"),
        ("user:mine", {"secret": "x" * 1025, "store": "keyring"}, 400, "studio.secret_invalid"),
        ("user:mine", {"secret": ["x"], "store": "keyring"}, 400, "studio.secret_invalid"),
        ("user:mine", {"secret": "x", "store": "file"}, 400, "studio.secret_invalid"),
        ("user:mine", ["not", "an", "object"], 400, "access.bad_request"),
    ],
)
def test_refused_secrets_are_not_echoed(env: Env, profile_id: str, body: Any, status: int, code: str) -> None:
    if isinstance(body, dict) and body.get("secret") == "x":
        body = {**body, "secret": CANARY}
    response = env.put(profile_id, body)
    assert response.status_code == status and response.json()["code"] == code
    assert CANARY not in response.text
    assert env.fake.values == {}


def test_storing_a_secret_needs_a_session_csrf_origin_and_json(env: Env) -> None:
    body = {"secret": CANARY, "store": "keyring"}
    no_csrf = {k: v for k, v in env.headers.items() if k.lower() != "x-tabrion-csrf"}
    assert env.put("user:mine", body, no_csrf).json()["code"] == "access.csrf_invalid"
    no_origin = {k: v for k, v in env.headers.items() if k != "Origin"}
    assert env.put("user:mine", body, no_origin).json()["code"] == "access.origin_required"
    response = env.studio.client.put(
        "/api/profiles/user:mine/secret", content=CANARY, headers={**env.headers, "Content-Type": "text/plain"}
    )
    assert response.json()["code"] == "access.json_only"
    assert env.fake.values == {}


def test_a_trusted_project_profile_reads_its_file_secret_with_the_c3_checks(env: Env) -> None:
    secret = env.project / "secrets" / "db"
    secret.write_text(CANARY + "\n", encoding="utf-8")
    secret.chmod(0o600)
    env.trust_project()
    profiles = env.profiles()
    assert profiles["project:proj"]["secret"] == {"source": "file", "has_secret": None}
    assert env.config.resolve_secret("project:proj") == CANARY
    if POSIX:
        secret.chmod(0o644)
        with pytest.raises(SecretError) as caught:
            env.config.resolve_secret("project:proj")
        assert caught.value.code == "studio.secret_file_not_private"
    # A file: profile names its own source: an entered password is not used for it.
    response = env.put("project:proj", {"secret": "x", "store": "session"})
    assert response.status_code == 409 and response.json()["code"] == "studio.secret_not_storable"


@pytest.mark.skipif(not POSIX, reason="folder symlinks")
def test_a_project_file_secret_moved_outside_after_the_trust_is_refused_when_resolving(
    env: Env, tmp_path: Path
) -> None:
    """I-2 (mutant K16): the API resolves a project file: secret with the project and configuration folders as roots."""
    secret = env.project / "secrets" / "db"
    secret.write_text("inside\n", encoding="utf-8")
    secret.chmod(0o600)
    env.trust_project()
    assert env.config.entry("project:proj").secret.roots == [env.project, env.config_dir]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "db").write_text(CANARY + "\n", encoding="utf-8")
    (outside / "db").chmod(0o600)
    secret.unlink()
    (env.project / "secrets").rmdir()
    (env.project / "secrets").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SecretError) as caught:
        env.config.resolve_secret("project:proj")
    assert caught.value.code in ("studio.secret_file_outside", "studio.profile_not_found")


def test_the_keyring_entry_of_a_project_profile_is_bound_to_the_folder(env: Env) -> None:
    file = env.project / ".tabrion" / "studio.toml"
    env.trust_project()
    file.write_text(PROJECT.replace('"file:secrets/db"', '"keyring:"'), encoding="utf-8")
    digest = env.studio.client.get("/api/project", headers=env.headers).json()["digest"]
    assert env.studio.client.post("/api/project/trust", json={"digest": digest}, headers=env.headers).status_code == 204
    assert env.put("project:proj", {"secret": CANARY, "store": "keyring"}).status_code == 204
    [(service, key)] = env.fake.values
    assert service == SERVICE and key.startswith("project:k1:proj:")


def _retrust(env: Env, text: str) -> dict[str, Any]:
    (env.project / ".tabrion" / "studio.toml").write_text(text, encoding="utf-8")
    project = env.studio.client.get("/api/project", headers=env.headers).json()
    trusted = env.studio.client.post("/api/project/trust", json={"digest": project["digest"]}, headers=env.headers)
    assert trusted.status_code == 204
    result: dict[str, Any] = project
    return result


@pytest.mark.parametrize("store", ["keyring", "session"])
def test_a_stored_password_does_not_follow_a_retargeted_project_profile(env: Env, store: str) -> None:
    """C-1 (M-1 of stu-007-review.md), the reproduced attack: same name, host changed by a commit, one re-trust."""
    env.trust_project()
    keyring_profile = PROJECT.replace('"file:secrets/db"', '"keyring:"').replace('name = "proj"', 'name = "prod"')
    _retrust(env, keyring_profile)
    assert env.put("project:prod", {"secret": CANARY, "store": store}).status_code == 204
    assert env.config.resolve_secret("project:prod") == CANARY
    project = _retrust(env, keyring_profile.replace('"db.internal"', '"evil.example"'))
    # D-01 said so before the trust: the saved password does not go with the profile.
    assert project["state"] == "changed"
    assert project["diff"]["connections"]["retargeted"] == ["prod"]
    with pytest.raises(SecretError) as caught:
        env.config.resolve_secret("project:prod")
    assert caught.value.code == "studio.secret_missing"
    assert env.profiles()["project:prod"]["secret"]["has_secret"] is None
    # Back to the old target: the secret entered for it is used again.
    _retrust(env, keyring_profile)
    assert env.config.resolve_secret("project:prod") == CANARY


def test_a_keyring_error_is_a_problem_without_its_message(env: Env) -> None:
    env.fake.fail = True
    response = env.put("user:mine", {"secret": CANARY, "store": "keyring"})
    assert response.status_code == 502 and response.json()["code"] == "studio.keyring_failed"
    assert "locked" not in response.text and CANARY not in response.text
