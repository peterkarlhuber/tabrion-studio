"""STU-XDM-2 against PostgreSQL with the real ``pg_dump`` and ``pg_restore``: the mandatory restore round trips
(E-X5, GS4-08) and the PostgreSQL rules of GS4-03..07, GS4-09 and GS4-22 (STU-ADR-0008 sections 3, 4 and 6; review
M-1, L-1 and L-3 of STU-XDM-1).

* A backup of a database (rows, a check constraint, an index, a sequence) restores into a **new** database that the
  admin role creates for the **lean restore role**; the level-2 fingerprints are equal, the rows too.
* The restore role (M-1): missing, the admin profile itself, or a role with CREATEROLE: refused, nothing created. An
  archive whose check function tries ``CREATE ROLE`` creates no role when it is restored.
* A database name that a PROD profile names under an alias of the server (``localhost`` for ``127.0.0.1``): refused
  (L-1). The existing database is never a target on PostgreSQL; an existing name is refused without server text.
* Planted ``PGHOST``, ``PGSSLMODE`` and ``PGPASSWORD`` do not redirect the tool; the admin password reaches no answer,
  metadata, audit record or log; an admin role made superuser after the unlock is refused (section 6).

The roles and databases are made by the test server's superuser and dropped again (marker ``postgres_superuser``).
The tools are the newest ``/usr/lib/postgresql/<n>/bin`` ones, or those of ``TABRION_STUDIO_TEST_PG_BIN``, or the
real path of the ones on ``PATH`` (found by the test only: Studio itself never looks at ``PATH``).
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.write_areas import Area, WriteAreas
from tests.support.write import WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
BACKUPS = "/api/write/database-manager/backups"
BACKUP = "/api/write/database-manager/backup"
PREVIEW = "/api/write/database-manager/restore/preview"
RESTORE = "/api/write/database-manager/restore"
DATABASE_MANAGER = WriteAreas(frozenset({Area.DATABASE_MANAGER}))


def find_tool(name: str) -> Path | None:
    candidates: list[Path] = []
    explicit = os.environ.get("TABRION_STUDIO_TEST_PG_BIN")
    if explicit:
        candidates.append(Path(explicit) / name)
    versions = Path("/usr/lib/postgresql")
    if versions.is_dir():
        found = [p for p in versions.glob(f"*/bin/{name}") if p.parts[-3].isdigit()]
        candidates += sorted(found, key=lambda p: int(p.parts[-3]), reverse=True)
    on_path = shutil.which(name)
    if on_path:
        candidates.append(Path(os.path.realpath(on_path)))
    for candidate in candidates:
        real = Path(os.path.realpath(candidate))
        if real == candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


class Pg:
    def __init__(self, env: WriteEnv, connection_id: str, admin: sqlalchemy.Engine, prefix: str, password: str) -> None:
        self.env, self.connection_id, self.admin, self.prefix, self.password = (
            env,
            connection_id,
            admin,
            prefix,
            password,
        )

    @property
    def source(self) -> str:
        return f"{self.prefix}_src"

    def sql(self, statement: str, database: str | None = None, params: tuple[Any, ...] = ()) -> list[Any]:
        engine = self.admin
        if database is not None:
            engine = sqlalchemy.create_engine(
                self.admin.url.set(database=database), isolation_level="AUTOCOMMIT", poolclass=sqlalchemy.pool.NullPool
            )
        try:
            with engine.connect() as connection:
                result = connection.exec_driver_sql(statement, params)
                return list(result.all()) if result.returns_rows else []
        finally:
            if database is not None:
                engine.dispose()

    def backup(self) -> Any:
        return self.env.send("POST", BACKUP, {"connection_id": self.connection_id})

    def made(self) -> str:
        response = self.backup()
        assert response.status_code == 201, response.text
        name: str = response.json()["backup"]["file"]
        return name

    def restore(self, backup_id: str, name: str, role: str | None = "pg-rst", **extra: Any) -> Any:
        body: dict[str, Any] = {
            "connection_id": self.connection_id,
            "backup_id": backup_id,
            "target": "new",
            "name": name,
            "typed": name,
            **extra,
        }
        if role is not None:
            body["restore_profile_id"] = f"write:{role}"
        return self.env.send("POST", RESTORE, body)

    def databases(self) -> set[str]:
        rows = self.sql("SELECT datname FROM pg_database WHERE datname LIKE %s", params=(f"{self.prefix}%",))
        return {row[0] for row in rows}

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in records if r["event"] in ("run.start", "run.result")]


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Pg]:
    """``<p>_adm`` (CREATEDB, may become ``<p>_rst``, reads the source), ``<p>_rst`` (lean), ``<p>_fat`` (CREATEROLE);
    the source database ``<p>_src`` of the test role with rows; an admin profile for each, coupled to a read profile
    of the source; the tools in ``database-tools.toml``; the admin profile unlocked."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    pg_dump, pg_restore = find_tool("pg_dump"), find_tool("pg_restore")
    if pg_dump is None or pg_restore is None:
        pytest.skip("pg_dump and pg_restore are needed")
    prefix = f"stuxdm2_{secrets.token_hex(4)}"
    password = f"adm-canary-{secrets.token_hex(12)}"
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        for statement in (
            f"CREATE ROLE {prefix}_rst LOGIN PASSWORD '{password}'",
            f"CREATE ROLE {prefix}_adm LOGIN CREATEDB PASSWORD '{password}'",
            f"CREATE ROLE {prefix}_fat LOGIN CREATEROLE PASSWORD '{password}'",
            # The admin role may make the restore role a database's owner (SET on the membership, PostgreSQL 16).
            f"GRANT {prefix}_rst TO {prefix}_adm",
            f'CREATE DATABASE {prefix}_src OWNER "{url.username}"',
        ):
            connection.exec_driver_sql(statement)
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    found = Pg(env, "", admin, prefix, password)
    try:
        owner = sqlalchemy.create_engine(
            url.set(drivername="postgresql+psycopg", database=found.source), poolclass=sqlalchemy.pool.NullPool
        )
        with owner.begin() as connection:
            for statement in (
                "CREATE TABLE parts (id serial PRIMARY KEY, material text NOT NULL CHECK (material <> ''),"
                " qty integer DEFAULT 0)",
                "CREATE INDEX parts_material ON parts (lower(material))",
                "INSERT INTO parts (material, qty) VALUES ('Steel', 3), ('Brass', 5)",
                f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {prefix}_adm",
                f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {prefix}_adm",
            ):
                connection.exec_driver_sql(statement)
        owner.dispose()
        tools = env.config_dir / "database-tools.toml"
        env.config_dir.mkdir(mode=0o700, exist_ok=True)
        tools.write_text(
            f"version = 1\n[postgresql]\npg_dump = {json.dumps(str(pg_dump))}\n"
            f"pg_restore = {json.dumps(str(pg_restore))}\n",
            encoding="utf-8",
        )
        tools.chmod(0o600)
        read = {
            "name": "pg",
            "engine": "postgresql",
            "host": url.host,
            "port": url.port,
            "database": found.source,
            "user": url.username,
            "environment": "dev",
            "sslmode": "prefer",
            "password": "keyring:",
        }
        assert env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()}).status_code == 201
        stored = env.send("PUT", "/api/profiles/user:pg/secret", {"secret": url.password, "store": "session"})
        assert stored.status_code == 204
        for role in ("adm", "rst", "fat"):
            created = env.create_write(
                name=f"pg-{role}", read="pg", kind="admin", user=f"{prefix}_{role}", password="keyring:"
            )
            assert created.status_code == 201, created.text
            secret = env.send(
                "PUT", f"/api/write/profiles/write:pg-{role}/secret", {"secret": password, "store": "session"}
            )
            assert secret.status_code == 204
            assert env.confirm_pair(f"write:pg-{role}").status_code == 200
        found.connection_id = env.open("user:pg")
        unlocked = env.unlock(found.connection_id, "write:pg-adm", "pg-adm")
        assert unlocked.status_code == 200, unlocked.text
        yield found
    finally:
        env.close()
        with admin.connect() as connection:
            names = connection.exec_driver_sql(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (f"{prefix}%",)
            ).all()
            for (name,) in names:
                quoted = name.replace('"', '""')
                connection.exec_driver_sql(f'DROP DATABASE "{quoted}" WITH (FORCE)')
            for role in ("adm", "rst", "fat", "pwned"):
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {prefix}_{role}")
        admin.dispose()


