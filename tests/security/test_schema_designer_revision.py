"""STU-XSD-3: a Schema Designer plan written as an Alembic revision into the trusted project (G-S3 is not granted;
GS3-01, GS3-06..08, GS3-16 j 5, GS3-19, GS3-27; TX-19, TX-20; STU-ADR-0007 section 5, E-X4).

1. **Kill switch (GS3-01).** With the ``schema_designer`` flag off the revision route answers 405 and nothing of the
   area or ``tabrion.plan`` is imported.
2. **Conditions (section 5).** The preview says whether a revision may be written and that it does not check its
   source fingerprint (j 5); profiles not trusted (D-01), code not trusted, no Alembic project and an environment
   other than DEV or TEST refuse, at the preview and again at the write.
3. **Never run (GS3-06).** ``env.py`` never runs, the new revision never runs (the database is unchanged), no Alembic
   command runs; the head comes from the migration worker under code trust.
4. **The file (GS3-07).** Only in ``<script_location>/versions``, name ``[0-9a-f]{12}_[a-z0-9_]{1,40}.py``, created
   exclusively with the umask's mode; refused with a link as the folder, a link or a file of the same name, several
   heads, a head that is not a revision id; a replay, a drift and a wrong message are refused.
5. **The text (GS3-08), audit and canaries (GS3-19, GS3-27).** A message with quotes, backslashes and a table name
   with quotes give a file whose parts are literals only (``ast``); ``run.start`` and ``run.result`` with action
   ``schema_designer.revision``; no path of the database or secret in the file.

Python 3.11 refuses every SQLite write unlock (no defensive mode), so these tests skip there.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.write.connection import WriteError, defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, WriteEnv, digest

pytestmark = pytest.mark.security

DRAFT = "/api/write/schema-designer/draft"
REVISION = "/api/write/schema-designer/revision"
SCHEMA_DESIGNER = WriteAreas(frozenset({Area.SCHEMA_DESIGNER}))
ROOT = Path(__file__).resolve().parents[2]
POSIX = sys.platform != "win32"
FILE_NAME = re.compile(r"[0-9a-f]{12}_[a-z0-9_]{1,40}\.py")
ADD_QTY = {"op": "add_column", "table": "parts", "column": {"name": "qty", "type": "INTEGER"}}
INI = "[alembic]\nscript_location = %(here)s/migrations\n"
#: env.py would leave a mark if anything ran it (GS3-06).
ENV_PY = "import pathlib\npathlib.Path(__file__).resolve().parents[1].joinpath('env_ran').write_text('ran')\n"
PROJECT_FILE = 'version = 1\n[[profile]]\nname = "other"\nengine = "sqlite"\npath = "other.db"\nenvironment = "dev"\n'


def _revision(name: str, down: str | None) -> str:
    return (
        f'"""{name}"""\nrevision = "{name}"\ndown_revision = {down!r}\nbranch_labels = None\ndepends_on = None\n\n\n'
        "def upgrade():\n    pass\n\n\ndef downgrade():\n    pass\n"
    )


def _refused(response: Any, status: int, code: str, issues: list[tuple[str, str]] | None = None) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    if issues is not None:
        assert [(i["pointer"], i["code"]) for i in response.json().get("issues", [])] == issues


class Project:
    """A WriteEnv whose project folder is an Alembic project, with ``parts`` unlocked and a draft started."""

    def __init__(self, env: WriteEnv, connection_id: str) -> None:
        self.env, self.connection_id = env, connection_id
        self.versions = env.project / "migrations" / "versions"

    def send(self, path: str, **body: Any) -> Any:
        return self.env.send("POST", path, {"connection_id": self.connection_id, **body})

    def edit(self, edit: dict[str, Any]) -> None:
        revision = self.send(f"{DRAFT}/view").json()["revision"]
        response = self.send(f"{DRAFT}/edits", revision=revision, edit=edit)
        assert response.status_code == 200, response.text

    def preview(self) -> dict[str, Any]:
        response = self.send(f"{DRAFT}/preview")
        assert response.status_code == 200, response.text
        doc: dict[str, Any] = response.json()
        return doc

    def write(self, plan: dict[str, Any], message: object = "add qty to parts") -> Any:
        return self.send(REVISION, plan_hash=plan["plan_hash"], message=message)

    def files(self) -> list[str]:
        return sorted(path.name for path in self.versions.iterdir())

    def new_files(self) -> list[str]:
        return [name for name in self.files() if name not in ("0001_init.py", "0002_more.py")]

    def trust_code(self) -> None:
        state = self.env.code.load()
        assert state.scan is not None, state
        self.env.code.trust(state.scan.digest)

    def trust_profiles(self) -> None:
        (self.env.project / ".tabrion" / "studio.toml").write_text(PROJECT_FILE, encoding="utf-8")
        digest_shown = self.env.send("GET", "/api/project").json()["digest"]
        assert self.env.send("POST", "/api/project/trust", {"digest": digest_shown}).status_code == 204

    def runs(self) -> list[dict[str, Any]]:
        answer = self.env.send("GET", "/api/write/audit?limit=1000")
        assert answer.status_code == 200, answer.text
        records = [r for r in answer.json()["records"] if r["event"].startswith("run.")]
        return sorted(records, key=lambda r: int(r["seq"]))


def _alembic_project(project: Path) -> None:
    versions = project / "migrations" / "versions"
    versions.mkdir(parents=True)
    (project / "alembic.ini").write_text(INI)
    (project / "migrations" / "env.py").write_text(ENV_PY)
    (versions / "0001_init.py").write_text(_revision("0001", None))


def _make(tmp_path: Path, static_dir: Path, environment: str = "dev", *, trust: bool = True) -> Project:
    env = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    _alembic_project(env.project)
    env.create_read(environment=environment)
    connection_id = env.open()
    assert env.create_write().status_code == 201
    assert env.unlock(connection_id).status_code == 200
    found = Project(env, connection_id)
    if trust:
        found.trust_profiles()
        found.trust_code()
    response = found.send(DRAFT)
    assert response.status_code == 201, response.text
    return found


@pytest.fixture
def project(tmp_path: Path, static_dir: Path) -> Iterator[Project]:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    found = _make(tmp_path, static_dir)
    try:
        yield found
    finally:
        found.env.close()


# --- 1. the kill switch (GS3-01) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("areas", [WriteAreas(), DATA_EDITOR], ids=["every_flag_off", "data_editor_only"])
def test_flag_off_the_revision_route_is_405_and_writes_nothing(
    tmp_path: Path, static_dir: Path, areas: WriteAreas
) -> None:
    env = WriteEnv(tmp_path, static_dir, areas)
    try:
        _alembic_project(env.project)
        env.create_read()
        connection_id = env.open()
        body = {"connection_id": connection_id, "plan_hash": "sha256:" + "0" * 64, "message": "x"}
        _refused(env.send("POST", REVISION, body), 405, "studio.method_not_allowed")
        assert sorted(p.name for p in (env.project / "migrations" / "versions").iterdir()) == ["0001_init.py"]
    finally:
        env.close()


def test_flag_off_nothing_of_the_revision_output_is_imported(tmp_path: Path) -> None:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tabrion_studio.config.write_areas import Area, WriteAreas\n"
        "from tests.support.write import WriteEnv\n"
        f"env = WriteEnv(Path({str(tmp_path)!r}), Path({str(tmp_path / 'static')!r}), "
        "WriteAreas(frozenset({Area.DATA_EDITOR})))\n"
        "env.create_read()\n"
        "c = env.open()\n"
        f"r = env.send('POST', {REVISION!r}, {{'connection_id': c, 'plan_hash': 'x', 'message': 'x'}})\n"
        "print(r.status_code, r.json()['code'])\n"
        "env.close()\n"
        "print(sorted(m for m in sys.modules if m.startswith('tabrion_studio.write.schema_designer')\n"
        "             or m in ('tabrion.plan', 'tabrion.ddl', 'alembic')))\n"
    )
    (tmp_path / "static").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["405 studio.method_not_allowed", "[]"]


# --- 2. the conditions (section 5) -----------------------------------------------------------------------------------


def test_the_preview_offers_the_revision_and_says_it_does_not_check_the_source(project: Project) -> None:
    """GS3-16 j 5: the preview of an Alembic project offers the revision (never a direct run) and says that the revision
    does not check its source fingerprint, which it shows."""
    project.edit(ADD_QTY)
    plan = project.preview()
    assert plan["executable"] is False and "alembic_project" in plan["blocked"]
    assert plan["revision_output"] == {"available": True, "blocked": [], "checks_source": False, "max_message": 200}
    assert plan["source_fingerprint"].startswith("v2:sha256:")


@pytest.mark.parametrize("missing", ["profiles", "code", "alembic"])
def test_untrusted_profiles_untrusted_code_or_no_alembic_project_refuse(
    tmp_path: Path, static_dir: Path, missing: str
) -> None:
    """Section 5: the project's profiles (D-01) and its code must be trusted, and it must have an Alembic setup; the
    preview says why not, and the write refuses with the same reason and writes nothing."""
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    found = _make(tmp_path, static_dir, trust=False)
    try:
        if missing != "profiles":
            found.trust_profiles()
        if missing == "alembic":
            (found.env.project / "alembic.ini").unlink()
        elif missing == "profiles":
            found.trust_code()
        reason = {"profiles": "project_untrusted", "code": "code_untrusted", "alembic": "no_alembic_project"}[missing]
        found.edit(ADD_QTY)
        plan = found.preview()
        assert plan["revision_output"]["available"] is False
        assert plan["revision_output"]["blocked"] == [reason]
        _refused(found.write(plan), 400, "studio.schema_revision_not_allowed", [("plan.reason", f"plan.{reason}")])
        assert found.new_files() == [] and found.runs() == []
    finally:
        found.env.close()


@pytest.mark.parametrize("planted", ["0002_more.py", "__init__.py"])
def test_a_file_planted_after_the_trust_never_runs(project: Project, planted: str) -> None:
    """GS3-06, TX-19: the conditions are checked again at the write: a revision or a package ``__init__.py`` planted
    since the trust takes it back, and neither it nor ``env.py`` runs, in the server or in the worker."""
    project.edit(ADD_QTY)
    plan = project.preview()
    mark = project.env.project / "planted_ran"
    canary = f"import pathlib\npathlib.Path({str(mark)!r}).write_text('ran')\n"
    body = _revision("0002", "0001") if planted == "0002_more.py" else ""
    (project.versions / planted).write_text(canary + body)
    _refused(project.write(plan), 400, "studio.schema_revision_not_allowed", [("plan.reason", "plan.code_untrusted")])
    assert [name for name in project.new_files() if name != planted] == []
    assert not mark.exists() and not (project.env.project / "migrations" / "env_ran").exists()


def test_the_area_imports_no_alembic_command_or_environment() -> None:
    """TX-19 (static): no module of the Schema Designer imports Alembic at all (no ``alembic.command``, no
    ``alembic.runtime.environment``, no ``MigrationContext``); the head comes from the 1.0 migration worker."""
    folder = ROOT / "src" / "tabrion_studio" / "write" / "schema_designer"
    for path in sorted(folder.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] == "alembic" for alias in node.names), path.name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "alembic", path.name
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & {"MigrationContext", "EnvironmentContext", "upgrade"}, path.name


def test_only_dev_and_test_get_a_revision() -> None:
    """Section 5: never for PROD (PROD is never unlocked, E-X7; this is the second lock) or an unset environment."""
    from types import SimpleNamespace

    from tabrion_studio.write.schema_designer.revision import RevisionSources, availability

    change = SimpleNamespace(steps=[object()])
    sources = RevisionSources(None)
    for environment, expected in (("dev", True), ("test", True), ("prod", False), ("", False), ("stage", False)):
        reasons = availability(SimpleNamespace(environment=environment), change, sources)  # type: ignore[arg-type]
        assert ("environment" not in reasons) is expected, environment
    assert availability(SimpleNamespace(environment="dev"), SimpleNamespace(steps=[]), sources)[0] == "nothing_to_run"  # type: ignore[arg-type]


def test_a_dev_profile_that_is_an_alias_of_a_prod_database_gets_no_revision(project: Project) -> None:
    """Review I-1 of #144: a PROD profile of the user's store that names the same SQLite file (by device and inode, the
    Database Manager's matching) refuses the revision like a PROD environment, at the preview and at the write."""
    alias = project.env.tmp_path / "alias.db"
    os.link(project.env.db, alias)
    project.env.create_read(name="live", environment="prod", path=alias)
    project.edit(ADD_QTY)
    plan = project.preview()
    assert plan["revision_output"]["blocked"] == ["environment"]
    _refused(project.write(plan), 400, "studio.schema_revision_not_allowed", [("plan.reason", "plan.environment")])
    assert project.new_files() == [] and project.runs() == []


