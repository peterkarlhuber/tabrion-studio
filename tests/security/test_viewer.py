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

from collections.abc import Iterator
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef
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
