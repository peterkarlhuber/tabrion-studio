"""STU-CR-2 and STU-CR-3: the Schema Designer's database creation (G-S3 is not granted; GS3-01..03, GS3-20, GS3-21,
GS3-27, TX-29, STU-ADR-0007 sections 1 and 11).

1. **Kill switch (GS3-01, STU-ADR-0006 proof items 1..3).** With the ``schema_designer`` flag off (every flag off,
   or only another area's) both routes answer ``405 studio.method_not_allowed``, nothing is created (no file, no
   database, no profile, no audit record) and nothing of the area is imported; a query parameter or a header never
   opens them. The counterpart shows the flag on loads it.
2. **SQLite (GS3-21):** the file is created ``0600`` by tabrion's ``create_private_file``, never through a link
   (the name, a side file, the folder or any folder above it), never in a folder others may write or that belongs to
   another user, never in Studio's own folders, never over an existing file, only after the typed file name, with
   ``run.start`` and ``run.result`` (GS3-27) and no path in the audit log.
3. **PROD (GS3-03):** the PostgreSQL route refuses a connection whose stored environment is ``prod``, also when the
   profile file is changed by hand after the unlock; the environment never comes from the request.
4. **Errors (GS3-20):** every refusal is a catalog problem that echoes nothing of the request (canary).

The PostgreSQL cases against a server are in ``test_schema_designer_create_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import WriteError, defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security

SQLITE_ROUTE = "/api/write/schema-designer/create/sqlite"
POSTGRES_ROUTE = "/api/write/schema-designer/create/postgresql"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
POSIX = sys.platform != "win32"
# Python 3.11 refuses every SQLite write unlock (no defensive mode; test_write_unlock.py proves the refusal).
needs_defensive = pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses a SQLite write connection")


def _refused(response: Any, status: int, code: str) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text


def _records(env: WriteEnv) -> list[dict[str, Any]]:
    answer = env.send("GET", "/api/write/audit?limit=1000")
    assert answer.status_code == 200, answer.text
    records: list[dict[str, Any]] = answer.json()["records"]
    return records


def _runs(env: WriteEnv) -> list[dict[str, Any]]:
    return [r for r in _records(env) if r["event"] in ("run.start", "run.result")]


@pytest.fixture
def env(tmp_path: Path, static_dir: Path) -> Iterator[WriteEnv]:
    environment = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    try:
        yield environment
    finally:
        environment.close()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A private folder of this user for new databases."""
    path = tmp_path / "databases"
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _create(env: WriteEnv, path: Path | str, typed: str | None = None) -> Any:
    name = typed if typed is not None else Path(path).name
    return env.send("POST", SQLITE_ROUTE, {"path": str(path), "typed": name})


# --- 1. the kill switch (GS3-01, GS3-02) ---------------------------------------------------------------------------


def _state(env: WriteEnv, folder: Path) -> dict[str, object]:
    profiles = env.config_dir / "profiles.toml"
    return {
        "profiles": profiles.read_bytes() if profiles.exists() else None,
        "folder": sorted(p.name for p in folder.iterdir()),
        "database": digest(env.db),
        "audit": sorted(p.name for p in env.audit_dir.iterdir()) if env.audit_dir.exists() else None,
        "audit_bytes": [p.read_bytes() for p in sorted(env.audit_dir.iterdir())] if env.audit_dir.exists() else None,
    }


