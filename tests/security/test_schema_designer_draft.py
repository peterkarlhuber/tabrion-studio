"""STU-CR-4: the Schema Designer's server-side draft, its edit operations and the read-only plan preview (G-S3 is not
granted; GS3-01, GS3-02, GS3-09, GS3-13, GS3-20; TX-21, TX-26, TX-27; STU-ADR-0007 sections 2, 3, 9 and 11).

1. **Kill switch (GS3-01, GS3-02).** With the ``schema_designer`` flag off (every flag off, or only another area's)
   the five draft routes answer ``405 studio.method_not_allowed``, change nothing and import nothing of the area,
   nor ``tabrion.plan`` or ``tabrion.ddl``; the counterpart shows the flag loads them.
2. **Server-side only (GS3-09).** A model, a snapshot, a plan or its JSON in a request is refused; the area never
   calls ``from_json`` (static check); the draft is bound to the session, the connection and the unlock and ends
   with the unlock; it is never written to disk.
3. **server_version (GS3-13).** Every ``plan`` call of the area passes the version of the write connection's library
   (a spy on every call, and a static check that each call names ``server_version``).
4. **Edits.** Each operation is checked against the level-2 model; tabrion's refusals and the area's own checks are
   fixed catalog problems; a refused edit leaves the draft as it was; the limits refuse with
   ``studio.schema_draft_limit``.
5. **Preview.** The plan from a fresh snapshot: steps, risk classes, transactional flag, SQL, fingerprints and hash;
   nothing runs, nothing is audited; no SQL, name or default text reaches the log or the audit log (TX-27).

Python 3.11 refuses every SQLite write unlock (no defensive mode), so the tests that need one skip there, as in
``test_dbm_create.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security

DRAFT = "/api/write/schema-designer/draft"
VIEW = f"{DRAFT}/view"
EDITS = f"{DRAFT}/edits"
PREVIEW = f"{DRAFT}/preview"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / "src" / "tabrion_studio" / "write" / "schema_designer"
needs_defensive = pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses a SQLite write connection")


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


class Designer:
    """A Schema Designer env with ``parts`` unlocked and helpers for the draft routes."""

    def __init__(self, env: WriteEnv, connection_id: str) -> None:
        self.env, self.connection_id = env, connection_id

    def start(self, **extra: Any) -> Any:
        return self.env.send("POST", DRAFT, {"connection_id": self.connection_id, **extra})

    def view(self) -> Any:
        return self.env.send("POST", VIEW, {"connection_id": self.connection_id})

    def revision(self) -> int:
        revision: int = self.view().json()["revision"]
        return revision

    def edit(self, edit: dict[str, Any], revision: int | None = None) -> Any:
        rev = self.revision() if revision is None else revision
        return self.env.send("POST", EDITS, {"connection_id": self.connection_id, "revision": rev, "edit": edit})

    def ok(self, edit: dict[str, Any]) -> dict[str, Any]:
        response = self.edit(edit)
        assert response.status_code == 200, response.text
        doc: dict[str, Any] = response.json()
        return doc

    def preview(self) -> Any:
        return self.env.send("POST", PREVIEW, {"connection_id": self.connection_id})

    def table(self, name: str) -> dict[str, Any]:
        table: dict[str, Any] = next(t for t in self.view().json()["tables"] if t["name"] == name)
        return table


def _sql(path: Path, statement: str) -> list[tuple[Any, ...]]:
    """One statement on the test's own connection to the file (not Studio's), closed again."""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(statement).fetchall()
        connection.commit()
    finally:
        connection.close()
    return rows


def _records(env: WriteEnv) -> list[dict[str, Any]]:
    answer = env.send("GET", "/api/write/audit?limit=1000")
    assert answer.status_code == 200, answer.text
    records: list[dict[str, Any]] = answer.json()["records"]
    return records


@pytest.fixture
def designer(tmp_path: Path, static_dir: Path) -> Iterator[Designer]:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    env = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    try:
        env.create_read()
        connection_id = env.open()
        assert env.create_write().status_code == 201
        assert env.unlock(connection_id).status_code == 200
        yield Designer(env, connection_id)
    finally:
        env.close()


# --- 1. the kill switch (GS3-01, GS3-02) ---------------------------------------------------------------------------

ROUTES: list[tuple[str, str, dict[str, Any]]] = [
    ("POST", DRAFT, {"schema": "main"}),
    ("DELETE", DRAFT, {}),
    ("POST", VIEW, {}),
    ("POST", EDITS, {"revision": 0, "edit": {"op": "drop_table", "table": "parts"}}),
    ("POST", PREVIEW, {}),
]


@pytest.mark.parametrize(
    "areas",
    [WriteAreas(), DATA_EDITOR, DATABASE_MANAGER, WriteAreas(frozenset({Area.DATA_EDITOR, Area.DATABASE_MANAGER}))],
    ids=["every_flag_off", "data_editor_only", "database_manager_only", "both_other_areas"],
)
def test_flag_off_every_draft_route_is_405_and_changes_nothing(
    tmp_path: Path, static_dir: Path, areas: WriteAreas
) -> None:
    """Proof item 2 per route: the W-9 answer and nothing changed; an unlocked write profile of the Data Editor
    never opens the Schema Designer (GS3-02)."""
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        env.create_read()
        connection_id = env.open()
        if Area.DATA_EDITOR in areas.enabled and defensive_available():
            assert env.create_write().status_code == 201
            assert env.unlock(connection_id).status_code == 200
        before = (digest(env.db), sorted(p.name for p in env.audit_dir.iterdir()) if env.audit_dir.exists() else None)
        for method, path, body in ROUTES:
            # The body even carries an unknown key: the refusal comes first, before the body is read.
            response = env.send(method, path, {"connection_id": connection_id, **body, "value": CANARY_VALUE})
            _refused(response, 405, "studio.method_not_allowed")
        after = (digest(env.db), sorted(p.name for p in env.audit_dir.iterdir()) if env.audit_dir.exists() else None)
        assert after == before
        assert not hasattr(env.studio.client.app.state, "schema_drafts")  # type: ignore[attr-defined]
    finally:
        env.close()


@pytest.mark.parametrize("query", ["?schema_designer=true", "?areas=schema_designer"])
def test_a_query_parameter_or_a_header_never_opens_the_draft_routes(
    tmp_path: Path, static_dir: Path, query: str
) -> None:
    env = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        headers = {**env.headers, "X-Tabrion-Write-Areas": "schema_designer", "X-Tabrion-Schema-Designer": "true"}
        refused = env.studio.client.post(
            f"{DRAFT}{query}", json={"connection_id": "c_x", "areas": {"schema_designer": True}}, headers=headers
        )
        _refused(refused, 405, "studio.method_not_allowed")
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
        "answers = []\n"
        f"for m, p in [('POST', {DRAFT!r}), ('DELETE', {DRAFT!r}), ('POST', {VIEW!r}), ('POST', {EDITS!r}),\n"
        f"             ('POST', {PREVIEW!r})]:\n"
        "    r = env.send(m, p, {'connection_id': c})\n"
        "    answers.append(f'{r.status_code} {r.json().get(\"code\") if r.content else None}')\n"
        "env.close()\n"
        "print(answers)\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write.schema_designer')\n"
        "             or m in ('tabrion.plan', 'tabrion.ddl')))\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    answers: list[str] = json.loads(lines[-2].replace("'", '"'))
    loaded: list[str] = json.loads(lines[-1].replace("'", '"'))
    return [*answers, *loaded]


@pytest.mark.parametrize(
    "areas",
    ["WriteAreas()", "WriteAreas(frozenset({Area.DATA_EDITOR}))", "WriteAreas(frozenset({Area.DATABASE_MANAGER}))"],
)
def test_flag_off_nothing_of_the_area_or_of_tabrions_planner_is_imported(tmp_path: Path, areas: str) -> None:
    """GS3-01: none of the area's modules, ``tabrion.plan`` or ``tabrion.ddl`` is loaded with the flag off."""
    assert _modules(tmp_path, areas) == ["405 studio.method_not_allowed"] * 5


