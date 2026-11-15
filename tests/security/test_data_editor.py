"""STU-026: the Data Editor on SQLite (03 sections 2 D and 5.2, 08 section 4.6; TX-02, TX-08, TX-09, TX-13, TX-14,
TX-16; review N-3 of PR #109, L-1 and I-1 of tabrion PR #78).

A changeset of one table is a plan of the executor ``data_editor.changeset``: preview, typed confirmation and one run
through the audited transaction; every step is a ``tabrion.rowedit`` statement under ``execute_checked`` with
``expect_rowcount=1``. Here: the kill switch (flag off: the executor and the table route are absent, 405, nothing
imported), the happy path, an optimistic conflict and a row count other than 1 (both roll the whole changeset back),
a NULL old value, refused targets (no primary key, a view), drift between preview and run, PROD, and a canary: no
cell value in any answer, log, audit record, the query history or the drafts. PostgreSQL is in
``test_data_editor_postgres.py``. Gate G-S2 is not granted: every test that writes turns the flag on itself.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any

import pytest

from tabrion_studio.config.schema import Profile
from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.write.audit import verify_log
from tabrion_studio.write.connection import defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, WriteEnv, digest

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses a SQLite write connection"),
]

EXECUTOR = "data_editor.changeset"
TABLE_ROUTE = "/api/write/data-editor/table"
COLUMNS = ["id", "material", "note"]


def _make(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            "DROP TABLE IF EXISTS parts;"
            "CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT COLLATE NOCASE, note TEXT);"
            "INSERT INTO parts VALUES (1, 'Steel', NULL), (2, 'Copper', 'red'), (3, 'Tin', 'soft');"
            "CREATE TABLE log (body TEXT);"
            "CREATE VIEW parts_view AS SELECT * FROM parts;"
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[LogSetup]:
    setup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        yield setup
    finally:
        setup.close()
        forget_secrets()
        assert setup.file is not None
        text = setup.file.read_text(encoding="utf-8")
        assert CANARY_SECRET not in text and CANARY_VALUE not in text


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, logs: LogSetup) -> Iterator[WriteEnv]:
    environment = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    _make(environment.db)
    environment.create_read()
    assert environment.create_write().status_code == 201
    try:
        yield environment
    finally:
        environment.close()
        if environment.audit_dir.exists():
            assert verify_log(environment.audit_dir).ok
            for path in environment.audit_dir.rglob("*"):
                if path.is_file():
                    assert CANARY_VALUE.encode() not in path.read_bytes()


def _unlocked(env: WriteEnv) -> str:
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert response.status_code == 200, response.text
    return connection_id


def _plan(env: WriteEnv, connection_id: str, changes: list[dict[str, Any]], **request: Any) -> Any:
    body = {
        "connection_id": connection_id,
        "executor": EXECUTOR,
        "request": {"schema": "main", "table": "parts", "columns": COLUMNS, "changes": changes, **request},
    }
    return env.send("POST", "/api/write/plans", body)


def _run(env: WriteEnv, connection_id: str, changes: list[dict[str, Any]], **request: Any) -> Any:
    """Preview, confirm with the text the preview asks for, execute: the execute answer."""
    preview = _plan(env, connection_id, changes, **request)
    assert preview.status_code == 201, preview.text
    doc = preview.json()
    confirmed = env.send(
        "POST",
        "/api/write/plans/confirm",
        {"connection_id": connection_id, "plan_hash": doc["plan_hash"], "typed": doc["confirm_text"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return env.send("POST", "/api/write/plans/execute", {"connection_id": connection_id, "plan_hash": doc["plan_hash"]})


def _confirmed(env: WriteEnv, connection_id: str, changes: list[dict[str, Any]]) -> str:
    preview = _plan(env, connection_id, changes)
    assert preview.status_code == 201, preview.text
    doc = preview.json()
    body = {"connection_id": connection_id, "plan_hash": doc["plan_hash"], "typed": doc["confirm_text"]}
    assert env.send("POST", "/api/write/plans/confirm", body).status_code == 200
    plan_hash: str = doc["plan_hash"]
    return plan_hash


def _execute(env: WriteEnv, connection_id: str, plan_hash: str) -> Any:
    return env.send("POST", "/api/write/plans/execute", {"connection_id": connection_id, "plan_hash": plan_hash})


def _rows(path: Path, table: str = "parts") -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608 - a fixed name
    finally:
        connection.close()


def _alter(path: Path, statement: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(statement)
        connection.commit()
    finally:
        connection.close()


def _refused(response: Any, status: int, code: str) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text


def _audit(env: WriteEnv) -> list[dict[str, Any]]:
    files = sorted(env.audit_dir.glob("audit*.jsonl"), key=lambda p: (p.name == "audit.jsonl", p.name))
    return [json.loads(line) for path in files for line in path.read_bytes().splitlines() if line]


def _update(key: int, old: tuple[Any, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "update",
        "key": {"id": key},
        "old": {"id": key, "material": old[0], "note": old[1]},
        "new": new,
    }


def _delete(key: int, old: tuple[Any, Any]) -> dict[str, Any]:
    return {"op": "delete", "key": {"id": key}, "old": {"id": key, "material": old[0], "note": old[1]}}


STEEL = (1, ("Steel", None))
COPPER = (2, ("Copper", "red"))
TIN = (3, ("Tin", "soft"))
ORIGINAL = [(1, "Steel", None), (2, "Copper", "red"), (3, "Tin", "soft")]


# --- the kill switch (TX-02, STU-ADR-0006 proof items) -----------------------------------------------------------


@pytest.mark.parametrize(
    "areas",
    [WriteAreas(), WriteAreas(frozenset({Area.SCHEMA_DESIGNER})), WriteAreas(frozenset({Area.DATABASE_MANAGER}))],
    ids=["every_flag_off", "schema_designer_only", "database_manager_only"],
)
def test_without_the_data_editor_flag_its_routes_are_refused(
    tmp_path: Path, static_dir: Path, areas: WriteAreas
) -> None:
    """Flag off: the table route is 405, and a changeset is refused (405 with every flag off; with another area's
    flag the executor is not registered); the database does not change."""
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        _make(env.db)
        env.create_read()
        before = digest(env.db)
        connection_id = env.open()
        table = env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "schema": "main", "table": "parts"})
        _refused(table, 405, "studio.method_not_allowed")
        planned = _plan(env, connection_id, [_update(*STEEL, {"material": CANARY_VALUE})])
        if areas.enabled:
            _refused(planned, 400, "studio.plan_executor_unknown")
            assert EXECUTOR not in env.studio.client.app.state.write_plans.executors  # type: ignore[attr-defined]
        else:
            _refused(planned, 405, "studio.method_not_allowed")
        assert digest(env.db) == before
    finally:
        env.close()


def _modules(tmp_path: Path, areas: str) -> list[str]:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), {areas})\n"
        "env.create_read()\n"
        "c = env.open()\n"
        f"r = env.send('POST', {TABLE_ROUTE!r}, {{'connection_id': c, 'table': 'parts'}})\n"
        "p = env.send('POST', '/api/write/plans', {'connection_id': c, 'executor': 'data_editor.changeset',\n"
        "             'request': {'table': 'parts', 'columns': ['id'], 'changes': [{'op': 'insert', 'values': {}}]}})\n"
        "print(r.status_code, p.status_code)\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith(('tabrion_studio.write', 'tabrion.rowedit'))))\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=root, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    loaded: list[str] = json.loads(lines[-1].replace("'", '"'))
    return [lines[-2], *loaded]


def test_flag_off_nothing_of_the_data_editor_is_imported(tmp_path: Path) -> None:
    """TX-02: with every flag off neither the write package nor tabrion.rowedit is loaded; with another area's flag
    on the write package is, but not the Data Editor nor tabrion.rowedit."""
    off = _modules(tmp_path / "off", "WriteAreas()")
    assert off == ["405 405"]
    other = _modules(tmp_path / "other", "WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))")
    assert other[0] == "405 400"
    assert "tabrion_studio.write.plan" in other
    assert "tabrion_studio.write.data_editor" not in other and "tabrion.rowedit" not in other


def test_flag_on_the_data_editor_is_loaded(tmp_path: Path) -> None:
    """The counterpart (the tests above are not vacuous): the Data Editor flag loads and registers it."""
    loaded = _modules(tmp_path, "WriteAreas(frozenset({Area.DATA_EDITOR}))")
    # Not unlocked: the table route and the plan answer "locked" (403, the write mode, W-9), not 405.
    assert loaded[0] == "405 405"
    assert "tabrion_studio.write.data_editor" in loaded and "tabrion.rowedit" in loaded


# --- the table route ---------------------------------------------------------------------------------------------


def test_the_table_route_names_columns_and_primary_key(env: WriteEnv) -> None:
    connection_id = env.open()
    locked = env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "table": "parts"})
    _refused(locked, 405, "studio.write_locked")
    connection_id = _unlocked(env)
    answer = env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "schema": "main", "table": "parts"})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "connection_id": connection_id,
        "schema": "main",
        "table": "parts",
        "columns": COLUMNS,
        "primary_key": ["id"],
        "triggers": [],
        "rules": [],
        "row_security": False,
        "max_changes": 1000,
    }
    _refused(
        env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "table": "parts", "x": 1}),
        400,
        "access.bad_request",
    )


@pytest.mark.parametrize(
    ("table", "code"),
    [
        ("log", "studio.plan_target_no_primary_key"),
        ("parts_view", "studio.plan_target_invalid"),
        ("missing", "studio.plan_target_invalid"),
    ],
)
def test_tables_without_a_primary_key_and_views_are_refused(env: WriteEnv, table: str, code: str) -> None:
    """03 section 2 D: only tables with a primary key (never rowid); a view is no table."""
    connection_id = _unlocked(env)
    _refused(env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "table": table}), 400, code)
    before = digest(env.db)
    insert = {"op": "insert", "values": {"body": CANARY_VALUE}} if table == "log" else {"op": "insert", "values": {}}
    _refused(
        _plan(env, connection_id, [insert], table=table, columns=["body"] if table == "log" else COLUMNS), 400, code
    )
    assert digest(env.db) == before


# --- the happy path ----------------------------------------------------------------------------------------------


def test_a_changeset_updates_inserts_and_deletes_in_one_transaction(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    changes = [
        _update(*STEEL, {"material": "Iron", "note": "cast"}),
        {"op": "insert", "values": {"material": "Titan", "note": None}},
        _delete(*TIN),
    ]
    preview = _plan(env, connection_id, changes)
    assert preview.status_code == 201, preview.text
    doc = preview.json()
    assert doc["executor"] == EXECUTOR and doc["destructive"] is True
    assert doc["steps"] == [
        {"action": "row.update", "target": "main.parts", "destructive": False, "rows": 1},
        {"action": "row.insert", "target": "main.parts", "destructive": False, "rows": 1},
        {"action": "row.delete", "target": "main.parts", "destructive": True, "rows": 1},
    ]
    assert doc["confirm_text"] == "parts"  # D-10: a delete is confirmed with the table's name
    assert "Iron" not in preview.text and "Titan" not in preview.text and "Steel" not in preview.text
    assert _rows(env.db) == ORIGINAL  # a preview changes nothing
    done = env.send(
        "POST",
        "/api/write/plans/confirm",
        {"connection_id": connection_id, "plan_hash": doc["plan_hash"], "typed": "parts"},
    )
    assert done.status_code == 200
    ran = _execute(env, connection_id, doc["plan_hash"])
    assert ran.status_code == 200, ran.text
    assert ran.json()["rows"] == {"updated": 1, "inserted": 1, "deleted": 1}
    assert _rows(env.db) == [(1, "Iron", "cast"), (2, "Copper", "red"), (4, "Titan", None)]
    start, result = [r for r in _audit(env) if r["event"].startswith("run.")][-2:]
    assert start["fields"]["action"] == EXECUTOR and start["fields"]["objects"] == ["main.parts"]
    assert start["fields"]["plan_hash"] == doc["plan_hash"]
    assert result["fields"]["result"] == "committed"
    assert result["fields"]["rows"] == {"updated": 1, "inserted": 1, "deleted": 1}


def test_a_changeset_without_a_delete_is_confirmed_with_the_profile_name(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    preview = _plan(env, connection_id, [_update(*COPPER, {"note": "blue"})]).json()
    assert preview["destructive"] is False and preview["confirm_text"] == "parts-write"
    assert _run(env, connection_id, [_update(*COPPER, {"note": "blue"})]).status_code == 200
    assert _rows(env.db)[1] == (2, "Copper", "blue")


def test_a_null_old_value_matches_null_and_a_null_new_value_is_null(env: WriteEnv) -> None:
    """NULL is not the empty text: ``null`` old matches only NULL (rowedit's NULL-safe predicate); ``null`` new
    writes NULL."""
    connection_id = _unlocked(env)
    assert _run(env, connection_id, [_update(*STEEL, {"note": "x"})]).status_code == 200
    assert _rows(env.db)[0] == (1, "Steel", "x")
    assert _run(env, connection_id, [_update(1, ("Steel", "x"), {"note": None})]).status_code == 200
    assert _rows(env.db)[0] == (1, "Steel", None)
    # "" is not NULL: the row's NULL does not match an empty old value.
    _refused(_run(env, connection_id, [_update(1, ("Steel", ""), {"note": "y"})]), 400, "studio.data_editor_conflict")
    assert _rows(env.db)[0] == (1, "Steel", None)


# --- conflicts and row counts: the whole changeset rolls back (03 section 5.2) --------------------------------


def test_an_optimistic_conflict_rolls_the_whole_changeset_back(env: WriteEnv) -> None:
    """TX-09: another client changed a shown value after the rows were loaded; the earlier steps of the changeset
    are rolled back too, and the answer names no value."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(
        env,
        connection_id,
        [
            {"op": "insert", "values": {"material": CANARY_VALUE}},
            _update(*COPPER, {"material": CANARY_VALUE}),
            _update(*STEEL, {"material": "Iron"}),
        ],
    )
    _alter(env.db, "UPDATE parts SET note = 'changed' WHERE id = 1")  # a shown column the change does not set
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.data_editor_conflict")
    assert _rows(env.db) == [(1, "Steel", "changed"), (2, "Copper", "red"), (3, "Tin", "soft")]
    result = [r for r in _audit(env) if r["event"] == "run.result"][-1]
    assert result["fields"]["result"] == "studio.data_editor_conflict"


