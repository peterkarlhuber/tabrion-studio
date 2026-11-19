"""STU-CR-6 part 1: the profile export (G-S3 is not granted; TX-30, GS3-22, W-10, TB-148, N-5; STU-ADR-0007 section
12). ``POST /api/write/schema-designer/profile-export`` writes read profiles as a tabrion connection profile file.

1. **Kill switch (GS3-01).** With the ``schema_designer`` flag off (every flag off, or only another area's) the route
   answers ``405``, writes nothing and the export module is not loaded.
2. **No secret, by an allowlist (N-5, TX-30).** A canary secret of a write profile, of an admin profile and of a read
   profile (in the keyring, in an environment variable) is absent from the file, the answer, the audit log and the
   log; a write or admin profile cannot be named; the file holds only the allowlisted fields, ``mode = read_only``
   and an ``env:`` reference at most. A field Studio's profile might gain later is not exported (an allowlist, not a
   denylist), and the module never reads write profiles or resolves a secret (static check).
3. **Single host only (W-10, TB-148).** A profile with a host list is refused, never cut to one host.
4. **The file (write/folders.py).** A relative or unnormalized path, another ending, a folder others may write, a
   link on the way, one of Studio's own folders, an existing file or link: refused, nothing written or changed; the
   new file is ``0600`` and parses with ``tabrion.profilefile``; the typed name must match; Windows refused.
5. **Audit.** ``run.start`` and ``run.result`` with the profile names and the file name, never the folder.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, fields
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from tabrion.profilefile import load as load_profile_file

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.config.secrets import SERVICE, write_keyring_key
from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write import folders, profile_export
from tabrion_studio.write.audit import AuditLog
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.profile_export import EXPORTED_FIELDS, export_profiles, exported_profile
from tests.support.write import CANARY_SECRET, DATA_EDITOR, DATABASE_MANAGER, WriteEnv

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(sys.platform == "win32", reason="the export is refused on Windows (its own test)"),
]

EXPORT = "/api/write/schema-designer/profile-export"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "src" / "tabrion_studio" / "write" / "profile_export.py"
WRITE_USER = "canary_write_role"
ADMIN_USER = "canary_admin_role"
READ_SECRET = f"read-secret-canary-{CANARY_SECRET[-12:]}"


def _profiles_file(db: Path) -> str:
    """A user profile file with read profiles and, coupled to them, a write and an admin profile with secrets."""
    return f"""version = 1

[[profile]]
name = "parts"
engine = "sqlite"
path = "{db}"
environment = "dev"

[[profile]]
name = "orders"
engine = "postgresql"
host = "db.example.org"
port = 5433
database = "orders"
user = "reader"
password = "env:ORDERS_READ_PASSWORD"
sslmode = "verify-full"
environment = "test"
read_only_role = true
statement_timeout_ms = 5000

[[profile]]
name = "ledger"
engine = "postgresql"
host = "localhost"
database = "ledger"
user = "ledger_reader"
password = "keyring:"
environment = "test"

[[write_profile]]
name = "orders-write"
kind = "write"
read_profile = "orders"
user = "{WRITE_USER}"
password = "env:ORDERS_WRITE_PASSWORD"

