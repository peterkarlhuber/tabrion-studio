"""STU-XSD-2 against PostgreSQL: the read-only schema comparison on the read profiles' connections, with the D-5
markers, triggers and policies (GS3-03, GS3-12, GS3-13; TX-28; STU-ADR-0007 sections 1, 5 and 9).

* the schema has a domain column, a partitioned table with a partition, a trigger with its PL/pgSQL function and a
  row-level security policy; all of it is made and read by the test database's own role, **not a superuser** (as the
  read profile Studio uses), so the comparison needs no special right;
* the same schema read through a DEV and a PROD profile is ``inconclusive``, not ``same``: the domain and the
  partitioned table are flagged (the snapshot does not see a domain's definition or a partition's bounds); triggers
  and policies are counted and are part of the fingerprint (D-5.1);
* a snapshot file taken before a trigger function's body changed and a policy was dropped names exactly those
  differences;
* the plan preview passes the server's ``server_version_num`` (GS3-13); none is made for PROD; nothing changes.

Marker ``postgres``: only ``TABRION_STUDIO_TEST_POSTGRES`` is needed.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import secrets
from typing import Any

import pytest
import sqlalchemy
from tabrion.plan import plan as tabrion_plan
from tabrion.schema import fingerprint, snapshot

from tabrion_studio.config.write_areas import Area, WriteAreas
from tests.support.write import WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
COMPARE = "/api/write/schema-designer/compare"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))


class Env:
    def __init__(self, env: WriteEnv, ids: dict[str, str], owner: sqlalchemy.Engine, schema: str) -> None:
        self.env, self.ids, self.owner, self.schema = env, ids, owner, schema

    def side(self, name: str) -> dict[str, Any]:
        return {"source": "connection", "connection_id": self.ids[name], "schema": self.schema}

    def ok(self, expected: dict[str, Any], actual: dict[str, Any], **extra: Any) -> dict[str, Any]:
        response = self.env.send("POST", COMPARE, {"expected": expected, "actual": actual, **extra})
        assert response.status_code == 200, response.text
        doc: dict[str, Any] = response.json()
        return doc

    def run(self, *statements: str) -> None:
        with self.owner.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)

    def snapshot_text(self) -> str:
        with self.owner.connect() as connection:
            return snapshot(sqlalchemy.inspect(connection), schemas=[self.schema], level=2).to_json()

    def fingerprint(self) -> str:
        with self.owner.connect() as connection:
            return fingerprint(snapshot(sqlalchemy.inspect(connection), schemas=[self.schema], level=2))


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Env]:
    if PG_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES is needed")
    schema = f"stuxsd2_{secrets.token_hex(4)}"
    url = sqlalchemy.make_url(PG_URL)
    owner = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    with owner.begin() as connection:
        for statement in (
            f"CREATE SCHEMA {schema}",
            f"CREATE DOMAIN {schema}.positive AS integer CHECK (VALUE > 0)",
            f"CREATE TABLE {schema}.parts (id integer PRIMARY KEY, qty {schema}.positive, material text NOT NULL)",
            f"CREATE TABLE {schema}.events (id integer, at date NOT NULL) PARTITION BY RANGE (at)",
            f"CREATE TABLE {schema}.events_2026 PARTITION OF {schema}.events "
            "FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')",
            f"CREATE TABLE {schema}.notes (id integer PRIMARY KEY, body text, owner text)",
            f"CREATE FUNCTION {schema}.touch() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN NEW.body := coalesce(NEW.body, ''); RETURN NEW; END $$",
            f"CREATE TRIGGER notes_touch BEFORE INSERT ON {schema}.notes "
            f"FOR EACH ROW EXECUTE FUNCTION {schema}.touch()",
            f"ALTER TABLE {schema}.notes ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY notes_own ON {schema}.notes USING (owner = current_user)",
        ):
            connection.exec_driver_sql(statement)
    env = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    try:
        for name, environment in (("pg", "dev"), ("pg-prod", "prod")):
            read = {
                "name": name,
                "engine": "postgresql",
                "host": url.host,
                "port": url.port,
                "database": url.database,
                "user": url.username,
                "environment": environment,
                "sslmode": "prefer",
            }
            if url.password:
                read["password"] = "keyring:"
            created = env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()})
            assert created.status_code == 201, created.text
            if url.password:
                stored = env.send(
                    "PUT", f"/api/profiles/user:{name}/secret", {"secret": url.password, "store": "session"}
                )
                assert stored.status_code == 204
        ids = {"dev": env.open("user:pg"), "prod": env.open("user:pg-prod", "prod")}
        yield Env(env, ids, owner, schema)
    finally:
        env.close()
        with owner.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        owner.dispose()


def _server_version(owner: sqlalchemy.Engine) -> tuple[int, ...]:
    with owner.connect() as connection:
        number = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    return (
        (number // 10000, number % 10000) if number >= 100000 else (number // 10000, number // 100 % 100, number % 100)
    )


def test_domains_and_partitions_are_flagged_and_the_verdict_is_inconclusive(pg: Env) -> None:
    before = pg.fingerprint()
    doc = pg.ok(pg.side("dev"), pg.side("prod"))
    assert (doc["verdict"], doc["differences"], doc["same_fingerprint"]) == ("inconclusive", [], True)
    flagged = {t["table"]: {f["kind"] for f in t["features"]} for t in doc["expected"]["flagged"]}
    assert "domain" in flagged[f"{pg.schema}.parts"]
    assert "partitioned" in flagged[f"{pg.schema}.events"]
    assert "partition" in flagged[f"{pg.schema}.events_2026"]
    domain = next(f for f in doc["expected"]["flagged"] if f["table"] == f"{pg.schema}.parts")["features"]
    assert {"kind": "domain", "name": "qty", "known": True, "marker": "domain:qty"} in domain
    assert not doc["unknown_features"]
    assert (doc["expected"]["triggers"], doc["expected"]["policies"], doc["expected"]["row_security"]) == (1, 1, 1)
    assert doc["actual"]["environment"] == "prod"
    assert pg.fingerprint() == before


def test_a_changed_trigger_function_and_a_dropped_policy_are_differences(pg: Env) -> None:
    earlier = pg.snapshot_text()
    pg.run(
        f"CREATE OR REPLACE FUNCTION {pg.schema}.touch() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN NEW.body := upper(coalesce(NEW.body, '')); RETURN NEW; END $$",
        f"DROP POLICY notes_own ON {pg.schema}.notes",
        f"ALTER TABLE {pg.schema}.notes DISABLE ROW LEVEL SECURITY",
    )
    doc = pg.ok({"source": "file", "text": earlier}, pg.side("dev"))
    assert doc["verdict"] == "different"
    found = {(d["kind"], d["table"], d["name"]) for d in doc["differences"]}
    notes = f"{pg.schema}.notes"
    assert (("policy_missing", notes, "notes_own")) in found
    assert ("row_security", notes, None) in found
    assert {kind for kind, table, _ in found if table == notes} >= {"function_missing", "function_added"}
    assert all(d["breaking"] for d in doc["differences"] if d["kind"] in ("policy_missing", "function_missing"))


def test_the_plan_preview_passes_the_servers_version_and_is_never_made_for_prod(
    pg: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    calls: list[dict[str, Any]] = []
    real = tabrion_plan

    def spy(**options: Any) -> Any:
        calls.append(options)
        return real(**options)

    monkeypatch.setattr(draft_module, "plan", spy)
    before = pg.fingerprint()
    ready = pg.ok(pg.side("prod"), pg.side("dev"), plan=True)["plan"]
    assert ready["status"] == "ready", ready
    assert ready["dialect"] == "postgresql" and ready["steps"] == []
    assert tuple(ready["server_version"]) == _server_version(pg.owner)
    assert [c["server_version"] for c in calls] == [_server_version(pg.owner)]
    refused = pg.ok(pg.side("dev"), pg.side("prod"), plan=True)["plan"]
    assert refused["problem"]["issues"] == [{"pointer": "compare.plan", "code": "compare.plan_prod"}]
    assert len(calls) == 1
    assert pg.fingerprint() == before


@pytest.mark.parametrize("schema", ["pg_catalog", "information_schema", "missing_schema_x"])
def test_postgresqls_own_and_a_missing_schema_are_refused(pg: Env, schema: str) -> None:
    side = {**pg.side("dev"), "schema": schema}
    response = pg.env.send("POST", COMPARE, {"expected": side, "actual": pg.side("dev")})
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_compare_schema"), response.text
