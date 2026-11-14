"""STU-055: the vector data inspection reads, bounds its counts and reports the database's real state.

* SQLite (always run): an empty file, a sqlite-vec ``vec0`` table read without the module (its ``sqlite_master`` and
  shadow tables as sqlite-vec v0.1.9 writes them), Orkexa's SQLite index (``index_spaces``, ``index_vectors``,
  ``index_meta``), the bounded and timed counts, malformed identities; sqlite-vec's install status is "not
  determinable", and nothing is ever called an ANN index.
* The route: the same guards as every explorer route (session, CSRF, Origin; other methods 405), and the database
  file is unchanged afterwards.
* PostgreSQL (``TABRION_STUDIO_TEST_POSTGRES``, marker ``postgres``): Orkexa's schema version 1 (``real[]``, exact
  search) and the extension status as the server reports it; with ``TABRION_STUDIO_TEST_POSTGRES_SUPERUSER``
  (marker ``postgres_superuser``) a role without ``SELECT`` on the tables gets hints, not an error.
* pgvector (``TABRION_STUDIO_TEST_PGVECTOR``, a superuser URL of a server with pgvector; marker ``pgvector``, run
  in the CI PostgreSQL job against a second server with pgvector; the main one has none): ``vector`` columns with an
  HNSW index, an IVFFlat index made invalid, no index, undeclared dimensions, an expression HNSW index on Orkexa's
  ``real[]``, and pgvector dropped again.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.security.sessions import CSRF_HEADER
from tabrion_studio.services import vectors as vectors_module
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.readonly import check_statement
from tabrion_studio.services.vectors import (
    MAX_TEXT,
    VectorInspection,
    parse_identity,
    split_top_level,
    vec0_body,
    vec0_columns,
)
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
PGVECTOR_URL = os.environ.get("TABRION_STUDIO_TEST_PGVECTOR")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
superuser = pytest.mark.skipif(
    PG_URL is None or PG_SUPERUSER_URL is None,
    reason="TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed",
)
SERIES = "generate_series(1, 20) g"
pgvector = pytest.mark.skipif(PGVECTOR_URL is None, reason="TABRION_STUDIO_TEST_PGVECTOR is not set")

#: sqlite_master of a vec0 table as sqlite-vec v0.1.9 writes it (recorded from a real file; Studio never loads the
#: module, so the test writes the same rows itself).
VEC0_SQL = (
    "CREATE VIRTUAL TABLE index_vec using vec0(namespace text partition key, embedding float[4] distance_metric=cosine)"
)
VEC0_SHADOW = (
    'CREATE TABLE "index_vec_info" (key text primary key, value any)',
    'CREATE TABLE "index_vec_chunks"(chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,size INTEGER NOT NULL,'
    "sequence_id integer,partition00,validity BLOB NOT NULL, rowids BLOB NOT NULL)",
    'CREATE TABLE "index_vec_rowids"(rowid INTEGER PRIMARY KEY AUTOINCREMENT,id,chunk_id INTEGER,chunk_offset INTEGER)',
    'CREATE TABLE "index_vec_vector_chunks00"(rowid PRIMARY KEY,vectors BLOB NOT NULL)',
)
#: Orkexa's SQLite index (orkexa/backends/sqlite/index.py on master).
ORKEXA_SQLITE = (
    "CREATE TABLE index_chunks (id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, source TEXT NOT NULL, title TEXT, "
    "file_id TEXT, metadata TEXT NOT NULL, text TEXT NOT NULL)",
    "CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE index_spaces (namespace TEXT NOT NULL, space TEXT NOT NULL, identity TEXT NOT NULL, "
    "dimensions INTEGER NOT NULL, state TEXT NOT NULL, PRIMARY KEY (namespace, space))",
    "CREATE TABLE index_vectors (space TEXT NOT NULL, chunk_id INTEGER NOT NULL, embedding BLOB NOT NULL, "
    "PRIMARY KEY (space, chunk_id))",
)
IDENTITY = {
    "provider": "ollama",
    "model": "nomic-embed-text",
    "dimensions": 4,
    "digest": "sha256:ab",
    "normalized": True,
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make(path: Path, *statements: str, vec0: bool = False) -> Path:
    connection = sqlite3.connect(path)
    with connection:
        for statement in statements:
            connection.execute(statement)
    if vec0:
        connection.execute("PRAGMA writable_schema = ON")
        with connection:
            connection.execute(
                "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) "
                "VALUES ('table', 'index_vec', 'index_vec', 0, ?)",
                (VEC0_SQL,),
            )
        connection.execute("PRAGMA writable_schema = OFF")
    connection.close()
    path.chmod(0o600)
    return path


def _open(path: Path) -> tuple[ConnectionRegistry, str]:
    source = Source(profiles={"project:v": Profile("v", "sqlite", "dev", path="data/v.db")}, files={"project:v": path})
    registry = ConnectionRegistry(source)
    return registry, registry.open("project:v").id


@pytest.fixture
def lite(tmp_path: Path) -> Iterator[Any]:
    opened: list[ConnectionRegistry] = []

    def inspect(path: Path, **options: Any) -> dict[str, Any]:
        registry, connection_id = _open(path)
        opened.append(registry)
        return VectorInspection(registry, **options).inspect(connection_id)

    yield inspect
    for registry in opened:
        registry.close_all()


def _codes(doc: dict[str, Any]) -> set[str]:
    return {hint["code"] for hint in doc["hints"]}


def _orkexa_rows(path: Path, *, vectors: int = 3, identity: object = None, state: str = "active") -> None:
    connection = sqlite3.connect(path)
    with connection:
        text = json.dumps(IDENTITY) if identity is None else identity
        connection.execute("INSERT INTO index_spaces VALUES ('default', 'default:k1', ?, 4, ?)", (text, state))
        connection.executemany(
            "INSERT INTO index_vectors VALUES ('default:k1', ?, x'0000803f0000803f0000803f0000803f')",
            [(i,) for i in range(vectors)],
        )
    connection.close()


# SQLite


def test_an_empty_sqlite_file_has_no_vector_data_and_sqlite_vec_is_not_determinable(lite: Any, tmp_path: Path) -> None:
    doc = lite(_make(tmp_path / "v.db", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body BLOB)"))
    assert doc["engine"] == "sqlite" and doc["columns"] == [] and doc["identities"] == []
    assert doc["extension"]["state"] == "not_determinable" and doc["extension"]["determinable"] is False
    assert doc["extension"]["installed_version"] is None
    assert {"no_vector_data", "sqlite_vec_undeterminable"} <= _codes(doc)


def test_a_vec0_table_is_read_without_the_module_and_is_never_an_ann_index(lite: Any, tmp_path: Path) -> None:
    path = _make(tmp_path / "v.db", *VEC0_SHADOW, *ORKEXA_SQLITE, vec0=True)
    connection = sqlite3.connect(path)
    with connection:
        connection.executemany(
            "INSERT INTO index_vec_info VALUES (?, ?)", [("CREATE_VERSION", "v0.1.9"), ("CREATE_VERSION_MAJOR", 0)]
        )
        connection.executemany("INSERT INTO index_vec_rowids (id) VALUES (?)", [(i,) for i in range(5)])
        connection.execute("INSERT INTO index_meta VALUES ('dimensions', '4')")
    connection.close()
    _orkexa_rows(path)
    before = _digest(path)
    doc = lite(path)
    assert _digest(path) == before  # read-only
    columns = {(c["table"], c["column"]): c for c in doc["columns"]}
    vec = columns[("index_vec", "embedding")]
    assert (vec["kind"], vec["type"], vec["dimensions"], vec["dimensions_source"]) == (
        "vec0",
        "float[4]",
        4,
        "declaration",
    )
    assert vec["search"] == "exact" and vec["layout"] == "orkexa"  # index_meta names it: Orkexa's older vec0 table
    assert vec["rows"] == {"value": 5, "kind": "exact"}
    (index,) = vec["indexes"]
    assert index["method"] == "vec0" and index["ann"] is False and index["created_with"] == "v0.1.9"
    assert index["options"] == ["distance_metric=cosine"]
    blob = columns[("index_vectors", "embedding")]
    assert (blob["kind"], blob["layout"], blob["search"], blob["dimensions"]) == ("blob", "orkexa", "exact", 4)
    assert blob["rows"] == {"value": 3, "kind": "exact"} and blob["indexes"] == []
    (identity,) = doc["identities"]
    assert identity["model"] == "nomic-embed-text" and identity["provider"] == "ollama"
    assert identity["digest"] == "sha256:ab" and identity["normalized"] is True and identity["complete"] is True
    assert identity["embeddings"] == {"value": 3, "kind": "exact"} and identity["space"] == "default:k1"
    assert "document_instruction" not in identity
    assert {"vec0_module", "exact_search", "sqlite_vec_undeterminable"} <= _codes(doc)
    assert "no_vector_data" not in _codes(doc)
    assert all(column["search"] != "ann" for column in doc["columns"])


def test_orkexa_metadata_problems_are_hints(lite: Any, tmp_path: Path) -> None:
    path = _make(tmp_path / "v.db", *ORKEXA_SQLITE)
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("INSERT INTO index_meta VALUES ('dimensions', '384')")  # a backup without index_vec
        connection.execute("INSERT INTO index_spaces VALUES ('a', 'a:1', '{not json', 4, 'active')")
        identity = json.dumps({**IDENTITY, "dimensions": 8})
        connection.execute("INSERT INTO index_spaces VALUES ('b', 'b:1', ?, 4, 'building')", (identity,))
    connection.close()
    doc = lite(path)
    hints = {(hint["code"], json.dumps(hint["params"], sort_keys=True)) for hint in doc["hints"]}
    assert ("legacy_table_missing", json.dumps({"table": "index_vec"})) in hints
    assert ("identity_incomplete", json.dumps({"space": "a:1"})) in hints
    assert ("dimension_mismatch", json.dumps({"identity": 8, "space": "b:1", "stored": 4}, sort_keys=True)) in hints
    assert ("space_building", json.dumps({"space": "b:1"})) in hints
    broken = next(item for item in doc["identities"] if item["space"] == "a:1")
    assert (
        broken["complete"] is False
        and broken["model"] is None
        and broken["embeddings"] == {"value": 0, "kind": "exact"}
    )


def test_a_float_blob_without_metadata_is_not_a_vector_column(lite: Any, tmp_path: Path) -> None:
    doc = lite(_make(tmp_path / "v.db", "CREATE TABLE index_vectors (space TEXT, chunk_id INTEGER, embedding BLOB)"))
    assert doc["columns"] == [] and "no_vector_data" in _codes(doc)


def test_counts_are_bounded_and_timed(lite: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _make(tmp_path / "v.db", *ORKEXA_SQLITE)
    _orkexa_rows(path, vectors=12)
    monkeypatch.setattr(vectors_module, "EXACT_COUNT_MAX", 10)
    doc = lite(path)
    assert doc["identities"][0]["embeddings"] == {"value": 10, "kind": "more_than"}
    # A count past its timer is canceled and shown as unknown; the rest of the answer stays.
    timed = lite(path, count_timeout_s=0.0)
    assert timed["identities"][0]["embeddings"] == {"value": None, "kind": "timeout"}
    assert timed["identities"][0]["model"] == "nomic-embed-text" and "count_timeout" in _codes(timed)
    # At most MAX_COUNTS counts per inspection.
    monkeypatch.setattr(vectors_module, "MAX_COUNTS", 1)
    capped = lite(path)
    assert capped["identities"][0]["embeddings"] == {"value": None, "kind": "skipped"}
    assert {"code": "counts_skipped", "severity": "info", "params": {"count": 1}} in capped["hints"]


def test_every_statement_is_a_checked_read_with_a_bound(
    lite: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = OpenConnection.fetch

    def spy(self: OpenConnection, sql: str, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        seen.append(sql)
        return real(self, sql, *args, **kwargs)

    monkeypatch.setattr(OpenConnection, "fetch", spy)
    path = _make(tmp_path / "v.db", *VEC0_SHADOW, *ORKEXA_SQLITE, vec0=True)
    _orkexa_rows(path)
    lite(path)
    assert seen
    for sql in seen:
        check_statement(sql)
        assert sql.upper().startswith("SELECT")
        if "count(*)" in sql:
            assert "LIMIT 100001) AS bounded" in sql


def test_vec0_parsing() -> None:
    columns = vec0_columns(
        'CREATE VIRTUAL TABLE "docs" USING vec0(id integer primary key, "my, col" int8[16], b bit[64], '
        "e float[1024] distance_metric=L2, +text text, user_id integer partition key)"
    )
    assert columns == [
        {"name": "my, col", "element": "int8", "dimensions": 16, "distance_metric": None},
        {"name": "b", "element": "bit", "dimensions": 64, "distance_metric": None},
        {"name": "e", "element": "float", "dimensions": 1024, "distance_metric": "l2"},
    ]
    assert vec0_columns("CREATE VIRTUAL TABLE f USING fts5(body)") is None
    assert vec0_columns(None) is None
    assert split_top_level("a float[3], b int, c text") == ["a float[3]", "b int", "c text"]


def _hostile(repeats: int) -> str:
    """PR #97 review M-1: a virtual-table text that repeats ``USING vec0(`` and ends in a comment, which made the
    earlier pattern backtrack quadratically (2 s at 70 KB, 31 s at 280 KB)."""
    return "CREATE VIRTUAL TABLE x USING vec0(" + "USING vec0(a) " * repeats + "b) /*z*/"


def test_vec0_parsing_is_linear_on_a_hostile_text() -> None:
    long_row, short_row = _hostile(20_000), _hostile(MAX_TEXT // 15)  # 280 KB, and just under the cap
    assert len(long_row) > 280_000 and len(short_row) <= MAX_TEXT
    started = time.monotonic()
    assert vec0_columns(long_row) is None  # past the cap: never parsed
    vec0_columns(short_row)
    assert time.monotonic() - started < 0.5
    assert vec0_body('CREATE VIRTUAL TABLE IF NOT EXISTS main."a(b" USING vec0(e float[2]) -- x') == "e float[2]"
    assert vec0_body('CREATE VIRTUAL TABLE "USING vec0(" USING fts5(body)') is None
    assert vec0_body("CREATE VIRTUAL TABLE t USING vec0") is None
    assert vec0_body('CREATE VIRTUAL TABLE "open USING vec0(a float[1])') is None


def test_a_hostile_file_is_classified_fast_and_long_values_are_cut(lite: Any, tmp_path: Path) -> None:
    path = _make(tmp_path / "v.db", *ORKEXA_SQLITE)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA writable_schema = ON")
    with connection:
        connection.execute(
            "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) VALUES ('table', 'x', 'x', 0, ?)",
            (_hostile(20_000),),
        )
    connection.execute("PRAGMA writable_schema = OFF")
    with connection:
        huge = json.dumps({**IDENTITY, "pad": "x" * (2 * MAX_TEXT)})
        connection.execute("INSERT INTO index_spaces VALUES ('default', 'default:k1', ?, 4, 'active')", (huge,))
    connection.close()
    started = time.monotonic()
    doc = lite(path)
    assert time.monotonic() - started < 0.5
    assert {"code": "definition_too_long", "severity": "warning", "params": {"table": "x"}} in doc["hints"]
    (identity,) = doc["identities"]
    assert identity["complete"] is False and identity["model"] is None  # cut on reading, never parsed
    assert {"code": "identity_incomplete", "severity": "warning", "params": {"space": "default:k1"}} in doc["hints"]


def test_identity_parsing() -> None:
    assert parse_identity(json.dumps(IDENTITY)) == {
        "provider": "ollama", "model": "nomic-embed-text", "digest": "sha256:ab", "dimensions": 4, "normalized": True,
    }  # fmt: skip
    assert parse_identity("[1, 2]") is None and parse_identity("{") is None
    assert parse_identity('{"dimensions": true, "model": 3}') == {
        "provider": None, "model": None, "digest": None, "dimensions": None, "normalized": None,
    }  # fmt: skip


def test_the_route_keeps_every_guard(tmp_path: Path) -> None:
    path = _make(tmp_path / "v.db", *ORKEXA_SQLITE)
    _orkexa_rows(path)
    before = _digest(path)
    registry, connection_id = _open(path)
    try:
        studio = make_studio(tmp_path / "static", connections=registry, explorer=Explorer(registry))
        client = studio.client
        route = f"/api/connections/{connection_id}/vectors"
        assert client.get(route).status_code == 401  # no session
        headers = {**studio.sign_in(), "Origin": ORIGIN}
        answer = client.get(route, headers=headers)
        assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
        assert answer.json()["identities"][0]["model"] == "nomic-embed-text"
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            refused = client.request(
                method, route, headers={**headers, "Content-Type": "application/json"}, content="{}"
            )
            assert refused.status_code == 405, method
        foreign = client.get(route, headers={**headers, "Origin": "http://evil.example"})
        assert foreign.status_code == 403
        no_csrf = {key: value for key, value in headers.items() if key.lower() != CSRF_HEADER.lower()}
        assert client.get(route, headers=no_csrf).status_code in (401, 403)
        gone = client.get("/api/connections/c_nope/vectors", headers=headers)
        assert gone.json()["code"] == "studio.connection_not_found"
    finally:
        registry.close_all()
    assert _digest(path) == before


# PostgreSQL


def _pg_profile(url: sqlalchemy.URL) -> Profile:
    return Profile(
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


def _pg_inspect(url_text: str, **options: Any) -> dict[str, Any]:
    url = sqlalchemy.make_url(url_text)
    source = Source(profiles={"user:pg": _pg_profile(url)}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    try:
        return VectorInspection(registry, **options).inspect(registry.open("user:pg").id)
    finally:
        registry.close_all()


def _orkexa_pg(schema: str, prefix: str = "orkexa_") -> list[str]:
    """Orkexa's schema version 1 for ``prefix`` (orkexa/backends/postgres/schema.py on master), with rows."""
    spaces, vectors = f"{schema}.{prefix}vector_spaces", f"{schema}.{prefix}chunk_vectors"
    identity = json.dumps({**IDENTITY, "dimensions": 3}).replace("'", "''")
    return [
        f'CREATE TABLE {spaces} (tenant_id text COLLATE "C" NOT NULL, namespace text COLLATE "C" NOT NULL, '
        'space text COLLATE "C" NOT NULL, identity jsonb NOT NULL, dimensions integer NOT NULL CHECK '
        "(dimensions > 0), state text COLLATE \"C\" NOT NULL CHECK (state IN ('active', 'building')), "
        "PRIMARY KEY (tenant_id, namespace, space))",
        f'CREATE TABLE {vectors} (tenant_id text COLLATE "C" NOT NULL, namespace text COLLATE "C" NOT NULL, '
        'space text COLLATE "C" NOT NULL, chunk_id bigint NOT NULL, embedding real[] NOT NULL, '
        "PRIMARY KEY (tenant_id, space, chunk_id))",
        f"INSERT INTO {spaces} VALUES ('t1', 'default', 'default:k', '{identity}', 3, 'active')",  # noqa: S608 (test-made names)
        f"INSERT INTO {vectors} SELECT 't1', 'default', 'default:k', g, '{{1,2,3}}' FROM generate_series(1, 7) g",  # noqa: S608 (test-made names)
        # A float array that no metadata confirms is not a vector column.
        f"CREATE TABLE {schema}.measurements (id int PRIMARY KEY, samples real[])",
    ]


