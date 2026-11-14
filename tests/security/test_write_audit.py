"""STU-XW-2: the audit log of write and admin actions (STU-ADR-0006; TX-12, TX-13, TX-14; review N-4).

* The hash chain and its anchor: :func:`verify_log` finds an edited, a removed, a reordered and a cut record, a
  removed head or segment and a planted file, and Studio appends nothing more to a log that failed (fail closed).
* The files: folder 0700, files 0600 (POSIX), never through a link; rotation never overwrites a name.
* Failure behavior: no ``run.start`` record, no run, nothing changed (fail closed); no unlock record, no unlock; a
  ``run.result`` record that fails after the commit is kept as a pending marker, reported and written later (N-4).
* Canary: no cell value, parameter, typed text or secret reaches the log or its listing.
* What is recorded: unlock (also refused), lock (with the reason), profile create, change (the coupling) and delete,
  secret store and remove, run start and result with row counts and the plan hash.
* ``GET /api/write/audit``: read only, verifies; its flag-off 405 and the structural no-import are in
  ``test_write_kill_switch.py``.

The log keeps no file open between records, so every test may edit or replace its files on Windows too.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
from typing import Any

import pytest

from tabrion_studio.config.write_areas import Area
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets, register_secret
from tabrion_studio.write import audit as audit_module
from tabrion_studio.write.audit import CURRENT, GENESIS, HEAD, PENDING, AuditError, AuditLog, clean_fields, verify_log
from tabrion_studio.write.connection import WriteError, defensive_available
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, DATABASE_MANAGER, WriteEnv, digest

pytestmark = pytest.mark.security
POSIX = sys.platform != "win32"
needs_defensive = pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses SQLite write connections")
PLAN = "ab" * 32


# --- helpers -------------------------------------------------------------------------------------------------------


def _lines(folder: Path, name: str = CURRENT) -> list[bytes]:
    return (folder / name).read_bytes().split(b"\n")[:-1]


def _put(folder: Path, lines: list[bytes], name: str = CURRENT) -> None:
    """Rewrite a file of the log by hand (as someone tampering with it would), keeping it private."""
    path = folder / name
    path.write_bytes(b"".join(line + b"\n" for line in lines))
    path.chmod(0o600)


def _records(folder: Path) -> list[dict[str, Any]]:
    out = []
    for path in [*sorted(folder.glob("audit-*.jsonl")), folder / CURRENT]:
        if path.exists():
            out += [json.loads(line) for line in path.read_bytes().split(b"\n")[:-1]]
    return out


def _log_with(folder: Path, count: int = 5) -> AuditLog:
    log = AuditLog(folder)
    for index in range(count):
        log.append("lock", profile=f"p{index}", kind="write", reason="locked")
    return log


def _every_byte(folder: Path, without: str | None = None) -> bytes:
    return b"".join(path.read_bytes() for path in sorted(folder.iterdir()) if path.is_file() and path.name != without)


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
    try:
        yield environment
    finally:
        environment.close()


def _unlocked(env: WriteEnv) -> str:
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert response.status_code == 200, response.text
    return connection_id


def _events(env: WriteEnv) -> list[str]:
    return [record["event"] for record in _records(env.audit_dir)]


# --- the chain -----------------------------------------------------------------------------------------------------


def test_a_new_log_chains_every_record_and_anchors_the_last(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    log = _log_with(folder, 3)
    records = _records(folder)
    assert [r["seq"] for r in records] == [1, 2, 3]
    assert records[0]["prev"] == GENESIS
    assert records[1]["prev"] == records[0]["hash"] and records[2]["prev"] == records[1]["hash"]
    head = json.loads((folder / HEAD).read_bytes())
    assert (head["seq"], head["hash"]) == (3, records[2]["hash"])
    assert verify_log(folder).to_dict() == {"ok": True, "records": 3, "problem": None, "at": None}
    assert log.listing()["verification"] == {"ok": True, "records": 3, "problem": None, "at": None}
    # A new process continues the same chain.
    again = AuditLog(folder)
    assert again.append("lock", profile="p", kind="write", reason="expired") == 4
    assert verify_log(folder).ok


def test_an_empty_folder_is_a_valid_empty_log(tmp_path: Path) -> None:
    assert verify_log(tmp_path / "missing").to_dict() == {"ok": True, "records": 0, "problem": None, "at": None}


def _edit_value(folder: Path) -> None:
    lines = _lines(folder)
    doc = json.loads(lines[2])
    doc["fields"]["reason"] = "expired"  # a changed field, the hash left as it was
    lines[2] = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
    _put(folder, lines)


def _edit_and_rehash(folder: Path) -> None:
    lines = _lines(folder)
    doc = json.loads(lines[2])
    doc["fields"]["profile"] = "someone-else"
    body = {k: v for k, v in doc.items() if k != "hash"}
    doc["hash"] = audit_module._digest(body)  # the record itself is consistent again; the next one is not
    lines[2] = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
    _put(folder, lines)


def _delete_middle(folder: Path) -> None:
    lines = _lines(folder)
    del lines[2]
    _put(folder, lines)


def _reorder(folder: Path) -> None:
    lines = _lines(folder)
    lines[1], lines[2] = lines[2], lines[1]
    _put(folder, lines)


def _truncate(folder: Path) -> None:
    _put(folder, _lines(folder)[:-2])


def _truncate_everything(folder: Path) -> None:
    (folder / CURRENT).unlink()


def _cut_line(folder: Path) -> None:
    data = (folder / CURRENT).read_bytes()
    (folder / CURRENT).write_bytes(data[:-10])


def _remove_head(folder: Path) -> None:
    (folder / HEAD).unlink()


def _insert(folder: Path) -> None:
    lines = _lines(folder)
    lines.insert(1, lines[1])
    _put(folder, lines)


@pytest.mark.parametrize(
    ("tamper", "problem", "at"),
    [
        (_edit_value, "audit.record_changed", 3),
        (_edit_and_rehash, "audit.chain_broken", 4),
        (_delete_middle, "audit.chain_broken", 3),
        (_reorder, "audit.chain_broken", 2),
        (_insert, "audit.chain_broken", 3),
        (_truncate, "audit.truncated", 3),
        (_truncate_everything, "audit.truncated", 0),
        (_cut_line, "audit.unreadable", 5),
        (_remove_head, "audit.head_missing", None),
    ],
    ids=["edit", "edit-rehash", "delete", "reorder", "insert", "truncate", "truncate-all", "cut-line", "no-head"],
)
def test_verification_finds_every_kind_of_tampering(tmp_path: Path, tamper: Any, problem: str, at: int | None) -> None:
    folder = tmp_path / "audit"
    _log_with(folder, 5)
    assert verify_log(folder).ok
    tamper(folder)
    result = verify_log(folder)
    assert (result.ok, result.problem, result.at) == (False, problem, at)
    # Fail closed: a Studio that opens this log writes nothing more to it, and refuses what needs a record.
    before = _every_byte(folder)
    log = AuditLog(folder)
    assert not log.available
    with pytest.raises(AuditError):
        log.append("run.start", run="r1", area="data_editor", action="data_editor.changeset")
    assert log.record("lock", profile="p", kind="write", reason="locked") is False
    assert log.status()["pending"] == 1
    assert _every_byte(folder, without=PENDING) == before  # the damaged log is kept as it is, for inspection
    assert log.listing()["verification"]["ok"] is False


def test_tampering_seen_by_a_listing_stops_further_records(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    log = _log_with(folder, 4)
    _edit_value(folder)
    listing = log.listing()
    assert listing["verification"] == {"ok": False, "records": 2, "problem": "audit.record_changed", "at": 3}
    assert listing["available"] is False
    with pytest.raises(AuditError):
        log.append("lock", profile="p", kind="write", reason="locked")


def test_a_lagging_head_after_a_stop_between_record_and_head_is_accepted_once(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    _log_with(folder, 3)
    records = _records(folder)
    (folder / HEAD).write_bytes(json.dumps({"v": 1, "seq": 2, "hash": records[1]["hash"]}).encode())
    assert verify_log(folder).ok
    (folder / HEAD).write_bytes(json.dumps({"v": 1, "seq": 1, "hash": records[0]["hash"]}).encode())
    assert verify_log(folder).problem == "audit.head_mismatch"
    (folder / HEAD).write_bytes(json.dumps({"v": 1, "seq": 3, "hash": "0" * 64}).encode())
    assert verify_log(folder).problem == "audit.head_mismatch"


def test_a_failed_head_write_keeps_the_record_and_stops_further_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "audit"
    log = _log_with(folder, 2)

    def refuse(path: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(audit_module, "write_private_file", refuse)
    assert log.append("lock", profile="p", kind="write", reason="locked") == 3
    assert not log.available
    with pytest.raises(AuditError):
        log.append("lock", profile="p", kind="write", reason="locked")
    monkeypatch.undo()
    assert verify_log(folder).to_dict() == {"ok": True, "records": 3, "problem": None, "at": None}


# --- files, links, rotation ------------------------------------------------------------------------------------------


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
def test_the_folder_is_0700_and_every_file_0600(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    log = _log_with(folder, 2)
    log.record("lock", profile="p", kind="write", reason="locked")
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    for path in folder.iterdir():
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path.name


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
def test_a_file_readable_by_others_is_refused_not_repaired(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    _log_with(folder, 2)
    (folder / CURRENT).chmod(0o644)
    assert verify_log(folder).problem == "audit.unsafe_file"
    log = AuditLog(folder)
    assert not log.available
    assert stat.S_IMODE(os.stat(folder / CURRENT).st_mode) == 0o644


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
def test_a_folder_others_may_write_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    folder.mkdir(mode=0o700)
    folder.chmod(0o777)
    log = AuditLog(folder)
    assert not log.available
    assert not (folder / CURRENT).exists()


def test_the_log_is_never_followed_through_a_link(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    _log_with(folder, 2)
    elsewhere = tmp_path / "elsewhere.jsonl"
    os.replace(folder / CURRENT, elsewhere)
    try:
        (folder / CURRENT).symlink_to(elsewhere)
    except OSError:
        pytest.skip("no symlinks here")
    assert verify_log(folder).problem == "audit.unsafe_file"
    before = elsewhere.read_bytes()
    log = AuditLog(folder)
    with pytest.raises(AuditError):
        log.append("lock", profile="p", kind="write", reason="locked")
    assert elsewhere.read_bytes() == before


def test_an_unusable_or_missing_folder_refuses_every_record(tmp_path: Path) -> None:
    log = AuditLog(None)
    assert not log.available
    with pytest.raises(AuditError):
        log.append("unlock", profile="p", kind="write", result="ok")
    file = tmp_path / "a-file"
    file.write_text("x")
    assert not AuditLog(file).available


def test_rotation_keeps_the_chain_across_segments_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_module, "MAX_SEGMENT_BYTES", 600)
    folder = tmp_path / "audit"
    log = _log_with(folder, 12)
    segments = sorted(p.name for p in folder.glob("audit-*.jsonl"))
    assert len(segments) >= 2
    assert segments[0] == "audit-000000000001.jsonl"
    assert verify_log(folder).to_dict() == {"ok": True, "records": 12, "problem": None, "at": None}
    assert [r["seq"] for r in log.listing(limit=3)["records"]] == [12, 11, 10]
    # The next rotation's name, planted by someone else: Studio refuses instead of overwriting it.
    current_first = _records(folder)[-1]["seq"] - len(_lines(folder)) + 1
    planted = folder / f"audit-{current_first:012d}.jsonl"
    planted.write_bytes(b"planted\n")
    planted.chmod(0o600)
    with pytest.raises(AuditError):
        for _ in range(20):
            log.append("lock", profile="p", kind="write", reason="locked")
    assert planted.read_bytes() == b"planted\n"
    assert not log.available


def test_a_removed_or_planted_segment_breaks_the_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_module, "MAX_SEGMENT_BYTES", 600)
    folder = tmp_path / "audit"
    _log_with(folder, 12)
    first = folder / "audit-000000000001.jsonl"
    data = first.read_bytes()
    first.unlink()
    assert verify_log(folder).problem == "audit.chain_broken"
    first.write_bytes(data)
    first.chmod(0o600)
    assert verify_log(folder).ok
    stray = folder / "audit-1.jsonl"
    stray.write_bytes(b"")
    assert verify_log(folder).problem == "audit.unexpected_file"


# --- fields: never a value -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"params": {"material": CANARY_VALUE}},
        {"statement": f"UPDATE parts SET material = '{CANARY_VALUE}'"},  # noqa: S608 - a canary, never run
        {"value": CANARY_VALUE},
        {"rows": {"updated": CANARY_VALUE}},
        {"rows": [CANARY_VALUE]},
        {"plan_hash": f"DROP TABLE {CANARY_VALUE}"},
        {"result": f"failed: {CANARY_VALUE}"},
        {"objects": CANARY_VALUE},
        {"kind": "write with spaces"},
        {"findings": -1},
        {"late": "yes"},
    ],
)
def test_a_field_that_is_not_a_name_count_code_or_hash_is_refused(tmp_path: Path, fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        clean_fields(fields)
    log = AuditLog(tmp_path / "audit")
    with pytest.raises(ValueError):
        log.append("run.start", **fields)
    assert not (tmp_path / "audit" / CURRENT).exists()


def test_a_name_that_carries_a_known_secret_is_redacted(tmp_path: Path) -> None:
    register_secret(CANARY_SECRET, lasting=True)
    try:
        assert clean_fields({"objects": [f"main.{CANARY_SECRET}", "main.parts"]}) == {
            "objects": ["[redacted]", "main.parts"]
        }
        assert clean_fields({"profile": "a\nb"}) == {"profile": "a\\x0ab"}
    finally:
        forget_secrets()


# --- the write foundation: what is recorded ---------------------------------------------------------------------------


@needs_defensive
def test_unlock_lock_and_profile_actions_are_recorded_without_secrets(env: WriteEnv) -> None:
    secret = env.send(
        "PUT", "/api/write/profiles/write:parts-write/secret", {"secret": CANARY_SECRET, "store": "session"}
    )
    assert secret.status_code == 204
    connection_id = _unlocked(env)
    wrong = env.unlock(connection_id, typed=CANARY_VALUE)
    assert wrong.json()["code"] == "studio.unlock_name_mismatch"
    assert env.send("DELETE", "/api/write/unlocks", {"connection_id": connection_id}).status_code == 204
    assert env.send("DELETE", "/api/write/profiles/write:parts-write/secret", {}).status_code == 204
    removed = env.send("DELETE", "/api/write/profiles", {"id": "write:parts-write", "digest": env.user_digest()})
    assert removed.status_code == 200 and removed.json()["audit_pending"] is False
    records = _records(env.audit_dir)
    events = [r["event"] for r in records]
    assert events[:2] == ["profile.create", "secret.store"]
    assert events.count("unlock") == 2 and "lock" in events
    assert "secret.remove" in events and events[-1] == "profile.delete"
    by_event = {r["event"]: r["fields"] for r in records}
    assert by_event["profile.create"] == {
        "profile": "parts-write",
        "kind": "write",
        "read_profile": "parts",  # the coupling
        "environment": "dev",
        "result": "ok",
    }
    assert by_event["secret.store"] == {"profile": "parts-write", "kind": "write", "store": "session", "result": "ok"}
    unlocks = [r["fields"] for r in records if r["event"] == "unlock"]
    assert unlocks[0]["result"] == "ok" and unlocks[0]["connection"] == connection_id
    assert unlocks[0]["environment"] == "dev" and unlocks[0]["engine"] == "sqlite"
    assert unlocks[1] == {"profile": "parts-write", "kind": "write", "result": "studio.unlock_name_mismatch"}
    assert by_event["lock"]["reason"] == "locked"
    text = _every_byte(env.audit_dir).decode()
    assert CANARY_SECRET not in text and CANARY_VALUE not in text
    assert verify_log(env.audit_dir).ok


def test_a_profile_change_records_the_changed_keys_and_the_coupling(env: WriteEnv) -> None:
    env.create_read(name="parts-two")
    response = env.send(
        "PUT",
        "/api/write/profiles",
        {
            "id": "write:parts-write",
            "profile": {"name": "parts-write", "kind": "write", "read_profile": "parts-two"},
            "digest": env.user_digest(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["audit_pending"] is False
    change = [r for r in _records(env.audit_dir) if r["event"] == "profile.change"][-1]["fields"]
    assert change["changed"] == ["read_profile"] and change["read_profile"] == "parts-two"


@needs_defensive
def test_expiry_and_a_closed_connection_are_recorded_as_locks(env: WriteEnv) -> None:
    clock = [1000.0]
    env.unlocks.clock = lambda: clock[0]
    connection_id = _unlocked(env)
    clock[0] += 16 * 60
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    second = _unlocked(env)
    assert env.send("DELETE", "/api/connections", {"connection_id": second}).status_code == 204
    reasons = [r["fields"]["reason"] for r in _records(env.audit_dir) if r["event"] == "lock"]
    assert reasons == ["expired", "connection_closed"]
    assert connection_id


# --- write runs: before the commit, fail closed, pending marker -------------------------------------------------------


@needs_defensive
def test_a_run_is_recorded_before_it_begins_and_after_it_committed(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    seen_before_run: list[str] = []
    with env.unlocks.audited_transaction(
        env.session(),
        connection_id,
        Area.DATA_EDITOR,
        action="data_editor.changeset",
        objects=["main.parts"],
        plan_hash=PLAN,
    ) as run:
        seen_before_run = _events(env)
        assert run.transaction is not None
        run.count("updated", run.transaction.execute("UPDATE parts SET material = :m WHERE id = 1", {"m": "Iron"}))
    assert seen_before_run[-1] == "run.start"
    assert run.audit_pending is False
    start, result = _records(env.audit_dir)[-2:]
    assert start["event"] == "run.start" and result["event"] == "run.result"
    assert start["fields"] == {
        "run": run.run_id,
        "area": "data_editor",
        "action": "data_editor.changeset",
        "profile": "parts-write",
        "kind": "write",
        "environment": "dev",
        "engine": "sqlite",
        "connection": connection_id,
        "objects": ["main.parts"],
        "plan_hash": PLAN,
    }
    assert result["fields"] == {
        "run": run.run_id,
        "area": "data_editor",
        "action": "data_editor.changeset",
        "profile": "parts-write",
        "result": "committed",
        "rows": {"updated": 1},
    }


@needs_defensive
def test_a_failed_run_is_recorded_with_its_code_and_rolled_back(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    before = digest(env.db)
    with (
        pytest.raises(WriteError),
        env.unlocks.audited_transaction(
            env.session(), connection_id, Area.DATA_EDITOR, action="data_editor.changeset"
        ) as run,
    ):
        assert run.transaction is not None
        run.transaction.execute("UPDATE parts SET material = 'Gold' WHERE id = 1")
        raise WriteError("studio.write_target_changed")
    assert digest(env.db) == before
    assert _records(env.audit_dir)[-1]["fields"]["result"] == "studio.write_target_changed"


@needs_defensive
def test_no_start_record_no_run_and_nothing_changes(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: the run.start record cannot be written, so the transaction never begins."""
    connection_id = _unlocked(env)
    before_db, before_log = digest(env.db), _every_byte(env.audit_dir)
    began: list[bool] = []

    def refuse(path: Path, data: bytes) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(audit_module, "_append_bytes", refuse)
    with (
        pytest.raises(WriteError) as caught,
        env.unlocks.audited_transaction(
            env.session(), connection_id, Area.DATA_EDITOR, action="data_editor.changeset"
        ) as run,
    ):
        began.append(True)
        assert run.transaction is not None
        run.transaction.execute("UPDATE parts SET material = 'Gold' WHERE id = 1")
    assert caught.value.code == "studio.audit_unavailable"
    assert began == []
    assert digest(env.db) == before_db
    monkeypatch.undo()
    assert _every_byte(env.audit_dir) == before_log
    assert verify_log(env.audit_dir).ok


