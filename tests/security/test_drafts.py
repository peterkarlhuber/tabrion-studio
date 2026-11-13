"""TD-033 drafts part, review F-5: the SQL editor's drafts live in the state database behind the authenticated API.

* The drafts' text is in ``state.sqlite`` (0600) and nowhere else: a canary text saved through ``PUT /api/drafts`` is
  in no other file under the test's folders and in no log record.
* A tab has a fixed set of fields: one with a parameter value (or any other field) is refused, and nothing is stored.
* Caps: 32 tabs, 512 K characters per text, 2 M in all; the body cap of the route.
* Pages of one project merge by tab (``known``); projects do not see each other's drafts.
* ``[history] retention_days`` applies, ``enabled = false`` and "Delete history" leave the drafts.
* The state database of version 1 (STU-020) is migrated to version 2 with its history kept and no backup copy left.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
import logging
from pathlib import Path
import stat
import sys
from typing import Any
import uuid

import pytest
from tabrion.sqlite import SQLiteProfile, connect, migrate

from tabrion_studio.api.config import Configuration
from tabrion_studio.app import BODY_LIMITS
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.schema import Settings
from tabrion_studio.config.user import UserConfig
from tabrion_studio.logs import configure_logging
from tabrion_studio.services import state as state_module
from tabrion_studio.services.drafts import MAX_TABS, MAX_TEXT_CHARS, MAX_TOTAL_CHARS, Drafts, DraftsError
from tabrion_studio.services.favorites import Favorites
from tabrion_studio.services.history import History
from tabrion_studio.services.state import SCHEMA_VERSION, STATE_FILE, StateStore, StateUnavailable
from tests.support.access import ORIGIN, Studio, make_studio

pytestmark = pytest.mark.security

CANARY = f"tabrion-draft-canary-{uuid.uuid4()}"
POSIX = sys.platform != "win32"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class Env:
    def __init__(self, studio: Studio, drafts: Drafts, history: History, clock: Clock, settings: dict[str, Settings]):
        self.studio = studio
        self.drafts = drafts
        self.history = history
        self.clock = clock
        self.settings = settings
        self.headers = {**studio.sign_in(), "Origin": ORIGIN}

    def get(self) -> Any:
        return self.studio.client.get("/api/drafts", headers=self.headers)

    def put(self, body: Any) -> Any:
        return self.studio.client.put("/api/drafts", json=body, headers=self.headers)


def _tab(tab_id: str, sql: str = "", **extra: Any) -> dict[str, Any]:
    tab = {"id": tab_id, "title": f"Query {tab_id}", "profile_id": "project:parts", "sql": sql, "saved_sql": ""}
    return {**tab, **extra}


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "state"
    folder.mkdir(mode=0o700)
    return folder


@pytest.fixture
def env(static_dir: Path, state_dir: Path) -> Iterator[Env]:
    clock = Clock()
    settings = {"value": Settings()}
    store = StateStore(state_dir)
    drafts = Drafts(store, "projkey1", lambda: settings["value"], clock=clock)
    history = History(store, "projkey1", lambda: settings["value"], clock=clock)
    studio = make_studio(static_dir, history=history, favorites=Favorites(store, "projkey1", set), drafts=drafts)
    try:
        yield Env(studio, drafts, history, clock, settings)
    finally:
        store.close()


def _state_bytes(state_dir: Path) -> bytes:
    return b"".join(path.read_bytes() for path in sorted(state_dir.glob(STATE_FILE + "*")))


def test_a_draft_survives_and_lives_only_in_the_state_database(env: Env, state_dir: Path, tmp_path: Path) -> None:
    console = io.StringIO()
    setup = configure_logging(tmp_path / "logs", "studio-drafts", level=logging.DEBUG, console=console)
    try:
        sql = f"SELECT '{CANARY}' AS c WHERE id = :id"
        saved = env.put({"tabs": [_tab("a", sql), _tab("b", "select 2")], "active_tab": "b", "known": ["a", "b"]})
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"tabs": 2}
        answer = env.get()
        assert answer.status_code == 200
        assert answer.json() == {"drafts": {"tabs": [_tab("a", sql), _tab("b", "select 2")], "active_tab": "b"}}
        # Only the state database holds the text: no other file under the test's folders, no log record.
        assert CANARY.encode() in _state_bytes(state_dir)
        for handler in setup.handlers:
            handler.flush()
        holders = sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file() and CANARY.encode() in path.read_bytes()
        )
        assert holders, "the canary must be somewhere"
        assert all(name.startswith(f"state/{STATE_FILE}") for name in holders), holders
        assert CANARY not in console.getvalue()
        if POSIX:
            for path in state_dir.glob(STATE_FILE + "*"):
                assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    finally:
        setup.close()


def test_without_drafts_the_answer_is_null(env: Env) -> None:
    assert env.get().json() == {"drafts": None}


@pytest.mark.parametrize(
    "tab",
    [
        _tab("a", "select :p", params={"p": CANARY}),
        _tab("a", values=[CANARY]),
        _tab("a", rows=[[CANARY]]),
        {"id": "a", "title": "T", "sql": 1},
        {"id": "", "title": "T", "sql": ""},
        _tab("a", script_path="a.sql", script_digest="not-a-digest"),
        _tab("a", script_path="a.sql"),
        _tab("a", script_crlf=True),
        _tab("a", script_path="", script_digest="c" * 64),
        _tab("a", script_path="a.sql", script_digest="c" * 64, script_crlf="yes"),
        _tab("x" * 65),
        {**_tab("a"), "title": "t" * 129},
    ],
)
def test_a_tab_with_another_field_or_shape_is_refused(env: Env, state_dir: Path, tab: dict[str, Any]) -> None:
    answer = env.put({"tabs": [tab], "active_tab": "a"})
    assert answer.status_code == 422
    assert answer.json()["code"] == "studio.invalid_request"
    assert env.get().json() == {"drafts": None}
    assert CANARY.encode() not in _state_bytes(state_dir)


@pytest.mark.parametrize(
    "body",
    [
        {"tabs": [_tab("a")], "active_tab": "a", "params": {"p": 1}},
        [],
        {"tabs": [_tab("a")], "activeTab": "a"},
    ],
)
def test_other_documents_are_bad_requests(env: Env, body: Any) -> None:
    answer = env.put(body)
    assert answer.status_code == 400
    assert answer.json()["code"] == "access.bad_request"


def test_invalid_lists_are_refused(env: Env) -> None:
    for body in (
        {"tabs": "a", "active_tab": "a"},
        {"tabs": [_tab("a"), _tab("a")], "active_tab": "a"},
        {"tabs": [_tab(f"t{i}") for i in range(MAX_TABS + 1)], "active_tab": "t0"},
        {"tabs": [_tab("a")], "active_tab": "a", "known": "a"},
        {"tabs": [_tab("a")], "active_tab": "a", "known": [1]},
        {"tabs": [_tab("a")], "active_tab": 3},
    ):
        answer = env.put(body)
        assert answer.json()["code"] == "studio.invalid_request", body


def test_the_caps(env: Env) -> None:
    fits = "x" * MAX_TEXT_CHARS
    assert env.put({"tabs": [_tab("a", fits)], "active_tab": "a"}).status_code == 200
    one = env.put({"tabs": [_tab("a", fits + "x")], "active_tab": "a"})
    assert one.status_code == 413
    assert one.json()["code"] == "studio.drafts_too_large"
    saved = env.put({"tabs": [{**_tab("a"), "saved_sql": fits + "x"}], "active_tab": "a"})
    assert saved.json()["code"] == "studio.drafts_too_large"
    count = MAX_TOTAL_CHARS // MAX_TEXT_CHARS
    full = [_tab(f"t{i}", fits) for i in range(count)]
    assert env.put({"tabs": full, "active_tab": "t0"}).status_code == 200
    over = env.put({"tabs": [*full, _tab("z", "x")], "active_tab": "t0"})
    assert over.json()["code"] == "studio.drafts_too_large"
    # The largest body the route takes is bounded by the streaming cap.
    limit = BODY_LIMITS["/api/drafts"]
    assert limit.code == "studio.drafts_too_large"
    big = env.studio.client.put(
        "/api/drafts",
        content=b'{"tabs":[' + b" " * limit.max_bytes + b"]}",
        headers={**env.headers, "Content-Type": "application/json"},
    )
    assert big.status_code == 413
    assert big.json()["code"] == "studio.drafts_too_large"


def test_pages_of_one_project_merge_by_tab(env: Env) -> None:
    env.put({"tabs": [_tab("a"), _tab("b")], "active_tab": "a", "known": ["a", "b"]})
    # Another page added c.
    env.put({"tabs": [_tab("a"), _tab("b"), _tab("c", "other")], "active_tab": "c", "known": ["a", "b", "c"]})
    # This page knew a and b, closed b and edited a.
    env.put({"tabs": [_tab("a", "mine")], "active_tab": "a", "known": ["a", "b"]})
    tabs = env.get().json()["drafts"]["tabs"]
    assert [(tab["id"], tab["sql"]) for tab in tabs] == [("a", "mine"), ("c", "other")]
    # Without `known`, the page's tabs replace the stored ones.
    env.put({"tabs": [_tab("z")], "active_tab": "nope"})
    assert env.get().json() == {"drafts": {"tabs": [_tab("z")], "active_tab": "z"}}
    # No tabs at all deletes the project's drafts.
    env.put({"tabs": [], "active_tab": None})
    assert env.get().json() == {"drafts": None}


def test_a_merge_stays_within_the_caps(env: Env) -> None:
    fits = "x" * MAX_TEXT_CHARS
    count = MAX_TOTAL_CHARS // MAX_TEXT_CHARS
    env.put({"tabs": [_tab(f"o{i}", fits) for i in range(count)], "active_tab": "o0"})
    env.put({"tabs": [_tab("mine", "select 1")], "active_tab": "mine", "known": ["mine"]})
    tabs = env.get().json()["drafts"]["tabs"]
    assert [tab["id"] for tab in tabs] == ["mine", *(f"o{i}" for i in range(count - 1))]
    many = [_tab(f"m{i}") for i in range(MAX_TABS)]
    env.put({"tabs": many, "active_tab": "m0", "known": [tab["id"] for tab in many]})
    env.put({"tabs": [_tab("n")], "active_tab": "n", "known": ["n"]})
    assert len(env.get().json()["drafts"]["tabs"]) == MAX_TABS


def test_script_fields_are_kept(env: Env) -> None:
    tab = _tab("a", "x", script_path="sql/report.sql", script_digest="c" * 64, script_crlf=True)
    env.put({"tabs": [tab], "active_tab": "a"})
    assert env.get().json()["drafts"]["tabs"] == [tab]
    plain = _tab("b", "y", script_path="sql/b.sql", script_digest="d" * 64, script_crlf=False)
    env.put({"tabs": [plain], "active_tab": "b"})
    stored = env.get().json()["drafts"]["tabs"][0]
    assert "script_crlf" not in stored


def test_projects_do_not_see_each_others_drafts(env: Env) -> None:
    env.put({"tabs": [_tab("a", "select 'project one'")], "active_tab": "a"})
    other = Drafts(env.drafts.store, "projkey2", clock=env.clock)
    assert other.read() == {"drafts": None}
    other.save([_tab("b", "select 'project two'")], "b")
    assert env.get().json()["drafts"]["tabs"][0]["sql"] == "select 'project one'"


def test_the_history_retention_applies_to_drafts(env: Env, state_dir: Path) -> None:
    env.settings["value"] = Settings(history_retention_days=2)
    marker = f"old_{uuid.uuid4().hex}"
    other = Drafts(env.drafts.store, "projkey2", lambda: env.settings["value"], clock=env.clock)
    other.save([_tab("o", f"select '{marker}'")], "o")
    env.put({"tabs": [_tab("a", f"select '{marker}'")], "active_tab": "a"})
    env.clock.now += 3 * 86_400
    # Read after the retention: deleted, not answered.
    assert env.get().json() == {"drafts": None}
    # A save deletes other projects' drafts that old as well.
    env.put({"tabs": [_tab("b", "select 1")], "active_tab": "b"})
    with env.drafts.store.connection() as connection:
        scopes = [row[0] for row in connection.execute("SELECT scope FROM drafts")]
    assert scopes == ["projkey1"]
    assert other.read() == {"drafts": None}


def test_purge_deletes_expired_drafts_of_every_project(env: Env, state_dir: Path) -> None:
    """At start and after a settings save (``server.py``, ``app.py``), like the history: the retention holds on disk."""
    marker = f"expired_{uuid.uuid4().hex}"
    other = Drafts(env.drafts.store, "projkey2", lambda: env.settings["value"], clock=env.clock)
    other.save([_tab("o", f"select '{marker}'")], "o")
    env.put({"tabs": [_tab("a", f"select '{marker}'")], "active_tab": "a"})
    assert env.drafts.purge() == 0
    env.settings["value"] = Settings(history_retention_days=1)
    env.clock.now += 2 * 86_400
    assert env.drafts.purge() == 2
    with env.drafts.store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
    # secure_delete and the checkpoint: the expired text is gone from the file and its write-ahead log.
    assert marker.encode() not in _state_bytes(state_dir)


def test_saving_the_settings_purges_expired_drafts(static_dir: Path, state_dir: Path, tmp_path: Path) -> None:
    """A shorter retention drops the drafts it expires at once, together with the history (``app.py``)."""
    project = tmp_path / "project"
    project.mkdir()
    config_dir = tmp_path / "config"
    configuration = Configuration(
        project=ProjectConfig(project, state_dir, "projkey1", config_dir), user=UserConfig(config_dir)
    )
    clock = Clock()
    store = StateStore(state_dir)
    drafts = Drafts(store, "projkey1", configuration.limits, clock=clock)
    history = History(store, "projkey1", configuration.limits, clock=clock)
    studio = make_studio(
        static_dir, config=configuration, history=history, favorites=Favorites(store, "projkey1", set), drafts=drafts
    )
    try:
        marker = f"expired_{uuid.uuid4().hex}"
        drafts.save([_tab("a", f"select '{marker}'")], "a")
        clock.now += 5 * 86_400
        headers = {**studio.sign_in(), "Origin": ORIGIN}
        saved = studio.client.put(
            "/api/settings", json={"settings": {"history": {"retention_days": 1}}, "digest": None}, headers=headers
        )
        assert saved.status_code == 200, saved.text
        assert marker.encode() not in _state_bytes(state_dir)
    finally:
        store.close()


def test_history_off_and_delete_history_leave_the_drafts(env: Env) -> None:
    env.settings["value"] = Settings(history_enabled=False)
    env.put({"tabs": [_tab("a", "select 1")], "active_tab": "a"})
    assert env.get().json()["drafts"]["tabs"][0]["sql"] == "select 1"
    deleted = env.studio.client.request("DELETE", "/api/history", json={}, headers=env.headers)
    assert deleted.status_code == 200
    assert env.get().json()["drafts"]["tabs"][0]["sql"] == "select 1"


def test_a_stored_document_that_cannot_be_read_is_no_draft(env: Env) -> None:
    with env.drafts.store.connection() as connection:
        connection.execute(
            "INSERT INTO drafts (scope, doc, updated_at) VALUES (?, ?, ?)", ("projkey1", "{", env.clock.now)
        )
    assert env.get().json() == {"drafts": None}
    with env.drafts.store.connection() as connection:
        connection.execute(
            "UPDATE drafts SET doc = ? WHERE scope = ?",
            (json.dumps({"tabs": [_tab("a", params={"p": 1})], "active_tab": "a"}), "projkey1"),
        )
    assert env.get().json() == {"drafts": None}


@pytest.mark.skipif(not POSIX, reason="symbolic links")
def test_without_the_state_database_the_routes_say_so(static_dir: Path, state_dir: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.sqlite"
    target.write_bytes(b"")
    (state_dir / STATE_FILE).symlink_to(target)
    store = StateStore(state_dir)
    studio = make_studio(static_dir, drafts=Drafts(store, "k"))
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    answer = studio.client.get("/api/drafts", headers=headers)
    assert answer.status_code == 503
    assert answer.json()["code"] == "studio.state_unavailable"
    put = studio.client.put("/api/drafts", json={"tabs": [_tab("a", CANARY)], "active_tab": "a"}, headers=headers)
    assert put.json()["code"] == "studio.state_unavailable"
    assert target.read_bytes() == b""


def test_the_service_refuses_what_the_route_refuses(env: Env) -> None:
    with pytest.raises(DraftsError):
        env.drafts.save([_tab("a", values={"p": CANARY})], "a")
    with pytest.raises(DraftsError):
        env.drafts.save(None, "a")


# Migration of the state database (version 1 of STU-020 to version 2).


def test_a_version_1_database_is_migrated_with_its_history(state_dir: Path) -> None:
    path = state_dir / STATE_FILE
    connection = connect(path, profile=SQLiteProfile(secure_delete=True, busy_timeout=5.0))
    migrate(connection, {1: state_module._migrate_v1}, target=1, backup=None)
    connection.execute(
        "INSERT INTO history (scope, profile_id, environment, engine, sql, sql_truncated, param_names, state,"
        " row_count, elapsed_ms, code, finished_at) VALUES ('', 'user:x', 'dev', 'sqlite', 'select 7', 0, '[]',"
        " 'done', 1, 1.0, NULL, 1.0)"
    )
    connection.close()
    store = StateStore(state_dir)
    try:
        with store.connection() as migrated:
            assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
            assert migrated.execute("SELECT sql FROM history").fetchall() == [("select 7",)]
            assert migrated.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
    finally:
        store.close()
    # The copy taken before the migration is gone: it would keep history text that a delete removed.
    assert sorted(item.name for item in state_dir.iterdir() if ".bak" in item.name) == []
    if POSIX:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_fresh_database_has_the_drafts_table(state_dir: Path) -> None:
    store = StateStore(state_dir)
    try:
        Drafts(store, "k").save([_tab("a")], "a")
        with store.connection() as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"history", "favorites", "recent", "drafts"} <= names
    finally:
        store.close()


def test_a_broken_state_database_makes_the_drafts_unavailable(state_dir: Path) -> None:
    """A database file that is not SQLite makes the drafts unavailable, never a crash."""
    (state_dir / STATE_FILE).write_bytes(b"not a database" * 100)
    (state_dir / STATE_FILE).chmod(0o600)
    with pytest.raises(StateUnavailable):
        Drafts(StateStore(state_dir), "k").read()