def test_flag_on_the_area_and_the_planner_are_loaded(tmp_path: Path) -> None:
    """The counterpart: with the flag on the routes answer (no unlock yet) and the area is loaded."""
    loaded = _modules(tmp_path, "WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))")
    assert loaded[:5] == [
        "405 studio.write_locked",
        "204 None",
        "405 studio.write_locked",
        "405 studio.write_locked",
        "405 studio.write_locked",
    ]
    assert "tabrion_studio.write.schema_designer.designer" in loaded and "tabrion.plan" in loaded


# --- 2. server-side only (GS3-09) ----------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["model", "target", "snapshot", "plan", "plan_json", "steps", "tables", "draft"])
def test_a_model_snapshot_or_plan_in_a_request_is_refused(designer: Designer, key: str) -> None:
    """GS3-09: no route takes a model, a snapshot or a plan: any such key refuses the request before it is read."""
    assert designer.start().status_code == 201
    payload = {"dialect": "sqlite", "tables": [], "level": 2, "steps": [], "plan_hash": "sha256:" + "0" * 64}
    for method, path, body in ROUTES:
        doc = {"connection_id": designer.connection_id, **body, key: json.dumps(payload)}
        _refused(designer.env.send(method, path, doc), 400, "access.bad_request")
    # The edit itself never takes one either: an unknown key of an operation is refused.
    response = designer.edit({"op": "add_column", "table": "parts", "column": {"name": "x", "type": "TEXT"}, key: {}})
    _refused(response, 400, "studio.schema_edit_invalid", [("edit.op", "edit.unknown_key")])
    assert designer.view().json()["revision"] == 0


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                out.append(node)
    return out


