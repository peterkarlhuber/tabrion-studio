"""STU-XDM-5: profile pairs, import and export of the Database Manager (G-S4 is not granted; GS4-01, GS4-21, GS4-22;
TX-45; STU-ADR-0008 section 8; W-10, TB-148, K-1, N-5, I-109 j).

1. **Kill switch (GS4-01).** With the ``database_manager`` flag off (every flag off, or only another area's) the four
   routes answer ``405``, read and write nothing, and neither the area's module nor the shared export is loaded.
2. **Export (N-5, W-10).** The shared export with the area ``database_manager``: a preview that writes nothing and
   names the coupled write and admin profiles it leaves out, the typed name and the preview's state; canary secrets of
   a read, a write and an admin profile absent from the file, the answers, the audit log and the log.
3. **Import (K-1, W-10).** Read profiles only, built from the export's allowlist; no reference of the file is kept,
   ``env:`` included (review M-1 of #155): every one becomes Studio's empty keyring entry; PROD stays PROD; a host
   list, a plain password, an unknown key, a write or admin mode, a missing environment, a database a PROD profile
   names: refused, never cut or relabeled.
4. **The file (write/folders.py).** A link, a linked folder, a folder others may write, Studio's own folders, a hard
   link, a file others may write, a pipe, a folder, a missing, malformed, non-UTF-8 or oversized file: refused.
5. **Name clashes fail closed.** An existing read, write or admin profile's name is never taken; a new name is chosen
   explicitly or the profile is left out.
6. **Preview, typed name, audit (GS4-22).** Bound to the preview's state and the user file's digest; ``run.start`` and
   ``run.result`` with the area, the action, the names and the file name, never a folder, a path or a reference.
7. **Pairs (I-109 j).** An imported PostgreSQL profile coupled to an admin profile refuses the unlock until the pair
   is confirmed, before any secret is read.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from tabrion.profilefile import load as load_profile_file

from tabrion_studio.config.schema import Profile, target_fingerprint
from tabrion_studio.config.secrets import SERVICE, SecretError, write_keyring_key
from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.services import connections as connections_module
from tabrion_studio.write.audit import AuditError
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.database_manager import profiles as dbm_profiles
from tabrion_studio.write.profile_export import EXPORTED_FIELDS
from tests.support.write import CANARY_SECRET, DATA_EDITOR, DATABASE_MANAGER, WriteEnv

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(sys.platform == "win32", reason="import and export are refused on Windows (their own test)"),
]

ROOT = Path(__file__).resolve().parents[2]
EXPORT_PREVIEW = "/api/write/database-manager/profiles/export/preview"
EXPORT = "/api/write/database-manager/profiles/export"
IMPORT_PREVIEW = "/api/write/database-manager/profiles/import/preview"
IMPORT = "/api/write/database-manager/profiles/import"
ROUTES = (EXPORT_PREVIEW, EXPORT, IMPORT_PREVIEW, IMPORT)
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
READ_SECRET = f"read-secret-canary-{CANARY_SECRET[-12:]}"
ENV_NAME = "XDM5_IMPORTED_PASSWORD"
WRITE_USER = "canary_write_role"
ADMIN_USER = "canary_admin_role"


def _store(db: Path) -> str:
    """The user's store: read profiles and, coupled to them, a write and an admin profile with secrets."""
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

[[profile]]
name = "ledger"
engine = "postgresql"
host = "localhost"
database = "ledger"
user = "ledger_reader"
password = "keyring:"
environment = "test"

[[profile]]
name = "books-prod"
engine = "postgresql"
host = "127.0.0.1"
database = "books"
user = "books_reader"
environment = "prod"

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


IMPORTED = f"""version = 1

[[profile]]
name = "local-copy"
engine = "sqlite"
path = "data/copy.db"
environment = "dev"

[[profile]]
name = "warehouse"
engine = "postgresql"
host = "localhost"
port = 5434
database = "warehouse"
user = "wh_reader"
password = "env:{ENV_NAME}"
environment = "test"
read_only_role = true
statement_timeout_ms = 4000
application_name = "nodestro"
description = "copied from another tool"
allow = ["public.items"]

[[profile]]
name = "archive-prod"
engine = "postgresql"
host = "localhost"
port = 5435
database = "archive"
user = "arch_reader"
password = "keyring:svc/user"
environment = "prod"

[[profile]]
name = "stats"
engine = "postgresql"
host = "localhost"
port = 5436
database = "stats"
password = "file:/home/someone/secret.txt"
environment = "dev"

[[profile]]
name = "asks"
engine = "postgresql"
host = "localhost"
port = 5437
database = "asks"
password = "prompt"
environment = "test"
"""


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WriteEnv]:
    monkeypatch.setenv("ORDERS_WRITE_PASSWORD", CANARY_SECRET)
    monkeypatch.setenv("ORDERS_READ_PASSWORD", READ_SECRET)
    monkeypatch.setenv(ENV_NAME, CANARY_SECRET)
    environment = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        environment.write_file(_store(environment.db))
        yield environment
    finally:
        environment.close()


def _folder(tmp_path: Path, name: str = "exchange") -> Path:
    folder = tmp_path / name
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    return folder


def _file(folder: Path, text: str | bytes = IMPORTED, name: str = "in.tabrion-profile.toml") -> Path:
    path = folder / name
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)
    path.chmod(0o600)
    return path


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


