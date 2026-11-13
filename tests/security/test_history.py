"""STU-020: the query history, the favorite and recent profiles and the state database (threats T-04 and T-05).

* STU-T-007 (T-04): ``state.sqlite`` is created 0600 through ``tabrion.sqlite.connect``; a link in its place is refused
  and the history answers ``studio.state_unavailable`` while queries run on.
* 08 section 5 "Parameter nie in Historie": a canary value sent as a query parameter is in no history answer, in no
  byte of the state database (with its ``-wal`` and ``-shm`` files) and in no log record; only the placeholder name is.
* The history can be turned off, keeps ``retention_days``, at most ``MAX_ENTRIES`` entries and ``MAX_SQL_CHARS`` of a
  statement; deleted entries leave no text in the file (``secure_delete`` and the checkpoint).
* Project profiles' entries and favorites belong to their project; user profiles' to every project.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import logging
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any
import uuid

import pytest

from tabrion_studio.api.config import form_settings
from tabrion_studio.config.files import parse_toml
from tabrion_studio.config.schema import Profile, Settings, validate_settings
from tabrion_studio.config.writer import checked_settings
from tabrion_studio.logs import configure_logging
from tabrion_studio.services import history as history_module
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.explorer import Explorer, ExplorerError
from tabrion_studio.services.favorites import MAX_RECENT, Favorites, FavoritesError
from tabrion_studio.services.history import MAX_SQL_CHARS, History, HistoryError
from tabrion_studio.services.queries import QueryEngine
from tabrion_studio.services.state import STATE_FILE, StateStore, StateUnavailable, scope_of
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, Studio, make_studio

pytestmark = pytest.mark.security

CANARY = f"tabrion-canary-{uuid.uuid4()}"
POSIX = sys.platform != "win32"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "state"
    folder.mkdir(mode=0o700)
    return folder


@pytest.fixture
def sqlite_file(tmp_path: Path) -> Path:
    path = tmp_path / "parts.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE part (id INTEGER PRIMARY KEY, name TEXT)")
    connection.executemany("INSERT INTO part (name) VALUES (?)", [(f"part {i}",) for i in range(5)])
    connection.commit()
    connection.close()
    path.chmod(0o600)
    return path


def _source(path: Path) -> Source:
    return Source(
        profiles={
            "project:parts": Profile("parts", "sqlite", "dev", path="data/parts.db"),
            "user:mine": Profile("mine", "sqlite", "prod", path="mine.db"),
        },
        files={"project:parts": path, "user:mine": path},
    )


class Env:
    def __init__(self, studio: Studio, history: History, favorites: Favorites, store: StateStore, clock: Clock) -> None:
        self.studio = studio
        self.history = history
        self.favorites = favorites
        self.store = store
        self.clock = clock
        self.headers = {**studio.sign_in(), "Origin": ORIGIN}

    def get(self, path: str, **params: Any) -> Any:
        return self.studio.client.get(path, params=params, headers=self.headers)

    def send(self, method: str, path: str, body: dict[str, Any]) -> Any:
        return self.studio.client.request(method, path, json=body, headers=self.headers)

    def run(self, sql: str, params: dict[str, Any] | None = None, profile: str = "project:parts") -> dict[str, Any]:
        environment = "prod" if profile == "user:mine" else None
        opened = self.send("POST", "/api/connections", {"profile_id": profile, "confirmed_environment": environment})
        assert opened.status_code == 201, opened.text
        started = self.send(
            "POST", "/api/queries", {"connection_id": opened.json()["connection_id"], "sql": sql, "params": params}
        )
        assert started.status_code == 202, started.text
        query = self.studio.runtime.queries.get(started.json()["query_id"])  # type: ignore[union-attr]
        assert query.done.wait(10)
        return dict(query.status())


@pytest.fixture
def env(static_dir: Path, state_dir: Path, sqlite_file: Path) -> Iterator[Env]:
    source = _source(sqlite_file)
    clock = Clock()
    settings = {"value": Settings()}
    store = StateStore(state_dir)
    history = History(store, "projkey1", lambda: settings["value"], clock=clock)
    favorites = Favorites(store, "projkey1", lambda: set(source.profiles), clock=clock)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits, history=history)
    studio = make_studio(static_dir, connections=registry, queries=engine, history=history, favorites=favorites)
    environment = Env(studio, history, favorites, store, clock)
    environment.settings = settings  # type: ignore[attr-defined]
    try:
        yield environment
    finally:
        engine.close_all()
        registry.close_all()
        store.close()


def _state_bytes(state_dir: Path) -> bytes:
    return b"".join(path.read_bytes() for path in sorted(state_dir.glob(STATE_FILE + "*")))


# The state database (STU-T-007).


@pytest.mark.skipif(not POSIX, reason="file modes")
def test_the_state_database_is_private(env: Env, state_dir: Path) -> None:
    env.run("SELECT 1")
    for path in state_dir.glob(STATE_FILE + "*"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700


def test_the_state_database_is_opened_lazily(static_dir: Path, state_dir: Path) -> None:
    store = StateStore(state_dir)
    History(store, "k")
    assert list(state_dir.iterdir()) == []


@pytest.mark.skipif(not POSIX, reason="symbolic links")
def test_a_link_in_place_of_the_state_database_is_refused(
    static_dir: Path, state_dir: Path, sqlite_file: Path, tmp_path: Path
) -> None:
    target = tmp_path / "elsewhere.sqlite"
    target.write_bytes(b"")
    (state_dir / STATE_FILE).symlink_to(target)
    source = _source(sqlite_file)
    store = StateStore(state_dir)
    history = History(store, "k")
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits, history=history)
    studio = make_studio(
        static_dir,
        connections=registry,
        queries=engine,
        history=history,
        favorites=Favorites(store, "k", lambda: set(source.profiles)),
    )
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    try:
        answer = studio.client.get("/api/history", headers=headers)
        assert answer.status_code == 503
        assert answer.json()["code"] == "studio.state_unavailable"
        assert studio.client.get("/api/favorites", headers=headers).json()["code"] == "studio.state_unavailable"
        # Queries still run, without an entry; the link's target is untouched.
        opened = studio.client.post("/api/connections", json={"profile_id": "project:parts"}, headers=headers)
        assert opened.status_code == 201
        started = studio.client.post(
            "/api/queries", json={"connection_id": opened.json()["connection_id"], "sql": "SELECT 1"}, headers=headers
        )
        query = engine.get(started.json()["query_id"])
        assert query.done.wait(10)
        assert query.status()["state"] == "done"
        assert target.read_bytes() == b""
    finally:
        engine.close_all()
        registry.close_all()


@pytest.mark.skipif(not POSIX, reason="file modes")
def test_a_state_database_others_can_write_is_refused(state_dir: Path) -> None:
    path = state_dir / STATE_FILE
    sqlite3.connect(path).close()
    path.chmod(0o666)
    with pytest.raises(StateUnavailable):
        History(StateStore(state_dir), "k").entries()


def test_scopes() -> None:
    assert scope_of("user:x", "k") == ""
    assert scope_of("project:x", "k") == "k"
    assert scope_of("other:x", "k") is None


# No parameter value in the history (08 section 5).


def test_a_parameter_value_reaches_no_entry_no_file_and_no_log(env: Env, state_dir: Path, tmp_path: Path) -> None:
    console = io.StringIO()
    setup = configure_logging(tmp_path / "logs", "studio-history", level=logging.DEBUG, console=console)
    try:
        status = env.run(
            "SELECT name FROM part WHERE name <> :secret AND :flag IS NOT NULL", {"secret": CANARY, "flag": CANARY}
        )
        assert status["state"] == "done"
        env.run("SELECT :token AS t", {"token": CANARY}, profile="user:mine")
        answer = env.get("/api/history")
        assert answer.status_code == 200
        text = answer.text
        assert CANARY not in text
        entries = answer.json()["entries"]
        assert [entry["param_names"] for entry in entries] == [["token"], ["flag", "secret"]]
        assert entries[1]["sql"] == "SELECT name FROM part WHERE name <> :secret AND :flag IS NOT NULL"
        env.store.checkpoint()
        assert CANARY.encode() not in _state_bytes(state_dir)
        for handler in setup.handlers:
            handler.flush()
        logged = console.getvalue() + "".join(p.read_text() for p in (tmp_path / "logs").glob("*"))
        assert CANARY not in logged
        assert "SELECT name FROM part" not in logged
    finally:
        setup.close()


def test_the_history_interface_has_no_place_for_values() -> None:
    """The recorder takes placeholder names only: a value cannot be passed by mistake."""
    import inspect

    parameters = set(inspect.signature(History.record).parameters)
    assert parameters == {
        "self",
        "profile_id",
        "environment",
        "engine",
        "sql",
        "param_names",
        "state",
        "row_count",
        "elapsed_ms",
        "code",
    }


def test_an_entry_keeps_state_rows_and_problem_code(env: Env) -> None:
    env.run("SELECT * FROM part")
    env.run("SELECT * FROM no_such_table")
    entries = env.get("/api/history").json()["entries"]
    failed, done = entries
    assert (done["state"], done["row_count"], done["code"], done["environment"]) == ("done", 5, None, "dev")
    assert done["engine"] == "sqlite" and done["profile_id"] == "project:parts"
    assert failed["state"] == "failed" and failed["code"].startswith("db.")
    assert failed["row_count"] == 0


# Settings: off, retention, size.


def test_history_off_records_nothing(env: Env) -> None:
    env.settings["value"] = Settings(history_enabled=False)  # type: ignore[attr-defined]
    env.run("SELECT 1")
    answer = env.get("/api/history").json()
    assert answer["enabled"] is False
    assert answer["entries"] == []


def test_entries_older_than_the_retention_go(env: Env) -> None:
    env.settings["value"] = Settings(history_retention_days=2)  # type: ignore[attr-defined]
    env.run("SELECT 1 AS old")
    env.clock.now += 3 * 86_400
    assert env.get("/api/history").json()["entries"] == []
    env.run("SELECT 2 AS new")
    with env.store.connection() as connection:
        texts = [row[0] for row in connection.execute("SELECT sql FROM history")]
    assert texts == ["SELECT 2 AS new"]


def test_a_long_statement_is_cut(env: Env) -> None:
    sql = "SELECT 1 AS x" + " " * MAX_SQL_CHARS + "-- end"
    env.run(sql)
    entry = env.get("/api/history").json()["entries"][0]
    assert entry["sql_truncated"] is True
    assert len(entry["sql"]) == MAX_SQL_CHARS


def test_at_most_max_entries_are_kept(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history_module, "MAX_ENTRIES", 3)
    for number in range(5):
        env.history.record(
            profile_id="project:parts",
            environment="dev",
            engine="sqlite",
            sql=f"SELECT {number}",
            param_names=[],
            state="done",
            row_count=1,
            elapsed_ms=1.0,
            code=None,
        )
    assert [e["sql"] for e in env.get("/api/history").json()["entries"]] == ["SELECT 4", "SELECT 3", "SELECT 2"]


def test_history_settings_validate_and_round_trip() -> None:
    settings, issues = validate_settings({"history": {"enabled": False, "retention_days": 7}})
    assert issues == []
    assert (settings.history_enabled, settings.history_retention_days) == (False, 7)
    assert validate_settings(parse_toml(checked_settings(settings)))[0] == settings
    _, bad = validate_settings({"history": {"enabled": "no", "retention_days": 0, "x": 1}})
    assert {(issue.pointer, issue.code) for issue in bad} == {
        ("settings.history.enabled", "config.type"),
        ("settings.history.retention_days", "config.range"),
        ("settings.history.x", "config.unknown_key"),
    }
    assert validate_settings({"history": []})[1][0].pointer == "settings.history"
    changed = form_settings({"history": {"retention_days": 90}}, Settings())
    assert (changed.history_enabled, changed.history_retention_days) == (True, 90)
    assert "[history]" not in checked_settings(Settings()).decode()


# Reading and deleting.


def _record(history: History, sql: str, profile: str = "project:parts") -> None:
    assert history.record(
        profile_id=profile,
        environment="dev",
        engine="sqlite",
        sql=sql,
        param_names=[],
        state="done",
        row_count=0,
        elapsed_ms=0.5,
        code=None,
    )


def test_pages_search_and_profile_filter(env: Env) -> None:
    for number in range(5):
        _record(env.history, f"SELECT {number} AS n_{number}")
    _record(env.history, "SELECT '100%' AS mine", profile="user:mine")
    first = env.get("/api/history", limit=2).json()
    assert [e["sql"] for e in first["entries"]] == ["SELECT '100%' AS mine", "SELECT 4 AS n_4"]
    second = env.get("/api/history", limit=2, before=first["next_before"]).json()
    assert [e["sql"] for e in second["entries"]] == ["SELECT 3 AS n_3", "SELECT 2 AS n_2"]
    assert env.get("/api/history", q="%").json()["entries"][0]["sql"] == "SELECT '100%' AS mine"
    assert len(env.get("/api/history", q="%").json()["entries"]) == 1
    assert len(env.get("/api/history", q="n_").json()["entries"]) == 5
    only = env.get("/api/history", profile_id="user:mine").json()["entries"]
    assert [e["profile_id"] for e in only] == ["user:mine"]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 501}, {"before": 0}, {"profile_id": "other:x"}, {"q": "x" * 201}],
)
def test_invalid_history_requests(env: Env, params: dict[str, Any]) -> None:
    answer = env.get("/api/history", **params)
    assert answer.status_code == 422
    assert answer.json()["code"] == "studio.invalid_request"


def test_project_entries_stay_in_their_project(env: Env) -> None:
    _record(env.history, "SELECT 'project'")
    _record(env.history, "SELECT 'user'", profile="user:mine")
    other = History(env.store, "otherproject")
    assert [e["sql"] for e in other.entries()["entries"]] == ["SELECT 'user'"]
    assert other.delete() == 1  # its own view: the user's entry only
    assert [e["sql"] for e in env.history.entries()["entries"]] == ["SELECT 'project'"]


def test_delete_one_a_profile_or_all(env: Env, state_dir: Path) -> None:
    marker = f"marker_{uuid.uuid4().hex}"
    for number in range(3):
        _record(env.history, f"SELECT '{marker}' AS c{number}")
    _record(env.history, f"SELECT '{marker}' AS mine", profile="user:mine")
    newest = env.get("/api/history").json()["entries"]
    assert env.send("DELETE", "/api/history", {"id": newest[1]["id"]}).json() == {"deleted": 1}
    assert env.send("DELETE", "/api/history", {"profile_id": "user:mine"}).json() == {"deleted": 1}
    assert env.send("DELETE", "/api/history", {}).json() == {"deleted": 2}
    assert env.get("/api/history").json()["entries"] == []
    # secure_delete and the checkpoint: no deleted text is left in the file or its write-ahead log.
    assert marker.encode() not in _state_bytes(state_dir)
    bad = env.send("DELETE", "/api/history", {"id": "1"})
    assert bad.json()["code"] == "studio.invalid_request"
    assert env.send("DELETE", "/api/history", {"other": 1}).json()["code"] == "access.bad_request"
    with pytest.raises(HistoryError):
        env.history.delete(entry_id=1, profile_id="user:mine")


# Favorites and recently opened profiles.


def test_favorites_set_clear_and_refuse_unknown_profiles(env: Env) -> None:
    assert env.get("/api/favorites").json() == {"favorites": [], "recent": []}
    set_ = env.send("PUT", "/api/favorites", {"profile_id": "project:parts", "favorite": True})
    assert set_.status_code == 200
    assert [f["profile_id"] for f in set_.json()["favorites"]] == ["project:parts"]
    again = env.send("PUT", "/api/favorites", {"profile_id": "project:parts", "favorite": True})
    assert len(again.json()["favorites"]) == 1
    unknown = env.send("PUT", "/api/favorites", {"profile_id": "project:nope", "favorite": True})
    assert (unknown.status_code, unknown.json()["code"]) == (404, "studio.profile_not_found")
    bad = env.send("PUT", "/api/favorites", {"profile_id": "project:parts", "favorite": "yes"})
    assert bad.json()["code"] == "studio.invalid_request"
    cleared = env.send("PUT", "/api/favorites", {"profile_id": "project:parts", "favorite": False})
    assert cleared.json()["favorites"] == []


def test_a_favorite_of_a_profile_that_is_gone_is_not_shown(env: Env) -> None:
    env.send("PUT", "/api/favorites", {"profile_id": "user:mine", "favorite": True})
    hidden = Favorites(env.store, "projkey1", lambda: {"project:parts"})
    assert hidden.state()["favorites"] == []
    with pytest.raises(FavoritesError):
        hidden.set_favorite("user:mine", True)


def test_project_favorites_stay_in_their_project(env: Env) -> None:
    env.favorites.set_favorite("project:parts", True)
    env.favorites.set_favorite("user:mine", True)
    other = Favorites(env.store, "otherproject", lambda: {"project:parts", "user:mine"})
    assert [f["profile_id"] for f in other.state()["favorites"]] == ["user:mine"]


def test_opening_a_connection_makes_the_profile_recent(env: Env) -> None:
    env.run("SELECT 1")
    env.clock.now += 10
    env.run("SELECT 1", profile="user:mine")
    recent = env.get("/api/favorites").json()["recent"]
    assert [r["profile_id"] for r in recent] == ["user:mine", "project:parts"]
    for number in range(MAX_RECENT + 3):
        env.clock.now += 1
        env.favorites.opened(f"project:p{number}")
    with env.store.connection() as connection:
        (count,) = connection.execute("SELECT count(*) FROM recent WHERE scope = 'projkey1'").fetchone()
    assert count == MAX_RECENT


# The catalog for completion (03 section 5.4: no PROD catalog read without a click).


def test_cached_only_answers_a_cached_catalog_only(env: Env) -> None:
    registry = env.studio.runtime.connections
    assert registry is not None
    explorer = Explorer(registry)
    connection = registry.open("project:parts")
    with pytest.raises(ExplorerError) as refused:
        explorer.catalog(connection.id, cached_only=True)
    assert refused.value.code == "studio.catalog_not_loaded"
    explorer.catalog(connection.id)
    assert explorer.catalog(connection.id, cached_only=True)["cached"] is True
