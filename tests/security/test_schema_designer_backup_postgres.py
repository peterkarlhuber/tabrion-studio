"""The STU-CR-5 follow-up against PostgreSQL with the real ``pg_dump`` and ``pg_restore``: a destructive Schema Designer
run on a DEV or TEST database takes a verified backup first and runs only when it succeeded (STU-ADR-0007 section 7;
STU-ADR-0008 section 3; GS3-15, GS3-30).

* With both tools in ``database-tools.toml``, a plan that drops a column is offered (no opt-out on PostgreSQL); the
  run makes a ``pg_dump`` archive in ``backups/schema-designer`` (0600, with its metadata) that holds the column and
  its rows, then runs the plan; ``run.start`` says ``backup: made``. On DEV and on TEST.
* A backup that fails (the write role may not read every table of the database) refuses the run with
  ``studio.backup_tool_failed``: nothing is changed, no archive is left, ``run.result`` names the code.
* A tools file that becomes unsafe between the preview and the run refuses it before ``run.start``
  (``backup_unavailable``, checked again right before the run). The password reaches no answer, audit record or log.
* A dry run makes no backup.

Each test makes its own database, role and schema with the test server's superuser and drops them again (marker
``postgres_superuser``). The tools are found as in ``test_dbm_restore_roundtrip.py`` (by the test only).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import logging
import os
from pathlib import Path
import secrets
import stat
import subprocess
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.write_areas import Area, WriteAreas
from tests.security.test_dbm_restore_roundtrip import find_tool
from tests.support.write import WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
DRAFT = "/api/write/schema-designer/draft"
CONFIRM = "/api/write/schema-designer/plan/confirm"
RUN = "/api/write/schema-designer/plan/run"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
DROP_MATERIAL = {"op": "drop_column", "table": "parts", "column": "material"}


class Pg:
    def __init__(
        self, env: WriteEnv, connection_id: str, admin: sqlalchemy.Engine, prefix: str, password: str, tools: Path
    ) -> None:
        self.env, self.connection_id, self.admin, self.prefix, self.password = (
            env,
            connection_id,
            admin,
            prefix,
            password,
        )
        self.pg_restore = tools

    @property
    def database(self) -> str:
        return f"{self.prefix}_db"

    @property
    def backups(self) -> Path:
        return self.env.config_dir / "backups" / "schema-designer"

    def send(self, path: str, **body: Any) -> Any:
        return self.env.send("POST", path, {"connection_id": self.connection_id, **body})

    def sql(self, *statements: str) -> list[tuple[Any, ...]]:
        engine = sqlalchemy.create_engine(
            self.admin.url.set(database=self.database), isolation_level="AUTOCOMMIT", poolclass=sqlalchemy.pool.NullPool
        )
        rows: list[tuple[Any, ...]] = []
        try:
            with engine.connect() as connection:
                for statement in statements:
                    result = connection.exec_driver_sql(statement)
                    rows = [tuple(row) for row in result.all()] if result.returns_rows else []
        finally:
            engine.dispose()
        return rows

    def columns(self) -> list[str]:
        return [
            row[0]
            for row in self.sql(
                "SELECT column_name FROM information_schema.columns "  # noqa: S608 - the test's own names
                f"WHERE table_schema = '{self.prefix}' AND table_name = 'parts' ORDER BY ordinal_position"
            )
        ]

    def confirmed(self, edit: dict[str, Any]) -> dict[str, Any]:
        assert self.send(DRAFT, schema=self.prefix).status_code == 201
        revision = self.send(f"{DRAFT}/view").json()["revision"]
        assert self.send(f"{DRAFT}/edits", revision=revision, edit=edit).status_code == 200
        response = self.send(f"{DRAFT}/preview")
        assert response.status_code == 200, response.text
        plan: dict[str, Any] = response.json()
        assert plan["executable"] is True, plan["blocked"]
        need = plan["confirmation"]
        assert need["backup"] == {
            "required": True,
            "engine": "postgresql",
            "opt_out_allowed": False,
            "opt_out_text": None,
        }
        skip = self.send(
            CONFIRM,
            plan_hash=plan["plan_hash"],
            typed=need["profile"],
            tables=need["tables"],
            steps=need["data_changing_steps"],
            locking=need["locking"],
            backup="skip",
            backup_typed=self.database,
        )
        assert (skip.status_code, skip.json()["code"]) == (400, "studio.schema_backup_required")
        confirmed = self.send(
            CONFIRM,
            plan_hash=plan["plan_hash"],
            typed=need["profile"],
            tables=need["tables"],
            steps=need["data_changing_steps"],
            locking=need["locking"],
            backup="make",
        )
        assert confirmed.status_code == 200, confirmed.text
        return plan

    def run(self, plan: dict[str, Any], dry_run: bool = False) -> Any:
        return self.send(RUN, plan_hash=plan["plan_hash"], dry_run=dry_run)

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in reversed(records) if r["event"] in ("run.start", "run.result")]

    def restore_list(self, archive: Path, *options: str) -> str:
        """``pg_restore`` of the archive as SQL text on stdout (no server): what the backup holds."""
        result = subprocess.run(
            [str(self.pg_restore), *options, "--file=-", str(archive)],
            capture_output=True,
            check=True,
            timeout=60,
        )
        return result.stdout.decode("utf-8", "replace")


@pytest.fixture
def make_pg(tmp_path: Path, static_dir: Path) -> Iterator[Callable[[str], Pg]]:
    """A factory: the database ``<p>_db`` (the superuser's) with the schema ``<p>`` and ``parts`` (two rows, owned by
    the write role ``<p>_w``), a read profile of the given environment, the write profile unlocked for the Schema
    Designer and both tools in ``database-tools.toml``."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    pg_dump, pg_restore = find_tool("pg_dump"), find_tool("pg_restore")
    if pg_dump is None or pg_restore is None:
        pytest.skip("pg_dump and pg_restore are needed")
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    made: list[tuple[WriteEnv, str]] = []

    def make(environment: str) -> Pg:
        prefix = f"stucr6_{secrets.token_hex(4)}"
        password = f"w-canary-{secrets.token_hex(12)}"
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE ROLE {prefix}_w LOGIN PASSWORD '{password}'")
            connection.exec_driver_sql(f"CREATE DATABASE {prefix}_db")
        env = WriteEnv(tmp_path / environment, static_dir, SCHEMA_DESIGNER)
        made.append((env, prefix))
        found = Pg(env, "", admin, prefix, password, pg_restore)
        found.sql(
            f"CREATE SCHEMA {prefix}",
            f"GRANT USAGE, CREATE ON SCHEMA {prefix} TO {prefix}_w",
            f"CREATE TABLE {prefix}.parts (id integer PRIMARY KEY, material text NOT NULL)",
            f"INSERT INTO {prefix}.parts VALUES (1, 'Steel'), (2, 'Oak')",  # noqa: S608
            f"ALTER TABLE {prefix}.parts OWNER TO {prefix}_w",
        )
        env.config_dir.mkdir(mode=0o700, exist_ok=True)
        tools = env.config_dir / "database-tools.toml"
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
            "database": found.database,
            "user": url.username,
            "environment": environment,
            "sslmode": "prefer",
        }
        if url.password:
            read["password"] = "keyring:"
        assert env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()}).status_code == 201
        if url.password:
            stored = env.send("PUT", "/api/profiles/user:pg/secret", {"secret": url.password, "store": "session"})
            assert stored.status_code == 204
        created = env.create_write(name="pg-w", read="pg", user=f"{prefix}_w", password="keyring:")
        assert created.status_code == 201, created.text
        secret = env.send("PUT", "/api/write/profiles/write:pg-w/secret", {"secret": password, "store": "session"})
        assert secret.status_code == 204
        assert env.confirm_pair("write:pg-w").status_code == 200
        found.connection_id = env.open("user:pg")
        unlocked = env.unlock(found.connection_id, "write:pg-w", "pg-w")
        assert unlocked.status_code == 200, unlocked.text
        return found

    try:
        yield make
    finally:
        with admin.connect() as connection:
            for env, prefix in made:
                env.close()
                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {prefix}_db WITH (FORCE)")
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {prefix}_w")
        admin.dispose()


