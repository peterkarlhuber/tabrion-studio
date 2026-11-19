"""STU-XDM-4 against PostgreSQL 16 or later: role management through the admin profile (E-X6, GS4-11..18,
STU-ADR-0008 section 6 and "As built (STU-XDM-4)").

* A login role is created with a canary password, changed, given and taken a membership and a table privilege,
  and dropped; every step needs the preview's ``state`` and the typed name, and is audited without the password.
* Refused: SUPERUSER and the other banned attributes, a membership in ``pg_read_server_files`` or in a role with a
  dangerous attribute, a group that holds TRIGGER in another database (K-2, checked in every database the admin
  may reach), Studio's own roles (the admin role and every profile's user), a hidden-name role, a server that a PROD
  profile names under an alias, a preview that no longer matches, and an admin role that became superuser.

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
ROLES = "/api/write/database-manager/roles"
PREVIEW = "/api/write/database-manager/roles/preview"
APPLY = "/api/write/database-manager/roles/apply"
DATABASE_MANAGER = WriteAreas(frozenset({Area.DATABASE_MANAGER}))
HIDDEN = "\u200b"


class Pg:
    def __init__(self, env: WriteEnv, admin: sqlalchemy.Engine, prefix: str, password: str) -> None:
        self.env, self.admin, self.prefix, self.password = env, admin, prefix, password
        self.connection_id = ""

    def name(self, suffix: str) -> str:
        return f"{self.prefix}_{suffix}"

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

    def member_of(self, role: str) -> set[str]:
        rows = self.sql(
            "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid"
            " JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %s",
            params=(role,),
        )
        return {row[0] for row in rows}

    def exists(self, role: str) -> bool:
        return self.sql("SELECT count(*) FROM pg_roles WHERE rolname = %s", params=(role,)) == [(1,)]

    def body(self, **fields: Any) -> dict[str, Any]:
        return {"connection_id": self.connection_id, **fields}

    def preview(self, **fields: Any) -> Any:
        return self.env.send("POST", PREVIEW, self.body(**fields))

    def apply(self, typed: str | None = None, **fields: Any) -> Any:
        """Preview, then apply with the preview's state and the typed name (the role, or the member)."""
        seen = self.preview(**{k: v for k, v in fields.items() if k != "password"})
        assert seen.status_code == 200, seen.text
        confirm = fields.get("member") or fields["role"]
        return self.env.send(
            "POST",
            APPLY,
            self.body(**fields, state=seen.json()["state"], typed=confirm if typed is None else typed),
        )

    def runs(self) -> list[dict[str, Any]]:
        records = self.env.send("GET", "/api/write/audit?limit=1000").json()["records"]
        return [r["fields"] for r in records if r["event"] in ("run.start", "run.result")]


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Pg]:
    """``<p>_adm`` (LOGIN CREATEROLE, the admin profile ``pg-adm``) and the read profile ``pg`` of the test role on
    ``<p>_src`` (a table ``parts``, SELECT for the admin role WITH GRANT OPTION); a second database ``<p>_oth`` with a
    table; the admin profile unlocked."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"stuxdm4_{secrets.token_hex(4)}"
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
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    found = Pg(env, admin, prefix, password)
    try:
        owner = sqlalchemy.create_engine(
            url.set(drivername="postgresql+psycopg", database=f"{prefix}_src"), poolclass=sqlalchemy.pool.NullPool
        )
        with owner.begin() as connection:
            for statement in (
                "CREATE TABLE parts (id serial PRIMARY KEY, material text NOT NULL)",
                f"GRANT SELECT ON parts TO {prefix}_adm WITH GRANT OPTION",
            ):
                connection.exec_driver_sql(statement)
        owner.dispose()
        found.sql("CREATE TABLE other_parts (id integer PRIMARY KEY)", f"{prefix}_oth")
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


def test_a_login_role_is_created_without_the_password_leaking(pg: Pg, caplog: pytest.LogCaptureFixture) -> None:
    """GS4-11/GS4-17: preview, typed name, apply; the role logs in; the canary reaches no answer, audit or log."""
    caplog.set_level(logging.DEBUG)
    role = pg.name("app")
    canary = f"role-canary-{secrets.token_hex(12)}"
    seen = pg.preview(action="create", role=role, options={"login": True})
    assert seen.status_code == 200, seen.text
    assert seen.json()["refusals"] == [] and seen.json()["target"] is None and seen.json()["typed"] == role
    response = pg.apply(action="create", role=role, options={"login": True}, password=canary)
    assert response.status_code == 200, response.text
    assert response.json()["after"]["attributes"]
    assert pg.exists(role)
    url = sqlalchemy.make_url(PG_URL or "")
    with psycopg.connect(
        host=url.host, port=url.port, dbname=f"{pg.prefix}_src", user=role, password=canary
    ) as connection:
        assert connection.execute("SELECT current_user").fetchone() == (role,)
    runs = pg.runs()
    starts = [r for r in runs if r.get("action") == "dbm.role.create"]
    assert starts and any(r.get("password_set") is True for r in starts), runs
    for text in (seen.text, response.text, str(runs), caplog.text):
        assert canary not in text
    assert pg.password not in caplog.text


def test_alter_membership_and_drop_round_trip(pg: Pg) -> None:
    group, app = pg.name("grp"), pg.name("app")
    for role in (group, app):
        assert pg.apply(action="create", role=role).status_code == 200
    partial = pg.env.send("POST", PREVIEW, pg.body(action="alter", role=app, options={"login": True}))
    assert _issues(partial) == [("role.options", "role.options_incomplete")]
    options = {"login": True, "inherit": True, "create_db": False, "connection_limit": 3, "valid_until": None}
    altered = pg.apply(action="alter", role=app, options=options)
    assert altered.status_code == 200, altered.text
    assert pg.sql("SELECT rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname = %s", params=(app,)) == [(True, 3)]
    granted = pg.apply(action="grant_membership", role=group, member=app)
    assert granted.status_code == 200, granted.text
    assert group in pg.member_of(app)
    assert granted.json()["differences"] is not None
    revoked = pg.apply(action="revoke_membership", role=group, member=app)
    assert revoked.status_code == 200, revoked.text
    assert group not in pg.member_of(app)
    mismatch = pg.apply(action="drop", role=app, typed=app[:-1])
    assert mismatch.json()["code"] == "studio.dbm_role_name_mismatch"
    assert pg.exists(app)
    dropped = pg.apply(action="drop", role=app)
    assert dropped.status_code == 200, dropped.text
    assert not pg.exists(app)
    actions = {r.get("action") for r in pg.runs()}
    assert {"dbm.role.alter", "dbm.role.grant_membership", "dbm.role.revoke_membership", "dbm.role.drop"} <= actions


def test_a_table_privilege_is_granted_and_revoked_in_the_connected_database(pg: Pg) -> None:
    app = pg.name("app")
    assert pg.apply(action="create", role=app).status_code == 200
    grant: dict[str, Any] = {"action": "grant_privileges", "role": app, "on": "table", "privileges": ["SELECT"]}
    granted = pg.apply(**grant, objects=["public.parts"])
    assert granted.status_code == 200, granted.text
    check = "SELECT has_table_privilege(%s, 'public.parts', 'SELECT')"
    assert pg.sql(check, f"{pg.prefix}_src", (app,)) == [(True,)]
    denied = pg.apply(**{**grant, "privileges": ["INSERT"]}, objects=["public.parts"])
    assert denied.json()["code"] == "studio.dbm_role_denied", denied.text
    revoked = pg.apply(**{**grant, "action": "revoke_privileges"}, objects=["public.parts"])
    assert revoked.status_code == 200, revoked.text
    assert pg.sql(check, f"{pg.prefix}_src", (app,)) == [(False,)]
    warned = pg.preview(
        action="grant_privileges", role=app, on="database", privileges=["CREATE"], objects=[f"{pg.prefix}_src"]
    )
    assert warned.status_code == 200, warned.text
    assert "create_on_database" in warned.json()["warnings"]
    missing = pg.preview(**grant, objects=["public.nothing_here"])
    assert missing.status_code == 200
    applied = pg.apply(**grant, objects=["public.nothing_here"])
    assert applied.json()["code"] == "studio.dbm_role_object_missing", applied.text


@pytest.mark.parametrize("option", ["superuser", "bypassrls", "createrole", "replication"])
def test_a_banned_attribute_is_refused(pg: Pg, option: str) -> None:
    role = pg.name("bad")
    response = pg.env.send("POST", PREVIEW, pg.body(action="create", role=role, options={option: True}))
    assert response.json()["code"] == "studio.dbm_role_attribute_banned"
    assert not pg.exists(role)


@pytest.mark.parametrize("group", ["pg_read_server_files", "pg_write_server_files", "pg_execute_server_program"])
def test_a_membership_in_a_server_access_role_is_refused(pg: Pg, group: str) -> None:
    app = pg.name("app")
    assert pg.apply(action="create", role=app).status_code == 200
    seen = pg.preview(action="grant_membership", role=group, member=app)
    assert "privileged_role" in seen.json()["refusals"], seen.text
    response = pg.env.send(
        "POST",
        APPLY,
        pg.body(action="grant_membership", role=group, member=app, state=seen.json()["state"], typed=app),
    )
    assert response.json()["code"] == "studio.dbm_role_unsafe"
    assert group not in pg.member_of(app)


def test_a_membership_in_a_role_with_a_dangerous_attribute_is_refused(pg: Pg) -> None:
    replicator, app = pg.name("rep"), pg.name("app")
    pg.sql(f"CREATE ROLE {replicator} REPLICATION")
    assert pg.apply(action="create", role=app).status_code == 200
    response = pg.apply(action="grant_membership", role=replicator, member=app)
    assert response.json()["code"] == "studio.dbm_role_unsafe", response.text
    assert replicator not in pg.member_of(app)


def test_a_group_with_trigger_in_another_database_is_refused(pg: Pg) -> None:
    """K-2 (P90-04): the group holds TRIGGER on a table of ``<p>_oth``, which is not the connected database."""
    group, app = pg.name("grp"), pg.name("app")
    for role in (group, app):
        assert pg.apply(action="create", role=role).status_code == 200
    pg.sql(f"GRANT TRIGGER ON other_parts TO {group}", f"{pg.prefix}_oth")
    seen = pg.preview(action="grant_membership", role=group, member=app)
    assert seen.status_code == 200, seen.text
    assert "escalation_grant" in seen.json()["refusals"]
    assert f"{pg.prefix}_oth" in seen.json()["k2"]["checked"]
    assert any(e.startswith(f"{pg.prefix}_oth:") for e in seen.json()["k2"]["found"])
    response = pg.apply(action="grant_membership", role=group, member=app)
    assert response.json()["code"] == "studio.dbm_role_unsafe"
    assert ("role.target", "role.escalation_grant") in _issues(response)
    assert group not in pg.member_of(app)
    trigger = pg.env.send(
        "POST",
        PREVIEW,
        pg.body(action="grant_privileges", role=app, on="table", privileges=["TRIGGER"], objects=["public.parts"]),
    )
    assert trigger.json()["code"] == "studio.dbm_role_unsafe"


@pytest.mark.parametrize("which", ["adm", "read"])
def test_studios_own_roles_are_never_changed(pg: Pg, which: str) -> None:
    """GS4-13: the admin role and the read profile's user (also CURRENT_USER and SESSION_USER)."""
    role = pg.name("adm") if which == "adm" else sqlalchemy.make_url(PG_URL or "").username
    assert role is not None
    seen = pg.preview(action="drop", role=role)
    assert "own_role" in seen.json()["refusals"], seen.text
    response = pg.apply(action="drop", role=role)
    assert response.json()["code"] == "studio.dbm_role_not_allowed"
    assert ("role.target", "role.own_role") in _issues(response)
    assert pg.exists(role)


