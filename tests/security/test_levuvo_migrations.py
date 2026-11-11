"""STU-024: the Levuvo migration formats (Nodestro, Axentra, Orkexa) against copied fixtures of the three repositories
(``levuvo_fixtures/SOURCE.txt``).

* The version tables are created with the platforms' own DDL and filled with the checksums their runners record
  (SHA-256 of ``Path.read_text``), computed here independently of Studio's ``sql_checksum``.
* States: current, pending (with the SQL preview), newer (the database knows more), changed (checksum or name),
  no files (an Axentra product namespace); a Nodestro table of a release before checksums.
* Files only inside the project (also in a virtual environment there); a link out of it is not followed; nothing of
  the project is imported (an Orkexa ``schema.py`` with a side effect stays silent).
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

from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services import levuvo_migrations as levuvo
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.migrations import Migrations
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
