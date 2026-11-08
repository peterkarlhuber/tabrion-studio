"""STU-006 project trust against a hostile project file (threat T-08, AT4; STU-T-016, -017).

STU-T-016: a project file is never used before the user confirmed exactly its bytes; what the dialog needs (hosts,
file references, environment variables, outside paths, unknown keys) is reported; a changed file needs a new
confirmation and comes with the diff. STU-T-017: ``file:`` references outside the project and the user configuration
are refused, links are not followed, and the trust record fails closed. Every case also checks that a canary in the
file never reaches an answer that is not about that value, nor a log line.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any
import uuid

import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.files import MAX_FILE_BYTES
from tabrion_studio.config.project import ProjectConfig, TrustError
from tabrion_studio.config.user import UserConfig
from tabrion_studio.logs import configure_logging
from tests.support.access import ORIGIN, Studio, make_studio

pytestmark = pytest.mark.security

POSIX = sys.platform != "win32"
CANARY = f"tabrion-canary-{uuid.uuid4()}"

GOOD = """version = 1
[[profile]]
name = "parts-dev"
engine = "sqlite"
path = "data/parts.db"
environment = "dev"

[[profile]]
name = "measure-central"
engine = "postgresql"
host = "db.internal"
database = "measurements"
user = "studio_ro"
password = "env:PGPASSWORD"
environment = "prod"
"""


class Env:
    def __init__(self, tmp_path: Path, static_dir: Path) -> None:
        self.project = tmp_path / "project"
        (self.project / ".tabrion").mkdir(parents=True)
        self.state = tmp_path / "state"
        self.state.mkdir(mode=0o700)
        self.config_dir = tmp_path / "config"
        self.config_dir.mkdir(mode=0o700)
        self.project_config = ProjectConfig(self.project, self.state, "k1", self.config_dir)
        config = Configuration(self.project_config, UserConfig(self.config_dir))
        self.studio: Studio = make_studio(static_dir, config=config)
        self.headers = {**self.studio.sign_in(), "Origin": ORIGIN}

    @property
    def file(self) -> Path:
        return self.project / ".tabrion" / "studio.toml"

    def write(self, text: str | bytes) -> str:
        data = text.encode() if isinstance(text, str) else text
        self.file.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def project_doc(self) -> dict[str, Any]:
        response = self.studio.client.get("/api/project", headers=self.headers)
        assert response.status_code == 200
        doc = response.json()
        assert isinstance(doc, dict)
        return doc

    def trust(self, digest: object) -> Any:
        return self.studio.client.post("/api/project/trust", json={"digest": digest}, headers=self.headers)

    def project_profiles(self) -> list[str]:
        doc = self.studio.client.get("/api/profiles", headers=self.headers).json()
        return [p["name"] for p in doc["profiles"] if p["source"] == "project"]


@pytest.fixture
def env(tmp_path: Path, static_dir: Path) -> Iterator[Env]:
    logs = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG)
    try:
        yield Env(tmp_path, static_dir)
    finally:
        logs.close()
        assert logs.file is not None
        assert CANARY not in logs.file.read_text(encoding="utf-8")


def test_without_a_project_file_nothing_is_to_trust(env: Env) -> None:
    doc = env.project_doc()
    assert doc["state"] == "none" and doc["trusted"] is False and doc["review"] is None
    assert env.trust("0" * 64).status_code == 404


def test_project_profiles_are_unused_until_trusted(env: Env) -> None:
    """STU-T-016: first load shows the review; the profiles appear only after the confirmation of this digest."""
    digest = env.write(GOOD)
    doc = env.project_doc()
    assert doc["state"] == "untrusted" and doc["digest"] == digest
    review = doc["review"]
    assert isinstance(review, dict)
    assert [c["name"] for c in review["connections"]] == ["parts-dev", "measure-central"]
    assert review["hosts"] == ["db.internal:5432"]
    assert review["files"] == [{"path": "data/parts.db", "kind": "database", "outside": False}]
    assert review["env"] == ["PGPASSWORD"]
    assert env.project_profiles() == []
    response = env.trust(digest)
    assert response.status_code == 204
    assert env.project_doc()["state"] == "trusted"
    assert env.project_profiles() == ["parts-dev", "measure-central"]
    record = env.project_config.record
    if POSIX:
        assert record.stat().st_mode & 0o777 == 0o600


def test_a_changed_file_needs_a_new_confirmation_and_shows_the_diff(env: Env) -> None:
    """T-08 and review U-1d: added, removed and changed connections and the new host and variable."""
    env.trust(env.write(GOOD))
    changed = (
        GOOD.replace('host = "db.internal"', 'host = "attacker.example"').replace(
            'name = "parts-dev"', 'name = "parts-dev2"'
        )
        + '\n[[profile]]\nname = "x"\nengine = "postgresql"\nhost = "h"\ndatabase = "d"\n'
        + ('password = "env:AWS_SECRET_ACCESS_KEY"\nenvironment = "dev"\n')
    )
    digest = env.write(changed)
    doc = env.project_doc()
    assert doc["state"] == "changed" and doc["trusted"] is False
    diff = doc["diff"]
    assert isinstance(diff, dict)
    assert diff["connections"] == {
        "added": ["parts-dev2", "x"],
        "removed": ["parts-dev"],
        "changed": ["measure-central"],
    }
    assert diff["hosts"] == {"added": ["attacker.example:5432", "h:5432"], "removed": ["db.internal:5432"]}
    assert diff["env"] == {"added": ["AWS_SECRET_ACCESS_KEY"], "removed": []}
    assert env.project_profiles() == []  # the old trust does not carry over
    assert env.trust(digest).status_code == 204
    assert env.project_profiles() == ["parts-dev2", "measure-central", "x"]


def test_trusting_needs_the_digest_that_was_shown(env: Env) -> None:
    """A file swapped between the dialog and the click is not trusted (TOCTOU)."""
    shown = env.write(GOOD)
    env.write(GOOD + "\n# changed after the dialog\n")
    response = env.trust(shown)
    assert response.status_code == 409
    assert response.json()["code"] == "studio.trust_stale"
    assert env.project_profiles() == []
    for bad in (None, "", "x" * 64, shown.upper(), 42, [shown]):
        assert env.trust(bad).status_code == 400
    bodies = [b"[]", b"not json", b'{"digest": "' + b"a" * 10_000 + b'"}']
    for body in bodies:
        response = env.studio.client.post(
            "/api/project/trust", content=body, headers={**env.headers, "Content-Type": "application/json"}
        )
        assert response.status_code == 400


def test_trust_needs_a_session_csrf_and_origin(env: Env) -> None:
    digest = env.write(GOOD)
    client = env.studio.client
    client.cookies.clear()
    assert client.post("/api/project/trust", json={"digest": digest}, headers=env.headers).status_code == 401
    env.headers = {**env.studio.sign_in(), "Origin": ORIGIN}
    no_csrf = {"Origin": ORIGIN}
    assert client.post("/api/project/trust", json={"digest": digest}, headers=no_csrf).status_code == 403
    no_origin = {k: v for k, v in env.headers.items() if k != "Origin"}
    assert client.post("/api/project/trust", json={"digest": digest}, headers=no_origin).status_code == 403
    foreign = {**env.headers, "Origin": "http://evil.example"}
    assert client.post("/api/project/trust", json={"digest": digest}, headers=foreign).status_code == 403
    form = {**env.headers, "Content-Type": "application/x-www-form-urlencoded"}
    assert client.post("/api/project/trust", content=f"digest={digest}", headers=form).status_code == 415
    assert env.project_profiles() == []


@pytest.mark.parametrize(
    "password",
    ["file:../outside.txt", "file:/etc/passwd", "file:~/../../../root/.ssh/id_rsa", "file:./secrets/../../x"],
)
def test_secret_files_outside_project_and_config_are_refused(env: Env, password: str) -> None:
    """STU-T-017: ``file:`` only inside the project folder or the user's configuration folder."""
    text = 'version = 1\n[[profile]]\nname = "p"\nengine = "sqlite"\npath = "a.db"\nenvironment = "dev"\n'
    digest = env.write(text + f'password = "{password}"\n')
    doc = env.project_doc()
    assert doc["state"] == "invalid"
    assert {"pointer": "profile[0].password", "code": "config.file_outside", "severity": "error"} in doc["issues"]
    assert env.trust(digest).json()["code"] == "studio.project_invalid"