def test_the_area_never_reads_a_plan_or_a_snapshot_from_json() -> None:
    """GS3-09 (static): no module of the area calls ``from_json`` (``MigrationPlan`` or ``SchemaSnapshot``), so no
    outside text can become a plan or a model; nor does it write a draft to a file. The one exception is the schema
    comparison (STU-XSD-2, TX-28), which reads a snapshot file to show and compare it only; its own static check is in
    ``test_schema_designer_compare.py``."""
    for path in sorted(AREA.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.name == "compare.py":
            assert [ast.unparse(call.func) for call in _calls(tree, "from_json")] == ["SchemaSnapshot.from_json"]
            continue
        assert _calls(tree, "from_json") == [], path.name
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        if path.name in ("draft.py", "designer.py"):
            assert not names & {"write_text", "write_bytes", "to_json_file", "dump"}, path.name


def test_every_plan_call_of_the_area_names_server_version() -> None:
    """GS3-13 (static): each call of ``plan`` in the area passes ``server_version`` as a keyword."""
    found = 0
    for path in sorted(AREA.glob("*.py")):
        for call in _calls(ast.parse(path.read_text(encoding="utf-8")), "plan"):
            found += 1
            assert any(keyword.arg == "server_version" for keyword in call.keywords), path.name
    assert found >= 1


def test_the_draft_is_bound_to_the_unlock_and_ends_with_it(designer: Designer) -> None:
    env = designer.env
    assert designer.start().status_code == 201
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}})
    drafts = env.studio.client.app.state.schema_drafts  # type: ignore[attr-defined]
    assert len(drafts) == 1
    assert env.send("DELETE", "/api/write/unlocks", {"connection_id": designer.connection_id}).status_code == 204
    assert len(drafts) == 0
    _refused(designer.view(), 405, "studio.write_locked")
    # A new unlock does not bring back the draft of the earlier one.
    assert env.unlock(designer.connection_id).status_code == 200
    _refused(designer.view(), 400, "studio.schema_draft_missing")


def test_a_draft_of_an_earlier_unlock_is_never_used(designer: Designer) -> None:
    """Belt and braces: even if the end listener had missed it, ``Drafts.get`` drops a draft of another unlock."""
    from tabrion_studio.write.schema_designer.draft import Drafts

    assert designer.start().status_code == 201
    drafts: Drafts = designer.env.studio.client.app.state.schema_drafts  # type: ignore[attr-defined]
    draft = drafts.get(
        designer.env.session(),
        designer.connection_id,
        designer.env.unlocks.require_unlock(
            designer.env.session(), designer.connection_id, Area.SCHEMA_DESIGNER
        ).unlock_id,
    )
    draft.unlock_id = "0" * 32
    _refused(designer.view(), 400, "studio.schema_draft_missing")
    assert len(drafts) == 0
    # Another session never reaches it either.
    from tabrion_studio.write.connection import WriteError

    drafts.put(draft)
    with pytest.raises(WriteError):
        drafts.get("another-session", designer.connection_id, draft.unlock_id)


def test_the_draft_is_not_written_to_disk(designer: Designer, tmp_path: Path) -> None:
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    db = digest(designer.env.db)
    assert designer.start().status_code == 201
    designer.ok({"op": "add_table", "table": CANARY_VALUE[:40], "columns": [{"name": "id", "type": "INTEGER"}]})
    assert designer.preview().status_code == 200
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix not in (".db-wal", ".db-shm"):
            assert CANARY_VALUE[:40].encode() not in path.read_bytes(), path
    assert digest(designer.env.db) == db
    new = [p for p in tmp_path.rglob("*") if p.is_file() and p not in before]
    assert all(p.parent == designer.env.audit_dir or p.name.startswith("parts.db") for p in new), new


# --- 3. server_version (GS3-13) ------------------------------------------------------------------------------------


def test_every_plan_call_passes_the_write_connections_sqlite_version(
    designer: Designer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    seen: list[object] = []
    from tabrion.plan import plan as real

    def spy(*, current: Any, target: Any, **options: Any) -> Any:
        seen.append(options.get("server_version"))
        assert options.get("concurrent_indexes") is False
        return real(current=current, target=target, **options)

    monkeypatch.setattr(draft_module, "plan", spy)
    assert designer.start().status_code == 201
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}})
    designer.edit({"op": "add_column", "table": "parts", "column": {"name": "bad", "type": "NO SUCH TYPE"}})
    preview = designer.preview()
    assert preview.status_code == 200
    assert len(seen) == 3
    assert all(version == sqlite3.sqlite_version_info for version in seen)
    assert preview.json()["server_version"] == list(sqlite3.sqlite_version_info)


