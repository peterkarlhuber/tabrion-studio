"""STU-XW-3: the plan, preview and confirmation framework (D-09..D-11; TX-06, TX-08, TX-09, TX-14, TX-16; N-2).

A write runs only as a server-side plan: preview (plan hash, fingerprint, targets with their triggers and rules, risk
notes), typed confirmation with the hash, one execution through the audited transaction with a drift check inside it.
Each refusal of the threat model has its test here: a swapped, an edited and a replayed plan, another session, another
connection, an earlier unlock, an expired plan, drift (a schema change between preview and run) and a file swapped
between preview and run. The kill-switch proof covers the new routes (flag off: 405, nothing changed, nothing
imported), and a canary shows that no value reaches an answer, a log or the audit log.

Every test but the flag-off ones runs with the Data Editor flag on ("flag on" path); the executor is the test's own
(:mod:`tests.support.plans`), since Studio registers none until STU-026.
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.write.audit import verify_log
from tabrion_studio.write.connection import defensive_available
from tabrion_studio.write.plan import MAX_TARGETS, PLAN_TTL_SECONDS, PlanContent, Plans, Step, Target
from tests.security.test_write_unlock import _release
from tests.support.access import ORIGIN
from tests.support.plans import RowExecutor
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, WriteEnv, digest, make_database

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses a SQLite write connection"),
]

PLAN_ROUTES = (
    ("POST", "/api/write/plans"),
    ("DELETE", "/api/write/plans"),
    ("POST", "/api/write/plans/confirm"),
    ("POST", "/api/write/plans/execute"),
)


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
    environment.create_read()
    assert environment.create_write().status_code == 201
    clock = [1000.0]
    environment.unlocks.clock = lambda: clock[0]
    environment.clock = clock  # type: ignore[attr-defined]
    environment.executor = RowExecutor()  # type: ignore[attr-defined]
    plans(environment).register(environment.executor)  # type: ignore[attr-defined]
    try:
        yield environment
    finally:
        environment.close()
        # The audit log stays a valid chain whatever the test refused.
        if environment.audit_dir.exists():
            assert verify_log(environment.audit_dir).ok


def plans(env: WriteEnv) -> Plans:
    store: Plans = env.studio.client.app.state.write_plans  # type: ignore[attr-defined]
    return store


def _unlocked(env: WriteEnv) -> str:
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert response.status_code == 200, response.text
    return connection_id


def _plan(env: WriteEnv, connection_id: str, material: str = "Iron", **request: Any) -> Any:
    body = {
        "connection_id": connection_id,
        "executor": "test.rows",
        "request": {"rows": [{"id": 1, "material": material}], **request},
    }
    return env.send("POST", "/api/write/plans", body)


def _preview(env: WriteEnv, connection_id: str, material: str = "Iron", **request: Any) -> dict[str, Any]:
    response = _plan(env, connection_id, material, **request)
    assert response.status_code == 201, response.text
    preview: dict[str, Any] = response.json()
    return preview


def _confirm(env: WriteEnv, connection_id: str, plan_hash: str, typed: str = "parts-write", **kw: Any) -> Any:
    body = {"connection_id": connection_id, "plan_hash": plan_hash, "typed": typed}
    return env.send("POST", "/api/write/plans/confirm", body, **kw)


def _execute(env: WriteEnv, connection_id: str, plan_hash: str, **kw: Any) -> Any:
    return env.send("POST", "/api/write/plans/execute", {"connection_id": connection_id, "plan_hash": plan_hash}, **kw)


def _confirmed(env: WriteEnv, connection_id: str, material: str = "Iron") -> str:
    preview = _preview(env, connection_id, material)
    assert _confirm(env, connection_id, preview["plan_hash"]).status_code == 200
    plan_hash: str = preview["plan_hash"]
    return plan_hash


def _material(path: Path) -> Any:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT material FROM parts WHERE id = 1").fetchone()[0]
    finally:
        connection.close()


def _alter(path: Path, statement: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def _records(env: WriteEnv) -> list[dict[str, Any]]:
    files = sorted(env.audit_dir.glob("audit*.jsonl"), key=lambda p: (p.name == "audit.jsonl", p.name))
    return [json.loads(line) for path in files for line in path.read_bytes().splitlines() if line]


def _runs(env: WriteEnv) -> list[dict[str, Any]]:
    return [record for record in _records(env) if record["event"] in ("run.start", "run.result")]


def _refused(response: Any, status: int, code: str) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text


# --- the flow --------------------------------------------------------------------------------------------------------


def test_preview_confirm_execute_runs_the_plan_once_and_records_its_hash(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    before = digest(env.db)
    preview = _preview(env, connection_id)
    assert set(preview) == {
        "plan_hash",
        "executor",
        "area",
        "connection_id",
        "write_profile_id",
        "engine",
        "environment",
        "targets",
        "steps",
        "destructive",
        "risks",
        "confirm_text",
        "confirmed",
        "expires_in_s",
    }
    assert len(preview["plan_hash"]) == 64
    assert preview["targets"] == [
        {"schema": "main", "table": "parts", "triggers": [], "rules": [], "row_security": False}
    ]
    assert preview["steps"] == [{"action": "update_row", "target": "main.parts", "destructive": False, "rows": 1}]
    assert preview["risks"] == ["test.note"] and preview["destructive"] is False
    assert preview["confirm_text"] == "parts-write"  # D-09: the write profile's name
    assert preview["expires_in_s"] == PLAN_TTL_SECONDS and preview["environment"] == "dev"
    assert "Iron" not in json.dumps(preview)  # the step's data is never shown
    assert digest(env.db) == before  # a preview changes nothing

    confirmed = _confirm(env, connection_id, preview["plan_hash"])
    assert confirmed.status_code == 200 and confirmed.json()["confirmed"] is True
    assert digest(env.db) == before

    done = _execute(env, connection_id, preview["plan_hash"])
    assert done.status_code == 200, done.text
    assert done.json() == {
        "plan_hash": preview["plan_hash"],
        "result": "committed",
        "rows": {"updated": 1},
        "audit_pending": False,
    }
    assert _material(env.db) == "Iron"
    start, result = _runs(env)[-2:]
    assert start["event"] == "run.start" and start["fields"]["plan_hash"] == preview["plan_hash"]
    assert start["fields"]["action"] == "test.rows" and start["fields"]["objects"] == ["main.parts"]
    assert result["fields"]["result"] == "committed" and result["fields"]["rows"] == {"updated": 1}
    assert result["fields"]["run"] == start["fields"]["run"]


def test_a_destructive_plan_is_confirmed_with_the_target_name(env: WriteEnv) -> None:
    """D-10: a destructive step needs the typed object name, and the preview says it is destructive."""
    connection_id = _unlocked(env)
    preview = _preview(env, connection_id, delete=True)
    assert preview["destructive"] is True and preview["risks"][0] == "plan.destructive"
    assert preview["confirm_text"] == "parts"
    _refused(
        _confirm(env, connection_id, preview["plan_hash"], "parts-write"), 400, "studio.plan_confirmation_mismatch"
    )
    assert _confirm(env, connection_id, preview["plan_hash"], "parts").status_code == 200
    assert _execute(env, connection_id, preview["plan_hash"]).json()["rows"] == {"deleted": 1}


@pytest.mark.parametrize("typed", ["", "Parts-write", "parts-write ", None, 1, ["parts-write"]])
def test_a_wrong_typed_confirmation_does_not_confirm(env: WriteEnv, typed: Any) -> None:
    connection_id = _unlocked(env)
    preview = _preview(env, connection_id)
    _refused(_confirm(env, connection_id, preview["plan_hash"], typed), 400, "studio.plan_confirmation_mismatch")
    _refused(_execute(env, connection_id, preview["plan_hash"]), 400, "studio.plan_not_confirmed")
    assert _material(env.db) == "Steel"


def test_an_unconfirmed_plan_never_runs_and_is_gone(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    preview = _preview(env, connection_id)
    _refused(_execute(env, connection_id, preview["plan_hash"]), 400, "studio.plan_not_confirmed")
    # One attempt: the plan is gone, a confirmation now finds nothing.
    _refused(_confirm(env, connection_id, preview["plan_hash"]), 400, "studio.plan_unknown")
    assert _material(env.db) == "Steel" and _runs(env) == []


def test_preview_names_the_triggers_on_the_target(tmp_path: Path, static_dir: Path, logs: LogSetup) -> None:
    """TX-16, review I-1 of tabrion PR #78: the row count covers only the statement; the preview names triggers."""
    environment = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        _alter(environment.db, "CREATE TABLE log (n INTEGER)")
        _alter(environment.db, "CREATE TRIGGER parts_audit AFTER UPDATE ON parts BEGIN INSERT INTO log VALUES (1); END")
        environment.create_read()
        assert environment.create_write().status_code == 201
        plans(environment).register(RowExecutor())
        connection_id = _unlocked(environment)
        preview = _preview(environment, connection_id)
        assert preview["targets"][0]["triggers"] == [
            {"name": "parts_audit", "timing": None, "events": [], "enabled": True}
        ]
        assert "plan.triggers" in preview["risks"]
    finally:
        environment.close()


