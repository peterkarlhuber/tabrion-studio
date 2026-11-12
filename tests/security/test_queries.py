"""STU-011: the query engine's caps, timeouts and cancel (STU-T-018..020, threat T-09), and its binding rules.

* STU-T-018: rows, bytes and cells are capped; a PostgreSQL query is read through a server-side cursor, so a cap
  stops the transfer (a 50-million-row series with ``max_rows=10`` ends at once).
* STU-T-019: the profile's timeout stops a query (SQLite: an endless recursive query; PostgreSQL: ``pg_sleep``).
* STU-T-020: a cancel stops a running query within one second, and the connection works afterwards.
* The binding of TD-012: one checked statement, through ``ConnectionRegistry.get``, ``:name`` never read inside a
  literal (I-2), no write, no SQL text or parameter value in the log.

The SQLite tests always run; the PostgreSQL tests need ``TABRION_STUDIO_TEST_POSTGRES`` (see ``test_read_only.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import io
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
import tracemalloc
from typing import Any, cast
import uuid

import pytest
import sqlalchemy
from tabrion.errors import TabrionError

from tabrion_studio.config.schema import Profile, SecretRef, Settings, validate_settings
from tabrion_studio.logs import LogSetup, configure_logging
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services import queries as queries_module
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import ConnectionRegistry, RegistryError, startup_options, studio_profile
from tabrion_studio.services.queries import Query, QueryEngine, QueryError, json_value
from tabrion_studio.services.readonly import StatementRefused
from tabrion_studio.services.sqltext import BoundStatement, ParameterError, bind, is_bound, placeholders
from tabrion_studio.services.tabrion_compat import Cancel, Cancelled
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
ENDLESS = "WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r) SELECT count(*) FROM r"


def wait(query: Query, seconds: float = 10.0) -> Query:
    assert query.done.wait(seconds), "the query did not finish"
    return query


@pytest.fixture
def sqlite_file(tmp_path: Path) -> Path:
    path = tmp_path / "parts.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE part (id INTEGER PRIMARY KEY, name TEXT, data BLOB)")
    connection.executemany("INSERT INTO part (name) VALUES (?)", [(f"part {i}",) for i in range(3000)])
    connection.execute("INSERT INTO part (name, data) VALUES (?, ?)", ("x" * 100_000, b"\x00" * 100_000))
    connection.commit()
    connection.close()
    path.chmod(0o600)
    return path


def sqlite_source(path: Path, **settings: Any) -> Source:
    return Source(
        profiles={"project:parts": Profile("parts", "sqlite", "dev", path="data/parts.db", statement_timeout_ms=800)},
        files={"project:parts": path},
        settings=Settings(**settings),
    )


@pytest.fixture
def lite(sqlite_file: Path) -> Iterator[tuple[QueryEngine, str, Source]]:
    source = sqlite_source(sqlite_file)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    try:
        yield engine, registry.open("project:parts").id, source
    finally:
        engine.close_all()
        registry.close_all()


# Parameters (sqltext): ``:name`` outside literals only (I-2 of stu-009-review.md).


@pytest.mark.parametrize(
    ("sql", "names"),
    [
        ("SELECT :a, :b, :a", ["a", "b"]),
        ("SELECT ':a' AS x, \":b\" FROM t -- :c\n/* :d /* :e */ */ WHERE y = :f", ["f"]),
        ("SELECT x::int, :n::text, $$ :g $$, $t$ :h $t$, E'\\' :i', 'it''s :j'", ["n"]),
        ("SELECT :_x1 FROM t WHERE a = :y", ["_x1", "y"]),
        ("SELECT 1 : 2, ':'", []),
    ],
)
def test_placeholders_skip_literals_identifiers_and_comments(sql: str, names: list[str]) -> None:
    assert placeholders(sql, "postgresql") == names


def test_sqlite_placeholders_skip_its_own_quotes() -> None:
    assert placeholders("SELECT [:a], `:b`, ':c', :d", "sqlite") == ["d"]


def test_postgres_binding_uses_the_driver_style_and_escapes_percent() -> None:
    bound = bind("SELECT '50%' AS p, :a::int + 1 AS n, ':b' AS c", {"a": 41, "unused": 1}, "postgresql")
    assert bound.driver_sql == "SELECT '50%%' AS p, %(a)s::int + 1 AS n, ':b' AS c"
    assert bound.values == {"a": 41}
    lite = bind("SELECT '50%', :a", {"a": "x"}, "sqlite")
    assert (lite.driver_sql, lite.values) == ("SELECT '50%', :a", {"a": "x"})


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({}, "studio.query_param_missing"),
        ({"a": [1]}, "studio.query_params_invalid"),
        ({"a": {"x": 1}}, "studio.query_params_invalid"),
        ({"a": float("nan")}, "studio.query_params_invalid"),
        ({"a": "x" * 1_000_001}, "studio.query_params_invalid"),
        ({f"p{i}": 1 for i in range(101)} | {"a": 1}, "studio.query_params_invalid"),
    ],
)
def test_bad_parameters_are_refused(params: dict[str, Any], code: str) -> None:
    with pytest.raises(ParameterError) as raised:
        bind("SELECT :a", params, "postgresql")
    assert raised.value.code == code


def test_json_values_for_the_page() -> None:
    assert json_value(2**60)[0] == str(2**60)  # beyond JavaScript's safe integers
    assert json_value(float("inf"))[0] == "inf"
    assert json_value(b"\x01\x02")[0] == {"base64": "AQI="}
    assert json_value({"a": [1]})[0] == {"a": [1]}
    value, _, cut = json_value("y" * 70_000)
    assert cut and len(value) == 64 * 1024


# SQLite


def test_a_query_runs_and_pages(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    query = wait(engine.start(connection_id, "SELECT id, name FROM part WHERE id <= :n ORDER BY id", {"n": 3}))
    status = query.status()
    assert status["state"] == "done" and status["row_count"] == 3 and not status["truncated"]
    assert status["columns"] == [{"name": "id", "type": None}, {"name": "name", "type": None}]
    page = engine.page(query.id, 1, 1)
    assert page["rows"] == [[2, "part 1"]] and page["row_count"] == 3


def test_i2_a_colon_inside_a_literal_is_text(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    query = wait(engine.start(connection_id, "SELECT ':name' AS x, '%' AS p"))
    assert query.rows == [[":name", "%"]]


def test_stu_t_018_rows_are_capped(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    preview = wait(engine.start(connection_id, "SELECT id FROM part"))
    assert (len(preview.rows), preview.truncated) == (1000, "rows")  # 1 000 by default (03 section 5.3)
    some = wait(engine.start(connection_id, "SELECT id FROM part", max_rows=10))
    assert (len(some.rows), some.truncated) == (10, "rows")
    every = wait(engine.start(connection_id, "SELECT id FROM part", max_rows=5000))
    assert (len(every.rows), every.truncated) == (3001, None)


def test_stu_t_018_the_setting_is_the_ceiling(sqlite_file: Path) -> None:
    source = sqlite_source(sqlite_file, max_rows=50)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    try:
        query = wait(engine.start(registry.open("project:parts").id, "SELECT id FROM part", max_rows=100_000))
        assert (query.max_rows, len(query.rows), query.truncated) == (50, 50, "rows")
    finally:
        registry.close_all()


def test_stu_t_018_bytes_and_cells_are_capped(sqlite_file: Path) -> None:
    source = sqlite_source(sqlite_file)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits, max_bytes=20_000)
    try:
        connection_id = registry.open("project:parts").id
        small = wait(engine.start(connection_id, "SELECT name FROM part", max_rows=5000))
        assert small.truncated == "bytes" and 0 < len(small.rows) < 3000 and small.size <= 20_000
        engine.max_bytes = queries_module.MAX_BYTES
        big = wait(engine.start(connection_id, "SELECT name, data FROM part WHERE data IS NOT NULL"))
        assert len(big.rows[0][0]) == 64 * 1024 and big.truncated_cells == [(0, 0), (0, 1)]
        assert engine.page(big.id)["truncated_cells"] == [[0, 0], [0, 1]]
    finally:
        registry.close_all()


def test_stu_t_019_the_profile_timeout_stops_an_endless_query(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    started = time.monotonic()
    query = wait(engine.start(connection_id, ENDLESS))
    assert time.monotonic() - started < 3.0  # 0.8 s timeout
    assert query.state == "failed" and query.problem is not None and query.problem.code == "db.timeout"


def test_stu_t_020_cancel_stops_a_query_within_a_second(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, source = lite
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], statement_timeout_ms=None)
    engine.registry.close_all()
    connection_id = engine.registry.open("project:parts").id
    query = engine.start(connection_id, ENDLESS)
    time.sleep(0.3)
    asked = time.monotonic()
    engine.cancel(query.id)
    wait(query, 5.0)
    assert time.monotonic() - asked < 1.0
    assert query.state == "canceled" and query.problem is not None and query.problem.code == "studio.query_canceled"
    assert wait(engine.start(connection_id, "SELECT 1")).rows == [[1]]  # the connection works afterwards


def test_a_cancel_before_the_statement_starts_stops_it() -> None:
    hook = Cancel()
    hook.cancel()
    with pytest.raises(Cancelled):
        hook.attach(object())
    assert hook.detach() is False


def test_writes_and_several_statements_are_refused(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    with pytest.raises(StatementRefused):
        engine.start(connection_id, "SELECT 1; DELETE FROM part")
    for sql in ("COMMIT", "DELETE FROM part", "DECLARE c CURSOR FOR SELECT 1", "SET cache_size = 1"):
        with pytest.raises(StatementRefused):
            engine.start(connection_id, sql)
    query = wait(engine.start(connection_id, "PRAGMA user_version = 7"))  # past the text check: SQLite refuses
    assert query.state == "failed" and query.problem is not None and query.problem.code.startswith("db.")
    assert wait(engine.start(connection_id, "SELECT count(*) FROM part")).rows == [[3001]]


def test_the_connection_is_checked_through_the_registry(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, source = lite
    with pytest.raises(RegistryError):
        engine.start("c_unknown", "SELECT 1")
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], path="data/other.db")
    with pytest.raises(RegistryError) as raised:
        engine.start(connection_id, "SELECT 1")
    assert raised.value.code == "studio.connection_stale"


def test_parallel_queries_are_limited(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    running = [engine.start(connection_id, ENDLESS) for _ in range(2)]
    with pytest.raises(QueryError) as raised:
        engine.start(connection_id, ENDLESS)  # 2 per connection (its pool)
    assert raised.value.code == "studio.query_limit"
    engine.per_connection, engine.max_parallel = 10, 2  # the limit for all of Studio (4 by default)
    with pytest.raises(QueryError):
        engine.start(connection_id, ENDLESS)
    assert (queries_module.MAX_PARALLEL, queries_module.PER_CONNECTION) == (4, 2)  # 03 section 5.3
    for query in running:
        engine.cancel(query.id)
        wait(query)
    assert wait(engine.start(connection_id, "SELECT 1")).state == "done"


def test_only_recent_queries_are_kept(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    first = wait(engine.start(connection_id, "SELECT 1"))
    for _ in range(queries_module.KEPT_QUERIES):
        wait(engine.start(connection_id, "SELECT 2"))
    with pytest.raises(QueryError) as raised:
        engine.get(first.id)
    assert raised.value.code == "studio.query_not_found"


def test_no_sql_text_or_parameter_in_the_log(lite: tuple[QueryEngine, str, Source], tmp_path: Path) -> None:
    engine, connection_id, _ = lite
    canary = f"tabrion-canary-{uuid.uuid4()}"
    setup: LogSetup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        literal = "SELECT '" + canary + "', :p"
        failing = "SELECT no_such_column, '" + canary + "' FROM part WHERE name = :p"  # noqa: S608 (a test canary)
        wait(engine.start(connection_id, literal, {"p": canary}))
        wait(engine.start(connection_id, failing, {"p": canary}))
    finally:
        setup.close()
    text = "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*"))
    assert "studio.query_finished" in text
    assert canary not in text


def test_close_all_cancels_running_queries(sqlite_file: Path) -> None:
    source = sqlite_source(sqlite_file)
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], statement_timeout_ms=None)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    query = engine.start(registry.open("project:parts").id, ENDLESS)
    time.sleep(0.2)
    engine.close_all()
    registry.close_all()
    assert query.finished and query.state == "canceled"
    with pytest.raises(QueryError):
        engine.start("c_x", "SELECT 1")


# The API


def test_the_query_routes(sqlite_file: Path, tmp_path: Path) -> None:
    source = sqlite_source(sqlite_file)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    studio = make_studio(tmp_path / "static", connections=registry, queries=engine)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    try:
        connection_id = registry.open("project:parts").id
        client = studio.client
        answer = client.post(
            "/api/queries",
            json={"connection_id": connection_id, "sql": "SELECT :n AS n", "params": {"n": 7}},
            headers=headers,
        )
        assert answer.status_code == 202, answer.text
        query_id = answer.json()["query_id"]
        with client.stream("GET", f"/api/queries/{query_id}/events", headers=headers) as events:
            assert events.headers["content-type"].startswith("text/event-stream")
            body = "".join(events.iter_text())
        assert body.rstrip().splitlines()[-2] == "event: done"
        assert client.get(f"/api/queries/{query_id}", headers=headers).json()["state"] == "done"
        rows = client.get(f"/api/queries/{query_id}/rows?offset=0&limit=10", headers=headers).json()
        assert rows["rows"] == [[7]] and rows["columns"] == [{"name": "n", "type": None}]
        assert client.post(f"/api/queries/{query_id}/cancel", json={}, headers=headers).status_code == 202

        def refused(body: Any) -> str:
            answer = client.post("/api/queries", json=body, headers=headers)
            assert answer.status_code >= 400
            return str(answer.json()["code"])

        assert refused({"connection_id": connection_id, "sql": "SELECT 1", "mode": "write"}) == (
            "studio.query_write_unsupported"
        )
        assert refused({"connection_id": connection_id, "sql": "SELECT 1; SELECT 2"}) == "studio.statement_multiple"
        assert refused({"connection_id": connection_id, "sql": "SELECT :x"}) == "studio.query_param_missing"
        assert refused({"connection_id": "c_nope", "sql": "SELECT 1"}) == "studio.connection_not_found"
        assert refused({"connection_id": connection_id, "sql": "x" * 600_000}) == "studio.query_too_large"
        assert refused({"connection_id": connection_id, "sql": "SELECT 1", "max_rows": 0}) == "studio.invalid_request"
        assert refused({"sql": 1}) == "access.bad_request"
        assert client.get("/api/queries/q_nope", headers=headers).json()["code"] == "studio.query_not_found"
        assert client.get(f"/api/queries/{query_id}/rows?limit=0", headers=headers).status_code == 422
        # Behind the session and CSRF checks like every API route (STU-003).
        assert client.get(f"/api/queries/{query_id}").status_code in (401, 403)
    finally:
        engine.close_all()
        registry.close_all()


def test_the_events_end_with_a_failure(lite: tuple[QueryEngine, str, Source], tmp_path: Path) -> None:
    engine, connection_id, _ = lite
    studio = make_studio(tmp_path / "static", connections=engine.registry, queries=engine)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    answer = studio.client.post(
        "/api/queries", json={"connection_id": connection_id, "sql": "SELECT nope FROM part"}, headers=headers
    )
    with studio.client.stream("GET", f"/api/queries/{answer.json()['query_id']}/events", headers=headers) as events:
        body = "".join(events.iter_text())
    assert "event: failed" in body and '"code":"db.' in body and "nope" not in body


# PostgreSQL


@pytest.fixture
def pg() -> Iterator[tuple[QueryEngine, str, Source]]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    profile = Profile(
        "pg",
        "postgresql",
        "dev",
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        password=SecretRef("keyring", ""),
        sslmode="prefer",
        statement_timeout_ms=1500,
    )
    source = Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    try:
        yield engine, registry.open("user:pg").id, source
    finally:
        engine.close_all()
        registry.close_all()


@pytest.mark.postgres
@postgres
def test_pg_types_parameters_and_literals(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    query = wait(
        engine.start(
            connection_id,
            "SELECT :a::int + 1 AS n, ':b' AS c, '50%' AS p, now()::date AS d, '{\"k\": [1]}'::jsonb AS j, "
            "ARRAY[1, 2] AS a, 1.50::numeric AS m",
            {"a": 41},
        )
    )
    assert query.state == "done", query.problem
    assert query.rows[0][:3] == [42, ":b", "50%"]
    assert query.rows[0][4:] == [{"k": [1]}, [1, 2], "1.50"]
    assert [c.type_name for c in query.columns] == ["int4", "text", "text", "date", "jsonb", "int4[]", "numeric"]


@pytest.mark.postgres
@postgres
def test_pg_stu_t_018_a_server_side_cursor_stops_the_transfer(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    started = time.monotonic()
    query = wait(
        # generate_series in the select list runs lazily (in FROM it would fill a tuplestore first).
        engine.start(connection_id, "SELECT generate_series(1, 50000000) AS g, repeat('x', 100) AS x", max_rows=10)
    )
    assert time.monotonic() - started < 1.5  # a full transfer of 50 million rows would take far longer
    assert (len(query.rows), query.truncated, query.state) == (10, "rows", "done")
    explain = wait(engine.start(connection_id, "EXPLAIN SELECT 1"))  # no cursor can hold EXPLAIN: a plain one
    assert explain.state == "done" and explain.rows


@pytest.mark.postgres
@postgres
def test_pg_stu_t_019_the_statement_timeout(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    started = time.monotonic()
    query = wait(engine.start(connection_id, "SELECT pg_sleep(10)"))
    assert time.monotonic() - started < 4.0  # 1.5 s
    assert query.state == "failed" and query.problem is not None and query.problem.code == "db.timeout"


@pytest.mark.postgres
@postgres
def test_pg_stu_t_020_cancel_within_a_second(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, source = pg
    source.profiles["user:pg"] = replace(source.profiles["user:pg"], statement_timeout_ms=None)
    engine.registry.close_all()
    connection_id = engine.registry.open("user:pg").id
    for sql in ("SELECT pg_sleep(30)", "SELECT count(*) FROM generate_series(1, 2000000000)"):
        query = engine.start(connection_id, sql)
        time.sleep(0.3)
        asked = time.monotonic()
        engine.cancel(query.id)
        wait(query, 5.0)
        assert time.monotonic() - asked < 1.0, sql
        assert query.state == "canceled"
    after = wait(engine.start(connection_id, "SELECT current_setting('transaction_read_only')"))
    assert after.rows == [["on"]]


@pytest.mark.postgres
@postgres
def test_pg_writes_fail_and_one_statement_only(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    with pytest.raises(StatementRefused):
        engine.start(connection_id, "CREATE TABLE studio_query_probe (x int)")
    connection = engine.registry.get(connection_id)
    with (
        pytest.raises(TabrionError) as refused,
        connection._read() as transaction,
        tabrion_compat.stream(
            transaction, "CREATE TABLE studio_query_probe (x int)", {}, cancel=Cancel(), server_side=False
        ),
    ):
        pass
    assert refused.value.classification is not None and refused.value.classification.kind.value == "read_only"
    # Past the text check, the server cursor's DECLARE is a prepared statement too: several commands are refused.
    escape = "SELECT '\\''; COMMIT; BEGIN READ WRITE; CREATE TABLE studio_query_probe (x int); COMMIT; SELECT ''''"
    with (
        pytest.raises(TabrionError) as raised,
        connection._read() as transaction,
        tabrion_compat.stream(transaction, escape, {}, cancel=Cancel(), server_side=True) as cursor,
    ):
        cursor.fetch(1)
    assert raised.value.classification is not None and raised.value.classification.sqlstate == "42601"
    check = wait(engine.start(connection_id, "SELECT to_regclass('studio_query_probe') IS NULL"))
    assert check.rows == [[True]]


def test_cancel_threads_do_not_outlive_the_query(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    before = threading.active_count()
    query = wait(engine.start(connection_id, "SELECT 1"))
    engine.cancel(query.id)  # finished: no cancel thread
    assert threading.active_count() <= before + 1


# The fix round of stu-011-review.md (M-1..M-3, L-1..L-3, I-3) and H-1 of tab-033-review.md.


# L-1: what is executed is what was checked.


@pytest.mark.parametrize(
    "forged",
    [
        BoundStatement("SELECT 1", "DELETE FROM part", {}),
        BoundStatement("SELECT 1; DELETE FROM part", "SELECT 1; DELETE FROM part", {}),
        BoundStatement("DELETE FROM part", "DELETE FROM part", {}),
        BoundStatement("SELECT :a", "SELECT :a", {"a": 1, "b": 2}),
        BoundStatement("SELECT :a", "SELECT :a", {}),
    ],
)
def test_l1_stream_refuses_a_statement_that_prepare_did_not_build(
    lite: tuple[QueryEngine, str, Source], forged: BoundStatement
) -> None:
    engine, connection_id, _ = lite
    connection = engine.registry.get(connection_id)
    before = connection.fetch("SELECT count(*) FROM part")
    with pytest.raises(StatementRefused):
        connection.stream(forged, lambda cursor: cursor.fetch(1))
    assert connection.fetch("SELECT count(*) FROM part") == before
    assert connection.stream(connection.prepare("SELECT :a", {"a": 7}), lambda cursor: cursor.fetch(1)) == [(7,)]


def test_l1_is_bound_compares_the_driver_text() -> None:
    good = bind("SELECT '50%', :a", {"a": 1}, "postgresql")
    assert is_bound(good, good.sql, "postgresql")
    assert not is_bound(BoundStatement(good.sql, "SELECT '50%', %(a)s", good.values), good.sql, "postgresql")
    assert not is_bound(BoundStatement(good.sql, good.driver_sql, {"b": 1}), good.sql, "postgresql")


# L-2 and H-1: the cancel guards, with fake driver connections.


class FakeCursor:
    description = None

    def __init__(self, on_execute: Any) -> None:
        self.on_execute = on_execute

    def execute(self, sql: str, values: Any) -> None:
        self.on_execute()

    def close(self) -> None:
        pass


class FakePsycopg:
    """What ``tabrion_compat`` uses of a psycopg connection."""

    def __init__(self) -> None:
        self.cancels = 0
        self.closed = False
        self.on_execute: Any = lambda: None

    def cancel_safe(self, timeout: float) -> None:
        self.cancels += 1

    def cursor(self, name: str | None = None) -> FakeCursor:
        return FakeCursor(self.on_execute)

    def close(self) -> None:
        self.closed = True


class CountingSqlite(sqlite3.Connection):
    interrupts = 0

    def interrupt(self) -> None:
        type(self).interrupts += 1
        super().interrupt()


class FakeTransaction:
    def __init__(self, dbapi: Any, *, invalidate_fails: bool = False) -> None:
        self.invalidated = 0
        outer = self

        class Connection:
            class connection:
                dbapi_connection = dbapi

            @staticmethod
            def invalidate() -> None:
                if invalidate_fails:
                    raise RuntimeError("cannot invalidate")
                outer.invalidated += 1

        self.connection = Connection


def test_l2_m10_a_cancel_after_detach_reaches_nothing() -> None:
    fake = FakePsycopg()
    hook = Cancel()
    hook.attach(fake)
    assert hook.detach() is False
    hook.cancel()
    assert fake.cancels == 0
    lite = sqlite3.connect(":memory:", factory=CountingSqlite)
    CountingSqlite.interrupts = 0
    hook = Cancel()
    hook.attach(lite)
    hook.detach()
    hook.cancel()
    assert CountingSqlite.interrupts == 0
    lite.close()


def fake_stream(transaction: FakeTransaction, hook: Cancel, *, server_side: bool) -> Any:
    return tabrion_compat.stream(cast(Any, transaction), "SELECT 1", {}, cancel=hook, server_side=server_side)


@pytest.mark.parametrize("engine", ["postgresql", "sqlite"])
def test_l2_m7_a_connection_that_got_a_cancel_is_invalidated(engine: str) -> None:
    dbapi: Any = FakePsycopg() if engine == "postgresql" else sqlite3.connect(":memory:", factory=CountingSqlite)
    hook = Cancel()
    transaction = FakeTransaction(dbapi)
    if engine == "postgresql":
        dbapi.on_execute = hook.cancel  # the cancel arrives while the statement runs
        with pytest.raises(Cancelled), fake_stream(transaction, hook, server_side=True):
            pass
        assert dbapi.cancels == 1
    else:
        with pytest.raises(Cancelled), fake_stream(transaction, hook, server_side=False):
            hook.cancel()
    assert transaction.invalidated == 1
    # The driver connection is closed after invalidate() too, so nothing can keep it open until GC.
    if engine == "postgresql":
        assert dbapi.closed
    else:
        with pytest.raises(sqlite3.ProgrammingError):
            dbapi.execute("SELECT 1")
    dbapi = FakePsycopg() if engine == "postgresql" else sqlite3.connect(":memory:", factory=CountingSqlite)
    quiet = FakeTransaction(dbapi)
    with fake_stream(quiet, Cancel(), server_side=False):
        pass
    assert quiet.invalidated == 0  # no cancel, no invalidation
    if engine == "sqlite":
        assert dbapi.execute("SELECT 1").fetchone() == (1,)  # not closed
        dbapi.close()
    else:
        assert not dbapi.closed


def test_l2_a_connection_that_cannot_be_invalidated_is_closed() -> None:
    fake = FakePsycopg()
    hook = Cancel()
    fake.on_execute = hook.cancel
    with pytest.raises(Cancelled), fake_stream(FakeTransaction(fake, invalidate_fails=True), hook, server_side=True):
        pass
    assert fake.closed


#: The reviewer's race: cancels after 0 to 30 ms (spread evenly, so the test is the same on every run).
DELAYS = [(index * 7 % 31) / 1000 for index in range(60)]


def test_h1_a_late_sqlite_interrupt_never_hits_the_next_statement(lite: tuple[QueryEngine, str, Source]) -> None:
    """The reviewer's race on SQLite: a short query, a cancel after 0 to 30 ms, then the next statement."""
    engine, connection_id, _ = lite
    connection = engine.registry.get(connection_id)
    for delay in DELAYS:
        query = engine.start(connection_id, "SELECT count(*) FROM part a, part b WHERE a.id < 40")
        threading.Event().wait(delay)
        engine.cancel(query.id)
        wait(query)
        assert query.state in ("done", "canceled")
        assert connection.fetch("SELECT 42") == [(42,)]