@pytest.mark.parametrize(
    "areas",
    [WriteAreas(), DATA_EDITOR, DATABASE_MANAGER, WriteAreas(frozenset({Area.DATA_EDITOR, Area.DATABASE_MANAGER}))],
    ids=["every_flag_off", "data_editor_only", "database_manager_only", "both_other_areas"],
)
def test_flag_off_both_routes_are_405_and_change_nothing(
    tmp_path: Path, static_dir: Path, folder: Path, areas: WriteAreas
) -> None:
    """Proof item 2 per route: the W-9 answer, no file, no profile, no audit record; another area's flag (also an
    unlocked write profile of the Data Editor) never opens the Schema Designer (GS3-02)."""
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        # On Python 3.11 no SQLite write unlock exists to try (refused, test_write_unlock.py); the flags still apply.
        if Area.DATA_EDITOR in areas.enabled and defensive_available():
            assert env.create_write().status_code == 201
            assert env.unlock(connection_id).status_code == 200
        before = _state(env, folder)
        target = folder / f"{CANARY_VALUE}.db"
        sqlite = env.send("POST", SQLITE_ROUTE, {"path": str(target), "typed": target.name})
        _refused(sqlite, 405, "studio.method_not_allowed")
        postgres = env.send(
            "POST",
            POSTGRES_ROUTE,
            {"connection_id": connection_id, "name": CANARY_VALUE, "typed": CANARY_VALUE},
        )
        _refused(postgres, 405, "studio.method_not_allowed")
        assert not target.exists()
        assert _state(env, folder) == before
    finally:
        env.close()


@pytest.mark.parametrize("query", ["?schema_designer=true", "?areas=schema_designer", "?write=1"])
def test_a_query_parameter_or_a_header_never_opens_the_routes(
    tmp_path: Path, static_dir: Path, folder: Path, query: str
) -> None:
    """Proof item 3: the Data Editor flag is on; a query parameter, a header or a body key never adds the Schema
    Designer (GS3-02)."""
    env = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        headers = {**env.headers, "X-Tabrion-Write-Areas": "schema_designer", "X-Tabrion-Schema-Designer": "true"}
        target = folder / "new.db"
        refused = env.studio.client.post(
            f"{SQLITE_ROUTE}{query}",
            json={"path": str(target), "typed": "new.db", "areas": {"schema_designer": True}},
            headers=headers,
        )
        _refused(refused, 405, "studio.method_not_allowed")
        assert not target.exists()
        assert env.send("GET", "/api/write/areas").json()["areas"]["schema_designer"] is False
    finally:
        env.close()


def _modules(tmp_path: Path, areas: str) -> list[str]:
    """The answers of both routes and the loaded modules of the area and of tabrion's creation code, in a fresh
    interpreter."""
    target = tmp_path / "databases" / "new.db"
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), {areas})\n"
        "env.create_read()\n"
        "c = env.open()\n"
        f"s = env.send('POST', {SQLITE_ROUTE!r}, {{'path': {str(target)!r}, 'typed': 'new.db'}})\n"
        f"p = env.send('POST', {POSTGRES_ROUTE!r}, {{'connection_id': c, 'name': 'x', 'typed': 'x'}})\n"
        "print(s.status_code, s.json().get('code'), p.status_code, p.json().get('code'))\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write.schema_designer')))\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    (tmp_path / "databases").mkdir(mode=0o700)
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=root, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    loaded: list[str] = json.loads(lines[-1].replace("'", '"'))
    assert not target.exists() or areas.startswith("WriteAreas(frozenset({Area.SCHEMA_DESIGNER")
    return [lines[-2], *loaded]


@pytest.mark.parametrize(
    "areas",
    ["WriteAreas()", "WriteAreas(frozenset({Area.DATA_EDITOR}))", "WriteAreas(frozenset({Area.DATABASE_MANAGER}))"],
)
def test_flag_off_nothing_of_the_area_is_imported(tmp_path: Path, areas: str) -> None:
    """GS3-01: with the Schema Designer flag off (every flag off, or another area's on) its code is not loaded."""
    loaded = _modules(tmp_path, areas)
    assert loaded == ["405 studio.method_not_allowed 405 studio.method_not_allowed"]


def test_flag_on_the_area_is_loaded(tmp_path: Path) -> None:
    """The counterpart (the test above is not vacuous): the flag loads the area; the SQLite file is created and the
    PostgreSQL route asks for an unlock."""
    loaded = _modules(tmp_path, "WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))")
    assert loaded[0] == "201 None 405 studio.write_locked"
    assert "tabrion_studio.write.schema_designer.create" in loaded


