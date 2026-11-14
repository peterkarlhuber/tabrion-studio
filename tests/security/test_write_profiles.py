"""STU-XW-1: write and admin profiles only in the user's own store (E-X8, review K-1 of PR #109, TX-03, TX-04, N-5,
N-6), and the PROD lock of couplings (E-X7).

- A project file that defines a write or admin profile, a coupling, or names a user's write profile is refused
  (proof item 5); a user write profile couples only to a user read profile, never to a project profile.
- A PROD profile can never have a write or admin profile: refused in the file, on create, and a coupled profile can
  never become PROD.
- A write profile has its own role (PostgreSQL) and its own secret; its secret is never listed, exported or reported.
- Creating one needs the page's confirmation of the "no production write credentials" note (N-6).
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import logging
from pathlib import Path
from typing import Any

import pytest

from tabrion_studio.config.schema import WriteProfile, validate_profiles
from tabrion_studio.config.writer import checked_profiles, dump_profiles
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tests.support.write import CANARY_SECRET, DATA_EDITOR, DATABASE_MANAGER, EVERY_AREA, WriteEnv

pytestmark = pytest.mark.security

READ = {"name": "parts", "engine": "sqlite", "path": "parts.db", "environment": "dev"}
PG_READ = {
    "name": "pg",
    "engine": "postgresql",
    "host": "db.example.internal",
    "database": "measurements",
    "user": "reader",
    "environment": "dev",
}


def _issues(doc: dict[str, Any], owner: str = "user") -> set[tuple[str, str]]:
    return {(i.pointer, i.code) for i in validate_profiles(doc, owner=owner).issues if i.severity == "error"}  # type: ignore[arg-type]


# --- K-1: the project file ----------------------------------------------------------------------------------------


def test_a_project_file_with_a_write_profile_is_refused() -> None:
    doc = {"version": 1, "profile": [READ], "write_profile": [{"name": "w", "kind": "write", "read_profile": "parts"}]}
    result = validate_profiles(doc)  # the default owner is the project: the stricter one
    assert not result.ok
    assert ("write_profile", "config.write_profile_in_project") in _issues(doc, "project")
    assert result.write_profiles == []


@pytest.mark.parametrize(
    "key",
    ["mode", "kind", "couples_to", "read_profile", "write_profile", "admin_profile", "write_user", "write_password"],
)
@pytest.mark.parametrize("owner", ["project", "user"])
def test_a_profile_that_would_be_a_write_profile_or_a_coupling_is_refused(key: str, owner: str) -> None:
    """A ``[[profile]]`` that names a write profile to couple to, or makes itself one, is refused in either file."""
    doc = {"version": 1, "profile": [{**READ, key: "parts-write"}]}
    result = validate_profiles(doc, owner=owner)  # type: ignore[arg-type]
    assert result.profiles == [] and result.write_profiles == []
    assert (f"profile[0].{key}", "config.write_key_not_allowed") in _issues(doc, owner)


def test_a_project_file_naming_a_users_write_profile_is_refused_through_the_api(
    tmp_path: Path, static_dir: Path
) -> None:
    env = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        env.create_read()
        assert env.create_write().status_code == 201
        (env.project / ".tabrion" / "studio.toml").write_text(
            'version = 1\n[[profile]]\nname = "proj"\nengine = "sqlite"\npath = "p.db"\nenvironment = "dev"\n'
            'couples_to = "parts-write"\n\n[[write_profile]]\nname = "proj-write"\nkind = "admin"\n'
            'read_profile = "proj"\n',
            encoding="utf-8",
        )
        project = env.send("GET", "/api/project").json()
        assert project["state"] == "invalid"
        codes = {issue["code"] for issue in project["issues"]}
        assert {"config.write_profile_in_project", "config.write_key_not_allowed"} <= codes
        # Nothing of the project file reaches the write profiles; the user's stays as it was.
        listed = env.send("GET", "/api/write/profiles").json()["write_profiles"]
        assert [item["id"] for item in listed] == ["write:parts-write"]
        assert listed[0]["read_profile_id"] == "user:parts"
    finally:
        env.close()


def test_a_write_profile_couples_only_to_a_user_profile_never_a_project_profile(
    tmp_path: Path, static_dir: Path
) -> None:
    """The user's write profile names a profile that only the (trusted) project file has: not coupled, not usable."""
    env = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        (env.project / ".tabrion" / "studio.toml").write_text(
            'version = 1\n[[profile]]\nname = "proj"\nengine = "sqlite"\npath = "p.db"\nenvironment = "dev"\n',
            encoding="utf-8",
        )
        digest = env.send("GET", "/api/project").json()["digest"]
        assert env.send("POST", "/api/project/trust", {"digest": digest}).status_code == 204
        created = env.create_write(name="proj-write", read="proj")
        assert created.status_code == 400
        assert created.json()["code"] == "studio.write_profile_invalid"
        assert created.json()["issues"] == [{"pointer": "profile.read_profile", "code": "config.coupling_unknown"}]
        env.write_file('version = 1\n\n[[write_profile]]\nname = "proj-write"\nkind = "write"\nread_profile = "proj"\n')
        assert env.config.user.profiles().write_profiles == []
        assert ("write_profile[0].read_profile", "config.coupling_unknown") in {
            (i.pointer, i.code) for i in env.config.user.profiles().issues
        }
        # Unlocking a trusted project connection with any write profile is refused (K-1).
        (env.project / "p.db").write_bytes(env.db.read_bytes())
        (env.project / "p.db").chmod(0o600)
        connection_id = env.open("project:proj")
        refused = env.unlock(connection_id, "write:proj-write", "proj-write")
        assert refused.status_code == 400 and refused.json()["code"] == "studio.write_profile_unknown"
    finally:
        env.close()


