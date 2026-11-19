"""STU-CR-2 and STU-CR-3: database creation, in the Database Manager's admin area since the G-S4 grant of
2026-11-18 (STU-CR-ADMIN; GS3-01..03, GS3-20, GS3-21, GS3-27, GS4-01..03, TX-29, STU-ADR-0007 sections 1 and 11 as
amended, STU-ADR-0008 section 10).

1. **Kill switch (GS4-01, STU-ADR-0006 proof items 1..3).** With the ``database_manager`` flag off (every flag off,
   or only other areas', the Schema Designer's among them) both routes answer ``405 studio.method_not_allowed``,
   nothing is created (no file, no database, no profile, no audit record) and nothing of the area is imported; a
   query parameter or a header never opens them. The counterpart shows the flag on loads it.
2. **SQLite (GS3-21):** the file is created ``0600`` by tabrion's ``create_private_file``, never through a link
   (the name, a side file, the folder or any folder above it), never in a folder others may write or that belongs to
   another user, never in Studio's own folders, never over an existing file, only after the typed file name, with
   ``run.start`` and ``run.result`` (GS3-27) and no path in the audit log.
3. **PROD and the admin profile (GS3-03, GS4-03):** the PostgreSQL route needs an admin unlock (a write profile never
   serves the area) and refuses a connection whose stored environment is ``prod``, also when the
   profile file is changed by hand after the unlock; the environment never comes from the request.
4. **Errors (GS3-20):** every refusal is a catalog problem that echoes nothing of the request (canary).

The PostgreSQL cases against a server are in ``test_dbm_create_postgres.py``.
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
from tabrion_studio.write import audit as audit_module
from tabrion_studio.write.connection import WriteError, defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security

SQLITE_ROUTE = "/api/write/database-manager/create/sqlite"
POSTGRES_ROUTE = "/api/write/database-manager/create/postgresql"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
#: The area that creates databases since the G-S4 grant (STU-CR-ADMIN).
ADMIN_AREA = DATABASE_MANAGER
POSIX = sys.platform != "win32"
# Python 3.11 refuses every SQLite write unlock (no defensive mode; test_write_unlock.py proves the refusal).
needs_defensive = pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses a SQLite write connection")
# Windows: Studio creates no SQLite file there (open point 5 of the PR #128 review), see
# test_windows_refuses_the_sqlite_creation.
creates_files = pytest.mark.skipif(not POSIX, reason="no SQLite creation on Windows")


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
    environment = WriteEnv(tmp_path, static_dir, ADMIN_AREA)
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
    [WriteAreas(), DATA_EDITOR, SCHEMA_DESIGNER, WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))],
    ids=["every_flag_off", "data_editor_only", "schema_designer_only", "both_other_areas"],
)
def test_flag_off_both_routes_are_405_and_change_nothing(
    tmp_path: Path, static_dir: Path, folder: Path, areas: WriteAreas
) -> None:
    """Proof item 2 per route: the W-9 answer, no file, no profile, no audit record; another area's flag (also an
    unlocked write profile of the Data Editor, and the Schema Designer's flag, which opened these routes before the
    G-S4 grant) never opens the admin area's database creation (GS4-02)."""
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        # On Python 3.11 no SQLite write unlock exists to try (refused, test_write_unlock.py); the flags still apply.
        if areas.enabled & {Area.DATA_EDITOR, Area.SCHEMA_DESIGNER} and defensive_available():
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


@pytest.mark.parametrize("query", ["?database_manager=true", "?areas=database_manager", "?write=1"])
def test_a_query_parameter_or_a_header_never_opens_the_routes(
    tmp_path: Path, static_dir: Path, folder: Path, query: str
) -> None:
    """Proof item 3: the Data Editor and Schema Designer flags are on; a query parameter, a header or a body key
    never adds the Database Manager (GS4-02)."""
    env = WriteEnv(tmp_path, static_dir, WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER})))
    try:
        headers = {**env.headers, "X-Tabrion-Write-Areas": "database_manager", "X-Tabrion-Database-Manager": "true"}
        target = folder / "new.db"
        refused = env.studio.client.post(
            f"{SQLITE_ROUTE}{query}",
            json={"path": str(target), "typed": "new.db", "areas": {"database_manager": True}},
            headers=headers,
        )
        _refused(refused, 405, "studio.method_not_allowed")
        assert not target.exists()
        assert env.send("GET", "/api/write/areas").json()["areas"]["database_manager"] is False
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
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write.database_manager')))\n"
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
    assert not target.exists() or areas.startswith("WriteAreas(frozenset({Area.DATABASE_MANAGER")
    return [lines[-2], *loaded]