def test_a_changed_role_after_the_preview_is_refused(pg: Pg) -> None:
    group, app, other = pg.name("grp"), pg.name("app"), pg.name("oth")
    for role in (group, app, other):
        assert pg.apply(action="create", role=role).status_code == 200
    seen = pg.preview(action="grant_membership", role=group, member=app)
    pg.sql(f"GRANT {other} TO {app}")
    response = pg.env.send(
        "POST",
        APPLY,
        pg.body(action="grant_membership", role=group, member=app, state=seen.json()["state"], typed=app),
    )
    assert response.json()["code"] == "studio.dbm_role_changed"
    assert group not in pg.member_of(app)


def test_a_hidden_name_is_escaped_in_the_list_and_refused(pg: Pg) -> None:
    hidden = f"{pg.prefix}_hid{HIDDEN}den"
    pg.sql(f'CREATE ROLE "{hidden}"')
    listing = pg.env.send("POST", ROLES, pg.body())
    assert listing.status_code == 200, listing.text
    rows = [r for r in listing.json()["roles"] if r["name"] == hidden]
    assert rows and rows[0]["hidden"] is True and rows[0]["display"] == f"{pg.prefix}_hid<U+200B>den"
    own = {r["name"] for r in listing.json()["roles"] if r["own"]}
    assert pg.name("adm") in own
    response = pg.env.send("POST", PREVIEW, pg.body(action="drop", role=hidden))
    assert response.json()["code"] == "studio.dbm_role_name_invalid"
    assert pg.exists(hidden)


