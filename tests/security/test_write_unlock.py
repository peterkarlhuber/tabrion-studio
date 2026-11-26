"""STU-XW-1: unlocking and the SQLite write connection (TX-03, TX-04, TX-05, TX-10, TX-14, TX-16; K-2; N-3).

Every test here runs with a flag on ("flag on" path of the kill-switch proof): the unlock is server-side, bound to
the session, the connection and the stored profiles, expires after 15 minutes, ends on reconnect, profile edits and
environment changes, and refuses PROD from the stored profile. The SQLite write connection keeps
``trusted_schema = OFF``, defensive mode and Studio's write authorizer, and opens only the read connection's file.
Read paths never get the write connection. Nothing in this module writes to a database except the tests of the
write connection's own limits, which use its ``transaction()`` hook directly.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import pytest

from tabrion_studio.config.schema import Profile, Settings
from tabrion_studio.config.write_areas import Area
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.write.audit import CURRENT
from tabrion_studio.write.connection import (
    WriteConnection,
    WriteError,
    defensive_available,
    open_write_connection,
    write_authorizer,
)
from tabrion_studio.write.unlock import UNLOCK_IDLE_SECONDS
from tests.support.access import ORIGIN
from tests.support.write import CANARY_SECRET, CANARY_VALUE, DATA_EDITOR, WriteEnv, digest, make_database

pytestmark = pytest.mark.security
DEFENSIVE = defensive_available()
needs_defensive = pytest.mark.skipif(not DEFENSIVE, reason="Python 3.11: see the refusal test below")


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
    try:
        yield environment
    finally:
        environment.close()


def _read_only(path: Path, sql: str) -> Any:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchone()[0]
    finally:
        connection.close()


def _handles(path: Path) -> int:
    """Linux: how many descriptors of this process have ``path`` open (the condition under which Windows refuses to
    rename or replace a file)."""
    target = os.path.realpath(path)
    count = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            count += os.readlink(f"/proc/self/fd/{fd}") == target
        except OSError:
            continue
    return count


def _release(env: WriteEnv, connection_id: str, *writes: WriteConnection) -> None:
    """Close Studio's handles on the database file, as between a preview and a run when the pools are idle (the
    next use reopens them): the window in which an attacker can swap the file. While a handle is open, Windows
    refuses to move the file at all (asserted there); on Linux the open handles are counted instead, so the
    Windows condition is checked on every run."""
    read = env.registry.get(connection_id)
    if sys.platform == "win32":
        with pytest.raises(PermissionError):
            os.replace(env.db, env.tmp_path / "refused.db")
    elif sys.platform == "linux":
        assert _handles(env.db) >= 1
    read._db.engine.dispose()
    for write in writes:
        write._db.engine.dispose()
    if sys.platform == "linux":
        assert _handles(env.db) == 0


def _unlocked(env: WriteEnv) -> str:
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert response.status_code == 200, response.text
    return connection_id


def test_python_311_refuses_a_sqlite_write_connection(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``setconfig`` (Python 3.11) defensive mode cannot be set: Studio refuses instead (fail closed)."""
    from tabrion_studio.write import connection as module

    monkeypatch.setattr(module, "_DEFENSIVE", None)
    connection_id = env.open()
    before = digest(env.db)
    response = env.unlock(connection_id)
    assert (response.status_code, response.json()["code"]) == (400, "studio.write_unsupported")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    assert digest(env.db) == before


@needs_defensive
def test_unlocking_shows_the_write_state_and_changes_nothing(env: WriteEnv) -> None:
    before = digest(env.db)
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["write_profile_id"] == "write:parts-write" and state["kind"] == "write"
    assert state["environment"] == "dev" and state["engine"] == "sqlite" and state["role"] is None
    assert state["expires_in_s"] == UNLOCK_IDLE_SECONDS and state["findings"] == []
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == [state]
    assert env.send("DELETE", "/api/write/unlocks", {"connection_id": connection_id}).status_code == 204
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    # Unlocking opened a write connection and changed nothing: the bytes and the journal mode are as they were.
    assert digest(env.db) == before
    assert _read_only(env.db, "PRAGMA journal_mode") == "delete"


