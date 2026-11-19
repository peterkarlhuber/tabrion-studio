"""STU-CR-6 part 2 against PostgreSQL 16 or later: the runtime-role wizard on the Database Manager's admin unlock
(STU-ADR-0007 section 12, TX-30, GS3-22, with the role rules of STU-ADR-0008 section 6 that apply).

* A new login role is created with a canary password and granted the rows of the schema ``app``: it reads and writes
  ``app.orders``, cannot write ``app.alembic_version`` or create anything in the schema, and the canary reaches no
  answer, audit record or log. An existing role is granted again after a migration.
* Refused: Studio's own roles (the admin role and the read profile's user), a schema the admin role does not own, a
  system schema, a schema with a SECURITY DEFINER function, an existing role that reaches SUPERUSER or holds TRIGGER
  in another database (K-2), a name that exists already, a missing schema, a preview that no longer matches and a
  wrong typed name. A grant that Tabrion refuses after the role was created drops the role again.

The roles and databases are made by the test server's superuser and dropped again (marker ``postgres_superuser``).
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os
from pathlib import Path
import secrets
from typing import Any

import psycopg
import pytest
import sqlalchemy

from tabrion_studio.config.write_areas import Area, WriteAreas
from tests.support.write import WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
PREVIEW = "/api/write/database-manager/runtime-role/preview"
APPLY = "/api/write/database-manager/runtime-role/apply"
BOTH = WriteAreas(frozenset({Area.SCHEMA_DESIGNER, Area.DATABASE_MANAGER}))


class Pg:
    def __init__(self, env: WriteEnv, admin: sqlalchemy.Engine, prefix: str) -> None:
        self.env, self.admin, self.prefix = env, admin, prefix
        self.connection_id = ""

    def name(self, suffix: str) -> str:
        return f"{self.prefix}_{suffix}"

    def sql(
        self, statement: str, database: str | None = None, params: tuple[Any, ...] = (), *, as_role: str | None = None
    ) -> list[Any]:
        """One statement as the superuser (or, with ``as_role``, after ``SET ROLE`` on the same connection)."""
        engine = sqlalchemy.create_engine(
            self.admin.url.set(database=database or self.name("src")),
            isolation_level="AUTOCOMMIT",
            poolclass=sqlalchemy.pool.NullPool,
        )
        try:
            with engine.connect() as connection:
                if as_role is not None:
                    connection.exec_driver_sql(f'SET ROLE "{as_role}"')
                result = connection.exec_driver_sql(statement, params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()

    def exists(self, role: str) -> bool:
        return self.sql("SELECT count(*) FROM pg_roles WHERE rolname = %s", params=(role,)) == [(1,)]

    def body(self, **fields: Any) -> dict[str, Any]:
        doc = {"connection_id": self.connection_id, "schema": "app", "protected_tables": ["alembic_version"]}
        return {**doc, **fields}

    def preview(self, **fields: Any) -> Any:
        return self.env.send("POST", PREVIEW, self.body(**fields))

    def apply(self, typed: str | None = None, **fields: Any) -> Any:
        seen = self.preview(**{k: v for k, v in fields.items() if k != "password"})
        assert seen.status_code == 200, seen.text
        body = self.body(**fields, state=seen.json()["state"], typed=fields["role"] if typed is None else typed)
        return self.env.send("POST", APPLY, body)

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in records if r["event"] in ("run.start", "run.result")]

    def login(self, role: str, password: str) -> psycopg.Connection[Any]:
        url = sqlalchemy.make_url(PG_URL or "")
        return psycopg.connect(
            host=url.host, port=url.port, dbname=self.name("src"), user=role, password=password, autocommit=True
        )


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Pg]:
    """``<p>_adm`` (LOGIN CREATEROLE, the admin profile ``pg-adm``) owns the schema ``app`` of ``<p>_src`` with the
    tables ``orders`` and ``alembic_version``; a second database ``<p>_oth``; the admin profile unlocked with the
    Schema Designer and the Database Manager on."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"stucr6_{secrets.token_hex(4)}"
    password = f"adm-canary-{secrets.token_hex(12)}"
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        version = connection.exec_driver_sql("SHOW server_version_num").scalar_one()
        if int(version) < 160000:
            pytest.skip("role management needs PostgreSQL 16 or later")
        for statement in (
            f"CREATE ROLE {prefix}_adm LOGIN CREATEROLE PASSWORD '{password}'",
            f'CREATE DATABASE {prefix}_src OWNER "{url.username}"',
            f"CREATE DATABASE {prefix}_oth",
        ):
            connection.exec_driver_sql(statement)
    env = WriteEnv(tmp_path, static_dir, BOTH)
    found = Pg(env, admin, prefix)
    try:
        found.sql(f"CREATE SCHEMA app AUTHORIZATION {prefix}_adm")
        for statement in (
            "CREATE TABLE app.orders (id serial PRIMARY KEY, item text NOT NULL)",
            "CREATE TABLE app.alembic_version (version_num varchar(32) PRIMARY KEY)",
            "INSERT INTO app.alembic_version VALUES ('base')",
        ):
            found.sql(statement, as_role=f"{prefix}_adm")
        found.sql("CREATE TABLE other_parts (id integer PRIMARY KEY)", found.name("oth"))
        read = {
            "name": "pg",
            "engine": "postgresql",
            "host": url.host,
            "port": url.port,
            "database": f"{prefix}_src",
            "user": url.username,
            "environment": "dev",
            "sslmode": "prefer",
            "password": "keyring:",
        }
        assert env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()}).status_code == 201
        stored = env.send("PUT", "/api/profiles/user:pg/secret", {"secret": url.password, "store": "session"})
        assert stored.status_code == 204
        created = env.create_write(name="pg-adm", read="pg", kind="admin", user=f"{prefix}_adm", password="keyring:")
        assert created.status_code == 201, created.text
        secret = env.send("PUT", "/api/write/profiles/write:pg-adm/secret", {"secret": password, "store": "session"})
        assert secret.status_code == 204
        assert env.confirm_pair("write:pg-adm").status_code == 200
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
            roles = connection.exec_driver_sql(
                "SELECT rolname FROM pg_roles WHERE rolname LIKE %s ORDER BY length(rolname) DESC", (f"{prefix}%",)
            ).all()
            for (role,) in roles:
                quoted = role.replace('"', '""')
                connection.exec_driver_sql(f'DROP OWNED BY "{quoted}"')
                connection.exec_driver_sql(f'DROP ROLE "{quoted}"')
        admin.dispose()