# --- the user's file ----------------------------------------------------------------------------------------------


def test_a_valid_write_profile_is_read_and_written_back_exactly() -> None:
    doc = {
        "version": 1,
        "profile": [PG_READ],
        "write_profile": [
            {"name": "pg-write", "kind": "write", "read_profile": "pg", "user": "writer", "password": "keyring:"},
            {"name": "pg-admin", "kind": "admin", "read_profile": "pg", "user": "admin", "password": "env:ADMIN_PW"},
        ],
    }
    result = validate_profiles(doc, owner="user")
    assert result.ok and [w.name for w in result.write_profiles] == ["pg-write", "pg-admin"]
    data = checked_profiles(result.profiles, result.write_profiles)
    assert data == dump_profiles(result.profiles, result.write_profiles)
    assert b"[[write_profile]]" in data and b'read_profile = "pg"' in data
    # The connection profile is the read profile's target with the write role: no target of its own.
    target = result.write_profiles[0].connection_profile(result.profiles[0])
    assert (target.host, target.database, target.environment, target.user) == (
        "db.example.internal",
        "measurements",
        "dev",
        "writer",
    )


@pytest.mark.parametrize(
    ("write", "read", "issue"),
    [
        ({"read_profile": "pg", "user": "writer"}, {**PG_READ, "environment": "prod"}, "read_profile:coupling_prod"),
        ({"read_profile": "nope", "user": "writer"}, PG_READ, "read_profile:coupling_unknown"),
        ({"read_profile": "pg"}, PG_READ, "user:coupling_role_required"),
        ({"read_profile": "pg", "user": "reader"}, PG_READ, "user:coupling_same_role"),
        ({"read_profile": "parts", "user": "x"}, READ, "read_profile:not_for_engine"),
        ({"read_profile": "pg", "user": "w", "host": "evil.example"}, PG_READ, "host:unknown_key"),
        ({"read_profile": "pg", "user": "w", "environment": "dev"}, PG_READ, "environment:unknown_key"),
        ({"read_profile": "pg", "user": "w", "password": "plain-canary"}, PG_READ, "password:plain_password"),
        ({"read_profile": "pg", "user": "w", "kind": "superuser"}, PG_READ, "kind:choice"),
    ],
)
def test_a_coupling_that_is_not_allowed_is_refused_in_the_file(
    write: dict[str, Any], read: dict[str, Any], issue: str
) -> None:
    """E-X7 (no PROD), K-1 (a user profile of the same file), E-X8 (its own role), TX-04 (no target of its own)."""
    raw = {"name": "w", "kind": "write", **write}
    doc = {"version": 1, "profile": [read], "write_profile": [raw]}
    result = validate_profiles(doc, owner="user")
    pointer, code = issue.split(":")
    assert result.write_profiles == []
    assert (f"write_profile[0].{pointer}", f"config.{code}") in _issues(doc)
    assert "plain-canary" not in repr(result.issues)