def test_the_postgres_prod_alias_matching_is_the_database_managers() -> None:
    """Review I-1 of #144 (PostgreSQL, with fakes): the same database name on the same server, by port and resolved
    addresses or by ``system_identifier`` of an open PROD connection, counts as PROD; another name or server does not;
    a target whose name or read profile is unknown fails closed."""
    from types import SimpleNamespace

    from tabrion_studio.write.prod_targets import SYSTEM_IDENTIFIER, names_prod

    def connection(name: str | None, identifier: str | None) -> Any:
        def fetch(sql: str) -> list[tuple[Any, ...]]:
            if sql == SYSTEM_IDENTIFIER:
                if identifier is None:
                    raise PermissionError("no pg_control_system")
                return [(identifier,)]
            if name is None:
                raise RuntimeError("no name")
            return [(name,)]

        return SimpleNamespace(engine="postgresql", identity=None, fetch=fetch)

    read = SimpleNamespace(name="dev", engine="postgresql", environment="dev", host="localhost", port=5432)
    prod = SimpleNamespace(
        name="live", engine="postgresql", environment="prod", host="127.0.0.1", port=5432, database="shop"
    )
    elsewhere = SimpleNamespace(**{**vars(prod), "host": "192.0.2.10"})
    assert names_prod(connection("shop", None), read, [read, prod], [])
    assert not names_prod(connection("other", None), read, [read, prod], [])
    assert not names_prod(connection("shop", None), read, [read, SimpleNamespace(**{**vars(prod), "port": 5433})], [])
    assert not names_prod(connection("shop", "7"), read, [read, elsewhere], [])
    opened = SimpleNamespace(profile_id="user:live", fetch=connection("shop", "7").fetch)
    assert names_prod(connection("shop", "7"), read, [read, elsewhere], [opened])
    assert not names_prod(connection("shop", "8"), read, [read, elsewhere], [opened])
    assert names_prod(connection(None, None), read, [read, prod], [])
    assert names_prod(connection("shop", None), None, [prod], [])
    assert not names_prod(connection("shop", None), read, [read], [])


