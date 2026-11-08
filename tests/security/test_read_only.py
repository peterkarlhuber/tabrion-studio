"""STU-009: every Studio connection is read-only, and a write attempt fails in the database (STU-T-010..013, -024).

The SQLite tests always run. The PostgreSQL tests need ``TABRION_STUDIO_TEST_POSTGRES``: a libpq URL whose role owns
the database (so that only Studio's settings, not missing privileges, stop the writes), for example
``postgresql://studio_owner:pw@127.0.0.1:5432/studio_test``. The tests create and drop their own table.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest
import sqlalchemy
from tabrion.errors import TabrionError

from tabrion_studio.config.schema import TARGET_FIELDS, Profile, SecretRef, Settings
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services.connections import (
    ConnectionRegistry,
    OpenConnection,
    RegistryError,
    connection_url,
    studio_profile,
)
from tabrion_studio.services.readonly import StatementRefused, check_statement, sqlite_authorizer

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")


@dataclass
class Entry:
    id: str
    source: str
    profile: Profile


@dataclass
class Source:
    """A ProfileSource for the registry: profiles by id, a password per id, and the calls it got."""

    profiles: dict[str, Profile] = field(default_factory=dict)
    passwords: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    def entry(self, profile_id: str) -> Entry:
        if profile_id not in self.profiles:
            raise SecretError("studio.profile_not_found")
        return Entry(profile_id, profile_id.partition(":")[0], self.profiles[profile_id])

    def resolve_secret(self, profile_id: str) -> str:
        self.calls.append(("resolve_secret", profile_id))
        if profile_id not in self.passwords:
            raise SecretError("studio.secret_missing")
        return self.passwords[profile_id]

    def database_file(self, profile_id: str) -> Path:
        self.calls.append(("database_file", profile_id))
        return self.files[profile_id]

    def limits(self) -> Settings:
        return self.settings


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def sqlite_file(tmp_path: Path) -> Path:
    path = tmp_path / "parts.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE part (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO part (name) VALUES ('bolt'), ('nut')")
    connection.commit()
    connection.close()
    path.chmod(0o600)
    return path


@pytest.fixture
def sqlite_connection(sqlite_file: Path) -> Iterator[tuple[OpenConnection, ConnectionRegistry, Source]]:
    source = Source(
        profiles={"project:parts": Profile("parts", "sqlite", "prod", path="data/parts.db")},
        files={"project:parts": sqlite_file},
    )
    registry = ConnectionRegistry(source)
    try:
        yield registry.open("project:parts"), registry, source
    finally:
        registry.close_all()


def test_sqlite_reads_work(sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source]) -> None:
    connection, _, source = sqlite_connection
    assert connection.read_only is True
    assert connection.fetch("SELECT name FROM part ORDER BY id") == [("bolt",), ("nut",)]
    assert connection.fetch("PRAGMA table_info(part)")[0][1] == "id"
    assert connection.fetch("PRAGMA query_only") == [(1,)]
    # T-1 of stu-006-review.md: a project file is opened only at the path database_file returned.
    assert ("database_file", "project:parts") in source.calls


WRITES = [
    "INSERT INTO part (name) VALUES ('washer')",
    "UPDATE part SET name = 'x'",
    "DELETE FROM part",
    "REPLACE INTO part (id, name) VALUES (1, 'x')",
    "CREATE TABLE other (x)",
    "CREATE TEMP TABLE scratch (x)",
    "CREATE INDEX part_name ON part (name)",
    "CREATE VIEW v AS SELECT 1",
    "DROP TABLE part",
    "ALTER TABLE part ADD COLUMN extra TEXT",
    "PRAGMA user_version = 7",
    "PRAGMA query_only = OFF",
    "PRAGMA journal_mode = DELETE",
    "PRAGMA writable_schema = ON",
    "ANALYZE",
]


@pytest.mark.parametrize("sql", WRITES)
def test_stu_t_010_a_sqlite_write_fails_in_the_database(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source], sqlite_file: Path, sql: str
) -> None:
    """The statement passes Studio's text check and is refused by SQLite itself (not by a text filter)."""
    connection, _, _ = sqlite_connection
    before = _sha(sqlite_file)
    assert check_statement(sql)
    with pytest.raises(TabrionError) as raised:
        connection.fetch(sql)
    assert raised.value.classification is not None
    assert raised.value.classification.kind.value in ("read_only", "permission")
    assert _sha(sqlite_file) == before
    assert connection.fetch("PRAGMA query_only") == [(1,)]


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH DATABASE '{out}' AS other",
        "VACUUM INTO '{out}'",
        "SELECT load_extension('{out}')",
    ],
)
def test_stu_t_024_attach_vacuum_into_and_extensions_are_refused(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source], tmp_path: Path, sql: str
) -> None:
    connection, _, _ = sqlite_connection
    out = tmp_path / "copy.db"
    # Past Studio's text check (which refuses VACUUM anyway): the authorizer denies the ATTACH that VACUUM INTO does.
    with pytest.raises(TabrionError), connection._read() as transaction:
        transaction.execute(sqlalchemy.text(sql.format(out=out)))
    assert not out.exists()