[[write_profile]]
name = "ledger-admin"
kind = "admin"
read_profile = "ledger"
user = "{ADMIN_USER}"
password = "keyring:"
"""


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WriteEnv]:
    monkeypatch.setenv("ORDERS_WRITE_PASSWORD", CANARY_SECRET)
    monkeypatch.setenv("ORDERS_READ_PASSWORD", READ_SECRET)
    environment = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    try:
        environment.write_file(_profiles_file(environment.db))
        yield environment
    finally:
        environment.close()


def _out(tmp_path: Path) -> Path:
    folder = tmp_path / "exports"
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    return folder


def _export(env: WriteEnv, ids: list[str], path: Path, typed: str | None = None) -> Any:
    body = {"profiles": ids, "path": str(path), "typed": path.name if typed is None else typed}
    return env.send("POST", EXPORT, body)


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


def _issues(response: Any) -> list[tuple[str, str]]:
    return [(issue["pointer"], issue["code"]) for issue in response.json().get("issues", [])]


def _records(env: WriteEnv) -> list[dict[str, Any]]:
    answer = env.send("GET", "/api/write/audit?limit=1000")
    assert answer.status_code == 200, answer.text
    records: list[dict[str, Any]] = answer.json()["records"]
    return sorted(records, key=lambda record: record["seq"])


# --- 1. the kill switch --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "areas", [WriteAreas(), DATA_EDITOR, DATABASE_MANAGER], ids=["every_flag_off", "data_editor", "database_manager"]
)
def test_flag_off_the_export_is_405_and_writes_nothing(tmp_path: Path, static_dir: Path, areas: WriteAreas) -> None:
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        target = _out(tmp_path) / "parts.tabrion-profile.toml"
        response = _export(env, ["user:parts"], target)
        assert _code(response) == (405, "studio.method_not_allowed")
        assert not target.exists()
    finally:
        env.close()


@pytest.mark.parametrize(
    ("areas", "loaded"),
    [
        ("WriteAreas()", False),
        ("WriteAreas(frozenset({Area.DATABASE_MANAGER}))", False),
        ("WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))", True),
    ],
)
def test_the_export_module_is_loaded_only_with_the_flag(tmp_path: Path, areas: str, loaded: bool) -> None:
    """GS3-01: with the flag off a request does not load the export (nor tabrion's profile file writer for it)."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), {areas})\n"
        "env.create_read()\n"
        f"r = env.send('POST', {EXPORT!r}, {{'profiles': ['user:parts'], 'path': '/nowhere/x.toml', 'typed': 'x'}})\n"
        "env.close()\n"
        "print(r.status_code)\n"
        "print('tabrion_studio.write.profile_export' in sys.modules)\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    status, imported = result.stdout.strip().splitlines()[-2:]
    assert imported == str(loaded)
    assert status == ("400" if loaded else "405")


# --- 2. no secret, by an allowlist ---------------------------------------------------------------------------------


def test_no_secret_and_no_write_or_admin_profile_reaches_the_export(
    env: WriteEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """TX-30, N-5: three read profiles exported; the write and admin profiles and every canary stay out."""
    # Canaries where Studio keeps secrets: the read profile's and the admin profile's keyring entries (the write
    # profile's is the environment variable ORDERS_WRITE_PASSWORD, the read profile's ORDERS_READ_PASSWORD).
    stored = env.send("PUT", "/api/profiles/user:ledger/secret", {"secret": READ_SECRET, "store": "keyring"})
    assert stored.status_code == 204, stored.text
    # The admin profile's entry, under the key Studio stores it by (its kind is not open with this area alone).
    user = env.config.user.profiles()
    admin = next(w for w in user.write_profiles if w.name == "ledger-admin")
    ledger = next(p for p in user.profiles if p.name == "ledger")
    env.fake.set_password(SERVICE, write_keyring_key(admin, admin.connection_profile(ledger)), CANARY_SECRET)
    assert sorted(env.fake.values.values()) == sorted([READ_SECRET, CANARY_SECRET])
    reads = env.fake.reads
    caplog.set_level(logging.DEBUG)
    target = _out(tmp_path) / "app.tabrion-profile.toml"
    response = _export(env, ["user:parts", "user:orders", "user:ledger"], target)
    assert response.status_code == 201, response.text
    assert response.json() == {
        "path": str(target),
        "file_name": target.name,
        "profiles": 3,
        "mode": "0600",
        "audit_pending": False,
    }
    text = target.read_text(encoding="utf-8")
    for secret in (CANARY_SECRET, READ_SECRET):
        assert secret not in text and secret not in response.text and secret not in caplog.text
    for absent in (WRITE_USER, ADMIN_USER, "orders-write", "ledger-admin", "read_write", '"admin"', "keyring", "file:"):
        assert absent not in text, absent
    assert os.stat(target).st_mode & 0o777 == 0o600
    parsed = load_profile_file(target)
    assert [p.name for p in parsed.profiles] == ["parts", "orders", "ledger"]
    assert {p.mode for p in parsed.profiles} == {"read_only"}
    orders = parsed.get("orders")
    assert (orders.host, orders.port, orders.database, orders.user, orders.sslmode) == (
        "db.example.org",
        5433,
        "orders",
        "reader",
        "verify-full",
    )
    assert (orders.environment, orders.read_only_role, orders.statement_timeout_ms) == ("test", True, 5000)
    # An env: reference names a variable and stays; a keyring entry of Studio becomes "prompt".
    assert str(orders.password) == "env:ORDERS_READ_PASSWORD"
    assert str(parsed.get("ledger").password) == "prompt"
    assert parsed.get("parts").password is None
    assert env.fake.reads == reads  # the export resolved no keyring entry
    for record in _records(env):
        assert CANARY_SECRET not in json.dumps(record) and READ_SECRET not in json.dumps(record)


@pytest.mark.parametrize(
    "profile_id", ["write:orders-write", "admin:ledger-admin", "orders-write", "user:orders-write"]
)
def test_a_write_or_admin_profile_cannot_be_named(env: WriteEnv, tmp_path: Path, profile_id: str) -> None:
    target = _out(tmp_path) / "x.tabrion-profile.toml"
    response = _export(env, [profile_id], target)
    assert _code(response) == (400, "studio.profile_export_invalid")
    assert _issues(response) in ([("export.profiles", "export.type")], [("export.profiles", "export.unknown")])
    assert not target.exists()


@dataclass(frozen=True)
class _GrownProfile(Profile):
    """Studio's profile with a field it might gain later, holding a secret: an allowlist does not export it."""

    api_token: str | None = None


def test_the_export_is_an_allowlist_not_a_copy() -> None:
    """N-5: only EXPORTED_FIELDS are read; a new field of Studio's profile (here with a canary) is never written."""
    grown = _GrownProfile(
        name="grown",
        engine="postgresql",
        environment="dev",
        host="localhost",
        database="d",
        user="u",
        password=SecretRef("file", "/home/u/secret"),
        api_token=CANARY_SECRET,
    )
    exported = exported_profile(grown)
    assert CANARY_SECRET not in repr(exported)
    assert str(exported.password) == "prompt"  # a file: reference names a local path: the consumer asks
    assert exported.mode == "read_only"
    # Every field of Studio's profile is either allowlisted or the password, which is mapped apart.
    studio_fields = {item.name for item in fields(Profile)}
    assert set(EXPORTED_FIELDS) <= studio_fields
    assert studio_fields - set(EXPORTED_FIELDS) == {"password"}


def test_the_module_never_reads_write_profiles_or_resolves_a_secret() -> None:
    """Static: no write profile, no secret store, no resolution is reached from the export module."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for forbidden in ("write_profiles", "secrets_store", "resolve", "resolve_secret", "resolve_entry_secret"):
        assert forbidden not in names, forbidden
    assert "secrets.token_hex" in MODULE.read_text(encoding="utf-8")  # the only use of `secrets`: the run id


# --- 3. single host only -------------------------------------------------------------------------------------------


@dataclass
class _Entry:
    id: str
    profile: Profile


def test_a_profile_with_several_hosts_is_refused_never_cut(tmp_path: Path) -> None:
    """W-10, TB-148: Studio's validator refuses a host list already; the export refuses it again on its own."""
    listed = Profile(name="ha", engine="postgresql", environment="dev", host="db1,db2", database="d", user="u")
    audit = AuditLog(None)
    target = _out(tmp_path) / "ha.tabrion-profile.toml"
    doc = {"profiles": ["user:ha"], "path": str(target), "typed": target.name}
    with pytest.raises(WriteError) as refused:
        export_profiles([_Entry("user:ha", listed)], audit, [], doc, area="schema_designer", action="x")
    assert refused.value.code == "studio.profile_export_multi_host"
    assert refused.value.issues == [("export.profiles", "export.host_list")]
    assert not target.exists()


# --- 4. the file ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("relative/x.tabrion-profile.toml", "not_absolute"),
        ("/home/u/../u/x.tabrion-profile.toml", "not_normalized"),
        ("/home/u/x.toml", "suffix"),
        ("/home/u/.tabrion-profile.toml", "suffix"),
        ("/home/u/x\u202e.tabrion-profile.toml", "control_character"),
        ("//server/share/x.tabrion-profile.toml", "network_path"),
        ("", "required"),
    ],
)
def test_a_path_that_is_not_valid_is_refused(env: WriteEnv, path: str, code: str) -> None:
    response = env.send("POST", EXPORT, {"profiles": ["user:parts"], "path": path, "typed": path.rsplit("/", 1)[-1]})
    assert _code(response) == (400, "studio.profile_export_invalid")
    assert _issues(response) == [("export.path", f"export.{code}")]