# --- 3 and 4. never run, the file (GS3-06, GS3-07) --------------------------------------------------------------------


def test_the_revision_is_written_and_nothing_runs(project: Project) -> None:
    """GS3-06, GS3-07: the file lands in the revision folder under a checked name, with the umask's mode, follows the
    single head and holds the plan; env.py never runs, the revision never runs (the database is unchanged), and the
    new file takes the code trust back (the migration status asks again)."""
    project.edit(ADD_QTY)
    before = digest(project.env.db)
    plan = project.preview()
    response = project.write(plan)
    assert response.status_code == 201, response.text
    doc = response.json()
    [name] = project.new_files()
    assert FILE_NAME.fullmatch(name) and name == f"{doc['revision']}_add_qty_to_parts.py"
    assert doc["path"] == f"migrations/versions/{name}"
    assert doc["down_revision"] == "0001" and doc["checks_source"] is False and doc["code_trust_changed"] is True
    assert doc["plan_hash"] == plan["plan_hash"] and doc["source_fingerprint"] == plan["source_fingerprint"]
    path = project.versions / name
    umask = os.umask(0)
    os.umask(umask)
    if POSIX:
        assert path.stat().st_mode & 0o777 == 0o666 & ~umask
    text = path.read_text(encoding="ascii")
    module = ast.parse(text)
    values = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert values["revision"] == doc["revision"] and values["down_revision"] == "0001"
    assert "ALTER TABLE" in text and "qty" in text
    assert not (project.env.project / "migrations" / "env_ran").exists()
    assert digest(project.env.db) == before
    assert project.env.code.load().state == "changed"
    # GS3-19: no path of the database, no connection id, no secret in the file.
    assert str(project.env.db) not in text and project.connection_id not in text and CANARY_SECRET not in text