def test_secret_files_inside_project_or_config_are_listed(env: Env) -> None:
    secret_in_config = env.config_dir / "db-secret"
    text = (
        'version = 1\n[[profile]]\nname = "a"\nengine = "sqlite"\npath = "../elsewhere.db"\nenvironment = "dev"\n'
        'password = "file:./secrets/db"\n'
        f'[[profile]]\nname = "b"\nengine = "sqlite"\npath = "b.db"\nenvironment = "dev"\n'
        f"password = 'file:{secret_in_config}'\n"
    )
    env.write(text)
    doc = env.project_doc()
    assert doc["state"] == "untrusted"
    files = doc["review"]["files"]
    assert {"path": "../elsewhere.db", "kind": "database", "outside": True} in files
    assert {"path": "./secrets/db", "kind": "secret", "outside": False} in files
    assert {"path": str(secret_in_config), "kind": "secret", "outside": True} in files


@pytest.mark.skipif(not POSIX, reason="symlinks")
def test_links_are_not_followed(env: Env, tmp_path: Path) -> None:
    """A link as the project file, as .tabrion, or a database path through a link out of the project."""
    target = tmp_path / "real.toml"
    target.write_text(GOOD)
    env.file.symlink_to(target)
    assert env.project_doc()["issues"] == [{"pointer": "file", "code": "config.unsafe_file", "severity": "error"}]
    env.file.unlink()
    os.rmdir(env.project / ".tabrion")
    (tmp_path / "dotdir").mkdir()
    (tmp_path / "dotdir" / "studio.toml").write_text(GOOD)
    (env.project / ".tabrion").symlink_to(tmp_path / "dotdir")
    assert env.project_doc()["state"] == "invalid"
    (env.project / ".tabrion").unlink()
    (env.project / ".tabrion").mkdir()
    (env.project / "data").symlink_to(tmp_path)
    env.write(GOOD)
    files = env.project_doc()["review"]["files"]
    assert files == [{"path": "data/parts.db", "kind": "database", "outside": True}]


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"x" * (MAX_FILE_BYTES + 1), "config.too_large"),
        (b"version = 1\n\xff\xfe", "config.encoding"),
        (f'version = 1\nname = "{CANARY}\n'.encode(), "config.syntax"),
        (b"version = 3\n", "config.unsupported_version"),
    ],
)
def test_unusable_files_are_invalid_without_echo(env: Env, content: bytes, code: str) -> None:
    env.write(content)
    response = env.studio.client.get("/api/project", headers=env.headers)
    doc = response.json()
    assert doc["state"] == "invalid" and doc["review"] is None
    assert code in {issue["code"] for issue in doc["issues"]}
    assert CANARY not in response.text
    assert env.project_profiles() == []