def test_a_write_profile_cannot_share_a_name() -> None:
    doc = {
        "version": 1,
        "profile": [READ],
        "write_profile": [
            {"name": "parts", "kind": "write", "read_profile": "parts"},
            {"name": "w", "kind": "write", "read_profile": "parts"},
            {"name": "w", "kind": "admin", "read_profile": "parts"},
        ],
    }
    assert {("write_profile[0].name", "config.duplicate"), ("write_profile[2].name", "config.duplicate")} <= _issues(
        doc
    )


# --- the API ------------------------------------------------------------------------------------------------------


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[LogSetup]:
    setup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        yield setup
    finally:
        setup.close()
        forget_secrets()
        assert setup.file is not None
        assert CANARY_SECRET not in setup.file.read_text(encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, logs: LogSetup) -> Iterator[WriteEnv]:
    environment = WriteEnv(tmp_path, static_dir, EVERY_AREA)
    try:
        yield environment
    finally:
        environment.close()


def test_creating_a_write_profile_needs_the_production_note_confirmed(env: WriteEnv) -> None:
    """N-6: the server creates none unless the page confirms it showed the note at that moment."""
    env.create_read()
    for confirm in (None, False, "true", 1):
        body: dict[str, Any] = {
            "profile": {"name": "parts-write", "kind": "write", "read_profile": "parts"},
            "digest": env.user_digest(),
        }
        if confirm is not None:
            body["confirm_no_prod_credentials"] = confirm
        response = env.send("POST", "/api/write/profiles", body)
        assert (response.status_code, response.json()["code"]) == (400, "studio.write_warning_unconfirmed")
    assert env.config.user.profiles().write_profiles == []
    assert env.create_write().status_code == 201
    assert [w.name for w in env.config.user.profiles().write_profiles] == ["parts-write"]


def test_coupling_to_a_prod_profile_is_refused(env: WriteEnv) -> None:
    """E-X7 on create: a PROD profile never gets a write or admin profile; nothing is written."""
    env.create_read(environment="prod")
    before = (env.config_dir / "profiles.toml").read_bytes()
    for kind in ("write", "admin"):
        response = env.create_write(name=f"prod-{kind}", kind=kind)
        assert response.status_code == 400 and response.json()["code"] == "studio.write_profile_invalid"
        assert response.json()["issues"] == [{"pointer": "profile.read_profile", "code": "config.coupling_prod"}]
    assert (env.config_dir / "profiles.toml").read_bytes() == before


