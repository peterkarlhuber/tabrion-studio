"""STU-XSD-2: the Schema Designer's read-only schema comparison and its plan preview (G-S3 effective, flag off; GS3-01,
GS3-03, GS3-09, GS3-12, GS3-13, GS3-16, GS3-20; TX-27, TX-28; STU-ADR-0007 sections 1 to 3, 5, 8, 9 and 11).

1. **Kill switch (GS3-01, GS3-02).** With the ``schema_designer`` flag off (every flag off, or only another area's)
   ``POST /api/write/schema-designer/compare`` answers ``405 studio.method_not_allowed``, reads nothing and imports
   nothing of the area, nor ``tabrion.plan`` or ``tabrion.ddl``.
2. **Read only.** Comparing and previewing change no database, write no audit record, keep no plan (the designer's
   own preview is left as it was) and write nothing to the disk; the log gets event names only (TX-27).
3. **PROD (GS3-03).** A PROD connection is compared read-only, as the reference or as the compared side; a plan
   preview *for* PROD is refused (``compare.plan_prod``), the comparison is still answered.
4. **Snapshot files (TX-28, GS3-09).** A file is compared and shown only: never the source or the target of a plan
   (a spy on tabrion's ``plan``), a malformed, level-1, oversized or too deep file is refused with a fixed problem,
   at most one side is a file, and only ``compare.py`` reads one (static check). A file written on Windows (CRLF,
   indented) compares like the file it came from.
5. **D-5 (GS3-12).** Feature markers are shown per table and make the verdict fail closed (``inconclusive``); a
   marker Studio does not know is ``unknown``; a change of a marked table is refused by tabrion's plan with the
   feature's kind only.
6. **server_version (GS3-13).** The preview passes the version of the actual connection's SQLite library.

Everything here runs on Windows, macOS and Linux and as a user without any special rights (no root, no bind mount).
The draft tests need a SQLite write unlock, which Python 3.11 refuses (no defensive mode); they skip there.
The PostgreSQL counterpart (markers, triggers, policies, a non-superuser role) is
``test_schema_designer_compare_postgres.py``.
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
import sqlalchemy
from tabrion.plan import plan as tabrion_plan
from tabrion.schema import SchemaSnapshot, snapshot

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security

COMPARE = "/api/write/schema-designer/compare"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / "src" / "tabrion_studio" / "write" / "schema_designer"
#: A table name that must never reach a log or a file (TX-27); SQLite takes it as written.
CANARY_TABLE = CANARY_VALUE[:40].replace("-", "_")


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


def _sql(path: Path, *statements: str) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def _database(path: Path, *statements: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _sql(path, "CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT)", *statements)
    path.chmod(0o600)
    return path


def snapshot_text(path: Path) -> str:
    """The level-2 snapshot of ``main`` of the file, made by the test with tabrion itself (as a user's script would)."""
    engine = sqlalchemy.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            return snapshot(sqlalchemy.inspect(connection), schemas=["main"], level=2).to_json()
    finally:
        engine.dispose()


def connection(connection_id: str, schema: str | None = None) -> dict[str, Any]:
    side: dict[str, Any] = {"source": "connection", "connection_id": connection_id}
    if schema is not None:
        side["schema"] = schema
    return side


def file(text: str) -> dict[str, Any]:
    return {"source": "file", "text": text}


class Compare:
    """A Schema Designer env with three SQLite databases: ``dev`` (env.db), ``test`` and ``prod``, each open."""

    def __init__(self, env: WriteEnv, ids: dict[str, str], files: dict[str, Path]) -> None:
        self.env, self.ids, self.files = env, ids, files

    def send(self, expected: dict[str, Any], actual: dict[str, Any], **extra: Any) -> Any:
        return self.env.send("POST", COMPARE, {"expected": expected, "actual": actual, **extra})

    def ok(self, expected: dict[str, Any], actual: dict[str, Any], **extra: Any) -> dict[str, Any]:
        response = self.send(expected, actual, **extra)
        assert response.status_code == 200, response.text
        doc: dict[str, Any] = response.json()
        return doc

    def side(self, name: str) -> dict[str, Any]:
        return connection(self.ids[name])

    def digests(self) -> dict[str, str]:
        return {name: digest(path) for name, path in self.files.items()}


def _env(tmp_path: Path, static_dir: Path, areas: WriteAreas = SCHEMA_DESIGNER, **statements: list[str]) -> Compare:
    env = WriteEnv(tmp_path, static_dir, areas)
    files = {"dev": env.db}
    _sql(env.db, *statements.get("dev", []))
    files["test"] = _database(tmp_path / "test.db", *statements.get("test", []))
    files["prod"] = _database(tmp_path / "prod.db", *statements.get("prod", []))
    env.create_read()
    env.create_read("parts-test", "test", files["test"])
    env.create_read("parts-prod", "prod", files["prod"])
    ids = {
        "dev": env.open(),
        "test": env.open("user:parts-test"),
        "prod": env.open("user:parts-prod", "prod"),
    }
    return Compare(env, ids, files)


@pytest.fixture
def cmp(tmp_path: Path, static_dir: Path) -> Iterator[Compare]:
    """``test`` has a column ``qty`` that ``dev`` lacks; ``dev`` has a table ``orders`` that ``test`` lacks."""
    compare = _env(
        tmp_path,
        static_dir,
        dev=["CREATE TABLE orders (id INTEGER PRIMARY KEY, part INTEGER REFERENCES parts (id))"],
        test=["ALTER TABLE parts ADD COLUMN qty INTEGER"],
    )
    try:
        yield compare
    finally:
        compare.env.close()


# --- 1. the kill switch (GS3-01, GS3-02) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "areas",
    [WriteAreas(), DATA_EDITOR, DATABASE_MANAGER, WriteAreas(frozenset({Area.DATA_EDITOR, Area.DATABASE_MANAGER}))],
    ids=["every_flag_off", "data_editor_only", "database_manager_only", "both_other_areas"],
)
def test_flag_off_the_comparison_is_405_and_reads_nothing(tmp_path: Path, static_dir: Path, areas: WriteAreas) -> None:
    compare = _env(tmp_path, static_dir, areas)
    try:
        before = compare.digests()
        for body in (
            {"expected": compare.side("dev"), "actual": compare.side("test"), "plan": True},
            {"expected": file(snapshot_text(compare.files["dev"])), "actual": compare.side("prod")},
            {"expected": compare.side("dev"), "value": CANARY_VALUE},
        ):
            _refused(compare.env.send("POST", COMPARE, body), 405, "studio.method_not_allowed")
        assert compare.digests() == before
        assert not hasattr(compare.env.studio.client.app.state, "schema_drafts")  # type: ignore[attr-defined]
    finally:
        compare.env.close()


