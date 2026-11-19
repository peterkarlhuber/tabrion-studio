"""G-S2 decision 3A (before G-R): the audit log's anchor in the OS keyring (residual risk 3 of the write threat
model; N-1 of the PR #114 review).

* The last ``(seq, hash)`` of the chain is kept in the keyring after every record, one entry per audit folder.
* A log that no longer reaches the anchor (the folder removed or moved aside, the log cut, or a consistent chain
  rebuilt with other records) is reported: an ``audit.reset`` record starts what follows, the status says ``reset``,
  every listing counts the reset records, and writes go on (the reset is itself in the chain).
* Without a usable keyring, or when it fails, the log works as before and the status says so; nothing secret is kept.
* Through the routes: the anchor lives in Studio's keyring (a fake here) and ``GET /api/write/audit`` reports it.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import pytest

from tabrion_studio.config.secrets import SERVICE, KeyringStore
from tabrion_studio.write.audit import CURRENT, HEAD, AuditAnchor, AuditLog, verify_log
from tabrion_studio.write.connection import defensive_available
from tests.support.fake_keyring import FakeKeyring
from tests.support.write import WriteEnv

pytestmark = pytest.mark.security
needs_defensive = pytest.mark.skipif(not defensive_available(), reason="Python 3.11 refuses SQLite write connections")


def _store() -> tuple[FakeKeyring, KeyringStore]:
    fake = FakeKeyring()
    return fake, KeyringStore(lambda: fake)


def _log(folder: Path, store: KeyringStore, count: int = 0) -> AuditLog:
    log = AuditLog(folder, anchor=AuditAnchor(store, folder))
    for index in range(count):
        log.append("lock", profile=f"p{index}", kind="write", reason="locked")
    return log


def _records(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (folder / CURRENT).read_bytes().split(b"\n")[:-1]]


def _anchor(fake: FakeKeyring, folder: Path) -> str | None:
    return fake.values.get((SERVICE, AuditAnchor(None, folder).key))


def test_the_anchor_follows_every_record(tmp_path: Path) -> None:
    fake, store = _store()
    folder = tmp_path / "audit"
    log = _log(folder, store, 3)
    last = _records(folder)[-1]
    assert _anchor(fake, folder) == f"1:3:{last['hash']}"
    assert log.status()["anchor"] == "ok"
    log.append("lock", profile="p", kind="write", reason="locked")
    assert _anchor(fake, folder) == f"1:4:{_records(folder)[-1]['hash']}"
    # One entry per folder; nothing else in the keyring, and nothing secret in it.
    assert [key for _service, key in fake.values] == [AuditAnchor(None, folder).key]
    other = tmp_path / "other" / "audit"
    _log(other, store, 1)
    assert len(fake.values) == 2


def test_an_unchanged_log_opens_without_a_reset(tmp_path: Path) -> None:
    _fake, store = _store()
    folder = tmp_path / "audit"
    _log(folder, store, 3)
    again = _log(folder, store)
    assert again.status()["anchor"] == "ok" and again.available
    assert [r["event"] for r in _records(folder)] == ["lock"] * 3
    assert again.listing()["resets"] == 0


@pytest.mark.parametrize("how", ["removed", "moved_aside", "cut", "rebuilt"])
def test_a_log_that_no_longer_reaches_the_anchor_is_reported(tmp_path: Path, how: str) -> None:
    fake, store = _store()
    folder = tmp_path / "audit"
    _log(folder, store, 4)
    anchored = _anchor(fake, folder)
    assert anchored is not None
    if how == "removed":
        shutil.rmtree(folder)
    elif how == "moved_aside":
        folder.rename(tmp_path / "audit-old")
    elif how == "cut":
        # A shorter chain that verifies on its own: two records and a matching head.
        records = _records(folder)[:2]
        (folder / CURRENT).write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in records))
        (folder / HEAD).write_bytes(json.dumps({"v": 1, "seq": 2, "hash": records[-1]["hash"]}).encode())
    else:
        # Someone who can write the folder rebuilds a whole consistent chain with other records.
        shutil.rmtree(folder)
        _log(folder, KeyringStore(lambda: FakeKeyring()), 5)
    found = len(_records(folder)) if (folder / CURRENT).exists() else 0
    log = _log(folder, store)
    assert log.available, log.status()
    assert log.status()["anchor"] == "reset"
    reset = _records(folder)[-1]
    assert reset["event"] == "audit.reset"
    assert reset["fields"] == {"anchor_seq": 4, "anchor_hash": anchored.split(":")[2], "found": found}
    assert verify_log(folder).ok
    # Writes go on, the anchor moves to the new chain, and every listing counts the reset.
    log.append("lock", profile="p", kind="write", reason="locked")
    assert _anchor(fake, folder) == f"1:{found + 2}:{_records(folder)[-1]['hash']}"
    assert log.listing()["resets"] == 1
    again = _log(folder, store)
    assert again.status()["anchor"] == "ok"
    assert again.listing()["resets"] == 1


def test_an_anchor_that_is_not_one_reads_as_a_reset(tmp_path: Path) -> None:
    fake, store = _store()
    folder = tmp_path / "audit"
    _log(folder, store, 2)
    fake.values[(SERVICE, AuditAnchor(None, folder).key)] = "garbage"
    log = _log(folder, store)
    assert log.status()["anchor"] == "reset"
    assert _records(folder)[-1]["event"] == "audit.reset"


def test_without_a_keyring_the_log_works_and_says_so(tmp_path: Path) -> None:
    folder = tmp_path / "audit"
    log = _log(folder, KeyringStore(lambda: None), 2)
    assert log.available
    assert log.status()["anchor"] == "unavailable"
    assert verify_log(folder).ok
    plain = AuditLog(tmp_path / "plain")
    assert plain.status()["anchor"] == "none"


def test_a_failing_keyring_never_stops_the_log(tmp_path: Path) -> None:
    fake, store = _store()
    folder = tmp_path / "audit"
    log = _log(folder, store, 1)
    fake.fail = True
    log.append("lock", profile="p", kind="write", reason="locked")
    assert log.available and log.status()["anchor"] == "failed"
    assert verify_log(folder).ok
    # A keyring that fails at the start: the anchor is unavailable, the log works.
    again = _log(folder, store, 1)
    assert again.available and again.status()["anchor"] == "unavailable"
    assert "secret-in-message" not in json.dumps(again.status())


def test_a_lagging_anchor_still_vouches_for_the_log(tmp_path: Path) -> None:
    """The anchor named an earlier record (a keyring write failed later): the chain still reaches it, no reset."""
    fake, store = _store()
    folder = tmp_path / "audit"
    log = _log(folder, store, 2)
    fake.fail = True
    log.append("lock", profile="p", kind="write", reason="locked")
    fake.fail = False
    again = _log(folder, store)
    assert again.status()["anchor"] == "ok"
    assert _anchor(fake, folder) == f"1:3:{_records(folder)[-1]['hash']}"


@needs_defensive
def test_the_route_reports_the_anchor_and_the_resets(
    tmp_path: Path, static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Studio's own keyring keeps the anchor; a start whose audit folder no longer reaches it reports the reset."""
    from tabrion_studio.config.write_areas import Area, WriteAreas
    from tests.support import write as support

    areas = WriteAreas(frozenset({Area.DATA_EDITOR}))
    env = WriteEnv(tmp_path / "first", static_dir, areas)
    try:
        env.create_read()
        assert env.create_write().status_code == 201
        doc = env.send("GET", "/api/write/audit").json()
        assert doc["anchor"] == "ok" and doc["resets"] == 0
        anchored = env.fake.values[(SERVICE, AuditAnchor(None, env.audit_dir).key)]
    finally:
        env.close()
    # A second start on another configuration folder whose keyring already holds an anchor for it (its log was
    # removed): the start records the reset and the listing reports it.
    folder = tmp_path / "second"
    seeded = FakeKeyring()
    seeded.values[(SERVICE, AuditAnchor(None, folder / "config" / "audit").key)] = anchored
    monkeypatch.setattr(support, "FakeKeyring", lambda: seeded)
    again = WriteEnv(folder, static_dir, areas)
    try:
        assert again.audit_dir == folder / "config" / "audit"
        doc = again.send("GET", "/api/write/audit").json()
    finally:
        again.close()
    assert doc["available"] is True
    assert doc["anchor"] == "reset" and doc["resets"] == 1
    assert doc["records"][0]["event"] == "audit.reset"