@pytest.mark.parametrize(
    ("ids", "code"),
    [
        ([], "required"),
        ("user:parts", "required"),
        (["user:parts", "user:parts"], "duplicate"),
        (["user:nope"], "unknown"),
        ([3], "type"),
        ([f"user:p{n}" for n in range(65)], "too_many"),
    ],
)
def test_a_profile_list_that_is_not_valid_is_refused(env: WriteEnv, tmp_path: Path, ids: Any, code: str) -> None:
    target = _out(tmp_path) / "x.tabrion-profile.toml"
    response = env.send("POST", EXPORT, {"profiles": ids, "path": str(target), "typed": target.name})
    assert _code(response) == (400, "studio.profile_export_invalid")
    assert _issues(response) == [("export.profiles", f"export.{code}")]
    assert not target.exists()


def test_the_typed_name_must_match(env: WriteEnv, tmp_path: Path) -> None:
    target = _out(tmp_path) / "x.tabrion-profile.toml"
    assert _code(_export(env, ["user:parts"], target, typed="y.tabrion-profile.toml")) == (
        400,
        "studio.profile_export_unconfirmed",
    )
    assert not target.exists()


def test_an_extra_key_is_refused(env: WriteEnv, tmp_path: Path) -> None:
    target = _out(tmp_path) / "x.tabrion-profile.toml"
    for key in ("mode", "secret", "write_profiles", "overwrite"):
        body = {"profiles": ["user:parts"], "path": str(target), "typed": target.name, key: True}
        assert _code(env.send("POST", EXPORT, body)) == (400, "access.bad_request")
    assert not target.exists()