# L-3: one cancel worker per query.


def test_l3_one_cancel_worker_per_query(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    query = engine.start(connection_id, ENDLESS)
    threading.Event().wait(0.05)
    for _ in range(25):
        engine.cancel(query.id)
    workers = [thread for thread in threading.enumerate() if thread.name == "studio-cancel"]
    assert len(workers) <= 1
    assert wait(query, 3.0).state == "canceled"


def test_l3_the_worker_keeps_asking_after_the_fast_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queries_module, "CANCEL_ATTEMPTS", 2)
    monkeypatch.setattr(queries_module, "CANCEL_SLOW_INTERVAL_S", 0.05)
    asked: list[int] = []

    class Stubborn(Cancel):
        def cancel(self) -> None:
            asked.append(1)
            if len(asked) == 6:
                query.done.set()  # the statement stops at the sixth request

    query = queries_module.Query("q_x", "c_x", "sqlite", 1, 1, 0.0, hook=Stubborn())
    QueryEngine._send_cancel(query)
    assert len(asked) == 6


# M-3: fetches within a byte budget; I-3: the configurable byte cap.

WIDE = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 8) "
    "SELECT x, zeroblob(16000000) AS b FROM c"
)


def test_m3_a_wide_blob_result_keeps_peak_memory_bounded(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    tracemalloc.start()
    try:
        query = wait(engine.start(connection_id, WIDE), 30.0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert query.state == "done", query.problem
    assert len(query.rows) == 8 and query.truncated_cells  # each value is cut to the cell cap
    # 8 values of 16 MB in one fetch would be 128 MB; the budget fetches them one at a time.
    assert peak < 60 * 1024 * 1024, peak


# R-M1 of the delta re-review: the budget follows the largest row so far, grows at most twice per fetch and counts
# nested values.

NULL_FIRST = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 24) "
    "SELECT x, CASE WHEN x > 1 THEN zeroblob(4000000) END AS b FROM c"
)


