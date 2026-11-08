"""STU-010: the explorer reads catalog and table detail on the read-only path, fast enough for 500 tables.

* The catalog lists schemas and objects by kind, cached until ``refresh``; 500 tables in under a second.
* The table detail: columns, primary key, foreign keys out and in, unique and check constraints, indexes, estimate.
* Names from the request are values (an unknown or hostile name is ``studio.object_not_found``, nothing runs).
* Every request goes through ``ConnectionRegistry.get``; the routes sit behind the session and CSRF checks.

The SQLite tests always run; the PostgreSQL tests need ``TABRION_STUDIO_TEST_POSTGRES`` (see ``test_read_only.py``)
and create and drop their own schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services.connections import ConnectionRegistry, RegistryError
from tabrion_studio.services.explorer import Explorer, ExplorerError
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
SCHEMA = """
CREATE TABLE supplier (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE part (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'unnamed' CHECK (length(name) < 80),
    supplier_id INTEGER REFERENCES supplier (id),
    weight REAL
) STRICT;
CREATE TABLE tag (part_id INTEGER, label TEXT, PRIMARY KEY (part_id, label)) WITHOUT ROWID;
CREATE INDEX part_supplier ON part (supplier_id);
CREATE VIEW heavy AS SELECT * FROM part WHERE weight > 10;
"""


@pytest.fixture
def lite(tmp_path: Path) -> Iterator[tuple[Explorer, str, Source]]:
    path = tmp_path / "parts.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        SCHEMA + "".join(f"CREATE TABLE bulk_{i:03d} (id INTEGER PRIMARY KEY, v TEXT);" for i in range(500))
    )
    connection.close()
    path.chmod(0o600)
    source = Source(
        profiles={"project:parts": Profile("parts", "sqlite", "dev", path="data/parts.db")},
        files={"project:parts": path},
    )
    registry = ConnectionRegistry(source)
    try:
        yield Explorer(registry), registry.open("project:parts").id, source
    finally:
        registry.close_all()


def test_the_catalog_of_500_tables_in_under_a_second(lite: tuple[Explorer, str, Source]) -> None:
    explorer, connection_id, _ = lite
    started = time.monotonic()
    doc = explorer.catalog(connection_id)
    assert time.monotonic() - started < 1.0  # 09 backlog STU-010: 500 tables < 1 s
    kinds = {(item["name"], item["kind"]) for item in doc["objects"]}
    assert {("part", "table"), ("supplier", "table"), ("tag", "table"), ("heavy", "view")} <= kinds
    assert sum(item["kind"] == "table" for item in doc["objects"]) == 503
    assert doc["schemas"] == ["main"] and doc["cached"] is False
    assert explorer.catalog(connection_id)["cached"] is True
    assert explorer.catalog(connection_id, refresh=True)["cached"] is False


def test_the_table_detail(lite: tuple[Explorer, str, Source]) -> None:
    explorer, connection_id, _ = lite
    part = explorer.table(connection_id, "main", "part")
    columns = {column["name"]: column for column in part["columns"]}
    assert columns["name"]["nullable"] is False and columns["name"]["default"] == "'unnamed'"
    assert columns["id"]["type"] == "INTEGER"
    assert part["primary_key"]["columns"] == ["id"]
    assert part["foreign_keys"] == [
        {
            "name": None,
            "columns": ["supplier_id"],
            "referred_schema": "main",
            "referred_table": "supplier",
            "referred_columns": ["id"],
        }
    ]
    assert [check["sqltext"] for check in part["check_constraints"]] == ["length(name) < 80"]
    assert part["indexes"] == [{"name": "part_supplier", "columns": ["supplier_id"], "unique": False}]
    supplier = explorer.table(connection_id, "main", "supplier")
    assert [item["table"] for item in supplier["referenced_by"]] == ["part"]
    assert supplier["unique_constraints"][0]["columns"] == ["name"]
    assert explorer.table(connection_id, "main", "heavy")["kind"] == "view"


@pytest.mark.parametrize(
    ("schema", "name"), [("main", "nope"), ("other", "part"), ("main", "part'; DROP TABLE part; --")]
)
def test_unknown_objects_are_refused(lite: tuple[Explorer, str, Source], schema: str, name: str) -> None:
    explorer, connection_id, _ = lite
    with pytest.raises(ExplorerError) as raised:
        explorer.table(connection_id, schema, name)
    assert raised.value.code == "studio.object_not_found"
    assert explorer.table(connection_id, "main", "part")["name"] == "part"


def test_the_sqlite_facts(lite: tuple[Explorer, str, Source]) -> None:
    explorer, connection_id, _ = lite
    info = explorer.sqlite_info(connection_id)
    assert info["journal_mode"] in ("delete", "wal") and info["user_version"] == 0 and info["page_size"] > 0
    assert info["file_size"] > 0 and info["foreign_keys"] in (True, False)
    if sys.platform != "win32":
        assert info["file_mode"] == "0600"
    tables = {table["name"]: table for table in info["tables"]}
    assert tables["part"]["strict"] is True and tables["tag"]["without_rowid"] is True
    assert tables["heavy"]["type"] == "view"
    with pytest.raises(ExplorerError) as raised:
        explorer.extensions(connection_id)
    assert raised.value.code == "studio.explorer_engine"


def test_every_request_checks_the_connection(lite: tuple[Explorer, str, Source]) -> None:
    explorer, connection_id, source = lite
    explorer.catalog(connection_id)
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], path="data/other.db")
    with pytest.raises(RegistryError) as raised:
        explorer.catalog(connection_id)  # also with a cached catalog
    assert raised.value.code == "studio.connection_stale"


def test_the_explorer_routes(lite: tuple[Explorer, str, Source], tmp_path: Path) -> None:
    explorer, connection_id, _ = lite
    studio = make_studio(tmp_path / "static", connections=explorer.registry, explorer=explorer)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    client = studio.client
    catalog = client.get(f"/api/connections/{connection_id}/catalog", headers=headers)
    assert catalog.status_code == 200 and catalog.json()["engine"] == "sqlite"
    assert (
        client.get(f"/api/connections/{connection_id}/catalog?refresh=true", headers=headers).json()["cached"] is False
    )
    table = client.get(f"/api/connections/{connection_id}/tables/main/part", headers=headers)
    assert table.status_code == 200 and table.json()["primary_key"]["columns"] == ["id"]
    missing = client.get(f"/api/connections/{connection_id}/tables/main/nope", headers=headers)
    assert (missing.status_code, missing.json()["code"]) == (404, "studio.object_not_found")
    assert client.get(f"/api/connections/{connection_id}/sqlite-info", headers=headers).json()["tables"]
    wrong = client.get(f"/api/connections/{connection_id}/extensions", headers=headers)
    assert (wrong.status_code, wrong.json()["code"]) == (409, "studio.explorer_engine")
    gone = client.get("/api/connections/c_nope/catalog", headers=headers)
    assert gone.json()["code"] == "studio.connection_not_found"
    assert client.get(f"/api/connections/{connection_id}/catalog").status_code in (401, 403)


@pytest.fixture
def pg() -> Iterator[tuple[Explorer, str]]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    statements = [
        "DROP SCHEMA IF EXISTS studio_explorer CASCADE",
        "CREATE SCHEMA studio_explorer",
        "CREATE TABLE studio_explorer.supplier (id int PRIMARY KEY, name text NOT NULL UNIQUE)",
        "CREATE TABLE studio_explorer.part (id int PRIMARY KEY, name text DEFAULT 'x' CHECK (length(name) < 80), "
        "supplier_id int REFERENCES studio_explorer.supplier (id))",
        "COMMENT ON COLUMN studio_explorer.part.name IS 'the part''s name'",
        "CREATE INDEX part_supplier ON studio_explorer.part (supplier_id)",
        "CREATE VIEW studio_explorer.named AS SELECT name FROM studio_explorer.part",
        "CREATE MATERIALIZED VIEW studio_explorer.counted AS SELECT count(*) FROM studio_explorer.part",
        "CREATE SEQUENCE studio_explorer.serial",
        "INSERT INTO studio_explorer.supplier SELECT g, 's' || g FROM generate_series(1, 100) g",
        "ANALYZE studio_explorer.supplier",
        *[f"CREATE TABLE studio_explorer.bulk_{i:03d} (id int PRIMARY KEY, v text)" for i in range(500)],
    ]
    with admin.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
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
    )
    source = Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    try:
        yield Explorer(registry), registry.open("user:pg").id
    finally:
        registry.close_all()
        with admin.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS studio_explorer CASCADE")
        admin.dispose()


@pytest.mark.postgres
@postgres
def test_pg_catalog_of_500_tables_in_under_a_second(pg: tuple[Explorer, str]) -> None:
    explorer, connection_id = pg
    started = time.monotonic()
    doc = explorer.catalog(connection_id)
    assert time.monotonic() - started < 1.0
    ours = {(item["name"], item["kind"]) for item in doc["objects"] if item["schema"] == "studio_explorer"}
    assert {("part", "table"), ("named", "view"), ("counted", "materialized_view"), ("serial", "sequence")} <= ours
    assert sum(kind == "table" for _, kind in ours) == 502
    assert not any(schema.startswith("pg_") or schema == "information_schema" for schema in doc["schemas"])


@pytest.mark.postgres
@postgres
def test_pg_table_detail_and_extensions(pg: tuple[Explorer, str]) -> None:
    explorer, connection_id = pg
    part = explorer.table(connection_id, "studio_explorer", "part")
    columns = {column["name"]: column for column in part["columns"]}
    assert columns["name"]["comment"] == "the part's name" and columns["id"]["nullable"] is False
    assert part["foreign_keys"][0]["referred_table"] == "supplier"
    assert part["indexes"][0]["name"] == "part_supplier"
    supplier = explorer.table(connection_id, "studio_explorer", "supplier")
    assert [item["table"] for item in supplier["referenced_by"]] == ["part"]
    assert supplier["row_estimate"] == 100
    assert explorer.table(connection_id, "studio_explorer", "serial")["kind"] == "sequence"
    extensions = explorer.extensions(connection_id)["extensions"]
    plpgsql = next(item for item in extensions if item["name"] == "plpgsql")
    assert plpgsql["schema"] == "pg_catalog" and plpgsql["update_available"] is False
    with pytest.raises(ExplorerError):
        explorer.sqlite_info(connection_id)


# The fix round of stu-010-review.md (M-1, L-1..L-3, I-3, I-6).


def _write(path: Path, *statements: str) -> None:
    writer = sqlite3.connect(path)
    for statement in statements:
        writer.execute(statement)
    writer.commit()
    writer.close()


def test_l1_a_table_dropped_after_the_catalog_is_not_found(lite: tuple[Explorer, str, Source], tmp_path: Path) -> None:
    explorer, connection_id, _ = lite
    explorer.catalog(connection_id)
    _write(tmp_path / "parts.db", "DROP TABLE bulk_000")
    with pytest.raises(ExplorerError) as raised:
        explorer.table(connection_id, "main", "bulk_000")
    assert raised.value.code == "studio.object_not_found"
    doc = explorer.catalog(connection_id)
    assert doc["cached"] is False  # the stale entry was dropped
    assert not any(item["name"] == "bulk_000" for item in doc["objects"])


def test_l3_unknown_names_rebuild_the_catalog_at_most_every_few_seconds(lite: tuple[Explorer, str, Source]) -> None:
    explorer, connection_id, _ = lite
    now = [1000.0]
    explorer.clock = lambda: now[0]
    builds: list[int] = []
    real = explorer._catalog

    def counting(*args: Any) -> dict[str, Any]:
        builds.append(1)
        return real(*args)

    explorer._catalog = counting  # type: ignore[method-assign,assignment]
    for index in range(10):
        with pytest.raises(ExplorerError):
            explorer.table(connection_id, "main", f"nope_{index}")
    assert len(builds) == 1  # the first build; a catalog younger than 5 s is not rebuilt
    now[0] += 6.0
    with pytest.raises(ExplorerError):
        explorer.table(connection_id, "main", "nope")
    assert len(builds) == 2
    with pytest.raises(ExplorerError):
        explorer.table(connection_id, "main", "nope")
    assert len(builds) == 2


def test_l3_a_new_table_is_found_once_the_catalog_is_old_enough(
    lite: tuple[Explorer, str, Source], tmp_path: Path
) -> None:
    explorer, connection_id, _ = lite
    now = [1000.0]
    explorer.clock = lambda: now[0]
    explorer.catalog(connection_id)
    _write(tmp_path / "parts.db", 'CREATE TABLE "x/y" (id INTEGER PRIMARY KEY)')
    now[0] += 6.0
    assert explorer.table(connection_id, "main", "x/y")["primary_key"]["columns"] == ["id"]


def test_i3_closing_a_connection_drops_its_catalog(lite: tuple[Explorer, str, Source]) -> None:
    explorer, connection_id, _ = lite
    explorer.catalog(connection_id)
    assert connection_id in explorer._cache
    explorer.registry.close(connection_id)
    assert connection_id not in explorer._cache


def test_l2_i6_busy_pool_and_names_with_a_slash(
    lite: tuple[Explorer, str, Source], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, source = lite
    _write(tmp_path / "parts.db", 'CREATE TABLE "x/y" (id INTEGER PRIMARY KEY)')
    monkeypatch.setattr(connections_module, "POOL_TIMEOUT_S", 0.3)
    registry = ConnectionRegistry(source)
    busy = Explorer(registry)
    connection_id = registry.open("project:parts").id
    studio = make_studio(tmp_path / "static", connections=registry, explorer=busy)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    try:
        found = studio.client.get(
            f"/api/connections/{connection_id}/table", params={"schema": "main", "name": "x/y"}, headers=headers
        )
        assert found.status_code == 200 and found.json()["name"] == "x/y"
        connection = registry.get(connection_id)
        release, holding = threading.Event(), threading.Barrier(3)

        def hold() -> None:
            with connection._read():  # one session of the pool, as a running query would
                holding.wait()
                release.wait(10)

        holders = [threading.Thread(target=hold) for _ in range(2)]
        for holder in holders:
            holder.start()
        holding.wait()
        try:
            answer = studio.client.get(f"/api/connections/{connection_id}/catalog?refresh=true", headers=headers)
        finally:
            release.set()
            for holder in holders:
                holder.join()
        assert (answer.status_code, answer.json()["code"]) == (503, "studio.connection_busy")
    finally:
        registry.close_all()


@pytest.mark.postgres
@postgres
def test_pg_m1_foreign_keys_into_search_path_schemas(pg: tuple[Explorer, str]) -> None:
    """A key into ``public`` and one into the schema named like the role (``$user``) keep their schema."""
    explorer, connection_id = pg
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    role = str(url.username)
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    setup = [
        "CREATE TABLE public.studio_explorer_ref (id int PRIMARY KEY)",
        f'CREATE SCHEMA "{role}"',
        f'CREATE TABLE "{role}".studio_explorer_dup (id int PRIMARY KEY)',
        "CREATE TABLE studio_explorer.studio_explorer_dup (id int PRIMARY KEY)",  # a decoy of the same name
        "CREATE TABLE studio_explorer.child (id int PRIMARY KEY, "
        "r int CONSTRAINT to_public REFERENCES public.studio_explorer_ref (id), "
        f'd int CONSTRAINT to_user REFERENCES "{role}".studio_explorer_dup (id))',
    ]
    try:
        with admin.begin() as connection:
            for statement in setup:
                connection.exec_driver_sql(statement)
        child = explorer.table(connection_id, "studio_explorer", "child")
        keys = {key["name"]: key for key in child["foreign_keys"]}
        assert keys["to_public"]["referred_schema"] == "public" and keys["to_public"]["columns"] == ["r"]
        assert keys["to_user"]["referred_schema"] == role and keys["to_user"]["referred_columns"] == ["id"]
        ref = explorer.table(connection_id, "public", "studio_explorer_ref")
        assert [(item["schema"], item["table"], item["columns"]) for item in ref["referenced_by"]] == [
            ("studio_explorer", "child", ["r"])
        ]
        dup = explorer.table(connection_id, role, "studio_explorer_dup")
        assert [item["name"] for item in dup["referenced_by"]] == ["to_user"]
        decoy = explorer.table(connection_id, "studio_explorer", "studio_explorer_dup")
        assert decoy["referenced_by"] == []
        part = explorer.table(connection_id, "studio_explorer", "part")  # a key inside one schema still works
        assert part["foreign_keys"][0]["referred_schema"] == "studio_explorer"
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS studio_explorer.child")
            connection.exec_driver_sql("DROP TABLE IF EXISTS public.studio_explorer_ref")
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{role}" CASCADE')
        admin.dispose()
