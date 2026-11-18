"""STU-XDM-3: deleting a database in the Database Manager, on SQLite (GS4-01..03, GS4-10, GS4-22; STU-ADR-0008
sections 2, 5 and 9; TX-37, TX-39). The PostgreSQL rules (maintenance, connected, sessions, ``force``) are in
``test_dbm_delete_postgres.py``.

1. **Kill switch (GS4-01, GS4-02).** Off by default: with no flag, or with only another area's flag, both routes
   answer 405 and nothing is deleted or backed up (the flag-off import check is in ``test_dbm_backup_restore.py``).
2. **The delete (GS4-10, GS4-22).** The exact file name typed, a verified backup first (origin ``pre_delete``, its
   rows equal), Studio's own connections and the unlock closed, the file gone, ``run.start`` and ``run.result`` of
   ``dbm.delete`` with ``force`` and the backup's name and SHA-256.
3. **Refusals (GS4-03).** Any environment but DEV and TEST, a file a PROD profile names (a hard link), a folder
   others may write, one of Studio's own folders, Windows, ``force`` or a name from the request, a wrong typed name,
   an unverified or failed backup, an unwritable audit log, a file another connection holds: nothing is deleted.

Python 3.11 refuses every SQLite write unlock (no defensive mode), so these tests skip there. The deletes themselves
run on POSIX only: Studio refuses them on Windows (asserted there).
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATABASE_MANAGER, WriteEnv, digest, make_database

pytestmark = pytest.mark.security

DELETE_PREVIEW = "/api/write/database-manager/delete/preview"
DELETE = "/api/write/database-manager/delete"
POSIX = sys.platform != "win32"
OTHER_AREAS = WriteAreas(frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}))
posix_only = pytest.mark.skipif(not POSIX, reason="Studio refuses a SQLite delete on Windows (STU-CR-2)")


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


class Dbm:
    def __init__(self, env: WriteEnv, connection_id: str) -> None:
        self.env, self.connection_id = env, connection_id

    @property
    def folder(self) -> Path:
        return self.env.config_dir / "backups" / "database-manager"

    def backups(self) -> list[Path]:
        return sorted(self.folder.glob("*.sqlite")) if self.folder.is_dir() else []

    def preview(self, **extra: Any) -> Any:
        return self.env.send("POST", DELETE_PREVIEW, {"connection_id": self.connection_id, **extra})

    def delete(self, typed: object = "parts.db", **extra: Any) -> Any:
        return self.env.send("POST", DELETE, {"connection_id": self.connection_id, "typed": typed, **extra})

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in records if r["event"] in ("run.start", "run.result")]

    def deletes(self) -> list[dict[str, Any]]:
        return [r for r in reversed(self.runs()) if r["action"] == "dbm.delete"]


def _env(tmp_path: Path, static_dir: Path, environment: str = "dev", path: Path | None = None) -> Dbm:
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    if path is not None:
        make_database(path)
    env.create_read(environment=environment, path=path)
    connection_id = env.open()
    created = env.create_write(name="parts-admin", kind="admin")
    assert created.status_code == 201, created.text
    unlocked = env.unlock(connection_id, "write:parts-admin", "parts-admin")
    assert unlocked.status_code == 200, unlocked.text
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


@pytest.mark.parametrize("areas", [None, OTHER_AREAS], ids=["default", "other-areas"])
@pytest.mark.parametrize("path", [DELETE_PREVIEW, DELETE])
def test_the_delete_is_off_by_default_and_with_another_areas_flag(
    tmp_path: Path, static_dir: Path, areas: WriteAreas | None, path: str
) -> None:
    env = WriteEnv(tmp_path, static_dir) if areas is None else WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        before = digest(env.db)
        body = {"connection_id": connection_id, "typed": "parts.db", "force": False}
        _refused(env.send("POST", path, body), 405, "studio.method_not_allowed")
        assert env.db.exists() and digest(env.db) == before
        assert not (env.config_dir / "backups").exists()
    finally:
        env.close()


def test_the_delete_needs_an_admin_unlock_and_known_fields(tmp_path: Path, static_dir: Path) -> None:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        env.create_read()
        connection_id = env.open()
        _refused(
            env.send("POST", DELETE, {"connection_id": connection_id, "typed": "parts.db"}), 405, "studio.write_locked"
        )
        _refused(
            env.send("POST", DELETE, {"connection_id": connection_id, "typed": "parts.db", "path": "/x"}),
            400,
            "access.bad_request",
        )
        assert env.db.exists()
    finally:
        env.close()


# --- 2. the delete (GS4-10, GS4-22) -------------------------------------------------------------------------------


def test_the_preview_names_the_file_and_changes_nothing(dbm: Dbm) -> None:
    before = digest(dbm.env.db)
    response = dbm.preview()
    if not POSIX:
        _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.platform")])
        return
    assert response.status_code == 200, response.text
    assert response.json() == {
        "engine": "sqlite",
        "target": "parts.db",
        "environment": "dev",
        "sessions": None,
        "force_offered": False,
        "backup_required": True,
    }
    assert digest(dbm.env.db) == before and dbm.backups() == []


@posix_only
def test_a_delete_backs_up_verifies_closes_and_deletes(dbm: Dbm) -> None:
    rows = _rows(dbm.env.db)
    response = dbm.delete()
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["engine"] == "sqlite" and doc["target"] == "parts.db" and doc["force"] is False
    assert doc["connections_closed"] == 1 and doc["audit_pending"] is False
    assert not dbm.env.db.exists() and not list(dbm.env.db.parent.glob("parts.db-*"))
    # The verified backup first (section 5), kept in Studio's backup folder.
    pre = doc["pre_backup"]
    assert [p.name for p in dbm.backups()] == [pre]
    assert _rows(dbm.folder / pre) == rows
    meta = json.loads((dbm.folder / f"{pre}.json").read_text())
    assert meta["origin"] == "pre_delete" and meta["fingerprint"]
    assert stat.S_IMODE((dbm.folder / pre).stat().st_mode) == 0o600
    # The unlock ended with Studio's connection to the file.
    assert dbm.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    actions = [(r["action"], r.get("result")) for r in reversed(dbm.runs())]
    assert actions[-4:] == [("dbm.backup", None), ("dbm.backup", "ok"), ("dbm.delete", None), ("dbm.delete", "deleted")]
    start, result = dbm.deletes()
    assert start["target"] == "parts.db" and start["pre_backup"] == pre and start["force"] is False
    assert start["sha256"] == meta["sha256"] and start["environment"] == "dev" and start["kind"] == "admin"
    assert result["result"] == "deleted"


@posix_only
def test_a_test_database_may_be_deleted_too(tmp_path: Path, static_dir: Path) -> None:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    dbm = _env(tmp_path, static_dir, environment="test")
    try:
        assert dbm.delete().status_code == 200
        assert not dbm.env.db.exists()
    finally:
        dbm.env.close()


# --- 3. refusals (GS4-03) -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["parts", "PARTS.DB", " parts.db", "parts.db ", "parts.db​", None, 7])
def test_a_wrong_typed_name_deletes_nothing(dbm: Dbm, typed: object) -> None:
    before = digest(dbm.env.db)
    response = dbm.delete(typed)
    if POSIX:
        _refused(response, 400, "studio.dbm_delete_name_mismatch")
    else:
        _refused(response, 400, "studio.dbm_delete_not_allowed")
    assert digest(dbm.env.db) == before and dbm.backups() == [] and dbm.deletes() == []


@posix_only
@pytest.mark.parametrize(
    ("extra", "code", "reason"),
    [
        ({"force": True, "force_typed": "parts.db"}, "studio.dbm_delete_not_allowed", "delete.force_not_offered"),
        ({"force": "yes"}, "studio.dbm_delete_not_allowed", "delete.force_not_offered"),
        ({"name": "other.db"}, "studio.dbm_delete_not_allowed", "delete.target"),
        ({"name": "parts.db"}, "studio.dbm_delete_not_allowed", "delete.target"),
    ],
)
def test_force_and_a_name_from_the_request_are_refused_on_sqlite(
    dbm: Dbm, extra: dict[str, Any], code: str, reason: str
) -> None:
    before = digest(dbm.env.db)
    _refused(dbm.delete(**extra), 400, code, [("delete.target", reason)])
    assert digest(dbm.env.db) == before and dbm.backups() == []


@pytest.mark.parametrize("environment", ["prod", "stage", "", None])
def test_only_a_dev_or_test_unlock_may_delete(
    dbm: Dbm, monkeypatch: pytest.MonkeyPatch, environment: str | None
) -> None:
    """Section 5: only the stored environments DEV and TEST. Studio's profiles know DEV, TEST and PROD, and PROD has no
    unlock at all (E-X7): an unlock that reached the area with any other environment is refused all the same."""
    from tabrion_studio.write.database_manager import backups, delete

    assert frozenset({"dev", "test"}) == delete.DELETE_ENVIRONMENTS
    real = backups._unlock

    def other(manager: Any, session: str, connection_id: object) -> Any:
        unlock = real(manager, session, connection_id)
        monkeypatch.setattr(unlock, "environment", environment)
        return unlock

    monkeypatch.setattr(backups, "_unlock", other)
    before = digest(dbm.env.db)
    for response in (dbm.preview(), dbm.delete()):
        _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.environment")])
    assert digest(dbm.env.db) == before and dbm.backups() == []


@posix_only
def test_a_file_that_a_prod_profile_names_is_refused(dbm: Dbm) -> None:
    """L-1: a PROD profile of the user's store names the same file (here through a hard link)."""
    alias = dbm.env.tmp_path / "alias.db"
    os.link(dbm.env.db, alias)
    dbm.env.create_read(name="parts-prod", environment="prod", path=alias)
    before = digest(dbm.env.db)
    for response in (dbm.preview(), dbm.delete()):
        _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.prod")])
    assert digest(dbm.env.db) == before and dbm.backups() == []


