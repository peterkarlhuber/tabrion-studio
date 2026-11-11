"""STU-021: export of a query result or a table as CSV or JSON, streamed.

* Acceptance (09 backlog): 1 million rows without a memory peak above 100 MB (tracemalloc), CSV and JSON.
* The statement check, the parameters and the object check apply before the first byte (problems); limits: rows
  (JSON says ``truncated``), one export per connection, a client that goes away stops the export and frees its slot.
* CSV: RFC 4180 quoting, NULL as the empty field, bytes as base64, a formula-like text gets a leading ``'``.
* JSON: valid JSON with rows as arrays, bytes as ``{"base64"}``, NaN as text.
* The route sits behind the session and the CSRF check.
"""

from __future__ import annotations

from collections.abc import Iterator
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
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
from tests.support.access import ORIGIN, make_studio

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
    with studio.client.stream("POST", "/api/exports", json={**body, "sql": MILLION, "table": None}, headers=headers):
        pass  # the client reads the head and goes away
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