def test_an_existing_file_or_link_is_never_overwritten(env: WriteEnv, tmp_path: Path) -> None:
    folder = _out(tmp_path)
    existing = folder / "old.tabrion-profile.toml"
    existing.write_text("keep me", encoding="utf-8")
    assert _code(_export(env, ["user:parts"], existing)) == (400, "studio.profile_export_exists")
    assert existing.read_text(encoding="utf-8") == "keep me"
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("other", encoding="utf-8")
    link = folder / "link.tabrion-profile.toml"
    link.symlink_to(elsewhere)
    dangling = folder / "dangling.tabrion-profile.toml"
    dangling.symlink_to(tmp_path / "missing.txt")
    assert _code(_export(env, ["user:parts"], link)) == (400, "studio.profile_export_exists")
    assert _code(_export(env, ["user:parts"], dangling)) == (400, "studio.profile_export_exists")
    assert elsewhere.read_text(encoding="utf-8") == "other"
    assert not (tmp_path / "missing.txt").exists()


def test_an_unsafe_folder_is_refused(env: WriteEnv, tmp_path: Path) -> None:
    open_folder = tmp_path / "shared"
    open_folder.mkdir()
    open_folder.chmod(0o777)
    assert _code(_export(env, ["user:parts"], open_folder / "x.tabrion-profile.toml")) == (
        400,
        "studio.profile_export_folder",
    )
    real = _out(tmp_path)
    via_link = tmp_path / "via-link"
    via_link.symlink_to(real, target_is_directory=True)
    assert _code(_export(env, ["user:parts"], via_link / "x.tabrion-profile.toml")) == (
        400,
        "studio.profile_export_folder",
    )
    assert _code(_export(env, ["user:parts"], tmp_path / "missing" / "x.tabrion-profile.toml")) == (
        400,
        "studio.profile_export_folder",
    )
    assert list(real.iterdir()) == []