def _audit_records(folder: Path) -> list[dict[str, Any]]:
    """The audit log's records (``event`` and ``fields``), oldest first."""
    out: list[dict[str, Any]] = []
    for path in [*sorted(folder.glob("audit-*.jsonl")), folder / CURRENT]:
        if path.exists():
            out += [json.loads(line) for line in path.read_bytes().split(b"\n")[:-1]]
    return [r for r in out if "event" in r]


def _findings(monkeypatch: pytest.MonkeyPatch, findings: list[dict[str, str]]) -> list[WriteConnection]:
    """Make the privilege check report ``findings`` (as on PostgreSQL) and record every connection closed."""
    closed: list[WriteConnection] = []
    real_close = WriteConnection.close

    def check(self: WriteConnection) -> None:
        self.findings = [dict(f) for f in findings]

    def close(self: WriteConnection) -> None:
        closed.append(self)
        real_close(self)

    monkeypatch.setattr(WriteConnection, "check_privileges", check)
    monkeypatch.setattr(WriteConnection, "close", close)
    return closed


@needs_defensive
@pytest.mark.parametrize(
    "code",
    [
        "admin.superuser",
        "admin.bypassrls",
        "admin.replication",
        "admin.broad:pg_read_server_files",
        "admin.broad:pg_write_server_files",
        "admin.broad:pg_execute_server_program",
    ],
)
@pytest.mark.parametrize("via", ["direct", "inherited"])
def test_an_over_privileged_write_role_is_not_unlocked(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, code: str, via: str
) -> None:
    """Owner decision 1A at G-S2: a write role with superuser, BYPASSRLS, REPLICATION or a server-file role refuses
    the unlock with 405 ``studio.write_role_over_privileged``; the write connection is closed, nothing is unlocked,
    the database is unchanged, and the refusal is the existing ``unlock`` record with its code (no new event)."""
    before = digest(env.db)
    closed = _findings(
        monkeypatch,
        [
            {"code": "admin.createrole", "via": via, "severity": "warning"},
            {"code": code, "via": via, "severity": "warning"},
        ],
    )
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert (response.status_code, response.json()["code"]) == (405, "studio.write_role_over_privileged")
    assert "admin." not in response.text  # the answer names no finding and no role
    assert len(closed) == 1
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert caught.value.code == "studio.write_locked"
    assert digest(env.db) == before
    unlocks = [r["fields"] for r in _audit_records(env.audit_dir) if r["event"] == "unlock"]
    assert [u["result"] for u in unlocks] == ["studio.write_role_over_privileged"]


def _journal_mode(database: Path) -> str:
    """The file's journal mode, as the read connection reports it at unlock."""
    with contextlib.closing(sqlite3.connect(database)) as raw:
        return str(raw.execute("PRAGMA journal_mode").fetchone()[0])


@needs_defensive
@pytest.mark.parametrize(
    "code",
    [
        "admin.superuser",
        "admin.bypassrls",
        "admin.replication",
        "admin.broad:pg_read_server_files",
        "admin.broad:pg_write_server_files",
        "admin.broad:pg_execute_server_program",
        "admin.createrole_pre16",
    ],
)
def test_an_over_privileged_admin_role_is_not_unlocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """STU-ADR-0008 section 6, decided at G-S4: an admin role with superuser, BYPASSRLS, REPLICATION, a server-file
    role or CREATEROLE before PostgreSQL 16 is never unlocked (``studio.admin_over_privileged``); the connection is
    closed first and the error names no finding and no role."""
    database = tmp_path / "admin.db"
    make_database(database)
    closed = _findings(monkeypatch, [{"code": code, "via": "inherited", "severity": "warning"}])
    stat = os.stat(database)
    with pytest.raises(WriteError) as caught:
        open_write_connection(
            "admin",
            Profile("a", "sqlite", "dev", path=str(database)),
            Settings(),
            database_file=database,
            journal_mode=_journal_mode(database),
            identity=(stat.st_dev, stat.st_ino),
        )
    assert caught.value.code == "studio.admin_over_privileged"
    assert len(closed) == 1