def _issues(response: Any) -> list[tuple[str, str]]:
    return [(issue["pointer"], issue["code"]) for issue in response.json().get("issues", [])]


def _records(env: WriteEnv) -> list[dict[str, Any]]:
    answer = env.send("GET", "/api/write/audit?limit=1000")
    assert answer.status_code == 200, answer.text
    records: list[dict[str, Any]] = answer.json()["records"]
    return sorted(records, key=lambda record: record["seq"])


def _runs(env: WriteEnv) -> list[dict[str, Any]]:
    return [r for r in _records(env) if r["event"] in ("run.start", "run.result")]


def _preview(env: WriteEnv, path: Path) -> Any:
    return env.send("POST", IMPORT_PREVIEW, {"path": str(path)})


def _import(
    env: WriteEnv,
    path: Path,
    chosen: list[dict[str, str]],
    *,
    preview: dict[str, Any] | None = None,
    typed: str | None = None,
    **extra: Any,
) -> Any:
    if preview is None:
        answer = _preview(env, path)
        assert answer.status_code == 200, answer.text
        preview = answer.json()
    body = {
        "path": str(path),
        "typed": path.name if typed is None else typed,
        "state": preview["state"],
        "profiles": chosen,
        "digest": preview["user_file"]["digest"],
        # The store of these tests is written by hand: confirm rewriting it (as the page asks the user to).
        "overwrite": True,
        **extra,
    }
    return env.send("POST", IMPORT, body)


def _user_profiles(env: WriteEnv) -> dict[str, Profile]:
    return {profile.name: profile for profile in env.config.user.profiles().profiles}


def _store_bytes(env: WriteEnv) -> bytes:
    return (env.config_dir / "profiles.toml").read_bytes()


# --- 1. the kill switch ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "areas",
    [WriteAreas(), DATA_EDITOR, SCHEMA_DESIGNER],
    ids=["every_flag_off", "data_editor", "schema_designer"],
)
def test_flag_off_every_profile_route_is_405_and_changes_nothing(
    tmp_path: Path, static_dir: Path, areas: WriteAreas
) -> None:
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.write_file(_store(env.db))
        folder = _folder(tmp_path)
        source = _file(folder)
        target = folder / "out.tabrion-profile.toml"
        before = _store_bytes(env)
        for path, body in (
            (EXPORT_PREVIEW, {"profiles": ["user:parts"], "path": str(target)}),
            (EXPORT, {"profiles": ["user:parts"], "path": str(target), "typed": target.name, "state": "0" * 64}),
            (IMPORT_PREVIEW, {"path": str(source)}),
            (IMPORT, {"path": str(source), "typed": source.name, "state": "0" * 64, "profiles": [{"name": "asks"}]}),
        ):
            response = env.send("POST", path, body)
            assert _code(response) == (405, "studio.method_not_allowed"), path
            assert "warehouse" not in response.text and CANARY_SECRET not in response.text
        assert not target.exists()
        assert _store_bytes(env) == before
    finally:
        env.close()