def test_a_server_a_prod_profile_names_under_an_alias_is_refused(pg: Pg) -> None:
    url = sqlalchemy.make_url(PG_URL or "")
    alias = "localhost" if url.host in ("127.0.0.1", "::1") else "127.0.0.1"
    prod = {
        "name": "pg-prod",
        "engine": "postgresql",
        "host": alias,
        "port": url.port,
        "database": "postgres",
        "user": url.username,
        "environment": "prod",
        "sslmode": "prefer",
    }
    assert pg.env.send("POST", "/api/profiles", {"profile": prod, "digest": pg.env.user_digest()}).status_code == 201
    for path, body in (
        (ROLES, pg.body()),
        (PREVIEW, pg.body(action="create", role=pg.name("app"))),
        (APPLY, pg.body(action="create", role=pg.name("app"), typed=pg.name("app"), state="0" * 64)),
    ):
        response = pg.env.send("POST", path, body)
        assert response.json()["code"] == "studio.dbm_role_not_allowed", response.text
        assert _issues(response) == [("role.target", "role.prod")]
    assert not pg.exists(pg.name("app"))


def test_an_admin_role_that_became_superuser_is_refused(pg: Pg) -> None:
    pg.sql(f"ALTER ROLE {pg.prefix}_adm SUPERUSER")
    try:
        response = pg.env.send("POST", PREVIEW, pg.body(action="create", role=pg.name("app")))
    finally:
        pg.sql(f"ALTER ROLE {pg.prefix}_adm NOSUPERUSER")
    assert (response.status_code, response.json()["code"]) == (405, "studio.admin_over_privileged")
    assert pg.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    assert not pg.exists(pg.name("app"))


