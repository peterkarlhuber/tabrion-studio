"""STU-CR-4 against PostgreSQL: the Schema Designer's draft of a schema read on the unlocked write connection, its
edits and the read-only plan preview (GS3-09, GS3-13, GS3-20; STU-ADR-0007 sections 2, 3 and 9).

* the draft starts from a level-2 snapshot of one schema the write role sees; PostgreSQL's own schemas and a missing
  schema are refused;
* every ``plan`` call passes ``server_version`` from the server's ``server_version_num`` read on the write connection
  (a spy; GS3-13);
* the preview shows the steps with their risk classes (``locking`` on PostgreSQL), SQL, fingerprints and hash, and
  nothing runs (the schema is unchanged afterwards);
* tabrion's refusals are fixed catalog problems: a primary key change (``plan.unsupported_change``), a partitioned
  table (``plan.unsupported_feature`` with the feature kind ``partitioned``), never a name.

The roles and the schema are made by the test server's superuser and dropped again (marker ``postgres_superuser``).
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import secrets
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.write_areas import Area, WriteAreas
from tests.support.write import CANARY_VALUE, WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
DRAFT = "/api/write/schema-designer/draft"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))


class Env:
    def __init__(self, env: WriteEnv, connection_id: str, admin: sqlalchemy.Engine, prefix: str) -> None:
        self.env, self.connection_id, self.admin, self.prefix = env, connection_id, admin, prefix

    def send(self, path: str, **body: Any) -> Any:
        return self.env.send("POST", path, {"connection_id": self.connection_id, **body})

    def edit(self, edit: dict[str, Any]) -> Any:
        revision = self.send(f"{DRAFT}/view").json()["revision"]
        return self.send(f"{DRAFT}/edits", revision=revision, edit=edit)

    def tables(self) -> list[str]:
        with self.admin.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1", (self.prefix,)
            ).all()
        return [row[0] for row in rows]


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Env]:
    """A schema ``<p>`` with a table ``parts`` and a partitioned table ``events``, and a write role ``<p>_w`` that
    can read them (USAGE on the schema), coupled to a read profile of the test database."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"stucr4_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        for statement in (
            f"CREATE ROLE {prefix}_w LOGIN PASSWORD '{password}'",
            f'GRANT CONNECT ON DATABASE "{url.database}" TO {prefix}_w',
            f"CREATE SCHEMA {prefix}",
            f"GRANT USAGE ON SCHEMA {prefix} TO {prefix}_w",
            f"CREATE TABLE {prefix}.parts (id integer PRIMARY KEY, material text NOT NULL)",
            f"CREATE TABLE {prefix}.events (id integer, at date) PARTITION BY RANGE (at)",
        ):
            connection.exec_driver_sql(statement)
    env = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    read = {
        "name": "pg",
        "engine": "postgresql",
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "user": url.username,
        "environment": "dev",
        "sslmode": "prefer",
    }
    if url.password:
        read["password"] = "keyring:"
    try:
        assert env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()}).status_code == 201
        if url.password:
            stored = env.send("PUT", "/api/profiles/user:pg/secret", {"secret": url.password, "store": "session"})
            assert stored.status_code == 204
        created = env.create_write(name="pg-w", read="pg", user=f"{prefix}_w", password="keyring:")
        assert created.status_code == 201, created.text
        secret = env.send("PUT", "/api/write/profiles/write:pg-w/secret", {"secret": password, "store": "session"})
        assert secret.status_code == 204
        assert env.confirm_pair("write:pg-w").status_code == 200
        connection_id = env.open("user:pg")
        unlocked = env.unlock(connection_id, "write:pg-w", "pg-w")
        assert unlocked.status_code == 200, unlocked.text
        yield Env(env, connection_id, admin, prefix)
    finally:
        env.close()
        with admin.connect() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {prefix} CASCADE")
            connection.exec_driver_sql(f"DROP OWNED BY {prefix}_w")
            connection.exec_driver_sql(f"DROP ROLE IF EXISTS {prefix}_w")
        admin.dispose()


