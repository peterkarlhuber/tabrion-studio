"""STU-CR-5 against PostgreSQL: the confirmed plan run directly on a DEV database (STU-ADR-0007 sections 4 to 11).

* an additive plan and a plan with a ``locking`` step run through ``tabrion.plan.execute_plan`` with the recorded
  hash, the confirmed source fingerprint, ``dev`` from the stored profile, the server's version and
  ``concurrent_indexes=False`` (GS3-10, GS3-13);
* a plan with a ``destructive``, ``rebuild`` or ``data_changing`` step is refused (``backup_unavailable``) while
  ``database-tools.toml`` names no ``pg_dump`` and ``pg_restore``: no backup, no such run (fail closed; with the tools
  the backup is made first, ``test_schema_designer_backup_postgres.py``, STU-CR-5 follow-up);
* a lock the run cannot get within ``lock_timeout`` stops it and changes nothing (GS3-18);
* decision 1A again at the start of the run, on the session that runs it: a role made superuser after the unlock is
  refused and the unlock ends (GS3-28);
* a trigger added after the confirmation is drift (GS3-11);
* a Levuvo version table (Orkexa's ``orkexa_schema`` too) or an ``alembic_version`` table in any schema refuses
  direct execution (GS3-04);
* the preview warns about code PostgreSQL does not track (functions) and dependent code (GS3-16).

The roles and the schema are made by the test server's superuser and dropped again (marker ``postgres_superuser``).
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import secrets
import threading
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.write_areas import Area, WriteAreas
from tests.support.write import WriteEnv

pytestmark = [pytest.mark.security, pytest.mark.postgres_superuser]

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
DRAFT = "/api/write/schema-designer/draft"
CONFIRM = "/api/write/schema-designer/plan/confirm"
RUN = "/api/write/schema-designer/plan/run"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
ADD_QTY = {"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "integer"}}


class Env:
    def __init__(self, env: WriteEnv, connection_id: str, admin: sqlalchemy.Engine, prefix: str) -> None:
        self.env, self.connection_id, self.admin, self.prefix = env, connection_id, admin, prefix

    def send(self, path: str, **body: Any) -> Any:
        return self.env.send("POST", path, {"connection_id": self.connection_id, **body})

    def start(self) -> None:
        response = self.send(DRAFT, schema=self.prefix)
        assert response.status_code == 201, response.text

    def edit(self, edit: dict[str, Any]) -> None:
        revision = self.send(f"{DRAFT}/view").json()["revision"]
        response = self.send(f"{DRAFT}/edits", revision=revision, edit=edit)
        assert response.status_code == 200, response.text

    def preview(self) -> dict[str, Any]:
        response = self.send(f"{DRAFT}/preview")
        assert response.status_code == 200, response.text
        doc: dict[str, Any] = response.json()
        return doc

    def confirm(self, plan: dict[str, Any]) -> Any:
        need = plan["confirmation"]
        return self.send(
            CONFIRM,
            plan_hash=plan["plan_hash"],
            typed=need["profile"],
            tables=need["tables"],
            steps=need["data_changing_steps"],
            locking=need["locking"],
            backup="make",
        )

    def confirmed(self) -> dict[str, Any]:
        plan = self.preview()
        response = self.confirm(plan)
        assert response.status_code == 200, response.text
        return plan

    def run(self, plan: dict[str, Any], dry_run: bool = False) -> Any:
        return self.send(RUN, plan_hash=plan["plan_hash"], dry_run=dry_run)

    def sql(self, *statements: str) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        with self.admin.connect() as connection:
            for statement in statements:
                result = connection.exec_driver_sql(statement)
                rows = [tuple(row) for row in result.all()] if result.returns_rows else []
        return rows

    def columns(self, table: str = "parts") -> list[str]:
        return [
            row[0]
            for row in self.sql(
                "SELECT column_name FROM information_schema.columns "  # noqa: S608 - the test's own names
                f"WHERE table_schema = '{self.prefix}' AND table_name = '{table}' ORDER BY ordinal_position"
            )
        ]

    def runs(self) -> list[dict[str, Any]]:
        answer = self.env.send("GET", "/api/write/audit?limit=1000")
        assert answer.status_code == 200, answer.text
        return [r for r in answer.json()["records"] if r["event"].startswith("run.")]


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert response.status_code == status, response.text
    doc = response.json()
    assert doc["code"] == code, response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in doc.get("issues", [])] == issues


@pytest.fixture
def pg(tmp_path: Path, static_dir: Path) -> Iterator[Env]:
    """A schema ``<p>`` with ``parts`` (owned by the write role ``<p>_w``, which may create in the schema), coupled to
    a DEV read profile of the test database, unlocked for the Schema Designer."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"stucr5_{secrets.token_hex(4)}"
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
            f"GRANT USAGE, CREATE ON SCHEMA {prefix} TO {prefix}_w",
            f"CREATE TABLE {prefix}.parts (id integer PRIMARY KEY, material text NOT NULL)",
            f"INSERT INTO {prefix}.parts VALUES (1, 'Steel'), (2, 'Oak')",  # noqa: S608
            f"ALTER TABLE {prefix}.parts OWNER TO {prefix}_w",
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
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {prefix}_other CASCADE")
            connection.exec_driver_sql(f"DROP OWNED BY {prefix}_w")
            connection.exec_driver_sql(f"DROP ROLE IF EXISTS {prefix}_w")
        admin.dispose()