# --- refusals of the threat model (TX-08, N-2) -----------------------------------------------------------------------


def test_a_swapped_plan_never_runs(env: WriteEnv) -> None:
    """TX-08: the hash of another plan runs that plan only if it was confirmed itself; a plan replaced in the store
    under a confirmed hash never runs."""
    connection_id = _unlocked(env)
    confirmed = _confirmed(env, connection_id, "Iron")
    other = _preview(env, connection_id, "Gold")
    _refused(_execute(env, connection_id, other["plan_hash"]), 400, "studio.plan_not_confirmed")
    assert _material(env.db) == "Steel"

    # A plan swapped in the store under the confirmed hash (its content is another plan's).
    store = plans(env)
    swapped = _preview(env, connection_id, "Gold")
    with store._lock:
        original = store._plans[confirmed]
        store._plans[confirmed] = dataclasses.replace(
            store._plans[swapped["plan_hash"]], plan_hash=confirmed, confirmed=True
        )
    _refused(_execute(env, connection_id, confirmed), 400, "studio.plan_changed")
    assert _material(env.db) == "Steel" and _runs(env) == []
    assert original.plan_hash == confirmed


def test_an_edited_plan_never_runs(env: WriteEnv) -> None:
    """TX-08: a plan whose stored steps changed after the preview, a hash with one character changed and a body that
    carries steps of its own are all refused; nothing runs."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id, "Iron")
    store = plans(env)
    with store._lock:
        plan = store._plans[plan_hash]
        step = dataclasses.replace(plan.content.steps[0], data={"id": 1, "material": "Gold"})
        store._plans[plan_hash] = dataclasses.replace(plan, content=dataclasses.replace(plan.content, steps=(step,)))
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_changed")

    plan_hash = _confirmed(env, connection_id, "Iron")
    flipped = ("0" if plan_hash[0] != "0" else "1") + plan_hash[1:]
    _refused(_execute(env, connection_id, flipped), 400, "studio.plan_unknown")
    for body in (
        {"connection_id": connection_id, "plan_hash": plan_hash, "steps": [{"material": "Gold"}]},
        {"connection_id": connection_id, "plan_hash": plan_hash, "request": {"rows": []}},
        {"connection_id": connection_id, "plan_hash": plan_hash, "fingerprint": "0" * 64},
    ):
        _refused(env.send("POST", "/api/write/plans/execute", body), 400, "access.bad_request")
    for bad in (None, 1, "", "x" * 64, plan_hash.upper(), [plan_hash]):
        _refused(_execute(env, connection_id, bad), 400, "studio.plan_unknown")  # type: ignore[arg-type]
    assert _material(env.db) == "Steel" and _runs(env) == []
    # The genuine plan is still there and runs as previewed.
    assert _execute(env, connection_id, plan_hash).status_code == 200
    assert _material(env.db) == "Iron"


def test_a_plan_runs_exactly_once(env: WriteEnv) -> None:
    """N-2: a replayed hash finds nothing, and so does a confirmation after the run."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id, "Iron")
    assert _execute(env, connection_id, plan_hash).status_code == 200
    _alter(env.db, "UPDATE parts SET material = 'Steel' WHERE id = 1")
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_unknown")
    _refused(_confirm(env, connection_id, plan_hash), 400, "studio.plan_unknown")
    assert _material(env.db) == "Steel"
    assert [record["event"] for record in _runs(env)] == ["run.start", "run.result"]