def test_the_write_is_audited(project: Project) -> None:
    """GS3-27: ``run.start`` before the file, ``run.result`` after it, action ``schema_designer.revision``, with the
    hash's hex part, the tables, the classes and the step count; never the message or the path."""
    project.edit(ADD_QTY)
    plan = project.preview()
    assert project.write(plan, "secret message words").status_code == 201
    start, result = project.runs()
    assert start["event"] == "run.start" and result["event"] == "run.result"
    assert start["fields"]["action"] == "schema_designer.revision" and start["fields"]["area"] == "schema_designer"
    assert start["fields"]["plan_hash"] == plan["plan_hash"].removeprefix("sha256:")
    assert start["fields"]["objects"] == ["main.parts"] and start["fields"]["steps"] == 1
    assert result["fields"]["result"] == "written" and result["fields"]["run"] == start["fields"]["run"]
    records = project.env.send("GET", "/api/write/audit?limit=1000").text
    assert "secret message words" not in records and "migrations/versions" not in records


def test_a_replay_a_drift_and_a_wrong_message_are_refused(project: Project) -> None:
    """One attempt per preview; a database change after the preview is ``studio.plan_drift``; a wrong message may be
    typed again (the plan stays)."""
    project.edit(ADD_QTY)
    plan = project.preview()
    for message in ("", "   ", "two\nlines", "nul\x00", "x" * 201, 7, None):
        _refused(project.write(plan, message), 400, "studio.schema_revision_invalid")
    assert project.write(plan).status_code == 201
    _refused(project.write(plan), 400, "studio.plan_unknown")
    plan = project.preview()
    import sqlite3

    connection = sqlite3.connect(project.env.db)
    connection.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    _refused(project.write(plan), 400, "studio.plan_drift")
    assert len(project.new_files()) == 1