@needs_defensive
@pytest.mark.parametrize("code", ["admin.createrole", "admin.createdb", "admin.broad:pg_read_all_data"])
def test_other_findings_of_an_admin_role_still_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """The other findings of an admin role stay warnings: CREATEROLE on 16 and later is what role management needs."""
    database = tmp_path / "admin.db"
    make_database(database)
    closed = _findings(monkeypatch, [{"code": code, "via": "direct", "severity": "warning"}])
    stat = os.stat(database)
    connection = open_write_connection(
        "admin",
        Profile("a", "sqlite", "dev", path=str(database)),
        Settings(),
        database_file=database,
        journal_mode=_journal_mode(database),
        identity=(stat.st_dev, stat.st_ino),
    )
    try:
        assert connection.findings == [{"code": code, "via": "direct", "severity": "warning"}]
        assert closed == []
    finally:
        connection.close()


@needs_defensive
@pytest.mark.parametrize(
    "code", ["admin.createrole", "admin.createrole_pre16", "admin.broad:pg_read_all_data", "admin.broad:pg_monitor"]
)
def test_other_findings_of_a_write_role_unlock_with_a_warning(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """Decision 1A: CREATEROLE (also before PostgreSQL 16) and every other predefined role stay warnings."""
    finding = {"code": code, "via": "inherited", "severity": "warning"}
    closed = _findings(monkeypatch, [finding])
    connection_id = env.open()
    response = env.unlock(connection_id)
    assert response.status_code == 200, response.text
    assert response.json()["findings"] == [finding]
    assert closed == []


@needs_defensive
@pytest.mark.parametrize(
    "typed",
    ["Parts-Write", "parts-write ", "", None, 1, "parts", f"{CANARY_VALUE}"],
    # The same test ID in every collection (pytest-xdist compares them across workers), the canary stays random.
    ids=lambda typed: "CANARY_VALUE" if typed == CANARY_VALUE else None,
)
def test_a_wrong_typed_name_does_not_unlock(env: WriteEnv, typed: Any) -> None:
    connection_id = env.open()
    response = env.unlock(connection_id, typed=typed)
    assert (response.status_code, response.json()["code"]) == (400, "studio.unlock_name_mismatch")
    assert CANARY_VALUE not in response.text
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []


@needs_defensive
def test_an_unlock_belongs_to_one_session(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    session = env.session()
    other = {**env.studio.sign_in(), "Origin": ORIGIN}  # a second tab: a new session cookie
    assert env.session() != session
    assert env.send("GET", "/api/write/unlocks", headers=other).json()["unlocks"] == []
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert caught.value.code == "studio.write_locked"
    assert env.unlocks.require(session, connection_id, Area.DATA_EDITOR).read_only is False


@needs_defensive
def test_an_unlock_belongs_to_one_connection_and_its_coupled_profile(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    other_db = env.tmp_path / "other.db"
    make_database(other_db)
    env.create_read(name="other", path=other_db)
    other = env.open("user:other")
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), other, Area.DATA_EDITOR)
    assert caught.value.code == "studio.write_locked"
    refused = env.unlock(other)  # parts-write is coupled to "parts", not to "other"
    assert (refused.status_code, refused.json()["code"]) == (400, "studio.write_coupling_invalid")
    unknown = env.unlock("c_not_open")
    assert (unknown.status_code, unknown.json()["code"]) == (400, "studio.write_target_unknown")
    assert env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR) is not None


@needs_defensive
def test_an_unlock_expires_after_15_minutes_without_use(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    clock: list[float] = env.clock  # type: ignore[attr-defined]
    clock[0] += UNLOCK_IDLE_SECONDS - 1
    env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)  # use postpones the expiry
    clock[0] += UNLOCK_IDLE_SECONDS - 1
    env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    clock[0] += UNLOCK_IDLE_SECONDS + 1
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert caught.value.code == "studio.write_locked"
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []


@needs_defensive
def test_a_disconnect_or_reconnect_ends_the_unlock(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    assert env.send("DELETE", "/api/connections", {"connection_id": connection_id}).status_code == 204
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    again = env.open()  # a reconnect is a new connection: the unlock does not carry over
    assert again != connection_id
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), again, Area.DATA_EDITOR)
    assert caught.value.code == "studio.write_locked"


@needs_defensive
def test_an_area_the_profile_kind_does_not_serve_is_refused(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), connection_id, Area.DATABASE_MANAGER)  # its flag is off
    assert caught.value.code == "studio.method_not_allowed"


@needs_defensive
def test_editing_the_coupled_profile_or_the_write_profile_ends_the_unlock(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    changed = {
        "id": "write:parts-write",
        "profile": {"name": "parts-write", "kind": "write", "read_profile": "parts"},
        "digest": env.user_digest(),
    }
    assert env.send("PUT", "/api/write/profiles", changed).status_code == 200
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    connection_id = _unlocked(env)
    same = {"name": "parts", "engine": "sqlite", "path": str(env.db), "environment": "dev", "statement_timeout_ms": 5}
    assert (
        env.send("PUT", "/api/profiles", {"id": "user:parts", "profile": same, "digest": env.user_digest()})
    ).status_code == 200
    with pytest.raises(WriteError):
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)


@needs_defensive
def test_changing_the_environment_of_a_coupled_profile_ends_every_unlock(env: WriteEnv) -> None:
    """TX-04: dev -> test of one coupled profile ends the unlocks of the other profiles too."""
    other_db = env.tmp_path / "other.db"
    make_database(other_db)
    env.create_read(name="other", path=other_db)
    assert env.create_write(name="other-write", read="other").status_code == 201
    first = _unlocked(env)
    second = env.open("user:other")
    assert env.unlock(second, "write:other-write", "other-write").status_code == 200
    assert len(env.send("GET", "/api/write/unlocks").json()["unlocks"]) == 2
    to_test = {"name": "other", "engine": "sqlite", "path": str(other_db), "environment": "test"}
    assert (
        env.send("PUT", "/api/profiles", {"id": "user:other", "profile": to_test, "digest": env.user_digest()})
    ).status_code == 200
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    for connection_id in (first, second):
        with pytest.raises(WriteError):
            env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)