@pytest.mark.parametrize(
    "areas",
    [
        "WriteAreas()",
        "WriteAreas(frozenset({Area.DATA_EDITOR}))",
        "WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))",
        "WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))",
    ],
)
def test_flag_off_nothing_of_the_area_is_imported(tmp_path: Path, areas: str) -> None:
    """GS4-01: with the Database Manager flag off (every flag off, or other areas' on, the Schema Designer's among
    them) nothing of the admin area, its database creation included, is loaded."""
    loaded = _modules(tmp_path, areas)
    assert loaded == ["405 studio.method_not_allowed 405 studio.method_not_allowed"]


def test_flag_on_the_area_is_loaded(tmp_path: Path) -> None:
    """The counterpart (the test above is not vacuous): the flag loads the area; the SQLite file is created and the
    PostgreSQL route asks for an unlock. On Windows the loaded area refuses SQLite creation for the platform."""
    loaded = _modules(tmp_path, "WriteAreas(frozenset({Area.DATABASE_MANAGER}))")
    sqlite = "201 None" if POSIX else "400 studio.schema_create_platform"
    assert loaded[0] == f"{sqlite} 405 studio.write_locked"
    assert "tabrion_studio.write.database_manager.create" in loaded


def test_the_areas_route_says_the_database_manager_is_off_by_default(tmp_path: Path, static_dir: Path) -> None:
    """Proof item 1 for the area: no flag source, so the page is told it is off (and hides the wizard)."""
    env = WriteEnv(tmp_path, static_dir)
    try:
        assert env.send("GET", "/api/write/areas").json()["areas"]["database_manager"] is False
    finally:
        env.close()


# --- 2. SQLite (STU-CR-2, GS3-21) ------------------------------------------------------------------------------------


@creates_files
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
    assert start["action"] == "dbm.create_sqlite" and start["area"] == "database_manager"
    assert start["objects"] == ["inventory.db"] and start["engine"] == "sqlite"
    assert result["result"] == "committed" and result["run"] == start["run"]
    # The folder never reaches the audit log (only the file name).
    assert str(folder) not in json.dumps(_records(env))


@creates_files
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


@creates_files
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


@creates_files
@pytest.mark.parametrize(("mode", "created"), [(0o777, False), (0o770, False), (0o702, False), (0o1777, True)])
def test_a_folder_above_that_others_may_write_is_refused_unless_sticky(
    env: WriteEnv, tmp_path: Path, mode: int, created: bool
) -> None:
    """Review P128-01: every folder above the target folder belongs to root or this user, and group and others may
    not write it unless its sticky bit is set (like OpenSSH's StrictModes)."""
    shared = tmp_path / "shared"
    shared.mkdir()
    mine = shared / "mine"
    mine.mkdir(mode=0o700)
    mine.chmod(0o700)
    shared.chmod(mode)
    try:
        response = _create(env, mine / "a.db")
        if created:
            assert response.status_code == 201, response.text
        else:
            _refused(response, 400, "studio.schema_folder_unsafe")
            assert list(mine.iterdir()) == []
    finally:
        shared.chmod(0o700)


def _info(mode: int, uid: int) -> os.stat_result:
    return os.stat_result((mode, 1, 1, 1, uid, 0, 0, 0, 0, 0))


@pytest.mark.parametrize(
    ("mode", "uid", "safe"),
    [
        (stat.S_IFDIR | 0o755, 0, True),
        (stat.S_IFDIR | 0o700, 1000, True),
        (stat.S_IFDIR | 0o755, 1001, False),  # another user's folder
        (stat.S_IFDIR | 0o775, 1000, False),
        (stat.S_IFDIR | 0o757, 0, False),
        (stat.S_IFDIR | 0o1777, 0, True),  # /tmp
        (stat.S_IFDIR | 0o1777, 1001, False),  # sticky, but another user's
        (stat.S_IFLNK | 0o777, 1000, False),
        (stat.S_IFREG | 0o700, 1000, False),
    ],
)
def test_which_folders_above_are_safe(mode: int, uid: int, safe: bool) -> None:
    from tabrion_studio.write.database_manager.create import ancestor_is_safe

    assert ancestor_is_safe(_info(mode, uid), 1000) is safe