def test_a_column_drop_is_planned_in_place_with_the_version(designer: Designer) -> None:
    """With ``server_version`` from 3.35 a plain SQLite column drop runs in place (I-124 e), not as a rebuild."""
    assert designer.start().status_code == 201
    designer.ok({"op": "drop_column", "table": "parts", "column": "material"})
    steps = designer.preview().json()["steps"]
    if sqlite3.sqlite_version_info >= (3, 35):
        assert [(s["operation"], s["risks"]) for s in steps] == [("drop_column", ["destructive"])]
    else:  # pragma: no cover - every supported Python ships a newer SQLite
        assert steps[0]["operation"] == "rebuild_table"


# --- 4. edits --------------------------------------------------------------------------------------------------------


def test_start_view_edit_and_preview(designer: Designer, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    started = designer.start()
    assert started.status_code == 201, started.text
    doc = started.json()
    assert doc["engine"] == "sqlite" and doc["schema"] == "main" and doc["revision"] == 0 and doc["changed"] is False
    assert [t["name"] for t in doc["tables"]] == ["parts"]
    assert doc["tables"][0]["primary_key"] == ["id"] and doc["tables"][0]["status"] == "unchanged"
    default = f"'{CANARY_VALUE}'"
    designer.ok(
        {
            "op": "add_table",
            "table": "orders",
            "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "part_id", "type": "INTEGER"},
                {"name": "note", "type": "TEXT", "default": default},
            ],
            "primary_key": ["id"],
        }
    )
    designer.ok(
        {
            "op": "add_constraint",
            "table": "orders",
            "constraint": {
                "kind": "foreign_key",
                "columns": ["part_id"],
                "referred_table": "parts",
                "referred_columns": ["id"],
                "on_delete": "CASCADE",
            },
        }
    )
    designer.ok({"op": "add_index", "table": "orders", "index": {"name": "orders_part", "columns": ["part_id"]}})
    designer.ok({"op": "alter_column", "table": "parts", "column": "material", "default": "'steel'"})
    view = designer.view().json()
    assert view["revision"] == 4 and view["edits"] == 4 and view["changed"] is True
    orders = designer.table("orders")
    assert orders["status"] == "new" and orders["foreign_keys"][0]["referred_table"] == "parts"
    assert designer.table("parts")["status"] == "changed"
    preview = designer.preview()
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["dialect"] == "sqlite" and plan["base_changed"] is False
    # STU-CR-5: a DEV file without migration management may run the plan, after its own confirmation; the preview
    # itself runs nothing (below).
    assert plan["executable"] is True and plan["blocked"] == []
    assert plan["plan_hash"].startswith("sha256:") and plan["source_fingerprint"].startswith("v2:sha256:")
    assert plan["target_fingerprint"] == view["draft_fingerprint"]
    operations = [(s["operation"], s["target"]) for s in plan["steps"]]
    assert ("create_table", "main.orders") in operations and ("rebuild_table", "main.parts") in operations
    rebuild = next(s for s in plan["steps"] if s["operation"] == "rebuild_table")
    assert "rebuild" in rebuild["risks"] and rebuild["transactional"] is True
    assert any(CANARY_VALUE in st["sql"] for s in plan["steps"] for st in s["statements"])
    assert set(plan["risks"]) >= {"additive", "rebuild"}
    # TX-27: no default text, statement, name or hash in the log; nothing of the draft in the audit log.
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (CANARY_VALUE, "CREATE TABLE", "orders", plan["plan_hash"], "steel"):
        assert secret not in logged
    events = [r["event"] for r in _records(designer.env)]
    assert events.count("unlock") == 1 and not [e for e in events if e.startswith("run.")]
    assert CANARY_VALUE not in json.dumps(_records(designer.env))
    # Nothing ran.
    assert _sql(designer.env.db, "SELECT name FROM sqlite_master WHERE type = 'table'") == [("parts",)]


def test_a_stale_revision_is_refused_and_a_refused_edit_changes_nothing(designer: Designer) -> None:
    assert designer.start().status_code == 201
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}})
    stale = designer.edit({"op": "drop_column", "table": "parts", "column": "qty"}, revision=0)
    _refused(stale, 400, "studio.schema_draft_stale")
    _refused(
        designer.edit({"op": "drop_column", "table": "parts", "column": "qty"}, revision="1"),  # type: ignore[arg-type]
        400,
        "studio.schema_draft_stale",
    )
    before = designer.view().json()
    _refused(
        designer.edit({"op": "add_column", "table": "parts", "column": {"name": "x", "type": "NOT A TYPE"}}),
        400,
        "studio.schema_model_invalid",
        [("plan.code", "ddl.unsupported_type")],
    )
    assert designer.view().json() == before


