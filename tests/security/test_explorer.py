"""STU-010: the explorer reads catalog and table detail on the read-only path, fast enough for 500 tables.

* The catalog lists schemas and objects by kind, cached until ``refresh``; 500 tables in under a second.
* The table detail: columns, primary key, foreign keys out and in, unique and check constraints, indexes, estimate.
* Names from the request are values (an unknown or hostile name is ``studio.object_not_found``, nothing runs).
* Every request goes through ``ConnectionRegistry.get``; the routes sit behind the session and CSRF checks.
* STU-023: the diagram answer has every table of a schema with columns, primary key and outgoing keys (views,
  sequences and other schemas left out), bounded by ``MAX_DIAGRAM_TABLES``.

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
import uuid

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services import explorer as explorer_module
from tabrion_studio.services.connections import ConnectionRegistry, RegistryError
from tabrion_studio.services.explorer import Explorer, ExplorerError
from tabrion_studio.services.tabrion_compat import CATALOG_SEARCH_PATH
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
    # One transaction (TD-028): executescript() runs each statement in autocommit, so the 505 statements were 505
    # commits, each a rollback journal created, flushed and deleted (about 2,000 fsync calls per fixture). Seconds
    # on Linux, but on the Windows runner a flush costs tens of milliseconds and the fixture ran 14 times: 2 to 12
    # minutes for this file. The test reads the schema; how it was written is not what it checks.
    connection.executescript(
        "BEGIN;"
        + SCHEMA
        + "".join(f"CREATE TABLE bulk_{i:03d} (id INTEGER PRIMARY KEY, v TEXT);" for i in range(500))
        + "COMMIT;"
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


def test_the_diagram_of_a_schema(lite: tuple[Explorer, str, Source]) -> None:
    """STU-023: one answer with every table of the schema; the view is left out, keys and columns are kept."""
    explorer, connection_id, _ = lite
    doc = explorer.diagram(connection_id, "main")
    assert (doc["connection_id"], doc["engine"], doc["schema"]) == (connection_id, "sqlite", "main")
    tables = {table["name"]: table for table in doc["tables"]}
    assert len(tables) == 503 and "heavy" not in tables
    assert [table["name"] for table in doc["tables"]] == sorted(tables)
    part = tables["part"]
    assert part["kind"] == "table" and part["primary_key"] == ["id"]
    assert part["columns"][:2] == [
        {"name": "id", "type": "INTEGER", "nullable": True},  # SQLite: an INTEGER PRIMARY KEY may be NULL in DDL
        {"name": "name", "type": "TEXT", "nullable": False},
    ]
    assert part["foreign_keys"] == [
        {
            "name": None,
            "columns": ["supplier_id"],
            "referred_schema": "main",
            "referred_table": "supplier",
            "referred_columns": ["id"],
        }
    ]
    assert tables["tag"]["primary_key"] == ["part_id", "label"] and tables["tag"]["foreign_keys"] == []
    assert tables["bulk_000"]["columns"] == [
        {"name": "id", "type": "INTEGER", "nullable": True},
        {"name": "v", "type": "TEXT", "nullable": True},
    ]


@pytest.mark.parametrize("schema", ["public", "", "main\x00", "' OR 1=1 --"])
def test_the_diagram_of_an_unknown_schema_is_refused(lite: tuple[Explorer, str, Source], schema: str) -> None:
    explorer, connection_id, _ = lite
    with pytest.raises(ExplorerError) as raised:
        explorer.diagram(connection_id, schema)
    assert raised.value.code == "studio.object_not_found"


def test_the_diagram_of_an_unknown_schema_rebuilds_the_catalog_at_most_every_few_seconds(
    lite: tuple[Explorer, str, Source],
) -> None:
    """L-2 of the STU-023 review: a schema created after the catalog was built gets the one throttled rebuild of
    ``known`` before it is refused, so the diagram does not need the explorer's Refresh."""
    explorer, connection_id, _ = lite
    now = [1000.0]
    explorer.clock = lambda: now[0]
    builds: list[int] = []
    real = explorer._catalog

    def counting(*args: Any) -> dict[str, Any]:
        builds.append(1)
        return real(*args)

    explorer._catalog = counting  # type: ignore[method-assign,assignment]
    for _ in range(5):
        with pytest.raises(ExplorerError):
            explorer.diagram(connection_id, "later")
    assert len(builds) == 1  # the first build; a catalog younger than 5 s is not rebuilt
    now[0] += 6.0
    with pytest.raises(ExplorerError) as raised:
        explorer.diagram(connection_id, "later")
    assert raised.value.code == "studio.object_not_found" and len(builds) == 2
    # A schema the catalog knows is read without a rebuild.
    now[0] += 6.0
    assert explorer.diagram(connection_id, "main")["schema"] == "main"
    assert len(builds) == 2


