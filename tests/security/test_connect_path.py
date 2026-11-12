"""TD-039: the setup queries of a new PostgreSQL connection run under a fixed ``search_path``.

The database's own setting puts a schema of another role ahead of ``pg_catalog`` (``ALTER DATABASE ... SET
search_path``). Objects in that schema answer for the names the dialect's setup uses; each one raises when called.
Opening a Studio connection must call none of them, and user SQL must still see the session's path (and so reach the
planted objects, which is the control). Needs ``TABRION_STUDIO_TEST_POSTGRES_SUPERUSER`` (a superuser of the same
server) to make and drop its own role and schema; everything is removed afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
import secrets
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.queries import Query, QueryEngine
from tests.security.test_read_only import Source

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")

pytestmark = [
    pytest.mark.postgres_superuser,
    pytest.mark.skipif(
        PG_URL is None or PG_SUPERUSER_URL is None,
        reason="TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed",
    ),
]

ROLE = "studio_td039_planter"
SCHEMA = "studio_td039_planted"

#: Objects that answer for names of the setup queries; each raises when it is called. They are made by another role.
PLANTED = f"""
CREATE FUNCTION {SCHEMA}.current_schema() RETURNS name LANGUAGE plpgsql AS
  $$BEGIN RAISE EXCEPTION 'studio_td039 planted object called'; END$$;
CREATE FUNCTION {SCHEMA}.version() RETURNS text LANGUAGE plpgsql AS
  $$BEGIN RAISE EXCEPTION 'studio_td039 planted object called'; END$$;
CREATE FUNCTION {SCHEMA}.planted_eq(oid, oid) RETURNS boolean LANGUAGE plpgsql AS
  $$BEGIN RAISE EXCEPTION 'studio_td039 planted object called'; END$$;
CREATE OPERATOR {SCHEMA}.= (LEFTARG = oid, RIGHTARG = oid, FUNCTION = {SCHEMA}.planted_eq);
CREATE FUNCTION {SCHEMA}.planted_name_eq(name, name) RETURNS boolean LANGUAGE plpgsql AS
  $$BEGIN RAISE EXCEPTION 'studio_td039 planted object called'; END$$;
CREATE OPERATOR {SCHEMA}.= (LEFTARG = name, RIGHTARG = name, FUNCTION = {SCHEMA}.planted_name_eq);
"""


def _profile(url: sqlalchemy.URL) -> Profile:
    return Profile(
        "pg",
        "postgresql",
        "dev",
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        password=SecretRef("keyring", ""),
        sslmode="prefer",
    )


def _run(engine: QueryEngine, connection_id: str, sql: str) -> Query:
    query = engine.start(connection_id, sql)
    assert query.done.wait(10.0), "the query did not finish"
    return query


@pytest.fixture
def planted() -> Iterator[str]:
    """The planted schema ahead of ``pg_catalog`` in the database's ``search_path``; yields that path."""
    assert PG_URL is not None and PG_SUPERUSER_URL is not None
    database = sqlalchemy.make_url(PG_URL).database
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    path = f"{SCHEMA}, pg_catalog, public"
    # One connection for setup and cleanup: it is opened before the database setting exists.
    connection = admin.connect()
    try:
        connection.exec_driver_sql(f"CREATE ROLE {ROLE} NOLOGIN PASSWORD '{secrets.token_hex(16)}'")
        connection.exec_driver_sql(f"CREATE SCHEMA {SCHEMA} AUTHORIZATION {ROLE}")
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {SCHEMA} TO PUBLIC")
        connection.exec_driver_sql(f"SET ROLE {ROLE}")
        for statement in PLANTED.split(";\n"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql(f'ALTER DATABASE "{database}" SET search_path = {path}')
        yield path
    finally:
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql(f'ALTER DATABASE "{database}" RESET search_path')
        connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.exec_driver_sql(f"DROP ROLE IF EXISTS {ROLE}")
        connection.close()
        admin.dispose()


def test_td039_opening_a_connection_calls_no_planted_object(planted: str) -> None:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    source = Source(profiles={"user:pg": _profile(url)}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    try:
        # The dialect's setup runs here; a planted object would raise and the open would fail.
        connection = registry.open("user:pg")
        dialect: Any = connection._db.engine.dialect
        # The dialect's default schema is the session's (the first schema of the database's path), not pg_catalog.
        assert dialect.default_schema_name == SCHEMA
        assert dialect.use_native_hstore is False  # the driver's type lookup is not sent

        # User SQL sees the session's path, on every pooled connection, and so reaches the planted objects.
        for _ in range(4):
            shown = _run(engine, connection.id, "SHOW search_path")
            assert shown.state == "done", shown.problem
            assert shown.rows == [[planted]]
        control = _run(engine, connection.id, "SELECT current_schema()")
        assert control.state == "failed"
        assert control.problem is not None and control.problem.sqlstate == "P0001"
        qualified = _run(engine, connection.id, "SELECT pg_catalog.current_schema()")
        assert qualified.rows == [[SCHEMA]]
    finally:
        engine.close_all()
        registry.close_all()