@pytest.mark.parametrize(
    ("areas", "loaded"),
    [
        ("WriteAreas()", False),
        ("WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))", False),
        ("WriteAreas(frozenset({Area.DATABASE_MANAGER}))", True),
    ],
)
def test_the_profiles_module_is_loaded_only_with_the_flag(tmp_path: Path, areas: str, loaded: bool) -> None:
    """GS4-01: with the flag off a request loads neither the area's module, its package nor the shared export."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), {areas})\n"
        "env.create_read()\n"
        "codes = []\n"
        f"for p in {list(ROUTES)!r}:\n"
        "    codes.append(env.send('POST', p, {'path': '/nowhere/x.toml'}).status_code)\n"
        "env.close()\n"
        "print(sorted(set(codes)))\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write')))\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    codes_line, modules_line = result.stdout.strip().splitlines()[-2:]
    modules = json.loads(modules_line.replace("'", '"'))
    for module in (
        "tabrion_studio.write.database_manager",
        "tabrion_studio.write.database_manager.profiles",
        "tabrion_studio.write.profile_export",
    ):
        assert (module in modules) is loaded, module
    assert json.loads(codes_line) == ([400] if loaded else [405])
    if "DATA_EDITOR" in areas:
        assert "tabrion_studio.write.unlock" in modules  # the write package is loaded: the check is not vacuous


# --- 2. export ------------------------------------------------------------------------------------------------------


def _export_preview(env: WriteEnv, ids: list[str], path: Path) -> Any:
    return env.send("POST", EXPORT_PREVIEW, {"profiles": ids, "path": str(path)})


def _export(env: WriteEnv, ids: list[str], path: Path, *, typed: str | None = None, state: str | None = None) -> Any:
    if state is None:
        preview = _export_preview(env, ids, path)
        assert preview.status_code == 200, preview.text
        state = preview.json()["state"]
    body = {"profiles": ids, "path": str(path), "typed": path.name if typed is None else typed, "state": state}
    return env.send("POST", EXPORT, body)


def test_the_export_preview_writes_nothing_and_names_the_pairs_left_out(env: WriteEnv, tmp_path: Path) -> None:
    target = _folder(tmp_path) / "app.tabrion-profile.toml"
    preview = _export_preview(env, ["user:parts", "user:orders", "user:ledger"], target)
    assert preview.status_code == 200, preview.text
    doc = preview.json()
    assert not target.exists()
    assert doc["file_name"] == target.name and len(doc["state"]) == 64
    assert [(p["id"], p["password"], p["mode"]) for p in doc["profiles"]] == [
        ("user:parts", None, "read_only"),
        ("user:orders", "env", "read_only"),
        ("user:ledger", "prompt", "read_only"),
    ]
    # The pairs: the write and admin profiles coupled to the chosen read profiles are named and never written.
    assert doc["left_out"] == [
        {"name": "orders-write", "kind": "write", "read_profile": "orders"},
        {"name": "ledger-admin", "kind": "admin", "read_profile": "ledger"},
    ]
    for absent in (CANARY_SECRET, READ_SECRET, WRITE_USER, ADMIN_USER, "ORDERS_READ_PASSWORD"):
        assert absent not in preview.text
    assert _runs(env) == []  # a preview is not a run


def test_the_export_writes_no_secret_of_any_pair(
    env: WriteEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """N-5: canaries of a read, a write and an admin profile are absent from the file, the answers, audit and log."""
    stored = env.send("PUT", "/api/profiles/user:ledger/secret", {"secret": READ_SECRET, "store": "keyring"})
    assert stored.status_code == 204, stored.text
    user = env.config.user.profiles()
    admin = next(w for w in user.stored_write_profiles if w.name == "ledger-admin")
    ledger = next(p for p in user.profiles if p.name == "ledger")
    env.fake.set_password(SERVICE, write_keyring_key(admin, admin.connection_profile(ledger)), CANARY_SECRET)
    reads = env.fake.reads
    caplog.set_level(logging.DEBUG)
    target = _folder(tmp_path) / "app.tabrion-profile.toml"
    response = _export(env, ["user:parts", "user:orders", "user:ledger"], target)
    assert response.status_code == 201, response.text
    assert response.json()["file_name"] == target.name and response.json()["profiles"] == 3
    text = target.read_text(encoding="utf-8")
    for secret in (CANARY_SECRET, READ_SECRET):
        assert secret not in text and secret not in response.text and secret not in caplog.text
    for absent in (WRITE_USER, ADMIN_USER, "orders-write", "ledger-admin", "read_write", '"admin"', "keyring"):
        assert absent not in text, absent
    assert os.stat(target).st_mode & 0o777 == 0o600
    parsed = load_profile_file(target)
    assert {p.mode for p in parsed.profiles} == {"read_only"}
    assert str(parsed.get("orders").password) == "env:ORDERS_READ_PASSWORD"
    assert str(parsed.get("ledger").password) == "prompt"
    assert env.fake.reads == reads  # nothing resolved
    runs = _runs(env)
    assert [r["event"] for r in runs] == ["run.start", "run.result"]
    start, result = (r["fields"] for r in runs)
    assert (start["area"], start["action"]) == ("database_manager", "dbm.profile_export")
    assert start["objects"] == ["parts", "orders", "ledger"] and start["target"] == target.name
    assert result["result"] == "committed"
    dumped = json.dumps(runs)
    for absent in (str(target.parent), CANARY_SECRET, READ_SECRET, "ORDERS_READ_PASSWORD", "env:"):
        assert absent not in dumped


def test_the_export_is_bound_to_its_preview_and_the_typed_name(env: WriteEnv, tmp_path: Path) -> None:
    target = _folder(tmp_path) / "bound.tabrion-profile.toml"
    state = _export_preview(env, ["user:parts"], target).json()["state"]
    assert _code(_export(env, ["user:parts"], target, typed="other.tabrion-profile.toml", state=state)) == (
        400,
        "studio.profile_export_unconfirmed",
    )
    # Another profile list than the preview's, or no preview at all: refused.
    assert _code(_export(env, ["user:parts", "user:orders"], target, state=state)) == (
        400,
        "studio.profile_export_changed",
    )
    assert _code(_export(env, ["user:parts"], target, state="")) == (400, "studio.profile_export_changed")
    # The profile changed after the preview (its file now says another path).
    other = tmp_path / "other.db"
    other.write_bytes(env.db.read_bytes())
    env.write_file(_store(other))
    assert _code(_export(env, ["user:parts"], target, state=state)) == (400, "studio.profile_export_changed")
    assert not target.exists()
    assert _runs(env) == []


def test_the_export_preview_refuses_what_the_export_refuses(env: WriteEnv, tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    existing = _file(folder, "x", name="taken.tabrion-profile.toml")
    assert _code(_export_preview(env, ["user:parts"], existing)) == (400, "studio.profile_export_exists")
    assert _code(_export_preview(env, ["write:orders-write"], folder / "w.tabrion-profile.toml")) == (
        400,
        "studio.profile_export_invalid",
    )
    assert _code(_export_preview(env, ["user:parts"], env.config_dir / "c.tabrion-profile.toml")) == (
        400,
        "studio.profile_export_reserved",
    )
    assert existing.read_text() == "x"


@dataclass
class _Entry:
    id: str
    profile: Profile


def test_a_multi_host_profile_is_never_exported_or_cut(tmp_path: Path) -> None:
    """W-10, TB-148: the shared preview refuses a host list as the export does (the validator refuses it already)."""
    from tabrion_studio.write.profile_export import preview_export

    listed = Profile(name="ha", engine="postgresql", environment="dev", host="db1,db2", database="d", user="u")
    target = _folder(tmp_path) / "ha.tabrion-profile.toml"
    with pytest.raises(WriteError) as refused:
        preview_export([_Entry("user:ha", listed)], [], {"profiles": ["user:ha"], "path": str(target)})
    assert refused.value.code == "studio.profile_export_multi_host"
    assert not target.exists()


# --- 3. import: what is imported ------------------------------------------------------------------------------------


def test_the_import_preview_changes_nothing_and_shows_read_profiles_only(env: WriteEnv, tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    source = _file(folder)
    before = _store_bytes(env)
    reads = env.fake.reads
    preview = _preview(env, source)
    assert preview.status_code == 200, preview.text
    doc = preview.json()
    assert _store_bytes(env) == before and env.fake.reads == reads and _runs(env) == []
    assert doc["file_name"] == source.name and len(doc["state"]) == 64
    profiles = {p["name"]: p for p in doc["profiles"]}
    assert list(profiles) == ["local-copy", "warehouse", "archive-prod", "stats", "asks"]
    assert all(p["importable"] for p in profiles.values()), profiles
    # A relative SQLite path is relative to the file's folder (the format's rule), stored as an absolute path.
    assert profiles["local-copy"]["path"] == str(folder / "data" / "copy.db")
    # No reference of the file is kept, env: included (review M-1 of #155): each is an empty keyring entry.
    for name in ("warehouse", "archive-prod", "stats", "asks"):
        assert profiles[name]["password"] == {"source": "keyring", "target": ""}, name
    assert profiles["archive-prod"]["environment"] == "prod"
    assert profiles["warehouse"]["not_imported"] == ["allow", "application_name", "description"]
    assert CANARY_SECRET not in preview.text and "/home/someone" not in preview.text and ENV_NAME not in preview.text


def test_imported_profiles_are_read_profiles_without_a_secret(
    env: WriteEnv, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """K-1, N-5: the import adds read profiles only; no secret is resolved, copied or stored; PROD stays PROD."""
    env.fake.set_password("svc", "user", CANARY_SECRET)  # what the file's keyring:svc/user names: never read
    reads = env.fake.reads
    caplog.set_level(logging.DEBUG)
    source = _file(_folder(tmp_path))
    chosen = [{"name": n} for n in ("local-copy", "warehouse", "archive-prod", "stats", "asks")]
    response = _import(env, source, chosen)
    assert response.status_code == 201, response.text
    assert response.json()["imported"] == ["local-copy", "warehouse", "archive-prod", "stats", "asks"]
    assert env.fake.reads == reads
    # Nothing stored: the only new keyring entry is the audit log's anchor (G-S2 3A), never a profile's secret.
    assert [key for key in env.fake.values if not key[1].startswith("audit-anchor:")] == [("svc", "user")]
    user = env.config.user.profiles()
    assert [w.name for w in user.stored_write_profiles] == ["orders-write", "ledger-admin"]  # no pair added
    stored = _user_profiles(env)
    assert stored["archive-prod"].environment == "prod"
    warehouse = stored["warehouse"]
    assert (warehouse.host, warehouse.port, warehouse.database, warehouse.user) == (
        "localhost",
        5434,
        "warehouse",
        "wh_reader",
    )
    assert (warehouse.environment, warehouse.read_only_role, warehouse.statement_timeout_ms) == ("test", True, 4000)
    for name in ("warehouse", "archive-prod", "stats", "asks"):
        ref = stored[name].password
        assert ref is not None and ref.to_dict() == {"source": "keyring", "target": ""}, name
    assert stored["local-copy"].password is None
    text = _store_bytes(env).decode("utf-8")
    for absent in (
        CANARY_SECRET,
        ENV_NAME,
        "svc/user",
        "/home/someone",
        "nodestro",
        "public.items",
        "prompt",
        "\nmode =",
    ):
        assert absent not in text, absent
    for secret in (CANARY_SECRET,):
        assert secret not in response.text and secret not in caplog.text
    # Each imported profile has only the allowlisted fields (and the password reference).
    for profile in (stored[name] for name in ("local-copy", "warehouse")):
        assert set(profile.to_dict()) <= {*EXPORTED_FIELDS, "password"}


class _Watched(dict[str, str]):
    """The process environment as Studio's secret store sees it, recording every variable name it is asked for."""

    def __init__(self, values: dict[str, str]) -> None:
        super().__init__(values)
        self.asked: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.asked.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        self.asked.append(key)
        return super().__getitem__(key)