INVALID: list[tuple[str, dict[str, Any], str, list[tuple[str, str]]]] = [
    ("unknown op", {"op": "drop_database"}, "studio.schema_edit_invalid", [("edit.op", "edit.choice")]),
    ("no op", {"table": "parts"}, "studio.schema_edit_invalid", [("edit.op", "edit.choice")]),
    (
        "missing table",
        {"op": "drop_table", "table": "nope"},
        "studio.schema_edit_invalid",
        [("edit.table", "edit.table_missing")],
    ),
    (
        "table exists",
        {"op": "add_table", "table": "PARTS", "columns": [{"name": "a", "type": "TEXT"}]},
        "studio.schema_edit_invalid",
        [("edit.table", "edit.table_exists")],
    ),
    (
        "no columns",
        {"op": "add_table", "table": "t", "columns": []},
        "studio.schema_edit_invalid",
        [("edit.columns", "edit.required")],
    ),
    (
        "dot in a name",
        {"op": "add_table", "table": "a.b", "columns": [{"name": "a", "type": "TEXT"}]},
        "studio.schema_edit_invalid",
        [("edit.table", "edit.dot")],
    ),
    (
        "control character",
        {"op": "rename_table", "table": "parts", "new_name": "a‮b"},
        "studio.schema_edit_invalid",
        [("edit.new_name", "edit.control_character")],
    ),
    (
        "no-break space",
        {"op": "rename_table", "table": "parts", "new_name": "a\u00a0b"},
        "studio.schema_edit_invalid",
        [("edit.new_name", "edit.control_character")],
    ),
    (
        "blank at the end",
        {"op": "rename_table", "table": "parts", "new_name": "ab "},
        "studio.schema_edit_invalid",
        [("edit.new_name", "edit.whitespace")],
    ),
    (
        "name too long",
        {"op": "rename_table", "table": "parts", "new_name": "ä" * 32},
        "studio.schema_edit_invalid",
        [("edit.new_name", "edit.too_long")],
    ),
    (
        "duplicate column",
        {"op": "add_column", "table": "parts", "column": {"name": "Material", "type": "TEXT"}},
        "studio.schema_edit_invalid",
        [("edit.column_name", "edit.column_exists")],
    ),
    (
        "column key unknown",
        {"op": "add_column", "table": "parts", "column": {"name": "x", "type": "TEXT", "sql": "x"}},
        "studio.schema_edit_invalid",
        [("edit.column", "edit.unknown_key")],
    ),
    (
        "pk column dropped",
        {"op": "drop_column", "table": "parts", "column": "id"},
        "studio.schema_edit_invalid",
        [("edit.column", "edit.column_in_use")],
    ),
    (
        "missing column",
        {"op": "alter_column", "table": "parts", "column": "nope", "nullable": False},
        "studio.schema_edit_invalid",
        [("edit.column", "edit.column_missing")],
    ),
    (
        "no change",
        {"op": "alter_column", "table": "parts", "column": "material"},
        "studio.schema_edit_invalid",
        [("edit.column", "edit.no_change")],
    ),
    (
        "nullable not a bool",
        {"op": "alter_column", "table": "parts", "column": "material", "nullable": "no"},
        "studio.schema_edit_invalid",
        [("edit.nullable", "edit.type")],
    ),
    (
        "second primary key",
        {"op": "add_constraint", "table": "parts", "constraint": {"kind": "primary_key", "columns": ["material"]}},
        "studio.schema_edit_invalid",
        [("edit.constraint_kind", "edit.primary_key_exists")],
    ),
    (
        "unique named on SQLite",
        {
            "op": "add_constraint",
            "table": "parts",
            "constraint": {"kind": "unique", "name": "u", "columns": ["material"]},
        },
        "studio.schema_edit_invalid",
        [("edit.constraint_name", "edit.name_not_supported")],
    ),
    (
        "fk count",
        {
            "op": "add_constraint",
            "table": "parts",
            "constraint": {
                "kind": "foreign_key",
                "columns": ["id", "material"],
                "referred_table": "parts",
                "referred_columns": ["id"],
            },
        },
        "studio.schema_edit_invalid",
        [("edit.referred_columns", "edit.count_mismatch")],
    ),
    (
        "fk action",
        {
            "op": "add_constraint",
            "table": "parts",
            "constraint": {
                "kind": "foreign_key",
                "columns": ["id"],
                "referred_table": "parts",
                "referred_columns": ["id"],
                "on_delete": "DROP",
            },
        },
        "studio.schema_edit_invalid",
        [("edit.on_delete", "edit.choice")],
    ),
    (
        "fk table",
        {
            "op": "add_constraint",
            "table": "parts",
            "constraint": {
                "kind": "foreign_key",
                "columns": ["id"],
                "referred_table": "nope",
                "referred_columns": ["id"],
            },
        },
        "studio.schema_edit_invalid",
        [("edit.referred_table", "edit.table_missing")],
    ),
    (
        "check without text",
        {"op": "add_constraint", "table": "parts", "constraint": {"kind": "check"}},
        "studio.schema_edit_invalid",
        [("edit.expression", "edit.required")],
    ),
    (
        "check with two statements",
        {
            "op": "add_constraint",
            "table": "parts",
            "constraint": {"kind": "check", "expression": "1); DROP TABLE parts; --"},
        },
        "studio.schema_model_invalid",
        [("plan.code", "ddl.unsafe_sql_text")],
    ),
    (
        "default with a comment",
        {"op": "alter_column", "table": "parts", "column": "material", "default": "'x' /* y */"},
        "studio.schema_model_invalid",
        [("plan.code", "ddl.unsafe_sql_text")],
    ),
    (
        "constraint position",
        {"op": "drop_constraint", "table": "parts", "kind": "unique", "position": 0},
        "studio.schema_edit_invalid",
        [("edit.position", "edit.constraint_missing")],
    ),
    (
        "index missing",
        {"op": "drop_index", "table": "parts", "name": "nope"},
        "studio.schema_edit_invalid",
        [("edit.name", "edit.index_missing")],
    ),
    (
        "index column",
        {"op": "add_index", "table": "parts", "index": {"name": "i", "columns": ["nope"]}},
        "studio.schema_edit_invalid",
        [("edit.index_columns", "edit.column_missing")],
    ),
    (
        "index columns twice",
        {"op": "add_index", "table": "parts", "index": {"name": "i", "columns": ["id", "ID"]}},
        "studio.schema_edit_invalid",
        [("edit.index_columns", "edit.duplicate")],
    ),
]


