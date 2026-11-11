"""STU-013: the data viewer pages one table with a ``LIMIT`` on every statement, by key, never by client values.

* Every statement the viewer sends has a ``LIMIT`` (a spy on :func:`tabrion_compat.stream`, 03 section 5.3).
* Keyset pages walk every row exactly once, ascending and descending, with ``NULL`` sort values last; ``rowid``
  (SQLite) and ``ctid`` (PostgreSQL) without a primary key, ``OFFSET`` for a view (with its hint).
* Filters are bound values; column names (sort, filter, cell) must be columns of the table, hostile ones included.
* The cell detail loads one value in full by the row's key, kept in the backend (the client sends only a token).
* The profile's timeout stops a statement (``db.timeout``); the routes sit behind the session check.

The SQLite tests always run; the PostgreSQL tests need ``TABRION_STUDIO_TEST_POSTGRES`` (see ``test_read_only.py``)
and create and drop their own schema.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef, Settings
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services import viewer as viewer_module
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.explorer import Explorer, ExplorerError
from tabrion_studio.services.viewer import Viewer, ViewerError, ViewerTimeout, parse_filters
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
HOSTILE = 'we"ird col; DROP TABLE item; --'
QUOTED = '"' + HOSTILE.replace('"', '""') + '"'
BIG = "x" * 100_000


def _items() -> list[tuple[int, str, float | None, str]]:
    # Repeated names and prices, and NULL prices, so that the key's later columns decide the order.
    return [(i, f"n{i % 13:02d}", None if i % 7 == 0 else float(i % 11), f"note {i}") for i in range(1, 251)]


@pytest.fixture
def lite(tmp_path: Path) -> Iterator[tuple[Viewer, str, Path]]:
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE odd (id INTEGER PRIMARY KEY, {QUOTED} TEXT)")
    connection.executescript(
        """
        CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL, note TEXT);
        CREATE TABLE loose (a TEXT, rowid TEXT);
        CREATE VIEW cheap AS SELECT name, price FROM item WHERE price < 5;
        CREATE VIEW endless AS
            WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c;
        """
    )
    connection.executemany("INSERT INTO item VALUES (?, ?, ?, ?)", _items())
    connection.executemany("INSERT INTO loose VALUES (?, ?)", [(f"a{i}", "shadow") for i in range(30)])
    connection.executemany("INSERT INTO odd VALUES (?, ?)", [(1, "50%_off"), (2, "5000 off"), (3, BIG)])
    connection.commit()
    connection.close()
    path.chmod(0o600)
    source = Source(
        profiles={"project:shop": Profile("shop", "sqlite", "dev", path="data/shop.db", statement_timeout_ms=800)},
        files={"project:shop": path},
    )
    registry = ConnectionRegistry(source)
    try:
        yield Viewer(Explorer(registry)), registry.open("project:shop").id, path
    finally:
        registry.close_all()


@pytest.fixture
def statements(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The driver text of every statement sent through :func:`tabrion_compat.stream`."""
    sent: list[str] = []
    real = tabrion_compat.stream

    @contextlib.contextmanager
    def spy(transaction: Any, sql: str, values: Any, **kwargs: Any) -> Iterator[Any]:
        sent.append(sql)
        with real(transaction, sql, values, **kwargs) as cursor:
            yield cursor

    monkeypatch.setattr(tabrion_compat, "stream", spy)
    return sent


def walk(
    viewer: Viewer, connection_id: str, schema: str, name: str, **options: Any
) -> tuple[list[list[Any]], set[str]]:
    """Every row of the table, page by page; also the paging modes seen."""
    page = viewer.rows(connection_id, schema, name, **options)
    rows, modes = list(page["rows"]), {page["paging"]}
    for _ in range(1000):  # a broken keyset could repeat pages forever
        if page["next"] is None:
            break
        page = viewer.rows(connection_id, schema, name, after=page["next"])
        rows += page["rows"]
        modes.add(page["paging"])
    return rows, modes


def expected(rows: list[tuple[Any, ...]], column: int | None, desc: bool) -> list[list[Any]]:
    """``ORDER BY column [DESC] NULLS LAST, id`` (``id`` the first column) in Python."""
    if column is None:
        return [list(row) for row in sorted(rows, key=lambda row: row[0])]
    present = sorted((row for row in rows if row[column] is not None), key=lambda row: row[0])
    present.sort(key=lambda row: row[column], reverse=desc)
    absent = sorted((row for row in rows if row[column] is None), key=lambda row: row[0])
    return [list(row) for row in present + absent]


@pytest.mark.parametrize("desc", [False, True])
@pytest.mark.parametrize(("sort", "column"), [(None, None), ("id", None), ("price", 2), ("name", 1)])
def test_keyset_pages_walk_every_row_once(
    lite: tuple[Viewer, str, Path], statements: list[str], sort: str | None, column: int | None, desc: bool
) -> None:
    viewer, connection_id, _ = lite
    rows, modes = walk(viewer, connection_id, "main", "item", limit=7, sort=sort, desc=desc)
    want = expected(_items(), column, desc)
    if sort == "id" and desc:
        want.reverse()
    assert rows == want
    assert modes == {"primary_key"}
    assert len(statements) == 36  # 250 rows in pages of 7
    assert all(" LIMIT " in f" {sql} ".replace("\n", " ") for sql in statements)
    assert not any("'" in sql for sql in statements)  # values are parameters, never literals


def test_rowid_pages_when_a_column_is_called_rowid(lite: tuple[Viewer, str, Path], statements: list[str]) -> None:
    viewer, connection_id, _ = lite
    first = viewer.rows(connection_id, "main", "loose", limit=8)
    assert (first["paging"], first["hint"]) == ("rowid", "no_primary_key")
    assert [column["name"] for column in first["columns"]] == ["a", "rowid"]
    rows, _ = walk(viewer, connection_id, "main", "loose", limit=8)
    assert rows == [[f"a{i}", "shadow"] for i in range(30)]
    assert all("_rowid_" in sql for sql in statements)
    assert viewer.cell(connection_id, "main", "loose", first["page"], 3, "a")["value"] == "a3"


def test_a_view_pages_by_offset_and_has_no_cell_detail(lite: tuple[Viewer, str, Path]) -> None:
    viewer, connection_id, _ = lite
    page = viewer.rows(connection_id, "main", "cheap", limit=10, sort="price")
    assert (page["paging"], page["hint"]) == ("offset", "unstable_order")
    rows, _ = walk(viewer, connection_id, "main", "cheap", limit=10, sort="price")
    assert sorted(row[1] for row in rows) == [row[1] for row in rows]
    assert len(rows) == sum(1 for item in _items() if item[2] is not None and item[2] < 5)
    with pytest.raises(ViewerError) as raised:
        viewer.cell(connection_id, "main", "cheap", page["page"], 0, "name")
    assert raised.value.code == "studio.viewer_cell_unavailable"