def test_the_database_refuses_writes_even_past_the_studio_handle(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source], sqlite_file: Path, tmp_path: Path
) -> None:
    """mode=ro, query_only and the authorizer are on the DBAPI connection itself: a raw engine connection (never
    offered by Studio) cannot write or attach either."""
    connection, _, _ = sqlite_connection
    before = _sha(sqlite_file)
    engine = connection._db.engine
    with engine.connect() as raw:
        for sql in ("INSERT INTO part (name) VALUES ('x')", f"ATTACH DATABASE '{tmp_path / 'a.db'}' AS a"):
            with pytest.raises(sqlalchemy.exc.DatabaseError):
                raw.exec_driver_sql(sql)
        with pytest.raises(sqlalchemy.exc.DatabaseError):
            raw.exec_driver_sql("PRAGMA query_only = OFF")
    # Outside any transaction, VACUUM INTO would write a copy: the authorizer denies the ATTACH it starts with.
    raw_dbapi = engine.raw_connection()
    try:
        driver = raw_dbapi.driver_connection
        assert isinstance(driver, sqlite3.Connection) and not driver.in_transaction
        with pytest.raises(sqlite3.DatabaseError, match="authoriz"):
            driver.execute(f"VACUUM INTO '{tmp_path / 'v.db'}'")
    finally:
        raw_dbapi.close()
    assert _sha(sqlite_file) == before
    assert not (tmp_path / "a.db").exists() and not (tmp_path / "v.db").exists()


def test_the_authorizer_is_an_allowlist() -> None:
    ok, deny = sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY
    assert sqlite_authorizer(sqlite3.SQLITE_SELECT, None, None, None, None) == ok
    assert sqlite_authorizer(sqlite3.SQLITE_READ, "part", "id", "main", None) == ok
    assert sqlite_authorizer(sqlite3.SQLITE_PRAGMA, "query_only", "ON", None, None) == ok
    assert sqlite_authorizer(sqlite3.SQLITE_PRAGMA, "table_info", "part", None, None) == ok
    assert sqlite_authorizer(sqlite3.SQLITE_PRAGMA, "query_only", "off", None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_PRAGMA, "optimize", None, None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, "LOAD_EXTENSION", None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_ATTACH, "x.db", None, None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_DETACH, "x", None, None, None) == deny
    assert sqlite_authorizer(9999, None, None, None, None) == deny  # an action SQLite adds later


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "studio.statement_empty"),
        (" ;; -- nothing\n", "studio.statement_empty"),
        ("SELECT 1; SELECT 2", "studio.statement_multiple"),
        ("COMMIT; INSERT INTO t VALUES (1)", "studio.statement_multiple"),
        ("BEGIN", "studio.statement_transaction"),
        ("start transaction read write", "studio.statement_transaction"),
        ("/* a /* nested */ comment */ COMMIT", "studio.statement_transaction"),
        ("-- x\n  ROLLBACK", "studio.statement_transaction"),
        ("END", "studio.statement_transaction"),
        ("SAVEPOINT a", "studio.statement_transaction"),
        ("SET TRANSACTION READ WRITE", "studio.statement_transaction"),
        ("set session characteristics as transaction read write", "studio.statement_transaction"),
        ("SET default_transaction_read_only = off", "studio.statement_transaction"),
        ("SET SESSION default_transaction_read_only TO off", "studio.statement_transaction"),
        ("SET LOCAL transaction_read_only = off", "studio.statement_transaction"),
        ("SET ROLE postgres", "studio.statement_transaction"),
        ("SET SESSION AUTHORIZATION postgres", "studio.statement_transaction"),
        ("RESET ALL", "studio.statement_transaction"),
        ("DISCARD ALL", "studio.statement_transaction"),
        ("PREPARE TRANSACTION 'x'", "studio.statement_transaction"),
        ("COPY t TO '/tmp/out.csv'", "studio.statement_refused"),
        ("LOAD 'plugin'", "studio.statement_refused"),
        ("NOTIFY channel", "studio.statement_refused"),
        ("LOCK TABLE t", "studio.statement_refused"),
        ("VACUUM", "studio.statement_refused"),
    ],
)
def test_statements_studio_refuses(sql: str, code: str) -> None:
    with pytest.raises(StatementRefused) as raised:
        check_statement(sql)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select ';' as semicolon;",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SET search_path TO app",
        "EXPLAIN SELECT 1",
        "/* BEGIN */ SELECT 'COMMIT'",
    ],
)
def test_statements_studio_passes_on(sql: str) -> None:
    assert check_statement(sql)


