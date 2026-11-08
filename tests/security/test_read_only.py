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
import shutil
import sqlite3
from typing import Any

import pytest
import sqlalchemy
import tabrion
from tabrion.errors import TabrionError

from tabrion_studio.config.schema import TARGET_FIELDS, Profile, SecretRef, Settings
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.logs import scrub
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import (
    ConnectionRegistry,
    HealthError,
    OpenConnection,
    RegistryError,
    connection_url,
    driver_options,
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
    resolved: list[Entry] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    def entry(self, profile_id: str) -> Entry:
        if profile_id not in self.profiles:
            raise SecretError("studio.profile_not_found")
        entry = Entry(profile_id, profile_id.partition(":")[0], self.profiles[profile_id])
        self.entries.append(entry)
        return entry

    def resolve_entry_secret(self, entry: Entry) -> str:
        self.calls.append(("resolve_entry_secret", entry.id))
        self.resolved.append(entry)
        if entry.id not in self.passwords:
            raise SecretError("studio.secret_missing")
        return self.passwords[entry.id]

    def database_file(self, profile_id: str) -> Path:
        self.calls.append(("database_file", profile_id))
        return self.files[profile_id]

    def limits(self) -> Settings:
        return self.settings


def below_the_check(connection: OpenConnection, sql: str) -> None:
    """``sql`` in one ``Database.read()`` of ``connection``, past Studio's text check: STU-T-010 shows that the
    database, not the check, is the boundary."""
    with (
        connection._read() as transaction,
        tabrion_compat.stream(transaction, sql, {}, cancel=tabrion_compat.Cancel(), server_side=False) as cursor,
    ):
        cursor.fetch(1)


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
    """SQLite itself refuses the statement, sent below Studio's text check (which refuses all but the pragmas too)."""
    connection, _, _ = sqlite_connection
    before = _sha(sqlite_file)
    if not sql.startswith("PRAGMA"):
        with pytest.raises(StatementRefused):
            check_statement(sql)
    with pytest.raises(TabrionError) as raised:
        below_the_check(connection, sql)
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
        ("SET standard_conforming_strings = off", "studio.statement_transaction"),
        ("set session backslash_quote to on", "studio.statement_transaction"),
        ("SELECT set_config('standard_conforming_strings','off',false)", "studio.statement_refused"),
        ("SELECT pg_catalog.SET_CONFIG ('role', 'postgres', false)", "studio.statement_refused"),
        # R-L1 of the re-review: the spellings that passed the regex.
        ("SELECT \"set_config\"('standard_conforming_strings','off',false)", "studio.statement_refused"),
        ("SELECT set_config/**/('role','postgres',false)", "studio.statement_refused"),
        ("SELECT set_config /* a /* nested */ comment */ ('role','postgres',false)", "studio.statement_refused"),
        ("SELECT set_config -- line\n('role','postgres',false)", "studio.statement_refused"),
        ("SELECT \"pg_catalog\".\"set_config\"\t(\n'role','postgres',false)", "studio.statement_refused"),
        ("SELECT * FROM set_config('role','postgres',false)", "studio.statement_refused"),
        ("SELECT U&\"\\0073et_config\"('role','postgres',false)", "studio.statement_refused"),
        # A literal must not hide the call from either way the server lexes '...' (standard_conforming_strings).
        ("SELECT 'a\\', set_config('role','postgres',false) --'", "studio.statement_refused"),
        ("SELECT E'a\\'', set_config('role','postgres',false) --'", "studio.statement_refused"),
        ("SELECT $$x$$, set_config('role','postgres',false)", "studio.statement_refused"),
        # R2-L1 of re-review 2: code the check cannot see into.
        ("DO $$BEGIN PERFORM set_config('role','postgres',false); END$$", "studio.statement_refused"),
        ("do $x$ BEGIN PERFORM pg_notify('c', 'x'); END $x$", "studio.statement_refused"),
        ("/* c */ DO LANGUAGE plpgsql $$BEGIN EXECUTE 'SELECT pg_advisory_lock(1)'; END$$", "studio.statement_refused"),
        (
            "SELECT query_to_xml('SELECT set_config(''standard_conforming_strings'',''off'',false)', true, true, '')",
            "studio.statement_refused",
        ),
        ("SELECT pg_catalog.query_to_xml_and_xmlschema('SELECT 1', true, true, '')", "studio.statement_refused"),
        ("SELECT cursor_to_xml('c', 1, true, true, '')", "studio.statement_refused"),
        ("SELECT * FROM ts_stat('SELECT pg_advisory_lock(1)::text::tsvector')", "studio.statement_refused"),
        ("SELECT * FROM crosstab('SELECT 1, 2, 3') AS t(a int, b int)", "studio.statement_refused"),
        ("SELECT * FROM dblink('dbname=x', 'SELECT 1') AS t(a int)", "studio.statement_refused"),
        # I-R1 of the re-review: calls that reach other sessions or outlive Studio's transaction.
        ("SELECT pg_notify('channel', 'x')", "studio.statement_refused"),
        ("SELECT pg_advisory_lock(1)", "studio.statement_refused"),
        ("SELECT pg_try_advisory_lock_shared(1, 2)", "studio.statement_refused"),
        # M-1, M-2 and I-2 of stu-011-review.md: first words outside the allowlist.
        ("DECLARE c CURSOR WITH HOLD FOR SELECT pg_sleep(5)", "studio.statement_refused"),
        ("declare c binary no scroll cursor for select 1", "studio.statement_refused"),
        ("FETCH ALL FROM c", "studio.statement_refused"),
        ("MOVE FORWARD 1 IN c", "studio.statement_refused"),
        ("CLOSE ALL", "studio.statement_refused"),
        ("SET statement_timeout = 0", "studio.statement_refused"),
        ("SET LOCAL lock_timeout = 0", "studio.statement_refused"),
        ("set search_path to public, pg_catalog", "studio.statement_refused"),
        ("DEALLOCATE ALL", "studio.statement_refused"),
        ("INSERT INTO part VALUES (1)", "studio.statement_refused"),
        ("CREATE TEMP TABLE t (x int)", "studio.statement_refused"),
        ("CALL p()", "studio.statement_refused"),
        ("GRANT SELECT ON part TO public", "studio.statement_refused"),
        ("EXPLAIN ANALYZE DELETE FROM part", "studio.statement_refused"),
        ("EXPLAIN (ANALYZE, BUFFERS) INSERT INTO part VALUES (1)", "studio.statement_refused"),
        ("EXPLAIN DECLARE c CURSOR FOR SELECT 1", "studio.statement_refused"),
        ("EXPLAIN", "studio.statement_refused"),
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
        "EXPLAIN SELECT 1",
        "EXPLAIN (ANALYZE, FORMAT JSON) SELECT 1",
        "EXPLAIN QUERY PLAN SELECT 1",
        "explain analyze verbose with x as (select 1) select * from x",
        "(SELECT 1) UNION (SELECT 2)",
        "TABLE part",
        "VALUES (1), (2)",
        "SHOW statement_timeout",
        "PRAGMA table_list",
        "/* BEGIN */ SELECT 'COMMIT'",
        # Not a call: a literal, a comment, a column and the transaction-level advisory lock.
        "SELECT 'set_config(' AS text",
        "SELECT 1 /* set_config( */",
        "SELECT $body$ set_config('role','x',false) $body$",
        "SELECT set_config_value FROM settings",
        'SELECT "SET_CONFIG"(1)',
        "SELECT pg_advisory_xact_lock(1)",
        "SELECT current_setting('role')",
    ],
)
def test_statements_studio_passes_on(sql: str) -> None:
    assert check_statement(sql)