def test_a_plan_of_another_session_is_unknown(env: WriteEnv) -> None:
    """N-2: another tab (a new session cookie) cannot confirm or run this session's plan."""
    connection_id = _unlocked(env)
    preview = _preview(env, connection_id)
    other = {**env.studio.sign_in(), "Origin": ORIGIN}
    _refused(_confirm(env, connection_id, preview["plan_hash"], headers=other), 400, "studio.plan_unknown")
    _refused(_execute(env, connection_id, preview["plan_hash"], headers=other), 400, "studio.plan_unknown")
    discarded = env.send("DELETE", "/api/write/plans", {"plan_hash": preview["plan_hash"]}, headers=other)
    assert discarded.status_code == 204
    assert plans(env).open_count(env.session()) == 0  # the new session has none
    assert preview["plan_hash"] in plans(env)._plans  # the owner's plan is untouched
    assert _material(env.db) == "Steel" and _runs(env) == []


def test_a_plan_of_another_connection_never_runs(env: WriteEnv) -> None:
    """N-2: the plan is bound to its read connection; another connection's ID is refused and the plan discarded,
    and a reconnect (a new connection ID, a new unlock) leaves no way to run it."""
    connection_id = _unlocked(env)
    other_db = env.tmp_path / "other.db"
    make_database(other_db)
    env.create_read(name="other", path=other_db)
    env.create_write(name="other-write", read="other")
    other = env.open("user:other")
    assert env.unlock(other, "write:other-write", "other-write").status_code == 200

    plan_hash = _confirmed(env, connection_id)
    _refused(_execute(env, other, plan_hash), 400, "studio.plan_binding")
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_unknown")  # discarded

    preview = _preview(env, connection_id)
    _refused(_confirm(env, other, preview["plan_hash"]), 400, "studio.plan_binding")
    _refused(_confirm(env, connection_id, preview["plan_hash"]), 400, "studio.plan_unknown")

    plan_hash = _confirmed(env, connection_id)
    assert env.send("DELETE", "/api/connections", {"connection_id": connection_id}).status_code in (200, 204)
    reopened = _unlocked(env)
    _refused(_execute(env, reopened, plan_hash), 400, "studio.plan_binding")
    assert _material(env.db) == "Steel" and _material(other_db) == "Steel" and _runs(env) == []