@pytest.mark.parametrize("query", ["?schema_designer=true", "?areas=schema_designer"])
def test_a_query_parameter_or_a_header_never_opens_the_comparison(tmp_path: Path, static_dir: Path, query: str) -> None:
    env = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        headers = {**env.headers, "X-Tabrion-Write-Areas": "schema_designer", "X-Tabrion-Schema-Designer": "true"}
        refused = env.studio.client.post(
            f"{COMPARE}{query}",
            json={"expected": {}, "actual": {}, "areas": {"schema_designer": True}},
            headers=headers,
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
        "side = {'source': 'connection', 'connection_id': c}\n"
        f"r = env.send('POST', {COMPARE!r}, {{'expected': side, 'actual': side, 'plan': True}})\n"
        'print(repr(f\'{r.status_code} {r.json().get("code", r.json().get("verdict"))}\'))\n'
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
    lines = result.stdout.strip().splitlines()
    answer: str = json.loads(lines[-2].replace("'", '"'))
    loaded: list[str] = json.loads(lines[-1].replace("'", '"'))
    return [answer, *loaded]


@pytest.mark.parametrize(
    "areas",
    ["WriteAreas()", "WriteAreas(frozenset({Area.DATA_EDITOR}))", "WriteAreas(frozenset({Area.DATABASE_MANAGER}))"],
)
def test_flag_off_nothing_of_the_area_or_of_tabrions_planner_is_imported(tmp_path: Path, areas: str) -> None:
    """GS3-01: none of the area's modules, ``tabrion.plan`` or ``tabrion.ddl`` is loaded with the flag off."""
    assert _modules(tmp_path, areas) == ["405 studio.method_not_allowed"]


def test_flag_on_the_comparison_answers_and_loads_the_area(tmp_path: Path) -> None:
    """The counterpart: with the flag on (no unlock needed for two connections) the comparison is loaded and answers."""
    loaded = _modules(tmp_path, "WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))")
    assert loaded[0] == "200 same"
    assert "tabrion_studio.write.schema_designer.compare" in loaded and "tabrion.plan" in loaded


# --- the comparison ---------------------------------------------------------------------------------------------------


def test_the_same_schema_is_same(cmp: Compare) -> None:
    doc = cmp.ok(cmp.side("dev"), cmp.side("dev"))
    assert (doc["verdict"], doc["differences"], doc["breaking"], doc["plan"]) == ("same", [], 0, None)
    assert doc["same_fingerprint"] and not doc["flagged"] and not doc["unknown_features"]
    assert doc["expected"]["fingerprint"] == doc["actual"]["fingerprint"]
    assert doc["expected"]["source"] == "connection" and doc["expected"]["schema"] == "main"
    assert doc["expected"]["environment"] == "dev" and doc["expected"]["tables"] == 2


def test_differences_are_listed_with_their_breaking_flag(cmp: Compare) -> None:
    before = cmp.digests()
    doc = cmp.ok(cmp.side("dev"), cmp.side("test"))
    assert doc["verdict"] == "different" and not doc["same_fingerprint"]
    found = {(d["kind"], d["table"], d["column"], d["breaking"]) for d in doc["differences"]}
    assert found == {("table_missing", "main.orders", None, True), ("column_added", "main.parts", "qty", False)}
    assert doc["breaking"] == 1
    assert (doc["expected"]["environment"], doc["actual"]["environment"]) == ("dev", "test")
    # Swapped, the same differences read the other way round (keyword-only compare: never confused).
    swapped = {(d["kind"], d["column"]) for d in cmp.ok(cmp.side("test"), cmp.side("dev"))["differences"]}
    assert swapped == {("table_added", None), ("column_missing", "qty")}
    assert cmp.digests() == before


def test_the_plan_preview_is_shown_never_kept_or_run(cmp: Compare, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan from the actual side to the expected one, with steps, risks, SQL and warnings; nothing changed, no
    audit record, no plan kept, and ``server_version`` is the actual connection's library version (GS3-13)."""
    from tabrion_studio.write.schema_designer import draft as draft_module

    calls: list[dict[str, Any]] = []
    real = tabrion_plan

    def spy(**options: Any) -> Any:
        calls.append(options)
        return real(**options)

    monkeypatch.setattr(draft_module, "plan", spy)
    before = cmp.digests()
    audit = sorted(p.name for p in cmp.env.audit_dir.rglob("*")) if cmp.env.audit_dir.exists() else []
    doc = cmp.ok(cmp.side("dev"), cmp.side("test"), plan=True)
    preview = doc["plan"]
    assert preview["status"] == "ready", preview
    assert preview["dialect"] == "sqlite" and preview["server_version"] == list(sqlite3.sqlite_version_info)
    operations = {(s["operation"], s["target"]) for s in preview["steps"]}
    assert ("create_table", "main.orders") in operations
    assert any(s["statements"] and s["statements"][0]["sql"] for s in preview["steps"])
    assert "destructive" in preview["risks"] or "rebuild" in preview["risks"]
    assert {"not_modelled", "fingerprint_gaps"} <= {w["code"] for w in preview["warnings"]}
    assert "plan_hash" not in preview and "plan_hash" not in doc
    assert preview["source_fingerprint"] == doc["actual"]["fingerprint"]
    assert preview["target_fingerprint"] == doc["expected"]["fingerprint"]
    assert [c["server_version"] for c in calls] == [sqlite3.sqlite_version_info]
    assert all(c["concurrent_indexes"] is False for c in calls)
    assert cmp.digests() == before
    after = sorted(p.name for p in cmp.env.audit_dir.rglob("*")) if cmp.env.audit_dir.exists() else []
    assert after == audit
    assert len(cmp.env.studio.client.app.state.schema_drafts) == 0  # type: ignore[attr-defined]
    # Nothing confirms or runs it: the run routes know no such plan.
    run = cmp.env.send("POST", "/api/write/schema-designer/plan/run", {"connection_id": cmp.ids["test"]})
    assert run.status_code in (400, 405) and run.json()["code"] != "studio.schema_run_failed"
    assert cmp.digests() == before


# --- PROD (GS3-03) ----------------------------------------------------------------------------------------------------


def test_prod_is_compared_read_only_and_never_gets_a_plan(cmp: Compare) -> None:
    before = cmp.digests()
    doc = cmp.ok(cmp.side("dev"), cmp.side("prod"), plan=True)
    assert doc["actual"]["environment"] == "prod" and doc["verdict"] == "different"
    assert doc["plan"] == {
        "status": "refused",
        "problem": {
            "code": "studio.schema_compare_plan_not_allowed",
            "issues": [{"pointer": "compare.plan", "code": "compare.plan_prod"}],
        },
    }
    # PROD as the reference: read only, and the plan is for the TEST database, never for PROD.
    reference = cmp.ok(cmp.side("prod"), cmp.side("test"), plan=True)
    assert reference["expected"]["environment"] == "prod" and reference["plan"]["status"] == "ready"
    assert cmp.digests() == before


def test_a_prod_connection_needs_its_confirmation_to_be_open(cmp: Compare) -> None:
    """The comparison reads only connections that are open: a PROD profile opens only with its confirmation."""
    refused = cmp.env.send("POST", "/api/connections", {"profile_id": "user:parts-prod"})
    assert refused.status_code == 409 and refused.json()["code"] == "studio.prod_confirmation_required"
    _refused(cmp.send(connection("c_unknown"), cmp.side("dev")), 400, "studio.write_target_unknown")


def test_no_plan_for_another_profiles_or_an_unset_environment() -> None:
    """Section 5 rules out a preview for anything but the user's own DEV and TEST profiles (unit level: a project
    profile cannot be made in this env)."""
    from tabrion_studio.write.schema_designer.compare import Side, _not_allowed

    model = SchemaSnapshot("sqlite", (), level=2)
    live = Side("connection", model, trusted=True, schema="main", environment="dev", own_profile=True)
    fake = object()
    actual = Side("connection", model, True, "main", "dev", True, fake)  # type: ignore[arg-type]
    assert _not_allowed(live, actual) is None
    assert _not_allowed(live, Side("connection", model, True, "main", "dev", False, fake)) == "plan_environment"  # type: ignore[arg-type]
    assert _not_allowed(live, Side("connection", model, True, "main", "stage", True, fake)) == "plan_environment"  # type: ignore[arg-type]
    assert _not_allowed(live, Side("connection", model, True, "main", "prod", True, fake)) == "plan_prod"  # type: ignore[arg-type]
    assert _not_allowed(live, Side("draft", model, True, "main", "dev")) == "plan_source"
    assert _not_allowed(Side("file", model, trusted=False), actual) == "plan_file"
    assert _not_allowed(live, Side("file", model, trusted=False)) == "plan_file"


# --- snapshot files (TX-28, GS3-09) ---------------------------------------------------------------------------------


def test_a_snapshot_file_is_compared_and_never_planned(cmp: Compare, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio.write.schema_designer import draft as draft_module

    calls: list[Any] = []
    monkeypatch.setattr(draft_module, "plan", lambda **options: calls.append(options))
    text = snapshot_text(cmp.files["dev"])
    doc = cmp.ok(file(text), cmp.side("dev"))
    assert doc["verdict"] == "same" and doc["expected"]["source"] == "file"
    assert doc["expected"]["schema"] is None and doc["expected"]["environment"] is None
    for expected, actual in ((file(text), cmp.side("test")), (cmp.side("test"), file(text))):
        refused = cmp.ok(expected, actual, plan=True)["plan"]
        assert refused["status"] == "refused"
        assert refused["problem"]["issues"] == [{"pointer": "compare.plan", "code": "compare.plan_file"}]
    assert calls == []


def test_a_snapshot_file_written_on_windows_compares_like_its_original(cmp: Compare) -> None:
    text = snapshot_text(cmp.files["test"])
    windows = json.dumps(json.loads(text), indent=2, ensure_ascii=False).replace("\n", "\r\n") + "\r\n"
    doc = cmp.ok(file(windows), cmp.side("test"))
    assert doc["verdict"] == "same", doc["differences"]
    assert doc["expected"]["fingerprint"] == doc["actual"]["fingerprint"]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("{", "compare.format"),
        ("[]", "compare.format"),
        ('{"format": 2, "dialect": "sqlite", "tables": "x"}', "compare.format"),
        ("[" * 100_000 + "]" * 100_000, "compare.format"),
        (json.dumps({"format": 2.0, "dialect": "sqlite", "level": 2, "tables": []}), "compare.format"),
    ],
    ids=["truncated", "not_an_object", "wrong_shape", "too_deep", "float_format"],
)
def test_a_malformed_snapshot_file_is_refused(cmp: Compare, text: str, code: str) -> None:
    response = cmp.send(file(text), cmp.side("dev"))
    _refused(response, 400, "studio.schema_snapshot_invalid", [("compare.expected", code)])


def test_a_level_one_snapshot_file_is_refused(cmp: Compare) -> None:
    engine = sqlalchemy.create_engine(f"sqlite:///{cmp.files['dev']}")
    try:
        with engine.connect() as connection:
            text = snapshot(sqlalchemy.inspect(connection), schemas=["main"], level=1).to_json()
    finally:
        engine.dispose()
    _refused(
        cmp.send(cmp.side("dev"), file(text)),
        400,
        "studio.schema_snapshot_invalid",
        [("compare.actual", "compare.level")],
    )


def test_an_oversized_snapshot_file_is_refused(cmp: Compare) -> None:
    from tabrion_studio.api.write import MAX_COMPARE_BODY
    from tabrion_studio.write.schema_designer.compare import MAX_SNAPSHOT_BYTES

    assert MAX_COMPARE_BODY == 2 * MAX_SNAPSHOT_BYTES + 4096
    big = " " * (MAX_SNAPSHOT_BYTES + 1)
    _refused(
        cmp.send(file(big), cmp.side("dev")),
        400,
        "studio.schema_snapshot_too_large",
        [("compare.expected", "compare.too_large")],
    )
    # Multi-byte characters count as their UTF-8 bytes.
    wide = "ä" * (MAX_SNAPSHOT_BYTES // 2 + 1)
    _refused(cmp.send(file(wide), cmp.side("dev")), 400, "studio.schema_snapshot_too_large")
    # Above the body cap the request is refused while it streams in.
    huge = cmp.env.send("POST", COMPARE, {"expected": file(" " * MAX_COMPARE_BODY), "actual": cmp.side("dev")})
    assert huge.status_code == 413 and huge.json()["code"] == "access.body_too_large"


def test_a_snapshot_file_over_the_table_limit_is_refused(cmp: Compare) -> None:
    doc = json.loads(snapshot_text(cmp.files["dev"]))
    table = doc["tables"][0]
    doc["tables"] = [{**table, "name": f"main.t{i:04d}"} for i in range(501)]
    _refused(
        cmp.send(file(json.dumps(doc)), cmp.side("dev")),
        400,
        "studio.schema_snapshot_too_large",
        [("compare.expected", "compare.too_large")],
    )


def test_two_files_and_other_engines_are_refused(cmp: Compare) -> None:
    text = snapshot_text(cmp.files["dev"])
    _refused(
        cmp.send(file(text), file(text)),
        400,
        "studio.schema_compare_invalid",
        [("compare.actual.source", "compare.two_files")],
    )
    other = json.loads(text)
    other["dialect"] = "postgresql"
    other["tables"] = []
    _refused(
        cmp.send(file(json.dumps(other)), cmp.side("dev")),
        400,
        "studio.schema_compare_mismatch",
        [("compare.actual", "compare.dialect")],
    )


def test_only_the_comparison_reads_a_snapshot_file_and_it_never_plans_one() -> None:
    """GS3-09, TX-28 (static): ``from_json`` is called once in the area, as ``SchemaSnapshot.from_json`` in
    ``compare.py``; ``MigrationPlan.from_json`` nowhere. The comparison imports neither the executor nor tabrion's
    ``execute_plan`` or ``tabrion.ddl``, and its only plan call is the server's own ``plan_for``."""
    found = []
    for path in sorted(AREA.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "from_json":
                found.append((path.name, ast.unparse(node)))
    assert found == [("compare.py", "SchemaSnapshot.from_json")]
    tree = ast.parse((AREA / "compare.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not {name for name in imported if "execute" in name or name.startswith("tabrion.ddl")}
    assert "tabrion.plan" not in imported
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names & {"write_text", "write_bytes", "dump", "record", "audited_transaction"}


# --- the request (GS3-09, GS3-20) -------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["model", "target", "snapshot", "plan_json", "steps", "environment", "plan_hash"])
def test_an_unknown_key_is_refused(cmp: Compare, key: str) -> None:
    body = {"expected": cmp.side("dev"), "actual": cmp.side("test"), key: CANARY_VALUE}
    _refused(cmp.env.send("POST", COMPARE, body), 400, "access.bad_request")


@pytest.mark.parametrize(
    ("expected", "actual", "extra", "issue"),
    [
        (None, {}, {}, ("compare.expected", "compare.required")),
        ("dev", {}, {}, ("compare.expected", "compare.type")),
        ({"source": "url"}, {}, {}, ("compare.expected.source", "compare.choice")),
        ({"source": "connection"}, {}, {}, ("compare.expected.connection_id", "compare.required")),
        ({"source": "file"}, {}, {}, ("compare.expected.text", "compare.required")),
        ({"source": "file", "text": 1}, {}, {}, ("compare.expected.text", "compare.type")),
        ({"source": "file", "text": "{}", "path": "/etc/passwd"}, {}, {}, ("compare.expected", "compare.unknown_key")),
        (
            {"source": "draft", "connection_id": "c", "schema": "main"},
            {},
            {},
            ("compare.expected", "compare.unknown_key"),
        ),
        ("same", "same", {"plan": "yes"}, ("compare.plan", "compare.type")),
    ],
)
def test_an_invalid_side_is_a_fixed_problem(
    cmp: Compare, expected: Any, actual: Any, extra: dict[str, Any], issue: tuple[str, str]
) -> None:
    side = cmp.side("dev")
    body = {
        "expected": side if expected == "same" else expected,
        "actual": side if actual in ({}, "same") else actual,
        **extra,
    }
    _refused(cmp.env.send("POST", COMPARE, body), 400, "studio.schema_compare_invalid", [issue])


@pytest.mark.parametrize("schema", ["temp", "other", "pg_catalog", "a.b", "x\x00"])
def test_sqlite_compares_main_only(cmp: Compare, schema: str) -> None:
    _refused(cmp.send(connection(cmp.ids["dev"], schema), cmp.side("test")), 400, "studio.schema_compare_schema")


def test_nothing_reaches_the_log_or_the_disk(
    tmp_path: Path, static_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """TX-27: a canary table name and its SQL reach the page only: never the log, the audit log or another file."""
    compare = _env(tmp_path, static_dir, dev=[f'CREATE TABLE "{CANARY_TABLE}" (id INTEGER PRIMARY KEY)'])
    try:
        caplog.set_level(logging.DEBUG)
        before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
        doc = compare.ok(compare.side("dev"), compare.side("test"), plan=True)
        assert any(d["table"] == f"main.{CANARY_TABLE}" for d in doc["differences"])
        assert CANARY_TABLE in json.dumps(doc["plan"])
        assert CANARY_TABLE not in caplog.text and "CREATE TABLE" not in caplog.text
        assert "studio.schema_compared" in {getattr(r, "event", None) for r in caplog.records}
        for path in tmp_path.rglob("*"):
            if path.is_file() and path.suffix not in (".db", ".db-wal", ".db-shm") and path not in before:
                assert CANARY_TABLE.encode() not in path.read_bytes(), path
    finally:
        compare.env.close()


# --- D-5: feature markers (GS3-12) ----------------------------------------------------------------------------------


def test_feature_markers_are_flagged_and_the_verdict_fails_closed(tmp_path: Path, static_dir: Path) -> None:
    marked = [
        "CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID",
        "CREATE TABLE log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT)",
    ]
    compare = _env(tmp_path, static_dir, dev=marked, test=marked)
    try:
        doc = compare.ok(compare.side("dev"), compare.side("test"))
        assert (doc["verdict"], doc["differences"], doc["same_fingerprint"]) == ("inconclusive", [], True)
        assert doc["flagged"] and not doc["unknown_features"]
        flagged = {t["table"]: [f["kind"] for f in t["features"]] for t in doc["expected"]["flagged"]}
        assert flagged == {"main.kv": ["without_rowid"], "main.log": ["autoincrement"]}
        assert all(f["known"] for t in doc["actual"]["flagged"] for f in t["features"])
    finally:
        compare.env.close()


def test_a_change_of_a_marked_table_is_refused_with_its_kind_only(tmp_path: Path, static_dir: Path) -> None:
    compare = _env(
        tmp_path,
        static_dir,
        # A changed type needs a rebuild, which would lose WITHOUT ROWID: tabrion refuses it (D-5.3).
        dev=["CREATE TABLE kv (k TEXT PRIMARY KEY, v INTEGER) WITHOUT ROWID"],
        test=["CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID"],
    )
    try:
        doc = compare.ok(compare.side("dev"), compare.side("test"), plan=True)
        assert doc["verdict"] == "different"
        assert doc["plan"]["status"] == "refused"
        problem = doc["plan"]["problem"]
        assert problem["code"] == "studio.schema_plan_refused"
        assert ("plan.feature", "plan.without_rowid") in {(i["pointer"], i["code"]) for i in problem["issues"]}
        assert "kv" not in json.dumps(problem)
    finally:
        compare.env.close()


def test_an_unknown_feature_marker_is_unknown_and_fails_closed(cmp: Compare) -> None:
    doc = json.loads(snapshot_text(cmp.files["test"]))
    doc["tables"][0]["features"] = ["mystery_feature:x"]
    found = cmp.ok(file(json.dumps(doc)), cmp.side("test"))
    assert found["verdict"] == "different" and found["unknown_features"] and found["flagged"]
    marker = found["expected"]["flagged"][0]["features"][0]
    assert marker == {"kind": "unknown", "name": "x", "known": False, "marker": "mystery_feature:x"}
    assert {d["kind"] for d in found["differences"]} == {"feature_missing"}
    # A database-wide marker alone, nothing else different: still not "same".
    doc["tables"][0].pop("features")
    doc["features"] = ["event_trigger:audit"]
    wide = cmp.ok(file(json.dumps(doc)), cmp.side("test"))
    assert wide["verdict"] == "different" and wide["expected"]["features"][0]["kind"] == "event_trigger"


# --- the draft as a side ----------------------------------------------------------------------------------------------


@pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses a SQLite write connection")
def test_the_draft_is_compared_and_its_own_preview_is_left_alone(cmp: Compare) -> None:
    env = cmp.env
    draft_side = {"source": "draft", "connection_id": cmp.ids["dev"]}
    _refused(cmp.send(draft_side, cmp.side("dev")), 405, "studio.write_locked")
    assert env.create_write().status_code == 201
    assert env.unlock(cmp.ids["dev"]).status_code == 200
    _refused(cmp.send(draft_side, cmp.side("dev")), 400, "studio.schema_draft_missing")
    assert env.send("POST", "/api/write/schema-designer/draft", {"connection_id": cmp.ids["dev"]}).status_code == 201
    edit = {"op": "add_column", "table": "parts", "column": {"name": "weight", "type": "REAL"}}
    edited = env.send(
        "POST",
        "/api/write/schema-designer/draft/edits",
        {"connection_id": cmp.ids["dev"], "revision": 0, "edit": edit},
    )
    assert edited.status_code == 200, edited.text
    assert (
        env.send("POST", "/api/write/schema-designer/draft/preview", {"connection_id": cmp.ids["dev"]}).status_code
        == 200
    )
    drafts = env.studio.client.app.state.schema_drafts  # type: ignore[attr-defined]
    unlock = env.unlocks.require_unlock(env.session(), cmp.ids["dev"], Area.SCHEMA_DESIGNER)
    kept = drafts.get(env.session(), cmp.ids["dev"], unlock.unlock_id).plan
    assert kept is not None
    before = cmp.digests()
    doc = cmp.ok(draft_side, cmp.side("dev"), plan=True)
    assert doc["expected"]["source"] == "draft" and doc["expected"]["environment"] == "dev"
    assert {(d["kind"], d["column"]) for d in doc["differences"]} == {("column_missing", "weight")}
    assert doc["plan"]["status"] == "ready"
    assert [s["operation"] for s in doc["plan"]["steps"]] == ["add_column"]
    # The designer's previewed plan is the same object as before: the comparison neither replaced nor dropped it.
    assert drafts.get(env.session(), cmp.ids["dev"], unlock.unlock_id).plan is kept
    # The draft as the compared side gets no plan (only a database is planned for).
    assert (
        cmp.ok(cmp.side("dev"), draft_side, plan=True)["plan"]["problem"]["issues"][0]["code"] == "compare.plan_source"
    )
    assert cmp.digests() == before