@pytest.mark.parametrize("environment", ["dev", "test"])
def test_a_destructive_run_takes_a_verified_backup_first(
    make_pg: Callable[[str], Pg], environment: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    pg = make_pg(environment)
    plan = pg.confirmed(DROP_MATERIAL)
    assert "destructive" in plan["risks"]
    response = pg.run(plan)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["result"] == "committed" and doc["backup"] == "made"
    archive = Path(doc["backup_path"])
    assert archive.parent == pg.backups and archive.suffix == ".dump"
    assert sorted(p.name for p in pg.backups.iterdir()) == [archive.name, f"{archive.name}.json"]
    assert archive.read_bytes().startswith(b"PGDMP")
    for path in (archive, pg.backups / f"{archive.name}.json"):
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    meta = json.loads((pg.backups / f"{archive.name}.json").read_text())
    assert meta["origin"] == "schema_designer" and meta["complete"] is False
    assert meta["source"] == {"database": pg.database, "profile": "pg", "environment": environment}
    assert meta["tool"]["name"] == "pg_dump" and meta["size"] == archive.stat().st_size
    # The backup holds what the run dropped; the database no longer does.
    assert "material text NOT NULL" in pg.restore_list(archive, "--schema-only")
    assert "Steel" in pg.restore_list(archive, "--data-only")
    assert pg.columns() == ["id"]
    runs = pg.runs()
    assert [(r.get("backup"), r.get("result")) for r in runs] == [("made", None), (None, "committed")]
    for text in (response.text, json.dumps(runs), caplog.text, json.dumps(meta)):
        assert pg.password not in text
    assert str(archive) not in json.dumps(runs) and str(archive) not in caplog.text
    assert not list((pg.env.config_dir / "tool-runs").iterdir())  # the passfile is gone


def test_a_backup_that_fails_refuses_the_run_and_changes_nothing(make_pg: Callable[[str], Pg]) -> None:
    """A table the write role may not read: ``pg_dump`` fails (it never passes ``--enable-row-security`` or skips a
    table), so there is no backup and no run."""
    pg = make_pg("dev")
    pg.sql(f"CREATE SCHEMA {pg.prefix}_hidden", f"CREATE TABLE {pg.prefix}_hidden.secret (id integer)")
    plan = pg.confirmed(DROP_MATERIAL)
    response = pg.run(plan)
    assert response.status_code == 400, response.text
    doc = response.json()
    assert doc["code"] == "studio.backup_tool_failed"
    assert [(i["pointer"], i["code"]) for i in doc["issues"]] == [("tool.pg_dump", "tool.permission")]
    assert pg.columns() == ["id", "material"]
    assert not pg.backups.exists() or list(pg.backups.iterdir()) == []
    runs = pg.runs()
    assert [r.get("result") for r in runs] == [None, "studio.backup_tool_failed"]
    assert pg.password not in response.text + json.dumps(runs)


def test_tools_that_became_unsafe_after_the_preview_refuse_the_run(make_pg: Callable[[str], Pg]) -> None:
    pg = make_pg("dev")
    plan = pg.confirmed(DROP_MATERIAL)
    (pg.env.config_dir / "database-tools.toml").chmod(0o644)  # no longer private: not read
    response = pg.run(plan)
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_run_not_allowed")
    assert [(i["pointer"], i["code"]) for i in response.json()["issues"]] == [
        ("plan.reason", "plan.backup_unavailable")
    ]
    assert pg.columns() == ["id", "material"] and pg.runs() == []
    assert not pg.backups.exists()


def test_a_dry_run_takes_no_backup(make_pg: Callable[[str], Pg]) -> None:
    pg = make_pg("dev")
    plan = pg.confirmed(DROP_MATERIAL)
    response = pg.run(plan, dry_run=True)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "rolled_back" and response.json()["backup"] == "dry_run"
    assert pg.columns() == ["id", "material"]
    assert not pg.backups.exists()
