"""STU-021: export of a query result or a table as CSV or JSON, streamed.

* Acceptance (09 backlog): 1 million rows without a memory peak above 100 MB (tracemalloc), CSV and JSON.
* The statement check, the parameters and the object check apply before the first byte (problems); limits: rows
  (JSON says ``truncated``), one export per connection, a client that goes away stops the export and frees its slot.
* CSV: RFC 4180 quoting, NULL as the empty field, bytes as base64, a formula-like text gets a leading ``'``.
* JSON: valid JSON with rows as arrays, bytes as ``{"base64"}``, NaN as text.
* The route sits behind the session and the CSRF check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, MutableMapping
import csv
import datetime
import decimal
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import tracemalloc
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services import exports as exports_module
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.exports import ExportError, Exports
from tabrion_studio.services.readonly import StatementRefused
from tests.security.test_read_only import Source
from tests.support.access import HOST, ORIGIN, PORT, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
MILLION = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 1000000) "
    "SELECT x AS id, 'part number ' || x AS name, x * 1.5 AS price FROM c"
)


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[tuple[Exports, ConnectionRegistry, str]]:
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE part (id INTEGER PRIMARY KEY, name TEXT, price REAL, data BLOB);"
        "CREATE TABLE seq_like (x INTEGER);"
        'CREATE TABLE "a""b" ("c""d" TEXT); INSERT INTO "a""b" VALUES (\'x"y\');'
        'CREATE TABLE "..odd""name;\r\n" (v INTEGER); INSERT INTO "..odd""name;\r\n" VALUES (1);'
    )
    connection.executemany(
        "INSERT INTO part VALUES (?, ?, ?, ?)",
        [
            (1, "bolt", 0.5, b"\x00\x01"),
            (2, '=HYPERLINK("http://evil.example")', None, None),
            (3, 'quote " and, comma\nnewline', float("nan"), None),
            (4, "-2+3", 2.0, None),
        ],
    )
    connection.commit()
    connection.close()
    path.chmod(0o600)
    source = Source(
        profiles={"project:shop": Profile("shop", "sqlite", "dev", path="shop.db", statement_timeout_ms=None)},
        files={"project:shop": path},
    )
    registry = ConnectionRegistry(source)
    try:
        yield Exports(registry, Explorer(registry)), registry, registry.open("project:shop").id
    finally:
        registry.close_all()


def drain(chunks: Iterator[bytes]) -> tuple[int, int, bytes]:
    """Bytes and line count of the export, keeping only its last 4 KB (the test must not hold the export either)."""
    size, lines, tail = 0, 0, b""
    for chunk in chunks:
        size += len(chunk)
        lines += chunk.count(b"\n")
        tail = (tail + chunk)[-4096:]
    return size, lines, tail


@pytest.mark.parametrize("export_format", ["csv", "json"])
def test_a_million_rows_without_a_peak_above_100_mb(
    shop: tuple[Exports, ConnectionRegistry, str], export_format: str
) -> None:
    exports, _, connection_id = shop
    tracemalloc.start()
    try:
        export = exports.start(connection_id, export_format, sql=MILLION)
        size, lines, tail = drain(export.chunks())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert lines == 1_000_001 if export_format == "csv" else lines >= 1_000_001
    assert size > 30_000_000  # the whole export went through
    if export_format == "json":
        assert tail.endswith(b'"truncated":null}\n') and b"[1000000," in tail
    assert peak < 100 * 1024 * 1024, peak


def test_csv_quoting_nulls_bytes_and_formulas(shop: tuple[Exports, ConnectionRegistry, str]) -> None:
    exports, _, connection_id = shop
    text = b"".join(exports.start(connection_id, "csv", table={"schema": "main", "name": "part"}).chunks())
    rows = list(csv.reader(io.StringIO(text.decode("utf-8"))))
    assert rows[0] == ["id", "name", "price", "data"]
    assert rows[1] == ["1", "bolt", "0.5", "AAE="]
    assert rows[2][1] == '\'=HYPERLINK("http://evil.example")' and rows[2][2] == "" and rows[2][3] == ""
    assert rows[3][1] == 'quote " and, comma\nnewline'
    assert rows[4][1] == "'-2+3"
    raw = b"".join(
        exports.start(connection_id, "csv", table={"schema": "main", "name": "part"}, escape_formulas=False).chunks()
    )
    assert list(csv.reader(io.StringIO(raw.decode())))[2][1].startswith("=HYPERLINK")


def test_numbers_are_never_escaped_as_formulas(shop: tuple[Exports, ConnectionRegistry, str]) -> None:
    exports, _, connection_id = shop
    text = b"".join(
        exports.start(connection_id, "csv", sql="SELECT -5 AS n, -0.5 AS f, '-5' AS t, X'FB' AS b").chunks()
    )
    assert list(csv.reader(io.StringIO(text.decode()))) == [["n", "f", "t", "b"], ["-5", "-0.5", "'-5", "'+w=="]]


#: The OWASP formula starts and their fullwidth forms (F-3 of stu-021-review.md), listed here, not taken from the code.
FORMULA_STARTS = ["=", "+", "-", "@", "\t", "\r", "\uff1d", "\uff0b", "\uff0d", "\uff20"]


@pytest.mark.parametrize("prefix", FORMULA_STARTS)
def test_every_formula_start_is_escaped_in_values_and_names(
    shop: tuple[Exports, ConnectionRegistry, str], prefix: str
) -> None:
    exports, _, connection_id = shop
    value = prefix + "1+1"
    sql = f'SELECT :v AS v, 1 AS "{prefix}x"'
    export = exports.start(connection_id, "csv", sql=sql, params={"v": value})
    assert list(csv.reader(io.StringIO(b"".join(export.chunks()).decode()))) == [
        ["v", "'" + prefix + "x"],
        ["'" + value, "1"],
    ]
    raw = exports.start(connection_id, "csv", sql=sql, params={"v": value}, escape_formulas=False)
    assert list(csv.reader(io.StringIO(b"".join(raw.chunks()).decode())))[1][0] == value


def test_quoted_names_and_a_safe_file_name(shop: tuple[Exports, ConnectionRegistry, str]) -> None:
    exports, _, connection_id = shop
    export = exports.start(connection_id, "csv", table={"schema": "main", "name": 'a"b'})
    assert list(csv.reader(io.StringIO(b"".join(export.chunks()).decode()))) == [['c"d'], ['x"y']]
    assert export.filename == "a_b.csv"
    odd = exports.start(connection_id, "json", table={"schema": "main", "name": '..odd"name;\r\n'})
    assert json.loads(b"".join(odd.chunks()))["rows"] == [[1]]
    assert odd.filename == "odd_name_.json"


def test_non_finite_and_large_numbers_and_intervals() -> None:
    def refuse(name: str) -> None:
        raise AssertionError(f"bare {name} in the JSON")

    encoder = exports_module._Encoder("json", ["a"], escape_formulas=True)
    row = [
        float("nan"), float("inf"), float("-inf"), decimal.Decimal("NaN"), decimal.Decimal("-Infinity"),
        2**53, -(2**53) + 1, {"big": 2**70, "inf": float("inf")},
        datetime.timedelta(days=428, hours=4, minutes=5, seconds=6), datetime.timedelta(days=-1, hours=22),
    ]  # fmt: skip
    encoder.head()
    encoder.row(row)
    encoder.tail(False)
    doc = json.loads(encoder.take() or b"", parse_constant=refuse)
    assert doc["rows"] == [
        ["NaN", "Infinity", "-Infinity", "NaN", "-Infinity", str(2**53), -(2**53) + 1,
         {"big": 2**70, "inf": "Infinity"}, "P428DT4H5M6S", "PT-2H"],
    ]  # fmt: skip
    text = exports_module._Encoder("csv", ["a"], escape_formulas=True)
    text.row([float("nan"), float("-inf"), datetime.timedelta(days=-1), -5])
    assert text.take() == b"NaN,-Infinity,P-1D,-5\r\n"


def test_two_exports_at_a_time(tmp_path: Path) -> None:
    path = tmp_path / "many.db"
    sqlite3.connect(path).close()
    path.chmod(0o600)
    names = ("project:a", "project:b", "project:c")
    profiles = {name: Profile(name[8:], "sqlite", "dev", path="many.db", statement_timeout_ms=None) for name in names}
    registry = ConnectionRegistry(Source(profiles=profiles, files=dict.fromkeys(names, path)))
    try:
        exports = Exports(registry, Explorer(registry))
        ids = [registry.open(name).id for name in names]
        running = [exports.start(ids[0], "csv", sql=MILLION), exports.start(ids[1], "csv", sql=MILLION)]
        for export in running:
            next(export.chunks())
        with pytest.raises(ExportError) as raised:
            exports.start(ids[2], "csv", sql="SELECT 1")
        assert raised.value.code == "studio.export_busy"
        for export in running:
            export.close()
        deadline = time.monotonic() + 5
        while exports._running and time.monotonic() < deadline:
            time.sleep(0.05)
        assert b"".join(exports.start(ids[2], "csv", sql="SELECT 1 AS one").chunks()) == b"one\r\n1\r\n"
    finally:
        registry.close_all()


def test_json_is_valid_and_keeps_types(shop: tuple[Exports, ConnectionRegistry, str]) -> None:
    exports, _, connection_id = shop
    doc = json.loads(
        b"".join(exports.start(connection_id, "json", sql="SELECT * FROM part ORDER BY id", max_rows=3).chunks())
    )
    assert doc["columns"] == ["id", "name", "price", "data"] and doc["truncated"] == "rows"
    assert doc["rows"][0] == [1, "bolt", 0.5, {"base64": "AAE="}]
    assert doc["rows"][1][1].startswith("=HYPERLINK")  # JSON is data, not a spreadsheet
    assert len(doc["rows"]) == 3
    full = json.loads(b"".join(exports.start(connection_id, "json", sql="SELECT 1 AS a, 2 AS a").chunks()))
    assert full == {"columns": ["a", "a"], "rows": [[1, 2]], "truncated": None}


def test_parameters_and_refusals_come_before_the_first_byte(shop: tuple[Exports, ConnectionRegistry, str]) -> None:
    exports, _, connection_id = shop
    export = exports.start(connection_id, "json", sql="SELECT name FROM part WHERE id = :id", params={"id": 1})
    found = json.loads(b"".join(export.chunks()))
    assert found["rows"] == [["bolt"]]
    with pytest.raises(StatementRefused):
        exports.start(connection_id, "csv", sql="DELETE FROM part")
    for bad in (
        {"sql": "SELECT 1", "table": {"schema": "main", "name": "part"}},
        {},
        {"sql": "SELECT 1", "max_rows": 1_000_001},
        {"sql": "SELECT 1", "max_rows": True},
        {"table": {"schema": "main"}},
    ):
        with pytest.raises(ExportError) as raised:
            exports.start(connection_id, "csv", **bad)
        assert raised.value.code == "studio.export_invalid"
    with pytest.raises(ExportError):
        exports.start(connection_id, "xml", sql="SELECT 1")
    with pytest.raises(Exception) as missing:
        exports.start(connection_id, "csv", table={"schema": "main", "name": "nope"})
    assert getattr(missing.value, "code", None) == "studio.object_not_found"


def test_one_export_per_connection_and_closing_frees_it(shop: tuple[Exports, ConnectionRegistry, str]) -> None:
    exports, _, connection_id = shop
    first = exports.start(connection_id, "csv", sql=MILLION)
    next(first.chunks())  # running, and waiting for the client
    with pytest.raises(ExportError) as raised:
        exports.start(connection_id, "csv", sql="SELECT 1")
    assert raised.value.code == "studio.export_busy"
    first.close()  # the client went away
    deadline = time.monotonic() + 5
    while exports._running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not exports._running
    assert b"".join(exports.start(connection_id, "csv", sql="SELECT 1 AS one").chunks()) == b"one\r\n1\r\n"
    assert not [thread for thread in threading.enumerate() if thread.name == "studio-export"]


@pytest.fixture
def held_worker(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[threading.Event, threading.Event]]:
    """Holds an export worker at the start of its last step (``timer.cancel()`` in ``_produce``'s ``finally``),
    after the client could already see the end or the error of its export, until the test opens the gate (at most
    10 s): ``(held, gate)``. A deterministic stand-in for a worker that is slow to finish (TD-057)."""
    held, gate = threading.Event(), threading.Event()

    class Timer(threading.Timer):
        def cancel(self) -> None:
            if threading.current_thread().name == "studio-export" and not gate.is_set():
                held.set()
                gate.wait(10)
            super().cancel()

    monkeypatch.setattr(threading, "Timer", Timer)
    try:
        yield held, gate
    finally:
        gate.set()


def test_td057_a_finished_export_frees_its_slot_before_the_client_sees_its_end(
    shop: tuple[Exports, ConnectionRegistry, str], held_worker: tuple[threading.Event, threading.Event]
) -> None:
    """The next export on the same connection may start as soon as the previous one was read to its end, even while
    that export's worker is still finishing (it was ``studio.export_busy`` in that gap); the earlier worker's end
    never frees the later export's slot, and two exports at once on one connection are still refused."""
    exports, _, connection_id = shop
    held, gate = held_worker
    first = exports.start(connection_id, "csv", sql="SELECT 1 AS one")
    assert b"".join(first.chunks()) == b"one\r\n1\r\n"
    assert held.wait(5)  # the first worker has not finished yet
    second = exports.start(connection_id, "csv", sql=MILLION)
    next(second.chunks())  # running, and waiting for the client
    gate.set()
    assert first._done.wait(5)
    assert exports._running == {connection_id: second}
    with pytest.raises(ExportError) as raised:
        exports.start(connection_id, "csv", sql="SELECT 1")
    assert raised.value.code == "studio.export_busy"
    second.close()
    assert second._done.wait(5)
    assert not exports._running


def test_td057_a_failed_export_frees_its_slot_before_the_client_sees_its_error(
    shop: tuple[Exports, ConnectionRegistry, str], held_worker: tuple[threading.Event, threading.Event]
) -> None:
    exports, _, connection_id = shop
    held, gate = held_worker
    failing = exports.start(connection_id, "csv", sql="SELECT abs(-9223372036854775807 - 1) AS x")
    with pytest.raises(Exception) as failed:
        b"".join(failing.chunks())
    assert not isinstance(failed.value, ExportError)  # the database's error, not a stop
    assert held.wait(5)  # the failed worker has not finished yet
    assert b"".join(exports.start(connection_id, "csv", sql="SELECT 1 AS one").chunks()) == b"one\r\n1\r\n"
    gate.set()
    assert failing._done.wait(5)
    assert not exports._running


def test_the_timeout_stops_an_export(
    shop: tuple[Exports, ConnectionRegistry, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exports, _, connection_id = shop
    monkeypatch.setattr(exports_module, "EXPORT_TIMEOUT_S", 0.5)
    export = exports.start(connection_id, "csv", sql=MILLION)
    chunks = export.chunks()
    next(chunks)
    time.sleep(1.0)  # a client that reads too slowly
    with pytest.raises(ExportError) as raised:
        for _ in chunks:
            time.sleep(0.01)
    assert raised.value.code == "studio.export_stopped"


def test_the_export_route(shop: tuple[Exports, ConnectionRegistry, str], tmp_path: Path) -> None:
    exports, registry, connection_id = shop
    studio = make_studio(tmp_path / "static", connections=registry, exports=exports)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    body = {"connection_id": connection_id, "format": "csv", "table": {"schema": "main", "name": "part"}}
    answer = studio.client.post("/api/exports", json=body, headers=headers)
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/csv")
    assert answer.headers["content-disposition"] == 'attachment; filename="part.csv"'
    assert answer.headers["cache-control"] == "no-store"
    assert answer.text.splitlines()[0] == "id,name,price,data"
    refused = studio.client.post(
        "/api/exports",
        json={"connection_id": connection_id, "format": "csv", "sql": "DROP TABLE part"},
        headers=headers,
    )
    assert refused.json()["code"].startswith("studio.statement")
    assert "DROP" not in refused.text
    invalid = studio.client.post("/api/exports", json={"connection_id": connection_id, "format": "x"}, headers=headers)
    assert (invalid.status_code, invalid.json()["code"]) == (400, "studio.export_invalid")
    gone = studio.client.post("/api/exports", json={**body, "connection_id": "c_nope"}, headers=headers)
    assert gone.json()["code"] == "studio.connection_not_found"
    assert studio.client.post("/api/exports", json=body).status_code in (401, 403)
    # An error on the first row is a problem document, not a 200 with a cut file (the first fetch comes first).
    failing = studio.client.post(
        "/api/exports",
        json={"connection_id": connection_id, "format": "csv", "sql": "SELECT abs(-9223372036854775808) AS x"},
        headers=headers,
    )
    assert failing.status_code != 200 and failing.json()["code"].startswith("db.")
    assert not exports._running
    with studio.client.stream("POST", "/api/exports", json={**body, "sql": MILLION, "table": None}, headers=headers):
        pass  # the client reads the head and goes away
    deadline = time.monotonic() + 5
    while exports._running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not exports._running


#: Measures an export in a fresh process by its resident memory, which also counts what tracemalloc does not see
#: (libpq's result buffers, SQLite's pages): the peak (VmHWM, reset after a warm-up) minus the size before.
RSS_PROBE = r"""
import json, sys
from pathlib import Path
from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.exports import Exports
from tests.security.test_read_only import Source
import sqlalchemy

job = json.loads(sys.stdin.read())
if job["engine"] == "sqlite":
    profile = Profile("shop", "sqlite", "dev", path="shop.db", statement_timeout_ms=None)
    source = Source(profiles={"p": profile}, files={"p": Path(job["target"])})
else:
    url = sqlalchemy.make_url(job["target"])
    profile = Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username,
        password=SecretRef("keyring", ""), sslmode="prefer", statement_timeout_ms=120_000,
    )
    source = Source(profiles={"p": profile}, passwords={"p": url.password or ""})
registry = ConnectionRegistry(source)
exports = Exports(registry, Explorer(registry))
connection_id = registry.open("p").id


def memory(name):
    line = next(l for l in Path("/proc/self/status").read_text().splitlines() if l.startswith(name + ":"))
    return int(line.split()[1]) * 1024


for _ in exports.start(connection_id, job["format"], sql=job["warm_up"]).chunks():
    pass
before = memory("VmRSS")
Path("/proc/self/clear_refs").write_text("5")  # the peak starts again from here
size = sum(len(chunk) for chunk in exports.start(connection_id, job["format"], sql=job["sql"]).chunks())
print(json.dumps({"size": size, "peak": memory("VmHWM") - before}))
registry.close_all()
"""
#: F-1 of stu-021-review.md: 1011 narrow rows (the batch grows), then 589 rows of 400 KB.
WIDE_AFTER_NARROW = {
    "sqlite": (
        "WITH RECURSIVE g(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 1600) "
        "SELECT n, CASE WHEN n <= 1011 THEN '' ELSE hex(zeroblob(200000)) END AS v FROM g"
    ),
    "postgresql": (
        "SELECT g AS n, CASE WHEN g <= 1011 THEN '' ELSE repeat('x', 400000) END AS v FROM generate_series(1, 1600) g"
    ),
}
linux = pytest.mark.skipif(not Path("/proc/self/clear_refs").exists(), reason="needs Linux /proc")


def rss_export(engine: str, target: str, export_format: str, sql: str) -> dict[str, int]:
    job = {"engine": engine, "target": target, "format": export_format, "sql": sql, "warm_up": "SELECT 1 AS one"}
    done = subprocess.run(
        [sys.executable, "-c", RSS_PROBE],
        input=json.dumps(job).encode(),
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=300,
        check=True,
    )
    result: dict[str, int] = json.loads(done.stdout.decode().strip().splitlines()[-1])
    return result


@linux
@pytest.mark.parametrize("export_format", ["csv", "json"])
def test_wide_rows_after_narrow_ones_stay_under_100_mb(
    shop: tuple[Exports, ConnectionRegistry, str], tmp_path: Path, export_format: str
) -> None:
    measured = rss_export("sqlite", str(tmp_path / "shop.db"), export_format, WIDE_AFTER_NARROW["sqlite"])
    assert measured["size"] > 589 * 400_000  # the whole export went through
    assert measured["peak"] < 100 * 1024 * 1024, measured


def test_an_export_fetch_is_small_after_narrow_rows() -> None:
    assert exports_module.next_export_batch(1, 1) == 2
    assert exports_module.next_export_batch(500, 10) == exports_module.EXPORT_BATCH_ROWS == 32
    assert exports_module.next_export_batch(32, 4 * 1024 * 1024) == 1


@pytest.mark.postgres
@pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
@linux
@pytest.mark.parametrize("export_format", ["csv", "json"])
def test_pg_wide_rows_after_narrow_ones_stay_under_100_mb(export_format: str) -> None:
    assert PG_URL is not None
    measured = rss_export("postgresql", PG_URL, export_format, WIDE_AFTER_NARROW["postgresql"])
    assert measured["size"] > 589 * 400_000
    assert measured["peak"] < 100 * 1024 * 1024, measured


def test_a_client_that_goes_away_stops_the_export(
    shop: tuple[Exports, ConnectionRegistry, str], tmp_path: Path
) -> None:
    """The ASGI app driven directly, so the disconnect reaches it (the test client reads every response in full)."""
    exports, registry, connection_id = shop
    studio = make_studio(tmp_path / "static", connections=registry, exports=exports)
    csrf = studio.sign_in()
    cookies = "; ".join(f"{name}={value}" for name, value in studio.client.cookies.items())
    body = json.dumps({"connection_id": connection_id, "format": "csv", "sql": MILLION}).encode()
    headers = [
        (b"host", HOST.encode()),
        (b"origin", ORIGIN.encode()),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cookie", cookies.encode()),
        *((name.lower().encode(), value.encode()) for name, value in csrf.items()),
    ]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/exports", "raw_path": b"/api/exports", "query_string": b"", "root_path": "",
        "headers": headers, "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", PORT),
    }  # fmt: skip
    messages: list[dict[str, Any]] = []

    async def drive() -> None:
        streaming = asyncio.Event()
        asked = False

        async def receive() -> dict[str, Any]:
            nonlocal asked
            if not asked:
                asked = True
                return {"type": "http.request", "body": body, "more_body": False}
            await streaming.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            messages.append(dict(message))
            if message["type"] == "http.response.body":
                streaming.set()

        await asyncio.wait_for(studio.client.app(scope, receive, send), 30)

    asyncio.run(drive())
    assert messages[0]["type"] == "http.response.start" and messages[0]["status"] == 200
    deadline = time.monotonic() + 5
    while exports._running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not exports._running


@pytest.mark.postgres
@pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
def test_pg_a_server_side_cursor_export(tmp_path: Path) -> None:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    profile = Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username,
        password=SecretRef("keyring", ""), sslmode="prefer", statement_timeout_ms=60_000,
    )  # fmt: skip
    registry = ConnectionRegistry(Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""}))
    try:
        exports = Exports(registry, Explorer(registry))
        connection_id = registry.open("user:pg").id
        sql = (
            "SELECT g AS id, repeat('x', 20) AS name, now() AS at, gen_random_uuid() AS u "
            "FROM generate_series(1, 300000) g"
        )
        tracemalloc.start()
        try:
            _, lines, tail = drain(exports.start(connection_id, "csv", sql=sql).chunks())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert lines == 300_001 and tail.split(b"\r\n")[-2].startswith(b"300000,")
        assert peak < 60 * 1024 * 1024, peak
        typed = exports.start(connection_id, "json", sql="SELECT 1.5::numeric AS n, '{\"a\": [1]}'::jsonb AS j")
        doc: dict[str, Any] = json.loads(b"".join(typed.chunks()))
        assert doc["rows"] == [["1.5", {"a": [1]}]]
        with pytest.raises(StatementRefused):
            exports.start(connection_id, "csv", sql="COPY (SELECT 1) TO STDOUT")
    finally:
        registry.close_all()