def test_a_coupled_profile_never_becomes_prod_and_is_not_removed(env: WriteEnv) -> None:
    env.create_read()
    assert env.create_write().status_code == 201
    to_prod = {"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "prod"}
    response = env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": to_prod, "digest": env.user_digest()})
    assert response.status_code == 422 and response.json()["code"] == "studio.profile_invalid"
    assert response.json()["issues"] == [{"pointer": "profile.environment", "code": "config.coupling_prod"}]
    removed = env.send("DELETE", "/api/profiles", {"id": "user:parts", "digest": env.user_digest()})
    assert (removed.status_code, removed.json()["code"]) == (409, "studio.profile_coupled")
    user = env.config.user.profiles()
    assert [p.environment for p in user.profiles] == ["dev"] and [w.name for w in user.write_profiles] == [
        "parts-write"
    ]


def test_saving_a_read_profile_keeps_the_write_profiles(env: WriteEnv) -> None:
    env.create_read()
    assert env.create_write().status_code == 201
    env.create_read(name="other")
    to_test = {"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "test"}
    assert (
        env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": to_test, "digest": env.user_digest()})
    ).status_code == 200
    assert [w.name for w in env.config.user.profiles().write_profiles] == ["parts-write"]
    assert env.config.user.profiles().issues == []


def test_a_write_profiles_secret_is_its_own_and_never_listed(env: WriteEnv) -> None:
    """N-5: the write secret is stored under its own key, never shown, and not in the read profile listing."""
    env.create_read(name="parts")
    pg = {
        "name": "pg",
        "engine": "postgresql",
        "host": "127.0.0.1",
        "database": "d",
        "user": "reader",
        "environment": "dev",
        "password": "keyring:",
    }
    assert env.send("POST", "/api/profiles", {"profile": pg, "digest": env.user_digest()}).status_code == 201
    created = env.create_write(name="pg-write", read="pg", user="writer", password="keyring:")
    assert created.status_code == 201, created.text
    stored = env.send("PUT", "/api/write/profiles/write:pg-write/secret", {"secret": CANARY_SECRET, "store": "keyring"})
    assert stored.status_code == 204
    keys = [key for (_, key) in env.fake.values]
    assert len(keys) == 1 and keys[0].startswith("write:write:pg-write:")
    listing = env.send("GET", "/api/write/profiles")
    assert listing.status_code == 200 and CANARY_SECRET not in listing.text
    item = listing.json()["write_profiles"][0]
    assert item["secret"] == {"source": "keyring", "has_secret": None, "confirmed": False, "foreign": False}
    assert item["password"] == {"source": "keyring", "target": ""} and item["environment"] == "dev"
    profiles = env.send("GET", "/api/profiles")
    assert CANARY_SECRET not in profiles.text and "pg-write" not in profiles.text
    assert {p["id"] for p in profiles.json()["profiles"]} == {"user:parts", "user:pg"}
    # The read profile's own secret entry is not the write profile's.
    assert env.send("DELETE", "/api/write/profiles/write:pg-write/secret", {}).status_code == 204
    assert env.fake.values == {}


def test_editing_and_removing_a_write_profile(env: WriteEnv) -> None:
    env.create_read()
    assert env.create_write().status_code == 201
    changed = env.send(
        "PUT",
        "/api/write/profiles",
        {
            "id": "write:parts-write",
            "profile": {"name": "parts-write", "kind": "admin", "read_profile": "parts"},
            "digest": env.user_digest(),
        },
    )
    assert changed.status_code == 200, changed.text
    assert env.config.user.profiles().write_profiles == [WriteProfile("parts-write", "admin", "parts")]
    renamed = env.send(
        "PUT",
        "/api/write/profiles",
        {
            "id": "write:parts-write",
            "profile": {"name": "other", "kind": "admin", "read_profile": "parts"},
            "digest": env.user_digest(),
        },
    )
    assert renamed.status_code == 400 and renamed.json()["code"] == "studio.write_profile_invalid"
    removed = env.send("DELETE", "/api/write/profiles", {"id": "write:parts-write", "digest": env.user_digest()})
    assert removed.status_code == 200
    assert env.config.user.profiles().write_profiles == []
    missing = env.send("DELETE", "/api/write/profiles", {"id": "write:parts-write", "digest": env.user_digest()})
    assert (missing.status_code, missing.json()["code"]) == (400, "studio.write_profile_unknown")


def test_an_admin_profile_needs_the_database_manager_flag(tmp_path: Path, static_dir: Path) -> None:
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        env.create_read()
        assert env.create_write(name="parts-admin", kind="admin").status_code == 201
        refused = env.create_write(name="parts-write", kind="write")
        assert (refused.status_code, refused.json()["code"]) == (405, "studio.method_not_allowed")
        assert [w["kind"] for w in env.send("GET", "/api/write/profiles").json()["write_profiles"]] == ["admin"]
    finally:
        env.close()


def test_write_profiles_are_not_in_the_diagnosis_report_or_the_profile_listing(env: WriteEnv) -> None:
    """N-5 (STU-XW-1 part): no write profile and no write secret in the support output Studio has today: the
    profile listing and the diagnosis report of the read connection."""
    env.create_read()
    assert env.create_write().status_code == 201
    env.config.secrets.session.set("write:write:parts-write:x", CANARY_SECRET)
    from tabrion_studio.services.diagnosis_report import DiagnosisReports
    from tabrion_studio.services.diagnostics import Diagnostics

    connection_id = env.open()
    reports = DiagnosisReports(env.registry, Diagnostics(env.registry))
    for report_format in ("markdown", "json"):
        body = reports.build(connection_id, report_format).body.decode("utf-8")
        assert CANARY_SECRET not in body and "parts-write" not in body and "write_profile" not in body


# --- L-1 of PR #111: write profiles only from the command line's or the platform's folder ------------------------


def test_write_profiles_of_a_folder_a_variable_chose_are_not_used(env: WriteEnv) -> None:
    """With ``write_profiles_allowed=False`` (the server's choice when an area is on and an environment variable chose
    the folder; ``test_write_flags.py`` proves that wiring with a real process) the file's write profiles are not
    listed, not unlockable and none can be created; the read profiles work as before."""
    env.create_read()
    assert env.create_write().status_code == 201
    env.config.user.write_profiles_allowed = False
    listing = env.send("GET", "/api/write/profiles").json()
    assert listing["write_profiles"] == []
    assert listing["write_profiles_allowed"] is False and listing["write_profiles_ignored"] is True
    created = env.create_write(name="other-write")
    assert (created.status_code, created.json()["code"]) == (409, "studio.write_profiles_ignored")
    connection_id = env.open()
    refused = env.unlock(connection_id)
    assert (refused.status_code, refused.json()["code"]) == (400, "studio.write_profile_unknown")
    for method in ("PUT", "DELETE"):
        response = env.send(
            method,
            "/api/write/profiles",
            {
                "id": "write:parts-write",
                "profile": {"name": "parts-write", "kind": "write", "read_profile": "parts"},
                "digest": env.user_digest(),
            },
        )
        assert (response.status_code, response.json()["code"]) == (400, "studio.write_profile_unknown")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    assert [p.name for p in env.config.user.profiles().profiles] == ["parts"]


def test_ignored_write_profiles_stay_in_the_file_and_keep_their_couplings(env: WriteEnv) -> None:
    """Not used is not dropped: saving a read profile keeps them, and a coupled read profile still never becomes
    PROD and is not removed (the file stays valid for a start from the command line's folder)."""
    env.create_read()
    assert env.create_write().status_code == 201
    env.config.user.write_profiles_allowed = False
    env.create_read(name="other")
    to_test = {"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "test"}
    saved = env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": to_test, "digest": env.user_digest()})
    assert saved.status_code == 200
    assert [w.name for w in env.config.user.profiles().stored_write_profiles] == ["parts-write"]
    to_prod = {**to_test, "environment": "prod"}
    response = env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": to_prod, "digest": env.user_digest()})
    assert response.status_code == 422 and response.json()["issues"][0]["code"] == "config.coupling_prod"
    removed = env.send("DELETE", "/api/profiles", {"id": "user:parts", "digest": env.user_digest()})
    assert (removed.status_code, removed.json()["code"]) == (409, "studio.profile_coupled")
    env.config.user.write_profiles_allowed = True
    assert [w.name for w in env.config.user.profiles().write_profiles] == ["parts-write"]
    assert env.config.user.profiles().issues == []