def test_a_plan_of_an_earlier_unlock_never_runs(env: WriteEnv) -> None:
    """N-2: locked and unlocked again (same session, same connection): the earlier unlock's plan is refused."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id)
    preview = _preview(env, connection_id)
    assert env.send("DELETE", "/api/write/unlocks", {"connection_id": connection_id}).status_code == 204
    assert env.unlock(connection_id).status_code == 200
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_binding")
    _refused(_confirm(env, connection_id, preview["plan_hash"]), 400, "studio.plan_binding")
    assert _material(env.db) == "Steel" and _runs(env) == []


def test_without_an_unlock_no_plan_is_made_or_run(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id)
    assert env.send("DELETE", "/api/write/unlocks", {"connection_id": connection_id}).status_code == 204
    _refused(_plan(env, connection_id), 405, "studio.write_locked")
    _refused(_execute(env, connection_id, plan_hash), 405, "studio.write_locked")
    assert _material(env.db) == "Steel" and _runs(env) == []


def test_an_expired_plan_never_runs(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id)
    preview = _preview(env, connection_id)
    env.clock[0] += PLAN_TTL_SECONDS + 1  # type: ignore[attr-defined]
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_expired")
    _refused(_confirm(env, connection_id, preview["plan_hash"]), 400, "studio.plan_expired")
    assert _material(env.db) == "Steel" and _runs(env) == []


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE parts ADD COLUMN weight REAL",
        "CREATE TRIGGER parts_later AFTER UPDATE ON parts BEGIN SELECT 1; END",
        "CREATE INDEX parts_material ON parts (material)",
        "CREATE TABLE unrelated (n INTEGER)",
    ],
)
def test_drift_between_preview_and_run_aborts_the_run(env: WriteEnv, change: str) -> None:
    """TX-09: the schema changed after the preview; the check inside the transaction stops the run, nothing changes,
    and the run's result record names the refusal."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id)
    _alter(env.db, change)
    before = digest(env.db)
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_drift")
    assert digest(env.db) == before and _material(env.db) == "Steel"
    start, result = _runs(env)[-2:]
    assert start["fields"]["plan_hash"] == plan_hash
    assert result["fields"]["result"] == "studio.plan_drift" and result["fields"]["rows"] == {}
    assert env.executor.ran == []  # type: ignore[attr-defined]