def peak_of(engine: QueryEngine, connection_id: str, sql: str) -> tuple[Query, int]:
    tracemalloc.start()
    try:
        query = wait(engine.start(connection_id, sql), 60.0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return query, peak


def test_r_m1_a_null_first_row_does_not_open_the_budget(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, source = lite
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], statement_timeout_ms=None)
    query, peak = peak_of(engine, connection_id, NULL_FIRST)
    assert query.state == "done", query.problem
    assert len(query.rows) == 24
    # Before: 1 row, then the other 23 in one fetch (92 MB). Now: 1, 2, then one at a time.
    assert peak < 30 * 1024 * 1024, peak


def test_r_m1_nested_values_count_in_full() -> None:
    raw_size = queries_module._raw_size
    assert raw_size((["x" * 1000] * 100,)) >= 100_000
    assert raw_size(({"k": ["y" * 500, {"z": "w" * 500}]},)) >= 1000
    deep: Any = "x"
    for _ in range(10_000):  # deeper than the recursion limit
        deep = [deep]
    assert raw_size((deep,)) > 10_000
    assert raw_size((None, 1, 2.5)) == 48


def test_r_m1_batches_grow_at_most_twice_per_fetch() -> None:
    sizes, fetched = [], 1
    for _ in range(12):
        fetched = queries_module.next_batch_rows(fetched, 100)
        sizes.append(fetched)
    assert sizes == [2, 4, 8, 16, 32, 64, 128, 256, 500, 500, 500, 500]
    assert queries_module.next_batch_rows(256, 4_000_000) == 1  # one large row closes the budget again
    assert queries_module.next_batch_rows(8, 300_000) == 6


