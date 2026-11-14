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
from tests.support.plans import RowExecutor
from tests.support.write import CANARY_SECRET, EVERY_AREA, WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")


@pytest.fixture
def roles() -> Iterator[dict[str, Any]]:
    """``<p>_w`` (plain, with a hostile role search_path), ``<p>_su`` (SUPERUSER), ``<p>_member`` (member of a
    CREATEROLE role), ``<p>_files`` (member of ``pg_read_server_files`` and ``pg_execute_server_program``),
    ``<p>_chain`` (member of ``<p>_bulk``, a member of ``pg_write_all_data``) and a schema ``<p>`` with a shadowing
    ``lower(text)`` and ``=`` for text."""
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
        f"CREATE ROLE {prefix}_files LOGIN PASSWORD '{password}'",
        f"GRANT pg_read_server_files, pg_execute_server_program TO {prefix}_files",
        f"CREATE ROLE {prefix}_bulk NOLOGIN IN ROLE pg_write_all_data",
        f"CREATE ROLE {prefix}_chain LOGIN PASSWORD '{password}' IN ROLE {prefix}_bulk",
        f'GRANT CONNECT ON DATABASE "{database}" TO {prefix}_w, {prefix}_member, {prefix}_files, {prefix}_chain',
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
            for role in (
                f"{prefix}_w",
                f"{prefix}_su",
                f"{prefix}_member",
                f"{prefix}_cr",
                f"{prefix}_files",
                f"{prefix}_chain",
                f"{prefix}_bulk",
            ):
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


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (
            "_files",
            {
                "admin.broad:pg_read_server_files": ("direct", "dangerous"),
                "admin.broad:pg_execute_server_program": ("direct", "dangerous"),
            },
        ),
        ("_chain", {"admin.broad:pg_write_all_data": ("inherited", "warning")}),
    ],
)
def test_membership_in_a_predefined_role_is_a_finding(
    roles: dict[str, Any], suffix: str, expected: dict[str, tuple[str, str]]
) -> None:
    """Review I-1 of PR #111 (tabrion ADR 0015 D-2): a predefined role reached directly or through another role is
    ``admin.broad:<role>``; the server-file roles are ``dangerous``. The test's own roles are never named."""
    prefix = roles["prefix"]
    for kind in ("write", "admin"):
        connection = open_write_connection(kind, _profile(f"{prefix}{suffix}"), Settings(), password=roles["password"])
        try:
            found = {f["code"]: (f["via"], f["severity"]) for f in connection.findings}
            assert found == expected, found
            assert prefix not in repr(connection.findings)
        finally:
            connection.close()


def test_createrole_before_16_is_dangerous_and_reported_by_the_server_version(roles: dict[str, Any]) -> None:
    """Review I-1 of PR #111 (tabrion ADR 0015 D-1): ``admin.createrole_pre16`` exactly when the server is older
    than PostgreSQL 16; ``admin.createrole`` on every version."""
    prefix = roles["prefix"]
    admin = open_write_connection("admin", _profile(f"{prefix}_member"), Settings(), password=roles["password"])
    try:
        version = int(admin.fetch("SELECT pg_catalog.current_setting('server_version_num')")[0][0])
        assert admin.facts["server_version"] == version
        found = {f["code"]: (f["via"], f["severity"]) for f in admin.findings}
        assert found["admin.createrole"] == ("inherited", "warning")
        if version < 160000:
            assert found["admin.createrole_pre16"] == ("inherited", "dangerous")
        else:
            assert "admin.createrole_pre16" not in found
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
        # STU-XW-4 (I-109 j): no unlock before the pair of server and secret reference is confirmed.
        refused = env.unlock(connection_id, "write:pg-write", "pg-write")
        assert (refused.status_code, refused.json()["code"]) == (400, "studio.secret_unconfirmed")
        for name in ("pg-write", "pg-admin"):
            assert env.confirm_pair(f"write:{name}").status_code == 200
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
        # STU-XW-2: both unlocks are in the audit log, with the findings counted; no password reaches it (TX-13).
        audit = b"".join(p.read_bytes() for p in sorted(env.audit_dir.iterdir()) if p.is_file())
        assert audit.count(b'"event":"unlock"') == 3 and b'"findings":' in audit
        assert roles["password"].encode() not in audit and CANARY_SECRET.encode() not in audit
        if url.password:
            assert url.password.encode() not in audit
    finally:
        env.close()


# --- STU-XW-3: plans on PostgreSQL (TX-08, TX-09, TX-16) --------------------------------------------------------------