def test_a_dropped_target_is_drift(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id)
    _alter(env.db, "ALTER TABLE parts RENAME TO parts_old")
    _refused(_execute(env, connection_id, plan_hash), 400, "studio.plan_drift")


def test_a_file_swapped_between_preview_and_run_never_runs(env: WriteEnv) -> None:
    """TX-16, K-2: the file is swapped for one with the same schema (so only its identity differs) after the
    preview: the run is refused before anything begins, and neither file changes."""
    connection_id = _unlocked(env)
    plan_hash = _confirmed(env, connection_id)
    write = env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    replacement = env.tmp_path / "replacement.db"
    make_database(replacement)
    _release(env, connection_id, write)
    original = env.tmp_path / "original.db"
    os.replace(env.db, original)
    os.replace(replacement, env.db)
    before = digest(env.db)
    response = _execute(env, connection_id, plan_hash)
    _refused(response, 405, "studio.write_target_changed")
    assert digest(env.db) == before and _material(env.db) == "Steel" and _material(original) == "Steel"
    assert _runs(env) == []


def test_the_fingerprint_covers_the_file_identity(env: WriteEnv) -> None:
    """The drift check itself sees a swapped file of the same schema, also if the unlock's own check were missed."""
    from tabrion_studio.write import plan as module

    connection_id = _unlocked(env)
    write = env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    targets = (Target("main", "parts"),)
    first, _ = module.read_catalog(write.fetch, "sqlite", targets, write.database_file)
    assert module.read_catalog(write.fetch, "sqlite", targets, write.database_file)[0] == first
    replacement = env.tmp_path / "replacement.db"
    make_database(replacement)
    _release(env, connection_id, write)
    os.replace(replacement, env.db)
    second, _ = module.read_catalog(write.fetch, "sqlite", targets, write.database_file)
    assert second != first


# --- what a plan may be ---------------------------------------------------------------------------------------------


class _Fixed:
    """An executor that returns a fixed plan content (to check the framework's own validation)."""

    def __init__(self, content: object, name: str = "test.fixed", area: Area = Area.DATA_EDITOR) -> None:
        self.name = name
        self.area = area
        self.content = content

    def build(self, request: Any, engine: str) -> Any:
        return self.content

    def run_step(self, *args: Any) -> None:  # pragma: no cover - never reached
        raise AssertionError("must not run")


@pytest.mark.parametrize(
    "content",
    [
        None,
        PlanContent((), (Step("update_row", 0),)),
        PlanContent((Target("main", "parts"),), ()),
        PlanContent((Target("temp", "parts"),), (Step("update_row", 0),)),  # SQLite: main only
        PlanContent((Target("main", ""),), (Step("update_row", 0),)),
        PlanContent((Target("main", "par\x00ts"),), (Step("update_row", 0),)),
        PlanContent((Target("main", "x" * 200),), (Step("update_row", 0),)),
        PlanContent((Target("main", "parts"), Target("main", "PARTS")), (Step("update_row", 0),)),
        PlanContent(tuple(Target("main", f"t{i}") for i in range(MAX_TARGETS + 1)), (Step("update_row", 0),)),
        PlanContent((Target("main", "parts"),), (Step("update_row", 1),)),
        PlanContent((Target("main", "parts"),), (Step("Update Row", 0),)),
        PlanContent((Target("main", "parts"),), (Step("update_row", 0, rows=-1),)),
        PlanContent((Target("main", "parts"),), (Step("update_row", 0, data={"x": float("nan")}),)),
        PlanContent((Target("main", "parts"),), (Step("update_row", 0, data={"x": object()}),)),
        PlanContent((Target("main", "parts"),), (Step("update_row", 0),), risks=("Not a token",)),
    ],
)
def test_a_plan_content_that_is_not_valid_is_refused(env: WriteEnv, content: object) -> None:
    plans(env).register(_Fixed(content))
    connection_id = _unlocked(env)
    response = env.send(
        "POST", "/api/write/plans", {"connection_id": connection_id, "executor": "test.fixed", "request": {}}
    )
    _refused(response, 400, "studio.plan_invalid")
    assert plans(env).open_count(env.session()) == 0