def test_a_backup_restores_into_a_new_database_as_the_restore_role(pg: Pg, caplog: pytest.LogCaptureFixture) -> None:
    """GS4-08: the round trip, with the metadata (GS4-07), the preview, the audit records (GS4-22) and the canary."""
    caplog.set_level(logging.INFO)
    response = pg.backup()
    assert response.status_code == 201, response.text
    name = response.json()["backup"]["file"]
    folder = pg.env.config_dir / "backups" / "database-manager"
    meta = json.loads((folder / f"{name}.json").read_text())
    assert name.endswith(".dump") and (folder / name).read_bytes().startswith(b"PGDMP")
    assert meta["engine"] == "postgresql" and meta["format"] == "custom" and meta["complete"] is False
    assert meta["scope_notice"].startswith("Not a complete backup")
    assert meta["tool"]["name"] == "pg_dump" and len(meta["tool"]["sha256"]) == 64
    assert meta["server_version"] and meta["fingerprint"].startswith("v2:sha256:") and meta["extensions"] == 0
    assert meta["system_identifier"] is None or meta["system_identifier"].isdigit()
    assert meta["source"]["database"] == pg.source and pg.password not in meta["source"]["url"]
    listing = pg.env.send("GET", BACKUPS).json()
    assert listing["tools"]["pg_dump"]["ok"] is True and listing["tools"]["pg_restore"]["ok"] is True
    preview = pg.env.send("POST", PREVIEW, {"connection_id": pg.connection_id, "backup_id": name})
    assert preview.status_code == 200, preview.text
    assert preview.json()["targets"] == ["new"] and preview.json()["extensions"] == 0
    target = f"{pg.prefix}_copy"
    restored = pg.restore(name, target)
    assert restored.status_code == 201, restored.text
    doc = restored.json()
    assert doc["verified"] is True and doc["fingerprint_after"] == meta["fingerprint"]
    assert pg.sql("SELECT id, material, qty FROM parts ORDER BY id", target) == [(1, "Steel", 3), (2, "Brass", 5)]
    owner = pg.sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", params=(target,))
    assert owner == [(f"{pg.prefix}_rst",)]
    runs = pg.runs()
    actions = [(r["action"], r.get("result")) for r in reversed(runs)]
    assert actions == [("dbm.backup", None), ("dbm.backup", "ok"), ("dbm.restore", None), ("dbm.restore", "restored")]
    start = runs[1]
    assert start["restore_profile"] == "pg-rst" and start["target"] == target and start["backup_file"] == name
    everything = json.dumps(runs) + caplog.text + response.text + restored.text + json.dumps(meta)
    assert pg.password not in everything