@creates_files
def test_a_folder_above_replaced_by_a_link_after_the_check_creates_nothing_there(
    env: WriteEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P128-01: a folder on the path is replaced by a link to another folder after the checks. The file is
    created relative to the folder Studio holds open, the changed path is noticed before the database is opened, and
    the new file is removed again: nothing is left in either folder."""
    from tabrion_studio.write.database_manager import create as create_module

    base = tmp_path / "base"
    (base / "mine").mkdir(mode=0o700, parents=True)
    (base / "mine").chmod(0o700)
    other = tmp_path / "other"
    (other / "mine").mkdir(mode=0o700, parents=True)
    (other / "mine").chmod(0o700)
    moved = tmp_path / "moved"
    create_in = create_module._create_in

    def swapped(descriptor: int, name: str) -> tuple[int, int]:
        base.rename(moved)
        base.symlink_to(other, target_is_directory=True)
        return create_in(descriptor, name)

    monkeypatch.setattr(create_module, "_create_in", swapped)
    _refused(_create(env, base / "mine" / "a.db"), 400, "studio.schema_folder_unsafe")
    assert list((other / "mine").iterdir()) == []
    assert list((moved / "mine").iterdir()) == []
    results = [r["fields"]["result"] for r in _runs(env) if r["event"] == "run.result"]
    assert results == ["studio.schema_folder_unsafe"]


@creates_files
def test_the_file_is_checked_by_identity_before_it_is_opened(
    env: WriteEnv, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name is replaced by another file between the create and the open: refused before SQLite opens it, and
    the other file is neither opened, changed nor removed."""
    from tabrion_studio.write.database_manager import create as create_module

    target = folder / "a.db"
    create_in = create_module._create_in
    opened: list[str] = []

    def replaced(descriptor: int, name: str) -> tuple[int, int]:
        identity = create_in(descriptor, name)
        target.rename(folder / "kept.db")  # kept, so the replacement cannot reuse its inode
        target.write_bytes(CANARY_VALUE.encode())
        target.chmod(0o600)
        return identity

    monkeypatch.setattr(create_module, "_create_in", replaced)
    monkeypatch.setattr(create_module, "sqlite_connect", lambda path, **_: opened.append(path))
    _refused(_create(env, target), 400, "studio.schema_file_exists")
    assert opened == []
    assert target.read_bytes() == CANARY_VALUE.encode()


@pytest.mark.skipif(POSIX, reason="Windows only")
def test_windows_refuses_the_sqlite_creation(env: WriteEnv, folder: Path) -> None:
    """Open point 5 of the PR #128 review: on Windows Studio creates no SQLite file (it does not check a folder's
    access control list); a valid request is refused before anything is written or recorded."""
    _refused(_create(env, folder / "a.db"), 400, "studio.schema_create_platform")
    assert list(folder.iterdir()) == []
    assert _runs(env) == []


@creates_files
def test_a_missing_folder_is_refused_and_not_created(env: WriteEnv, folder: Path) -> None:
    _refused(_create(env, folder / "missing" / "a.db"), 400, "studio.schema_folder_unsafe")
    assert not (folder / "missing").exists()


@creates_files
def test_a_file_as_folder_is_refused(env: WriteEnv, folder: Path) -> None:
    (folder / "plain").write_text("x")
    _refused(_create(env, folder / "plain" / "a.db"), 400, "studio.schema_folder_unsafe")


@creates_files
@pytest.mark.parametrize("nested", [False, True], ids=["itself", "below"])
@pytest.mark.parametrize("which", ["config", "audit", "state", "log", "project"])
def test_studios_own_folders_are_refused(env: WriteEnv, which: str, nested: bool) -> None:
    """No database file in the configuration folder (where profiles.toml and write-areas.toml live), the audit
    log's folder, the state folder (Studio's own state database), the log folder or the project's .tabrion folder,
    nor in a folder below one of them (review P128-02)."""
    folder = {
        "config": env.config_dir,
        "audit": env.audit_dir,
        "state": env.state,
        "log": env.log_dir,
        "project": env.project / ".tabrion",
    }[which]
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    if nested:
        folder = folder / "inner"
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
    _refused(_create(env, folder / "studio.db"), 400, "studio.schema_folder_reserved")
    assert not (folder / "studio.db").exists()


@creates_files
def test_studios_own_folders_are_compared_by_identity(tmp_path: Path) -> None:
    """Review P128-02: the reserved folders are compared by device and inode, not by their spelling: a reserved folder
    named through a link (another name of the same folder) still covers the folder and everything below it."""
    from tabrion_studio.write.database_manager import create as create_module

    own = tmp_path / "own"
    (own / "inner").mkdir(mode=0o700, parents=True)
    own.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(own, target_is_directory=True)
    reserved = create_module.reserved_identities([alias, tmp_path / "missing"])
    assert reserved == {(os.stat(own).st_dev, os.stat(own).st_ino)}
    for folder in (own, own / "inner"):
        with pytest.raises(WriteError) as caught:
            create_module._check_folder(str(folder), reserved)
        assert caught.value.code == "studio.schema_folder_reserved"
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    other.chmod(0o700)
    assert create_module._check_folder(str(other), reserved).st_ino == os.stat(other).st_ino


@creates_files
@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="a bind mount needs root")
def test_a_bind_mount_of_a_reserved_folder_is_refused(env: WriteEnv, tmp_path: Path) -> None:
    """Review P128-02: a second name of the configuration folder (a bind mount: same device and inode, a path outside
    it) is refused; skipped where this user cannot mount."""
    env.config_dir.mkdir(mode=0o700, exist_ok=True)
    env.config_dir.chmod(0o700)
    alias = tmp_path / "mounted"
    alias.mkdir(mode=0o700)
    mounted = subprocess.run(["mount", "--bind", str(env.config_dir), str(alias)], capture_output=True, check=False)
    if mounted.returncode != 0:
        pytest.skip("mount --bind is not available here")
    try:
        _refused(_create(env, alias / "via-alias.db"), 400, "studio.schema_folder_reserved")
        assert not (env.config_dir / "via-alias.db").exists()
    finally:
        subprocess.run(["umount", str(alias)], check=True)


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("relative/a.db", "create.not_absolute"),
        # {root} is an absolute, normalized folder of this platform and {s} its separator (C:\... on Windows), so each
        # case reaches its own rule there too; nothing is created.
        ("{root}{s}..{s}x{s}a.db", "create.not_normalized"),
        ("{root}{s}{s}a.db", "create.not_normalized"),
        ("{root}{s}a.txt", "create.suffix"),
        ("{root}{s}a", "create.suffix"),
        ("{root}{s}a\x00.db", "create.control_character"),
        ("{root}{s}a\n.db", "create.control_character"),
        # Review P128-03: a space other than U+0020 (no-break, narrow no-break, ideographic) and the line and paragraph
        # separators look like a blank; U+0020 itself is a valid character of a file name.
        ("{root}{s}a\u00a0.db", "create.control_character"),
        ("{root}{s}a\u202f.db", "create.control_character"),
        ("{root}{s}a\u3000.db", "create.control_character"),
        ("{root}{s}a\u2028.db", "create.control_character"),
        ("//host/share/a.db", "create.network_path"),
        ("/net/host/a.db", "create.network_path"),
        ("", "create.required"),
        ("/" + "a" * 5000 + ".db", "create.too_long"),
    ],
)
def test_a_path_that_is_not_valid_is_refused_with_its_field(env: WriteEnv, folder: Path, path: str, code: str) -> None:
    path = path.replace("{root}", str(folder)).replace("{s}", os.sep)
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