def test_a_case_only_change_is_a_conflict_despite_a_nocase_column(env: WriteEnv) -> None:
    """tabrion compares text under BINARY: a concurrent change of only the case is seen (I-121 c)."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id, [_update(*STEEL, {"note": "n"})])
    _alter(env.db, "UPDATE parts SET material = 'STEEL' WHERE id = 1")
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.data_editor_conflict")
    assert _rows(env.db)[0] == (1, "STEEL", None)


def test_a_deleted_row_is_a_conflict(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id, [_delete(*TIN), _update(*STEEL, {"note": "n"})])
    _alter(env.db, "DELETE FROM parts WHERE id = 3")
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.data_editor_conflict")
    assert _rows(env.db) == ORIGINAL[:2]


def test_a_row_count_other_than_one_rolls_back(env: WriteEnv) -> None:
    """A BEFORE INSERT trigger that ignores the row makes the insert change 0 rows: the count is not 1, so the
    whole changeset is rolled back (the trigger is in the preview, TX-16)."""
    _alter(
        env.db,
        "CREATE TRIGGER parts_skip BEFORE INSERT ON parts WHEN NEW.material = 'skip' BEGIN SELECT RAISE(IGNORE); END",
    )
    connection_id = _unlocked(env)
    preview = _plan(env, connection_id, [_update(*STEEL, {"material": "Iron"})])
    assert preview.json()["targets"][0]["triggers"] == [
        {"name": "parts_skip", "timing": None, "events": [], "enabled": True}
    ]
    response = _run(
        env,
        connection_id,
        [_update(*STEEL, {"material": "Iron"}), {"op": "insert", "values": {"material": "skip"}}],
    )
    _refused(response, 400, "studio.data_editor_rowcount")
    assert _rows(env.db) == ORIGINAL


def test_a_constraint_error_rolls_back_without_values(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    response = _run(
        env,
        connection_id,
        [_update(*STEEL, {"material": "Iron"}), {"op": "insert", "values": {"id": 2, "material": CANARY_VALUE}}],
    )
    assert response.status_code in (400, 409), response.text
    assert response.json()["code"].startswith("db.")
    assert CANARY_VALUE not in response.text
    assert _rows(env.db) == ORIGINAL


# --- drift, binding and PROD -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE parts ADD COLUMN weight REAL",
        "CREATE TRIGGER parts_late AFTER UPDATE ON parts BEGIN INSERT INTO log VALUES ('x'); END",
        "DROP TABLE parts; CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT, note TEXT);"
        "INSERT INTO parts VALUES (1, 'Steel', NULL), (2, 'Copper', 'red'), (3, 'Tin', 'soft');",
    ],
)
def test_drift_between_preview_and_run_aborts(env: WriteEnv, change: str) -> None:
    """TX-09: a schema or trigger change after the preview stops the run before the first step."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id, [_update(*STEEL, {"material": "Iron"})])
    _alter(env.db, change)
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_drift")
    assert _rows(env.db)[0][:2] == (1, "Steel")