@pytest.mark.postgres
@pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
def test_pg_f1_the_export_route_refuses_statements_that_create_a_table(tmp_path: Path) -> None:
    """F-1 of the STU-016 review, through ``POST /api/exports`` with ``sql``: refused, and no table exists."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    profile = Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username,
        password=SecretRef("keyring", ""), sslmode="prefer",
    )  # fmt: skip
    registry = ConnectionRegistry(Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""}))
    name = f"studio_into_{os.urandom(4).hex()}"
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        exports = Exports(registry, Explorer(registry))
        connection_id = registry.open("user:pg").id
        studio = make_studio(tmp_path / "static", connections=registry, exports=exports)
        headers = {**studio.sign_in(), "Origin": ORIGIN}
        for sql in (
            f"explain analyze select 'x' as v into {name}",
            f"EXPLAIN ANALYZE SELECT 'x' AS v INTO TEMPORARY {name}",
            f"SELECT 'x' AS v INTO {name}",
        ):
            with pytest.raises(StatementRefused):
                exports.start(connection_id, "csv", sql=sql)
            answer = studio.client.post(
                "/api/exports", json={"connection_id": connection_id, "format": "csv", "sql": sql}, headers=headers
            )
            assert answer.status_code == 400 and answer.json()["code"] == "studio.statement_refused", answer.text
        with admin.connect() as c:
            found = c.exec_driver_sql("SELECT count(*) FROM pg_catalog.pg_class WHERE relname = %s", (name,))
            assert found.scalar_one() == 0
    finally:
        registry.close_all()
        with admin.begin() as c:  # what a failing run made, in the test role's own schema
            c.exec_driver_sql(f"DROP TABLE IF EXISTS public.{name}")
        admin.dispose()