@needs_defensive
def test_a_hand_edit_of_the_profile_file_ends_the_unlock_on_its_next_use(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    file = env.config_dir / "profiles.toml"
    file.write_text(file.read_text(encoding="utf-8").replace('kind = "write"', 'kind = "admin"'), encoding="utf-8")
    with pytest.raises(WriteError):
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []


# --- PROD (E-X7) ----------------------------------------------------------------------------------------------------


def test_no_write_connection_opens_for_prod() -> None:
    prod = Profile("p", "sqlite", "prod", path="x.db")
    with pytest.raises(WriteError) as caught:
        open_write_connection("write", prod, Settings(), database_file=Path("x.db"), identity=(1, 1))
    assert caught.value.code == "studio.write_prod_locked"


@needs_defensive
def test_unlock_and_use_refuse_prod_from_the_stored_profile(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every entry point re-reads the environment on the server. A file cannot couple to PROD, so the stored read is
    made PROD here directly: the unlock and every use refuse it with the W-9 answer, never a write connection."""
    connection_id = _unlocked(env)
    real = env.unlocks._stored

    def prod_now(name: str) -> Any:
        write, read = real(name)
        return write, Profile(read.name, read.engine, "prod", path=read.path)

    monkeypatch.setattr(env.unlocks, "_stored", prod_now)
    with pytest.raises(WriteError) as caught:
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert caught.value.code == "studio.write_prod_locked"
    refused = env.unlock(env.open())
    assert (refused.status_code, refused.json()["code"]) == (405, "studio.write_prod_locked")
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []


@needs_defensive
def test_a_prod_connection_is_never_unlocked(env: WriteEnv) -> None:
    """A PROD read profile with a hand-written coupling: the file refuses the coupling; unlocking is refused."""
    prod_db = env.tmp_path / "prod.db"
    make_database(prod_db)
    env.create_read(name="prod", environment="prod", path=prod_db)
    file = env.config_dir / "profiles.toml"
    env.write_file(
        file.read_text(encoding="utf-8") + '\n[[write_profile]]\nname = "prod-write"\nkind = "write"\n'
        'read_profile = "prod"\n'
    )
    before = digest(prod_db)
    connection_id = env.open("user:prod", "prod")
    response = env.unlock(connection_id, "write:prod-write", "prod-write")
    assert response.status_code in (400, 405) and response.status_code != 403
    assert response.json()["code"] in ("studio.write_profile_unknown", "studio.write_prod_locked")
    assert digest(prod_db) == before


# --- read paths never get the write connection (TX-03, TX-10, N-3) ------------------------------------------------


@needs_defensive
def test_read_paths_keep_the_read_connection_while_unlocked(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    before = digest(env.db)
    assert env.registry.get(connection_id).read_only is True
    for sql in ("INSERT INTO parts VALUES (2, 'Gold')", "UPDATE parts SET material = 'Gold'", "DELETE FROM parts"):
        answer = env.send("POST", "/api/queries", {"connection_id": connection_id, "sql": sql})
        assert answer.status_code == 400, answer.text
        assert answer.json()["code"].startswith("studio.statement")
    worker = env.registry.worker_target(connection_id)
    assert worker["database_file"] == str(env.registry.get(connection_id).database_file)
    assert digest(env.db) == before


@needs_defensive
def test_the_write_mode_of_a_query_is_403_and_never_reaches_the_history(env: WriteEnv) -> None:
    """W-9 (403 only for the write mode) and N-3: a refused write-mode statement is not recorded."""
    connection_id = _unlocked(env)
    sql = "UPDATE parts SET material = '" + CANARY_VALUE + "'"  # noqa: S608 - a fixed test statement
    answer = env.send("POST", "/api/queries", {"connection_id": connection_id, "sql": sql, "mode": "write"})
    assert (answer.status_code, answer.json()["code"]) == (403, "studio.query_write_unsupported")
    assert CANARY_VALUE not in answer.text
    # Neither the history nor the drafts (both in the state database) hold the statement.
    state_files = b"".join(p.read_bytes() for p in env.state.rglob("*") if p.is_file())
    assert CANARY_VALUE.encode() not in state_files


# --- the SQLite write connection (K-2, TX-16) ----------------------------------------------------------------------


def _write_connection(env: WriteEnv) -> Any:
    connection_id = _unlocked(env)
    return env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)


@needs_defensive
def test_the_write_connection_has_trusted_schema_off_and_defensive_mode_on(env: WriteEnv) -> None:
    write = _write_connection(env)
    raw = write._db.engine.raw_connection()
    try:
        driver: sqlite3.Connection = raw.driver_connection
        assert driver.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert driver.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE) is True  # type: ignore[attr-defined]
    finally:
        raw.close()


@needs_defensive
@pytest.mark.parametrize(
    "statement",
    [
        "ATTACH DATABASE ':memory:' AS other",
        "SELECT load_extension('x')",
        "SELECT fts3_tokenizer('simple')",
        "PRAGMA writable_schema = ON",
        "PRAGMA journal_mode = OFF",
        "PRAGMA foreign_keys = OFF",
        "CREATE TABLE evil (x)",
        "DROP TABLE parts",
        "ALTER TABLE parts ADD COLUMN y",
        "UPDATE sqlite_master SET sql = 'x'",
        "CREATE TEMP TABLE t (x)",
        "VACUUM INTO 'copy.db'",
    ],
)
def test_the_write_authorizer_refuses_what_leaves_the_limits(env: WriteEnv, statement: str) -> None:
    write = _write_connection(env)
    before = digest(env.db)
    # The driver's or tabrion's error, never a success.
    with pytest.raises(Exception) as caught, write.transaction() as transaction:
        transaction.execute(statement)
    assert caught.value is not None
    assert digest(env.db) == before
    assert not (env.tmp_path / "copy.db").exists()


@needs_defensive
def test_a_hostile_trigger_runs_only_within_the_limits(tmp_path: Path, static_dir: Path) -> None:
    """A trigger of the file that calls a function outside the allowlist: the row change fails as a whole."""
    env = WriteEnv(tmp_path, static_dir, DATA_EDITOR)
    try:
        hostile = sqlite3.connect(env.db)
        hostile.execute(
            "CREATE TRIGGER t AFTER INSERT ON parts BEGIN SELECT load_extension('/tmp/evil'); "
            "SELECT fts3_tokenizer('simple'); END"
        )
        hostile.commit()
        hostile.close()
        env.create_read()
        assert env.create_write().status_code == 201
        write = env.unlocks.require(env.session(), _unlocked(env), Area.DATA_EDITOR)
        with pytest.raises(Exception), write.transaction() as transaction:  # noqa: B017
            transaction.execute("INSERT INTO parts VALUES (2, 'Gold')")
        assert _read_only(env.db, "SELECT count(*) FROM parts") == 1
        # A plain row change is allowed (the role, here the file, is the boundary).
        with write.transaction() as transaction:
            transaction.execute("UPDATE parts SET material = 'Iron' WHERE id = 1")
    finally:
        env.close()


def test_the_write_authorizer_allows_reads_and_row_changes_only() -> None:
    ok, deny = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY
    assert write_authorizer(sqlite3.SQLITE_INSERT, "parts", None, "main", None) == ok
    assert write_authorizer(sqlite3.SQLITE_UPDATE, "parts", "material", "main", None) == ok
    assert write_authorizer(sqlite3.SQLITE_DELETE, "parts", None, "main", None) == ok
    assert write_authorizer(sqlite3.SQLITE_UPDATE, "sqlite_master", "sql", "main", None) == deny
    assert write_authorizer(sqlite3.SQLITE_INSERT, "sqlite_stat1", None, "main", None) == deny
    assert write_authorizer(sqlite3.SQLITE_ATTACH, "x", None, None, None) == deny
    assert write_authorizer(sqlite3.SQLITE_DETACH, "x", None, None, None) == deny
    assert write_authorizer(sqlite3.SQLITE_CREATE_TABLE, "x", None, "main", None) == deny
    assert write_authorizer(sqlite3.SQLITE_FUNCTION, None, "load_extension", None, None) == deny
    assert write_authorizer(sqlite3.SQLITE_FUNCTION, None, "lower", None, None) == ok
    assert write_authorizer(sqlite3.SQLITE_PRAGMA, "writable_schema", "1", None, None) == deny
    assert write_authorizer(sqlite3.SQLITE_PRAGMA, "query_only", "0", None, None) == ok
    assert write_authorizer(sqlite3.SQLITE_PRAGMA, "table_info", "parts", None, None) == ok


@needs_defensive
def test_a_file_swapped_after_the_read_connection_opened_is_not_unlocked(env: WriteEnv) -> None:
    """TX-16 at unlock: the file at the path is no longer the one the read connection opened."""
    connection_id = env.open()
    replacement = env.tmp_path / "replacement.db"
    make_database(replacement)
    _release(env, connection_id)
    os.replace(replacement, env.db)
    response = env.unlock(connection_id)
    assert response.status_code in (400, 405) and response.json()["code"] in (
        "studio.write_target_changed",
        "studio.write_target_unknown",
    )
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []


@needs_defensive
def test_a_file_swapped_after_the_unlock_stops_the_write_connection(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    write = env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    write.verify()  # the check runs, and passes, while the file is the one unlocked
    replacement = env.tmp_path / "replacement.db"
    make_database(replacement)
    _release(env, connection_id, write)
    os.replace(replacement, env.db)
    with pytest.raises(WriteError) as caught:
        write.verify()
    assert caught.value.code == "studio.write_target_changed"
    with pytest.raises(WriteError):
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    with pytest.raises(WriteError), write.transaction():
        pass


@needs_defensive
def test_a_link_in_place_of_the_file_is_refused(env: WriteEnv) -> None:
    connection_id = _unlocked(env)
    write = env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    other = env.tmp_path / "other.db"
    make_database(other)
    moved = env.tmp_path / "moved.db"
    _release(env, connection_id, write)
    os.replace(env.db, moved)
    try:
        env.db.symlink_to(other)
    except OSError:
        pytest.skip("no symlinks here")
    with pytest.raises(WriteError):
        write.verify()


def _side_files(path: Path) -> list[Path]:
    return [path, *(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal"))]


@needs_defensive
def test_a_missing_file_is_never_created_by_a_write_connection(tmp_path: Path) -> None:
    """L-3 of PR #111: opening a write connection on a missing file fails closed and leaves no file behind."""
    profile = Profile("parts", "sqlite", "dev", path=str(tmp_path / "missing.db"))
    missing = tmp_path / "missing.db"
    with pytest.raises(WriteError) as caught:
        open_write_connection("write", profile, Settings(), database_file=missing, journal_mode="wal", identity=(1, 1))
    assert caught.value.code == "studio.write_target_changed"
    assert not any(os.path.lexists(path) for path in _side_files(missing))


@needs_defensive
def test_a_file_removed_behind_an_open_write_connection_is_not_created_again(env: WriteEnv) -> None:
    """L-3 of PR #111: a new pool connection (here after the pool was emptied) opens ``file:...?mode=rw`` and then
    tabrion's ``create=False`` path: the removed file stays removed, and the use fails closed."""
    connection_id = _unlocked(env)
    write = env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    write.verify()
    # Every handle on the file closed (Windows refuses to remove an open file); the next use opens a new connection
    # through the creator.
    _release(env, connection_id, write)
    for path in _side_files(env.db):
        if path.exists():
            path.unlink()
    with pytest.raises(WriteError) as caught:
        write.fetch("SELECT count(*) FROM parts")
    assert caught.value.code == "studio.write_target_changed"
    assert not any(os.path.lexists(path) for path in _side_files(env.db))


@needs_defensive
def test_the_creator_never_creates_a_missing_file(tmp_path: Path) -> None:
    """The pool's creator itself (the URI ``mode=rw`` open), also when the earlier identity check passed."""
    from tabrion.sqlite import SQLiteProfile

    from tabrion_studio.write.connection import _open_existing

    missing = tmp_path / "gone.db"
    with pytest.raises(WriteError) as caught:
        _open_existing(str(missing), SQLiteProfile())
    assert caught.value.code == "studio.write_target_changed"
    assert not any(os.path.lexists(path) for path in _side_files(missing))


@needs_defensive
def test_refusals_echo_nothing_of_the_request(env: WriteEnv) -> None:
    """TX-14 canary: ids, typed names and values from the request never come back."""
    connection_id = env.open()
    for body in (
        {"connection_id": CANARY_VALUE, "write_profile_id": "write:parts-write", "typed_name": "parts-write"},
        {"connection_id": connection_id, "write_profile_id": f"write:{CANARY_VALUE}", "typed_name": CANARY_SECRET},
        {"connection_id": connection_id, "write_profile_id": "write:parts-write", "typed_name": CANARY_SECRET},
    ):
        response = env.send("POST", "/api/write/unlocks", body)
        assert response.status_code == 400
        assert CANARY_VALUE not in response.text and CANARY_SECRET not in response.text
    secret = env.send("PUT", "/api/write/profiles/write:parts-write/secret", {"secret": CANARY_SECRET, "store": "x"})
    assert secret.status_code == 400 and CANARY_SECRET not in secret.text
