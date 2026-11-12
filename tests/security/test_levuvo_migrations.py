"""STU-024: the Levuvo migration formats (Nodestro, Axentra, Orkexa) against copied fixtures of the three repositories
(``levuvo_fixtures/SOURCE.txt``).

* The version tables are created with the platforms' own DDL and filled with the checksums their runners record
  (SHA-256 of ``Path.read_text``), computed here independently of Studio's ``sql_checksum``.
* States: current, pending (with the SQL preview), newer (the database knows more), changed (checksum or name),
  no files (an Axentra product namespace); a Nodestro table of a release before checksums.
* Files only inside the project (also in a virtual environment there); a link out of it is not followed; nothing of
  the project is imported (an Orkexa ``schema.py`` with a side effect stays silent).
* F-1 of ``stu-024-review.md``: one version table that cannot be read (no privilege, unexpected columns or types, a
  failed read) gives its entry ``state: unreadable`` and never fails the answer; only Orkexa's own table names count.
* F-2: the preview cap, the file cap, the identifier quoting, a project path with glob characters, the order of the
  states and the entries of a database without version tables.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any
import uuid

import pytest
import sqlalchemy
from tabrion.errors import Classification, Kind, TabrionError

from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services import levuvo_migrations as levuvo
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.migrations import Migrations
from tabrion_studio.services.readonly import StatementRefused
from tests.security.test_read_only import Source

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX links")
FIXTURES = Path(__file__).parent / "levuvo_fixtures"

#: The platforms' own DDL of their version tables (SOURCE.txt names the commits).
NODESTRO_TABLE = (
    "CREATE TABLE {schema}.nodestro_schema_migrations (version integer PRIMARY KEY, name text NOT NULL, "
    "applied_at timestamptz NOT NULL DEFAULT now(), checksum text)"
)
NODESTRO_TABLE_BEFORE_CHECKSUMS = (
    "CREATE TABLE {schema}.nodestro_schema_migrations (version integer PRIMARY KEY, name text NOT NULL, "
    "applied_at timestamptz NOT NULL DEFAULT now())"
)
AXENTRA_TABLE = (
    "CREATE TABLE {schema}.axentra_schema_migrations (namespace text NOT NULL, version integer NOT NULL, "
    "name text NOT NULL, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now(), "
    "PRIMARY KEY (namespace, version))"
)
ORKEXA_TABLE = (
    "CREATE TABLE {schema}.shop_orkexa_schema (version integer PRIMARY KEY, applied_at timestamptz NOT NULL "
    "DEFAULT now())"
)
ORKEXA_SCHEMA_PY = "import pathlib\npathlib.Path(__file__).with_name('ran').write_text('x')\nSCHEMA_VERSION = 1\n"


def recorded_checksum(path: Path) -> str:
    """What Nodestro's and Axentra's runners record (``hashlib.sha256(path.read_text(...).encode())``)."""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def make_project(root: Path, *, nodestro: str = "src/nodestro_runtime/migrations/postgres") -> Path:
    project = root / "product"
    shutil.copytree(FIXTURES / "nodestro", project / nodestro)
    shutil.copytree(FIXTURES / "axentra", project / "src/axentra/data/sql")
    orkexa = project / "src/orkexa/backends/postgres"
    orkexa.mkdir(parents=True)
    (orkexa / "schema.py").write_text(ORKEXA_SCHEMA_PY)
    return project


# Files and comparison (no server needed).


def test_the_checksum_is_the_one_the_runners_record(tmp_path: Path) -> None:
    for path in sorted((FIXTURES / "nodestro").glob("*.sql")) + sorted((FIXTURES / "axentra").glob("*.sql")):
        assert levuvo.sql_checksum(path.read_bytes()) == recorded_checksum(path)
    crlf = tmp_path / "0001_x.sql"
    crlf.write_bytes(b"CREATE TABLE a (x int);\r\nCREATE TABLE b (y int);\r\n")
    assert levuvo.sql_checksum(crlf.read_bytes()) == recorded_checksum(crlf)


def test_files_are_found_in_the_project_and_its_virtual_environment(tmp_path: Path) -> None:
    project = make_project(tmp_path, nodestro=".venv/lib/python3.12/site-packages/nodestro_runtime/migrations/postgres")
    nodestro = levuvo._file_set(project, levuvo._NODESTRO_FILES)
    assert nodestro is not None and nodestro.issue is None
    assert nodestro.folder == ".venv/lib/python3.12/site-packages/nodestro_runtime/migrations/postgres"
    assert sorted(nodestro.files) == [1, 2, 3] and nodestro.files[1].file == "0001_documents.sql"
    axentra = levuvo._file_set(project, levuvo._AXENTRA_FILES)
    assert axentra is not None and sorted(axentra.files) == [6, 7] and axentra.files[7].name == "users_without_email"
    assert levuvo._orkexa_expected(project) == (1, "src/orkexa/backends/postgres/schema.py")
    assert not (project / "src/orkexa/backends/postgres/ran").exists()  # read as text, never imported


@posix
def test_a_link_out_of_the_project_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    shutil.copytree(FIXTURES / "nodestro", outside)
    project = tmp_path / "product"
    (project / "src/nodestro_runtime/migrations").mkdir(parents=True)
    (project / "src/nodestro_runtime/migrations/postgres").symlink_to(outside)
    assert levuvo._file_set(project, levuvo._NODESTRO_FILES) is None
    inside = project / "nodestro_runtime/migrations/postgres"
    inside.mkdir(parents=True)
    (inside / "0001_documents.sql").symlink_to(outside / "0001_documents.sql")
    linked = levuvo._file_set(project, levuvo._NODESTRO_FILES)
    assert linked is not None and linked.issue == "config.unsafe_file" and linked.files == {}


def test_the_states(tmp_path: Path) -> None:
    files = levuvo._file_set(make_project(tmp_path), levuvo._NODESTRO_FILES)
    assert files is not None
    sums = {v: f.checksum for v, f in files.files.items()}
    names = {v: f.file for v, f in files.files.items()}

    def compare(applied: list[tuple[int, str, str | None]]) -> dict[str, Any]:
        return levuvo._compare(files, applied, file_names=True)

    all_applied: list[tuple[int, str, str | None]] = [(v, names[v], sums[v]) for v in (1, 2, 3)]
    assert compare(all_applied)["state"] == "current"
    pending = compare(all_applied[:2])
    assert pending["state"] == "pending"
    step = pending["steps"][2]
    assert step["status"] == "pending" and step["checksum"] == sums[3] and step["preview"].startswith("--")
    assert compare([*all_applied, (4, "0004_jobs.sql", "x" * 64)])["state"] == "newer"
    assert compare([(1, names[1], "0" * 64), *all_applied[1:]])["state"] == "changed"
    assert compare([(1, "0001_other.sql", sums[1]), *all_applied[1:]])["state"] == "changed"
    before_checksums = compare([(v, names[v], None) for v in (1, 2, 3)])
    assert before_checksums["state"] == "current" and not before_checksums["steps"][0]["checksum_recorded"]
    assert levuvo._compare(None, [(1, "crm", "a")], file_names=False)["state"] == "no_files"


def test_a_sqlite_connection_has_no_levuvo_format(tmp_path: Path) -> None:
    database = tmp_path / "x.db"
    sqlite3.connect(database).close()
    database.chmod(0o600)
    source = Source(profiles={"project:x": Profile("x", "sqlite", "dev", path="x.db")}, files={"project:x": database})
    registry = ConnectionRegistry(source)
    try:
        project = make_project(tmp_path)
        doc = Migrations(registry, CodeTrust(project, tmp_path, "k")).status(registry.open("project:x").id)
        assert [item["format"] for item in doc["formats"]] == ["sqlite_user_version"]
    finally:
        registry.close_all()


# PostgreSQL, against the server.


@pytest.fixture
def pg_schema() -> Iterator[tuple[ConnectionRegistry, str, sqlalchemy.Engine, str]]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    schema = f"studio_levuvo_{uuid.uuid4().hex[:8]}"
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    profile = Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username,
        password=SecretRef("keyring", ""), sslmode="prefer",
    )  # fmt: skip
    registry = ConnectionRegistry(Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""}))
    try:
        yield registry, registry.open("user:pg").id, admin, schema
    finally:
        registry.close_all()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()


def mine(doc: dict[str, Any], schema: str) -> dict[tuple[str, str | None], dict[str, Any]]:
    return {(item["format"], item.get("namespace")): item for item in doc["formats"] if item.get("schema") == schema}


@pytest.mark.postgres
@postgres
def test_pg_the_three_formats(
    pg_schema: tuple[ConnectionRegistry, str, sqlalchemy.Engine, str], tmp_path: Path
) -> None:
    registry, connection_id, admin, schema = pg_schema
    project = make_project(tmp_path)
    nodestro = FIXTURES / "nodestro"
    axentra = FIXTURES / "axentra"
    with admin.begin() as connection:
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        for ddl in (NODESTRO_TABLE, AXENTRA_TABLE, ORKEXA_TABLE):
            connection.exec_driver_sql(ddl.format(schema=f'"{schema}"'))
        for path in sorted(nodestro.glob("000[12]_*.sql")):
            connection.exec_driver_sql(
                "INSERT INTO nodestro_schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (int(path.name[:4]), path.name, recorded_checksum(path)),
            )
        path = axentra / "0006_search.sql"
        connection.exec_driver_sql(
            "INSERT INTO axentra_schema_migrations (namespace, version, name, checksum) "
            "VALUES ('axentra', 6, 'search', %s), ('crm', 1, 'contacts', %s)",
            (recorded_checksum(path), "c" * 64),
        )
        connection.exec_driver_sql("INSERT INTO shop_orkexa_schema (version) VALUES (1)")
    migrations = Migrations(registry, CodeTrust(project, tmp_path, "k"))
    found = mine(migrations.status(connection_id), schema)
    assert found[("nodestro", None)]["state"] == "pending" and found[("nodestro", None)]["current"] == 2
    assert [step["status"] for step in found[("nodestro", None)]["steps"]] == ["applied", "applied", "pending"]
    assert found[("nodestro", None)]["files"] == "src/nodestro_runtime/migrations/postgres"
    assert found[("axentra", "axentra")]["state"] == "pending"  # 0007 is not applied
    assert found[("axentra", "crm")]["state"] == "no_files" and found[("axentra", "crm")]["current"] == 1
    orkexa = found[("orkexa", None)]
    assert (orkexa["prefix"], orkexa["current"], orkexa["expected"], orkexa["state"]) == ("shop_", 1, 1, "current")
    assert not (project / "src/orkexa/backends/postgres/ran").exists()
    # A changed applied file, then a database newer than the files.
    (project / "src/nodestro_runtime/migrations/postgres/0001_documents.sql").write_text("-- edited\n")
    assert mine(migrations.status(connection_id), schema)[("nodestro", None)]["state"] == "changed"
    with admin.begin() as connection:
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        connection.exec_driver_sql(
            "INSERT INTO axentra_schema_migrations VALUES ('axentra', 7, 'users_without_email', %s), "
            "('axentra', 8, 'later', %s)",
            (recorded_checksum(axentra / "0007_users_without_email.sql"), "d" * 64),
        )
    assert mine(migrations.status(connection_id), schema)[("axentra", "axentra")]["state"] == "newer"


@pytest.mark.postgres
@postgres
def test_pg_a_nodestro_table_before_checksums(
    pg_schema: tuple[ConnectionRegistry, str, sqlalchemy.Engine, str], tmp_path: Path
) -> None:
    registry, connection_id, admin, schema = pg_schema
    project = make_project(tmp_path)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        connection.exec_driver_sql(NODESTRO_TABLE_BEFORE_CHECKSUMS.format(schema=f'"{schema}"'))
        for version in (1, 2, 3):
            name = next((FIXTURES / "nodestro").glob(f"{version:04d}_*.sql")).name
            connection.exec_driver_sql(
                "INSERT INTO nodestro_schema_migrations (version, name) VALUES (%s, %s)", (version, name)
            )
    found = mine(Migrations(registry, CodeTrust(project, tmp_path, "k")).status(connection_id), schema)
    assert found[("nodestro", None)]["state"] == "current"
    assert not any(step["checksum_recorded"] for step in found[("nodestro", None)]["steps"])


# F-2 of stu-024-review.md: the guarantees the first tests left open.


def test_the_preview_holds_the_first_2000_characters(tmp_path: Path) -> None:
    folder = tmp_path / "product/nodestro_runtime/migrations/postgres"
    folder.mkdir(parents=True)
    text = "".join(f"-- line {number:05d}\n" for number in range(400))  # 6 000 characters
    (folder / "0001_long.sql").write_text(text)
    files = levuvo._file_set(tmp_path / "product", levuvo._NODESTRO_FILES)
    assert files is not None and files.files[1].preview == text[: levuvo.PREVIEW_CHARS]
    assert len(files.files[1].preview) == levuvo.PREVIEW_CHARS == 2_000
    step = levuvo._compare(files, [], file_names=True)["steps"][0]
    assert step["status"] == "pending" and step["preview"] == text[:2_000]


@pytest.mark.parametrize("line_end", ["\n", "\r\n", "\r"])
def test_the_preview_is_the_text_the_runner_reads(tmp_path: Path, line_end: str) -> None:
    """Line ends as the runners read them (``Path.read_text``), whatever the file and the platform have: the preview
    is the text the checksum covers, and its 2 000 characters do not depend on ``\\r``."""
    folder = tmp_path / "product/nodestro_runtime/migrations/postgres"
    folder.mkdir(parents=True)
    text = "".join(f"-- line {number:05d}\n" for number in range(400))
    (folder / "0001_long.sql").write_bytes(text.replace("\n", line_end).encode("utf-8"))
    files = levuvo._file_set(tmp_path / "product", levuvo._NODESTRO_FILES)
    assert files is not None and files.files[1].preview == text[:2_000]
    assert files.files[1].checksum == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_more_files_than_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(tmp_path)  # three Nodestro files
    monkeypatch.setattr(levuvo, "MAX_FILES", 3)
    at_cap = levuvo._file_set(project, levuvo._NODESTRO_FILES)
    assert at_cap is not None and at_cap.issue is None and sorted(at_cap.files) == [1, 2, 3]
    monkeypatch.setattr(levuvo, "MAX_FILES", 2)
    over = levuvo._file_set(project, levuvo._NODESTRO_FILES)
    assert over is not None and over.issue == "migrations.too_many_files" and over.files == {}
    assert levuvo._compare(over, [], file_names=True)["files_issue"] == "migrations.too_many_files"


def test_identifiers_are_quoted() -> None:
    assert levuvo._quote('a"b') == '"a""b"'
    assert levuvo._select("version", 'x"y', "t") == 'SELECT version FROM "x""y"."t"'


@pytest.mark.parametrize(
    "name",
    [
        "a[b]c",  # valid everywhere; without glob.escape the pattern matches "abc" and not itself
        pytest.param(
            "a[b]*c?", marks=pytest.mark.skipif(sys.platform == "win32", reason="* and ? are not valid on Windows")
        ),
    ],
)
def test_a_project_path_with_glob_characters(tmp_path: Path, name: str) -> None:
    venv_files = ".venv/lib/python3.12/site-packages/nodestro_runtime/migrations/postgres"
    project = make_project(tmp_path / name, nodestro=venv_files)
    (tmp_path / "abc").mkdir()  # what the pattern would match without glob.escape
    files = levuvo._file_set(project, levuvo._NODESTRO_FILES)
    assert files is not None and files.folder == venv_files and sorted(files.files) == [1, 2, 3]


def test_the_order_of_the_states(tmp_path: Path) -> None:
    files = levuvo._file_set(make_project(tmp_path), levuvo._NODESTRO_FILES)
    assert files is not None
    one, two = (files.files[v] for v in (1, 2))
    unknown = (4, "0004_jobs.sql", "x" * 64)
    # 3 is pending and 4 unknown: changed > newer > pending.
    newer = levuvo._compare(files, [(1, one.file, one.checksum), (2, two.file, two.checksum), unknown], file_names=True)
    assert [step["status"] for step in newer["steps"]] == ["applied", "applied", "pending", "unknown"]
    assert newer["state"] == "newer"
    changed = levuvo._compare(files, [(1, one.file, "0" * 64), unknown], file_names=True)
    assert changed["state"] == "changed"


def test_the_orkexa_states() -> None:
    assert levuvo._orkexa_state(0, 1) == "pending"
    assert levuvo._orkexa_state(1, 1) == "current"
    assert levuvo._orkexa_state(2, 1) == "newer"
    assert levuvo._orkexa_state(2, None) == "no_files"


class FakeConnection:
    """A PostgreSQL connection that answers the catalog query with ``tables`` and every other query with ``read``."""

    engine = "postgresql"

    def __init__(self, tables: list[tuple[Any, ...]], read: Any = None) -> None:
        self.tables = tables
        self.read = read
        self.queries: list[str] = []

    def fetch(self, sql: str, params: Any = None) -> list[tuple[Any, ...]]:
        if sql == levuvo._TABLES_SQL:
            return self.tables
        self.queries.append(sql)
        if isinstance(self.read, BaseException):
            raise self.read
        return self.read(sql) if callable(self.read) else []


NODESTRO_COLUMNS = ["version integer", "name text", "applied_at timestamp with time zone", "checksum text"]


def test_a_database_without_version_tables_shows_the_files(tmp_path: Path) -> None:
    found = levuvo.levuvo_formats(FakeConnection([]), make_project(tmp_path))  # type: ignore[arg-type]
    by_format = {(item["format"], item.get("namespace")): item for item in found}
    assert set(by_format) == {("nodestro", None), ("axentra", "axentra")}  # no Orkexa table, no Orkexa entry
    for item in by_format.values():
        assert (item["schema"], item["table"], item["current"], item["state"]) == (None, None, 0, "pending")
        assert item["problem"] is None and all(step["status"] == "pending" for step in item["steps"])
    assert levuvo.levuvo_formats(FakeConnection([]), tmp_path / "empty") == []  # type: ignore[arg-type]


def test_an_axentra_table_shows_its_own_namespace_with_the_files(tmp_path: Path) -> None:
    columns = ["namespace text", "version integer", "name text", "checksum text"]
    connection = FakeConnection([("app", "axentra_schema_migrations", True, columns)], lambda sql: [])
    found = levuvo.levuvo_formats(connection, make_project(tmp_path))  # type: ignore[arg-type]
    axentra = [item for item in found if item["format"] == "axentra"]
    assert [(item["schema"], item["namespace"], item["state"]) for item in axentra] == [("app", "axentra", "pending")]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (StatementRefused("studio.statement_refused"), "studio.statement_refused"),
        (
            TabrionError(
                "database.timeout",
                classification=Classification(Kind.TIMEOUT, True, False, True, "psycopg", "57014", *[None] * 4),
            ),
            "db.timeout",
        ),
    ],
)
def test_a_failed_read_makes_only_that_table_unreadable(tmp_path: Path, error: Exception, code: str) -> None:
    tables = [
        ("a", "nodestro_schema_migrations", True, NODESTRO_COLUMNS),
        ("a", "axentra_schema_migrations", True, ["namespace text", "version integer", "name text", "checksum text"]),
        ("a", "shop_orkexa_schema", True, ["version integer"]),
    ]
    connection = FakeConnection(tables, error)
    found = levuvo.levuvo_formats(connection, make_project(tmp_path))  # type: ignore[arg-type]
    assert len(connection.queries) == 3  # each table was tried
    assert [(item["format"], item["state"], item["problem"]["code"]) for item in found] == [
        ("nodestro", "unreadable", code),
        ("axentra", "unreadable", code),
        ("orkexa", "unreadable", code),
    ]
    nodestro = found[0]
    assert nodestro["current"] is None and nodestro["steps"] == []
    assert nodestro["files"] == "src/nodestro_runtime/migrations/postgres"
    assert (found[2]["prefix"], found[2]["expected"]) == ("shop_", 1)


def test_values_the_conversion_refuses_make_the_table_unreadable(tmp_path: Path) -> None:
    tables = [
        ("a", "nodestro_schema_migrations", True, NODESTRO_COLUMNS),
        ("a", "x_orkexa_schema", True, ["version integer"]),
    ]

    def read(sql: str) -> list[tuple[Any, ...]]:
        return [("1.5",)] if "coalesce" in sql else [("v1", "0001_x.sql", None)]

    found = levuvo.levuvo_formats(FakeConnection(tables, read), tmp_path)  # type: ignore[arg-type]
    assert [(item["format"], item["state"], item["problem"]["code"]) for item in found] == [
        ("nodestro", "unreadable", "studio.migrations_table"),
        ("orkexa", "unreadable", "studio.migrations_table"),
    ]


def test_the_columns_are_checked_before_the_table_is_read(tmp_path: Path) -> None:
    tables = [
        ("a", "nodestro_schema_migrations", False, NODESTRO_COLUMNS),  # no privilege
        ("b", "nodestro_schema_migrations", True, ["version text", "name text"]),  # wrong type
        ("c", "nodestro_schema_migrations", True, ["version bigint"]),  # no name
        ("d", "nodestro_schema_migrations", True, ["version smallint", "name character varying"]),  # old, fine
    ]
    connection = FakeConnection(tables, lambda sql: [(1, "0001_documents.sql", None)])
    found = levuvo.levuvo_formats(connection, tmp_path)  # type: ignore[arg-type]
    assert [(item["schema"], item["state"], (item["problem"] or {}).get("code")) for item in found] == [
        ("a", "unreadable", "db.permission"),
        ("b", "unreadable", "studio.migrations_table"),
        ("c", "unreadable", "studio.migrations_table"),
        ("d", "no_files", None),
    ]
    assert connection.queries == [levuvo._select("version, name, NULL", "d", "nodestro_schema_migrations",
                                                 f"ORDER BY version LIMIT {levuvo.MAX_ROWS}")]  # fmt: skip


def test_only_orkexa_table_names_count() -> None:
    names = ["shop_orkexa_schema", "a_orkexa_schema", "a" + "b" * 30 + "_orkexa_schema"]
    foreign = [
        "orkexa_schema",
        "_orkexa_schema",
        "myorkexa_schema",
        "Shop_orkexa_schema",
        "a b_orkexa_schema",
        "a:b_orkexa_schema",
        "1a_orkexa_schema",
        "a" + "b" * 31 + "_orkexa_schema",
        "shop_orkexa_schema2",
    ]
    assert all(levuvo._ORKEXA_TABLE.fullmatch(name) for name in names)
    assert not any(levuvo._ORKEXA_TABLE.fullmatch(name) for name in foreign)
    tables = [("a", name, True, ["version integer"]) for name in foreign]
    assert levuvo.levuvo_formats(FakeConnection(tables), Path("/nonexistent")) == []  # type: ignore[arg-type]


# F-1 and F-2 against the server.


@pytest.fixture
def extra_schemas(
    pg_schema: tuple[ConnectionRegistry, str, sqlalchemy.Engine, str],
) -> Iterator[list[str]]:
    """More schemas of the test (any name); dropped afterwards."""
    _, _, admin, _ = pg_schema
    created: list[str] = []
    yield created
    with admin.begin() as connection:
        for schema in created:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {driver_name(schema)} CASCADE")


def driver_name(schema: str) -> str:
    """``schema`` quoted for ``exec_driver_sql``, which hands psycopg ``%`` placeholders (``%b`` is one)."""
    return levuvo._quote(schema).replace("%", "%%")


def create_schema(admin: sqlalchemy.Engine, created: list[str], schema: str, *statements: str) -> str:
    """Create ``schema`` with ``statements`` (``{schema}``: its name); returns the name for ``exec_driver_sql``."""
    created.append(schema)
    quoted = driver_name(schema)
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {quoted}")
        for statement in statements:
            connection.exec_driver_sql(statement.format(schema=quoted))
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,)).scalar() == 1
        )
    return quoted


@pytest.mark.postgres
@postgres
def test_pg_one_unreadable_table_does_not_fail_the_answer(
    pg_schema: tuple[ConnectionRegistry, str, sqlalchemy.Engine, str], extra_schemas: list[str], tmp_path: Path
) -> None:
    registry, connection_id, admin, schema = pg_schema
    project = make_project(tmp_path)
    nodestro_rows = "INSERT INTO {schema}.nodestro_schema_migrations (version, name) VALUES (1, '0001_documents.sql')"
    with admin.begin() as connection:
        connection.exec_driver_sql(NODESTRO_TABLE.format(schema=f'"{schema}"'))
        connection.exec_driver_sql(nodestro_rows.format(schema=f'"{schema}"'))
    # No USAGE on the schema; no SELECT on the table: the role owns both and takes its own privileges back.
    no_usage = create_schema(admin, extra_schemas, f"{schema}_no_usage", NODESTRO_TABLE, ORKEXA_TABLE)
    no_select = create_schema(admin, extra_schemas, f"{schema}_no_select", AXENTRA_TABLE)
    with admin.begin() as connection:
        connection.exec_driver_sql(f"REVOKE USAGE ON SCHEMA {no_usage} FROM CURRENT_USER")
        connection.exec_driver_sql(f"REVOKE SELECT ON {no_select}.axentra_schema_migrations FROM CURRENT_USER")  # noqa: S608 (quoted name)
    # A version column of the wrong type, and tables only named like Orkexa's.
    wrong = create_schema(
        admin,
        extra_schemas,
        f"{schema}_wrong",
        NODESTRO_TABLE.replace("version integer", "version text"),
        "CREATE TABLE {schema}.myorkexa_schema (id int)",
        'CREATE TABLE {schema}."Shop_orkexa_schema" (id int)',
        'CREATE TABLE {schema}."a:b_orkexa_schema" (id int)',
        "CREATE TABLE {schema}.orkexa_schema (id int)",
        "CREATE TABLE {schema}.x_orkexa_schema (version text)",
    )
    with admin.begin() as connection:
        connection.exec_driver_sql(f"INSERT INTO {wrong}.nodestro_schema_migrations (version, name) VALUES ('v1', 'a')")  # noqa: S608 (quoted name)
    # The catalog query itself: privileges are read, and only Orkexa's own table names are listed.
    listing = {(row[0], row[1]): row[2] for row in registry.get(connection_id).fetch(levuvo._TABLES_SQL)}
    assert listing[(schema, "nodestro_schema_migrations")] is True
    assert listing[(f"{schema}_no_usage", "nodestro_schema_migrations")] is False
    assert listing[(f"{schema}_no_select", "axentra_schema_migrations")] is False
    assert sorted(table for name, table in listing if name == f"{schema}_wrong") == [
        "nodestro_schema_migrations",
        "x_orkexa_schema",
    ]
    doc = Migrations(registry, CodeTrust(project, tmp_path, "k")).status(connection_id)
    entries = {(item["format"], item["schema"], item["table"]): item for item in doc["formats"]}
    ok = entries[("nodestro", schema, "nodestro_schema_migrations")]
    assert (ok["state"], ok["current"], ok["problem"]) == ("pending", 1, None)
    assert ok["steps"][0]["status"] == "applied"

    def unreadable(key: tuple[str, str, str]) -> str:
        item = entries[key]
        assert (item["state"], item["current"], item["steps"]) == ("unreadable", None, [])
        return str(item["problem"]["code"])

    assert unreadable(("nodestro", f"{schema}_no_usage", "nodestro_schema_migrations")) == "db.permission"
    assert unreadable(("orkexa", f"{schema}_no_usage", "shop_orkexa_schema")) == "db.permission"
    assert unreadable(("axentra", f"{schema}_no_select", "axentra_schema_migrations")) == "db.permission"
    assert unreadable(("nodestro", f"{schema}_wrong", "nodestro_schema_migrations")) == "studio.migrations_table"
    assert unreadable(("orkexa", f"{schema}_wrong", "x_orkexa_schema")) == "studio.migrations_table"
    assert not [key for key in entries if key[1] == f"{schema}_wrong" and key[2] not in (
        "nodestro_schema_migrations", "x_orkexa_schema")]  # fmt: skip


@pytest.mark.postgres
@postgres
@pytest.mark.parametrize("name", ['a"b', "a:b", "a%b", "a%s", "a'b", "A b;--"])
def test_pg_schema_names_are_quoted(
    pg_schema: tuple[ConnectionRegistry, str, sqlalchemy.Engine, str],
    extra_schemas: list[str],
    tmp_path: Path,
    name: str,
) -> None:
    registry, connection_id, admin, schema = pg_schema
    hostile = f"{schema}_{name}"
    quoted = create_schema(admin, extra_schemas, hostile, NODESTRO_TABLE, ORKEXA_TABLE)
    with admin.begin() as connection:
        checksum = recorded_checksum(FIXTURES / "nodestro/0001_documents.sql")  # hex digits only
        connection.exec_driver_sql(
            f"INSERT INTO {quoted}.nodestro_schema_migrations (version, name, checksum) "  # noqa: S608 (quoted name)
            f"VALUES (1, '0001_documents.sql', '{checksum}')"
        )
        connection.exec_driver_sql(f"INSERT INTO {quoted}.shop_orkexa_schema (version) VALUES (1), (2)")  # noqa: S608 (quoted name)
    found = mine(Migrations(registry, CodeTrust(make_project(tmp_path), tmp_path, "k")).status(connection_id), hostile)
    assert (found[("nodestro", None)]["state"], found[("nodestro", None)]["current"]) == ("pending", 1)
    assert (found[("orkexa", None)]["state"], found[("orkexa", None)]["current"]) == ("newer", 2)


@pytest.mark.postgres
@postgres
def test_pg_orkexa_pending(pg_schema: tuple[ConnectionRegistry, str, sqlalchemy.Engine, str], tmp_path: Path) -> None:
    registry, connection_id, admin, schema = pg_schema
    with admin.begin() as connection:
        connection.exec_driver_sql(ORKEXA_TABLE.format(schema=f'"{schema}"'))
    found = mine(Migrations(registry, CodeTrust(make_project(tmp_path), tmp_path, "k")).status(connection_id), schema)
    orkexa = found[("orkexa", None)]
    assert (orkexa["current"], orkexa["expected"], orkexa["state"], orkexa["problem"]) == (0, 1, "pending", None)