def test_several_heads_refuse(project: Project) -> None:
    """GS3-07: the new revision follows the single head; with two heads nothing is written."""
    (project.versions / "0002_more.py").write_text(_revision("0002", None))
    project.trust_code()
    project.edit(ADD_QTY)
    plan = project.preview()
    assert plan["revision_output"]["available"] is True
    _refused(project.write(plan), 409, "studio.schema_revision_head_unknown")
    assert project.new_files() == [] and project.runs() == []


@pytest.mark.skipif(not POSIX, reason="POSIX links")
def test_a_link_as_the_revision_folder_refuses(project: Project, tmp_path: Path) -> None:
    """GS3-07: the revision folder is reached without a link: a link to a copy with the same files (so the digest
    still matches) refuses, and nothing lands in either folder."""
    elsewhere = tmp_path / "elsewhere"
    project.versions.rename(elsewhere)
    project.versions.symlink_to(elsewhere, target_is_directory=True)
    project.edit(ADD_QTY)
    plan = project.preview()
    response = project.write(plan)
    assert response.status_code in (400, 409), response.text
    assert response.json()["code"] in ("studio.schema_revision_folder", "studio.schema_revision_not_allowed")
    assert sorted(p.name for p in elsewhere.iterdir()) == ["0001_init.py"]