def test_an_imported_env_reference_to_a_foreign_host_is_never_kept_or_resolved(
    env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review M-1 of #155: a file names a foreign server and an ``env:`` reference to a variable of the user; the
    imported profile keeps no reference, so the variable's value is never read or sent to that server."""
    variable = "XDM5_FOREIGN_CANARY"
    monkeypatch.setenv(variable, CANARY_SECRET)
    watched = _Watched(dict(os.environ))
    monkeypatch.setattr(env.config.secrets, "_environ", watched)
    caplog.set_level(logging.DEBUG)
    text = f"""version = 1

[[profile]]
name = "foreign"
engine = "postgresql"
host = "db.attacker.example"
database = "app"
user = "reader"
password = "env:{variable}"
sslmode = "verify-full"
environment = "test"
"""
    source = _file(_folder(tmp_path), text)
    preview = _preview(env, source)
    assert preview.status_code == 200, preview.text
    item = preview.json()["profiles"][0]
    assert item["importable"] is True and item["password"] == {"source": "keyring", "target": ""}
    response = _import(env, source, [{"name": "foreign"}])
    assert response.status_code == 201, response.text
    stored = _user_profiles(env)["foreign"]
    assert stored.host == "db.attacker.example"
    assert stored.password is not None and stored.password.to_dict() == {"source": "keyring", "target": ""}
    # What a test or a connect resolves: Studio's empty entry, never the variable.
    entry = next(e for e in env.config.entries() if e.profile.name == "foreign")
    with pytest.raises(SecretError):
        env.config.secrets.resolve(entry.secret)
    listed = env.send("GET", "/api/profiles")
    assert listed.status_code == 200
    assert variable not in watched.asked
    store = _store_bytes(env).decode("utf-8")
    for output in (store, preview.text, response.text, listed.text, json.dumps(_records(env)), caplog.text):
        assert variable not in output and CANARY_SECRET not in output


@pytest.mark.parametrize(
    ("table", "refusal"),
    [
        ('mode = "read_write"', "import.mode"),
        ('mode = "admin"', "import.mode"),
        (None, "import.environment_required"),
        ("statement_timeout_ms = 999999999", "config.range"),
    ],
    ids=["read_write", "admin", "no_environment", "studio_range"],
)
def test_a_profile_studio_would_not_hold_as_a_read_profile_is_not_imported(
    env: WriteEnv, tmp_path: Path, table: str | None, refusal: str
) -> None:
    environment = "" if table is None else 'environment = "dev"\n'
    extra = "" if table is None else f"{table}\n"
    text = (
        'version = 1\n\n[[profile]]\nname = "odd"\nengine = "postgresql"\nhost = "localhost"\ndatabase = "odd"\n'
        f'user = "u"\n{environment}{extra}'
    )
    source = _file(_folder(tmp_path), text)
    preview = _preview(env, source)
    assert preview.status_code == 200, preview.text
    item = preview.json()["profiles"][0]
    assert item["importable"] is False
    assert refusal in [r["code"] for r in item["refusals"]]
    before = _store_bytes(env)
    response = _import(env, source, [{"name": "odd"}], preview=preview.json())
    assert _code(response) == (400, "studio.dbm_profile_import_refused")
    assert ("import.profiles", refusal) in _issues(response)
    assert _store_bytes(env) == before
    assert _runs(env) == []


def test_an_imported_profile_never_relabels_a_prod_database(env: WriteEnv, tmp_path: Path) -> None:
    """A DEV or TEST profile for the database of a PROD profile (of the store or the same file) is not imported."""
    text = """version = 1

[[profile]]
name = "books-dev"
engine = "postgresql"
host = "localhost"
database = "books"
environment = "dev"

[[profile]]
name = "inv-prod"
engine = "postgresql"
host = "localhost"
port = 5440
database = "inv"
environment = "prod"

[[profile]]
name = "inv-test"
engine = "postgresql"
host = "127.0.0.1"
port = 5440
database = "inv"
environment = "test"
"""
    preview = _preview(env, _file(_folder(tmp_path), text))
    assert preview.status_code == 200, preview.text
    items = {p["name"]: p for p in preview.json()["profiles"]}
    assert items["inv-prod"]["importable"] is True  # PROD stays PROD
    for name in ("books-dev", "inv-test"):  # localhost and 127.0.0.1 are this computer: the same server
        assert items[name]["importable"] is False
        assert [r["code"] for r in items[name]["refusals"]] == ["import.prod_target"], name


def test_a_sqlite_profile_for_a_prod_file_is_not_imported(env: WriteEnv, tmp_path: Path) -> None:
    prod_file = tmp_path / "prod.db"
    prod_file.write_bytes(env.db.read_bytes())
    env.write_file(
        _store(env.db) + f'\n[[profile]]\nname = "p"\nengine = "sqlite"\npath = "{prod_file}"\nenvironment = "prod"\n'
    )
    folder = _folder(tmp_path)
    alias = folder / "alias.db"
    os.link(prod_file, alias)  # the same file under another name
    text = 'version = 1\n\n[[profile]]\nname = "q"\nengine = "sqlite"\npath = "alias.db"\nenvironment = "dev"\n'
    preview = _preview(env, _file(folder, text))
    item = preview.json()["profiles"][0]
    assert item["importable"] is False and item["refusals"] == [{"field": "environment", "code": "import.prod_target"}]


# --- 3b. import: files refused as a whole ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["db1,db2", "/var/run/postgresql,db.example.org", ",", "db1,"],
    ids=["two", "socket_then_remote", "bare", "trailing"],
)
def test_a_multi_host_profile_is_refused_never_cut(env: WriteEnv, tmp_path: Path, host: str) -> None:
    """W-10, TB-148: the whole file is refused, never imported with the first host or without that profile."""
    text = (
        IMPORTED
        + f'\n[[profile]]\nname = "ha"\nengine = "postgresql"\nhost = "{host}"\ndatabase = "d"\nenvironment = "dev"\n'
    )
    source = _file(_folder(tmp_path), text)
    before = _store_bytes(env)
    response = _preview(env, source)
    assert _code(response) == (400, "studio.dbm_profile_import_multi_host")
    assert _issues(response) == [("import.file", "import.host_list")]
    assert "db1" not in response.text and "example.org" not in response.text
    forged = _import(env, source, [{"name": "asks"}], preview={"state": "0" * 64, "user_file": {"digest": None}})
    assert _code(forged) in ((400, "studio.dbm_profile_import_changed"), (400, "studio.dbm_profile_import_multi_host"))
    assert _store_bytes(env) == before


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (b'version = 1\n[[profile]\nname = "x"', "import.syntax"),
        (b"version = 1\n\xff\xfe", "import.encoding"),
        (b"version = 2\n", "import.version"),
        (b"", "import.required"),
        (b"version = 1\n", "import.empty"),
        (
            b'version = 1\n[[profile]]\nname = "x"\nengine = "postgresql"\nhost = "h"\ndatabase = "d"\n'
            b'password = "hunter2-plain-canary"\nenvironment = "dev"\n',
            "import.plain_password",
        ),
        (
            b'version = 1\n[[profile]]\nname = "x"\nengine = "sqlite"\npath = "/a.db"\nenvironment = "dev"\n'
            b'kind = "admin"\n',
            "import.unknown_key",
        ),
        (
            b'version = 1\nwrite_profile = [{name = "w", kind = "admin", read_profile = "x"}]\n'
            b'[[profile]]\nname = "x"\nengine = "sqlite"\npath = "/a.db"\nenvironment = "dev"\n',
            "import.unknown_key",
        ),
        (
            b'version = 1\n[[profile]]\nname = "x"\nengine = "sqlite"\npath = "//host/share/a.db"\n'
            b'environment = "dev"\n',
            "import.network_path",
        ),
    ],
    ids=[
        "syntax",
        "encoding",
        "version",
        "empty_file",
        "no_profile",
        "plain_password",
        "write_key",
        "write_table",
        "unc",
    ],
)
def test_a_malformed_file_is_refused_as_a_whole(env: WriteEnv, tmp_path: Path, text: bytes, code: str) -> None:
    source = _file(_folder(tmp_path), text)
    response = _preview(env, source)
    assert _code(response) == (400, "studio.dbm_profile_import_invalid"), response.text
    assert ("import.file", code) in _issues(response)
    assert "hunter2" not in response.text


