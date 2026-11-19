"""STU-CR-5: confirming and running a Schema Designer plan on SQLite (G-S3 effective, flag off; GS3-01, GS3-03..05,
GS3-09..11, GS3-13..17, GS3-19, GS3-20, GS3-27, GS3-29; TX-18, TX-21..TX-27; STU-ADR-0007 sections 4 to 11).

1. **Kill switch (GS3-01).** With the ``schema_designer`` flag off the confirmation and run routes answer 405 and
   nothing of the area, ``tabrion.plan`` or ``tabrion.ddl`` is imported.
2. **Direct execution only (GS3-03, GS3-04, GS3-05).** STAGE previews but never runs; each migration marker, an
   Alembic project and a marker added between the preview and the run refuse; ``execute_plan`` gets the stored
   environment, and a request that names one is refused.
3. **Re-plan and binding (GS3-09..GS3-11).** A database change after the confirmation refuses the run before any
   statement (``studio.plan_drift``); the hash compared is the server's record; a hash of another session, of an
   earlier unlock, a reused hash, a run without a confirmation are refused.
4. **Risk classes (GS3-14).** The typed write profile name, the typed name of each table a ``destructive`` or
   ``rebuild`` step changes, each ``data_changing`` step: any wrong or missing part refuses and the plan stays.
5. **Backup (GS3-15).** Made before a run that needs it (folder 0700, file 0600, the old schema in it); a failed
   backup stops the run; leaving it out only on ``dev`` with the file's name typed, audited.
6. **The run (GS3-13, GS3-17, GS3-27).** ``execute_plan`` with the recorded hash, the preview's source fingerprint, the
   confirmed classes, ``server_version`` and ``concurrent_indexes=False`` (a spy); a rebuild keeps the rows, the
   triggers, the views and the child rows of a cascading foreign key; ``run.start`` and ``run.result`` with the hash,
   the tables, the classes and the step count; an unwritable audit log stops the run.
7. **Dry run (GS3-29)** only on request: rolled back, audited as ``schema_designer.dry_run``, no backup.
8. **Warnings and errors (GS3-16, GS3-20), canaries (GS3-19).** The preview warnings; a refusal names the table by its
   position only; no default text, statement or name reaches the log, the audit log or the history.

Python 3.11 refuses every SQLite write unlock (no defensive mode), so these tests skip there.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, WriteEnv, digest

pytestmark = pytest.mark.security

DRAFT = "/api/write/schema-designer/draft"
CONFIRM = "/api/write/schema-designer/plan/confirm"
RUN = "/api/write/schema-designer/plan/run"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
ROOT = Path(__file__).resolve().parents[2]
POSIX = sys.platform != "win32"


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


def _sql(path: Path, *statements: str) -> list[tuple[Any, ...]]:
    """Statements on the test's own connection to the file (not Studio's); the rows of the last one."""
    connection = sqlite3.connect(path)
    try:
        rows: list[tuple[Any, ...]] = []
        for statement in statements:
            rows = connection.execute(statement).fetchall()
        connection.commit()
    finally:
        connection.close()
    return rows


def _schema(path: Path) -> list[tuple[Any, ...]]:
    return _sql(path, "SELECT type, name, sql FROM sqlite_master ORDER BY type, name")


class Runner:
    """A Schema Designer env with ``parts`` unlocked and helpers for the draft, confirmation and run routes."""

    def __init__(self, env: WriteEnv, connection_id: str) -> None:
        self.env, self.connection_id = env, connection_id

    def send(self, path: str, **body: Any) -> Any:
        return self.env.send("POST", path, {"connection_id": self.connection_id, **body})

    def start(self) -> None:
        response = self.send(DRAFT)
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

    def confirm(self, plan: dict[str, Any], **override: Any) -> Any:
        need = plan["confirmation"]
        body: dict[str, Any] = {
            "plan_hash": plan["plan_hash"],
            "typed": need["profile"],
            "tables": need["tables"],
            "steps": need["data_changing_steps"],
            "locking": need["locking"],
            "backup": "make",
        }
        body.update(override)
        return self.send(CONFIRM, **{k: v for k, v in body.items() if v is not None})

    def run(self, plan: dict[str, Any], dry_run: bool = False) -> Any:
        return self.send(RUN, plan_hash=plan["plan_hash"], dry_run=dry_run)

    def confirmed(self, **override: Any) -> dict[str, Any]:
        plan = self.preview()
        response = self.confirm(plan, **override)
        assert response.status_code == 200, response.text
        return plan

    def records(self) -> list[dict[str, Any]]:
        answer = self.env.send("GET", "/api/write/audit?limit=1000")
        assert answer.status_code == 200, answer.text
        records: list[dict[str, Any]] = answer.json()["records"]
        return records

    def runs(self) -> list[dict[str, Any]]:
        return [r for r in self.records() if r["event"].startswith("run.")]

    @property
    def backups(self) -> Path:
        return self.env.config_dir / "backups" / "schema-designer"


def _env(tmp_path: Path, static_dir: Path, environment: str = "dev", areas: WriteAreas = SCHEMA_DESIGNER) -> Runner:
    env = WriteEnv(tmp_path, static_dir, areas)
    env.create_read(environment=environment)
    connection_id = env.open()
    assert env.create_write().status_code == 201
    assert env.unlock(connection_id).status_code == 200
    return Runner(env, connection_id)


@pytest.fixture
def runner(tmp_path: Path, static_dir: Path) -> Iterator[Runner]:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    found = _env(tmp_path, static_dir)
    try:
        yield found
    finally:
        found.env.close()


DROP_MATERIAL = {"op": "drop_column", "table": "parts", "column": "material"}
ADD_QTY = {"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}}


# --- 1. the kill switch (GS3-01) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("areas", [WriteAreas(), DATA_EDITOR], ids=["every_flag_off", "data_editor_only"])
def test_flag_off_the_run_routes_are_405_and_change_nothing(
    tmp_path: Path, static_dir: Path, areas: WriteAreas
) -> None:
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        before = digest(env.db)
        for path, body in (
            (CONFIRM, {"plan_hash": "sha256:" + "0" * 64, "typed": "parts-write", "tables": ["parts"]}),
            (RUN, {"plan_hash": "sha256:" + "0" * 64, "dry_run": False}),
        ):
            response = env.send("POST", path, {"connection_id": connection_id, **body})
            _refused(response, 405, "studio.method_not_allowed")
        assert digest(env.db) == before
        assert not (env.config_dir / "backups").exists()
    finally:
        env.close()


def test_flag_off_nothing_of_the_executor_is_imported(tmp_path: Path) -> None:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), "
        "WriteAreas(frozenset({Area.DATA_EDITOR})))\n"
        "env.create_read()\n"
        "c = env.open()\n"
        f"for p in ({CONFIRM!r}, {RUN!r}):\n"
        "    r = env.send('POST', p, {'connection_id': c, 'plan_hash': 'x'})\n"
        "    print(r.status_code, r.json()['code'])\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write.schema_designer')\n"
        "             or m in ('tabrion.plan', 'tabrion.ddl')))\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["405 studio.method_not_allowed"] * 2 + ["[]"]


# --- 2. direct execution only (GS3-03, GS3-04, GS3-05) -----------------------------------------------------------


def test_a_plan_runs_on_test_and_never_outside_dev_or_test(tmp_path: Path, static_dir: Path) -> None:
    """GS3-03 and the review note of #132: direct execution on DEV and TEST only, from the stored profile. Studio
    knows no STAGE environment; PROD never gets an unlock (E-X7, ``test_write_unlock.py``), and the executor's own
    gate refuses any other environment an unlock might carry. TEST runs (with its backup, and no opt-out)."""
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    from types import SimpleNamespace

    from tabrion_studio.write.schema_designer.execute import blocked

    test = _env(tmp_path / "test", static_dir, "test")
    try:
        test.start()
        test.edit(ADD_QTY)
        plan = test.confirmed()
        assert plan["executable"] is True and plan["confirmation"]["backup"]["opt_out_allowed"] is False
        response = test.run(plan)
        assert response.status_code == 200, response.text
        assert [c[0] for c in _sql(test.env.db, "SELECT name FROM pragma_table_info('parts')")] == [
            "id",
            "material",
            "qty",
        ]
        unlocks = test.env.studio.client.app.state.write_unlocks  # type: ignore[attr-defined]
        unlock = unlocks.require_unlock(test.env.session(), test.connection_id, Area.SCHEMA_DESIGNER)
        change = SimpleNamespace(steps=[SimpleNamespace(transactional=True, target="main.parts")], risks=["additive"])
        assert blocked(unlock, change, tmp_path) == ()
        for environment in ("stage", "prod", None):
            other = SimpleNamespace(environment=environment, connection=unlock.connection)
            assert blocked(other, change, tmp_path) == ("environment",)  # type: ignore[arg-type]
    finally:
        test.env.close()


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        ("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)", "migration_managed"),
        ("CREATE TABLE NODESTRO_SCHEMA_MIGRATIONS (version INTEGER)", "migration_managed"),
        ("CREATE VIEW axentra_schema_migrations AS SELECT 1 AS version", "migration_managed"),
        ("PRAGMA user_version = 3", "migration_managed"),
    ],
)
def test_each_migration_marker_refuses_direct_execution(runner: Runner, statement: str, reason: str) -> None:
    """GS3-04: tabrion's ``migration_markers``, at the preview and again between the confirmation and the run."""
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    _sql(runner.env.db, statement)  # a marker added after the confirmation
    before = _schema(runner.env.db)
    _refused(runner.run(plan), 400, "studio.schema_run_not_allowed", [("plan.reason", f"plan.{reason}")])
    assert _schema(runner.env.db) == before and not runner.runs()
    # A draft started with the marker in place: the preview says so and the confirmation is refused.
    assert runner.env.send("DELETE", DRAFT, {"connection_id": runner.connection_id}).status_code == 204
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.preview()
    assert plan["executable"] is False and reason in plan["blocked"]
    _refused(runner.confirm(plan), 400, "studio.schema_run_not_allowed")