def test_the_restore_role_must_be_a_separate_lean_role(pg: Pg) -> None:
    """M-1: the archive's code runs as the role that restores, so never as the admin role or one with CREATEROLE;
    without a lean restore profile nothing is created."""
    name = pg.made()
    target = f"{pg.prefix}_copy"
    for role, issue in (
        (None, "restore.role_required"),
        ("pg-adm", "restore.role_is_admin"),
        ("pg-fat", "restore.createrole"),
        ("pg-nobody", "restore.role_unknown"),
    ):
        response = pg.restore(name, target, role)
        assert response.json()["code"] == "studio.dbm_restore_role_unsafe", response.text
        assert issue in [i["code"] for i in response.json()["issues"]]
    assert target not in pg.databases()


def test_an_archive_function_cannot_create_a_role_during_the_restore(pg: Pg) -> None:
    """The regression test of M-1: a check function that tries CREATE ROLE runs during COPY as the lean restore
    role, so no role is created."""
    pg.sql(
        "CREATE FUNCTION public.probe(x integer) RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN"
        f" BEGIN EXECUTE 'CREATE ROLE {pg.prefix}_pwned'; EXCEPTION WHEN others THEN NULL; END;"
        " RETURN true; END $$",
        pg.source,
    )
    pg.sql("CREATE TABLE public.probed (x integer CHECK (public.probe(x)))", pg.source)
    pg.sql("INSERT INTO public.probed VALUES (1), (2)", pg.source)
    pg.sql(f"GRANT SELECT ON public.probed TO {pg.prefix}_adm", pg.source)
    pg.sql(f"DROP ROLE IF EXISTS {pg.prefix}_pwned")  # the source's own insert ran as a role that may not either
    name = pg.made()
    restored = pg.restore(name, f"{pg.prefix}_copy")
    assert restored.status_code == 201, restored.text
    assert pg.sql("SELECT count(*) FROM pg_roles WHERE rolname = %s", params=(f"{pg.prefix}_pwned",)) == [(0,)]
    assert pg.sql("SELECT count(*) FROM public.probed", f"{pg.prefix}_copy") == [(2,)]