@creates_files
def test_without_an_audit_record_nothing_is_created(
    env: WriteEnv, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GS3-27 on every platform: the run.start record cannot be written, so no file is created (fail closed)."""

    def refuse(path: Path, data: bytes) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(audit_module, "_append_bytes", refuse)
    _refused(_create(env, folder / "a.db"), 405, "studio.audit_unavailable")
    assert list(folder.iterdir()) == []


@pytest.mark.skipif(not POSIX, reason="POSIX modes: an audit folder others may write")
def test_without_an_audit_log_nothing_is_created(tmp_path: Path, static_dir: Path, folder: Path) -> None:
    """GS3-27: an audit folder others may write is refused, so no run.start record and no file (fail closed)."""
    (tmp_path / "config").mkdir(mode=0o700)
    (tmp_path / "config" / "audit").mkdir(mode=0o700)
    (tmp_path / "config" / "audit").chmod(0o777)
    env = WriteEnv(tmp_path, static_dir, ADMIN_AREA)
    try:
        _refused(_create(env, folder / "a.db"), 405, "studio.audit_unavailable")
        assert list(folder.iterdir()) == []
    finally:
        env.close()
        (tmp_path / "config" / "audit").chmod(0o700)


@creates_files
def test_a_file_that_appears_meanwhile_is_left_alone(
    env: WriteEnv, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing name is left unchanged (``O_EXCL``): a file made by someone else between Studio's check and the
    create is refused as existing and neither changed nor removed; the run is recorded as refused."""
    from tabrion_studio.write.database_manager import create as create_module

    target = folder / "race.db"
    create_in = create_module._create_in

    def planted(descriptor: int, name: str) -> tuple[int, int]:
        target.write_bytes(CANARY_VALUE.encode())
        target.chmod(0o600)
        return create_in(descriptor, name)

    monkeypatch.setattr(create_module, "_create_in", planted)
    _refused(_create(env, target), 400, "studio.schema_file_exists")
    assert target.read_bytes() == CANARY_VALUE.encode()
    results = [r["fields"]["result"] for r in _runs(env) if r["event"] == "run.result"]
    assert results == ["studio.schema_file_exists"]


@creates_files
def test_a_failure_after_the_create_removes_only_the_new_file(
    env: WriteEnv, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.database_manager import create as create_module

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
def test_a_write_profile_never_creates_a_database(tmp_path: Path, static_dir: Path) -> None:
    """STU-CR-ADMIN: with the Schema Designer and the Database Manager on, a write profile unlocked for the Schema
    Designer does not serve the admin area (``KIND_AREAS``): the PostgreSQL creation answers 405 and nothing is
    recorded."""
    env = WriteEnv(tmp_path, static_dir, WriteAreas(frozenset({Area.SCHEMA_DESIGNER, Area.DATABASE_MANAGER})))
    try:
        env.create_read()
        assert env.create_write().status_code == 201
        connection_id = env.open()
        assert env.unlock(connection_id).status_code == 200
        response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
        _refused(response, 405, "studio.method_not_allowed")
        assert [r for r in _runs(env) if r["fields"].get("area") == "database_manager"] == []
    finally:
        env.close()


@needs_defensive
def test_a_sqlite_connection_cannot_create_a_postgresql_database(env: WriteEnv) -> None:
    env.create_read()
    assert env.create_write(name="parts-admin", kind="admin").status_code == 201
    connection_id = env.open()
    assert env.unlock(connection_id, "write:parts-admin", "parts-admin").status_code == 200
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
    _refused(response, 400, "studio.schema_create_engine")
    assert _runs(env) == []


def test_a_prod_connection_is_refused(env: WriteEnv) -> None:
    """PROD from the stored profile: no admin profile can be coupled to it, a hand-written coupling is not used, and
    the route answers locked; an ``environment`` in the request is refused as an unknown key."""
    env.create_read(environment="prod")
    created = env.create_write(name="parts-admin", kind="admin")
    assert (created.status_code, created.json()["issues"][0]["code"]) == (400, "config.coupling_prod")
    env.write_file(
        (env.config_dir / "profiles.toml").read_text(encoding="utf-8")
        + '\n[[write_profile]]\nname = "parts-admin"\nkind = "admin"\nread_profile = "parts"\n'
    )
    connection_id = env.open(environment="prod")
    unlocked = env.unlock(connection_id, "write:parts-admin", "parts-admin")
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
    assert env.create_write(name="parts-admin", kind="admin").status_code == 201
    connection_id = env.open()
    assert env.unlock(connection_id, "write:parts-admin", "parts-admin").status_code == 200
    text = (env.config_dir / "profiles.toml").read_text(encoding="utf-8")
    env.write_file(text.replace('environment = "dev"', 'environment = "prod"'))
    response = env.send("POST", POSTGRES_ROUTE, {"connection_id": connection_id, "name": "n1", "typed": "n1"})
    # The profile file no longer couples the write profile (a PROD profile cannot have one): the unlock ends.
    _refused(response, 400, "studio.write_profile_unknown")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    assert [r for r in _runs(env) if r["fields"].get("area") == "database_manager"] == []


@pytest.mark.parametrize("environment", ["prod", "staging", None])
def test_only_dev_and_test_unlocks_reach_the_creation(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, environment: str | None
) -> None:
    """The second lock of the route itself (E-X7): an unlock whose stored environment is not dev or test is refused
    with ``studio.write_prod_locked`` even if the unlock's own checks let it through."""
    from tabrion_studio.write.database_manager import create as create_module

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
        # Review P128-03: a space other than U+0020 in a database or owner name.
        ({"name": "a\u00a0b"}, "name", "create.control_character"),
        ({"name": "a", "owner": "o\u2007x"}, "owner", "create.control_character"),
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
