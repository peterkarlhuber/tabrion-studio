"""STU-CR-3 against PostgreSQL: a new database through ``tabrion.postgres.create_database`` on the unlocked write
connection (GS3-03, GS3-20, GS3-21, GS3-27, TX-29): the owner checks (CREATEDB, an owner the role may and may not
become, a missing owner), a duplicate database without server text (found first, and the server's own refusal when
the look-up misses it), a driver error as ``db.<kind>`` only, PROD refused, the audit records, and the write session
left as it was (autocommit off, the fixed ``search_path``).

The roles are made by the test server's superuser and dropped again, with every database a test made (marker
``postgres_superuser``, like ``test_write_postgres.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os
from pathlib import Path
import secrets
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import WRITE_SEARCH_PATH
from tests.support.write import CANARY_VALUE, WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
ROUTE = "/api/write/schema-designer/create/postgresql"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))


class Env:
    def __init__(self, env: WriteEnv, connection_id: str, admin: sqlalchemy.Engine, prefix: str) -> None:
        self.env, self.connection_id, self.admin, self.prefix = env, connection_id, admin, prefix

    def create(self, name: str, **fields: object) -> Any:
        body = {"connection_id": self.connection_id, "name": name, "typed": name, **fields}
        return self.env.send("POST", ROUTE, body)

    def database(self, name: str) -> tuple[Any, ...] | None:
        with self.admin.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT pg_get_userbyid(datdba), pg_encoding_to_char(encoding) FROM pg_database WHERE datname = %s",
                (name,),
            ).first()
        return None if row is None else tuple(row)

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in records if r["event"] in ("run.start", "run.result")]


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Env]:
    """``<p>_cdb`` (CREATEDB, member of ``<p>_mine``), ``<p>_plain`` (no CREATEDB), ``<p>_mine`` and ``<p>_other``
    (NOLOGIN owners); a write profile for each login role coupled to a read profile of the test database."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"stucr3_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        for statement in (
            f"CREATE ROLE {prefix}_mine NOLOGIN",
            f"CREATE ROLE {prefix}_other NOLOGIN",
            f"CREATE ROLE {prefix}_cdb LOGIN CREATEDB PASSWORD '{password}' IN ROLE {prefix}_mine",
            f"CREATE ROLE {prefix}_plain LOGIN PASSWORD '{password}'",
            f'GRANT CONNECT ON DATABASE "{url.database}" TO {prefix}_cdb, {prefix}_plain',
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
    assert env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()}).status_code == 201
    if url.password:
        stored = env.send("PUT", "/api/profiles/user:pg/secret", {"secret": url.password, "store": "session"})
        assert stored.status_code == 204
    for role in ("cdb", "plain"):
        created = env.create_write(name=f"pg-{role}", read="pg", user=f"{prefix}_{role}", password="keyring:")
        assert created.status_code == 201, created.text
        secret = env.send(
            "PUT", f"/api/write/profiles/write:pg-{role}/secret", {"secret": password, "store": "session"}
        )
        assert secret.status_code == 204
        assert env.confirm_pair(f"write:pg-{role}").status_code == 200
    connection_id = env.open("user:pg")
    assert env.unlock(connection_id, "write:pg-cdb", "pg-cdb").status_code == 200
    try:
        yield Env(env, connection_id, admin, prefix)
    finally:
        env.close()
        with admin.connect() as connection:
            names = connection.exec_driver_sql(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (f"{prefix}%",)
            ).all()
            for (name,) in names:
                quoted = name.replace('"', '""')
                connection.exec_driver_sql(f'DROP DATABASE "{quoted}" WITH (FORCE)')
            for role in ("cdb", "plain", "mine", "other"):
                connection.exec_driver_sql(f"DROP OWNED BY {prefix}_{role}")
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {prefix}_{role}")
        admin.dispose()