def test_a_name_a_prod_profile_names_under_an_alias_is_refused(pg: Pg) -> None:
    """L-1: the PROD profile says ``localhost`` where the admin's read profile says ``127.0.0.1`` (or the other way
    round): the same server, so the database it names is never a restore target."""
    name = pg.made()
    target = f"{pg.prefix}_prodcopy"
    url = sqlalchemy.make_url(PG_URL or "")
    alias = "localhost" if url.host in ("127.0.0.1", "::1") else "127.0.0.1"
    prod = {
        "name": "pg-prod",
        "engine": "postgresql",
        "host": alias,
        "port": url.port,
        "database": target,
        "user": url.username,
        "environment": "prod",
        "sslmode": "prefer",
    }
    assert pg.env.send("POST", "/api/profiles", {"profile": prod, "digest": pg.env.user_digest()}).status_code == 201
    response = pg.restore(name, target)
    assert response.json()["code"] == "studio.dbm_restore_not_allowed", response.text
    assert [(i["pointer"], i["code"]) for i in response.json()["issues"]] == [("restore.target", "restore.prod")]
    assert target not in pg.databases()


def test_only_a_new_database_is_a_target_and_an_existing_name_is_refused(pg: Pg) -> None:
    name = pg.made()
    existing = pg.env.send(
        "POST",
        RESTORE,
        {
            "connection_id": pg.connection_id,
            "backup_id": name,
            "target": "existing",
            "typed": pg.source,
            "restore_profile_id": "write:pg-rst",
        },
    )
    assert existing.json()["code"] == "studio.dbm_restore_not_allowed"
    taken = pg.restore(name, pg.source)
    assert taken.json()["code"] == "studio.dbm_restore_target_exists", taken.text
    assert "already exists" not in taken.text
    mismatch = pg.restore(name, f"{pg.prefix}_copy", typed=f"{pg.prefix}_cop")
    assert mismatch.json()["code"] == "studio.dbm_restore_name_mismatch"
    assert pg.databases() == {pg.source}


def test_an_edited_archive_is_never_restored(pg: Pg) -> None:
    name = pg.made()
    archive = pg.env.config_dir / "backups" / "database-manager" / name
    data = bytearray(archive.read_bytes())
    data[-1] ^= 0x01
    archive.write_bytes(bytes(data))
    response = pg.restore(name, f"{pg.prefix}_copy")
    assert response.json()["code"] == "studio.dbm_backup_invalid"
    assert pg.databases() == {pg.source}


def test_planted_libpq_variables_do_not_redirect_the_tool(pg: Pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """GS4-05 with a real pg_dump: Studio's environment has a foreign host, no TLS and a wrong password; the tool
    gets none of them and backs up the profile's database."""
    monkeypatch.setenv("PGHOST", "192.0.2.1")
    monkeypatch.setenv("PGSSLMODE", "disable")
    monkeypatch.setenv("PGPASSWORD", "wrong-password")
    monkeypatch.setenv("PGDATABASE", "postgres")
    response = pg.backup()
    assert response.status_code == 201, response.text
    meta = json.loads(
        (pg.env.config_dir / "backups" / "database-manager" / f"{response.json()['backup']['file']}.json").read_text()
    )
    assert meta["source"]["database"] == pg.source


def test_an_admin_role_that_became_superuser_is_refused(pg: Pg) -> None:
    """Section 6 at every action: the admin role gained SUPERUSER after its unlock; nothing is backed up and the
    unlock ends."""
    pg.sql(f"ALTER ROLE {pg.prefix}_adm SUPERUSER")
    try:
        response = pg.backup()
    finally:
        pg.sql(f"ALTER ROLE {pg.prefix}_adm NOSUPERUSER")
    assert (response.status_code, response.json()["code"]) == (405, "studio.admin_over_privileged")
    folder = pg.env.config_dir / "backups" / "database-manager"
    assert not folder.exists() or not list(folder.glob("*.dump"))
    assert pg.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
