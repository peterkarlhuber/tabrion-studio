"""The STU-CR-5 follow-up: the Schema Designer's backup before a destructive PostgreSQL run, through the verified
``pg_dump`` runner of STU-XDM-2 (STU-ADR-0007 section 7; STU-ADR-0008 section 3; GS3-15, GS3-30; GS4-04..07 as G-S3
conditions).

1. **Kill switch.** Every area is off by default; with the ``schema_designer`` flag off the run routes answer 405 and
   no tool-run or backup folder is made.
2. **When it is offered** (:func:`execute.backup_possible`, :func:`execute.blocked`): a PostgreSQL plan with a
   ``destructive``, ``rebuild`` or ``data_changing`` step is ``backup_unavailable`` without both tools in
   ``database-tools.toml``, without the folder of ``write-areas.toml``, with an unsafe tools file and on Windows.
3. **The backup** (:func:`execute.backup_postgres`, fake tools that record what they get): an archive and its
   metadata in ``backups/schema-designer`` (0600, origin ``schema_designer``, the scope notice), the password only in
   the passfile; refused, with nothing left behind: no tools, a tool that is not safe, a tool older than the server,
   a tool that fails, a profile changed since the unlock, Windows (``tool.platform``).
4. **The run** (:func:`execute.run`): ``execute_plan`` is called only after the backup returned; a failed backup
   refuses before it, with ``run.start`` and ``run.result`` (the code) in the audit log; a dry run makes none.

The real ``pg_dump`` against a server is in ``test_schema_designer_execution_postgres.py``. The fake tools are POSIX
programs, so the backup tests skip on Windows; the Windows refusal is tested on every system.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from tabrion_studio.config.write_areas import ALL_OFF, Area, load_write_areas
from tabrion_studio.write import pgtools
from tabrion_studio.write.backup_store import BackupStore
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.schema_designer import execute
from tests.security.test_dbm_tools import CANARY_PASSWORD, Fakes
from tests.support.write import WriteEnv

pytestmark = pytest.mark.security

POSIX = sys.platform != "win32"
posix_only = pytest.mark.skipif(not POSIX, reason="the runner refuses Windows; the fake tools are POSIX programs")
CONFIRM = "/api/write/schema-designer/plan/confirm"
RUN = "/api/write/schema-designer/plan/run"


def clock() -> datetime.datetime:
    return datetime.datetime(2026, 11, 16, 8, 30, tzinfo=datetime.UTC)


DESTRUCTIVE = SimpleNamespace(steps=[SimpleNamespace(transactional=True, target="s.parts")], risks=["destructive"])


class FakeConnection:
    engine = "postgresql"
    timeout_s = 5.0

    def __init__(self, version: tuple[int, ...] = (16, 4)) -> None:
        self.version = version

    def engine_version(self) -> tuple[int, ...]:
        return self.version


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def append(self, event: str, **fields: Any) -> None:
        self.records.append((event, fields))

    def record(self, event: str, **fields: Any) -> bool:
        self.records.append((event, fields))
        return True


class FakeUnlocks:
    """What :func:`execute.backup_postgres` and :func:`execute.run` use of :class:`Unlocks`."""

    def __init__(self, changed: bool = False) -> None:
        self.changed = changed
        self.audit = FakeAudit()
        self.locked: list[tuple[str, str]] = []

    def unlock_credentials(self, _unlock: Any) -> tuple[Any, Any, str | None]:
        if self.changed:
            raise WriteError("studio.write_target_changed")
        write = SimpleNamespace(user="shop_w", requirepeer=None)
        read = SimpleNamespace(host="db.example.test", port=5432, database="shop", sslmode="verify-full")
        return write, read, CANARY_PASSWORD

    def lock(self, session: str, connection_id: str) -> None:
        self.locked.append((session, connection_id))


def _unlock(version: tuple[int, ...] = (16, 4)) -> Any:
    return SimpleNamespace(
        connection=FakeConnection(version),
        connection_id="c1",
        session="s1",
        kind="schema",
        write_name="shop-w",
        read_name="shop",
        environment="dev",
    )


def _plan() -> Any:
    from tabrion.plan import RiskClass

    return SimpleNamespace(
        confirmation=execute.Confirmation(classes=frozenset({RiskClass.DESTRUCTIVE}), backup="make"),
        tables=["s.parts"],
        plan_hash="sha256:" + "a" * 64,
        risks=frozenset({"destructive"}),
        steps=(object(),),
        target=object(),
        source_fingerprint="v2:sha256:" + "b" * 64,
        schema="s",
        server_version=(16, 4),
    )


def _folder(fakes: Fakes) -> Path:
    return fakes.state / "backups" / "schema-designer"


def _left(fakes: Fakes) -> list[str]:
    """Every file below the backup and tool-run folders (a backup, a part of one, a passfile)."""
    found: list[str] = []
    for top in (fakes.state / "backups", fakes.state / pgtools.RUN_FOLDER):
        if top.exists():
            found += [str(p.relative_to(fakes.state)) for p in top.rglob("*") if not p.is_dir()]
    return found


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    if not POSIX:
        pytest.skip("the runner refuses Windows; the fake tools are POSIX programs")
    return Fakes(tmp_path)


# --- 1. kill switch -----------------------------------------------------------------------------------------------


def test_every_area_is_off_by_default_and_the_run_routes_are_405(tmp_path: Path, static_dir: Path) -> None:
    folder = tmp_path / "no-flags"
    folder.mkdir(mode=0o700)
    assert load_write_areas(folder) == ALL_OFF and not ALL_OFF.on(Area.SCHEMA_DESIGNER)
    env = WriteEnv(tmp_path, static_dir)
    try:
        env.create_read()
        connection_id = env.open()
        for path, body in (
            (CONFIRM, {"plan_hash": "sha256:" + "0" * 64, "typed": "parts-write", "tables": ["parts"]}),
            (RUN, {"plan_hash": "sha256:" + "0" * 64, "dry_run": False}),
        ):
            response = env.send("POST", path, {"connection_id": connection_id, **body})
            assert (response.status_code, response.json()["code"]) == (405, "studio.method_not_allowed")
        assert not (env.config_dir / "backups").exists()
        assert not (env.config_dir / pgtools.RUN_FOLDER).exists()
    finally:
        env.close()


# --- 2. when the backup is offered --------------------------------------------------------------------------------


@posix_only
def test_a_destructive_postgresql_plan_is_offered_only_with_both_tools(fakes: Fakes) -> None:
    unlock = _unlock()
    project = fakes.base  # no alembic.ini
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(execute, "management", lambda _connection, _project: [])
        assert execute.blocked(unlock, DESTRUCTIVE, project, fakes.state) == ("backup_unavailable",)
        assert execute.blocked(unlock, DESTRUCTIVE, project, None) == ("backup_unavailable",)
        fakes.configure(fakes.tool("pg_dump"), None)
        assert execute.blocked(unlock, DESTRUCTIVE, project, fakes.state) == ("backup_unavailable",)
        fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"), mode=0o644)  # not private: not read
        assert execute.blocked(unlock, DESTRUCTIVE, project, fakes.state) == ("backup_unavailable",)
        fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
        assert execute.backup_possible(fakes.state) is True
        assert execute.blocked(unlock, DESTRUCTIVE, project, fakes.state) == ()
        # The other conditions of section 5 are untouched: STAGE still never runs.
        stage = SimpleNamespace(**{**vars(unlock), "environment": "stage"})
        assert execute.blocked(stage, DESTRUCTIVE, project, fakes.state) == ("environment",)  # type: ignore[arg-type]
        additive = SimpleNamespace(steps=DESTRUCTIVE.steps, risks=["additive"])
        assert execute.blocked(unlock, additive, project, None) == ()


def test_windows_is_never_offered_a_postgresql_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute, "_POSIX", False)
    assert execute.backup_possible(tmp_path) is False
    monkeypatch.setattr(execute, "management", lambda _connection, _project: [])
    assert execute.blocked(_unlock(), DESTRUCTIVE, tmp_path, tmp_path) == ("backup_unavailable",)


# --- 3. the backup ------------------------------------------------------------------------------------------------


@posix_only
def test_the_backup_is_a_verified_archive_with_its_metadata(fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
    made = execute.backup_postgres(FakeUnlocks(), _unlock(), fakes.state, clock)  # type: ignore[arg-type]
    folder = _folder(fakes)
    assert made.path.parent == folder and made.path.name.startswith("20261116T083000Z-")
    assert made.path.suffix == ".dump" and made.path.read_bytes().startswith(b"PGDMP")
    for path in (folder.parent, folder):
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o700
    meta_path = folder / f"{made.path.name}.json"
    for path in (made.path, meta_path):
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    meta = json.loads(meta_path.read_text())
    assert meta["origin"] == "schema_designer" and meta["engine"] == "postgresql" and meta["complete"] is False
    assert meta["scope_notice"].startswith("Not a complete backup") and meta["size"] == made.size
    assert meta["source"] == {"database": "shop", "profile": "shop", "environment": "dev"}
    assert meta["tool"]["name"] == "pg_dump" and meta["tool"]["version"] == "17.2" and meta["extensions"] == 1
    # The tool connected with the write role and the profile's sslmode; the password only in the passfile.
    seen = fakes.seen("pg_dump")
    (dbname,) = [a for a in seen["argv"] if a.startswith("--dbname=")]
    assert "user='shop_w'" in dbname and "sslmode='verify-full'" in dbname and "dbname='shop'" in dbname
    assert CANARY_PASSWORD not in json.dumps(seen["argv"]) + json.dumps(seen["env"])
    assert seen["passfile"] == pgtools.passfile_line(
        pgtools.Connection("db.example.test", 5432, "shop", "shop_w", "verify-full", CANARY_PASSWORD)
    )
    assert "--enable-row-security" not in seen["argv"]
    assert CANARY_PASSWORD not in caplog.text and CANARY_PASSWORD not in meta_path.read_text()
    assert sorted(_left(fakes)) == sorted(str(p.relative_to(fakes.state)) for p in (made.path, meta_path))


@posix_only
@pytest.mark.parametrize(
    ("setup", "code", "issue"),
    [
        ("none", "studio.backup_tool_unavailable", ("tool.pg_dump", "tool.not_configured")),
        ("unsafe_file", "studio.backup_tool_unavailable", ("tool.pg_dump", "tool.not_configured")),
        ("writable_tool", "studio.backup_tool_unavailable", ("tool.pg_dump", "tool.writable")),
        ("old_tool", "studio.backup_tool_unavailable", ("tool.pg_dump", "tool.too_old")),
        ("failing_tool", "studio.backup_tool_failed", ("tool.pg_dump", "tool.auth")),
        ("bad_archive", "studio.backup_tool_failed", ("tool.pg_restore", "tool.auth")),
        ("profile_changed", "studio.write_target_changed", None),
    ],
)
def test_a_backup_that_cannot_be_made_refuses_and_leaves_nothing(
    fakes: Fakes, setup: str, code: str, issue: tuple[str, str] | None
) -> None:
    unlocks = FakeUnlocks(changed=setup == "profile_changed")
    version = (16, 4)
    if setup == "unsafe_file":
        fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"), mode=0o644)
    elif setup == "writable_tool":
        tool = fakes.tool("pg_dump")
        tool.chmod(0o777)
        fakes.configure(tool, fakes.tool("pg_restore"))
    elif setup == "old_tool":
        fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
        version = (18, 1)  # the server is newer than the tools (17.2)
    elif setup == "failing_tool":
        fakes.configure(fakes.tool("pg_dump", mode="fail"), fakes.tool("pg_restore"))
    elif setup == "bad_archive":
        fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore", mode="badlist"))
    elif setup != "none":
        fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
    with pytest.raises(WriteError) as refused:
        execute.backup_postgres(unlocks, _unlock(version), fakes.state, clock)  # type: ignore[arg-type]
    assert refused.value.code == code
    if issue is not None:
        assert refused.value.issues == [issue]
    assert [name for name in _left(fakes) if not name.startswith(pgtools.RUN_FOLDER)] == []
    assert [name for name in _left(fakes) if name.endswith("passfile")] == []


@posix_only
def test_a_backup_folder_that_is_a_file_refuses(fakes: Fakes) -> None:
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
    (fakes.state / "backups").mkdir(mode=0o700)
    _folder(fakes).write_text("not a folder")
    with pytest.raises(WriteError) as refused:
        execute.backup_postgres(FakeUnlocks(), _unlock(), fakes.state, clock)  # type: ignore[arg-type]
    assert refused.value.code == "studio.schema_backup_failed"


@posix_only
def test_metadata_that_cannot_be_written_removes_the_archive(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise WriteError("studio.dbm_backup_failed")

    monkeypatch.setattr(BackupStore, "write_metadata", broken)
    with pytest.raises(WriteError) as refused:
        execute.backup_postgres(FakeUnlocks(), _unlock(), fakes.state, clock)  # type: ignore[arg-type]
    assert refused.value.code == "studio.schema_backup_failed"
    assert list(_folder(fakes).iterdir()) == []


def test_windows_refuses_the_backup_with_tool_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows the runner refuses (its passfile and mode rules are POSIX); simulated elsewhere."""
    if POSIX:
        monkeypatch.setattr(pgtools, "_POSIX", False)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with pytest.raises(WriteError) as refused:
        execute.backup_postgres(FakeUnlocks(), _unlock(), state, clock)  # type: ignore[arg-type]
    assert refused.value.code == "studio.backup_tool_unavailable"
    assert refused.value.issues == [("tool.pg_dump", "tool.platform")]
    assert not (state / "backups").exists()