@needs_defensive
def test_a_failed_record_after_the_commit_keeps_a_pending_marker_and_warns(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, logs: LogSetup
) -> None:
    """N-4: the write has happened; the result record is kept as pending, reported, and written later."""
    connection_id = _unlocked(env)
    real_append = audit_module._append_bytes

    with env.unlocks.audited_transaction(
        env.session(), connection_id, Area.DATA_EDITOR, action="data_editor.changeset", objects=["main.parts"]
    ) as run:
        assert run.transaction is not None
        changed = run.transaction.execute("UPDATE parts SET material = :m WHERE id = 1", {"m": CANARY_VALUE})
        run.count("updated", changed)

        def refuse(path: Path, data: bytes) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(audit_module, "_append_bytes", refuse)
    assert run.audit_pending is True
    assert _read_material(env) == CANARY_VALUE  # committed
    assert _events(env)[-1] == "run.start"
    marker = json.loads((env.audit_dir / PENDING).read_bytes())
    assert [item["event"] for item in marker["pending"]] == ["run.result"]
    assert marker["pending"][0]["fields"]["result"] == "committed"
    status = env.send("GET", "/api/write/audit").json()
    assert status["pending"] == 1 and status["verification"]["ok"] is True
    assert stat.S_IMODE(os.stat(env.audit_dir / PENDING).st_mode) == 0o600 or not POSIX
    # A new write is refused while the pending record cannot be written (no start record, no run).
    with (
        pytest.raises(WriteError) as caught,
        env.unlocks.audited_transaction(env.session(), connection_id, Area.DATA_EDITOR, action="data_editor.changeset"),
    ):
        pytest.fail("the run must not begin")
    assert caught.value.code == "studio.audit_unavailable"
    # The log is writable again: the next record writes the pending one first, marked late.
    monkeypatch.setattr(audit_module, "_append_bytes", real_append)
    assert env.send("DELETE", "/api/write/unlocks", {"connection_id": connection_id}).status_code == 204
    records = _records(env.audit_dir)
    assert [r["event"] for r in records[-2:]] == ["run.result", "lock"]
    assert records[-2]["fields"]["late"] is True and records[-2]["fields"]["run"] == run.run_id
    assert "occurred" in records[-2]["fields"]
    assert not (env.audit_dir / PENDING).exists()
    assert env.send("GET", "/api/write/audit").json()["pending"] == 0
    assert verify_log(env.audit_dir).ok
    assert CANARY_VALUE.encode() not in _every_byte(env.audit_dir)
    assert logs.file is not None
    assert "studio.audit_pending" in logs.file.read_text(encoding="utf-8")