#: R2-I2 of re-review 2: each text hides the call under one way of lexing '...' and shows it under the other.
#: With standard_conforming_strings on, 'x\'' is the string x\ and then an open quote; with it off, the string x'.
HIDDEN_UNLESS_BACKSLASH = "SELECT 'x\\'' , set_config('role','postgres',false) -- '"
#: E'\'' is always the string ' (backslash escapes in E'...'); 'a\' is a\ with standard_conforming_strings on, so
#: the call is plain to the server there, while a reader that treats E'...' like '...' (or '...' like E'...') sees a
#: literal.
HIDDEN_UNLESS_E_ESCAPES = "SELECT E'\\'', 'a\\', set_config('role','postgres',false) -- '"


@pytest.mark.parametrize("sql", [HIDDEN_UNLESS_BACKSLASH, HIDDEN_UNLESS_E_ESCAPES])
def test_r2_i2_a_literal_cannot_hide_a_call_from_either_lexing(sql: str) -> None:
    """Kills mutants T1 (one reading only) and T6 (E'...' without escapes) of re-review 2."""
    with pytest.raises(StatementRefused) as raised:
        check_statement(sql)
    assert raised.value.code == "studio.statement_refused"


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
    assert public == {"close", "fetch", "health", "inspect", "prepare", "read_only", "stream", "to_dict"}
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
    assert (sqlite.drivername, sqlite.host, dict(sqlite.query)) == ("sqlite", None, {})
    assert sqlite.database == str(Path("/real/x.db"))  # the path as given (backslashes on Windows)


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
    shutil.copyfile(sqlite_file, other)  # not a rename: Windows keeps an open file in place
    source.files["project:parts"] = other  # database_file now resolves elsewhere (a link swapped in, T-1)
    assert registry.list() == []
    with pytest.raises(RegistryError):
        registry.get(again.id)
    del source.profiles["project:parts"]
    with pytest.raises(SecretError):
        registry.open("project:parts")