def _plan_env(roles: dict[str, Any], tmp_path: Path, static_dir: Path) -> tuple[WriteEnv, str, sqlalchemy.Engine]:
    """A table ``<p>.parts`` with a BEFORE UPDATE trigger and an ON UPDATE DO ALSO rule, the read profile ``pg``, the
    write profile ``pg-write`` (role ``<p>_w``) unlocked on an open connection, and the test executor registered."""
    assert PG_URL is not None and PG_SUPERUSER_URL is not None
    prefix = roles["prefix"]
    admin = sqlalchemy.create_engine(sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"))
    url = sqlalchemy.make_url(PG_URL)
    with admin.begin() as connection:
        for statement in (
            f"CREATE FUNCTION {prefix}.code_default() RETURNS text LANGUAGE sql AS $$ SELECT 'p' $$",
            f"CREATE FUNCTION {prefix}.material_ok(text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT true $$",
            f"CREATE TABLE {prefix}.parts (id integer PRIMARY KEY,"
            f" material text CHECK ({prefix}.material_ok(material)), code text DEFAULT {prefix}.code_default())",
            f"CREATE TABLE {prefix}.log (n integer)",
            f"INSERT INTO {prefix}.parts VALUES (1, 'Steel')",  # noqa: S608 (test names)
            f"CREATE FUNCTION {prefix}.touch() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog"
            " AS $$ BEGIN RETURN NEW; END $$",
            f"CREATE TRIGGER parts_touch BEFORE UPDATE ON {prefix}.parts FOR EACH ROW"
            f" EXECUTE FUNCTION {prefix}.touch()",
            f"CREATE RULE parts_log AS ON UPDATE TO {prefix}.parts DO ALSO INSERT INTO {prefix}.log VALUES (1)",  # noqa: S608
            f"GRANT SELECT, UPDATE ON {prefix}.parts TO {prefix}_w",
            f"GRANT SELECT ON {prefix}.parts TO {url.username}",
            f"GRANT USAGE ON SCHEMA {prefix} TO {url.username}",
        ):
            connection.exec_driver_sql(statement)
    env = WriteEnv(tmp_path, static_dir, EVERY_AREA)
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
    created = env.create_write(name="pg-write", read="pg", user=f"{prefix}_w", password="keyring:")
    assert created.status_code == 201, created.text
    secret = env.send(
        "PUT", "/api/write/profiles/write:pg-write/secret", {"secret": roles["password"], "store": "session"}
    )
    assert secret.status_code == 204
    connection_id = env.open("user:pg")
    assert env.confirm_pair("write:pg-write").status_code == 200
    assert env.unlock(connection_id, "write:pg-write", "pg-write").status_code == 200
    env.studio.client.app.state.write_plans.register(RowExecutor())  # type: ignore[attr-defined]
    return env, connection_id, admin


def _pg_plan(env: WriteEnv, connection_id: str, prefix: str, material: str) -> dict[str, Any]:
    request = {"schema": prefix, "table": "parts", "rows": [{"id": 1, "material": material}]}
    response = env.send(
        "POST", "/api/write/plans", {"connection_id": connection_id, "executor": "test.rows", "request": request}
    )
    assert response.status_code == 201, response.text
    preview: dict[str, Any] = response.json()
    confirmed = env.send(
        "POST",
        "/api/write/plans/confirm",
        {"connection_id": connection_id, "plan_hash": preview["plan_hash"], "typed": "pg-write"},
    )
    assert confirmed.status_code == 200, confirmed.text
    return preview


def _material(admin: sqlalchemy.Engine, prefix: str) -> Any:
    with admin.connect() as connection:
        return connection.exec_driver_sql(f"SELECT material FROM {prefix}.parts WHERE id = 1").scalar()  # noqa: S608


def test_a_postgres_plan_names_rules_and_triggers_and_runs_once(
    roles: dict[str, Any], tmp_path: Path, static_dir: Path
) -> None:
    """TX-16, review I-1 of tabrion PR #78: the preview names the trigger and the rule on the target (their changes
    are not in the row count) and the risk notes say so; the confirmed plan runs once under the write role."""
    prefix = roles["prefix"]
    env, connection_id, admin = _plan_env(roles, tmp_path, static_dir)
    try:
        preview = _pg_plan(env, connection_id, prefix, "Iron")
        target = preview["targets"][0]
        assert (target["schema"], target["table"]) == (prefix, "parts")
        assert target["triggers"] == [
            {"name": "parts_touch", "timing": "before", "events": ["update"], "enabled": True}
        ]
        assert target["rules"] == [{"name": "parts_log", "event": "update", "instead": False}]
        assert {"plan.triggers", "plan.rules"} <= set(preview["risks"])
        done = env.send(
            "POST", "/api/write/plans/execute", {"connection_id": connection_id, "plan_hash": preview["plan_hash"]}
        )
        assert done.status_code == 200, done.text
        assert done.json()["rows"] == {"updated": 1}
        assert _material(admin, prefix) == "Iron"
        with admin.connect() as connection:  # the rule ran too, outside the count
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {prefix}.log").scalar() == 1  # noqa: S608
        replay = env.send(
            "POST", "/api/write/plans/execute", {"connection_id": connection_id, "plan_hash": preview["plan_hash"]}
        )
        assert (replay.status_code, replay.json()["code"]) == (400, "studio.plan_unknown")
    finally:
        env.close()
        admin.dispose()


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE {p}.parts ADD COLUMN weight real",
        "CREATE TRIGGER parts_later AFTER UPDATE ON {p}.parts FOR EACH ROW EXECUTE FUNCTION {p}.touch()",
        "ALTER TABLE {p}.parts DISABLE TRIGGER parts_touch",
        "CREATE OR REPLACE RULE parts_log AS ON UPDATE TO {p}.parts DO INSTEAD NOTHING",
        "ALTER TABLE {p}.parts ENABLE ROW LEVEL SECURITY",
        "DROP TABLE {p}.parts CASCADE",
        # Review M-1 of PR #115: the code a trigger, a CHECK constraint or a default runs is part of the fingerprint.
        "CREATE OR REPLACE FUNCTION {p}.touch() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog"
        " AS $$ BEGIN NEW.material := 'Hacked'; RETURN NEW; END $$",
        "CREATE OR REPLACE FUNCTION {p}.touch() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER"
        " SET search_path = pg_catalog AS $$ BEGIN RETURN NEW; END $$",
        "CREATE OR REPLACE FUNCTION {p}.material_ok(text) RETURNS boolean LANGUAGE sql IMMUTABLE"
        " AS $$ SELECT $1 IS NOT NULL $$",
        "CREATE OR REPLACE FUNCTION {p}.code_default() RETURNS text LANGUAGE sql AS $$ SELECT 'q' $$",
    ],
)
def test_drift_on_postgres_aborts_the_run(roles: dict[str, Any], tmp_path: Path, static_dir: Path, change: str) -> None:
    """TX-09: a schema, trigger, rule or row-security change between preview and run stops the run inside the
    transaction; nothing changes."""
    prefix = roles["prefix"]
    env, connection_id, admin = _plan_env(roles, tmp_path, static_dir)
    try:
        preview = _pg_plan(env, connection_id, prefix, "Iron")
        with admin.begin() as connection:
            connection.exec_driver_sql(change.format(p=prefix))
        refused = env.send(
            "POST", "/api/write/plans/execute", {"connection_id": connection_id, "plan_hash": preview["plan_hash"]}
        )
        assert (refused.status_code, refused.json()["code"]) == (400, "studio.plan_drift"), refused.text
        if not change.startswith("DROP"):
            assert _material(admin, prefix) == "Steel"
    finally:
        env.close()
        admin.dispose()