@posix_only
def test_a_file_in_a_folder_others_may_write_is_refused(dbm: Dbm) -> None:
    folder = dbm.env.db.parent
    before = stat.S_IMODE(os.stat(folder).st_mode)
    folder.chmod(0o777)
    try:
        response = dbm.delete()
    finally:
        folder.chmod(before)
    _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.folder_unsafe")])
    assert dbm.env.db.exists() and dbm.backups() == []


@posix_only
def test_a_file_in_one_of_studios_own_folders_is_refused(tmp_path: Path, static_dir: Path) -> None:
    """Studio's configuration folder (by device and inode) never holds a file the Database Manager deletes."""
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    inside = tmp_path / "config" / "inside.db"
    dbm = _env(tmp_path, static_dir, path=inside)
    try:
        response = dbm.delete("inside.db")
        _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.reserved_folder")])
        assert inside.exists()
    finally:
        dbm.env.close()


def test_a_sqlite_delete_is_refused_on_windows(dbm: Dbm, monkeypatch: pytest.MonkeyPatch) -> None:
    """STU-CR-2 refuses a new SQLite file on Windows (owner and modes cannot be read); the delete follows it."""
    from tabrion_studio.write.database_manager import delete

    assert delete._WINDOWS is (sys.platform == "win32")
    monkeypatch.setattr(delete, "_WINDOWS", True)
    before = digest(dbm.env.db)
    for response in (dbm.preview(), dbm.delete()):
        _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.platform")])
    assert digest(dbm.env.db) == before and dbm.backups() == []