@pytest.mark.parametrize(("edit", "code", "issues"), [case[1:] for case in INVALID], ids=[case[0] for case in INVALID])
def test_invalid_edits_are_fixed_catalog_problems(
    designer: Designer, edit: dict[str, Any], code: str, issues: list[tuple[str, str]]
) -> None:
    """GS3-20: each refusal is a catalog problem with a field and a code, never the request's value."""
    assert designer.start().status_code == 201
    before = designer.view().json()
    _refused(designer.edit(edit), 400, code, issues)
    assert designer.view().json() == before


def test_renames_carry_the_references_and_are_planned_as_drop_and_add(designer: Designer) -> None:
    assert designer.start().status_code == 201
    designer.ok(
        {
            "op": "add_table",
            "table": "orders",
            "columns": [{"name": "id", "type": "INTEGER"}, {"name": "part", "type": "INTEGER"}],
            "primary_key": ["id"],
        }
    )
    designer.ok(
        {
            "op": "add_constraint",
            "table": "orders",
            "constraint": {
                "kind": "foreign_key",
                "columns": ["part"],
                "referred_table": "parts",
                "referred_columns": ["id"],
            },
        }
    )
    designer.ok({"op": "rename_table", "table": "orders", "new_name": "order_lines"})
    designer.ok({"op": "rename_column", "table": "order_lines", "column": "part", "new_name": "part_id"})
    lines = designer.table("order_lines")
    assert lines["foreign_keys"][0]["columns"] == ["part_id"]
    # Renaming the referred table carries every reference to it.
    designer.ok({"op": "rename_table", "table": "parts", "new_name": "items"})
    assert designer.table("order_lines")["foreign_keys"][0]["referred_table"] == "items"
    view = designer.view().json()
    assert view["dropped"] == ["parts"]
    operations = {(s["operation"], s["target"]) for s in designer.preview().json()["steps"]}
    assert ("drop_table", "main.parts") in operations and ("create_table", "main.items") in operations


def test_a_column_a_check_names_is_neither_dropped_nor_renamed(designer: Designer) -> None:
    assert designer.start().status_code == 201
    designer.ok(
        {
            "op": "add_constraint",
            "table": "parts",
            "constraint": {"kind": "check", "expression": "length(material) > 0"},
        }
    )
    issues = [("edit.column", "edit.column_named_in_text")]
    _refused(
        designer.edit({"op": "drop_column", "table": "parts", "column": "material"}),
        400,
        "studio.schema_edit_invalid",
        issues,
    )
    _refused(
        designer.edit({"op": "rename_column", "table": "parts", "column": "MATERIAL", "new_name": "m"}),
        400,
        "studio.schema_edit_invalid",
        issues,
    )
    designer.ok({"op": "drop_constraint", "table": "parts", "kind": "check", "position": 0})
    designer.ok({"op": "drop_column", "table": "parts", "column": "material"})