def test_a_target_that_is_not_a_table_is_refused(env: WriteEnv) -> None:
    _alter(env.db, "CREATE VIEW parts_view AS SELECT * FROM parts")
    connection_id = _unlocked(env)
    for table in ("missing", "parts_view"):
        _refused(_plan(env, connection_id, table=table), 400, "studio.plan_target_invalid")


@pytest.mark.parametrize(
    "body",
    [
        {"executor": "test.unknown", "request": {}},
        {"executor": None, "request": {}},
        {"executor": ["test.rows"], "request": {}},
    ],
)
def test_an_unknown_executor_is_refused(env: WriteEnv, body: dict[str, Any]) -> None:
    connection_id = _unlocked(env)
    _refused(
        env.send("POST", "/api/write/plans", {"connection_id": connection_id, **body}),
        400,
        "studio.plan_executor_unknown",
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"connection_id": "C", "executor": "test.rows", "request": {"rows": [{"id": 1, "material": NaN}]}}',
        b'{"connection_id": "C", "executor": "test.rows", "request": [1]}',
        b'{"connection_id": "C", "executor": "test.rows", "request": {"rows": "x"}}',
        b'{"connection_id": "C", "executor": "test.rows", "request": {"rows": []}}',
    ],
)
def test_a_request_that_is_not_valid_is_refused(env: WriteEnv, raw: bytes) -> None:
    connection_id = _unlocked(env)
    response = env.studio.client.post(
        "/api/write/plans",
        content=raw.replace(b'"C"', json.dumps(connection_id).encode()),
        headers={**env.headers, "Content-Type": "application/json"},
    )
    _refused(response, 400, "studio.plan_invalid")


def test_an_executor_of_an_area_that_is_off_is_refused(env: WriteEnv) -> None:
    """TX-01, TX-15: the plan needs its executor's own area; the Data Editor's flag does not open the Database
    Manager's plans."""
    plans(env).register(RowExecutor("test.admin", Area.DATABASE_MANAGER))
    connection_id = _unlocked(env)
    response = env.send(
        "POST",
        "/api/write/plans",
        {"connection_id": connection_id, "executor": "test.admin", "request": {"rows": [{"id": 1}]}},
    )
    _refused(response, 405, "studio.method_not_allowed")


def test_executor_names_are_tokens_and_registered_once(env: WriteEnv) -> None:
    store = plans(env)
    with pytest.raises(ValueError):
        store.register(RowExecutor("test.rows"))
    with pytest.raises(ValueError):
        store.register(RowExecutor("Not a token"))
    assert set(Plans(env.unlocks).executors) == set()  # Studio registers none of its own (STU-026 will)


def test_open_plans_are_capped_per_session(env: WriteEnv) -> None:
    from tabrion_studio.write.plan import MAX_PLANS_PER_SESSION

    connection_id = _unlocked(env)
    first = _preview(env, connection_id)["plan_hash"]
    for _ in range(MAX_PLANS_PER_SESSION):
        _preview(env, connection_id)
    assert plans(env).open_count(env.session()) == MAX_PLANS_PER_SESSION
    _refused(_confirm(env, connection_id, first), 400, "studio.plan_unknown")