def test_a_database_is_created_with_an_owner_the_role_may_become(pg: Env) -> None:
    name = f"{pg.prefix}_shop"
    response = pg.create(name, owner=f"{pg.prefix}_mine", template="template0", encoding="UTF8")
    assert response.status_code == 201, response.text
    assert response.json() == {
        "engine": "postgresql",
        "database": name,
        "owner": f"{pg.prefix}_mine",
        "audit_pending": False,
    }
    assert pg.database(name) == (f"{pg.prefix}_mine", "UTF8")
    runs = pg.runs()
    start, result = runs[1], runs[0]
    assert start["action"] == "schema_designer.create_postgresql" and start["objects"] == [name]
    assert start["profile"] == "pg-cdb" and start["environment"] == "dev" and start["engine"] == "postgresql"
    assert result["result"] == "committed" and result["run"] == start["run"]
    # No owner, locale or statement in the audit log (names, codes and counts only).
    assert f"{pg.prefix}_mine" not in str(runs) and "CREATE" not in str(runs)


def test_without_an_owner_the_role_owns_it_and_a_name_that_needs_quoting_works(pg: Env) -> None:
    name = f'{pg.prefix} Shop "Ä"'
    response = pg.create(name)
    assert response.status_code == 201, response.text
    assert pg.database(name) == (f"{pg.prefix}_cdb", "UTF8")


def test_the_write_session_is_left_as_it_was(pg: Env) -> None:
    """After the autocommit statement the pooled session is back in its normal mode with the pinned search_path."""
    assert pg.create(f"{pg.prefix}_a").status_code == 201
    unlocks = pg.env.unlocks
    connection = unlocks.require(pg.env.session(), pg.connection_id, Area.SCHEMA_DESIGNER)
    assert connection.fetch("SELECT pg_catalog.current_setting('search_path')") == [(WRITE_SEARCH_PATH,)]
    with connection.idle_autocommit() as raw:
        assert raw.autocommit is True
    with connection.transaction():
        pass
    assert pg.create(f"{pg.prefix}_b").status_code == 201


def test_a_duplicate_database_is_refused_without_server_text(pg: Env) -> None:
    name = f"{pg.prefix}_dup"
    assert pg.create(name).status_code == 201
    again = pg.create(name)
    assert (again.status_code, again.json()["code"]) == (400, "studio.schema_database_exists"), again.text
    assert "already exists" not in again.text and name not in again.text
    assert pg.runs()[0]["result"] == "studio.schema_database_exists"