@pytest.mark.postgres
@postgres
def test_pg_r_m1_wide_arrays_and_a_null_first_row_keep_peak_memory_bounded(
    pg: tuple[QueryEngine, str, Source],
) -> None:
    engine, connection_id, source = pg
    source.profiles["user:pg"] = replace(source.profiles["user:pg"], statement_timeout_ms=60_000)
    engine.registry.close_all()
    connection_id = engine.registry.open("user:pg").id
    arrays, peak = peak_of(
        engine, connection_id, "SELECT array_fill('xxxxxxxx'::text, ARRAY[100000]) FROM generate_series(1, 30)"
    )
    assert arrays.state == "done", arrays.problem
    # Before: 1 row, then 29 rows of 100000 strings in one fetch (about 200 MB). Now one row at a time.
    assert peak < 80 * 1024 * 1024, peak
    nulls, peak = peak_of(
        engine,
        connection_id,
        "SELECT CASE WHEN g > 1 THEN repeat('x', 4000000) END FROM generate_series(1, 24) AS g",
    )
    assert nulls.state == "done", nulls.problem
    assert peak < 40 * 1024 * 1024, peak


def test_i9_a_busy_pool_is_connection_busy(sqlite_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I-9 of the stu-010 delta re-review: a query that finds no free session within the pool's wait is
    ``studio.connection_busy``, not ``db.resource``."""
    monkeypatch.setattr(connections_module, "POOL_TIMEOUT_S", 0.3)
    source = sqlite_source(sqlite_file)
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], statement_timeout_ms=None)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    try:
        connection_id = registry.open("project:parts").id
        connection = registry.get(connection_id)
        release, holding = threading.Event(), threading.Barrier(3)

        def hold() -> None:
            with connection._read():  # one session of the pool, as another running statement would
                holding.wait()
                release.wait(10)

        holders = [threading.Thread(target=hold) for _ in range(2)]
        for holder in holders:
            holder.start()
        holding.wait()
        try:
            query = wait(engine.start(connection_id, "SELECT 1"), 10.0)
        finally:
            release.set()
            for holder in holders:
                holder.join()
        assert query.state == "failed" and query.problem is not None
        assert query.problem.code == "studio.connection_busy"
    finally:
        engine.close_all()
        registry.close_all()


def test_i3_the_byte_cap_is_the_max_result_mb_setting(sqlite_file: Any) -> None:
    source = sqlite_source(sqlite_file, max_result_mb=1)
    registry = ConnectionRegistry(source)
    engine = QueryEngine(registry, source.limits)
    try:
        connection_id = registry.open("project:parts").id
        query = wait(engine.start(connection_id, "SELECT name, data FROM part", max_rows=5000))
        assert query.max_bytes == 1024 * 1024 and query.truncated in ("bytes", None)
        # Waited for, so no statement of this test still holds a session when the registry closes.
        assert wait(QueryEngine(registry).start(connection_id, "SELECT 1")).max_bytes == 20 * 1024 * 1024
    finally:
        engine.close_all()
        registry.close_all()
    assert validate_settings({"limits": {"max_result_mb": 200}})[0].max_result_mb == 200
    assert [issue.code for issue in validate_settings({"limits": {"max_result_mb": 201}})[1]] == ["config.range"]


def test_a_session_returned_after_close_is_closed(sqlite_file: Path) -> None:
    """``Database.close()`` (``engine.dispose()``) closes only the idle sessions. One that a running statement holds
    is closed when it comes back, not left open in the disposed pool until garbage collection (the
    ``ResourceWarning: unclosed database`` that surfaced in an unrelated test's teardown)."""
    registry = ConnectionRegistry(sqlite_source(sqlite_file))
    connection = registry.get(registry.open("project:parts").id)
    held: list[sqlite3.Connection] = []
    inside, release = threading.Event(), threading.Event()

    def hold() -> None:
        with connection._read() as transaction:  # a running statement's session
            held.append(transaction.connection.connection.dbapi_connection)
            inside.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert inside.wait(10)
        registry.close_all()  # while the session is checked out
    finally:
        release.set()
        holder.join()
    with pytest.raises(sqlite3.ProgrammingError):  # closed at check-in, still referenced here
        held[0].execute("SELECT 1")


# M-2: startup options and the check-in reset.


def test_m2_startup_options_carry_studios_session_settings() -> None:
    settings = studio_profile(sqlite_source_profile(), Settings()).postgres
    options = startup_options(settings)
    assert options.split(" -c ")[0] == "-c default_transaction_read_only=on"
    assert "-c statement_timeout=30000" in options and "-c lock_timeout=5000" in options
    assert "-c TimeZone=UTC" in options
    # H-2 residual of stu-013-review.md: exact float text whatever the role sets (the viewer's keys are text).
    assert "-c extra_float_digits=3" in options


def test_r_i1_settings_the_startup_options_cannot_match_are_refused() -> None:
    settings = studio_profile(sqlite_source_profile(), Settings()).postgres
    for change in ({"search_path": ("App", "public")}, {"transaction_timeout_ms": 60_000}):
        with pytest.raises(RegistryError):
            startup_options(replace(settings, **change))


def sqlite_source_profile() -> Any:
    return Profile("pg", "postgresql", "dev", host="localhost", database="x")


def _below_the_check(connection: Any, sql: str) -> list[tuple[Any, ...]]:
    with (
        connection._read() as transaction,
        tabrion_compat.stream(transaction, sql, {}, cancel=Cancel(), server_side=False) as cursor,
    ):
        return list(cursor.fetch(10))


@pytest.mark.postgres
@postgres
def test_pg_m2_no_setting_outlives_its_statement(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    connection = engine.registry.get(connection_id)
    with pytest.raises(StatementRefused):
        engine.start(connection_id, "SET statement_timeout = 0")
    # Past the text check (what a function of the database could do): the setting must not survive check-in.
    for sql in ("SET statement_timeout = 0", "SET lock_timeout = 0", "SET search_path = public, pg_catalog"):
        for _ in range(3):  # whichever of the pool's connections it lands on
            _below_the_check(connection, sql)
    shown = {
        name: {connection.fetch(f"SHOW {name}")[0][0] for _ in range(4)}
        for name in ("statement_timeout", "lock_timeout", "search_path", "default_transaction_read_only")
    }
    assert shown["statement_timeout"] == {"1500ms"}
    assert shown["lock_timeout"] == {"5s"}
    assert shown["search_path"] == {'"$user", public'}
    assert shown["default_transaction_read_only"] == {"on"}
    assert connection.fetch("SHOW application_name") == [("tabrion-studio",)]


@pytest.mark.postgres
@postgres
def test_pg_r2_i1_a_role_change_does_not_outlive_its_statement(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    connection = engine.registry.get(connection_id)
    login = connection.fetch("SELECT session_user")[0][0]
    # Past the text check (what a function of the database could do); RESET ALL alone keeps the role.
    for _ in range(3):  # whichever of the pool's connections it lands on
        _below_the_check(connection, f'SET ROLE "{login}"')
    assert {connection.fetch("SELECT pg_catalog.current_setting('role')")[0][0] for _ in range(4)} == {"none"}


def test_r_h1_the_sqlite_nested_comment_probe_is_refused(lite: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = lite
    with pytest.raises(StatementRefused):
        engine.start(connection_id, "/* /* */ DELETE FROM part -- */ select\n")


@pytest.mark.postgres
@postgres
def test_pg_r_h1_the_carriage_return_probes_are_refused(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    for statement in (
        "DECLARE c CURSOR WITH HOLD FOR SELECT pg_sleep(6)",
        "DO $$BEGIN PERFORM pg_advisory_lock(4242); END$$",
        "COPY (SELECT 'rr18d') TO '/tmp/studio_r_h1_copy.txt'",
        "COMMIT",
    ):
        with pytest.raises(StatementRefused):
            engine.start(connection_id, f"-- x\r{statement} /*\nshow */")


@pytest.mark.postgres
@postgres
def test_pg_r_h1_check_in_closes_held_cursors_and_advisory_locks(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    connection = engine.registry.get(connection_id)
    # Past the text check: what a function of the database could leave on the session.
    _below_the_check(connection, "DECLARE studio_r_h1 CURSOR WITH HOLD FOR SELECT 1")
    _below_the_check(connection, "SELECT pg_advisory_lock(424242)")
    for _ in range(4):  # whichever of the pool's connections it lands on
        assert connection.fetch("SELECT count(*) FROM pg_cursors WHERE name = 'studio_r_h1'") == [(0,)]
    held = connection.fetch("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = 424242 AND granted")
    assert held == [(0,)]


@pytest.mark.postgres
@postgres
def test_pg_m1_a_held_cursor_is_refused(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    for sql in (
        "DECLARE c CURSOR WITH HOLD FOR SELECT pg_sleep(5)",
        "FETCH ALL FROM c",
        "CLOSE ALL",
        "EXPLAIN ANALYZE DECLARE c CURSOR FOR SELECT 1",
    ):
        with pytest.raises(StatementRefused):
            engine.start(connection_id, sql)


@pytest.mark.postgres
@postgres
def test_pg_h1_a_canceled_connection_leaves_the_pool(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    connection = engine.registry.get(connection_id)
    hook = Cancel()
    seen: list[int] = []

    def consume(cursor: tabrion_compat.ResultCursor) -> None:
        seen.append(cursor.fetch(1)[0][0])
        hook.cancel()  # a cancel sent while the statement is attached
        cursor.fetch(1000)

    statement = connection.prepare("SELECT pg_backend_pid(), pg_sleep(0.01) FROM generate_series(1, 1000)")
    with pytest.raises(Exception):  # noqa: B017 - Cancelled, or the server's query_canceled
        connection.stream(statement, consume, cancel=hook)
    canceled_pid = seen[0]
    later = {connection.fetch("SELECT pg_backend_pid()")[0][0] for _ in range(6)}
    assert canceled_pid not in later
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        count = connection.fetch("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid", {"pid": str(canceled_pid)})[
            0
        ][0]
        if count == 0:
            break
        threading.Event().wait(0.1)
    assert count == 0  # the backend is gone: no late cancel packet can reach anyone


@pytest.mark.postgres
@postgres
def test_pg_h1_a_late_cancel_never_hits_the_next_statement(pg: tuple[QueryEngine, str, Source]) -> None:
    engine, connection_id, _ = pg
    connection = engine.registry.get(connection_id)
    for delay in DELAYS[:40]:
        query = engine.start(connection_id, "SELECT pg_sleep(0.01)")
        threading.Event().wait(delay)
        engine.cancel(query.id)
        wait(query)
        assert connection.fetch("SELECT 42") == [(42,)]