def test_a_file_with_too_many_profiles_is_refused(env: WriteEnv, tmp_path: Path) -> None:
    tables = "".join(
        f'[[profile]]\nname = "p{i}"\nengine = "sqlite"\npath = "/d{i}.db"\nenvironment = "dev"\n' for i in range(65)
    )
    response = _preview(env, _file(_folder(tmp_path), "version = 1\n" + tables))
    assert _code(response) == (400, "studio.dbm_profile_import_invalid")
    assert _issues(response) == [("import.file", "import.too_many")]


def test_an_oversized_file_is_refused_unread(env: WriteEnv, tmp_path: Path) -> None:
    source = _file(_folder(tmp_path), b"version = 1\n" + b"#" * (256 * 1024))
    assert _code(_preview(env, source)) == (400, "studio.dbm_profile_import_too_large")


# --- 4. the file and its folder -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("relative/in.tabrion-profile.toml", "import.not_absolute"),
        ("/a/../b/in.tabrion-profile.toml", "import.not_normalized"),
        ("/a/in.toml", "import.suffix"),
        ("/a/in‮.tabrion-profile.toml", "import.control_character"),
        ("//host/share/in.tabrion-profile.toml", "import.network_path"),
        ("", "import.required"),
    ],
)
def test_a_path_that_is_not_valid_is_refused(env: WriteEnv, path: str, code: str) -> None:
    response = env.send("POST", IMPORT_PREVIEW, {"path": path})
    assert _code(response) == (400, "studio.dbm_profile_import_invalid")
    assert _issues(response) == [("import.path", code)]