@pytest.mark.parametrize(
    ("text", "column", "named"),
    [
        ("größe > 0", "größe", True),  # review P132-01: a non-ASCII bare name
        ("länge(größe) > 0", "länge", True),
        ("_x1$ > 0", "_x1$", True),
        ("preis€ > 0", "preis€", True),  # a symbol is an identifier character for both engines
        ("x☃ > 0", "x☃", True),
        ("cafe\u0301 > 0", "cafe\u0301", True),  # a combining mark (NFD)
        ("préis1 > 0", "préis", False),
        ("名前 <> ''", "名前", True),
        ("a > b", "a", True),
        ("a > b", "c", False),
        ('"Größe" > 0', "größe", True),
        ('U&"gr\\00F6\\00DFe" > 0', "anything", True),  # a Unicode-escape name fails closed
        ('u&"x" > 0', "y", True),
        ("1 > 0", "x", False),
        (None, "x", False),
    ],
)
def test_which_texts_may_name_a_column(text: str | None, column: str, named: bool) -> None:
    from tabrion_studio.write.schema_designer.draft import text_may_name

    assert text_may_name(text, column.casefold()) is named


def test_a_column_with_a_non_ascii_name_a_check_names_is_kept(designer: Designer) -> None:
    """Review P132-01: a check that names a column with a non-ASCII bare name blocks its drop and rename."""
    assert designer.start().status_code == 201
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "größe", "type": "INTEGER"}})
    designer.ok({"op": "add_constraint", "table": "parts", "constraint": {"kind": "check", "expression": "größe > 0"}})
    issues = [("edit.column", "edit.column_named_in_text")]
    _refused(
        designer.edit({"op": "drop_column", "table": "parts", "column": "größe"}),
        400,
        "studio.schema_edit_invalid",
        issues,
    )
    _refused(
        designer.edit({"op": "rename_column", "table": "parts", "column": "größe", "new_name": "x"}),
        400,
        "studio.schema_edit_invalid",
        issues,
    )


def test_a_referenced_table_and_its_key_are_not_dropped(designer: Designer) -> None:
    assert designer.start().status_code == 201
    designer.ok({"op": "add_table", "table": "orders", "columns": [{"name": "part", "type": "INTEGER"}]})
    designer.ok(
        {
            "op": "add_constraint",
            "table": "orders",
            "constraint": {
                "kind": "foreign_key",
                "columns": ["part"],
                "referred_table": "parts",
                "referred_columns": ["id"],
            },
        }
    )
    _refused(
        designer.edit({"op": "drop_table", "table": "parts"}),
        400,
        "studio.schema_edit_invalid",
        [("edit.table", "edit.table_referenced")],
    )
    _refused(
        designer.edit({"op": "drop_constraint", "table": "parts", "kind": "primary_key"}),
        400,
        "studio.schema_edit_invalid",
        [("edit.kind", "edit.table_referenced")],
    )
    designer.ok({"op": "drop_constraint", "table": "orders", "kind": "foreign_key", "position": 0})
    designer.ok({"op": "drop_table", "table": "parts"})


def test_tabrion_refuses_a_column_a_view_names(designer: Designer) -> None:
    """A tabrion refusal (``PlanRefused``, reason ``dependent_object``) is a fixed problem without the view's name or
    text (STU-ADR-0007 section 2)."""
    _sql(designer.env.db, f"CREATE VIEW v_{CANARY_VALUE[-8:]} AS SELECT material FROM parts")  # noqa: S608 - a fixed text
    assert designer.start().status_code == 201
    response = designer.edit({"op": "drop_column", "table": "parts", "column": "material"})
    # STU-CR-5 (GS3-12): the table by its position in the draft, never by its name.
    issues = [("plan.reason", "plan.dependent_object"), ("tables.0", "plan.dependent_object")]
    _refused(response, 400, "studio.schema_plan_refused", issues)
    assert CANARY_VALUE[-8:] not in response.text and "parts" not in response.text


def test_the_preview_plans_from_a_fresh_snapshot(designer: Designer) -> None:
    assert designer.start().status_code == 201
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}})
    _sql(designer.env.db, "CREATE TABLE added_later (id INTEGER)")
    plan = designer.preview().json()
    assert plan["base_changed"] is True
    # The draft never had the new table: the plan shows that it would drop it.
    assert ("drop_table", "main.added_later") in {(s["operation"], s["target"]) for s in plan["steps"]}


def test_the_preview_follows_the_stored_environment(designer: Designer) -> None:
    """GS3-03: a profile changed to PROD by hand after the draft started: the file no longer couples the write profile
    (a PROD profile cannot have one), the unlock ends and with it the draft; nothing is planned."""
    assert designer.start().status_code == 201
    path = designer.env.config_dir / "profiles.toml"
    designer.env.write_file(path.read_text(encoding="utf-8").replace('environment = "dev"', 'environment = "prod"'))
    _refused(designer.preview(), 400, "studio.write_profile_unknown")
    assert designer.env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    assert len(designer.env.studio.client.app.state.schema_drafts) == 0  # type: ignore[attr-defined]
    _refused(designer.view(), 405, "studio.write_locked")