def test_an_admin_role_with_a_server_access_role_is_refused(pg: Pg) -> None:
    pg.sql(f"GRANT pg_read_server_files TO {pg.prefix}_adm")
    try:
        response = pg.env.send("POST", ROLES, pg.body())
    finally:
        pg.sql(f"REVOKE pg_read_server_files FROM {pg.prefix}_adm")
    assert (response.status_code, response.json()["code"]) == (405, "studio.admin_over_privileged")


@pytest.mark.parametrize(
    ("on", "privilege", "target", "reason"),
    [
        ("function", "EXECUTE", "public.definer()", "role.security_definer"),
        ("schema", "CREATE", "public", "role.schema_create"),
        ("table", "SELECT", "pg_catalog.pg_class", "role.system_object"),
    ],
)
def test_tabrions_privilege_refusals_reach_the_route(pg: Pg, on: str, privilege: str, target: str, reason: str) -> None:
    """GS4-12 through the routes: EXECUTE on a foreign SECURITY DEFINER function, CREATE on ``public``, a system
    object; nothing granted."""
    app = pg.name("app")
    pg.sql(
        "CREATE FUNCTION public.definer() RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
        f"{pg.prefix}_src",
    )
    assert pg.apply(action="create", role=app).status_code == 200
    response = pg.apply(action="grant_privileges", role=app, on=on, privileges=[privilege], objects=[target])
    assert response.json()["code"] == "studio.dbm_role_unsafe", response.text
    assert reason in {code for _pointer, code in _issues(response)}, response.text
    acl = pg.sql(
        "SELECT count(*) FROM information_schema.role_routine_grants WHERE grantee = %s", f"{pg.prefix}_src", (app,)
    )
    assert acl == [(0,)]
    assert pg.sql("SELECT has_schema_privilege(%s, 'public', 'CREATE')", f"{pg.prefix}_src", (app,)) == [(False,)]