def test_stu_t_011_every_environment_gets_a_read_only_profile() -> None:
    """Studio 1.0 cannot write: no environment, prod included, gets a writable tabrion profile."""
    for environment in ("dev", "test", "prod"):
        profile = studio_profile(Profile("p", "postgresql", environment, host="h"), Settings())
        assert profile.read_only is True
    prod = studio_profile(Profile("p", "postgresql", "prod", host="h", sslmode="verify-full"), Settings())
    assert prod.postgres.statement_timeout_ms == 15_000  # 03 section 5.3
    assert prod.postgres.lock_timeout_ms == 5_000
    dev = studio_profile(Profile("p", "postgresql", "dev", host="h", statement_timeout_ms=1234), Settings())
    assert dev.postgres.statement_timeout_ms == 1234


def test_stu_t_012_a_handle_offers_no_way_to_write() -> None:
    public = {name for name in dir(OpenConnection) if not name.startswith("_")}
    assert public == {"close", "fetch", "health", "read_only", "to_dict"}
    assert not any(hasattr(module, "transaction") for module in (connections_module,))


PG_PROFILE = Profile(
    "measure",
    "postgresql",
    "prod",
    host="db.internal",
    port=6543,
    database="m",
    user="studio_ro",
    password=SecretRef("keyring", ""),
    sslmode="verify-full",
    read_only_role=True,
    statement_timeout_ms=5000,
)


def test_the_driver_gets_only_the_fingerprinted_fields() -> None:
    """L-1 of stu-008-review.md (binding for STU-009): every parameter Studio passes to the driver is in
    TARGET_FIELDS, so a stored password is never sent to a target its fingerprint does not name."""
    url = connection_url(PG_PROFILE, "pw")
    assert (url.drivername, url.host, url.port, url.database, url.username, url.password) == (
        "postgresql",
        "db.internal",
        6543,
        "m",
        "studio_ro",
        "pw",
    )
    assert dict(url.query) == {"sslmode": "verify-full"}
    # Fields outside the target do not reach the driver ...
    others: dict[str, Any] = {
        "name": "other",
        "environment": "dev",
        "password": SecretRef("env", "X"),
        "read_only_role": False,
    }
    for key, value in {**others, "statement_timeout_ms": 1}.items():
        assert connection_url(replace(PG_PROFILE, **{key: value}), "pw") == url, key
    # ... and every target field that changes the URL is fingerprinted.
    changes: dict[str, Any] = {"host": "elsewhere", "port": 5432, "database": "n", "user": "u", "sslmode": "disable"}
    assert set(changes) <= set(TARGET_FIELDS)
    for key, value in changes.items():
        assert connection_url(replace(PG_PROFILE, **{key: value}), "pw") != url, key
    sqlite = connection_url(Profile("p", "sqlite", "dev", path="x.db"), None, Path("/real/x.db"))
    assert (sqlite.drivername, sqlite.database, sqlite.host, dict(sqlite.query)) == ("sqlite", "/real/x.db", None, {})


def test_a_connection_whose_profile_changes_is_closed(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source], sqlite_file: Path, tmp_path: Path
) -> None:
    """A re-targeted, removed or no longer trusted profile makes its open connection stale (C-1, T-1)."""
    connection, registry, source = sqlite_connection
    assert registry.get(connection.id) is connection
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], path="data/other.db")
    with pytest.raises(RegistryError) as raised:
        registry.get(connection.id)
    assert raised.value.code == "studio.connection_stale"
    with pytest.raises(RegistryError):
        registry.get(connection.id)  # closed and gone
    assert registry.list() == []
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], path="data/parts.db")
    again = registry.open("project:parts")
    other = tmp_path / "swapped.db"
    sqlite_file.rename(other)
    source.files["project:parts"] = other  # database_file now resolves elsewhere (a link swapped in, T-1)
    assert registry.list() == []
    with pytest.raises(RegistryError):
        registry.get(again.id)
    del source.profiles["project:parts"]
    with pytest.raises(SecretError):
        registry.open("project:parts")


def test_a_missing_sqlite_file_is_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    source = Source(profiles={"user:m": Profile("m", "sqlite", "dev", path=str(missing))}, files={"user:m": missing})
    with pytest.raises(RegistryError) as raised:
        ConnectionRegistry(source).open("user:m")
    assert raised.value.code == "studio.database_missing"
    assert not missing.exists()