def test_filters_are_bound_values(lite: tuple[Viewer, str, Path], statements: list[str]) -> None:
    viewer, connection_id, _ = lite

    def ids(table: str, filters: list[dict[str, Any]]) -> list[Any]:
        rows, _ = walk(viewer, connection_id, "main", table, limit=50, filters=json.dumps(filters))
        return [row[0] for row in rows]

    every = _items()
    assert ids("item", [{"column": "price", "op": "is_null"}]) == [i for i, _, p, _ in every if p is None]
    assert ids(
        "item", [{"column": "price", "op": "ge", "value": 9}, {"column": "name", "op": "eq", "value": "n01"}]
    ) == [i for i, n, p, _ in every if p is not None and p >= 9 and n == "n01"]
    assert ids("item", [{"column": "price", "op": "eq", "value": None}]) == [i for i, _, p, _ in every if p is None]
    assert ids("item", [{"column": "note", "op": "starts_with", "value": "note 2"}]) == [
        i for i, *_, note in every if note.startswith("note 2")
    ]
    # % and _ are literal characters of a contains filter, and the column's name is taken as a name.
    assert ids("odd", [{"column": HOSTILE, "op": "contains", "value": "%_"}]) == [1]
    assert ids("odd", [{"column": HOSTILE, "op": "contains", "value": "' OR 1=1 --"}]) == []
    assert not any("OR 1=1" in sql or "%_" in sql for sql in statements)
    page = viewer.rows(connection_id, "main", "odd", sort=HOSTILE, desc=True)
    assert [row[0] for row in page["rows"]] == [3, 2, 1]


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        '[{"column": "price", "op": "between", "value": 1}]',
        '[{"column": "price", "op": "eq", "value": [1]}]',
        '[{"column": "price", "op": "eq", "value": {"$gt": 1}}]',
        '[{"column": "name", "op": "contains", "value": 5}]',
        '[{"column": "price", "op": "eq", "value": 1, "sql": "1=1"}]',
        '[{"column": 5, "op": "eq", "value": 1}]',
        json.dumps([{"column": "price", "op": "is_null"}] * 21),
        '[{"column": "price", "op": "eq", "value": NaN}]',
    ],
)
def test_malformed_filters_are_refused(raw: str) -> None:
    with pytest.raises(ViewerError) as raised:
        parse_filters(raw)
    assert raised.value.code == "studio.viewer_filter_invalid"


@pytest.mark.parametrize(
    "options",
    [
        {"sort": "nope"},
        {"sort": "price; DROP TABLE item"},
        {"sort": '"price"'},
        {"filters": [{"column": "rowid", "op": "eq", "value": 1}]},
        {"filters": [{"column": "item.price", "op": "is_null"}]},
    ],
)
def test_unknown_columns_are_refused(
    lite: tuple[Viewer, str, Path], statements: list[str], options: dict[str, Any]
) -> None:
    viewer, connection_id, _ = lite
    with pytest.raises(ViewerError) as raised:
        viewer.rows(connection_id, "main", "item", **options)
    assert raised.value.code == "studio.viewer_column_unknown"
    assert statements == []


def test_unknown_objects_and_limits_are_refused(lite: tuple[Viewer, str, Path]) -> None:
    viewer, connection_id, _ = lite
    for schema, name in [("main", "nope"), ("other", "item"), ("main", "item; --")]:
        with pytest.raises(ExplorerError) as missing:
            viewer.rows(connection_id, schema, name)
        assert missing.value.code == "studio.object_not_found"
    for limit in (0, 1001, True):
        with pytest.raises(ViewerError) as raised:
            viewer.rows(connection_id, "main", "item", limit=limit)
        assert raised.value.code == "studio.invalid_request"