def _server_version(admin: sqlalchemy.Engine) -> tuple[int, ...]:
    with admin.connect() as connection:
        number = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    return (
        (number // 10000, number % 10000) if number >= 100000 else (number // 10000, number // 100 % 100, number % 100)
    )


def test_a_draft_of_a_schema_is_planned_with_the_servers_version(pg: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    seen: list[object] = []
    real = draft_module.plan

    def spy(current: Any, target: Any, **options: Any) -> Any:
        seen.append(options.get("server_version"))
        return real(current, target, **options)

    monkeypatch.setattr(draft_module, "plan", spy)
    started = pg.send(DRAFT, schema=pg.prefix)
    assert started.status_code == 201, started.text
    doc = started.json()
    assert doc["engine"] == "postgresql" and doc["schema"] == pg.prefix
    assert [t["name"] for t in doc["tables"]] == ["events", "parts"]
    assert next(t for t in doc["tables"] if t["name"] == "events")["features"] == ["partitioned"]
    response = pg.edit(
        {
            "op": "add_table",
            "table": "orders",
            "columns": [{"name": "id", "type": "INTEGER", "nullable": False}, {"name": "part", "type": "INTEGER"}],
            "primary_key": ["id"],
        }
    )
    assert response.status_code == 200, response.text
    response = pg.edit(
        {
            "op": "add_constraint",
            "table": "orders",
            "constraint": {
                "kind": "foreign_key",
                "name": "orders_part_fk",
                "columns": ["part"],
                "referred_table": "parts",
                "referred_columns": ["id"],
            },
        }
    )
    assert response.status_code == 200, response.text
    assert (
        pg.edit({"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}}).status_code == 200
    )
    preview = pg.send(f"{DRAFT}/preview")
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    version = _server_version(pg.admin)
    assert plan["server_version"] == list(version)
    assert seen and all(item == version for item in seen)
    assert plan["dialect"] == "postgresql" and plan["executable"] is False
    steps = {(s["operation"], s["target"]): s for s in plan["steps"]}
    assert "locking" in steps[("add_column", f"{pg.prefix}.parts")]["risks"]
    assert ("create_table", f"{pg.prefix}.orders") in steps
    assert all(s["transactional"] for s in plan["steps"])
    # Nothing ran.
    assert pg.tables() == ["events", "parts"]


@pytest.mark.parametrize("schema", ["pg_catalog", "information_schema", "pg_toast", "PG_temp_1"])
def test_postgresqls_own_schemas_are_refused(pg: Env, schema: str) -> None:
    response = pg.send(DRAFT, schema=schema)
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_draft_schema")


def test_a_missing_schema_is_refused_without_its_name(pg: Env) -> None:
    response = pg.send(DRAFT, schema=f"x{CANARY_VALUE}"[:60])
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_draft_schema")
    assert CANARY_VALUE[:30] not in response.text


def test_tabrions_refusals_are_fixed_problems(pg: Env) -> None:
    assert pg.send(DRAFT, schema=pg.prefix).status_code == 201
    # A PostgreSQL primary key change is not supported by the planner.
    response = pg.edit({"op": "drop_constraint", "table": "parts", "kind": "primary_key"})
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_model_invalid"), response.text
    assert response.json()["issues"] == [{"pointer": "plan.code", "code": "plan.unsupported_change"}]
    # A partitioned table is never changed (ADR 0015 D-5): the feature's kind only, never the table's name.
    response = pg.edit({"op": "add_column", "table": "events", "column": {"name": "x", "type": "INTEGER"}})
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_plan_refused"), response.text
    assert response.json()["issues"] == [
        {"pointer": "plan.reason", "code": "plan.unsupported_feature"},
        {"pointer": "plan.feature", "code": "plan.partitioned"},
    ]
    assert pg.prefix not in response.text


def test_constraints_need_a_name_on_postgresql(pg: Env) -> None:
    assert pg.send(DRAFT, schema=pg.prefix).status_code == 201
    response = pg.edit(
        {"op": "add_constraint", "table": "parts", "constraint": {"kind": "unique", "columns": ["material"]}}
    )
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_edit_invalid")
    assert response.json()["issues"] == [{"pointer": "edit.constraint_name", "code": "edit.name_required"}]
    named = {"kind": "unique", "name": "parts_material_key", "columns": ["material"]}
    assert pg.edit({"op": "add_constraint", "table": "parts", "constraint": named}).status_code == 200
    # Names are case-sensitive on PostgreSQL: a column "Material" is a new column.
    added = pg.edit({"op": "add_column", "table": "parts", "column": {"name": "Material", "type": "TEXT"}})
    assert added.status_code == 200, added.text