def test_the_areas_route_says_the_schema_designer_is_off_by_default(tmp_path: Path, static_dir: Path) -> None:
    """Proof item 1 for the area: no flag source, so the page is told it is off (and hides the wizard)."""
    env = WriteEnv(tmp_path, static_dir)
    try:
        assert env.send("GET", "/api/write/areas").json()["areas"]["schema_designer"] is False
    finally:
        env.close()


# --- 2. SQLite (STU-CR-2, GS3-21) ------------------------------------------------------------------------------------


def test_a_new_sqlite_file_is_private_initialized_and_audited(env: WriteEnv, folder: Path) -> None:
    target = folder / "inventory.db"
    response = _create(env, target)
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc == {
        "engine": "sqlite",
        "path": str(target),
        "file_name": "inventory.db",
        "mode": "0600" if POSIX else None,
        "audit_pending": False,
    }
    info = os.lstat(target)
    assert stat.S_ISREG(info.st_mode)
    if POSIX:
        assert stat.S_IMODE(info.st_mode) == 0o600
    assert target.read_bytes()[:16] == b"SQLite format 3\x00"
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM sqlite_schema").fetchone() == (0,)
    connection.close()
    runs = _runs(env)
    assert [r["event"] for r in runs] == ["run.result", "run.start"]  # newest first
    start, result = runs[1]["fields"], runs[0]["fields"]
    assert start["action"] == "schema_designer.create_sqlite" and start["area"] == "schema_designer"
    assert start["objects"] == ["inventory.db"] and start["engine"] == "sqlite"
    assert result["result"] == "committed" and result["run"] == start["run"]
    # The folder never reaches the audit log (only the file name).
    assert str(folder) not in json.dumps(_records(env))


def test_the_new_file_can_be_saved_as_a_profile_and_opened(env: WriteEnv, folder: Path) -> None:
    """The page's second step: a read profile through the normal profile route, then a connection."""
    target = folder / "fresh.sqlite3"
    assert _create(env, target).status_code == 201
    env.create_read(name="fresh", path=target)
    connection_id = env.open("user:fresh")
    assert connection_id


@pytest.mark.parametrize("typed", ["", "Inventory.db", "inventory", "inventory.db ", None])
def test_the_file_name_must_be_typed_exactly(env: WriteEnv, folder: Path, typed: str | None) -> None:
    target = folder / "inventory.db"
    response = env.send("POST", SQLITE_ROUTE, {"path": str(target), "typed": typed})
    _refused(response, 400, "studio.schema_create_unconfirmed")
    assert not target.exists()
    assert _runs(env) == []


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_an_existing_file_or_side_file_is_never_overwritten(env: WriteEnv, folder: Path, suffix: str) -> None:
    target = folder / "inventory.db"
    existing = Path(str(target) + suffix)
    existing.write_bytes(CANARY_VALUE.encode())
    existing.chmod(0o600)
    response = _create(env, target)
    _refused(response, 400, "studio.schema_file_exists")
    assert existing.read_bytes() == CANARY_VALUE.encode()
    assert sorted(p.name for p in folder.iterdir()) == [existing.name]
    assert _runs(env) == []