def test_an_alembic_project_gets_no_direct_execution(runner: Runner) -> None:
    """GS3-04: a project with an ``alembic.ini`` gets revisions only, even when its database has no version table."""
    runner.start()
    runner.edit(ADD_QTY)
    (runner.env.project / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n")
    plan = runner.preview()
    assert plan["blocked"] == ["alembic_project"]
    (runner.env.project / "alembic.ini").unlink()
    plan = runner.confirmed()
    (runner.env.project / "alembic.ini").write_text("")
    _refused(runner.run(plan), 400, "studio.schema_run_not_allowed", [("plan.reason", "plan.alembic_project")])
    assert not runner.runs()


def test_the_environment_comes_from_the_stored_profile_only(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    """GS3-05: a request that names an environment (or any other key) is refused; ``execute_plan`` gets ``dev`` from
    the stored profile, with the recorded hash, the preview's fingerprint, the confirmed classes, the library's
    version and ``concurrent_indexes=False`` (GS3-13)."""
    from tabrion_studio.write.schema_designer import execute

    seen: list[dict[str, Any]] = []
    from tabrion.plan import execute_plan as real

    def spy(database: Any, target: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(database, target, **kwargs)

    monkeypatch.setattr(execute, "execute_plan", spy)
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.preview()
    for key, value in (("environment", "dev"), ("allow", ["additive"]), ("plan", {}), ("target", {})):
        _refused(runner.send(CONFIRM, plan_hash=plan["plan_hash"], **{key: value}), 400, "access.bad_request")
        _refused(runner.send(RUN, plan_hash=plan["plan_hash"], **{key: value}), 400, "access.bad_request")
    assert runner.confirm(plan).status_code == 200
    assert runner.run(plan).status_code == 200
    assert len(seen) == 1
    call = seen[0]
    assert call["environment"] == "dev" and call["plan_hash"] == plan["plan_hash"]
    assert call["expected_fingerprint"] == plan["source_fingerprint"]
    assert {risk.value for risk in call["allow"]} == set(plan["risks"]) == {"additive"}
    assert call["server_version"] == sqlite3.sqlite_version_info and call["concurrent_indexes"] is False
    assert call["schemas"] == ["main"] and call["dry_run"] is False


# --- 3. re-plan, hash compare and binding (GS3-09..GS3-11) ------------------------------------------------------


def test_a_change_after_the_confirmation_refuses_the_run_before_any_statement(runner: Runner) -> None:
    """GS3-10: the database changed after the confirmation: Studio's re-plan finds another hash; nothing runs."""
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    _sql(runner.env.db, "CREATE TABLE added_later (id INTEGER)")
    before = _schema(runner.env.db)
    _refused(runner.run(plan), 400, "studio.plan_drift")
    assert _schema(runner.env.db) == before and not runner.runs()
    # One attempt: the plan is gone.
    _refused(runner.run(plan), 400, "studio.plan_unknown")


def test_the_executors_own_drift_check_is_plan_drift(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    """GS3-10: a change between Studio's re-plan and the executor's transaction is found by tabrion's check after
    ``BEGIN IMMEDIATE`` (``SchemaDrift``): ``studio.plan_drift``, recorded as the run's result; nothing changed."""
    from tabrion.plan import execute_plan as real

    from tabrion_studio.write.schema_designer import execute

    def change_first(database: Any, target: Any, **kwargs: Any) -> Any:
        _sql(runner.env.db, "CREATE TABLE sneaked_in (id INTEGER)")
        return real(database, target, **kwargs)

    monkeypatch.setattr(execute, "execute_plan", change_first)
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    _refused(runner.run(plan), 400, "studio.plan_drift")
    assert [c[0] for c in _sql(runner.env.db, "SELECT name FROM pragma_table_info('parts')")] == ["id", "material"]
    results = [r for r in runner.runs() if r["event"] == "run.result"]
    assert results[0]["fields"]["result"] == "studio.plan_drift"


def test_the_hash_is_the_servers_record_and_binds_session_unlock_and_one_run(
    runner: Runner, tmp_path: Path, static_dir: Path
) -> None:
    """GS3-11: a hash the server did not record, a reused hash, a hash of an earlier unlock and a run without a
    confirmation are refused; another session sees no plan."""
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.preview()
    _refused(runner.run(plan), 400, "studio.plan_not_confirmed")  # the attempt used the plan up
    plan = runner.preview()
    forged = {**plan, "plan_hash": "sha256:" + "1" * 64}
    _refused(runner.confirm(forged), 400, "studio.plan_unknown")
    _refused(runner.run(forged), 400, "studio.plan_unknown")
    assert runner.confirm(plan).status_code == 200
    # An earlier unlock: lock and unlock again; the draft and its plan ended with the first unlock.
    assert runner.env.send("DELETE", "/api/write/unlocks", {"connection_id": runner.connection_id}).status_code == 204
    assert runner.env.unlock(runner.connection_id).status_code == 200
    _refused(runner.run(plan), 400, "studio.plan_unknown")
    # Another session of the same Studio (a second tab, a new session cookie: last, as it replaces the client's)
    # has no unlock and no draft, so it reaches no plan.
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    other = {**runner.env.studio.sign_in(), "Origin": runner.env.headers["Origin"]}
    body = {"connection_id": runner.connection_id, "plan_hash": plan["plan_hash"], "dry_run": False}
    _refused(runner.env.send("POST", RUN, body, other), 405, "studio.write_locked")
    assert "qty" not in str(_schema(runner.env.db))


def test_an_edit_after_the_preview_drops_the_plan(runner: Runner) -> None:
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    runner.edit({"op": "add_column", "table": "parts", "column": {"name": "note", "type": "TEXT"}})
    _refused(runner.run(plan), 400, "studio.plan_unknown")


def test_an_expired_plan_is_refused(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.schema_designer import execute

    runner.start()
    runner.edit(ADD_QTY)
    monkeypatch.setattr(execute, "RUN_TTL_SECONDS", -1)
    plan = runner.preview()
    _refused(runner.confirm(plan), 400, "studio.plan_expired")
    _refused(runner.confirm(plan), 400, "studio.plan_unknown")


# --- 4. the confirmation per risk class (GS3-14) -----------------------------------------------------------------


def test_each_class_needs_its_own_confirmation(runner: Runner) -> None:
    """``destructive``: the table typed; ``data_changing``: the step named; the write profile's name always. A wrong
    part refuses and names nothing; the plan stays for another try."""
    runner.start()
    runner.edit(DROP_MATERIAL)
    runner.edit({"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER", "default": "0"}})
    plan = runner.preview()
    need = plan["confirmation"]
    assert need["tables"] == ["parts"] and need["profile"] == "parts-write" and need["locking"] is False
    assert len(need["data_changing_steps"]) == 1 and set(plan["risks"]) >= {"destructive", "data_changing"}
    overrides: list[dict[str, Any]] = [
        {"typed": "parts-writ"},
        {"typed": None},
        {"tables": []},
        {"tables": ["Parts"]},
        {"tables": ["parts", "parts"]},
        {"tables": ["parts", "other"]},
        {"steps": []},
        {"steps": [0, 1]},
        {"steps": "1"},
        {"locking": True},
        {"backup": "later"},
    ]
    for override in overrides:
        _refused(runner.confirm(plan, **override), 400, "studio.plan_confirmation_mismatch")
    _refused(runner.run(plan), 400, "studio.plan_not_confirmed")
    plan = runner.preview()
    assert runner.confirm(plan).status_code == 200
    assert runner.run(plan).status_code == 200
    assert [c[0] for c in _sql(runner.env.db, "SELECT name FROM pragma_table_info('parts')")] == ["id", "qty"]


# --- 5. the backup (GS3-15) --------------------------------------------------------------------------------------


def test_a_destructive_run_makes_a_private_backup_first(runner: Runner) -> None:
    runner.start()
    runner.edit(DROP_MATERIAL)
    before = _schema(runner.env.db)
    plan = runner.confirmed()
    response = runner.run(plan)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["backup"] == "made" and answer["result"] == "committed" and answer["steps_run"] == 1
    files = list(runner.backups.iterdir())
    assert len(files) == 1 and answer["backup_path"] == str(files[0])
    assert files[0].name.endswith(".sqlite") and "parts" not in files[0].name
    assert _schema(files[0]) == before
    assert _sql(files[0], "SELECT material FROM parts") == [("Steel",)]
    if POSIX:
        assert stat.S_IMODE(os.lstat(files[0]).st_mode) == 0o600
        assert stat.S_IMODE(os.lstat(runner.backups).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(runner.backups.parent).st_mode) == 0o700
    start = next(r for r in runner.runs() if r["event"] == "run.start")
    assert start["fields"]["backup"] == "made" and str(files[0]) not in json.dumps(runner.records())


def test_a_failed_backup_stops_the_run(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.schema_designer import execute

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(execute, "backup", broken)
    runner.start()
    runner.edit(DROP_MATERIAL)
    before = digest(runner.env.db)
    plan = runner.confirmed()
    _refused(runner.run(plan), 400, "studio.schema_backup_failed")
    assert digest(runner.env.db) == before
    result = next(r for r in runner.runs() if r["event"] == "run.result")
    assert result["fields"]["result"] == "studio.schema_backup_failed"


def test_a_backup_folder_that_is_a_file_stops_the_run(runner: Runner) -> None:
    runner.backups.parent.mkdir(mode=0o700)
    runner.backups.write_text("not a folder")
    runner.start()
    runner.edit(DROP_MATERIAL)
    before = digest(runner.env.db)
    plan = runner.confirmed()
    _refused(runner.run(plan), 400, "studio.schema_backup_failed")
    assert digest(runner.env.db) == before


def test_the_backup_may_be_left_out_on_dev_only_with_the_files_name(runner: Runner) -> None:
    runner.start()
    runner.edit(DROP_MATERIAL)
    plan = runner.preview()
    backup = plan["confirmation"]["backup"]
    assert backup == {"required": True, "engine": "sqlite", "opt_out_allowed": True, "opt_out_text": "parts.db"}
    _refused(runner.confirm(plan, backup="skip"), 400, "studio.plan_confirmation_mismatch")
    _refused(runner.confirm(plan, backup="skip", backup_typed="parts"), 400, "studio.plan_confirmation_mismatch")
    response = runner.confirm(plan, backup="skip", backup_typed="parts.db")
    assert response.status_code == 200 and response.json()["backup"] == "skip"
    answer = runner.run(plan).json()
    assert answer["backup"] == "opted_out" and answer["backup_path"] is None
    assert not runner.backups.exists()
    start = next(r for r in runner.runs() if r["event"] == "run.start")
    assert start["fields"]["backup"] == "opted_out"


def test_the_backup_cannot_be_left_out_on_test(tmp_path: Path, static_dir: Path) -> None:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    test = _env(tmp_path, static_dir, "test")
    try:
        test.start()
        test.edit(DROP_MATERIAL)
        plan = test.preview()
        assert plan["confirmation"]["backup"]["opt_out_allowed"] is False
        _refused(test.confirm(plan, backup="skip", backup_typed="parts.db"), 400, "studio.schema_backup_required")
    finally:
        test.env.close()


def test_an_additive_run_needs_no_backup(runner: Runner) -> None:
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    assert plan["confirmation"]["backup"]["required"] is False
    assert runner.run(plan).json()["backup"] == "not_needed"
    assert not runner.backups.exists()


# --- 6. the run (GS3-13, GS3-17, GS3-27) -------------------------------------------------------------------------


def test_a_rebuild_keeps_rows_triggers_views_and_child_rows(runner: Runner) -> None:
    """GS3-17: a SQLite rebuild through the executor, with foreign keys turned off before ``BEGIN``: the rows, the
    trigger, the view and the child rows of an ``ON DELETE CASCADE`` key stay; ``integrity_check`` passes."""
    _sql(
        runner.env.db,
        "CREATE TABLE kids (id INTEGER PRIMARY KEY, part INTEGER REFERENCES parts(id) ON DELETE CASCADE)",
        "INSERT INTO kids VALUES (10, 1)",
        "CREATE TABLE log (what TEXT)",
        "CREATE TRIGGER parts_log AFTER INSERT ON parts BEGIN INSERT INTO log VALUES ('x'); END",
        "CREATE VIEW part_ids AS SELECT id FROM parts",
    )
    runner.start()
    runner.edit({"op": "alter_column", "table": "parts", "column": "material", "nullable": False})
    plan = runner.preview()
    assert [s["operation"] for s in plan["steps"]] == ["rebuild_table"]
    assert runner.confirm(plan).status_code == 200
    response = runner.run(plan)
    assert response.status_code == 200, response.text
    db = runner.env.db
    assert _sql(db, "SELECT id, material FROM parts") == [(1, "Steel")]
    assert _sql(db, "SELECT id, part FROM kids") == [(10, 1)]
    assert _sql(db, "SELECT name FROM sqlite_master WHERE type IN ('trigger', 'view') ORDER BY 1") == [
        ("part_ids",),
        ("parts_log",),
    ]
    assert _sql(db, "PRAGMA integrity_check") == [("ok",)] and _sql(db, "PRAGMA foreign_key_check") == []
    assert "NOT NULL" in _sql(db, "SELECT sql FROM sqlite_master WHERE name = 'parts'")[0][0]
    # The draft starts again from the database as it is now.
    view = runner.send(f"{DRAFT}/view").json()
    assert view["changed"] is False


def test_the_run_is_audited_with_hash_tables_classes_and_steps(
    runner: Runner, caplog: pytest.LogCaptureFixture
) -> None:
    """GS3-27, GS3-19: ``run.start`` and ``run.result``; the canary default text is in no log, audit record or
    history, and neither is a statement or the plan's SQL."""
    caplog.set_level(logging.DEBUG)
    runner.start()
    default = f"'{CANARY_VALUE}'"
    runner.edit({"op": "add_column", "table": "parts", "column": {"name": "note", "type": "TEXT", "default": default}})
    plan = runner.confirmed()
    assert runner.run(plan).status_code == 200
    start, result = (r for r in reversed(runner.records()) if r["event"].startswith("run."))
    assert start["event"] == "run.start" and result["event"] == "run.result"
    fields = start["fields"]
    assert fields["action"] == "schema_designer.run" and fields["area"] == "schema_designer"
    assert fields["plan_hash"] == plan["plan_hash"].removeprefix("sha256:")
    assert fields["objects"] == ["main.parts"] and fields["steps"] == 1 and fields["dry_run"] is False
    assert sorted(fields["risks"]) == sorted(plan["risks"]) and fields["environment"] == "dev"
    assert result["fields"]["result"] == "committed" and result["fields"]["run"] == fields["run"]
    audit = json.dumps(runner.records())
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for text in (CANARY_VALUE, "ALTER TABLE", "ADD COLUMN"):
        assert text not in audit and text not in logged
    assert plan["plan_hash"] not in logged
    history = runner.env.send("GET", "/api/history")
    assert CANARY_VALUE not in history.text
    assert CANARY_VALUE in _sql(runner.env.db, "SELECT sql FROM sqlite_master WHERE name = 'parts'")[0][0]


def test_an_unwritable_audit_log_stops_the_run(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.audit import AuditError

    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    audit = runner.env.studio.client.app.state.write_unlocks.audit  # type: ignore[attr-defined]

    def refuse(*_args: Any, **_kwargs: Any) -> int:
        raise AuditError()

    monkeypatch.setattr(audit, "append", refuse)
    before = digest(runner.env.db)
    response = runner.run(plan)
    assert response.json()["code"] == "studio.audit_unavailable"
    assert digest(runner.env.db) == before and not runner.backups.exists()


def test_a_failed_check_before_the_commit_rolls_back(runner: Runner) -> None:
    """A row that breaks a foreign key: tabrion's ``foreign_key_check`` before the commit refuses; nothing stays."""
    _sql(
        runner.env.db,
        "CREATE TABLE kids (id INTEGER PRIMARY KEY, part INTEGER REFERENCES parts(id))",
        "PRAGMA foreign_keys = OFF",
        "INSERT INTO kids VALUES (1, 99)",
    )
    runner.start()
    runner.edit({"op": "alter_column", "table": "kids", "column": "part", "nullable": False})
    before = _schema(runner.env.db)
    plan = runner.confirmed()
    _refused(runner.run(plan), 400, "studio.schema_run_check_failed", [("plan.check", "plan.foreign_key_check")])
    assert _schema(runner.env.db) == before


# --- 7. the dry run (GS3-29) -------------------------------------------------------------------------------------


def test_a_dry_run_only_on_request_rolls_back_and_is_audited(runner: Runner) -> None:
    runner.start()
    runner.edit(DROP_MATERIAL)
    before = digest(runner.env.db)
    plan = runner.confirmed()
    _refused(runner.send(RUN, plan_hash=plan["plan_hash"], dry_run="yes"), 400, "studio.plan_invalid")
    response = runner.run(plan, dry_run=True)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["result"] == "rolled_back" and answer["dry_run"] is True and answer["backup"] == "dry_run"
    assert digest(runner.env.db) == before and not runner.backups.exists()
    start = next(r for r in runner.runs() if r["event"] == "run.start")
    assert start["fields"]["action"] == "schema_designer.dry_run" and start["fields"]["dry_run"] is True
    result = next(r for r in runner.runs() if r["event"] == "run.result")
    assert result["fields"]["result"] == "dry_run"
    # The confirmation is used up like a run's.
    _refused(runner.run(plan), 400, "studio.plan_unknown")
    # The draft is unchanged: it can be previewed and run for real.
    plan = runner.confirmed()
    assert runner.run(plan).json()["result"] == "committed"


# --- 8. warnings, errors and the schema authorizer (GS3-16, GS3-20) ----------------------------------------------


def test_the_preview_warnings(runner: Runner) -> None:
    _sql(runner.env.db, "CREATE TABLE notes (code TEXT PRIMARY KEY, body TEXT)")
    runner.start()
    runner.edit({"op": "alter_column", "table": "notes", "column": "body", "nullable": False})
    runner.edit({"op": "alter_column", "table": "parts", "column": "material", "nullable": False})
    plan = runner.preview()
    codes = [(w["code"], w["table"]) for w in plan["warnings"]]
    assert ("not_modelled", None) in codes and ("fingerprint_gaps", None) in codes
    # A rebuild without an INTEGER PRIMARY KEY gives the rows new rowid values; parts keeps its alias.
    assert ("sqlite_rowid", "notes") in codes and ("sqlite_rowid", "parts") not in codes
    assert not [c for c in codes if c[0] in ("pg_dependent_code", "function_drift", "locking")]


def test_a_refusal_names_the_table_by_its_position_only(runner: Runner) -> None:
    """GS3-12, GS3-20: a view names the dropped column or table: tabrion refuses the plan; the problem gives the
    reason and the table's position on the page (the draft's tables, or the dropped ones), never its name."""
    _sql(
        runner.env.db,
        "CREATE TABLE aaa (id INTEGER)",
        f"CREATE VIEW v_{CANARY_VALUE[-8:]} AS SELECT material FROM parts",  # noqa: S608
        "CREATE VIEW w_view AS SELECT id FROM aaa",
    )
    runner.start()
    revision = runner.send(f"{DRAFT}/view").json()["revision"]
    for edit, pointer in (
        (DROP_MATERIAL, "tables.1"),
        ({"op": "drop_table", "table": "aaa"}, "dropped.0"),
    ):
        response = runner.send(f"{DRAFT}/edits", revision=revision, edit=edit)
        _refused(
            response,
            400,
            "studio.schema_plan_refused",
            [("plan.reason", "plan.dependent_object"), (pointer, "plan.dependent_object")],
        )
        assert CANARY_VALUE[-8:] not in response.text and "parts" not in response.text
        assert "aaa" not in response.text


def test_the_schema_authorizer() -> None:
    from tabrion_studio.write.schema_designer.execute import schema_authorizer

    ok, deny = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY
    allowed = [
        (sqlite3.SQLITE_CREATE_TABLE, "t", None, "main"),
        (sqlite3.SQLITE_DROP_TABLE, "t", None, "main"),
        (sqlite3.SQLITE_CREATE_INDEX, "i", "t", "main"),
        (sqlite3.SQLITE_CREATE_TRIGGER, "tr", "t", "main"),
        (sqlite3.SQLITE_ALTER_TABLE, "main", "t", None),
        (sqlite3.SQLITE_UPDATE, "sqlite_master", "sql", "main"),
        (sqlite3.SQLITE_FUNCTION, None, "sqlite_drop_column", None),
        (sqlite3.SQLITE_PRAGMA, "foreign_keys", "OFF", None),
        (sqlite3.SQLITE_PRAGMA, "trusted_schema", "OFF", None),
        (sqlite3.SQLITE_INSERT, "parts", None, "main"),
        (sqlite3.SQLITE_READ, "parts", "id", "main"),
    ]
    denied = [
        (sqlite3.SQLITE_CREATE_TABLE, "t", None, "other"),
        (sqlite3.SQLITE_CREATE_TEMP_TABLE, "t", None, "temp"),
        (sqlite3.SQLITE_CREATE_VIEW, "v", None, "main"),
        (sqlite3.SQLITE_DROP_VIEW, "v", None, "main"),
        (sqlite3.SQLITE_CREATE_VTABLE, "v", "fts5", "main"),
        (sqlite3.SQLITE_ALTER_TABLE, "other", "t", None),
        (sqlite3.SQLITE_ATTACH, "x.db", None, None),
        (sqlite3.SQLITE_DETACH, "x", None, None),
        (sqlite3.SQLITE_UPDATE, "sqlite_master", "sql", "other"),
        (sqlite3.SQLITE_FUNCTION, None, "load_extension", None),
        (sqlite3.SQLITE_PRAGMA, "writable_schema", "ON", None),
        (sqlite3.SQLITE_PRAGMA, "journal_mode", "OFF", None),
        (sqlite3.SQLITE_ANALYZE, "t", None, "main"),
    ]
    for action, arg1, arg2, db in allowed:
        assert schema_authorizer(action, arg1, arg2, db, None) == ok, (action, arg1, arg2, db)
    for action, arg1, arg2, db in denied:
        assert schema_authorizer(action, arg1, arg2, db, None) == deny, (action, arg1, arg2, db)


def test_the_write_authorizer_is_back_after_the_run(runner: Runner) -> None:
    """The schema authorizer is for the run's transaction only: afterwards the unlock's connection refuses DDL."""
    runner.start()
    runner.edit(ADD_QTY)
    plan = runner.confirmed()
    assert runner.run(plan).status_code == 200
    unlocks = runner.env.studio.client.app.state.write_unlocks  # type: ignore[attr-defined]
    connection = unlocks.require(runner.env.session(), runner.connection_id, Area.SCHEMA_DESIGNER)
    with connection.raw_sqlite() as raw, pytest.raises(sqlite3.DatabaseError):
        raw.execute("CREATE TABLE never (id INTEGER)")
    assert ("never",) not in _sql(runner.env.db, "SELECT name FROM sqlite_master")
