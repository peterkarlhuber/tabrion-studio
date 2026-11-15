"""STU-XDM-2: the Database Manager's backup and restore through its routes, on SQLite (GS4-01..03, GS4-07..09,
GS4-22; STU-ADR-0008 sections 2 to 4 and 9; TX-35..TX-38). The PostgreSQL round trips and the restore role are in
``test_dbm_restore_roundtrip.py``; the tool runner in ``test_dbm_tools.py``.

1. **Kill switch (GS4-01, GS4-02).** With only another area's flag on, every route of the area answers 405 and none
   of its modules (the area, the tool runner, the backup folder) is imported.
2. **Backup files (GS4-07).** The folder 0700, the files 0600, metadata with size, SHA-256, engine, fingerprint and
   the scope notice; no secret; the audit records ``run.start`` and ``run.result``; an unwritable audit log stops it.
3. **Restore round trips (GS4-08)** into a new file and over the profile's own DEV file (its backup first, Studio's
   own connections to it closed; review I-4), with tabrion's ``integrity_check`` and the fingerprint compared.
4. **Refusals (GS4-03, GS4-09).** A target a PROD profile names (by device and inode), a STAGE unlock, a wrong typed
   name, a backup from outside the folder, a link, an edited archive or metadata file, a missing metadata file, a
   target swapped before the run; nothing changes.

Python 3.11 refuses every SQLite write unlock (no defensive mode), so these tests skip there.
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
from tabrion_studio.write.connection import defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security

BACKUPS = "/api/write/database-manager/backups"
BACKUP = "/api/write/database-manager/backup"
PREVIEW = "/api/write/database-manager/restore/preview"
RESTORE = "/api/write/database-manager/restore"
ROOT = Path(__file__).resolve().parents[2]
POSIX = sys.platform != "win32"
OTHER_AREAS = WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


def _rows(path: Path) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT id, material FROM parts ORDER BY id").fetchall()
    finally:
        connection.close()


def _insert(path: Path, row_id: int, material: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("INSERT INTO parts VALUES (?, ?)", (row_id, material))
        connection.commit()
    finally:
        connection.close()


class Dbm:
    def __init__(self, env: WriteEnv, connection_id: str) -> None:
        self.env, self.connection_id = env, connection_id

    @property
    def folder(self) -> Path:
        return self.env.config_dir / "backups" / "database-manager"

    def backup(self) -> Any:
        return self.env.send("POST", BACKUP, {"connection_id": self.connection_id})

    def made(self) -> str:
        response = self.backup()
        assert response.status_code == 201, response.text
        name: str = response.json()["backup"]["file"]
        return name

    def preview(self, backup_id: object) -> Any:
        return self.env.send("POST", PREVIEW, {"connection_id": self.connection_id, "backup_id": backup_id})

    def restore(self, backup_id: object, target: str, name: str | None, typed: str | None) -> Any:
        body: dict[str, Any] = {"connection_id": self.connection_id, "backup_id": backup_id, "target": target}
        if name is not None:
            body["name"] = name
        if typed is not None:
            body["typed"] = typed
        return self.env.send("POST", RESTORE, body)

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in records if r["event"] in ("run.start", "run.result")]

    def reconnect(self) -> None:
        """After a restore over the profile's file Studio closed its connections to it (I-4): open and unlock again."""
        self.connection_id = self.env.open()
        assert self.env.unlock(self.connection_id, "write:parts-admin", "parts-admin").status_code == 200


def _env(tmp_path: Path, static_dir: Path, environment: str = "dev") -> Dbm:
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    env.create_read(environment=environment)
    connection_id = env.open()
    created = env.create_write(name="parts-admin", kind="admin")
    assert created.status_code == 201, created.text
    assert env.unlock(connection_id, "write:parts-admin", "parts-admin").status_code == 200
    return Dbm(env, connection_id)