def test_a_plain_password_in_the_project_file_never_leaves_it(env: Env) -> None:
    text = GOOD.replace('password = "env:PGPASSWORD"', f'password = "{CANARY}"')
    env.write(text)
    for path in ("/api/project", "/api/profiles", "/api/settings"):
        response = env.studio.client.get(path, headers=env.headers)
        assert CANARY not in response.text, path
    assert {"pointer": "profile[1].password", "code": "config.plain_password", "severity": "error"} in (
        env.project_doc()["issues"]
    )


@pytest.mark.parametrize(
    "record",
    [
        b"not json",
        b"[]",
        b'{"version": 1}',
        b'{"version": 2, "project": "P", "digest": "D", "review": {}}',
        b'{"version": 1, "project": "/other", "digest": "D", "review": {}}',
        b'{"version": 1, "project": "P", "digest": "D", "review": []}',
        b'{"version": 1, "project": "P", "digest": "d0", "review": {}}',
    ],
)
def test_a_damaged_or_foreign_trust_record_means_untrusted(env: Env, record: bytes) -> None:
    digest = env.write(GOOD)
    raw = record.replace(b'"P"', json.dumps(str(env.project)).encode()).replace(b'"D"', f'"{digest}"'.encode())
    path = env.project_config.record
    path.write_bytes(raw)
    path.chmod(0o600)
    assert env.project_doc()["state"] == "untrusted"
    assert env.project_profiles() == []