def test_the_registry_reuses_and_limits_connections(sqlite_file: Path) -> None:
    profiles = {f"user:p{i}": Profile(f"p{i}", "sqlite", "dev", path=f"p{i}.db") for i in range(3)}
    source = Source(profiles=profiles, files=dict.fromkeys(profiles, sqlite_file))
    registry = ConnectionRegistry(source, max_open=2)
    try:
        first = registry.open("user:p0")
        assert registry.open("user:p0") is first
        registry.open("user:p1")
        with pytest.raises(RegistryError) as raised:
            registry.open("user:p2")
        assert raised.value.code == "studio.connection_limit"
        assert registry.close(first.id) and not registry.close(first.id)
        registry.open("user:p2")
    finally:
        registry.close_all()
    assert registry.list() == []


# ---------------------------------------------------------------------------------------------------------------------
# PostgreSQL (STU-T-010..013 against a real server)
# ---------------------------------------------------------------------------------------------------------------------

postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")


@pytest.fixture
def pg() -> Iterator[tuple[OpenConnection, sqlalchemy.Engine]]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    with admin.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS studio_ro_probe")
        c.exec_driver_sql("CREATE TABLE studio_ro_probe (id int)")
        c.exec_driver_sql("INSERT INTO studio_ro_probe VALUES (1)")
    profile = Profile(
        "pg",
        "postgresql",
        "prod",
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        password=SecretRef("keyring", ""),
    )
    source = Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    try:
        yield registry.open("user:pg"), admin
    finally:
        registry.close_all()
        with admin.begin() as c:
            c.exec_driver_sql("DROP TABLE IF EXISTS studio_ro_probe")
        admin.dispose()


def _rows(admin: sqlalchemy.Engine) -> int:
    with admin.connect() as c:
        return int(c.exec_driver_sql("SELECT count(*) FROM studio_ro_probe").scalar_one())


@pytest.mark.postgres
@postgres
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO studio_ro_probe VALUES (2)",
        "UPDATE studio_ro_probe SET id = 3",
        "DELETE FROM studio_ro_probe",
        "CREATE TABLE studio_ro_other (x int)",
        "CREATE TEMP TABLE scratch (x int)",
        "DROP TABLE studio_ro_probe",
        "TRUNCATE studio_ro_probe",
    ],
)
def test_stu_t_010_a_postgres_write_fails_in_the_database(
    pg: tuple[OpenConnection, sqlalchemy.Engine], sql: str
) -> None:
    connection, admin = pg
    assert check_statement(sql)
    with pytest.raises(TabrionError):
        connection.fetch(sql)
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_stu_t_013_the_session_and_every_transaction_are_read_only(
    pg: tuple[OpenConnection, sqlalchemy.Engine],
) -> None:
    connection, admin = pg
    assert PG_URL is not None
    password = sqlalchemy.make_url(PG_URL).password
    assert password and password not in connection.url and "***" in connection.url  # tabrion.safe_url
    assert connection.fetch("SHOW default_transaction_read_only") == [("on",)]
    assert connection.fetch("SHOW transaction_read_only") == [("on",)]
    assert connection.fetch("SHOW application_name") == [("tabrion-studio",)]
    assert connection.fetch("SELECT count(*) FROM studio_ro_probe") == [(1,)]
    # Even with Studio's text check bypassed, a switch to READ WRITE in the same transaction is caught by tabrion's
    # check before COMMIT, and the write is rolled back (I-56).
    with pytest.raises(TabrionError), connection._read() as transaction:
        transaction.execute(sqlalchemy.text("SET TRANSACTION READ WRITE"))
        transaction.execute(sqlalchemy.text("INSERT INTO studio_ro_probe VALUES (5)"))
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_the_known_gap_of_multiple_statements_is_closed_by_the_text_check(
    pg: tuple[OpenConnection, sqlalchemy.Engine],
) -> None:
    """03 section 5.1: PostgreSQL runs a multi-statement text in one round trip, so ``COMMIT; BEGIN READ WRITE;
    ...`` would leave Studio's read-only transaction. check_statement refuses it before it reaches the server."""
    connection, admin = pg
    for sql in (
        "ROLLBACK; BEGIN READ WRITE; INSERT INTO studio_ro_probe VALUES (9); COMMIT",
        "COMMIT; BEGIN; SET TRANSACTION READ WRITE; INSERT INTO studio_ro_probe VALUES (10); COMMIT",
    ):
        with pytest.raises(StatementRefused):
            connection.fetch(sql)
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_a_wrong_password_is_a_db_problem_without_server_text(pg: tuple[OpenConnection, sqlalchemy.Engine]) -> None:
    from tabrion_studio.problems import db_problem

    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    profile = Profile("x", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username)
    source = Source(profiles={"user:x": profile}, passwords={"user:x": "wrong-password-canary"})
    with pytest.raises(Exception) as raised:
        ConnectionRegistry(source).open("user:x")
    doc: dict[str, Any] = db_problem(getattr(raised.value, "classification", raised.value)).to_dict()
    assert doc["code"] == "db.connection"
    assert "wrong-password-canary" not in str(doc) and "password authentication" not in str(doc)