def test_prod_is_refused_from_the_stored_profile(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-X7, W-9: a profile that is PROD by the time of the run (read from the store on the server, never from the
    request) refuses the table route, the preview and the run; nothing changes."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id, [_update(*STEEL, {"material": "Iron"})])
    real = env.unlocks._stored

    def prod_now(name: str) -> Any:
        write, read = real(name)
        return write, Profile(read.name, read.engine, "prod", path=read.path)

    monkeypatch.setattr(env.unlocks, "_stored", prod_now)
    _refused(_execute(env, connection_id, plan_hash), 405, "studio.write_prod_locked")
    for response in (
        env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "table": "parts"}),
        _plan(env, connection_id, [_update(*STEEL, {"material": "Iron"})]),
    ):
        assert response.status_code in (403, 405) and response.json()["code"] in (
            "studio.write_prod_locked",
            "studio.write_locked",
        )
    assert _rows(env.db) == ORIGINAL


def test_a_prod_profile_cannot_be_coupled_or_unlocked(env: WriteEnv) -> None:
    prod_db = env.tmp_path / "prod.db"
    _make(prod_db)
    env.create_read(name="prod", environment="prod", path=prod_db)
    created = env.create_write(name="prod-write", read="prod")
    assert created.status_code == 400 and created.status_code != 403
    connection_id = env.open("user:prod", "prod")
    _refused(
        env.send("POST", TABLE_ROUTE, {"connection_id": connection_id, "table": "parts"}), 405, "studio.write_locked"
    )
    _refused(_plan(env, connection_id, [_update(*STEEL, {"material": "Iron"})]), 405, "studio.write_locked")
    assert _rows(prod_db) == ORIGINAL