def _server_version(admin: sqlalchemy.Engine) -> tuple[int, ...]:
    with admin.connect() as connection:
        number = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    return (
        (number // 10000, number % 10000) if number >= 100000 else (number // 10000, number // 100 % 100, number % 100)
    )


def test_an_additive_plan_runs_with_the_recorded_hash_and_the_servers_version(
    pg: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.schema_designer import execute

    seen: list[dict[str, Any]] = []
    from tabrion.plan import execute_plan as real

    def spy(database: Any, target: Any, **options: Any) -> Any:
        seen.append(options)
        return real(database, target, **options)

    monkeypatch.setattr(execute, "execute_plan", spy)
    pg.start()
    pg.edit(ADD_QTY)
    plan = pg.confirmed()
    assert plan["executable"] is True and plan["confirmation"]["backup"]["required"] is False
    response = pg.run(plan)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["result"] == "committed" and doc["backup"] == "not_needed" and doc["backup_path"] is None
    assert pg.columns() == ["id", "material", "qty"]
    assert pg.sql(f"SELECT count(*) FROM {pg.prefix}.parts") == [(2,)]  # noqa: S608
    (options,) = seen
    assert options["plan_hash"] == plan["plan_hash"] and options["environment"] == "dev"
    assert options["expected_fingerprint"] == plan["source_fingerprint"]
    assert options["server_version"] == _server_version(pg.admin) and options["concurrent_indexes"] is False
    assert options["schemas"] == [pg.prefix] and options["dry_run"] is False
    assert [r["fields"].get("result") for r in reversed(pg.runs())] == [None, "committed"]  # newest first


def test_a_locking_step_runs_after_its_acknowledgement(pg: Env) -> None:
    pg.start()
    pg.edit({"op": "add_index", "table": "parts", "index": {"name": "parts_material", "columns": ["material"]}})
    plan = pg.preview()
    assert plan["executable"] is True and plan["confirmation"]["locking"] is True
    assert {"code": "locking", "table": None, "lock_timeout_s": 5.0} in plan["warnings"]
    need = plan["confirmation"]
    unacknowledged = pg.send(
        CONFIRM, plan_hash=plan["plan_hash"], typed=need["profile"], tables=need["tables"], steps=[], locking=False
    )
    _refused(unacknowledged, 400, "studio.plan_confirmation_mismatch")
    plan = pg.confirmed()
    response = pg.run(plan)
    assert response.status_code == 200, response.text
    indexes = pg.sql(f"SELECT indexname FROM pg_indexes WHERE schemaname = '{pg.prefix}' ORDER BY 1")  # noqa: S608
    assert ("parts_material",) in indexes


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "drop_column", "table": "parts", "column": "material"},
        {"op": "drop_table", "table": "parts"},
        {"op": "alter_column", "table": "parts", "column": "material", "type": "varchar(40)"},
    ],
    ids=["drop_column", "drop_table", "alter_type"],
)
def test_a_plan_that_needs_a_backup_never_runs_without_the_tools(pg: Env, edit: dict[str, Any]) -> None:
    """STU-ADR-0008 section 3 (the STU-CR-5 follow-up): no tools in ``database-tools.toml``, no backup, no
    destructive, rebuild or data-changing run."""
    pg.start()
    pg.edit(edit)
    plan = pg.preview()
    assert plan["executable"] is False and "backup_unavailable" in plan["blocked"]
    assert any(w["code"] == "pg_dependent_code" and w["table"] == "parts" for w in plan["warnings"])
    _refused(pg.confirm(plan), 400, "studio.schema_run_not_allowed", [("plan.reason", "plan.backup_unavailable")])
    _refused(pg.run(plan), 400, "studio.plan_not_confirmed")
    assert pg.columns() == ["id", "material"] and pg.runs() == []