def test_a_change_after_tabrions_commit_is_shown_as_a_difference(pg: Pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """GS4-15 (P90-06): another session adds a membership between Tabrion's commit and Studio's read after it."""
    from tabrion_studio.write.database_manager import roles

    group, app, other = pg.name("grp"), pg.name("app"), pg.name("oth")
    for role in (group, app, other):
        assert pg.apply(action="create", role=role).status_code == 200
    real = roles._call

    def call_then_meddle(request: Any, connection: Any, password: str | None) -> Any:
        result = real(request, connection, password)
        pg.sql(f"GRANT {other} TO {app}")
        return result

    monkeypatch.setattr(roles, "_call", call_then_meddle)
    response = pg.apply(action="grant_membership", role=group, member=app)
    assert response.status_code == 200, response.text
    assert "member_of" in response.json()["differences"]
    assert any(r.get("differences") for r in pg.runs() if r.get("action") == "dbm.role.grant_membership")


@pytest.fixture
def above_reader(pg: Pg) -> tuple[str, str]:
    """``<p>_grp`` above the read profile's user (made by the superuser) and ``<p>_top`` above ``<p>_grp``, so the
    profile user reaches both, the second only through the first (review L-1 of #149)."""
    reader = sqlalchemy.make_url(PG_URL or "").username
    group, top = pg.name("grp"), pg.name("top")
    pg.sql(f"CREATE ROLE {group}")
    pg.sql(f"CREATE ROLE {top}")
    pg.sql(f'GRANT {group} TO "{reader}"')
    pg.sql(f"GRANT {top} TO {group}")
    return group, top


@pytest.mark.parametrize("which", ["grp", "top"])
def test_a_privilege_for_a_group_above_an_own_role_is_refused(
    pg: Pg, above_reader: tuple[str, str], which: str
) -> None:
    """L-1 path 1: a privilege given to a group the profile user is in (directly or through another group)."""
    group = above_reader[0] if which == "grp" else above_reader[1]
    fields: dict[str, Any] = {"action": "grant_privileges", "role": group, "on": "table", "privileges": ["SELECT"]}
    seen = pg.preview(**fields, objects=["public.parts"])
    assert "own_role_reached" in seen.json()["refusals"], seen.text
    response = pg.apply(**fields, objects=["public.parts"])
    assert response.json()["code"] == "studio.dbm_role_not_allowed", response.text
    assert ("role.target", "role.own_role_reached") in _issues(response)
    check = "SELECT has_table_privilege(%s, 'public.parts', 'SELECT')"
    assert pg.sql(check, f"{pg.prefix}_src", (group,)) == [(False,)]


def test_a_membership_for_a_group_above_an_own_role_is_refused(pg: Pg, above_reader: tuple[str, str]) -> None:
    """L-1 path 2: ``GRANT x TO <group>`` would make the profile user reach ``x``."""
    group, _top = above_reader
    extra = pg.name("x")
    assert pg.apply(action="create", role=extra).status_code == 200
    seen = pg.preview(action="grant_membership", role=extra, member=group)
    assert "own_role_reached" in seen.json()["refusals"], seen.text
    response = pg.apply(action="grant_membership", role=extra, member=group)
    assert response.json()["code"] == "studio.dbm_role_not_allowed"
    assert extra not in pg.member_of(group)
    reader = sqlalchemy.make_url(PG_URL or "").username
    assert pg.sql("SELECT pg_has_role(%s, %s, 'MEMBER')", params=(reader, extra)) == [(False,)]


@pytest.mark.parametrize("action", ["drop", "alter", "revoke_privileges", "revoke_membership"])
def test_changing_a_group_above_an_own_role_is_refused(pg: Pg, above_reader: tuple[str, str], action: str) -> None:
    """Dropping, changing or revoking from such a group changes what Studio's own role can do: refused too."""
    group, top = above_reader
    fields: dict[str, Any] = {"action": action, "role": group}
    if action == "alter":
        fields["options"] = {
            "login": False,
            "inherit": True,
            "create_db": False,
            "connection_limit": None,
            "valid_until": None,
        }
    elif action == "revoke_privileges":
        fields |= {"on": "table", "privileges": ["SELECT"], "objects": ["public.parts"]}
    elif action == "revoke_membership":
        fields = {"action": action, "role": top, "member": group}
    seen = pg.preview(**fields)
    assert "own_role_reached" in seen.json()["refusals"], seen.text
    assert pg.apply(**fields).json()["code"] == "studio.dbm_role_not_allowed"
    assert pg.exists(group) and top in pg.member_of(group)


def test_a_role_the_admin_created_is_not_its_own(pg: Pg) -> None:
    """PostgreSQL 16 makes the creating admin role a member of a new role with only the ADMIN option (no INHERIT,
    no SET): that passes no rights on, so the role stays manageable."""
    app = pg.name("app")
    assert pg.apply(action="create", role=app).status_code == 200
    assert pg.sql("SELECT pg_has_role(%s, %s, 'MEMBER')", params=(pg.name("adm"), app)) == [(True,)]
    seen = pg.preview(action="drop", role=app)
    assert seen.json()["refusals"] == [], seen.text


def test_a_profile_without_a_user_counts_its_os_user(pg: Pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review I-2 of #149: libpq takes the OS user for a profile without ``user``; that role is Studio's own, and
    when the OS user cannot be known, every role change is refused (fail closed)."""
    from tabrion_studio.write.database_manager import roles

    url = sqlalchemy.make_url(PG_URL or "")
    osu = pg.name("osu")
    pg.sql(f"CREATE ROLE {osu}")
    bare = {
        "name": "pg-bare",
        "engine": "postgresql",
        "host": url.host,
        "port": url.port,
        "database": f"{pg.prefix}_src",
        "environment": "dev",
        "sslmode": "prefer",
    }
    assert pg.env.send("POST", "/api/profiles", {"profile": bare, "digest": pg.env.user_digest()}).status_code == 201
    monkeypatch.setattr(roles, "_os_user", lambda: osu)
    seen = pg.preview(action="drop", role=osu)
    assert "own_role" in seen.json()["refusals"], seen.text
    listing = pg.env.send("POST", ROLES, pg.body()).json()
    assert {r["name"] for r in listing["roles"] if r["own"]} >= {osu, pg.name("adm")}
    monkeypatch.setattr(roles, "_os_user", lambda: None)
    app = pg.name("app")
    seen = pg.preview(action="create", role=app)
    assert "own_role_unknown" in seen.json()["refusals"], seen.text
    response = pg.apply(action="create", role=app)
    assert response.json()["code"] == "studio.dbm_role_not_allowed"
    assert ("role.target", "role.own_role_unknown") in _issues(response)
    assert not pg.exists(app)