def test_studios_own_folders_are_refused(env: WriteEnv) -> None:
    for folder in (env.config_dir, env.audit_dir, env.project / ".tabrion"):
        folder.chmod(0o700)
        response = _export(env, ["user:parts"], folder / "x.tabrion-profile.toml")
        assert _code(response) == (400, "studio.profile_export_reserved"), folder
        assert not (folder / "x.tabrion-profile.toml").exists()


def test_windows_is_refused(env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_export, "_PLATFORM_OK", False)
    target = _out(tmp_path) / "x.tabrion-profile.toml"
    assert _code(_export(env, ["user:parts"], target)) == (400, "studio.profile_export_platform")
    assert not target.exists()


# --- 5. audit ------------------------------------------------------------------------------------------------------


def test_the_export_is_audited_without_the_folder(env: WriteEnv, tmp_path: Path) -> None:
    target = _out(tmp_path) / "audited.tabrion-profile.toml"
    assert _export(env, ["user:parts", "user:orders"], target).status_code == 201
    runs = [r for r in _records(env) if r["event"] in ("run.start", "run.result")]
    assert [r["event"] for r in runs] == ["run.start", "run.result"]
    start, result = (r["fields"] for r in runs)
    assert start["area"] == "schema_designer" and start["action"] == "schema_designer.profile_export"
    assert start["objects"] == ["parts", "orders"] and start["target"] == target.name
    assert result["result"] == "committed" and result["run"] == start["run"]
    assert str(target.parent) not in json.dumps(runs)


def test_a_refused_file_is_audited_with_its_problem(env: WriteEnv, tmp_path: Path) -> None:
    target = _out(tmp_path) / "race.tabrion-profile.toml"
    real_create = folders.create_new_file

    def racing(descriptor: int, name: str, data: bytes) -> tuple[int, int]:
        # Someone creates the name between the check and the create: O_EXCL refuses it.
        os.close(os.open(name, os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=descriptor))
        return real_create(descriptor, name, data)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(profile_export, "create_new_file", racing)
        assert _code(_export(env, ["user:parts"], target)) == (400, "studio.profile_export_exists")
    assert target.read_bytes() == b""
    results = [r["fields"]["result"] for r in _records(env) if r["event"] == "run.result"]
    assert results == ["studio.profile_export_exists"]


def test_a_folder_swapped_after_the_check_is_refused(env: WriteEnv, tmp_path: Path) -> None:
    """The folder checked is the folder written: one replaced after the check (same path) gets no file."""
    target = _out(tmp_path) / "swap.tabrion-profile.toml"
    real_check = folders.check_folder

    def swapping(folder: str, reserved: Any) -> os.stat_result:
        checked = real_check(folder, reserved)
        os.rename(folder, folder + ".old")
        os.mkdir(folder, 0o700)
        return checked

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(profile_export, "check_folder", swapping)
        assert _code(_export(env, ["user:parts"], target)) == (400, "studio.profile_export_folder")
    assert os.listdir(target.parent) == [] and os.listdir(str(target.parent) + ".old") == []
    results = [r["fields"]["result"] for r in _records(env) if r["event"] == "run.result"]
    assert results == ["studio.profile_export_folder"]


def test_a_failed_write_leaves_no_file(env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that fails after the create removes the new file: no half-written export stays behind."""
    target = _out(tmp_path) / "full.tabrion-profile.toml"

    real_write = os.write

    def full(descriptor: int, data: Any) -> int:
        if bytes(data).startswith(b"version = 1"):  # the export's text only; the audit log still writes
            raise OSError(28, "No space left on device")
        return real_write(descriptor, data)

    monkeypatch.setattr(folders.os, "write", full)
    response = _export(env, ["user:parts"], target)
    monkeypatch.undo()
    assert _code(response) == (500, "studio.profile_export_failed")
    assert not target.exists()
    results = [r["fields"]["result"] for r in _records(env) if r["event"] == "run.result"]
    assert results == ["studio.profile_export_failed"]
