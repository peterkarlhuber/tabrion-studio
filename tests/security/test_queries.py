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
from typing import Any
import uuid

import pytest
import sqlalchemy
from tabrion.errors import TabrionError

from tabrion_studio.config.schema import Profile, SecretRef, Settings
from tabrion_studio.logs import LogSetup, configure_logging
from tabrion_studio.services import queries as queries_module
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import ConnectionRegistry, RegistryError
from tabrion_studio.services.queries import Query, QueryEngine, QueryError, json_value
from tabrion_studio.services.readonly import StatementRefused
from tabrion_studio.services.sqltext import ParameterError, bind, placeholders
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
    with pytest.raises(StatementRefused):
        engine.start(connection_id, "COMMIT")
    query = wait(engine.start(connection_id, "DELETE FROM part"))
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
    query = wait(engine.start(connection_id, "CREATE TABLE studio_query_probe (x int)"))
    assert query.state == "failed" and query.problem is not None and query.problem.code == "db.read_only"
    # Past the text check, the server cursor's DECLARE is a prepared statement too: several commands are refused.
    escape = "SELECT '\\''; COMMIT; BEGIN READ WRITE; CREATE TABLE studio_query_probe (x int); COMMIT; SELECT ''''"
    connection = engine.registry.get(connection_id)
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
