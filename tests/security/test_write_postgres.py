"""STU-XW-1 against PostgreSQL: the write connection's fixed catalog-first ``search_path`` (K-2, TX-16) and the
admin privilege check with its finding on over-privilege (E-X6, E-X8, TX-11), and an unlock through the API.

The roles and the schema are made by the test server's superuser and dropped again (marker ``postgres_superuser``,
like the role tests of ``test_diagnostics.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import secrets
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, Settings
from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.connection import WRITE_SEARCH_PATH, open_write_connection
from tests.support.write import CANARY_SECRET, EVERY_AREA, WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")


@pytest.fixture
def roles() -> Iterator[dict[str, Any]]:
    """``<p>_w`` (plain, with a hostile role search_path), ``<p>_su`` (SUPERUSER), ``<p>_member`` (member of a
    CREATEROLE role) and a schema ``<p>`` with a shadowing ``lower(text)`` and ``=`` for text."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"stuxw1_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    admin = sqlalchemy.create_engine(sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"))
    database = sqlalchemy.make_url(PG_URL).database
    setup = [
        f"CREATE ROLE {prefix}_w LOGIN PASSWORD '{password}'",
        f"CREATE ROLE {prefix}_su LOGIN SUPERUSER PASSWORD '{password}'",
        f"CREATE ROLE {prefix}_cr NOLOGIN CREATEROLE",
        f"CREATE ROLE {prefix}_member LOGIN PASSWORD '{password}' IN ROLE {prefix}_cr",
        f'GRANT CONNECT ON DATABASE "{database}" TO {prefix}_w, {prefix}_member',
        f"CREATE SCHEMA {prefix}",
        f"GRANT USAGE ON SCHEMA {prefix} TO {prefix}_w",
        f"CREATE FUNCTION {prefix}.lower(text) RETURNS text LANGUAGE sql AS $$ SELECT 'shadowed' $$",
        f"CREATE FUNCTION {prefix}.always(text, text) RETURNS boolean LANGUAGE sql AS $$ SELECT true $$",
        f"CREATE OPERATOR {prefix}.= (LEFTARG = text, RIGHTARG = text, FUNCTION = {prefix}.always)",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {prefix} TO {prefix}_w",
        f"ALTER ROLE {prefix}_w SET search_path = {prefix}, pg_catalog, public",
        f'ALTER ROLE {prefix}_w IN DATABASE "{database}" SET search_path = {prefix}, pg_catalog, public',
    ]
    with admin.begin() as connection:
        for statement in setup:
            connection.exec_driver_sql(statement)
    try:
        yield {"prefix": prefix, "password": password}
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {prefix} CASCADE")
            for role in (f"{prefix}_w", f"{prefix}_su", f"{prefix}_member", f"{prefix}_cr"):
                connection.exec_driver_sql(f"DROP OWNED BY {role}")
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {role}")
        admin.dispose()


def _profile(user: str) -> Profile:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    return Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=user, sslmode="prefer"
    )


def test_the_write_session_has_the_fixed_catalog_first_search_path(roles: dict[str, Any]) -> None:
    """The role's own search_path puts a hostile schema first; the write session ignores it, so neither the
    shadowing function nor the shadowing operator takes part (K-2, TX-09)."""
    prefix = roles["prefix"]
    write = open_write_connection("write", _profile(f"{prefix}_w"), Settings(), password=roles["password"])
    try:
        assert write.fetch("SELECT pg_catalog.current_setting('search_path')") == [(WRITE_SEARCH_PATH,)]
        assert write.fetch("SELECT lower('ABC'), 'a'::text = 'b'::text") == [("abc", False)]
        with write.transaction() as transaction:
            # Still pinned inside a write transaction, and after a statement that changes it the reset restores it.
            transaction.execute(
                sqlalchemy.text("SELECT pg_catalog.set_config('search_path', :p, false)"), {"p": prefix}
            )
        assert write.fetch("SELECT pg_catalog.current_setting('search_path')") == [(WRITE_SEARCH_PATH,)]
        assert write.fetch("SELECT pg_catalog.current_setting('default_transaction_read_only')") == [("off",)]
        assert write.findings == []
    finally:
        write.close()


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("_w", []),
        ("_su", [("admin.superuser", "direct")]),
        ("_member", [("admin.createrole", "inherited")]),
    ],
)
def test_the_privilege_check_reports_over_privilege(
    roles: dict[str, Any], suffix: str, expected: list[tuple[str, str]]
) -> None:
    """TX-11: attributes held directly or through a reachable role are findings; never a role name."""
    prefix = roles["prefix"]
    admin = open_write_connection("admin", _profile(f"{prefix}{suffix}"), Settings(), password=roles["password"])
    try:
        found = [(f["code"], f["via"]) for f in admin.findings]
        for item in expected:
            assert item in found, found
        if not expected:
            assert found == []
        assert prefix not in repr(admin.findings)
        if suffix == "_member":
            assert admin.facts["reachable_roles"] >= 1
    finally:
        admin.close()


def test_an_unlock_through_the_api_uses_the_write_role(roles: dict[str, Any], tmp_path: Path, static_dir: Path) -> None:
    assert PG_URL is not None
    prefix = roles["prefix"]
    url = sqlalchemy.make_url(PG_URL)
    env = WriteEnv(tmp_path, static_dir, EVERY_AREA)
    try:
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
            assert (
                env.send("PUT", "/api/profiles/user:pg/secret", {"secret": url.password, "store": "session"})
            ).status_code == 204
        for name, kind, user in (("pg-write", "write", f"{prefix}_w"), ("pg-admin", "admin", f"{prefix}_su")):
            created = env.create_write(name=name, read="pg", kind=kind, user=user, password="keyring:")
            assert created.status_code == 201, created.text
            stored = env.send(
                "PUT", f"/api/write/profiles/write:{name}/secret", {"secret": roles["password"], "store": "session"}
            )
            assert stored.status_code == 204
        env.config.secrets.session.set("unused", CANARY_SECRET)
        connection_id = env.open("user:pg")
        unlocked = env.unlock(connection_id, "write:pg-write", "pg-write")
        assert unlocked.status_code == 200, unlocked.text
        assert unlocked.json()["role"] == f"{prefix}_w" and unlocked.json()["findings"] == []
        admin = env.unlock(connection_id, "write:pg-admin", "pg-admin")
        assert admin.status_code == 200, admin.text
        assert {f["code"] for f in admin.json()["findings"]} >= {"admin.superuser"}
        for answer in (unlocked, admin):
            assert roles["password"] not in answer.text and CANARY_SECRET not in answer.text
        write = env.unlocks.require(env.session(), connection_id, Area.DATABASE_MANAGER)
        assert write.kind == "admin"
        # The read connection stays the read profile's role and read-only.
        assert env.registry.get(connection_id).read_only is True
        rows = env.registry.get(connection_id).fetch("SELECT current_user::text")
        assert rows == [(url.username,)]
    finally:
        env.close()