@pytest.mark.skipif(not POSIX, reason="POSIX file modes")
def test_a_trust_record_readable_by_others_is_ignored(env: Env) -> None:
    env.trust(env.write(GOOD))
    env.project_config.record.chmod(0o644)
    assert env.project_doc()["state"] == "untrusted"
    env.project_config.record.chmod(0o600)
    assert env.project_doc()["state"] == "trusted"


def test_hostile_names_are_data(env: Env) -> None:
    """Names with commas, bidi controls and markup come back unchanged as list items (the dialog escapes them)."""
    names = ["parts-dev, safe (checked)", "evil" + chr(0x202E) + "txt.exe", "<img src=x onerror=alert(1)>"]
    body = "version = 1\n" + "".join(
        f'[[profile]]\nname = {json.dumps(n)}\nengine = "sqlite"\npath = "a.db"\nenvironment = "dev"\n' for n in names
    )
    env.write(body)
    connections = env.project_doc()["review"]["connections"]
    assert [c["name"] for c in connections] == names


@pytest.mark.skipif(not POSIX, reason="POSIX file modes")
def test_user_profiles_must_be_private(env: Env) -> None:
    user_file = env.config_dir / "profiles.toml"
    user_file.write_text(GOOD.replace("parts-dev", "mine"))
    user_file.chmod(0o644)
    doc = env.studio.client.get("/api/profiles", headers=env.headers).json()
    assert doc["profiles"] == [] and doc["issues"]["user"][0]["code"] == "config.not_private"
    user_file.chmod(0o600)
    doc = env.studio.client.get("/api/profiles", headers=env.headers).json()
    assert [p["id"] for p in doc["profiles"]] == ["user:mine", "user:measure-central"]
    assert doc["profiles"][1]["password"] == {"source": "env", "target": "PGPASSWORD"}


def test_settings(env: Env) -> None:
    (env.config_dir / "settings.toml").write_text('version = 1\nlanguage = "de"\ntelemetry = true\n')
    doc = env.studio.client.get("/api/settings", headers=env.headers).json()
    assert doc["settings"]["language"] == "de" and doc["settings"]["telemetry"] is False
    assert doc["issues"] == [
        {"pointer": "settings.telemetry", "code": "config.telemetry_unsupported", "severity": "error"}
    ]


def test_config_routes_need_a_session(env: Env) -> None:
    env.studio.client.cookies.clear()
    for path in ("/api/project", "/api/profiles", "/api/settings"):
        assert env.studio.client.get(path).status_code == 401


# --- Review stu-006-review.md: T-1 (drift after trust), T-2 (network paths), T-3 (no 500) ----------------------