def test_a_discarded_plan_is_gone(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    preview = _preview(env, connection_id)
    assert env.send("DELETE", "/api/write/plans", {"plan_hash": preview["plan_hash"]}).status_code == 204
    _refused(_confirm(env, connection_id, preview["plan_hash"]), 400, "studio.plan_unknown")
    assert env.send("DELETE", "/api/write/plans", {"plan_hash": "nonsense"}).status_code == 204


# --- canary (TX-14) --------------------------------------------------------------------------------------------------


def test_no_value_reaches_an_answer_a_log_or_the_audit_log(env: WriteEnv) -> None:
    """A marked cell value in the plan request appears in no preview, confirmation, run answer or refusal, in no log
    (the ``logs`` fixture checks the file) and in no audit record; nor does a known secret."""
    env.config.secrets.session.set("unused", CANARY_SECRET)
    connection_id = _unlocked(env)
    answers = []
    preview = _plan(env, connection_id, CANARY_VALUE)
    answers.append(preview)
    plan_hash = preview.json()["plan_hash"]
    answers.append(_confirm(env, connection_id, plan_hash, CANARY_VALUE))  # refused: a wrong typed text
    answers.append(_confirm(env, connection_id, plan_hash))
    answers.append(_execute(env, connection_id, plan_hash))
    assert answers[-1].status_code == 200
    assert _material(env.db) == CANARY_VALUE
    drifted = _plan(env, connection_id, CANARY_VALUE + "-2")
    answers.append(drifted)
    answers.append(_confirm(env, connection_id, drifted.json()["plan_hash"]))
    _alter(env.db, "ALTER TABLE parts ADD COLUMN extra TEXT")
    answers.append(_execute(env, connection_id, drifted.json()["plan_hash"]))
    assert answers[-1].json()["code"] == "studio.plan_drift"
    answers.append(_plan(env, connection_id, CANARY_VALUE, table=CANARY_VALUE))  # refused: not a table
    for answer in answers:
        assert CANARY_VALUE not in answer.text and CANARY_SECRET not in answer.text
    audit = b"".join(path.read_bytes() for path in sorted(env.audit_dir.iterdir()) if path.is_file())
    assert CANARY_VALUE.encode() not in audit and CANARY_SECRET.encode() not in audit
    assert audit.count(plan_hash.encode()) == 1  # in the run.start record


# --- kill switch (STU-ADR-0006 proof items 1, 2, 6; TX-02) ---------------------------------------------------------


@pytest.fixture
def off(tmp_path: Path, static_dir: Path) -> Iterator[WriteEnv]:
    environment = WriteEnv(tmp_path, static_dir)
    try:
        yield environment
    finally:
        environment.close()


@pytest.mark.parametrize(("method", "path"), PLAN_ROUTES, ids=[f"{m} {p}" for m, p in PLAN_ROUTES])
def test_flag_off_every_plan_route_is_405_and_changes_nothing(off: WriteEnv, method: str, path: str) -> None:
    off.create_read()
    connection_id = off.open()
    before = digest(off.db)
    body = {
        "connection_id": connection_id,
        "executor": "test.rows",
        "request": {"rows": [{"id": 1, "material": CANARY_VALUE}]},
        "plan_hash": "0" * 64,
        "typed": "parts-write",
    }
    response = off.send(method, path, body)
    _refused(response, 405, "studio.method_not_allowed")
    assert digest(off.db) == before
    assert not off.audit_dir.exists()
    assert getattr(off.studio.client.app.state, "write_plans", None) is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "areas",
    [WriteAreas(frozenset({Area.SCHEMA_DESIGNER})), WriteAreas(frozenset({Area.DATABASE_MANAGER}))],
)
def test_another_areas_flag_does_not_run_a_data_editor_plan(
    tmp_path: Path, static_dir: Path, areas: WriteAreas
) -> None:
    """With only another area on, the routes exist but the Data Editor executor's plans stay refused (405)."""
    environment = WriteEnv(tmp_path, static_dir, areas)
    try:
        plans(environment).register(RowExecutor())
        environment.create_read()
        connection_id = environment.open()
        response = _plan(environment, connection_id)
        _refused(response, 405, "studio.method_not_allowed")
    finally:
        environment.close()


def _modules_after(tmp_path: Path, areas: str) -> list[str]:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tests.support.write import WriteEnv, DATA_EDITOR\n"
        "from tabrion_studio.config.write_areas import ALL_OFF\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), {areas})\n"
        "env.create_read()\n"
        "c = env.open()\n"
        "for m, p in [('POST', '/api/write/plans'), ('POST', '/api/write/plans/confirm'),\n"
        "             ('POST', '/api/write/plans/execute'), ('DELETE', '/api/write/plans')]:\n"
        "    r = env.send(m, p, {'connection_id': c, 'plan_hash': '0' * 64, 'executor': 'x', 'request': {}})\n"
        "    assert r.status_code in (400, 405, 204), r.text\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write')))\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=root, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    loaded: list[str] = json.loads(result.stdout.strip().splitlines()[-1].replace("'", '"'))
    return loaded


def test_with_every_flag_off_no_plan_code_is_imported(tmp_path: Path) -> None:
    """TX-02: the plan routes' refusing handlers import no write code, the plan module included."""
    assert _modules_after(tmp_path, "ALL_OFF") == []


def test_with_a_flag_on_the_plan_code_is_loaded(tmp_path: Path) -> None:
    """The counterpart: the test above is not vacuous."""
    assert "tabrion_studio.write.plan" in _modules_after(tmp_path, "DATA_EDITOR")
