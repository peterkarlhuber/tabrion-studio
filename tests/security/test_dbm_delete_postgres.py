"""STU-XDM-3 against PostgreSQL with the real ``pg_dump``: deleting a database of the unlocked server (GS4-03,
GS4-10, GS4-22; STU-ADR-0008 sections 5, 6 and 9; TX-37, TX-39).

* Another DEV database is deleted only after its verified ``pg_dump`` backup (origin ``pre_delete``), which then
  restores into a new database with the same rows; the audit records carry ``force`` and the sessions seen.
* Never the maintenance or template databases, never the database the admin connection is connected to, never one a
  PROD profile names (under an alias of the server), never a name the user cannot confirm by typing.
* Other sessions on the target refuse the delete; ``force`` (PostgreSQL 13 or later) is offered only then, needs its
  own typed confirmation and is never a default. Studio's own read connections to the target are closed first.
* A backup that fails (a table the admin role cannot read) and a database swapped under the same name after the
  backup stop the delete.

The fixture is the one of ``test_dbm_restore_roundtrip.py`` (marker ``postgres_superuser``); the target databases
are made by the test server's superuser for the admin role and dropped again by the fixture.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
import pytest
import sqlalchemy

from tests.security import test_dbm_restore_roundtrip as roundtrip
from tests.security.test_dbm_restore_roundtrip import PG_URL, Pg

pg = roundtrip.pg  # the fixture

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

DELETE_PREVIEW = "/api/write/database-manager/delete/preview"
DELETE = "/api/write/database-manager/delete"


def _victim(pg: Pg, suffix: str = "victim") -> str:
    """A database of the admin role with a table it owns and two rows."""
    name = f"{pg.prefix}_{suffix}"
    pg.sql(f"CREATE DATABASE {name} OWNER {pg.prefix}_adm")
    pg.sql("CREATE TABLE public.parts (id serial PRIMARY KEY, material text NOT NULL)", name)
    pg.sql("INSERT INTO public.parts (material) VALUES ('Steel'), ('Brass')", name)
    pg.sql(f"ALTER TABLE public.parts OWNER TO {pg.prefix}_adm", name)
    return name


def _preview(pg: Pg, name: object) -> Any:
    return pg.env.send("POST", DELETE_PREVIEW, {"connection_id": pg.connection_id, "name": name})


def _delete(pg: Pg, name: object, typed: object = None, **extra: Any) -> Any:
    body = {"connection_id": pg.connection_id, "name": name, "typed": name if typed is None else typed, **extra}
    return pg.env.send("POST", DELETE, body)


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


def _dumps(pg: Pg) -> list[str]:
    folder = pg.env.config_dir / "backups" / "database-manager"
    return sorted(p.name for p in folder.glob("*.dump")) if folder.is_dir() else []


def _deletes(pg: Pg) -> list[dict[str, Any]]:
    return [r for r in reversed(pg.runs()) if r["action"] == "dbm.delete"]


def _hold(pg: Pg, name: str) -> psycopg.Connection[Any]:
    """A session on ``name`` of the lean restore role (the admin role is a member of it, so ``force`` may end it)."""
    url = sqlalchemy.make_url(PG_URL or "")
    return psycopg.connect(
        host=url.host, port=url.port, dbname=name, user=f"{pg.prefix}_rst", password=pg.password, autocommit=True
    )


def test_a_database_is_deleted_after_its_verified_backup(pg: Pg, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    victim = _victim(pg)
    preview = _preview(pg, victim)
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "engine": "postgresql",
        "target": victim,
        "environment": "dev",
        "sessions": 0,
        "force_offered": False,
        "backup_required": True,
    }
    for typed in (victim[:-1], victim.upper(), f" {victim}", f"{victim}​"):
        _refused(_delete(pg, victim, typed), 400, "studio.dbm_delete_name_mismatch")
    assert victim in pg.databases() and _dumps(pg) == []
    response = _delete(pg, victim)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["engine"] == "postgresql" and doc["target"] == victim and doc["force"] is False
    assert doc["connections_closed"] == 0 and doc["audit_pending"] is False
    assert victim not in pg.databases()
    pre = doc["pre_backup"]
    folder = pg.env.config_dir / "backups" / "database-manager"
    assert _dumps(pg) == [pre] and (folder / pre).read_bytes().startswith(b"PGDMP")
    meta = json.loads((folder / f"{pre}.json").read_text())
    assert meta["origin"] == "pre_delete" and meta["source"]["database"] == victim
    assert meta["fingerprint"].startswith("v2:sha256:")
    start, result = _deletes(pg)
    assert start["target"] == victim and start["force"] is False and start["sessions"] == 0
    assert start["pre_backup"] == pre and start["sha256"] == meta["sha256"] and result["result"] == "deleted"
    backup_start = next(r for r in reversed(pg.runs()) if r["action"] == "dbm.backup")
    assert backup_start["objects"] == [victim]
    assert pg.password not in response.text and pg.password not in caplog.text
    # The backup is a real one: it restores (as the lean restore role) with the same rows.
    back = f"{pg.prefix}_back"
    restored = pg.restore(pre, back)
    assert restored.status_code == 201, restored.text
    assert pg.sql("SELECT material FROM public.parts ORDER BY id", back) == [("Steel",), ("Brass",)]


@pytest.mark.parametrize(
    ("name", "code", "reason"),
    [
        ("postgres", "studio.dbm_delete_not_allowed", "delete.maintenance"),
        ("template0", "studio.dbm_delete_not_allowed", "delete.maintenance"),
        ("template1", "studio.dbm_delete_not_allowed", "delete.maintenance"),
        ("{source}", "studio.dbm_delete_not_allowed", "delete.connected"),
        ("{prefix}_tpl", "studio.dbm_delete_not_allowed", "delete.maintenance"),
        ("{prefix}_none", "studio.dbm_delete_target_missing", None),
        ("{prefix}_x​", "studio.dbm_delete_name_invalid", None),
        (" {prefix}_victim", "studio.dbm_delete_name_invalid", None),
        ("x" * 64, "studio.dbm_delete_name_invalid", None),
        ("", "studio.dbm_delete_name_invalid", None),
        (7, "studio.dbm_delete_name_invalid", None),
    ],
)
def test_maintenance_template_connected_and_unusable_names_are_refused(
    pg: Pg, name: object, code: str, reason: str | None
) -> None:
    _victim(pg)
    pg.sql(f"CREATE DATABASE {pg.prefix}_tpl OWNER {pg.prefix}_adm IS_TEMPLATE true")
    if isinstance(name, str):
        name = name.format(source=pg.source, prefix=pg.prefix)
    before = pg.databases()
    issues = None if reason is None else [("delete.target", reason)]
    try:
        _refused(_preview(pg, name), 400, code, issues)
        _refused(_delete(pg, name), 400, code, issues)
        assert pg.databases() == before and _dumps(pg) == [] and _deletes(pg) == []
    finally:
        pg.sql(f"ALTER DATABASE {pg.prefix}_tpl IS_TEMPLATE false")  # the fixture drops it then
    if name in ("postgres", "template1"):
        assert pg.sql("SELECT count(*) FROM pg_database WHERE datname = %s", params=(name,)) == [(1,)]


def test_other_sessions_need_force_and_its_own_confirmation(pg: Pg) -> None:
    victim = _victim(pg)
    holder = _hold(pg, victim)
    try:
        preview = _preview(pg, victim)
        assert preview.status_code == 200, preview.text
        assert preview.json()["sessions"] == 1 and preview.json()["force_offered"] is True
        # No force: refused before any backup, and the other session is left alone.
        _refused(_delete(pg, victim), 409, "studio.dbm_delete_in_use", [("delete.target", "delete.in_use")])
        assert _dumps(pg) == [] and _deletes(pg) == []
        _refused(_delete(pg, victim, force="true"), 400, "studio.dbm_delete_name_invalid")
        _refused(_delete(pg, victim, force=1), 400, "studio.dbm_delete_name_invalid")
        _refused(_delete(pg, victim, force=True), 400, "studio.dbm_delete_name_mismatch")
        _refused(_delete(pg, victim, force=True, force_typed=victim[:-1]), 400, "studio.dbm_delete_name_mismatch")
        assert victim in pg.databases() and _dumps(pg) == []
        assert holder.execute("SELECT 1").fetchall() == [(1,)]
        response = _delete(pg, victim, force=True, force_typed=victim)
        assert response.status_code == 200, response.text
        assert response.json()["force"] is True
        assert victim not in pg.databases()
        with pytest.raises(psycopg.OperationalError):
            holder.execute("SELECT 1")
    finally:
        holder.close()
    start, result = _deletes(pg)
    assert start["force"] is True and start["sessions"] == 1 and result["result"] == "deleted"


def test_force_is_not_offered_without_other_sessions(pg: Pg) -> None:
    victim = _victim(pg)
    response = _delete(pg, victim, force=True, force_typed=victim)
    _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.force_not_offered")])
    assert victim in pg.databases() and _dumps(pg) == []


def test_studios_own_read_connection_to_the_target_is_closed_first(pg: Pg) -> None:
    victim = _victim(pg)
    url = sqlalchemy.make_url(PG_URL or "")
    read = {
        "name": "pg-victim",
        "engine": "postgresql",
        "host": url.host,
        "port": url.port,
        "database": victim,
        "user": url.username,
        "environment": "dev",
        "sslmode": "prefer",
        "password": "keyring:",
    }
    assert pg.env.send("POST", "/api/profiles", {"profile": read, "digest": pg.env.user_digest()}).status_code == 201
    stored = pg.env.send("PUT", "/api/profiles/user:pg-victim/secret", {"secret": url.password, "store": "session"})
    assert stored.status_code == 204
    opened = pg.env.open("user:pg-victim")
    assert pg.env.send("GET", f"/api/connections/{opened}/catalog").status_code == 200
    response = _delete(pg, victim)
    assert response.status_code == 200, response.text
    assert response.json()["connections_closed"] == 1 and response.json()["force"] is False
    assert victim not in pg.databases()


def test_a_database_a_prod_profile_names_under_an_alias_is_refused(pg: Pg) -> None:
    victim = _victim(pg)
    url = sqlalchemy.make_url(PG_URL or "")
    alias = "localhost" if url.host in ("127.0.0.1", "::1") else "127.0.0.1"
    prod = {
        "name": "pg-prod",
        "engine": "postgresql",
        "host": alias,
        "port": url.port,
        "database": victim,
        "user": url.username,
        "environment": "prod",
        "sslmode": "prefer",
    }
    assert pg.env.send("POST", "/api/profiles", {"profile": prod, "digest": pg.env.user_digest()}).status_code == 201
    for response in (_preview(pg, victim), _delete(pg, victim)):
        _refused(response, 400, "studio.dbm_delete_not_allowed", [("delete.target", "delete.prod")])
    assert victim in pg.databases() and _dumps(pg) == []


def test_a_failed_backup_stops_the_delete(pg: Pg) -> None:
    """Fail closed: the admin role cannot read one table of the target, so pg_dump fails; nothing is deleted (the
    fix is never a broader role, see the guide)."""
    victim = _victim(pg)
    pg.sql("CREATE TABLE public.secret_notes (id integer)", victim)  # the superuser's, not readable by the admin
    response = _delete(pg, victim)
    _refused(response, 400, "studio.backup_tool_failed", [("tool.pg_dump", "tool.permission")])
    assert victim in pg.databases() and _dumps(pg) == [] and _deletes(pg) == []


def test_a_schema_changed_during_the_dump_stops_the_delete(pg: Pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review L-1 of PR #140: a table created in the target while pg_dump runs leaves the backup without a
    fingerprint (before and after differ), so the archive does not match what is dropped: nothing is deleted."""
    from tabrion_studio.write import pgtools

    victim = _victim(pg)
    real = pgtools.Runner.dump

    def changing(self: Any, *args: Any, **kwargs: Any) -> Any:
        archive = real(self, *args, **kwargs)
        pg.sql("CREATE TABLE public.late (id integer)", victim)
        return archive

    monkeypatch.setattr(pgtools.Runner, "dump", changing)
    _refused(_delete(pg, victim), 400, "studio.dbm_delete_backup_unverified")
    assert victim in pg.databases() and _deletes(pg) == []
    (pre,) = _dumps(pg)  # the backup is kept, without a fingerprint
    folder = pg.env.config_dir / "backups" / "database-manager"
    assert json.loads((folder / f"{pre}.json").read_text())["fingerprint"] is None