def test_no_folder_of_write_areas_refuses_the_backup() -> None:
    with pytest.raises(WriteError) as refused:
        execute.backup_postgres(FakeUnlocks(), _unlock(), None, clock)  # type: ignore[arg-type]
    assert refused.value.code == "studio.backup_tool_unavailable"


# --- 4. the run ---------------------------------------------------------------------------------------------------


def _spy_run(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    def execute_plan(_database: Any, _target: Any, **options: Any) -> Any:
        order.append("execute_plan" + (" dry" if options["dry_run"] else ""))
        return SimpleNamespace(steps_run=1, fingerprint_after="v2:sha256:" + "c" * 64)

    monkeypatch.setattr(execute, "execute_plan", execute_plan)
    monkeypatch.setattr(execute, "_RunDatabase", lambda connection: connection)


@posix_only
def test_a_successful_backup_lets_the_run_proceed(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _spy_run(monkeypatch, order)
    real = execute.backup_postgres

    def backup(*args: Any) -> Any:
        made = real(*args)
        order.append("backup")
        return made

    monkeypatch.setattr(execute, "backup_postgres", backup)
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
    unlocks = FakeUnlocks()
    outcome = execute.run(
        unlocks,  # type: ignore[arg-type]
        _unlock(),
        _plan(),
        dry_run=False,
        write_state=fakes.state,
        clock=clock,
    )
    assert order == ["backup", "execute_plan"]
    assert outcome.backup == "made" and outcome.backup_path is not None
    assert Path(outcome.backup_path).parent == _folder(fakes) and outcome.backup_path.endswith(".dump")
    events = [(event, fields.get("backup"), fields.get("result")) for event, fields in unlocks.audit.records]
    assert events == [("run.start", "made", None), ("run.result", None, "committed")]
    assert outcome.backup_path not in json.dumps(unlocks.audit.records)


@posix_only
@pytest.mark.parametrize("tools", ["none", "failing"])
def test_a_failed_backup_refuses_the_run_before_execute_plan(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch, tools: str
) -> None:
    order: list[str] = []
    _spy_run(monkeypatch, order)
    if tools == "failing":
        fakes.configure(fakes.tool("pg_dump", mode="fail"), fakes.tool("pg_restore"))
    unlocks = FakeUnlocks()
    with pytest.raises(WriteError) as refused:
        execute.run(
            unlocks,  # type: ignore[arg-type]
            _unlock(),
            _plan(),
            dry_run=False,
            write_state=fakes.state,
            clock=clock,
        )
    code = "studio.backup_tool_unavailable" if tools == "none" else "studio.backup_tool_failed"
    assert refused.value.code == code and order == []
    events = [(event, fields.get("result")) for event, fields in unlocks.audit.records]
    assert events == [("run.start", None), ("run.result", code)]
    assert not _folder(fakes).exists() or list(_folder(fakes).iterdir()) == []


@posix_only
def test_a_dry_run_makes_no_backup(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _spy_run(monkeypatch, order)
    fakes.configure(fakes.tool("pg_dump", mode="fail"), fakes.tool("pg_restore"))
    outcome = execute.run(
        FakeUnlocks(),  # type: ignore[arg-type]
        _unlock(),
        _plan(),
        dry_run=True,
        write_state=fakes.state,
        clock=clock,
    )
    assert order == ["execute_plan dry"] and outcome.backup == "dry_run" and outcome.backup_path is None
    assert not (fakes.state / "backups").exists()