def test_the_diagram_is_bounded(lite: tuple[Explorer, str, Source], monkeypatch: pytest.MonkeyPatch) -> None:
    """A schema with more tables than one answer holds is refused before anything is reflected."""
    explorer, connection_id, _ = lite
    monkeypatch.setattr(explorer_module, "MAX_DIAGRAM_TABLES", 503)
    assert len(explorer.diagram(connection_id, "main")["tables"]) == 503
    monkeypatch.setattr(explorer_module, "MAX_DIAGRAM_TABLES", 502)
    reflected: list[object] = []
    monkeypatch.setattr(explorer, "_reflect", lambda *args: reflected.append(args))
    with pytest.raises(ExplorerError) as raised:
        explorer.diagram(connection_id, "main")
    assert raised.value.code == "studio.diagram_too_large" and reflected == []


def test_the_sqlite_facts_before_3_37(lite: tuple[Explorer, str, Source], monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback without ``PRAGMA table_list`` still tells WITHOUT ROWID tables apart, as a bool."""
    explorer, connection_id, _ = lite
    monkeypatch.setattr(explorer_module, "_HAS_TABLE_LIST", False)
    tables = {table["name"]: table for table in explorer.sqlite_info(connection_id)["tables"]}
    assert tables["tag"]["without_rowid"] is True and tables["part"]["without_rowid"] is False
    assert tables["heavy"] == {"name": "heavy", "type": "view", "strict": False, "without_rowid": False}
    assert all(table["strict"] is False for table in tables.values())


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
    diagram = client.get(f"/api/connections/{connection_id}/diagram", params={"schema": "main"}, headers=headers)
    assert diagram.status_code == 200 and len(diagram.json()["tables"]) == 503
    unknown = client.get(f"/api/connections/{connection_id}/diagram", params={"schema": "nope"}, headers=headers)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "studio.object_not_found")
    too_long = client.get(f"/api/connections/{connection_id}/diagram", params={"schema": "x" * 257}, headers=headers)
    assert too_long.status_code == 422
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
    # STU-023: the diagram of the schema has its 502 tables, without the view, materialized view and sequence.
    diagram = {table["name"]: table for table in explorer.diagram(connection_id, "studio_explorer")["tables"]}
    assert len(diagram) == 502 and not {"named", "counted", "serial"} & set(diagram)
    assert diagram["part"]["primary_key"] == ["id"]
    assert diagram["part"]["foreign_keys"] == [
        {
            "name": "part_supplier_id_fkey",
            "columns": ["supplier_id"],
            "referred_schema": "studio_explorer",
            "referred_table": "supplier",
            "referred_columns": ["id"],
        }
    ]
    assert {"name": "id", "type": "INTEGER", "nullable": False} in diagram["part"]["columns"]


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
    """A key into ``public``, one into the schema named like the role (``$user``) and one into another schema keep
    their schema; a partitioned table's key is shown once, without its partitions' copies (N-1). The test creates a
    uniquely named schema and touches the role's schema only if it creates it (N-2)."""
    explorer, connection_id = pg
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    role = str(url.username)
    other = f"studio_explorer_{uuid.uuid4().hex[:8]}"
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    with admin.begin() as connection:
        role_schema_exists = bool(
            connection.exec_driver_sql("SELECT count(*) FROM pg_namespace WHERE nspname = %(n)s", {"n": role}).scalar()
        )
    user_schema = None if role_schema_exists else role
    setup = [
        "CREATE TABLE public.studio_explorer_ref (id int PRIMARY KEY)",
        f'CREATE SCHEMA "{other}"',
        f'CREATE TABLE "{other}".studio_explorer_dup (id int PRIMARY KEY)',
        "CREATE TABLE studio_explorer.studio_explorer_dup (id int PRIMARY KEY)",  # a decoy of the same name
        "CREATE TABLE studio_explorer.child (id int PRIMARY KEY, "
        "r int CONSTRAINT to_public REFERENCES public.studio_explorer_ref (id), "
        f'd int CONSTRAINT to_other REFERENCES "{other}".studio_explorer_dup (id))',
        "CREATE TABLE studio_explorer.pchild (id int, r int CONSTRAINT p_to_public "
        "REFERENCES public.studio_explorer_ref (id)) PARTITION BY RANGE (id)",
        "CREATE TABLE studio_explorer.pchild_1 PARTITION OF studio_explorer.pchild FOR VALUES FROM (0) TO (100)",
        "CREATE TABLE studio_explorer.pchild_2 PARTITION OF studio_explorer.pchild FOR VALUES FROM (100) TO (200)",
    ]
    if user_schema is not None:
        setup += [
            f'CREATE SCHEMA "{user_schema}"',
            f'CREATE TABLE "{user_schema}".studio_explorer_mine (id int PRIMARY KEY)',
            "CREATE TABLE studio_explorer.mine_child (id int PRIMARY KEY, "
            f'm int CONSTRAINT to_user REFERENCES "{user_schema}".studio_explorer_mine (id))',
        ]
    try:
        with admin.begin() as connection:
            for statement in setup:
                connection.exec_driver_sql(statement)
        child = explorer.table(connection_id, "studio_explorer", "child")
        keys = {key["name"]: key for key in child["foreign_keys"]}
        assert keys["to_public"]["referred_schema"] == "public" and keys["to_public"]["columns"] == ["r"]
        assert keys["to_other"]["referred_schema"] == other and keys["to_other"]["referred_columns"] == ["id"]
        ref = explorer.table(connection_id, "public", "studio_explorer_ref")
        assert [(item["schema"], item["table"], item["columns"]) for item in ref["referenced_by"]] == [
            ("studio_explorer", "child", ["r"]),
            ("studio_explorer", "pchild", ["r"]),
        ]
        dup = explorer.table(connection_id, other, "studio_explorer_dup")
        assert [item["name"] for item in dup["referenced_by"]] == ["to_other"]
        decoy = explorer.table(connection_id, "studio_explorer", "studio_explorer_dup")
        assert decoy["referenced_by"] == []
        pchild = explorer.table(connection_id, "studio_explorer", "pchild")
        assert [key["name"] for key in pchild["foreign_keys"]] == ["p_to_public"]
        part = explorer.table(connection_id, "studio_explorer", "part")  # a key inside one schema still works
        assert part["foreign_keys"][0]["referred_schema"] == "studio_explorer"
        if user_schema is not None:
            mine = explorer.table(connection_id, "studio_explorer", "mine_child")
            assert mine["foreign_keys"][0]["referred_schema"] == user_schema
        # STU-023: the diagram reads the same catalog: every referenced schema named, a partitioned key once.
        explorer.catalog(connection_id, refresh=True)
        diagram = {table["name"]: table for table in explorer.diagram(connection_id, "studio_explorer")["tables"]}
        assert {key["name"]: key["referred_schema"] for key in diagram["child"]["foreign_keys"]} == {
            "to_public": "public",
            "to_other": other,
        }
        assert [key["name"] for key in diagram["pchild"]["foreign_keys"]] == ["p_to_public"]
        assert all(diagram[name]["foreign_keys"] == [] for name in ("pchild_1", "pchild_2") if name in diagram)
        assert diagram["studio_explorer_dup"]["foreign_keys"] == []
    finally:
        with admin.begin() as connection:
            for table in ("child", "pchild", "mine_child"):
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS studio_explorer.{table}")
            connection.exec_driver_sql("DROP TABLE IF EXISTS studio_explorer.studio_explorer_dup")
            connection.exec_driver_sql("DROP TABLE IF EXISTS public.studio_explorer_ref")
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{other}" CASCADE')
            if user_schema is not None:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{user_schema}" CASCADE')
        admin.dispose()


@pytest.mark.postgres
@postgres
def test_pg_reflection_runs_under_the_pinned_catalog_path(pg: tuple[Explorer, str]) -> None:
    """TD-013 (review L-2 and I-3 of PR #91), without a superuser: inside ``OpenConnection.inspect`` (tabrion 0.2's
    ``Database.inspector()``) the session's ``search_path`` is ``tabrion_compat.CATALOG_SEARCH_PATH`` and the
    transaction is read-only; the pin is transaction-local, so the next statement has the session's own path."""
    explorer, connection_id = pg
    opened = explorer.registry.get(connection_id)

    def settings(found: sqlalchemy.Inspector) -> tuple[Any, Any, Any]:
        bind = found.bind
        assert isinstance(bind, sqlalchemy.Connection)
        path = bind.exec_driver_sql("SELECT pg_catalog.current_setting('search_path')").scalar()
        read_only = bind.exec_driver_sql("SELECT pg_catalog.current_setting('transaction_read_only')").scalar()
        return path, read_only, found.get_schema_names()

    path, read_only, schemas = opened.inspect(settings)
    assert path == CATALOG_SEARCH_PATH
    assert read_only == "on"
    assert "studio_explorer" in schemas
    session_path = opened.fetch("SELECT pg_catalog.current_setting('search_path')", catalog=False)
    assert session_path != [(CATALOG_SEARCH_PATH,)]