def test_a_partitioned_table_or_a_partition_is_refused(roles: dict[str, Any], tmp_path: Path, static_dir: Path) -> None:
    """Note N-1 of the PR #115 review: the partitions' triggers, rules and policies are not fingerprinted yet, so a
    partitioned table and a partition are refused as targets (until STU-026); nothing is planned."""
    prefix = roles["prefix"]
    env, connection_id, admin = _plan_env(roles, tmp_path, static_dir)
    try:
        with admin.begin() as connection:
            for statement in (
                f"CREATE TABLE {prefix}.parted (id integer, material text) PARTITION BY RANGE (id)",
                f"CREATE TABLE {prefix}.parted_1 PARTITION OF {prefix}.parted FOR VALUES FROM (0) TO (100)",
                f"GRANT SELECT, UPDATE ON {prefix}.parted, {prefix}.parted_1 TO {prefix}_w",
            ):
                connection.exec_driver_sql(statement)
        for table in ("parted", "parted_1"):
            request = {"schema": prefix, "table": table, "rows": [{"id": 1, "material": "Iron"}]}
            response = env.send(
                "POST",
                "/api/write/plans",
                {"connection_id": connection_id, "executor": "test.rows", "request": request},
            )
            assert (response.status_code, response.json()["code"]) == (400, "studio.plan_target_partitioned")
            assert table not in response.text
        assert env.studio.client.app.state.write_plans.open_count(env.session()) == 0  # type: ignore[attr-defined]
    finally:
        env.close()
        admin.dispose()