def test_an_unreadable_fingerprint_stops_the_delete(pg: Pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review L-1 of PR #140: when the target's fingerprint cannot be read, the backup is not verified and nothing
    is dropped."""
    from tabrion_studio.write.database_manager import backups

    victim = _victim(pg)
    monkeypatch.setattr(backups, "_target_fingerprint", lambda *args, **kwargs: None)
    _refused(_delete(pg, victim), 400, "studio.dbm_delete_backup_unverified")
    assert victim in pg.databases() and _deletes(pg) == []


def test_a_database_swapped_after_the_backup_is_refused(pg: Pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """TX-37: another database under the same name after the backup (a different OID): nothing is dropped."""
    from tabrion_studio.write.database_manager import backups

    victim = _victim(pg)
    real = backups._backup_postgres

    def swap(*args: Any, **kwargs: Any) -> Any:
        made = real(*args, **kwargs)
        pg.sql(f"DROP DATABASE {victim}")
        pg.sql(f"CREATE DATABASE {victim} OWNER {pg.prefix}_adm")
        return made

    monkeypatch.setattr(backups, "_backup_postgres", swap)
    before = pg.sql("SELECT oid FROM pg_database WHERE datname = %s", params=(victim,))
    _refused(_delete(pg, victim), 400, "studio.dbm_delete_target_changed")
    after = pg.sql("SELECT oid FROM pg_database WHERE datname = %s", params=(victim,))
    assert after and after != before
    assert _deletes(pg) == []