def test_page_tokens_are_kept_in_the_backend(lite: tuple[Viewer, str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    viewer, connection_id, _ = lite
    page = viewer.rows(connection_id, "main", "item", limit=5)
    assert page["next"] == page["page"] and page["next"].startswith("p_")
    for token, table in [("p_made_up", "item"), (page["next"], "loose")]:
        with pytest.raises(ViewerError) as raised:
            viewer.rows(connection_id, "main", table, after=token)
        assert raised.value.code == "studio.viewer_page_unknown"
    last = viewer.rows(connection_id, "main", "odd")
    assert last["next"] is None
    with pytest.raises(ViewerError) as raised:
        viewer.rows(connection_id, "main", "odd", after=last["page"])
    assert raised.value.code == "studio.viewer_page_unknown"
    monkeypatch.setattr(viewer_module, "KEPT_PAGES", 2)
    viewer.rows(connection_id, "main", "item")
    viewer.rows(connection_id, "main", "item")
    with pytest.raises(ViewerError):
        viewer.rows(connection_id, "main", "item", after=page["next"])  # evicted


def test_the_cell_detail_loads_a_value_in_full(lite: tuple[Viewer, str, Path], statements: list[str]) -> None:
    viewer, connection_id, path = lite
    page = viewer.rows(connection_id, "main", "odd")
    assert page["truncated_cells"] == [[2, 1]]
    assert len(page["rows"][2][1]) < len(BIG)
    cell = viewer.cell(connection_id, "main", "odd", page["page"], 2, HOSTILE)
    assert cell == {"value": BIG, "size": cell["size"], "truncated": False}
    assert " LIMIT " in statements[-1].replace("\n", " ")
    for row, column, code in [
        (3, HOSTILE, "studio.invalid_request"),
        (-1, HOSTILE, "studio.invalid_request"),
        (0, "nope", "studio.viewer_column_unknown"),
    ]:
        with pytest.raises(ViewerError) as raised:
            viewer.cell(connection_id, "main", "odd", page["page"], row, column)
        assert raised.value.code == code
    writer = sqlite3.connect(path)
    writer.execute("DELETE FROM odd WHERE id = 3")
    writer.commit()
    writer.close()
    with pytest.raises(ViewerError) as gone:
        viewer.cell(connection_id, "main", "odd", page["page"], 2, HOSTILE)
    assert gone.value.code == "studio.viewer_row_gone"


def test_the_profile_timeout_stops_a_statement(lite: tuple[Viewer, str, Path]) -> None:
    viewer, connection_id, _ = lite
    started = time.monotonic()
    with pytest.raises(ViewerTimeout):
        viewer.rows(connection_id, "main", "endless", sort="x")  # sorting an endless view never ends
    assert time.monotonic() - started < 3.0  # 0.8 s timeout
    assert viewer.rows(connection_id, "main", "endless", limit=3)["rows"] == [[1], [2], [3]]


def test_the_viewer_routes(lite: tuple[Viewer, str, Path], tmp_path: Path) -> None:
    viewer, connection_id, _ = lite
    studio = make_studio(tmp_path / "static", connections=viewer.explorer.registry, viewer=viewer)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    client = studio.client
    base = f"/api/connections/{connection_id}/tables/main"
    filters = json.dumps([{"column": "price", "op": "is_null"}])
    first = client.get(f"{base}/item/rows", params={"limit": 3, "sort": "name", "filter": filters}, headers=headers)
    assert first.status_code == 200
    doc = first.json()
    nulls = sorted((name, i) for i, name, price, _ in _items() if price is None)
    assert [row[0] for row in doc["rows"]] == [i for _, i in nulls[:3]] and doc["sort"] == "name"
    second = client.get(f"{base}/item/rows", params={"after": doc["next"]}, headers=headers).json()
    assert second["limit"] == 3 and all(row[2] is None for row in second["rows"])
    odd = client.get(f"{base}/odd/rows", headers=headers).json()
    cell = client.get(f"{base}/odd/cell", params={"page": odd["page"], "row": 2, "column": HOSTILE}, headers=headers)
    assert cell.status_code == 200 and cell.json()["value"] == BIG
    refusals: list[tuple[str, dict[str, str | int], int, str]] = [
        ("item/rows", {"sort": "nope"}, 400, "studio.viewer_column_unknown"),
        ("item/rows", {"filter": "[1]"}, 400, "studio.viewer_filter_invalid"),
        ("item/rows", {"after": "p_nope"}, 404, "studio.viewer_page_unknown"),
        ("nope/rows", {}, 404, "studio.object_not_found"),
        ("cheap/cell", {"page": "p_nope", "row": 0, "column": "name"}, 404, "studio.viewer_page_unknown"),
        ("endless/rows", {"sort": "x"}, 504, "db.timeout"),
    ]
    for path, params, status, code in refusals:
        answer = client.get(f"{base}/{path}", params=params, headers=headers)
        assert (answer.status_code, answer.json()["code"]) == (status, code)
    assert client.get(f"{base}/item/rows", params={"limit": 0}, headers=headers).status_code == 422
    by_query = client.get(
        f"/api/connections/{connection_id}/rows", params={"schema": "main", "name": "odd"}, headers=headers
    ).json()
    assert by_query["rows"] == odd["rows"]
    full = client.get(
        f"/api/connections/{connection_id}/cell",
        params={"schema": "main", "name": "odd", "page": by_query["page"], "row": 2, "column": HOSTILE},
        headers=headers,
    )
    assert full.json()["value"] == BIG
    assert client.get("/api/connections/c_nope/tables/main/item/rows", headers=headers).json()["code"] == (
        "studio.connection_not_found"
    )
    assert client.get(f"{base}/item/rows").status_code in (401, 403)


@pytest.fixture
def pg() -> Iterator[tuple[Viewer, str]]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    statements = [
        'DROP SCHEMA IF EXISTS "Studio Viewer" CASCADE',
        'CREATE SCHEMA "Studio Viewer"',
        'CREATE TABLE "Studio Viewer".item (id int PRIMARY KEY, name text NOT NULL, price numeric, note text)',
        'CREATE TABLE "Studio Viewer".loose (a text, n int)',
        f'CREATE TABLE "Studio Viewer".odd (id int PRIMARY KEY, {QUOTED} text)',
        'CREATE VIEW "Studio Viewer".cheap AS SELECT name, price FROM "Studio Viewer".item WHERE price < 5',
        'CREATE MATERIALIZED VIEW "Studio Viewer".counted AS SELECT name, count(*) AS n FROM "Studio Viewer".item '
        "GROUP BY name",
        'CREATE VIEW "Studio Viewer".slow AS SELECT pg_sleep(10)::text AS s, 1 AS x',
        'CREATE TABLE "Studio Viewer".pair (a int, b int, v text, PRIMARY KEY (a, b))',
        'CREATE TABLE "Studio Viewer".stamped (id uuid PRIMARY KEY, at timestamptz, amount numeric)',
        'INSERT INTO "Studio Viewer".stamped SELECT md5(g::text)::uuid, '
        "timestamptz '2026-01-01' + g * interval '1 hour', g / 3.0 FROM generate_series(1, 50) g",
        # H-1: no primary key, rows of several relations (the review's probes).
        'CREATE TABLE "Studio Viewer".parted (k int, v text) PARTITION BY RANGE (k)',
        'CREATE TABLE "Studio Viewer".parted_1 PARTITION OF "Studio Viewer".parted FOR VALUES FROM (0) TO (100)',
        'CREATE TABLE "Studio Viewer".parted_2 PARTITION OF "Studio Viewer".parted FOR VALUES FROM (100) TO (200)',
        "INSERT INTO \"Studio Viewer\".parted SELECT g, 'p' || g FROM generate_series(1, 10) g",
        "INSERT INTO \"Studio Viewer\".parted SELECT 100 + g, 'p' || g FROM generate_series(1, 10) g",
        'CREATE TABLE "Studio Viewer".parent (k int, v text)',
        'CREATE TABLE "Studio Viewer".child () INHERITS ("Studio Viewer".parent)',
        "INSERT INTO \"Studio Viewer\".parent SELECT g, 'own' FROM generate_series(1, 10) g",
        "INSERT INTO \"Studio Viewer\".child SELECT g, 'child' FROM generate_series(11, 20) g",
        # H-2 and M-1: key types that do not come back from the driver as the database compares them.
        'CREATE TABLE "Studio Viewer".typed (id int PRIMARY KEY, r real, j jsonb, a int[], t json, p point, '
        "x xml, b bytea, long text)",
        "INSERT INTO \"Studio Viewer\".typed SELECT g, 0.1, jsonb_build_object('k', mod(g, 4)), ARRAY[mod(g, 3), 1], "
        "'{}', point(g, g), '<a/>', NULL, NULL FROM generate_series(1, 30) g",
    ]
    with admin.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            'INSERT INTO "Studio Viewer".item VALUES (%(id)s, %(name)s, %(price)s, %(note)s)',
            [{"id": i, "name": n, "price": p, "note": note} for i, n, p, note in _items()],
        )
        connection.exec_driver_sql(
            "INSERT INTO \"Studio Viewer\".loose SELECT 'a' || g, g FROM generate_series(1, 40) g"
        )
        connection.exec_driver_sql(
            "INSERT INTO \"Studio Viewer\".pair SELECT mod(g, 4), g, 'v' || g FROM generate_series(1, 30) g"
        )
        connection.exec_driver_sql(
            'INSERT INTO "Studio Viewer".odd VALUES (1, %(a)s), (2, %(b)s)', {"a": "50%_off", "b": "5000 off"}
        )
        connection.exec_driver_sql('REFRESH MATERIALIZED VIEW "Studio Viewer".counted')
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
    try:
        yield Viewer(Explorer(registry)), registry.open("user:pg").id
    finally:
        registry.close_all()
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA IF EXISTS "Studio Viewer" CASCADE')
        admin.dispose()


def _numbers(rows: list[list[Any]]) -> list[list[Any]]:
    # numeric comes back as text (exact decimals); compare as floats.
    return [[row[0], row[1], None if row[2] is None else float(row[2]), row[3]] for row in rows]


@pytest.mark.postgres
@postgres
@pytest.mark.parametrize("desc", [False, True])
@pytest.mark.parametrize(("sort", "column"), [(None, None), ("price", 2), ("name", 1)])
def test_pg_keyset_pages_walk_every_row_once(
    pg: tuple[Viewer, str], statements: list[str], sort: str | None, column: int | None, desc: bool
) -> None:
    viewer, connection_id = pg
    rows, modes = walk(viewer, connection_id, "Studio Viewer", "item", limit=9, sort=sort, desc=desc)
    # The names are plain ASCII letters and digits: every collation orders them as Python does.
    assert _numbers(rows) == expected(_items(), column, desc)
    assert modes == {"primary_key"}
    assert all(" LIMIT " in sql.replace("\n", " ") for sql in statements)


@pytest.mark.postgres
@postgres
def test_pg_composite_keys_ctid_offset_and_cells(pg: tuple[Viewer, str]) -> None:
    viewer, connection_id = pg
    schema = "Studio Viewer"
    # Keys of other types (uuid, timestamptz, numeric) go back to the driver as it returned them.
    rows, modes = walk(viewer, connection_id, schema, "stamped", limit=7, sort="at", desc=True)
    assert modes == {"primary_key"} and len(rows) == 50 and len({row[0] for row in rows}) == 50
    assert [row[1] for row in rows] == sorted((row[1] for row in rows), reverse=True)
    rows, _ = walk(viewer, connection_id, schema, "stamped", limit=7, sort="amount")
    assert len({row[0] for row in rows}) == 50
    rows, modes = walk(viewer, connection_id, schema, "pair", limit=4, sort="v", desc=True)
    assert modes == {"primary_key"} and [row[2] for row in rows] == sorted(
        (f"v{g}" for g in range(1, 31)), reverse=True
    )
    rows, modes = walk(viewer, connection_id, schema, "loose", limit=6)
    assert modes == {"ctid"} and sorted(row[1] for row in rows) == list(range(1, 41))
    rows, modes = walk(viewer, connection_id, schema, "counted", limit=4, sort="name")
    assert modes == {"ctid"} and [row[0] for row in rows] == sorted(f"n{i:02d}" for i in range(13))
    page = viewer.rows(connection_id, schema, "loose", limit=6, sort="n", desc=True)
    assert page["hint"] == "no_primary_key"
    assert viewer.cell(connection_id, schema, "loose", page["page"], 0, "a")["value"] == "a40"
    view = viewer.rows(connection_id, schema, "cheap", limit=5, sort="price")
    assert (view["paging"], view["hint"]) == ("offset", "unstable_order")
    filtered = viewer.rows(
        connection_id,
        schema,
        "item",
        filters=[{"column": "price", "op": "lt", "value": 2}, {"column": "id", "op": "le", "value": "40"}],
    )
    assert [row[0] for row in filtered["rows"]] == [i for i, _, p, _ in _items() if p is not None and p < 2 and i <= 40]
    odd = viewer.rows(connection_id, schema, "odd", filters=[{"column": HOSTILE, "op": "contains", "value": "%_"}])
    assert [row[0] for row in odd["rows"]] == [1]


@pytest.mark.postgres
@postgres
def test_pg_the_statement_timeout(pg: tuple[Viewer, str]) -> None:
    viewer, connection_id = pg
    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        viewer.rows(connection_id, "Studio Viewer", "slow")
    assert time.monotonic() - started < 4.0  # 1.5 s timeout
    assert isinstance(raised.value, ViewerTimeout) or getattr(raised.value, "code", None) == "db.timeout"
    assert viewer.rows(connection_id, "Studio Viewer", "item", limit=1)["rows"][0][0] == 1


# The fix round of stu-013-review.md: the reviewer's probes as tests.


def _sqlite(path: Path, *statements: str) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def test_nulls_in_a_sqlite_primary_key_lose_no_rows(lite: tuple[Viewer, str, Path]) -> None:
    """M-2: SQLite allows NULL in a primary key that is not the row id, in several rows."""
    viewer, connection_id, path = lite
    _sqlite(
        path,
        "CREATE TABLE nk (k TEXT PRIMARY KEY, v INT)",
        "INSERT INTO nk VALUES ('a', 1), (NULL, 2), (NULL, 3), ('b', 4), (NULL, 5)",
    )
    viewer.explorer.catalog(connection_id, refresh=True)
    for options in ({}, {"sort": "v"}, {"sort": "v", "desc": True}, {"sort": "k", "desc": True}):
        rows, modes = walk(viewer, connection_id, "main", "nk", limit=2, **options)
        assert modes == {"primary_key"}
        assert sorted(row[1] for row in rows) == [1, 2, 3, 4, 5], options
    first = viewer.rows(connection_id, "main", "nk", limit=5, sort="v")
    # A row with a NULL key is found again by its row id (and what the page showed of it).
    assert viewer.cell(connection_id, "main", "nk", first["page"], 2, "v")["value"] == 3
    _sqlite(path, "UPDATE nk SET v = 30 WHERE v = 3")
    with pytest.raises(ViewerError) as gone:
        viewer.cell(connection_id, "main", "nk", first["page"], 2, "v")
    assert gone.value.code == "studio.viewer_row_gone"
    # A row with its full key is found by the key: a changed value is the value now.
    _sqlite(path, "UPDATE nk SET v = 40 WHERE k = 'b'")
    assert viewer.cell(connection_id, "main", "nk", first["page"], 3, "v")["value"] == 40


def test_a_reused_rowid_is_not_another_rows_cell(lite: tuple[Viewer, str, Path]) -> None:
    """M-3: a rowid freed by a delete and taken by an insert does not show the new row under the old one."""
    viewer, connection_id, path = lite
    _sqlite(path, "CREATE TABLE notes (a TEXT)", "INSERT INTO notes VALUES ('one'), ('two'), ('secret-old')")
    viewer.explorer.catalog(connection_id, refresh=True)
    page = viewer.rows(connection_id, "main", "notes")
    assert page["paging"] == "rowid"
    assert viewer.cell(connection_id, "main", "notes", page["page"], 2, "a")["value"] == "secret-old"
    _sqlite(path, "DELETE FROM notes WHERE a = 'secret-old'", "INSERT INTO notes VALUES ('different-row')")
    with pytest.raises(ViewerError) as gone:
        viewer.cell(connection_id, "main", "notes", page["page"], 2, "a")
    assert gone.value.code == "studio.viewer_row_gone"
    assert viewer.cell(connection_id, "main", "notes", page["page"], 0, "a")["value"] == "one"


def test_bytes_are_capped_on_the_server(lite: tuple[Viewer, str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """M-4: lengths are measured before values are fetched; pages and cells have byte caps."""
    import tracemalloc

    viewer, connection_id, path = lite
    huge_text = "é" * (6 * 1024 * 1024)  # 12 MB of UTF-8, 6 M characters
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, t TEXT, b BLOB)")
    connection.execute("INSERT INTO big VALUES (1, ?, ?)", (huge_text, b"\x00" * (30 * 1024 * 1024)))
    connection.executemany("INSERT INTO big VALUES (?, ?, NULL)", [(i, "w" * 100_000) for i in range(2, 42)])
    connection.commit()
    connection.close()
    viewer.explorer.catalog(connection_id, refresh=True)
    tracemalloc.start()
    try:
        page = viewer.rows(connection_id, "main", "big", limit=1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024  # the 12 MB text and the 30 MB blob stay in the database
    assert page["truncated_cells"] == [[0, 1], [0, 2]]
    assert len(page["rows"][0][1].encode("utf-8")) <= 64 * 1024 and set(page["rows"][0][1]) == {"é"}
    assert len(page["rows"][0][2]["base64"]) <= 64 * 1024
    peaks = []
    for column in ("t", "b"):
        tracemalloc.start()
        try:
            cells = {column: viewer.cell(connection_id, "main", "big", page["page"], 0, column)}
            peaks.append(tracemalloc.get_traced_memory()[1])
        finally:
            tracemalloc.stop()
        text = cells.get("t", locals().get("text"))
        blob = cells.get("b")
    # The blob in full would be 30 MB, and its base64 40 MB more; 10 MB of it and its base64 are less than 40 MB.
    assert peaks[0] < 40 * 1024 * 1024 and peaks[1] < 40 * 1024 * 1024
    assert text is not None and blob is not None
    # 10 MB means bytes: of UTF-8 for text (a cut character is left out), of the value for bytes.
    assert text["truncated"] and text["size"] == len(huge_text.encode("utf-8"))
    assert 10 * 1024 * 1024 - 2 <= len(text["value"].encode("utf-8")) <= 10 * 1024 * 1024
    assert blob["truncated"] and blob["size"] == 30 * 1024 * 1024
    assert len(base64.b64decode(blob["value"]["base64"])) == 10 * 1024 * 1024
    # A page stops at the byte cap (max_result_mb) and the next page goes on from there.
    monkeypatch.setattr(viewer, "limits", lambda: Settings(max_result_mb=1))
    first = viewer.rows(connection_id, "main", "big", limit=1000, filters=[{"column": "id", "op": "gt", "value": 1}])
    assert first["truncated"] == "bytes" and 5 <= len(first["rows"]) < 40 and first["next"] is not None
    rows, _ = walk(viewer, connection_id, "main", "big", limit=1000, filters=[{"column": "id", "op": "gt", "value": 1}])
    assert [row[0] for row in rows] == list(range(2, 42))


def test_long_sort_keys_are_not_kept(lite: tuple[Viewer, str, Path]) -> None:
    """L-1: only the last row's key is kept, and a key too long to keep continues the walk with an OFFSET."""
    viewer, connection_id, path = lite
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE wide (id INTEGER PRIMARY KEY, s TEXT)")
    connection.executemany(
        "INSERT INTO wide VALUES (?, ?)", [(i, f"{(i * 7) % 23:02d}" + "z" * 70_000) for i in range(23)]
    )
    connection.commit()
    connection.close()
    viewer.explorer.catalog(connection_id, refresh=True)
    rows, _ = walk(viewer, connection_id, "main", "wide", limit=4, sort="s", desc=True)
    assert [row[0] for row in rows] == sorted(range(23), key=lambda i: (i * 7) % 23, reverse=True)
    page = viewer.rows(connection_id, "main", "wide", limit=4, sort="s")
    state = viewer._pages[page["page"]]
    assert state.anchor is None and state.skip == 4  # nothing of 70 KB kept
    # The row's identity is its primary key, which is short: the cell detail still finds the row.
    assert viewer.cell(connection_id, "main", "wide", page["page"], 0, "s")["value"].startswith("00z")
    short = viewer.rows(connection_id, "main", "item", limit=10, sort="name")
    # (name, id, rowid) of the last row only: the row id orders keys that SQLite lets be NULL (M-2).
    assert len(viewer._pages[short["page"]].anchor or ()) == 3


def test_page_tokens_belong_to_their_session_and_connection(
    lite: tuple[Viewer, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-4: a token works only in the session that got it; a closed connection drops its pages."""
    viewer, connection_id, _ = lite
    page = viewer.rows(connection_id, "main", "item", limit=5, owner="session-a")
    for owner in ("session-b", None):
        with pytest.raises(ViewerError) as raised:
            viewer.rows(connection_id, "main", "item", after=page["next"], owner=owner)
        assert raised.value.code == "studio.viewer_page_unknown"
        with pytest.raises(ViewerError):
            viewer.cell(connection_id, "main", "item", page["page"], 0, "name", owner=owner)
    assert viewer.rows(connection_id, "main", "item", after=page["next"], owner="session-a")["rows"]
    monkeypatch.setattr(viewer_module, "KEPT_PER_SESSION", 2)
    other = viewer.rows(connection_id, "main", "item", limit=5, owner="session-b")
    for _ in range(3):
        viewer.rows(connection_id, "main", "item", limit=5, owner="session-a")
    assert viewer.rows(connection_id, "main", "item", after=other["next"], owner="session-b")["rows"]
    with pytest.raises(ViewerError):  # pushed out by its own session's pages only
        viewer.rows(connection_id, "main", "item", after=page["next"], owner="session-a")
    kept = viewer.rows(connection_id, "main", "item", limit=5, owner="session-a")
    viewer.explorer.registry.close(connection_id)
    assert not any(p.connection_id == connection_id for p in viewer._pages.values())
    assert kept["page"] not in viewer._pages


def test_reflection_is_cached_and_statements_are_limited(
    lite: tuple[Viewer, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-5: no reflection per page while the cache is fresh; at most so many viewer statements at a time."""
    viewer, connection_id, _ = lite
    connection = viewer.explorer.registry.get(connection_id)
    calls: list[int] = []
    real = type(connection).inspect

    def counted(self: Any, consume: Any) -> Any:
        calls.append(1)
        return real(self, consume)

    monkeypatch.setattr(type(connection), "inspect", counted)
    page = viewer.rows(connection_id, "main", "item", limit=5)
    calls.clear()
    viewer.rows(connection_id, "main", "item", after=page["next"])
    viewer.cell(connection_id, "main", "item", page["page"], 0, "name")
    assert calls == []
    viewer.explorer.catalog(connection_id, refresh=True)  # a refresh drops it
    calls.clear()
    viewer.rows(connection_id, "main", "item", limit=5)
    assert len(calls) == 1  # the columns again (the refresh built the catalog)
    busy = Viewer(viewer.explorer, max_parallel=0)
    with pytest.raises(ViewerError) as raised:
        busy.rows(connection_id, "main", "item")
    assert raised.value.code == "studio.query_limit"
    single = Viewer(viewer.explorer, per_connection=1)
    inner: list[str] = []

    def other() -> None:
        try:
            single.rows(connection_id, "main", "odd")
        except ViewerError as exc:
            inner.append(exc.code)

    def nested(cursor: Any) -> Any:
        # A second request (another thread, like another tab) while this statement runs on the same connection.
        worker = threading.Thread(target=other)
        worker.start()
        worker.join(5)
        return cursor.fetch(2)

    statement: sqlalchemy.Select[Any] = sqlalchemy.select(sqlalchemy.literal_column("1")).limit(1)
    single._run(connection, statement, nested)
    assert inner == ["studio.query_limit"]
    assert single.rows(connection_id, "main", "odd")["rows"]  # the slot is free again


def test_the_routes_refuse_without_a_session_or_from_elsewhere(lite: tuple[Viewer, str, Path], tmp_path: Path) -> None:
    """I-1 and L-4: every form of both routes sits behind the session check; a token stays in its session."""
    viewer, connection_id, _ = lite
    studio = make_studio(tmp_path / "static", connections=viewer.explorer.registry, viewer=viewer)
    client = studio.client
    base = f"/api/connections/{connection_id}"
    forms: list[tuple[str, dict[str, str | int]]] = [
        (f"{base}/tables/main/item/rows", {}),
        (f"{base}/rows", {"schema": "main", "name": "item"}),
        (f"{base}/tables/main/item/cell", {"page": "p_x", "row": 0, "column": "name"}),
        (f"{base}/cell", {"schema": "main", "name": "item", "page": "p_x", "row": 0, "column": "name"}),
    ]
    for path, params in forms:
        assert client.get(path, params=params).status_code in (401, 403)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    for path, params in forms:
        answer = client.get(path, params=params, headers={**headers, "Origin": "https://elsewhere.example"})
        assert answer.status_code == 403
        answer = client.get(path, params=params, headers={**headers, "Sec-Fetch-Site": "cross-site"})
        assert answer.status_code == 403
    page = client.get(forms[0][0], params={"limit": 2}, headers=headers).json()
    assert client.get(forms[0][0], params={"after": page["next"]}, headers=headers).status_code == 200
    other = {**studio.sign_in(), "Origin": ORIGIN}  # a second session (its cookie replaces the first)
    answer = client.get(forms[0][0], params={"after": page["next"]}, headers=other)
    assert (answer.status_code, answer.json()["code"]) == (404, "studio.viewer_page_unknown")
    answer = client.get(forms[2][0], params={"page": page["page"], "row": 0, "column": "name"}, headers=other)
    assert answer.json()["code"] == "studio.viewer_page_unknown"


@pytest.mark.postgres
@postgres
def test_pg_partitions_and_inheritance_lose_no_rows(pg: tuple[Viewer, str], statements: list[str]) -> None:
    """H-1: the physical key is (tableoid, ctid); a ctid alone repeats across partitions and children."""
    viewer, connection_id = pg
    for name, want in [("parted", sorted([*range(1, 11), *range(101, 111)])), ("parent", list(range(1, 21)))]:
        for options in ({}, {"sort": "v"}, {"sort": "v", "desc": True}):
            rows, modes = walk(viewer, connection_id, "Studio Viewer", name, limit=3, **options)
            assert modes == {"ctid"}
            assert sorted(row[0] for row in rows) == want, (name, options)
    page = viewer.rows(connection_id, "Studio Viewer", "parted", limit=20)
    found = {viewer.cell(connection_id, "Studio Viewer", "parted", page["page"], i, "k")["value"] for i in range(20)}
    assert found == {*range(1, 11), *range(101, 111)}
    assert all(" LIMIT " in sql.replace("\n", " ") for sql in statements)


@pytest.mark.postgres
@postgres
def test_pg_keys_are_compared_in_their_own_type(pg: tuple[Viewer, str]) -> None:
    """H-2 and M-1: real, jsonb and array keys page to the end; json, xml and point are not sortable."""
    viewer, connection_id = pg
    for sort in ("r", "j", "a"):
        for desc in (False, True):
            rows, _ = walk(viewer, connection_id, "Studio Viewer", "typed", limit=5, sort=sort, desc=desc)
            assert sorted(row[0] for row in rows) == list(range(1, 31)), (sort, desc)
    rows, _ = walk(viewer, connection_id, "Studio Viewer", "typed", limit=4, sort="j")
    assert [row[2]["k"] for row in rows] == sorted(row[2]["k"] for row in rows)
    for sort in ("t", "p", "x"):
        with pytest.raises(ViewerError) as raised:
            viewer.rows(connection_id, "Studio Viewer", "typed", sort=sort)
        assert raised.value.code == "studio.viewer_sort_unsupported"
    page = viewer.rows(connection_id, "Studio Viewer", "typed", limit=3)
    assert page["columns"][5]["type"] == "point"  # a type SQLAlchemy does not know keeps its name


@pytest.mark.postgres
@postgres
def test_pg_a_reused_ctid_is_not_another_rows_cell(pg: tuple[Viewer, str]) -> None:
    """M-3: after a delete, VACUUM and an insert, the same ctid holds another row: row_gone, not its value."""
    viewer, connection_id = pg
    assert PG_URL is not None
    engine = sqlalchemy.create_engine(sqlalchemy.make_url(PG_URL).set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE "Studio Viewer".notes (a text)')
            connection.exec_driver_sql("""INSERT INTO "Studio Viewer".notes VALUES ('one'), ('two'), ('secret-old')""")
        viewer.explorer.catalog(connection_id, refresh=True)
        page = viewer.rows(connection_id, "Studio Viewer", "notes")
        assert viewer.cell(connection_id, "Studio Viewer", "notes", page["page"], 2, "a")["value"] == "secret-old"
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("""DELETE FROM "Studio Viewer".notes WHERE a = 'secret-old'""")
            connection.exec_driver_sql('VACUUM "Studio Viewer".notes')
            connection.exec_driver_sql("""INSERT INTO "Studio Viewer".notes VALUES ('different-row')""")
            reused = connection.exec_driver_sql(
                """SELECT ctid::text FROM "Studio Viewer".notes WHERE a = 'different-row'"""
            ).scalar()
        assert reused == "(0,3)"  # the probe's precondition: the slot was reused
        with pytest.raises(ViewerError) as gone:
            viewer.cell(connection_id, "Studio Viewer", "notes", page["page"], 2, "a")
        assert gone.value.code == "studio.viewer_row_gone"
        assert viewer.cell(connection_id, "Studio Viewer", "notes", page["page"], 1, "a")["value"] == "two"
    finally:
        engine.dispose()


@pytest.mark.postgres
@postgres
def test_pg_bytes_are_capped_on_the_server(pg: tuple[Viewer, str]) -> None:
    """M-4: a 30 MB bytea and a 12 MB text stay in the database for a page; the cell loads 10 MB of their bytes."""
    import tracemalloc

    viewer, connection_id = pg
    assert PG_URL is not None
    engine = sqlalchemy.create_engine(sqlalchemy.make_url(PG_URL).set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """UPDATE "Studio Viewer".typed SET b = decode(repeat('00', 30 * 1024 * 1024), 'hex'), """
                """long = repeat('é', 6 * 1024 * 1024) WHERE id = 1"""
            )
    finally:
        engine.dispose()
    tracemalloc.start()
    try:
        page = viewer.rows(connection_id, "Studio Viewer", "typed", limit=2)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024
    assert [0, 7] in page["truncated_cells"] and [0, 8] in page["truncated_cells"]
    blob = viewer.cell(connection_id, "Studio Viewer", "typed", page["page"], 0, "b")
    text = viewer.cell(connection_id, "Studio Viewer", "typed", page["page"], 0, "long")
    assert blob["truncated"] and blob["size"] == 30 * 1024 * 1024
    assert len(base64.b64decode(blob["value"]["base64"])) == 10 * 1024 * 1024
    assert text["truncated"] and text["size"] == 12 * 1024 * 1024
    assert 10 * 1024 * 1024 - 2 <= len(text["value"].encode("utf-8")) <= 10 * 1024 * 1024
    small = viewer.cell(connection_id, "Studio Viewer", "typed", page["page"], 1, "j")
    assert small == {"value": {"k": 2}, "size": len('{"k": 2}'), "truncated": False}


# The delta re-review of 4ad445a (stu-013-review.md, FINAL): N-1, H-2 residual and M-4 residual.

AT_SQL = "@1=1 OR 1"  # a primary key column whose name starts with "@" and reads as SQL


def test_n1_at_columns_in_a_sqlite_primary_key_are_columns(
    lite: tuple[Viewer, str, Path], statements: list[str]
) -> None:
    """N-1: a key column called "@..." is a quoted column, never a physical id or raw SQL."""
    viewer, connection_id, path = lite
    quoted_hostile = '"' + HOSTILE.replace('"', '""') + '"'
    _sqlite(
        path,
        f'CREATE TABLE inj ("{AT_SQL}" TEXT, k TEXT, {quoted_hostile} TEXT, PRIMARY KEY ("{AT_SQL}", k))',
        """INSERT INTO inj VALUES ('a', NULL, 'one'), ('b', NULL, 'two'), ('c', 'k', 'three')""",
        'CREATE TABLE at_pair ("@a" TEXT, b INT, v TEXT, PRIMARY KEY ("@a", b))',
        """INSERT INTO at_pair VALUES ('x', 1, 'mine'), ('y', 2, 'other')""",
        'CREATE TABLE at_only ("@a" TEXT NOT NULL, "@rowid" INT NOT NULL, v TEXT, PRIMARY KEY ("@a", "@rowid"))',
        """INSERT INTO at_only VALUES ('x', 1, 'mine'), ('x', 2, 'other')""",
    )
    viewer.explorer.catalog(connection_id, refresh=True)
    # NULLs in k: the cell is found by the full identity (key columns and the row id).
    page = viewer.rows(connection_id, "main", "inj")
    assert [row[2] for row in page["rows"]] == ["one", "two", "three"]
    statements.clear()
    assert viewer.cell(connection_id, "main", "inj", page["page"], 1, HOSTILE)["value"] == "two"
    assert viewer.cell(connection_id, "main", "inj", page["page"], 2, HOSTILE)["value"] == "three"
    for sql in statements:
        flat = sql.replace("\n", " ")
        assert f'"{AT_SQL}" = ' in flat and f"WHERE {AT_SQL[1:]}" not in flat and f" {AT_SQL[1:]} =" not in flat
        assert f".{quoted_hostile}" in flat or f" {quoted_hostile}" in flat
    # A deleted row whose key comes back with another "@a" value is not the old row's cell.
    page = viewer.rows(connection_id, "main", "at_pair")
    _sqlite(path, """DELETE FROM at_pair WHERE "@a" = 'x'""", """INSERT INTO at_pair VALUES ('z', 1, 'OTHER ROW')""")
    with pytest.raises(ViewerError) as gone:
        viewer.cell(connection_id, "main", "at_pair", page["page"], 0, "v")
    assert gone.value.code == "studio.viewer_row_gone"
    assert viewer.cell(connection_id, "main", "at_pair", page["page"], 1, "v")["value"] == "other"
    # Every key column starts with "@" (one even reads like the row id marker): the WHERE still has all of them.
    page = viewer.rows(connection_id, "main", "at_only", sort="v")
    assert [viewer.cell(connection_id, "main", "at_only", page["page"], i, "v")["value"] for i in (0, 1)] == [
        "mine",
        "other",
    ]
    rows, _ = walk(viewer, connection_id, "main", "at_only", limit=1, sort="@rowid", desc=True)
    assert [row[1] for row in rows] == [2, 1]


@pytest.mark.postgres
@postgres
def test_pg_n1_at_columns_in_a_primary_key_are_columns(pg: tuple[Viewer, str], statements: list[str]) -> None:
    """N-1 on PostgreSQL: the by-key cell uses every key column, "@..." ones included, and quotes them."""
    viewer, connection_id = pg
    assert PG_URL is not None
    engine = sqlalchemy.create_engine(sqlalchemy.make_url(PG_URL).set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'CREATE TABLE "Studio Viewer".at_pair ("@a" text, "{AT_SQL}" int, v text, '
                f'PRIMARY KEY ("@a", "{AT_SQL}"))'
            )
            connection.exec_driver_sql("""INSERT INTO "Studio Viewer".at_pair VALUES ('x', 1, 'mine'), ('y', 2, 'b')""")
        viewer.explorer.catalog(connection_id, refresh=True)
        page = viewer.rows(connection_id, "Studio Viewer", "at_pair")
        assert page["paging"] == "primary_key"
        statements.clear()
        assert viewer.cell(connection_id, "Studio Viewer", "at_pair", page["page"], 0, "v")["value"] == "mine"
        flat = statements[-1].replace("\n", " ")
        assert '"@a" = ' in flat and f'"{AT_SQL}" = ' in flat
        with engine.begin() as connection:
            connection.exec_driver_sql("""DELETE FROM "Studio Viewer".at_pair WHERE "@a" = 'x'""")
            connection.exec_driver_sql("""INSERT INTO "Studio Viewer".at_pair VALUES ('z', 1, 'OTHER ROW')""")
        with pytest.raises(ViewerError) as gone:
            viewer.cell(connection_id, "Studio Viewer", "at_pair", page["page"], 0, "v")
        assert gone.value.code == "studio.viewer_row_gone"
        rows, _ = walk(viewer, connection_id, "Studio Viewer", "at_pair", limit=1, sort=AT_SQL, desc=True)
        assert [row[1] for row in rows] == [2, 1]
    finally:
        engine.dispose()


@pytest.fixture
def low_float_digits(pg: tuple[Viewer, str]) -> Iterator[tuple[Viewer, str]]:
    """The database sets extra_float_digits = 0 (PostgreSQL 11's default; a role or database may set it), and a
    float8 sort column holds 30 equal values 0.1 + 0.2, whose text at 0 digits is "0.3"."""
    viewer, connection_id = pg
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    engine = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'ALTER DATABASE "{url.database}" SET extra_float_digits = 0')
            connection.exec_driver_sql('CREATE TABLE "Studio Viewer".floats (id int PRIMARY KEY, f float8)')
            connection.exec_driver_sql(
                'INSERT INTO "Studio Viewer".floats SELECT g, 0.1::float8 + 0.2::float8 FROM generate_series(1, 30) g'
            )
            # The precondition of the probe: a new session of the role does see 0 digits.
            with engine.connect() as fresh:
                assert fresh.exec_driver_sql("SHOW extra_float_digits").scalar() == "0"
        viewer.explorer.catalog(connection_id, refresh=True)
        yield viewer, connection_id
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'ALTER DATABASE "{url.database}" RESET extra_float_digits')
        engine.dispose()


@pytest.mark.postgres
@postgres
@pytest.mark.parametrize("desc", [False, True])
def test_pg_h2_float_keys_page_whatever_extra_float_digits_says(
    low_float_digits: tuple[Viewer, str], desc: bool
) -> None:
    """H-2 residual: Studio's sessions start with extra_float_digits=3, so the kept key text is the exact value."""
    viewer, connection_id = low_float_digits
    registry = viewer.explorer.registry
    registry.close(connection_id)  # a new session, after the database setting
    connection_id = registry.open("user:pg").id
    rows, _ = walk(viewer, connection_id, "Studio Viewer", "floats", limit=5, sort="f", desc=desc)
    assert sorted(row[0] for row in rows) == list(range(1, 31))
    assert len(rows) == 30


@pytest.mark.postgres
@postgres
def test_pg_h2_a_keyset_without_progress_goes_on_by_offset(
    low_float_digits: tuple[Viewer, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-2 residual, the progress guard: without the pin, "0.3" < 0.30000000000000004, so every keyset page would
    start again from the first row. The walk sees that its anchor did not advance and goes on by OFFSET, and it ends.
    Here (all values equal) every row comes exactly once; in general the guard only makes the walk end (docs/TODO.md).
    """
    viewer, connection_id = low_float_digits
    real = connections_module.startup_options
    monkeypatch.setattr(
        connections_module, "startup_options", lambda session: real(session).replace(" -c extra_float_digits=3", "")
    )
    registry = viewer.explorer.registry
    registry.close(connection_id)
    connection_id = registry.open("user:pg").id
    for limit in (5, 7):
        rows, _ = walk(viewer, connection_id, "Studio Viewer", "floats", limit=limit, sort="f")
        assert [row[0] for row in rows] == list(range(1, 31)), limit


def test_m4_a_small_first_row_does_not_size_the_next_batch(
    lite: tuple[Viewer, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-4 residual: the batch after a tiny first row is sized by the columns' byte caps, not by that row: the fetch
    stays near FETCH_BUDGET_BYTES (2 MB), and a page near max_result_mb (1 MB here)."""
    import tracemalloc

    viewer, connection_id, path = lite
    columns = ", ".join(f"c{i} TEXT" for i in range(10))
    _sqlite(path, f"CREATE TABLE wide10 (id INTEGER PRIMARY KEY, {columns})")
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO wide10 (id) VALUES (1)")  # the first row is tiny
    connection.executemany(
        "INSERT INTO wide10 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(i, *(chr(97 + j) * 60_000 for j in range(10))) for i in range(2, 80)],
    )
    connection.commit()
    connection.close()
    viewer.explorer.catalog(connection_id, refresh=True)
    monkeypatch.setattr(viewer, "limits", lambda: Settings(max_result_mb=1))
    sizes: list[int] = []
    real = tabrion_compat.ResultCursor.fetch

    def fetch(self: Any, size: int) -> Any:
        sizes.append(size)
        return real(self, size)

    monkeypatch.setattr(tabrion_compat.ResultCursor, "fetch", fetch)
    tracemalloc.start()
    try:
        page = viewer.rows(connection_id, "main", "wide10", limit=1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert page["truncated"] == "bytes" and page["next"] is not None
    # 11 columns at 64 KB each: 2 MB holds 2 such rows. Before the fix the batch after the first row was 500 rows
    # (here all 79, about 47 MB).
    assert max(sizes) <= 2, sizes
    assert peak < 8 * 1024 * 1024, peak


@pytest.mark.postgres
@postgres
def test_pg_r1_decoded_json_counts_toward_the_page_cap(pg: tuple[Viewer, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """R-1 of the re-review of 974b958: 60 KB of jsonb text like "[[], [], ...]" decode into about 1.3 MB of Python
    lists. The page cap counts that memory, and the fetch batch is sized for it, so a page stays near max_result_mb."""
    import tracemalloc

    viewer, connection_id = pg
    assert PG_URL is not None
    engine = sqlalchemy.create_engine(sqlalchemy.make_url(PG_URL).set(drivername="postgresql+psycopg"))
    columns = ", ".join(f"j{i} jsonb" for i in range(4))
    nested = "(SELECT jsonb_agg('[]'::jsonb) FROM generate_series(1, 15000))"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE TABLE "Studio Viewer".nested (id int PRIMARY KEY, {columns})')
            connection.exec_driver_sql('INSERT INTO "Studio Viewer".nested (id) VALUES (1)')  # the first row is tiny
            connection.exec_driver_sql(
                f'INSERT INTO "Studio Viewer".nested SELECT g, {nested}, {nested}, {nested}, {nested} '  # noqa: S608
                "FROM generate_series(2, 13) g"
            )
    finally:
        engine.dispose()
    viewer.explorer.catalog(connection_id, refresh=True)
    monkeypatch.setattr(viewer, "limits", lambda: Settings(max_result_mb=1))
    tracemalloc.start()
    try:
        page = viewer.rows(connection_id, "Studio Viewer", "nested", limit=1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 * 1024 * 1024, peak  # before: 8 rows of 5 MB decoded in one fetch
    assert page["truncated"] == "bytes" and len(page["rows"]) == 1  # the next row alone is more than 1 MB decoded
    assert page["truncated_cells"] == []  # the values are whole JSON, not cut
    rows, _ = walk(viewer, connection_id, "Studio Viewer", "nested", limit=1000)
    assert [row[0] for row in rows] == list(range(1, 14))
    assert all(len(row[1]) == 15000 for row in rows[1:])