def test_sqlite_refuses_several_statements_itself(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source], sqlite_file: Path
) -> None:
    """Past Studio's text check, sqlite3 runs one statement per execute (H-1 holds for SQLite in the driver)."""
    connection, _, _ = sqlite_connection
    before = _sha(sqlite_file)
    with pytest.raises((TabrionError, sqlite3.ProgrammingError)), connection._read() as transaction:
        transaction.fetch_all(sqlalchemy.text("SELECT 1; INSERT INTO part (name) VALUES ('x')"))
    assert _sha(sqlite_file) == before


def test_a_changed_environment_closes_the_connection(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source],
) -> None:
    """Review I-3 (mutant R8): a profile moved from prod to dev is not the one the handle was opened for."""
    connection, registry, source = sqlite_connection
    source.profiles["project:parts"] = replace(source.profiles["project:parts"], environment="dev")
    with pytest.raises(RegistryError) as raised:
        registry.get(connection.id)
    assert raised.value.code == "studio.connection_stale"


def test_driver_options_keep_libpq_from_finding_a_password() -> None:
    """M-1: without a password, libpq gets the empty password and a passfile that cannot exist; every connection
    uses prepared statements (H-1)."""
    without = driver_options(None)
    assert without["password"] == "" and without["prepare_threshold"] == 0
    assert not Path(without["passfile"]).exists() and Path(without["passfile"]).parent.is_file()
    assert "password" not in driver_options("pw")  # never overrides the URL's password


def test_a_connection_test_shares_the_limit(sqlite_file: Path) -> None:
    """Review I-4: tests count against max_open, so concurrent tests cannot open engines without bound."""
    source = Source(profiles={"user:p": Profile("p", "sqlite", "dev", path="p.db")}, files={"user:p": sqlite_file})
    registry = ConnectionRegistry(source, max_open=1)
    try:
        assert registry.test("user:p")["ok"] is True
        registry.open("user:p")
        with pytest.raises(RegistryError) as raised:
            registry.test("user:p")
        assert raised.value.code == "studio.connection_limit"
    finally:
        registry.close_all()


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
    with pytest.raises(StatementRefused):
        check_statement(sql)
    with pytest.raises(TabrionError):
        below_the_check(connection, sql)
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


#: The escape of H-1 in stu-009-review.md, verbatim: one statement to the splitter with
#: ``standard_conforming_strings = off``, six to the server.
ESCAPE = "SELECT '\\''; ROLLBACK; BEGIN READ WRITE; INSERT INTO studio_ro_probe VALUES (42); COMMIT; SELECT ''''"