# --- requests the executor refuses -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        # Not every shown column as old (review L-1 of tabrion #78).
        {"op": "update", "key": {"id": 1}, "old": {"id": 1, "material": "Steel"}, "new": {"material": "x"}},
        # A key that is not the primary key, or whose value is not the old one.
        {
            "op": "update",
            "key": {"material": "Steel"},
            "old": {"id": 1, "material": "Steel", "note": None},
            "new": {"note": "x"},
        },
        {"op": "update", "key": {"id": 2}, "old": {"id": 1, "material": "Steel", "note": None}, "new": {"note": "x"}},
        {
            "op": "update",
            "key": {"id": True},
            "old": {"id": 1, "material": "Steel", "note": None},
            "new": {"note": "x"},
        },
        # Nothing to set, a column that is not shown, an unknown operation or key, a value of another kind.
        {"op": "update", "key": {"id": 1}, "old": {"id": 1, "material": "Steel", "note": None}, "new": {}},
        {"op": "update", "key": {"id": 1}, "old": {"id": 1, "material": "Steel", "note": None}, "new": {"x": 1}},
        {"op": "upsert", "values": {"material": "x"}},
        {"op": "insert", "values": {"material": "x"}, "where": "1=1"},
        {"op": "insert", "values": {"material": {"nested": 1}}},
        {"op": "insert", "values": {"material": [1]}},
        {"op": "delete", "key": {"id": None}, "old": {"id": None, "material": "Steel", "note": None}},
    ],
)
def test_a_change_the_executor_does_not_take_is_refused(env: WriteEnv, change: dict[str, Any]) -> None:
    connection_id = _unlocked(env)
    _refused(_plan(env, connection_id, [change]), 400, "studio.plan_invalid")
    assert _rows(env.db) == ORIGINAL