def test_a_lock_the_run_cannot_get_stops_it_and_changes_nothing(pg: Env) -> None:
    """GS3-18: ``lock_timeout`` (5 s) on the run's session; another session holds the table's lock."""
    pg.start()
    pg.edit(ADD_QTY)
    plan = pg.confirmed()
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        engine = sqlalchemy.create_engine(pg.admin.url)  # not autocommit: the lock lasts until the rollback
        try:
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(f"LOCK TABLE {pg.prefix}.parts IN ACCESS EXCLUSIVE MODE")
                held.set()
                release.wait(30)
        finally:
            engine.dispose()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        response = pg.run(plan)
    finally:
        release.set()
        holder.join()
    _refused(response, 409, "db.locked")  # tabrion's lock_timeout (SQLSTATE 55P03), rolled back
    assert pg.columns() == ["id", "material"]
    assert pg.runs()[0]["fields"]["result"] == "db.locked"  # newest first


def test_the_write_role_is_checked_again_when_the_run_starts(pg: Env) -> None:
    """GS3-28, decision 1A: a role made superuser after the unlock is refused on the run's own session, nothing is
    changed and the unlock ends."""
    pg.start()
    pg.edit(ADD_QTY)
    plan = pg.confirmed()
    pg.sql(f"ALTER ROLE {pg.prefix}_w SUPERUSER")
    try:
        _refused(pg.run(plan), 405, "studio.write_role_over_privileged")
    finally:
        pg.sql(f"ALTER ROLE {pg.prefix}_w NOSUPERUSER")
    assert pg.columns() == ["id", "material"]
    _refused(pg.send(f"{DRAFT}/view"), 405, "studio.write_locked")


def test_a_change_made_after_the_confirmation_is_drift_or_a_refusal(pg: Env) -> None:
    """GS3-11: a column added by someone else after the confirmation changes the plan (drift); a trigger added
    after it is a feature tabrion refuses on PostgreSQL. Either way nothing runs."""
    pg.start()
    pg.edit(ADD_QTY)
    plan = pg.confirmed()
    pg.sql(f"ALTER TABLE {pg.prefix}.parts ADD COLUMN note text")
    _refused(pg.run(plan), 400, "studio.plan_drift")
    assert pg.columns() == ["id", "material", "note"]
    pg.sql(f"ALTER TABLE {pg.prefix}.parts DROP COLUMN note")
    plan = pg.confirmed()
    pg.sql(
        f"CREATE FUNCTION {pg.prefix}.touch() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        f"CREATE TRIGGER parts_touch BEFORE UPDATE ON {pg.prefix}.parts FOR EACH ROW EXECUTE FUNCTION "
        f"{pg.prefix}.touch()",
    )
    issues = [
        ("plan.reason", "plan.unsupported_feature"),
        ("plan.feature", "plan.trigger"),
        ("tables.0", "plan.unsupported_feature"),
    ]
    _refused(pg.run(plan), 400, "studio.schema_plan_refused", issues)
    assert pg.columns() == ["id", "material"] and pg.runs() == []


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        ("CREATE TABLE {p}.orkexa_schema (version integer)", "levuvo_managed"),
        ("CREATE TABLE {p}.billing_orkexa_schema (version integer)", "levuvo_managed"),
        ("CREATE TABLE {p}_other.alembic_version (version_num varchar(32))", "migration_managed"),
    ],
)
def test_a_version_table_in_any_schema_refuses_direct_execution(pg: Env, statement: str, reason: str) -> None:
    """GS3-04: tabrion's markers and Levuvo's version tables, also outside the draft's schema, found right before
    the run."""
    pg.sql(f"CREATE SCHEMA {pg.prefix}_other")
    pg.start()
    pg.edit(ADD_QTY)
    plan = pg.confirmed()
    pg.sql(statement.format(p=pg.prefix))
    _refused(pg.run(plan), 400, "studio.schema_run_not_allowed", [("plan.reason", f"plan.{reason}")])
    assert pg.columns() == ["id", "material"]


def test_the_preview_warns_about_code_postgresql_does_not_track(pg: Env) -> None:
    pg.start()
    pg.edit(ADD_QTY)
    codes = [w["code"] for w in pg.preview()["warnings"]]
    # ``ADD COLUMN`` takes PostgreSQL's ACCESS EXCLUSIVE lock: tabrion marks it ``locking``.
    assert codes == ["not_modelled", "function_drift", "fingerprint_gaps", "locking"]