@pytest.fixture
def pg_schema() -> Iterator[tuple[sqlalchemy.Engine, str]]:
    assert PG_URL is not None
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    schema = f"studio_vec_{uuid.uuid4().hex[:8]}"
    with admin.connect() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    try:
        yield admin, schema
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.dispose()


@pytest.mark.postgres
@postgres
def test_pg_orkexa_layout_is_exact_search_with_its_identity(
    pg_schema: tuple[sqlalchemy.Engine, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin, schema = pg_schema
    with admin.connect() as connection:
        for statement in _orkexa_pg(schema):
            connection.exec_driver_sql(statement)
        available, installed = connection.exec_driver_sql(
            "SELECT (SELECT default_version FROM pg_available_extensions WHERE name = 'vector'), "
            "(SELECT extversion FROM pg_extension WHERE extname = 'vector')"
        ).one()
    assert PG_URL is not None
    doc = _pg_inspect(PG_URL)
    ours = [column for column in doc["columns"] if column["schema"] == schema]
    (column,) = ours
    assert (column["table"], column["column"], column["kind"], column["layout"]) == (
        "orkexa_chunk_vectors", "embedding", "float_array", "orkexa",
    )  # fmt: skip
    assert column["search"] == "exact" and column["dimensions"] == 3 and column["dimensions_source"] == "metadata"
    assert column["rows"] == {"value": 7, "kind": "exact"}
    identity = next(item for item in doc["identities"] if item["schema"] == schema)
    assert (identity["tenant"], identity["space"], identity["model"], identity["complete"]) == (
        "t1", "default:k", "nomic-embed-text", True,
    )  # fmt: skip
    assert identity["embeddings"] == {"value": 7, "kind": "exact"}
    # The extension status is the server's own: installed, available or missing, with its versions.
    extension = doc["extension"]
    assert extension["determinable"] is True and extension["installed_version"] == installed
    assert extension["available_version"] == available
    expected = "installed" if installed is not None else ("available" if available is not None else "missing")
    assert extension["state"] in ({expected, "other_schema"} if installed else {expected})
    hint = {"code": "exact_search", "severity": "info",
            "params": {"schema": schema, "table": "orkexa_chunk_vectors", "column": "embedding"}}  # fmt: skip
    assert hint in doc["hints"]
    # Past the bound the estimate is shown, marked as an estimate, and the table is not counted.
    with admin.connect() as connection:
        connection.exec_driver_sql(f"ANALYZE {schema}.orkexa_chunk_vectors")
    monkeypatch.setattr(vectors_module, "EXACT_COUNT_MAX", 3)
    estimated = _pg_inspect(PG_URL)
    column = next(c for c in estimated["columns"] if c["schema"] == schema)
    assert column["rows"] == {"value": 7, "kind": "estimate"}
    assert {"code": "count_estimated", "severity": "info", "params": {"schema": schema,
            "table": "orkexa_chunk_vectors"}} in estimated["hints"]  # fmt: skip


@pytest.mark.postgres_superuser
@superuser
def test_pg_a_role_without_select_gets_hints_not_an_error(pg_schema: tuple[sqlalchemy.Engine, str]) -> None:
    admin, schema = pg_schema
    assert PG_SUPERUSER_URL is not None and PG_URL is not None
    role, password = f"studio_vec_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    root = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    database = sqlalchemy.make_url(PG_URL).database
    try:
        with admin.connect() as connection:
            for statement in _orkexa_pg(schema):
                connection.exec_driver_sql(statement)
        with root.connect() as connection:
            connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
            connection.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{database}" TO {role}')
            connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        url = sqlalchemy.make_url(PG_URL).set(username=role, password=password)
        doc = _pg_inspect(url.render_as_string(hide_password=False))
        column = next(c for c in doc["columns"] if c["schema"] == schema)
        assert column["rows"] is None and column["search"] == "exact"
        assert not [item for item in doc["identities"] if item["schema"] == schema]
        codes = {(hint["code"], hint["params"].get("table")) for hint in doc["hints"]}
        assert ("table_unreadable", "orkexa_chunk_vectors") in codes
        assert ("metadata_unreadable", "orkexa_vector_spaces") in codes
    finally:
        with root.connect() as connection:
            connection.exec_driver_sql(f"DROP OWNED BY {role}")
            connection.exec_driver_sql(f"DROP ROLE IF EXISTS {role}")
        root.dispose()


@pytest.mark.pgvector
@pgvector
def test_pgvector_ann_and_exact_columns_follow_the_catalog() -> None:
    assert PGVECTOR_URL is not None
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PGVECTOR_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    schema = f"studio_vec_{uuid.uuid4().hex[:8]}"
    with admin.connect() as connection:
        had = connection.exec_driver_sql("SELECT count(*) FROM pg_extension WHERE extname = 'vector'").scalar()
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            for statement in [
                f"CREATE SCHEMA {schema}",
                f"CREATE TABLE {schema}.items (id int PRIMARY KEY, a vector(3), b vector(3), c vector(3), d vector)",
                f"INSERT INTO {schema}.items SELECT g, '[1,2,3]', '[1,2,3]', '[1,2,3]', '[1]' FROM {SERIES}",  # noqa: S608
                f"CREATE INDEX items_a ON {schema}.items USING hnsw (a vector_cosine_ops) WITH (m = 8)",
                f"CREATE INDEX items_b ON {schema}.items USING ivfflat (b vector_l2_ops) WITH (lists = 1)",
                f"UPDATE pg_index SET indisvalid = false WHERE indexrelid = '{schema}.items_b'::regclass",  # noqa: S608 (test-made names)
                f"CREATE INDEX items_c ON {schema}.items (c)",  # a B-tree: not an ANN index
                *_orkexa_pg(schema),
                f"CREATE INDEX vectors_hnsw ON {schema}.orkexa_chunk_vectors "
                "USING hnsw ((embedding::vector(3)) vector_cosine_ops)",
            ]:
                connection.exec_driver_sql(statement)
        doc = _pg_inspect(PGVECTOR_URL)
        assert doc["extension"]["state"] == "installed" and doc["extension"]["installed_version"]
        columns = {(c["table"], c["column"]): c for c in doc["columns"] if c["schema"] == schema}
        a, b, c, d = (columns[("items", name)] for name in "abcd")
        assert a["search"] == "ann" and a["dimensions"] == 3 and a["rows"] == {"value": 20, "kind": "exact"}
        (index,) = a["indexes"]
        assert (index["method"], index["state"], index["ann"], index["operator_classes"], index["options"]) == (
            "hnsw", "valid", True, ["vector_cosine_ops"], ["m=8"],
        )  # fmt: skip
        assert b["search"] == "exact" and b["indexes"][0]["state"] == "invalid"
        assert c["search"] == "exact" and c["indexes"][0]["method"] == "btree" and c["indexes"][0]["ann"] is False
        assert d["search"] == "exact" and d["dimensions"] is None
        orkexa = columns[("orkexa_chunk_vectors", "embedding")]
        assert orkexa["search"] == "ann" and orkexa["indexes"][0]["name"] == "vectors_hnsw"
        assert orkexa["indexes"][0]["expression"] is True and index["expression"] is False
        assert index["partial"] is False
        hints = {(h["code"], h["params"].get("column") or h["params"].get("index")) for h in doc["hints"]}
        assert {("index_invalid", "items_b"), ("dimensions_undeclared", "d"), ("exact_search", "b")} <= hints
        assert ("exact_search", "a") not in hints
        with admin.connect() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
            connection.exec_driver_sql("DROP EXTENSION vector")
        gone = _pg_inspect(PGVECTOR_URL)
        assert gone["extension"]["state"] == "available" and gone["extension"]["installed_version"] is None
        assert "pgvector_available" in _codes(gone)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            if had:
                connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        admin.dispose()