def test_an_existing_name_is_never_overwritten(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    """GS3-07: created exclusively: a file of the new name (here a trusted revision, the head) stays as it is."""
    monkeypatch.setattr("tabrion_studio.write.schema_designer.revision.secrets.token_hex", lambda n: "ab" * n)
    name = "abababababab_add_qty_to_parts.py"
    kept = _revision("0002", "0001")
    (project.versions / name).write_text(kept)
    project.trust_code()
    project.edit(ADD_QTY)
    plan = project.preview()
    _refused(project.write(plan), 409, "studio.schema_revision_exists")
    assert (project.versions / name).read_text() == kept
    assert [r["fields"]["result"] for r in project.runs() if r["event"] == "run.result"] == [
        "studio.schema_revision_exists"
    ]


@pytest.mark.skipif(not POSIX, reason="POSIX links")
def test_a_link_of_the_new_name_or_as_the_folder_is_never_followed(tmp_path: Path) -> None:
    """GS3-07: ``write_file`` never follows a link: one of the new name stays and its target is untouched; a folder
    that became a link after its check (another inode) refuses."""
    from tabrion_studio.write.schema_designer.revision import revision_folder, write_file

    project = tmp_path / "p"
    (project / "migrations" / "versions").mkdir(parents=True)
    folder, found = revision_folder(project, project / "migrations")
    target = tmp_path / "target.txt"
    target.write_text("keep")
    (folder / "0123456789ab_x.py").symlink_to(target)
    with pytest.raises(WriteError) as caught:
        write_file(folder, found, "0123456789ab_x.py", "x = 1\n")
    assert caught.value.code == "studio.schema_revision_exists" and target.read_text() == "keep"
    folder.rename(tmp_path / "moved")
    (tmp_path / "other").mkdir()
    folder.symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(WriteError) as caught:
        write_file(folder, found, "0123456789ab_y.py", "x = 1\n")
    assert caught.value.code == "studio.schema_revision_folder" and list((tmp_path / "other").iterdir()) == []
    with pytest.raises(WriteError) as caught:
        revision_folder(project, project / "migrations")
    assert caught.value.code == "studio.schema_revision_folder"


def test_a_head_that_is_not_a_revision_id_refuses() -> None:
    from tabrion_studio.write.schema_designer.revision import head_of

    def status(**alembic: Any) -> dict[str, Any]:
        base = {"format": "alembic", "code_trust": "trusted", "revisions": "read", "heads": ["0001"]}
        return {"formats": [{**base, **alembic}]}

    assert head_of(status()) == "0001"
    cases: list[dict[str, Any]] = [
        status(heads=[]),
        status(heads=["a", "b"]),
        status(heads=["x'); import os #"]),
        status(heads=None),
        status(code_trust="changed"),
        status(revisions="unknown (could not be read)"),
        {"formats": []},
        {},
    ]
    for bad in cases:
        with pytest.raises(WriteError) as caught:
            head_of(bad)
        assert caught.value.code == "studio.schema_revision_head_unknown"


def test_the_name_is_built_from_the_id_and_a_slug() -> None:
    from tabrion_studio.write.schema_designer.revision import file_name, slug

    assert slug("Add QTY to parts!") == "add_qty_to_parts"
    assert slug("../../etc/passwd") == "etc_passwd"
    assert slug('"""; import os') == "import_os"
    assert slug("ÄÖÜ") == "revision"
    assert len(slug("a" * 300)) == 40
    assert FILE_NAME.fullmatch(file_name("0123456789ab", "x/../y"))


# --- 5. the text (GS3-08) -------------------------------------------------------------------------------------------


def test_quotes_and_backslashes_stay_literals(project: Project) -> None:
    """GS3-08 (tabrion K-3 vectors): a message and a column name with quotes and backslashes give a file whose parts
    are a docstring, imports, assignments of literals and the two functions; the message is a literal."""
    message = '""" + __import__("os").system("x") + """ \\ \' end'
    project.edit({"op": "add_column", "table": "parts", "column": {"name": 'q"""\\x', "type": "INTEGER"}})
    plan = project.preview()
    response = project.write(plan, message)
    assert response.status_code == 201, response.text
    [name] = project.new_files()
    module = ast.parse((project.versions / name).read_text(encoding="ascii"))
    kinds = set()
    for node in module.body:
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant)
            kinds.add("docstring")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            kinds.add("import")
        elif isinstance(node, ast.Assign):
            ast.literal_eval(node.value)
            kinds.add("assign")
        elif isinstance(node, ast.FunctionDef):
            assert node.name in ("upgrade", "downgrade")
            kinds.add("function")
        else:  # pragma: no cover - the test fails here
            raise AssertionError(ast.dump(node))
    assert kinds == {"docstring", "import", "assign", "function"}
    assert not any(isinstance(n, ast.Name) and n.id == "__import__" for n in ast.walk(module))