@pytest.fixture
def dbm(tmp_path: Path, static_dir: Path) -> Iterator[Dbm]:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    found = _env(tmp_path, static_dir)
    try:
        yield found
    finally:
        found.env.close()


# --- 1. the kill switch (GS4-01, GS4-02) --------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), [("GET", BACKUPS), ("POST", BACKUP), ("POST", PREVIEW), ("POST", RESTORE)])
def test_another_areas_flag_never_opens_the_database_manager(
    tmp_path: Path, static_dir: Path, method: str, path: str
) -> None:
    env = WriteEnv(tmp_path, static_dir, OTHER_AREAS)
    try:
        env.create_read()
        connection_id = env.open()
        body = {"connection_id": connection_id, "backup_id": "20261115T000000Z-00000000.sqlite", "target": "new"}
        response = env.send(method, path, None if method == "GET" else body)
        _refused(response, 405, "studio.method_not_allowed")
        assert not (env.config_dir / "backups").exists()
        assert not (env.config_dir / "tool-runs").exists()
    finally:
        env.close()


def test_with_the_flag_off_no_module_of_the_area_is_imported(tmp_path: Path) -> None:
    """GS4-01: the Data Editor and the Schema Designer are on, the Database Manager is off; its routes answer 405 and
    neither the area, the tool runner nor the backup folder module is loaded (a fresh interpreter)."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        "areas = WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), areas)\n"
        "env.create_read()\n"
        "c = env.open()\n"
        "for m, p in [('GET', '/api/write/database-manager/backups'), ('POST', '/api/write/database-manager/backup'),\n"
        "             ('POST', '/api/write/database-manager/restore/preview'),\n"
        "             ('POST', '/api/write/database-manager/restore')]:\n"
        "    assert env.send(m, p, None if m == 'GET' else {'connection_id': c}).status_code == 405\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write')))\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout.strip().splitlines()[-1].replace("'", '"'))
    assert "tabrion_studio.write.unlock" in loaded  # the write package is loaded: the check is not vacuous
    for module in (
        "tabrion_studio.write.database_manager",
        "tabrion_studio.write.database_manager.backups",
        "tabrion_studio.write.pgtools",
        "tabrion_studio.write.backup_store",
    ):
        assert module not in loaded


def test_no_setting_or_profile_names_a_tool(tmp_path: Path, static_dir: Path) -> None:
    """GS4-04: the tool paths come only from the owner's database-tools.toml; the settings route and a profile
    refuse such a key, so neither a settings save nor a project file can set a tool."""
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        saved = env.send("PUT", "/api/settings", {"settings": {"pg_dump": "/bin/sh"}, "digest": None})
        assert saved.status_code == 422 and saved.json()["code"] == "studio.settings_invalid"
        profile = {"name": "p", "engine": "sqlite", "path": str(env.db), "environment": "dev", "pg_dump": "/bin/sh"}
        created = env.send("POST", "/api/profiles", {"profile": profile, "digest": env.user_digest()})
        assert created.status_code in (400, 422) and "pg_dump" not in created.text
        assert not (env.config_dir / "database-tools.toml").exists()
    finally:
        env.close()


# --- 2. backup files (GS4-07, GS4-22) -----------------------------------------------------------------------------


def test_a_backup_is_private_described_and_audited(dbm: Dbm, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    response = dbm.backup()
    assert response.status_code == 201, response.text
    answer = response.json()
    name = answer["backup"]["file"]
    assert name.endswith(".sqlite") and "parts" not in name  # Studio's own name, never the user's
    archive, meta_file = dbm.folder / name, dbm.folder / f"{name}.json"
    assert archive.is_file() and meta_file.is_file()
    if POSIX:
        for folder in (dbm.folder, dbm.folder.parent):
            assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        for file in (archive, meta_file):
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
    meta = json.loads(meta_file.read_text())
    assert meta["engine"] == "sqlite" and meta["format"] == "sqlite" and meta["origin"] == "database_manager"
    assert meta["size"] == archive.stat().st_size and meta["sha256"] == digest(archive)
    assert meta["fingerprint"].startswith("v2:sha256:")
    assert meta["source"]["database"] == "parts.db" and meta["source"]["environment"] == "dev"
    assert meta["scope_notice"] and meta["complete"] is True
    assert _rows(archive) == _rows(dbm.env.db)
    runs = dbm.runs()
    start, result = runs[1], runs[0]
    assert start["action"] == "dbm.backup" and start["area"] == "database_manager"
    assert start["backup_file"] == name and start["objects"] == ["parts.db"]
    assert result["result"] == "ok" and result["sha256"] == meta["sha256"] and result["run"] == start["run"]
    text = meta_file.read_text() + caplog.text + response.text
    assert CANARY_SECRET not in text and "Steel" not in text


def test_the_listing_shows_backups_their_size_and_the_tools(dbm: Dbm) -> None:
    first, second = dbm.made(), dbm.made()
    listing = dbm.env.send("GET", BACKUPS)
    assert listing.status_code == 200, listing.text
    doc = listing.json()
    assert {b["file"] for b in doc["backups"]} == {first, second}
    assert doc["count"] == 2
    assert doc["total_bytes"] == sum((dbm.folder / n).stat().st_size for n in (first, second))
    # The listing names the source's database and profile, never its address (a host or a file path).
    assert set(doc["backups"][0]["source"]) == {"database", "profile", "environment"}
    assert str(dbm.env.tmp_path) not in listing.text
    # No database-tools.toml: neither tool is usable, and the page is told why (never a path).
    assert doc["tools"]["pg_dump"]["ok"] is False
    assert doc["tools"]["pg_dump"]["reason"] in ("tool.not_configured", "tool.platform")
    assert dbm.env.send("GET", BACKUPS + "?limit=1").status_code == 400


def test_an_unwritable_audit_log_stops_the_backup(dbm: Dbm, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.audit import AuditError

    def refuse(*_args: Any, **_kwargs: Any) -> int:
        raise AuditError()

    monkeypatch.setattr(dbm.env.unlocks.audit, "append", refuse)
    _refused(dbm.backup(), 405, "studio.audit_unavailable")
    assert not dbm.folder.exists() or not any(dbm.folder.iterdir())


def test_a_backup_needs_an_admin_unlock(tmp_path: Path, static_dir: Path) -> None:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        env.create_read()
        connection_id = env.open()
        _refused(env.send("POST", BACKUP, {"connection_id": connection_id}), 405, "studio.write_locked")
        _refused(env.send("POST", BACKUP, {"connection_id": connection_id, "path": "/x"}), 400, "access.bad_request")
    finally:
        env.close()


# --- 3. restore round trips (GS4-08) ------------------------------------------------------------------------------


def test_a_backup_restores_into_a_new_file(dbm: Dbm) -> None:
    name = dbm.made()
    preview = dbm.preview(name)
    assert preview.status_code == 200, preview.text
    assert preview.json()["targets"] == ["new", "existing"] and preview.json()["existing_name"] == "parts.db"
    _refused(dbm.restore(name, "new", "copy.db", "copy"), 400, "studio.dbm_restore_name_mismatch")
    response = dbm.restore(name, "new", "copy.db", "copy.db")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["verified"] is True and doc["fingerprint_after"] == doc["fingerprint_expected"]
    assert doc["pre_backup"] is None and doc["connections_closed"] == 0
    copy = dbm.env.db.parent / "copy.db"
    assert _rows(copy) == _rows(dbm.env.db)
    if POSIX:
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    runs = dbm.runs()
    start, result = runs[1], runs[0]
    assert start["action"] == "dbm.restore" and start["target"] == "copy.db" and start["backup_file"] == name
    assert result["result"] == "restored" and result["verified"] is True
    # A second restore under the same name: the file exists now.
    _refused(dbm.restore(name, "new", "copy.db", "copy.db"), 400, "studio.dbm_restore_target_exists")


def test_a_backup_restores_over_the_dev_file_after_its_own_backup(dbm: Dbm) -> None:
    name = dbm.made()
    before = _rows(dbm.env.db)
    _insert(dbm.env.db, 2, "Brass")
    response = dbm.restore(name, "existing", None, "parts.db")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["verified"] is True and doc["connections_closed"] == 1
    assert _rows(dbm.env.db) == before
    # The target's own backup first (section 4), with the row that the restore replaced.
    pre = doc["pre_backup"]
    assert pre and pre != name and (2, "Brass") in _rows(dbm.folder / pre)
    assert json.loads((dbm.folder / f"{pre}.json").read_text())["origin"] == "pre_restore"
    # I-4: Studio's own connection to the file was closed, and with it the unlock.
    assert dbm.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    actions = [(r["action"], r.get("result")) for r in reversed(dbm.runs())]
    assert actions[-4:] == [
        ("dbm.backup", None),
        ("dbm.backup", "ok"),
        ("dbm.restore", None),
        ("dbm.restore", "restored"),
    ]
    dbm.reconnect()
    assert dbm.env.send("POST", BACKUP, {"connection_id": dbm.connection_id}).status_code == 201


# --- 4. refusals (GS4-03, GS4-09) ---------------------------------------------------------------------------------


def test_a_target_that_a_prod_profile_names_is_refused(dbm: Dbm) -> None:
    """GS4-03, L-1: a PROD profile of the user's store names the same file (here through a hard link): refused,
    whichever profile the request came through; nothing changes."""
    name = dbm.made()
    alias = dbm.env.tmp_path / "alias.db"
    os.link(dbm.env.db, alias)
    dbm.env.create_read(name="parts-prod", environment="prod", path=alias)
    _insert(dbm.env.db, 2, "Brass")
    before = digest(dbm.env.db)
    _refused(
        dbm.restore(name, "existing", None, "parts.db"),
        400,
        "studio.dbm_restore_not_allowed",
        [("restore.target", "restore.prod")],
    )
    assert digest(dbm.env.db) == before


def test_a_test_database_is_a_restore_target_too(tmp_path: Path, static_dir: Path) -> None:
    """Section 4: DEV and TEST (the only environments besides PROD, which has no unlock, E-X7)."""
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    dbm = _env(tmp_path, static_dir, environment="test")
    try:
        name = dbm.made()
        _insert(dbm.env.db, 2, "Brass")
        response = dbm.restore(name, "existing", None, "parts.db")
        assert response.status_code == 201 and response.json()["verified"] is True, response.text
        assert _rows(dbm.env.db) == [(1, "Steel")]
    finally:
        dbm.env.close()


@pytest.mark.parametrize(
    "backup_id",
    ["../parts.db", "20261115T000000Z-00000000.sqlite", "x.sqlite", "/etc/passwd", 7, None],
)
def test_only_a_backup_of_the_folder_is_restored(dbm: Dbm, backup_id: object) -> None:
    dbm.made()
    _refused(dbm.restore(backup_id, "new", "copy.db", "copy.db"), 400, "studio.dbm_backup_unknown")
    assert not (dbm.env.db.parent / "copy.db").exists()


def test_an_edited_archive_or_metadata_file_is_refused(dbm: Dbm) -> None:
    name = dbm.made()
    archive, meta_file = dbm.folder / name, dbm.folder / f"{name}.json"
    original = archive.read_bytes()
    # The same size, other bytes (past the header): the SHA-256 differs.
    edited = bytearray(original)
    edited[-1] ^= 0xFF
    archive.write_bytes(bytes(edited))
    for target, name_given in (("new", "copy.db"), ("existing", None)):
        typed = name_given or "parts.db"
        _refused(
            dbm.restore(name, target, name_given, typed),
            400,
            "studio.dbm_backup_invalid",
            [("backup.file", "backup.changed")],
        )
    archive.write_bytes(original)
    meta = json.loads(meta_file.read_text())
    meta["size"] = meta["size"] + 1
    meta_file.write_text(json.dumps(meta))
    _refused(dbm.preview(name), 400, "studio.dbm_backup_invalid", [("backup.file", "backup.changed")])
    meta["size"] -= 1
    meta["engine"] = "postgresql"
    meta_file.write_text(json.dumps(meta))
    _refused(dbm.preview(name), 400, "studio.dbm_backup_invalid", [("backup.file", "backup.metadata_invalid")])
    meta_file.unlink()
    _refused(dbm.preview(name), 400, "studio.dbm_backup_invalid", [("backup.file", "backup.metadata_missing")])
    assert not (dbm.env.db.parent / "copy.db").exists()


@pytest.mark.skipif(not POSIX, reason="a symbolic link needs a privilege on Windows")
def test_a_backup_that_is_a_link_is_refused(dbm: Dbm) -> None:
    name = dbm.made()
    archive = dbm.folder / name
    elsewhere = dbm.env.tmp_path / "elsewhere.sqlite"
    archive.rename(elsewhere)
    archive.symlink_to(elsewhere)
    _refused(dbm.preview(name), 400, "studio.dbm_backup_invalid", [("backup.file", "backup.link")])
    _refused(dbm.restore(name, "new", "copy.db", "copy.db"), 400, "studio.dbm_backup_invalid")


@pytest.mark.parametrize("name", ["copy.txt", "../copy.db", ".hidden.db", "a/b.db", "copy‮.db", ""])
def test_a_new_file_needs_a_plain_name(dbm: Dbm, name: str) -> None:
    backup = dbm.made()
    _refused(dbm.restore(backup, "new", name, name), 400, "studio.dbm_restore_name_invalid")


def test_a_target_swapped_before_the_run_is_refused(dbm: Dbm) -> None:
    """TX-37: the file behind the unlock was replaced (another inode under the same name): refused before anything
    is written, and the swapped-in file is left as it is."""
    name = dbm.made()
    swapped = dbm.env.tmp_path / "swapped.db"
    swapped.write_bytes(dbm.env.db.read_bytes())
    # Studio's handles closed first, as when the pools are idle between two requests: the window in which the file
    # can be swapped. While a handle is open, Windows refuses to move the file at all (asserted there).
    write = dbm.env.unlocks.require(dbm.env.session(), dbm.connection_id, Area.DATABASE_MANAGER)
    if sys.platform == "win32":
        with pytest.raises(PermissionError):
            os.replace(swapped, dbm.env.db)
    dbm.env.registry.get(dbm.connection_id)._db.engine.dispose()
    write._db.engine.dispose()
    os.replace(swapped, dbm.env.db)
    before = digest(dbm.env.db)
    response = dbm.restore(name, "existing", None, "parts.db")
    assert response.status_code in (400, 405) and response.json()["code"] in (
        "studio.write_target_changed",
        "studio.dbm_restore_target_changed",
    )
    assert digest(dbm.env.db) == before


def test_a_wrong_typed_name_restores_nothing(dbm: Dbm) -> None:
    name = dbm.made()
    _insert(dbm.env.db, 2, "Brass")
    before = digest(dbm.env.db)
    for typed in ("parts", "PARTS.DB", " parts.db", None):
        _refused(dbm.restore(name, "existing", None, typed), 400, "studio.dbm_restore_name_mismatch")
    _refused(dbm.restore(name, "elsewhere", None, "parts.db"), 400, "studio.dbm_restore_not_allowed")
    assert digest(dbm.env.db) == before
    assert len(list(dbm.folder.glob("*.sqlite"))) == 1  # no backup of the target was taken for a refused restore