@pytest.mark.skipif(not POSIX, reason="POSIX links")
def test_a_link_as_the_name_is_never_followed(env: WriteEnv, folder: Path, tmp_path: Path) -> None:
    """A dangling link and a link to an existing file: refused, the link's target is neither made nor changed."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    dangling = folder / "dangling.db"
    dangling.symlink_to(elsewhere / "made-through-link.db")
    _refused(_create(env, dangling), 400, "studio.schema_file_exists")
    assert not (elsewhere / "made-through-link.db").exists()
    victim = elsewhere / "victim.db"
    victim.write_bytes(b"keep")
    pointing = folder / "pointing.db"
    pointing.symlink_to(victim)
    _refused(_create(env, pointing), 400, "studio.schema_file_exists")
    assert victim.read_bytes() == b"keep"
    side = folder / "side.db-wal"
    side.symlink_to(elsewhere / "side-target")
    _refused(_create(env, folder / "side.db"), 400, "studio.schema_file_exists")
    assert not (elsewhere / "side-target").exists()


@pytest.mark.skipif(not POSIX, reason="POSIX links")
def test_a_folder_reached_through_a_link_is_refused(env: WriteEnv, folder: Path, tmp_path: Path) -> None:
    """The folder itself a link, and a link further up: refused; the real path works."""
    link = tmp_path / "link-to-databases"
    link.symlink_to(folder, target_is_directory=True)
    _refused(_create(env, link / "a.db"), 400, "studio.schema_folder_unsafe")
    nested = folder / "nested"
    nested.mkdir(mode=0o700)
    _refused(_create(env, link / "nested" / "b.db"), 400, "studio.schema_folder_unsafe")
    assert sorted(p.name for p in folder.iterdir()) == ["nested"] and list(nested.iterdir()) == []
    assert _create(env, nested / "b.db").status_code == 201


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
@pytest.mark.parametrize("mode", [0o770, 0o777, 0o1777, 0o720, 0o702])
def test_a_folder_others_may_write_is_refused(env: WriteEnv, folder: Path, mode: int) -> None:
    folder.chmod(mode)
    try:
        _refused(_create(env, folder / "a.db"), 400, "studio.schema_folder_unsafe")
        assert list(folder.iterdir()) == []
    finally:
        folder.chmod(0o700)


def test_a_missing_folder_is_refused_and_not_created(env: WriteEnv, folder: Path) -> None:
    _refused(_create(env, folder / "missing" / "a.db"), 400, "studio.schema_folder_unsafe")
    assert not (folder / "missing").exists()


def test_a_file_as_folder_is_refused(env: WriteEnv, folder: Path) -> None:
    (folder / "plain").write_text("x")
    _refused(_create(env, folder / "plain" / "a.db"), 400, "studio.schema_folder_unsafe")


@pytest.mark.parametrize("which", ["config", "audit", "state"])
def test_studios_own_folders_are_refused(env: WriteEnv, which: str) -> None:
    """No database file in the configuration folder (where profiles.toml and write-areas.toml live), the audit
    log's folder or the state folder (Studio's own state database)."""
    folder = {"config": env.config_dir, "audit": env.audit_dir, "state": env.state}[which]
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    _refused(_create(env, folder / "studio.db"), 400, "studio.schema_folder_reserved")
    assert not (folder / "studio.db").exists()


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("relative/a.db", "create.not_absolute"),
        ("/tmp/../tmp/a.db", "create.not_normalized"),  # noqa: S108 - never created
        ("/tmp//a.db", "create.not_normalized"),  # noqa: S108
        ("/tmp/a.txt", "create.suffix"),  # noqa: S108
        ("/tmp/a", "create.suffix"),  # noqa: S108
        ("/tmp/a\x00.db", "create.control_character"),  # noqa: S108
        ("/tmp/a\n.db", "create.control_character"),  # noqa: S108
        ("//host/share/a.db", "create.network_path"),
        ("/net/host/a.db", "create.network_path"),
        ("", "create.required"),
        ("/" + "a" * 5000 + ".db", "create.too_long"),
    ],
)
def test_a_path_that_is_not_valid_is_refused_with_its_field(env: WriteEnv, path: str, code: str) -> None:
    response = env.send("POST", SQLITE_ROUTE, {"path": path, "typed": Path(path).name if path else "x"})
    _refused(response, 400, "studio.schema_create_invalid")
    assert response.json()["issues"] == [{"pointer": "create.path", "code": code}]
    assert _runs(env) == []


def test_a_canary_in_the_path_is_never_echoed_or_logged(
    env: WriteEnv, folder: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """GS3-20: an answer never carries the request's path or name; the log neither."""
    caplog.set_level(logging.DEBUG)
    for path in (folder / f"{CANARY_VALUE}.txt", folder / CANARY_SECRET / "a.db"):
        response = env.send("POST", SQLITE_ROUTE, {"path": str(path), "typed": path.name})
        assert response.status_code == 400
        assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    assert CANARY_VALUE not in caplog.text and CANARY_SECRET not in caplog.text