@pytest.mark.postgres
@postgres
def test_h1_the_standard_conforming_strings_escape_does_not_write(pg: tuple[OpenConnection, sqlalchemy.Engine]) -> None:
    connection, admin = pg
    # Studio's text check refuses the first step ...
    with pytest.raises(StatementRefused):
        connection.fetch("SELECT set_config('standard_conforming_strings','off',false)")
    # ... and past it, the server refuses the text: every statement is a prepared statement (extended protocol).
    with connection._read() as transaction:
        transaction.fetch_all(sqlalchemy.text("SELECT set_config('standard_conforming_strings','off',false)"))
    assert check_statement(ESCAPE)  # the splitter sees one statement
    for _ in range(3):
        with pytest.raises(TabrionError) as raised, connection._read() as transaction:
            transaction.fetch_all(sqlalchemy.text(ESCAPE))
        assert raised.value.classification is not None and raised.value.classification.sqlstate == "42601"
    with pytest.raises(TabrionError):
        connection.fetch(ESCAPE)
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_h1_the_server_refuses_several_statements_past_the_text_check(
    pg: tuple[OpenConnection, sqlalchemy.Engine],
) -> None:
    connection, admin = pg
    for sql in (
        "SELECT 1; SELECT 2",
        "ROLLBACK; BEGIN READ WRITE; INSERT INTO studio_ro_probe VALUES (9); COMMIT",
    ):
        with pytest.raises(TabrionError) as raised, connection._read() as transaction:
            transaction.fetch_all(sqlalchemy.text(sql))
        assert raised.value.classification is not None and raised.value.classification.sqlstate == "42601"
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_m1_pgpassword_is_never_sent_for_a_profile_without_a_password(
    pg: tuple[OpenConnection, sqlalchemy.Engine], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A (project) profile without a password reference must not make libpq send the user's PGPASSWORD or .pgpass
    password to the host the profile names."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    assert url.password
    passfile = tmp_path / "pgpass"
    passfile.write_text(f"*:*:*:*:{url.password}\n", encoding="utf-8")
    passfile.chmod(0o600)
    monkeypatch.setenv("PGPASSWORD", url.password)
    monkeypatch.setenv("PGPASSFILE", str(passfile))
    profile = Profile("x", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username)
    source = Source(profiles={"project:x": profile})
    with pytest.raises(HealthError) as raised:
        ConnectionRegistry(source).open("project:x")
    assert raised.value.classification.kind.value == "connection"


@pytest.mark.postgres
@postgres
def test_l1_the_password_is_resolved_from_the_entry_the_url_comes_from(
    pg: tuple[OpenConnection, sqlalchemy.Engine],
) -> None:
    """L-1 and review I-3 (mutant R9): one read of the profile per connect, and the password is redacted in logs."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    profile = Profile(
        "y",
        "postgresql",
        "dev",
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        password=SecretRef("keyring", ""),
    )
    source = Source(profiles={"user:y": profile}, passwords={"user:y": url.password or ""})
    registry = ConnectionRegistry(source)
    try:
        registry.open("user:y")
    finally:
        registry.close_all()
    # I-R2 of the re-review (mutant N5): one read, and the very object of that read (Entry compares by value).
    assert len(source.entries) == 1
    assert len(source.resolved) == 1 and source.resolved[0] is source.entries[0]
    assert url.password and url.password not in scrub(f"connect with {url.password}")


@dataclass
class SwappingSource(Source):
    """A profile file that another program rewrites between two reads: the second read names another host."""

    def entry(self, profile_id: str) -> Entry:
        entry = super().entry(profile_id)
        if len(self.entries) > 1:
            entry = Entry(entry.id, entry.source, replace(entry.profile, host="swapped.example"))
            self.entries[-1] = entry
        return entry


def test_i_r2_one_profile_read_per_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant N5 of the re-review: the secret must come from the entry the URL is built from, read once."""
    urls: list[sqlalchemy.URL] = []

    def from_url(url: sqlalchemy.URL, **_options: Any) -> Any:
        urls.append(url)
        raise RegistryError("studio.connection_failed")

    monkeypatch.setattr(tabrion.Database, "from_url", from_url)
    profile = Profile("y", "postgresql", "dev", host="db.example", user="u", password=SecretRef("keyring", ""))
    source = SwappingSource(profiles={"user:y": profile}, passwords={"user:y": "pw-1234"})
    with pytest.raises(RegistryError):
        ConnectionRegistry(source).open("user:y")
    assert len(source.entries) == 1
    assert [entry is source.entries[0] for entry in source.resolved] == [True]
    assert [url.host for url in urls] == ["db.example"]