def _issues(response: Any) -> list[tuple[str, str]]:
    return [(i["pointer"], i["code"]) for i in response.json().get("issues", [])]


def test_a_new_runtime_role_reads_and_writes_rows_only(pg: Pg, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    role = pg.name("rt")
    canary = f"rt-canary-{secrets.token_hex(12)}"
    seen = pg.preview(role=role, create=True)
    assert seen.status_code == 200, seen.text
    doc = seen.json()
    assert doc["refusals"] == [] and doc["target"] is None and doc["typed"] == role
    assert doc["schema"]["tables"] == 2 and doc["schema"]["protected_found"] == ["alembic_version"]
    assert "rerun_after_migration" in doc["warnings"]
    response = pg.apply(role=role, create=True, password=canary)
    assert response.status_code == 200, response.text
    done = response.json()
    assert done["created"] is True and done["role"] == role and done["schema"] == "app"
    assert done["can_create"] is False and done["tables"] == 2
    with pg.login(role, canary) as connection:
        connection.execute("INSERT INTO app.orders (item) VALUES ('bolt')")
        assert connection.execute("SELECT item FROM app.orders").fetchall() == [("bolt",)]
        connection.execute("UPDATE app.orders SET item = 'nut'")
        assert connection.execute("SELECT version_num FROM app.alembic_version").fetchall() == [("base",)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("INSERT INTO app.alembic_version VALUES ('evil')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE app.planted (id integer)")
    attributes = pg.sql(
        "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles"
        " WHERE rolname = %s",
        params=(role,),
    )
    assert attributes == [(True, False, False, False, False, False)]
    runs = [r for r in pg.runs() if r.get("action") == "dbm.role.runtime"]  # newest first
    assert [r.get("result") for r in runs] == ["changed", None]
    result, start = runs
    assert start["created"] is True and start["password_set"] is True and start["schema"] == "app"
    assert start["protected"] == ["alembic_version"] and result["connect"] is True
    for text in (seen.text, response.text, str(pg.runs()), caplog.text):
        assert canary not in text


def test_an_existing_role_is_granted_again_after_a_migration(pg: Pg) -> None:
    role = pg.name("rt")
    pg.sql(f"CREATE ROLE {role} LOGIN PASSWORD 'rt-pw'")
    first = pg.apply(role=role, create=False)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is False
    # A table the admin role creates later gets the rights by the default privileges.
    pg.sql("CREATE TABLE app.later (id integer)", as_role=pg.name("adm"))
    with pg.login(role, "rt-pw") as connection:
        assert connection.execute("SELECT count(*) FROM app.later").fetchone() == (0,)
    again = pg.preview(role=role, create=False)
    assert again.status_code == 200 and again.json()["refusals"] == []
    assert again.json()["k2"]["found"] == [] and pg.name("oth") in again.json()["k2"]["checked"]
    assert pg.apply(role=role, create=False).status_code == 200


@pytest.mark.parametrize("which", ["admin", "reader"])
def test_studios_own_roles_are_refused(pg: Pg, which: str) -> None:
    role = pg.name("adm") if which == "admin" else (sqlalchemy.make_url(PG_URL or "").username or "")
    seen = pg.preview(role=role, create=False)
    assert seen.status_code == 200, seen.text
    assert "own_role" in seen.json()["refusals"]
    refused = pg.env.send("POST", APPLY, pg.body(role=role, create=False, typed=role, state=seen.json()["state"]))
    assert refused.status_code == 405 and refused.json()["code"] == "studio.dbm_role_not_allowed"
    assert ("role.target", "role.own_role") in _issues(refused)


def test_a_schema_the_admin_role_does_not_own_is_refused(pg: Pg) -> None:
    seen = pg.preview(role=pg.name("rt"), create=True, schema="public", protected_tables=[])
    assert seen.status_code == 200, seen.text
    assert "not_owner" in seen.json()["refusals"]
    pg.sql("CREATE TABLE app.foreign_owned (id integer)")  # the superuser's table in the admin's schema
    assert "not_owner" in pg.preview(role=pg.name("rt"), create=True).json()["refusals"]
    assert not pg.exists(pg.name("rt"))


@pytest.mark.parametrize("schema", ["pg_catalog", "information_schema"])
def test_a_system_schema_is_refused(pg: Pg, schema: str) -> None:
    seen = pg.preview(role=pg.name("rt"), create=True, schema=schema, protected_tables=[])
    assert seen.status_code == 200, seen.text
    assert "system_object" in seen.json()["refusals"]


def test_a_security_definer_function_in_the_schema_is_refused(pg: Pg) -> None:
    pg.sql(
        "CREATE FUNCTION app.lift() RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'", as_role=pg.name("adm")
    )
    seen = pg.preview(role=pg.name("rt"), create=True)
    assert seen.status_code == 200 and "security_definer" in seen.json()["refusals"]


def test_an_existing_role_that_reaches_superuser_or_holds_k2_grants_is_refused(pg: Pg) -> None:
    lifted, k2 = pg.name("lifted"), pg.name("k2")
    pg.sql(f"CREATE ROLE {pg.name('su')} SUPERUSER")
    pg.sql(f"CREATE ROLE {lifted} LOGIN")
    pg.sql(f"GRANT {pg.name('su')} TO {lifted}")
    assert "reachable_superuser" in pg.preview(role=lifted, create=False).json()["refusals"]
    pg.sql(f"CREATE ROLE {k2} LOGIN")
    pg.sql(f"GRANT TRIGGER ON other_parts TO {k2}", pg.name("oth"))
    seen = pg.preview(role=k2, create=False)
    assert "escalation_grant" in seen.json()["refusals"]
    assert any(entry.startswith(f"{pg.name('oth')}:trigger") for entry in seen.json()["k2"]["found"])


def test_create_refuses_an_existing_name_and_a_missing_schema(pg: Pg) -> None:
    pg.sql(f"CREATE ROLE {pg.name('rt')}")
    assert "exists" in pg.preview(role=pg.name("rt"), create=True).json()["refusals"]
    missing = pg.preview(role=pg.name("new"), create=True, schema="nowhere")
    assert (missing.status_code, missing.json()["code"]) == (400, "studio.dbm_role_object_missing")
    gone = pg.preview(role=pg.name("ghost"), create=False)
    assert (gone.status_code, gone.json()["code"]) == (400, "studio.dbm_role_missing")


def test_the_apply_is_bound_to_the_preview_and_the_typed_name(pg: Pg) -> None:
    role = pg.name("rt")
    seen = pg.preview(role=role, create=True)
    pg.sql("CREATE TABLE app.newer (id integer)", as_role=pg.name("adm"))
    stale = pg.env.send("POST", APPLY, pg.body(role=role, create=True, typed=role, state=seen.json()["state"]))
    assert (stale.status_code, stale.json()["code"]) == (400, "studio.dbm_role_changed")
    wrong = pg.apply(role=role, create=True, typed=role.upper())
    assert wrong.json()["code"] == "studio.dbm_role_name_mismatch"
    assert not pg.exists(role)


def test_a_refused_grant_drops_the_role_it_created(pg: Pg) -> None:
    """TB-150: PUBLIC may write the protected table, so Tabrion rolls the grant back; the new role goes again."""
    pg.sql("GRANT INSERT ON app.alembic_version TO PUBLIC")
    role = pg.name("rt")
    response = pg.apply(role=role, create=True, password=f"x-{secrets.token_hex(8)}")
    assert response.json()["code"] == "studio.dbm_role_unsafe", response.text
    assert ("role.target", "role.protected_writable") in _issues(response)
    assert not pg.exists(role)
    result = next(r for r in pg.runs() if r.get("action") == "dbm.role.runtime")  # newest first
    assert result["result"] == "studio.dbm_role_unsafe" and result["undone"] is True