def test_links_and_unsafe_files_are_refused(env: WriteEnv, tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    real = _file(folder, name="real.tabrion-profile.toml")
    link = folder / "link.tabrion-profile.toml"
    link.symlink_to(real)
    dangling = folder / "dangling.tabrion-profile.toml"
    dangling.symlink_to(folder / "nothing")
    hard = folder / "hard.tabrion-profile.toml"
    os.link(real, hard)
    shared = _file(folder, name="shared.tabrion-profile.toml")
    shared.chmod(0o666)
    pipe = folder / "pipe.tabrion-profile.toml"
    os.mkfifo(pipe, 0o600)
    a_folder = folder / "folder.tabrion-profile.toml"
    a_folder.mkdir()
    for path in (link, dangling, hard, shared, pipe, a_folder):
        assert _code(_preview(env, path)) == (400, "studio.dbm_profile_import_folder"), path.name
    assert _code(_preview(env, folder / "missing.tabrion-profile.toml")) == (400, "studio.dbm_profile_import_missing")
    os.unlink(hard)  # one name again: now it is read
    assert _preview(env, real).status_code == 200


def test_unsafe_folders_and_studios_own_folders_are_refused(env: WriteEnv, tmp_path: Path) -> None:
    real = _folder(tmp_path)
    _file(real)
    via_link = tmp_path / "via-link"
    via_link.symlink_to(real, target_is_directory=True)
    assert _code(_preview(env, via_link / "in.tabrion-profile.toml")) == (400, "studio.dbm_profile_import_folder")
    open_folder = _folder(tmp_path, "open")
    _file(open_folder)
    open_folder.chmod(0o777)
    assert _code(_preview(env, open_folder / "in.tabrion-profile.toml")) == (400, "studio.dbm_profile_import_folder")
    above = _folder(tmp_path, "above")
    below = above / "below"
    below.mkdir(mode=0o700)
    _file(below)
    above.chmod(0o777)  # a folder above that others may write, without the sticky bit
    assert _code(_preview(env, below / "in.tabrion-profile.toml")) == (400, "studio.dbm_profile_import_folder")
    for folder in (env.config_dir, env.audit_dir, env.project / ".tabrion"):
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        _file(folder)
        response = _preview(env, folder / "in.tabrion-profile.toml")
        assert _code(response) == (400, "studio.dbm_profile_import_reserved"), folder


def test_windows_is_refused(env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbm_profiles, "_PLATFORM_OK", False)
    source = _file(_folder(tmp_path))
    assert _code(_preview(env, source)) == (400, "studio.dbm_profile_import_platform")


# --- 5. name clashes ------------------------------------------------------------------------------------------------


def test_a_name_clash_is_refused_unless_a_new_name_is_chosen(env: WriteEnv, tmp_path: Path) -> None:
    clash = (
        'version = 1\n\n[[profile]]\nname = "parts"\nengine = "sqlite"\npath = "other.db"\nenvironment = "test"\n\n'
        '[[profile]]\nname = "ledger-admin"\nengine = "sqlite"\npath = "x.db"\nenvironment = "dev"\n'
    )
    source = _file(_folder(tmp_path), clash)
    preview = _preview(env, source)
    items = {p["name"]: p for p in preview.json()["profiles"]}
    assert items["parts"]["name_taken"] is True and items["ledger-admin"]["name_taken"] is True
    before = _store_bytes(env)
    for chosen in (
        [{"name": "parts"}],  # an existing read profile
        [{"name": "ledger-admin"}],  # an existing admin profile's name
        [{"name": "ledger-admin", "as": "orders-write"}],  # a new name that a write profile has
        [{"name": "parts", "as": "ledger"}],  # a new name that another read profile has
    ):
        response = _import(env, source, chosen, preview=preview.json())
        assert _code(response) == (409, "studio.dbm_profile_import_exists"), chosen
        assert _store_bytes(env) == before
    assert _runs(env) == []
    response = _import(env, source, [{"name": "parts", "as": "parts-copy"}], preview=preview.json())
    assert response.status_code == 201, response.text
    stored = _user_profiles(env)
    assert stored["parts"].path == str(env.db) and stored["parts"].environment == "dev"  # untouched
    assert stored["parts-copy"].environment == "test"


def test_a_profile_added_after_the_check_is_never_replaced(env: WriteEnv, tmp_path: Path) -> None:
    """The clash is checked again inside the save: a profile of that name added meanwhile wins."""
    source = _file(_folder(tmp_path))
    preview = _preview(env, source).json()
    real = env.config.user.save_profiles
    meanwhile = Profile(name="asks", engine="sqlite", environment="dev", path=str(env.db))

    def racing(change: Any, expected: object, *, overwrite: bool = False) -> str:
        # Someone added a profile of that name after the import's check: the save's list now holds it.
        return real(lambda current: change([*current, meanwhile]), expected, overwrite=overwrite)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(env.config.user, "save_profiles", racing)
        response = _import(env, source, [{"name": "asks"}], preview=preview)
    assert _code(response) == (409, "studio.dbm_profile_import_exists")
    assert "asks" not in _user_profiles(env)  # nothing saved
    results = [r["fields"]["result"] for r in _runs(env) if r["event"] == "run.result"]
    assert results == ["studio.dbm_profile_import_exists"]


@pytest.mark.parametrize(
    "chosen",
    [
        [],
        [{"name": "nope"}],
        [{"name": "asks"}, {"name": "asks", "as": "asks-2"}],
        [{"name": "asks", "as": "x"}, {"name": "stats", "as": "x"}],
        [{"name": "asks", "kind": "admin"}],
        ["asks"],
        [{"name": "asks", "as": ""}],
    ],
    ids=["none", "unknown", "twice", "same_new_name", "extra_key", "not_an_object", "empty_name"],
)
def test_a_choice_that_is_not_valid_is_refused(env: WriteEnv, tmp_path: Path, chosen: Any) -> None:
    source = _file(_folder(tmp_path))
    response = _import(env, source, chosen)
    assert _code(response) == (400, "studio.dbm_profile_import_invalid")
    assert _issues(response)[0][0] == "import.profiles"


def test_a_new_name_with_a_hidden_character_is_refused(env: WriteEnv, tmp_path: Path) -> None:
    source = _file(_folder(tmp_path))
    response = _import(env, source, [{"name": "asks", "as": "asks​"}])
    assert _code(response) == (400, "studio.dbm_profile_import_refused")
    assert _issues(response) == [("import.profiles", "import.name_hidden")]


# --- 6. preview, typed name, audit ----------------------------------------------------------------------------------


def test_the_import_is_bound_to_its_preview(env: WriteEnv, tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    source = _file(folder)
    preview = _preview(env, source).json()
    before = _store_bytes(env)
    assert _code(_import(env, source, [{"name": "asks"}], preview=preview, typed="x.tabrion-profile.toml")) == (
        400,
        "studio.dbm_profile_import_unconfirmed",
    )
    source.write_text(IMPORTED.replace('"asks"', '"asks2"'), encoding="utf-8")
    assert _code(_import(env, source, [{"name": "asks"}], preview=preview)) == (
        400,
        "studio.dbm_profile_import_changed",
    )
    assert _store_bytes(env) == before
    # The user file changed since the preview: the save's own check refuses it.
    _file(folder)
    preview = _preview(env, source).json()
    env.write_file(
        _store(env.db) + '\n[[profile]]\nname = "meanwhile"\nengine = "sqlite"\npath = "/m.db"\nenvironment = "dev"\n'
    )
    assert _code(_import(env, source, [{"name": "asks"}], preview=preview)) == (409, "studio.config_stale")
    assert "asks" not in _user_profiles(env)


def test_a_hand_written_store_is_rewritten_only_when_confirmed(env: WriteEnv, tmp_path: Path) -> None:
    """As every save of the user's profiles: a file written by hand is rewritten only with the user's consent."""
    source = _file(_folder(tmp_path))
    before = _store_bytes(env)
    response = _import(env, source, [{"name": "asks"}], overwrite=False)
    assert _code(response) == (409, "studio.config_hand_written")
    assert _store_bytes(env) == before
    assert _code(_import(env, source, [{"name": "asks"}], overwrite="yes")) == (
        400,
        "studio.dbm_profile_import_invalid",
    )


def test_the_import_is_audited_without_folder_path_or_reference(env: WriteEnv, tmp_path: Path) -> None:
    source = _file(_folder(tmp_path))
    response = _import(env, source, [{"name": "warehouse", "as": "wh"}, {"name": "local-copy"}])
    assert response.status_code == 201, response.text
    runs = _runs(env)
    assert [r["event"] for r in runs] == ["run.start", "run.result"]
    start, result = (r["fields"] for r in runs)
    assert (start["area"], start["action"]) == ("database_manager", "dbm.profile_import")
    assert start["objects"] == ["wh", "local-copy"] and start["target"] == source.name
    assert result["result"] == "committed" and result["run"] == start["run"]
    dumped = json.dumps(runs)
    for absent in (str(source.parent), "data/copy.db", ENV_NAME, "env:", "keyring", CANARY_SECRET, "localhost"):
        assert absent not in dumped, absent


def test_an_unwritable_audit_log_stops_the_import(
    env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.audit import AuditLog

    def refuse(self: AuditLog, event: str, **fields: Any) -> int:
        raise AuditError()

    source = _file(_folder(tmp_path))
    preview = _preview(env, source).json()
    before = _store_bytes(env)
    monkeypatch.setattr(AuditLog, "append", refuse)
    response = _import(env, source, [{"name": "asks"}], preview=preview)
    assert _code(response) == (405, "studio.audit_unavailable")
    assert _store_bytes(env) == before


# --- 7. pairs: an imported profile in a pair needs the I-109 j confirmation ----------------------------------------


def test_an_imported_profile_in_a_pair_needs_the_confirmation_before_any_secret(
    env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _file(_folder(tmp_path))
    assert _import(env, source, [{"name": "warehouse"}]).status_code == 201
    created = env.create_write(
        name="warehouse-admin", read="warehouse", kind="admin", user="wh_admin", password=f"env:{ENV_NAME}"
    )
    assert created.status_code == 201, created.text
    listed = env.send("GET", "/api/write/profiles").json()["write_profiles"]
    item = next(w for w in listed if w["name"] == "warehouse-admin")
    assert item["read_profile_id"] == "user:warehouse" and item["secret"]["confirmed"] is False
    read = _user_profiles(env)["warehouse"]
    stand_in = SimpleNamespace(
        id="c_wh",
        profile_id="user:warehouse",
        engine="postgresql",
        environment=read.environment,
        fingerprint=target_fingerprint(read.to_dict()),
        database_file=None,
    )

    def get(connection_id: object) -> Any:
        if connection_id != "c_wh":
            raise connections_module.RegistryError("studio.connection_not_found")
        return stand_in

    resolved: list[object] = []
    original = env.config.secrets.resolve

    def spy(target: Any) -> str:
        resolved.append(target)
        return original(target)

    monkeypatch.setattr(env.registry, "get", get)
    monkeypatch.setattr(env.config.secrets, "resolve", spy)
    refused = env.unlock("c_wh", "write:warehouse-admin", "warehouse-admin")
    assert _code(refused) == (400, "studio.secret_unconfirmed")
    assert resolved == [] and CANARY_SECRET not in refused.text
    # The confirmation names the imported server, so the user sees where the secret would go.
    preview = env.send("POST", "/api/write/secret-confirmations", {"write_profile_id": "write:warehouse-admin"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["pair"]["host"] == "localhost" and preview.json()["pair"]["port"] == 5434