def test_a_duplicate_the_look_up_misses_is_the_same_answer(pg: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A database made by someone else between the look-up and the statement: the server's DuplicateDatabase is
    answered with the same fixed problem, no server text."""
    from tabrion_studio.write.schema_designer import create as create_module

    name = f"{pg.prefix}_race_{CANARY_VALUE[-12:]}".replace("-", "_")
    with pg.admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    monkeypatch.setattr(create_module, "_EXISTS", "SELECT 1 WHERE %s::text IS NULL")
    response = pg.create(name)
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_database_exists"), response.text
    assert "already exists" not in response.text and CANARY_VALUE[-12:] not in response.text


def test_a_role_without_createdb_is_refused(pg: Env) -> None:
    pg.env.send("DELETE", "/api/write/unlocks", {"connection_id": pg.connection_id})
    assert pg.env.unlock(pg.connection_id, "write:pg-plain", "pg-plain").status_code == 200
    name = f"{pg.prefix}_nope"
    response = pg.create(name)
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_createdb_required"), response.text
    assert pg.database(name) is None


def test_an_owner_the_role_may_not_become_is_refused(pg: Env) -> None:
    name = f"{pg.prefix}_owned"
    response = pg.create(name, owner=f"{pg.prefix}_other")
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_owner_not_allowed"), response.text
    assert f"{pg.prefix}_other" not in response.text
    assert pg.database(name) is None
    missing = pg.create(name, owner=f"{pg.prefix}_missing")
    assert (missing.status_code, missing.json()["code"]) == (400, "studio.schema_owner_missing")
    assert pg.database(name) is None
    superuser = pg.create(name, owner="postgres")
    assert (superuser.status_code, superuser.json()["code"]) == (400, "studio.schema_owner_not_allowed")


def test_a_driver_error_is_a_db_problem_without_server_text(pg: Env, caplog: pytest.LogCaptureFixture) -> None:
    """An unknown locale: the server's message names it; neither the answer nor the log carries it (TX-46)."""
    caplog.set_level(logging.DEBUG)
    marker = "zz_" + CANARY_VALUE[-12:].replace("-", "_")
    name = f"{pg.prefix}_locale"
    response = pg.create(name, locale=marker, template="template0")
    assert response.status_code >= 400 and response.json()["code"].startswith("db."), response.text
    assert marker not in response.text
    assert marker not in caplog.text
    assert pg.database(name) is None
    assert pg.runs()[0]["result"].startswith("db.")


def test_without_an_unlock_nothing_is_created(pg: Env) -> None:
    pg.env.send("DELETE", "/api/write/unlocks", {"connection_id": pg.connection_id})
    name = f"{pg.prefix}_locked"
    response = pg.create(name)
    assert (response.status_code, response.json()["code"]) == (405, "studio.write_locked")
    assert pg.database(name) is None


def test_a_profile_changed_to_prod_is_refused(pg: Env) -> None:
    """GS3-03: the stored environment, read again on every use; a hand edit to prod ends the unlock and refuses the
    creation before anything is sent."""
    text = (pg.env.config_dir / "profiles.toml").read_text(encoding="utf-8")
    pg.env.write_file(text.replace('environment = "dev"', 'environment = "prod"'))
    name = f"{pg.prefix}_prod"
    response = pg.create(name)
    assert response.status_code in (400, 405) and response.status_code != 201, response.text
    assert pg.database(name) is None
    assert all(run.get("objects") != [name] for run in pg.runs())


def test_a_typed_name_that_does_not_match_creates_nothing(pg: Env) -> None:
    name = f"{pg.prefix}_typed"
    body = {"connection_id": pg.connection_id, "name": name, "typed": name.upper()}
    response = pg.env.send("POST", ROUTE, body)
    assert (response.status_code, response.json()["code"]) == (400, "studio.schema_create_unconfirmed")
    assert pg.database(name) is None


# --- decision 1A (G-S2) for the Schema Designer's unlock and runs (GS3-28, review L-1 of PR #127) -------------------


@pytest.mark.parametrize(
    "grant",
    ["ALTER ROLE {p}_cdb BYPASSRLS", "ALTER ROLE {p}_cdb REPLICATION", "GRANT pg_read_server_files TO {p}_mine"],
)
def test_the_schema_designer_unlock_refuses_an_over_privileged_role(pg: Env, grant: str) -> None:
    """1A applies to the Schema Designer's unlock too: the role, directly or through ``<p>_mine``, is refused."""
    pg.env.send("DELETE", "/api/write/unlocks", {"connection_id": pg.connection_id})
    with pg.admin.connect() as connection:
        connection.exec_driver_sql(grant.format(p=pg.prefix))
    refused = pg.env.unlock(pg.connection_id, "write:pg-cdb", "pg-cdb")
    assert (refused.status_code, refused.json()["code"]) == (405, "studio.write_role_over_privileged"), refused.text
    assert pg.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    name = f"{pg.prefix}_never"
    response = pg.create(name)
    assert (response.status_code, response.json()["code"]) == (405, "studio.write_locked")
    assert pg.database(name) is None


@pytest.mark.parametrize(
    "grant",
    ["ALTER ROLE {p}_cdb BYPASSRLS", "GRANT pg_execute_server_program TO {p}_mine"],
)
def test_a_right_gained_after_the_unlock_refuses_the_run_and_ends_the_unlock(pg: Env, grant: str) -> None:
    """The privilege check runs again at the start of each run, on the session the statement uses: a role that
    gained a refusing right since its unlock creates nothing, the unlock ends, and ``run.result`` names the code."""
    with pg.admin.connect() as connection:
        connection.exec_driver_sql(grant.format(p=pg.prefix))
    name = f"{pg.prefix}_late"
    response = pg.create(name)
    assert (response.status_code, response.json()["code"]) == (405, "studio.write_role_over_privileged"), response.text
    assert pg.database(name) is None
    assert pg.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    runs = pg.runs()
    assert runs[0]["result"] == "studio.write_role_over_privileged" and runs[0]["run"] == runs[1]["run"]
    again = pg.create(name)
    assert (again.status_code, again.json()["code"]) == (405, "studio.write_locked")