@pytest.mark.parametrize("extra", [{"mode": "0644"}, {"environment": "dev"}, {"overwrite": True}])
def test_a_body_with_another_key_is_refused(env: WriteEnv, folder: Path, extra: dict[str, object]) -> None:
    """The server decides the mode and never overwrites; a key it does not know is refused, not ignored."""
    response = env.send("POST", SQLITE_ROUTE, {"path": str(folder / "a.db"), "typed": "a.db", **extra})
    assert (response.status_code, response.json()["code"]) == (400, "access.bad_request")
    assert list(folder.iterdir()) == []


def test_without_an_audit_log_nothing_is_created(tmp_path: Path, static_dir: Path, folder: Path) -> None:
    """GS3-27: no run.start record, no file (fail closed)."""
    (tmp_path / "config").mkdir(mode=0o700)
    (tmp_path / "config" / "audit").mkdir(mode=0o700)
    (tmp_path / "config" / "audit").chmod(0o777)
    env = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    try:
        _refused(_create(env, folder / "a.db"), 405, "studio.audit_unavailable")
        assert list(folder.iterdir()) == []
    finally:
        env.close()
        (tmp_path / "config" / "audit").chmod(0o700)


def test_a_file_that_appears_meanwhile_is_left_alone(
    env: WriteEnv, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tabrion leaves an existing name unchanged: a file made by someone else between Studio's check and the create
    is refused as existing and neither changed nor removed; the run is recorded as refused."""
    from tabrion_studio.write.schema_designer import create as create_module

    target = folder / "race.db"

    def planted(path: str) -> None:
        Path(path).write_bytes(CANARY_VALUE.encode())
        Path(path).chmod(0o600)

    monkeypatch.setattr(create_module, "create_private_file", planted)
    _refused(_create(env, target), 400, "studio.schema_file_exists")
    assert target.read_bytes() == CANARY_VALUE.encode()
    results = [r["fields"]["result"] for r in _runs(env) if r["event"] == "run.result"]
    assert results == ["studio.schema_file_exists"]


def test_a_failure_after_the_create_removes_only_the_new_file(
    env: WriteEnv, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.schema_designer import create as create_module

    def broken(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError(f"disk I/O error {CANARY_VALUE}")

    monkeypatch.setattr(create_module, "sqlite_connect", broken)
    response = _create(env, folder / "broken.db")
    assert response.status_code == 400 and CANARY_VALUE not in response.text
    assert list(folder.iterdir()) == []
    results = [r["fields"]["result"] for r in _runs(env) if r["event"] == "run.result"]
    assert len(results) == 1 and results[0] != "committed"


# --- 3. PROD and the unlock on the PostgreSQL route (GS3-03) ---------------------------------------------------------


def test_the_postgresql_route_needs_an_unlock(env: WriteEnv) -> None:
    env.create_read()
    connection_id = env.open()
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
    _refused(response, 405, "studio.write_locked")
    assert _runs(env) == []


@needs_defensive
def test_a_sqlite_connection_cannot_create_a_postgresql_database(env: WriteEnv) -> None:
    env.create_read()
    assert env.create_write().status_code == 201
    connection_id = env.open()
    assert env.unlock(connection_id).status_code == 200
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
    _refused(response, 400, "studio.schema_create_engine")
    assert _runs(env) == []


def test_a_prod_connection_is_refused(env: WriteEnv) -> None:
    """PROD from the stored profile: no write profile can be coupled to it, a hand-written coupling is not used, and
    the route answers locked; an ``environment`` in the request is refused as an unknown key."""
    env.create_read(environment="prod")
    created = env.create_write()
    assert (created.status_code, created.json()["issues"][0]["code"]) == (400, "config.coupling_prod")
    env.write_file(
        (env.config_dir / "profiles.toml").read_text(encoding="utf-8")
        + '\n[[write_profile]]\nname = "parts-write"\nkind = "write"\nread_profile = "parts"\n'
    )
    connection_id = env.open(environment="prod")
    unlocked = env.unlock(connection_id)
    assert unlocked.status_code in (400, 405) and unlocked.status_code != 200
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
    _refused(response, 405, "studio.write_locked")
    claimed = env.send(
        "POST",
        POSTGRES_ROUTE,
        {"connection_id": connection_id, "name": "n1", "typed": "n1", "environment": "dev"},
    )
    assert (claimed.status_code, claimed.json()["code"]) == (400, "access.bad_request")


@needs_defensive
def test_a_profile_changed_to_prod_after_the_unlock_is_refused(env: WriteEnv) -> None:
    """The environment is read again from the user's file on every use: a hand edit to ``prod`` after the unlock
    refuses the route before anything is recorded or sent."""
    env.create_read()
    assert env.create_write().status_code == 201
    connection_id = env.open()
    assert env.unlock(connection_id).status_code == 200
    text = (env.config_dir / "profiles.toml").read_text(encoding="utf-8")
    env.write_file(text.replace('environment = "dev"', 'environment = "prod"'))
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
    # The profile file no longer couples the write profile (a PROD profile cannot have one): the unlock ends.
    _refused(response, 400, "studio.write_profile_unknown")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    assert [r for r in _runs(env) if r["fields"].get("area") == "schema_designer"] == []


@pytest.mark.parametrize("environment", ["prod", "staging", None])
def test_only_dev_and_test_unlocks_reach_the_creation(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, environment: str | None
) -> None:
    """The second lock of the route itself (E-X7): an unlock whose stored environment is not dev or test is refused
    with ``studio.write_prod_locked`` even if the unlock's own checks let it through."""
    from tabrion_studio.write.schema_designer import create as create_module

    class FakeConnection:
        engine = "postgresql"

    class FakeUnlock:
        def __init__(self) -> None:
            self.environment = environment
            self.connection = FakeConnection()

    class FakeUnlocks:
        def require_unlock(self, *_args: object) -> FakeUnlock:
            return FakeUnlock()

    with pytest.raises(WriteError) as caught:
        create_module._target(FakeUnlocks(), "s", "c")  # type: ignore[arg-type]
    assert caught.value.code == "studio.write_prod_locked"


@pytest.mark.parametrize(
    ("body", "field", "code"),
    [
        ({"name": ""}, "name", "create.required"),
        ({"name": "a" * 64}, "name", "create.too_long"),
        ({"name": "ä" * 32}, "name", "create.too_long"),  # 64 bytes in UTF-8
        ({"name": "a\x00b"}, "name", "create.control_character"),
        ({"name": "a\nb"}, "name", "create.control_character"),
        ({"name": " a"}, "name", "create.whitespace"),
        ({"name": 5}, "name", "create.type"),
        ({"name": "a", "owner": "o​x"}, "owner", "create.control_character"),
        ({"name": "a", "template": "postgres"}, "template", "create.choice"),
        ({"name": "a", "encoding": "UTF8;"}, "encoding", "create.format"),
        ({"name": "a", "locale": "C' x"}, "locale", "create.format"),
    ],
)
def test_the_fields_are_checked_before_anything_else(
    env: WriteEnv, body: dict[str, object], field: str, code: str
) -> None:
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": "c_x", "typed": body.get("name"), **body})
    _refused(response, 400, "studio.schema_create_invalid")
    assert response.json()["issues"] == [{"pointer": f"create.{field}", "code": code}]


def test_the_database_name_must_be_typed_exactly(env: WriteEnv) -> None:
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": "c_x", "name": "shop", "typed": "Shop"})
    _refused(response, 400, "studio.schema_create_unconfirmed")