def _read_material(env: WriteEnv) -> str:
    import sqlite3

    connection = sqlite3.connect(f"file:{env.db}?mode=ro", uri=True)
    try:
        value: str = connection.execute("SELECT material FROM parts WHERE id = 1").fetchone()[0]
        return value
    finally:
        connection.close()


def test_a_pending_marker_survives_a_restart_and_is_written_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "audit"
    log = _log_with(folder, 2)

    def refuse(path: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(audit_module, "_append_bytes", refuse)
    assert log.record("run.result", run="r1", area="data_editor", action="a", result="committed") is False
    monkeypatch.undo()
    assert (folder / PENDING).exists()
    again = AuditLog(folder)  # a new process
    assert again.status()["pending"] == 0
    assert not (folder / PENDING).exists()
    last = _records(folder)[-1]
    assert last["event"] == "run.result" and last["fields"]["late"] is True
    assert verify_log(folder).ok


def test_a_pending_marker_that_cannot_be_saved_stays_in_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "audit"
    log = _log_with(folder, 1)

    def refuse(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(audit_module, "_append_bytes", refuse)
    monkeypatch.setattr(audit_module, "write_private_file", refuse)
    assert log.record("lock", profile="p", kind="write", reason="locked") is False
    assert log.status()["pending"] == 1
    assert not (folder / PENDING).exists()


def test_a_damaged_pending_marker_stops_the_log(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    _log_with(folder, 1)
    (folder / PENDING).write_bytes(json.dumps({"pending": [{"event": "run.result", "fields": {"value": 1}}]}).encode())
    (folder / PENDING).chmod(0o600)
    log = AuditLog(folder)
    assert log.status()["problem"] == "audit.pending_unreadable"


@needs_defensive
def test_no_unlock_record_no_unlock(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    connection_id = env.open()

    def refuse(path: Path, data: bytes) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(audit_module, "_append_bytes", refuse)
    response = env.unlock(connection_id)
    assert (response.status_code, response.json()["code"]) == (405, "studio.audit_unavailable")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    monkeypatch.undo()
    assert env.unlock(connection_id).status_code == 200


def test_a_damaged_log_refuses_profile_changes_first(env: WriteEnv) -> None:
    _edit_value_of_first(env.audit_dir)
    listing = env.send("GET", "/api/write/audit").json()
    assert listing["verification"]["ok"] is False and listing["available"] is False
    before = (env.config_dir / "profiles.toml").read_bytes()
    created = env.create_write(name="another")
    assert (created.status_code, created.json()["code"]) == (405, "studio.audit_unavailable")
    assert (env.config_dir / "profiles.toml").read_bytes() == before


def _edit_value_of_first(folder: Path) -> None:
    lines = _lines(folder)
    doc = json.loads(lines[0])
    doc["fields"]["profile"] = "x"
    lines[0] = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
    _put(folder, lines)


def test_without_an_audit_folder_nothing_is_unlocked(env: WriteEnv) -> None:
    env.unlocks.audit = AuditLog(None)
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert (response.status_code, response.json()["code"]) == (405, "studio.audit_unavailable")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []


# --- canary across the API -----------------------------------------------------------------------------------------


@needs_defensive
def test_canary_no_value_or_secret_reaches_the_log_or_its_listing(env: WriteEnv) -> None:
    register_secret(CANARY_SECRET, lasting=True)
    env.send("PUT", "/api/write/profiles/write:parts-write/secret", {"secret": CANARY_SECRET, "store": "session"})
    connection_id = _unlocked(env)
    env.unlock(connection_id, write_id=f"write:{CANARY_VALUE}", typed=CANARY_SECRET)
    env.unlock(connection_id, typed=CANARY_SECRET)
    with env.unlocks.audited_transaction(
        env.session(),
        connection_id,
        Area.DATA_EDITOR,
        action="data_editor.changeset",
        objects=["main.parts", f"main.{CANARY_SECRET}"],
    ) as run:
        assert run.transaction is not None
        run.count(
            "updated",
            run.transaction.execute(
                "UPDATE parts SET material = :value WHERE id = 1 AND :secret IS NOT NULL",
                {"value": CANARY_VALUE, "secret": CANARY_SECRET},
            ),
        )
    with (
        pytest.raises(WriteError),
        env.unlocks.audited_transaction(
            env.session(), connection_id, Area.DATA_EDITOR, action="data_editor.changeset"
        ) as failing,
    ):
        assert failing.transaction is not None
        failing.transaction.execute(f"UPDATE parts SET material = '{CANARY_VALUE}'")  # noqa: S608 - the canary
        raise WriteError("studio.write_target_changed")
    stored = _every_byte(env.audit_dir).decode()
    listing = env.send("GET", "/api/write/audit?limit=1000")
    assert listing.status_code == 200
    for text in (stored, listing.text):
        assert CANARY_VALUE not in text and CANARY_SECRET not in text
        assert "UPDATE" not in text and "material" not in text
    assert "[redacted]" in stored


# --- the read-only route ---------------------------------------------------------------------------------------------


@needs_defensive
def test_the_listing_is_newest_first_verified_and_read_only(env: WriteEnv) -> None:
    _unlocked(env)
    before = _every_byte(env.audit_dir)
    response = env.send("GET", "/api/write/audit?limit=2")
    assert response.status_code == 200
    doc = response.json()
    assert doc["available"] is True and doc["pending"] == 0 and doc["problem"] is None
    assert doc["verification"] == {"ok": True, "records": 2, "problem": None, "at": None}
    assert [r["event"] for r in doc["records"]] == ["unlock", "profile.create"]
    assert set(doc["records"][0]) == {"seq", "time", "event", "fields"}
    assert _every_byte(env.audit_dir) == before


@pytest.mark.parametrize("query", ["?limit=0", "?limit=1001", "?limit=x", "?limit=-1", "?limit=2&delete=1", "?seq=1"])
def test_the_listing_refuses_a_bad_query(env: WriteEnv, query: str) -> None:
    response = env.send("GET", f"/api/write/audit{query}")
    assert (response.status_code, response.json()["code"]) == (400, "access.bad_request")


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_no_route_changes_the_log(env: WriteEnv, method: str) -> None:
    before = _every_byte(env.audit_dir)
    response = env.send(method, "/api/write/audit", {"seq": 1})
    assert response.status_code == 405
    assert _every_byte(env.audit_dir) == before


def test_the_listing_is_open_with_the_database_manager_flag_alone(tmp_path: Path, static_dir: Path) -> None:
    env = WriteEnv(tmp_path, static_dir, DATABASE_MANAGER)
    try:
        response = env.send("GET", "/api/write/audit")
        assert response.status_code == 200
        assert response.json()["verification"]["ok"] is True
    finally:
        env.close()