@pytest.mark.skipif(not POSIX, reason="symlinks")
def test_a_folder_that_becomes_a_link_out_of_the_project_needs_a_new_confirmation(env: Env, tmp_path: Path) -> None:
    """T-1 (probe P-4): same bytes, but data/ now leads out of the project: changed, with the file in the diff."""
    (env.project / "data").mkdir()
    digest = env.write(GOOD)
    assert env.trust(digest).status_code == 204
    assert env.project_doc()["state"] == "trusted"
    assert env.project_config.database_file("parts-dev") == (env.project / "data" / "parts.db").resolve()
    # A later checkout replaces data/ with a link out of the project; studio.toml is unchanged.
    os.rmdir(env.project / "data")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (env.project / "data").symlink_to(elsewhere)
    doc = env.project_doc()
    assert doc["state"] == "changed" and doc["digest"] == digest and doc["trusted"] is False
    assert doc["diff"]["files"] == {
        "added": [{"path": "data/parts.db", "kind": "database", "outside": True}],
        "removed": [{"path": "data/parts.db", "kind": "database", "outside": False}],
    }
    assert env.project_profiles() == []
    with pytest.raises(TrustError) as caught:
        env.project_config.database_file("parts-dev")
    assert caught.value.code == "studio.project_untrusted"
    # Confirming the new review (same digest) trusts it, now with "outside" recorded.
    assert env.trust(digest).status_code == 204
    assert env.project_doc()["state"] == "trusted"
    assert env.project_config.database_file("parts-dev") == (elsewhere / "parts.db").resolve()