@posix_only
def test_an_unverified_backup_stops_the_delete(dbm: Dbm, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.database_manager import backups

    real = backups._sqlite_file_fingerprint

    def other(path: Path, limits: Any) -> str | None:
        found = real(path, limits)
        return None if found is None else found + "0"

    monkeypatch.setattr(backups, "_sqlite_file_fingerprint", other)
    before = digest(dbm.env.db)
    _refused(dbm.delete(), 400, "studio.dbm_delete_backup_unverified")
    assert digest(dbm.env.db) == before and dbm.deletes() == []


@posix_only
def test_a_failed_backup_stops_the_delete(dbm: Dbm, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.database_manager import backups

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(backups, "sqlite_backup", fail)
    before = digest(dbm.env.db)
    response = dbm.delete()
    _refused(response, 400, "studio.dbm_backup_failed")
    assert "disk full" not in response.text
    assert digest(dbm.env.db) == before and dbm.deletes() == []


@posix_only
def test_an_unwritable_audit_log_stops_the_delete(dbm: Dbm, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.audit import AuditError

    real = dbm.env.unlocks.audit.append

    def refuse_delete(event: str, **fields: Any) -> Any:
        if fields.get("action") == "dbm.delete":
            raise AuditError()
        return real(event, **fields)

    monkeypatch.setattr(dbm.env.unlocks.audit, "append", refuse_delete)
    before = digest(dbm.env.db)
    _refused(dbm.delete(), 405, "studio.audit_unavailable")
    assert digest(dbm.env.db) == before


@posix_only
def test_a_file_another_connection_holds_is_not_deleted(dbm: Dbm) -> None:
    """Tabrion's exclusive-lock check: a connection outside Studio holds the file; Studio never ends it."""
    holder = sqlite3.connect(dbm.env.db, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        response = dbm.delete()
        _refused(response, 409, "studio.dbm_delete_in_use", [("delete.target", "delete.in_use")])
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert _rows(dbm.env.db) == [(1, "Steel")]
    start, result = dbm.deletes()
    assert start["pre_backup"] and result["result"] == "studio.dbm_delete_in_use"


@posix_only
def test_a_target_swapped_after_the_check_is_refused(dbm: Dbm, monkeypatch: pytest.MonkeyPatch) -> None:
    """TX-37: the file behind the unlock is replaced while the backup runs: nothing is deleted."""
    from tabrion_studio.write.database_manager import delete

    real = delete._verified_sqlite_backup
    swapped = dbm.env.tmp_path / "swapped.db"

    def swap(manager: Any, unlock: Any) -> Any:
        made = real(manager, unlock)
        swapped.write_bytes(dbm.env.db.read_bytes())
        os.replace(swapped, dbm.env.db)
        return made

    monkeypatch.setattr(delete, "_verified_sqlite_backup", swap)
    response = dbm.delete()
    _refused(response, 400, "studio.dbm_delete_target_changed")
    assert dbm.env.db.exists()