@pytest.mark.parametrize(
    "request_",
    [
        {"columns": ["id", "material", "note", "ghost"]},  # a column the table does not have
        {"columns": ["material", "note"]},  # the primary key is not shown
        {"columns": ["id", "id", "note"]},
        {"columns": []},
        {"schema": "other"},
        {"sql": "DELETE FROM parts"},
    ],
)
def test_a_request_the_executor_does_not_take_is_refused(env: WriteEnv, request_: dict[str, Any]) -> None:
    connection_id = _unlocked(env)
    columns = request_.get("columns", COLUMNS)
    change = {"op": "update", "key": {"id": 1}, "old": dict.fromkeys(columns, None), "new": {"note": "x"}}
    _refused(_plan(env, connection_id, [change], **request_), 400, "studio.plan_invalid")
    assert _rows(env.db) == ORIGINAL


def test_one_change_per_row(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    changes = [_update(*STEEL, {"note": "a"}), _delete(*STEEL)]
    _refused(_plan(env, connection_id, changes), 400, "studio.plan_invalid")


# --- canary: no value outside the database (TX-13, TX-14, N-3) ------------------------------------------------


def test_no_cell_value_reaches_answers_logs_audit_history_or_drafts(env: WriteEnv) -> None:
    """A marked value written, compared as old, and in a refused and a failed changeset: it is in the database only.
    The query history and the drafts (review N-3) work during the test (a read query is recorded), and hold no value
    of a changeset."""
    connection_id = _unlocked(env)
    answers: list[str] = []
    ran = _run(env, connection_id, [_update(*STEEL, {"material": CANARY_VALUE})])
    answers.append(ran.text)
    assert ran.status_code == 200
    assert _rows(env.db)[0] == (1, CANARY_VALUE, None)
    refused = _run(env, connection_id, [_update(1, (CANARY_VALUE, "wrong"), {"note": CANARY_VALUE})])
    answers.append(refused.text)
    assert refused.json()["code"] == "studio.data_editor_conflict"
    failed = _run(env, connection_id, [{"op": "insert", "values": {"id": 2, "material": CANARY_VALUE}}])
    answers.append(failed.text)
    invalid = _plan(env, connection_id, [{"op": "insert", "values": {"ghost": CANARY_VALUE}}])
    answers.append(invalid.text)
    for text in answers:
        assert CANARY_VALUE not in text
    # The history works: a read query is recorded; no changeset is.
    query = env.send("POST", "/api/queries", {"connection_id": connection_id, "sql": "SELECT 'history-probe'"})
    assert query.status_code in (200, 201, 202), query.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        entries = env.history.entries()
        if any("history-probe" in json.dumps(entry) for entry in entries.get("entries", [])):
            break
        time.sleep(0.05)
    else:
        pytest.fail("the read query was not recorded in the history")
    assert CANARY_VALUE not in json.dumps(env.history.entries())
    assert CANARY_VALUE not in json.dumps(env.send("GET", "/api/drafts").json())
    for path in env.state.rglob("*"):
        if path.is_file():
            assert CANARY_VALUE.encode() not in path.read_bytes(), path.name