@pytest.mark.skipif(not POSIX, reason="symlinks")
def test_the_database_file_is_rechecked_when_it_is_opened(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-1: the containment re-check at open time refuses a path confirmed as inside that now leads outside."""
    (env.project / "data").mkdir()
    assert env.trust(env.write(GOOD)).status_code == 204
    with pytest.raises(TrustError) as caught:
        env.project_config.database_file("measure-central")  # a PostgreSQL profile has no database file
    assert caught.value.code == "studio.profile_not_found"
    # Simulate the race: the link appears after load() saw a trusted state, before the file is opened.
    load = env.project_config.load

    def load_then_swap() -> Any:
        state = load()
        os.rmdir(env.project / "data")
        (env.project / "data").symlink_to(tmp_path)
        return state

    monkeypatch.setattr(env.project_config, "load", load_then_swap)
    with pytest.raises(TrustError) as caught:
        env.project_config.database_file("parts-dev")
    assert caught.value.code == "studio.database_outside"


@pytest.mark.parametrize(
    ("line", "pointer"),
    [
        ('path = "//attacker.example/share/x.db"', "profile[0].path"),
        ('path = "\\\\\\\\attacker.example\\\\share\\\\x.db"', "profile[0].path"),
        ('path = "\\\\\\\\?\\\\C:\\\\x.db"', "profile[0].path"),
        ('path = "C:x.db"', "profile[0].path"),
        ('path = "/net/attacker.example/x.db"', "profile[0].path"),
        ('path = "a.db"\npassword = "file://attacker.example/s/x"', "profile[0].password"),
    ],
)
def test_network_paths_are_refused_before_any_file_system_access(
    env: Env, monkeypatch: pytest.MonkeyPatch, line: str, pointer: str
) -> None:
    """T-2: the page's automatic GET /api/project never resolves a value that names another host."""
    env.write(f'version = 1\n[[profile]]\nname = "p"\nengine = "sqlite"\nenvironment = "dev"\n{line}\n')
    touched: list[str] = []
    resolve, lstat = Path.resolve, os.lstat

    def watched_resolve(self: Path, strict: bool = False) -> Path:
        touched.append(str(self))
        return resolve(self, strict=strict)

    def watched_lstat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        touched.append(str(path))
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", watched_resolve)
    monkeypatch.setattr(os, "lstat", watched_lstat)
    doc = env.project_doc()
    assert doc["state"] == "invalid"
    assert {"pointer": pointer, "code": "config.network_path", "severity": "error"} in doc["issues"]
    assert not any("attacker" in path or "x.db" in path for path in touched), touched


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path resolution")
def test_windows_never_resolves_a_unc_path_from_the_project_file(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-2 on Windows: nt._getfinalpathname (which opens the path, an SMB connection for UNC) is never called."""
    import nt

    called: list[str] = []
    original = getattr(nt, "_getfinalpathname")  # noqa: B009 (private, not in the stubs)

    def watched(path: str) -> str:
        called.append(path)
        return str(original(path))

    monkeypatch.setattr(nt, "_getfinalpathname", watched)
    for value in ("//attacker.example/share/x.db", "\\\\\\\\attacker.example\\\\share\\\\x.db"):
        env.write(f'version = 1\n[[profile]]\nname = "p"\nengine = "sqlite"\nenvironment = "dev"\npath = "{value}"\n')
        assert env.project_doc()["state"] == "invalid"
    assert not any("attacker" in path for path in called), called


def test_deeply_nested_project_file_is_invalid_and_user_profiles_still_show(env: Env) -> None:
    """T-3 (probe P-6): 1.8 KB of nested arrays is invalid, not a 500, and the user's profiles still load."""
    user = env.config_dir / "profiles.toml"
    user.write_text('version = 1\n[[profile]]\nname = "mine"\nengine = "sqlite"\npath = "m.db"\nenvironment = "dev"\n')
    user.chmod(0o600)
    env.write(b"version = 1\nx = " + b"[" * 900 + b"]" * 900 + b"\n")
    doc = env.project_doc()
    assert doc["state"] == "invalid"
    assert doc["issues"][0]["code"] in ("config.too_deep", "config.syntax")
    response = env.studio.client.get("/api/profiles", headers=env.headers)
    assert response.status_code == 200
    assert [p["name"] for p in response.json()["profiles"]] == ["mine"]
    assert response.json()["project_state"] == "invalid"


@pytest.mark.parametrize(
    "review",
    [
        '{"connections": 5, "hosts": [], "files": [], "env": [], "unknown_keys": []}',
        '{"connections": [{"name": ["unhashable"]}], "hosts": [], "files": [], "env": [], "unknown_keys": []}',
        '{"connections": [], "hosts": 7, "files": [], "env": [], "unknown_keys": []}',
        '{"connections": [], "hosts": [], "files": [{"path": 1}], "env": [], "unknown_keys": []}',
        '{"connections": [], "hosts": [[]], "files": [], "env": [], "unknown_keys": []}',
        "{}",
        '{"connections": ' + "[" * 100_000 + "]" * 100_000 + "}",
    ],
)
def test_a_trust_record_with_a_damaged_review_means_untrusted(env: Env, review: str) -> None:
    """T-3 (probe P-5): a record of the right owner and mode, but a wrongly typed or deeply nested review."""
    digest = env.write(GOOD)
    record = f'{{"version": 1, "project": {json.dumps(str(env.project))}, "digest": "{digest}", "review": {review}}}'
    env.project_config.record.write_text(record, encoding="utf-8")
    env.project_config.record.chmod(0o600)
    assert env.project_doc()["state"] == "untrusted"
    response = env.studio.client.get("/api/profiles", headers=env.headers)
    assert response.status_code == 200 and response.json()["project_state"] == "untrusted"


def test_an_unexpected_error_while_loading_the_project_keeps_the_user_profiles(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-3: whatever goes wrong with the project file, /api/profiles still lists the user's own profiles."""
    user = env.config_dir / "profiles.toml"
    user.write_text('version = 1\n[[profile]]\nname = "mine"\nengine = "sqlite"\npath = "m.db"\nenvironment = "dev"\n')
    user.chmod(0o600)

    def broken() -> Any:
        raise TypeError(CANARY)

    monkeypatch.setattr(env.project_config, "load", broken)
    response = env.studio.client.get("/api/profiles", headers=env.headers)
    assert response.status_code == 200
    assert [p["name"] for p in response.json()["profiles"]] == ["mine"]
    doc = env.project_doc()
    assert doc["state"] == "invalid" and CANARY not in json.dumps(doc)