def test_the_schema_must_be_main_on_sqlite(designer: Designer) -> None:
    _refused(designer.start(schema="temp"), 400, "studio.schema_draft_schema")
    _refused(designer.start(schema=f"x{CANARY_VALUE}"[:60]), 400, "studio.schema_draft_schema")
    assert designer.start(schema="main").status_code == 201


def test_discard_needs_no_unlock_and_drops_the_draft(designer: Designer) -> None:
    assert designer.start().status_code == 201
    assert designer.env.send("DELETE", DRAFT, {"connection_id": designer.connection_id}).status_code == 204
    _refused(designer.view(), 400, "studio.schema_draft_missing")
    _refused(designer.preview(), 400, "studio.schema_draft_missing")
    assert designer.env.send("DELETE", DRAFT, {"connection_id": designer.connection_id}).status_code == 204


# --- limits (I-1) ----------------------------------------------------------------------------------------------------


def test_the_edit_limit(designer: Designer, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    monkeypatch.setattr(draft_module, "MAX_EDITS", 2)
    assert designer.start().status_code == 201
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "a", "type": "INTEGER"}})
    designer.ok({"op": "add_column", "table": "parts", "column": {"name": "b", "type": "INTEGER"}})
    _refused(
        designer.edit({"op": "add_column", "table": "parts", "column": {"name": "c", "type": "INTEGER"}}),
        400,
        "studio.schema_draft_limit",
        [("edit.edits", "edit.too_many")],
    )


def test_the_table_column_element_and_size_limits(designer: Designer, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    assert designer.start().status_code == 201
    monkeypatch.setattr(draft_module, "MAX_TABLES", 1)
    _refused(
        designer.edit({"op": "add_table", "table": "t", "columns": [{"name": "a", "type": "TEXT"}]}),
        400,
        "studio.schema_draft_limit",
        [("edit.tables", "edit.too_many")],
    )
    monkeypatch.setattr(draft_module, "MAX_COLUMNS", 2)
    _refused(
        designer.edit({"op": "add_column", "table": "parts", "column": {"name": "c", "type": "TEXT"}}),
        400,
        "studio.schema_draft_limit",
        [("edit.columns", "edit.too_many")],
    )
    monkeypatch.setattr(draft_module, "MAX_TABLE_ELEMENTS", 1)
    _refused(
        designer.edit({"op": "add_index", "table": "parts", "index": {"name": "i", "columns": ["material"]}}),
        400,
        "studio.schema_draft_limit",
        [("edit.elements", "edit.too_many")],
    )
    monkeypatch.setattr(draft_module, "MAX_TABLE_ELEMENTS", 100)
    monkeypatch.setattr(draft_module, "MAX_DRAFT_BYTES", 600)
    _refused(
        designer.edit({"op": "alter_column", "table": "parts", "column": "material", "default": "'" + "x" * 600 + "'"}),
        400,
        "studio.schema_draft_limit",
        [("edit.size", "edit.too_many")],
    )
    _refused(
        designer.edit({"op": "alter_column", "table": "parts", "column": "material", "default": "x" * 4001}),
        400,
        "studio.schema_edit_invalid",
        [("edit.default", "edit.too_long")],
    )


def test_a_draft_is_not_started_from_a_schema_above_the_table_limit(
    designer: Designer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    monkeypatch.setattr(draft_module, "MAX_TABLES", 0)
    _refused(designer.start(), 400, "studio.schema_draft_limit", [("edit.tables", "edit.too_many")])


def test_the_number_of_drafts_is_limited() -> None:
    from tabrion.schema import SchemaSnapshot

    from tabrion_studio.write.connection import WriteError
    from tabrion_studio.write.schema_designer.draft import MAX_DRAFTS, Draft, Drafts

    drafts = Drafts()
    empty = SchemaSnapshot("sqlite", (), level=2)
    for number in range(MAX_DRAFTS):
        drafts.put(Draft(f"s{number}", "c", "u", "sqlite", "main", empty, empty, (3, 45, 0)))
    with pytest.raises(WriteError) as refused:
        drafts.put(Draft("s-last", "c", "u", "sqlite", "main", empty, empty, (3, 45, 0)))
    assert refused.value.code == "studio.schema_draft_limit"
    # Starting again under the same session and connection replaces the draft instead.
    drafts.put(Draft("s0", "c", "u2", "sqlite", "main", empty, empty, (3, 45, 0)))
    drafts.unlock_ended("u")
    assert len(drafts) == 1
