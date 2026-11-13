"""The statement check knows every way PostgreSQL calls a function by name (B-1 and B-2 of the security assessment).

``check_statement`` refuses the functions of ``readonly._REFUSED_FUNCTIONS`` and ``_REFUSED_PREFIXES`` in each call
form the server accepts, not only ``name(``; reads that merely mention such a name stay allowed. The check-in reset
closes the session's dblink connections. The PostgreSQL tests need ``TABRION_STUDIO_TEST_POSTGRES`` (as in
``test_read_only.py``); the dblink test also needs ``TABRION_STUDIO_TEST_POSTGRES_SUPERUSER`` to install dblink when
the database does not have it (and removes it again only then).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import os
import secrets
import time
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.schema import Profile, SecretRef, Settings
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.readonly import StatementRefused, check_statement

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")

#: Refused names from each part of the list: an exact name, a prefix family (``dblink``), a lock that would stay held
#: on the pooled connection, and the pg_surgery and pg_visibility functions (B-2).
NAMES = [
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_advisory_lock",
    "dblink_exec",
    "dblink_connect",
    "lo_import",
    "pg_file_unlink",
    "heap_force_kill",
    "heap_force_freeze",
    "pg_truncate_visibility_map",
]

#: Each way the server reads ``{f}`` as a call: with ``(``, and as an attribute of a value (``x.f`` and ``(x).f``
#: mean ``f(x)``), with quoted identifiers, schema qualification, and white space or comments between the parts.
CALL_FORMS = [
    "SELECT {f}(1)",
    "SELECT pg_catalog.{f}(1)",
    'SELECT "pg_catalog"."{f}" (1)',
    "SELECT {f} /* c */ (1)",
    "SELECT {f} -- c\n(1)",
    "SELECT (1).{f}",
    "SELECT ('x'::text).{f}",
    'SELECT (1)."{f}"',
    "SELECT (1) . {f}",
    "SELECT (1)./* c */{f}",
    "SELECT (1).-- c\n{f}",
    "SELECT (1)\n.\n{f}",
    "SELECT x.{f} FROM (SELECT 1::bigint AS x) s",
    "SELECT s.x.{f} FROM (SELECT 1::bigint AS x) s",
    "SELECT (s.x).{f} FROM (SELECT 1::bigint AS x) s",
    'SELECT "s"."x"."{f}" FROM (SELECT 1::bigint AS x) s',
    "SELECT (ARRAY[1])[1].{f}",
    "SELECT 1 WHERE (1).{f} IS NOT NULL",
    "WITH a AS (SELECT (1).{f}) SELECT * FROM a",
    "EXPLAIN ANALYZE SELECT (1).{f}",
    # A literal does not hide the attribute from either way the server lexes '...' (standard_conforming_strings).
    "SELECT 'a\\', (1).{f} --'",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("form", CALL_FORMS)
def test_every_call_form_of_a_refused_function_is_refused(form: str, name: str) -> None:
    with pytest.raises(StatementRefused) as raised:
        check_statement(form.format(f=name))
    assert raised.value.code == "studio.statement_refused"


@pytest.mark.parametrize(
    "sql",
    [
        # Composite and qualified column access without a refused function.
        "SELECT (t).id FROM part t",
        "SELECT (t).* FROM part t",
        "SELECT t.id, part.name FROM part t, part",
        "SELECT (s.r).f1 FROM (SELECT ROW(1, 2) AS r) s",
        "SELECT ('abc'::text).upper",
        "SELECT (ARRAY[1, 2])[1]",
        # A refused function's name as a bare column, an alias or a sort key, never called.
        "SELECT pg_terminate_backend FROM part",
        "SELECT 1 AS dblink_exec, 2 AS heap_force_kill",
        "SELECT id AS pg_truncate_visibility_map FROM part ORDER BY pg_truncate_visibility_map",
        'SELECT "pg_advisory_lock" FROM part',
        # Names that only start like a refused one, or the name inside a literal or a comment.
        "SELECT pg_terminate_backend_count, t.heap_force_kills FROM part t",
        "SELECT 'x.pg_terminate_backend', '(1).dblink_exec'",
        "SELECT 1 -- (1).pg_terminate_backend",
        "SELECT /* x.heap_force_kill */ 1",
        "SELECT $$ (1).dblink_exec $$",
        # Functions that only read, also in attribute form.
        "SELECT pg_backend_pid()",
        "SELECT pg_current_xact_id_if_assigned()",
        "SELECT (1).pg_typeof",
    ],
)
def test_reads_that_mention_but_do_not_call_a_refused_function_pass(sql: str) -> None:
    assert check_statement(sql)


# ---------------------------------------------------------------------------------------------------------------------
# The check-in reset closes dblink connections (unit, with a recording cursor)
# ---------------------------------------------------------------------------------------------------------------------


class DatabaseError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@dataclass
class Cursor:
    schema: str | None
    unnamed: bool = True
    failing: str | None = None
    statements: list[str] = field(default_factory=list)
    _row: tuple[str] | None = None

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        self._row = None
        if "pg_extension" in statement:
            self._row = None if self.schema is None else (self.schema,)
        elif statement.endswith(".dblink_disconnect()"):
            if self.failing:
                raise DatabaseError(self.failing)
            if not self.unnamed:
                raise DatabaseError("08003")

    def fetchone(self) -> tuple[str] | None:
        return self._row


@dataclass
class DriverConnection:
    cursor_: Cursor
    calls: list[str] = field(default_factory=list)

    def rollback(self) -> None:
        self.calls.append("rollback")

    def commit(self) -> None:
        self.calls.append("commit")

    def cursor(self) -> Cursor:
        return self.cursor_


@dataclass
class Record:
    invalidated: list[BaseException] = field(default_factory=list)

    def invalidate(self, exc: BaseException) -> None:
        self.invalidated.append(exc)


def _dblink_statements(cursor: Cursor) -> list[str]:
    return cursor.statements[len(tabrion_compat._RESET_STATEMENTS) :]


def test_the_reset_does_nothing_more_without_dblink() -> None:
    cursor = Cursor(schema=None)
    record = Record()
    tabrion_compat.reset_session(DriverConnection(cursor), record)
    assert record.invalidated == []
    assert len(_dblink_statements(cursor)) == 1  # the lookup of the extension only
    assert not any("dblink_" in statement for statement in cursor.statements)


@pytest.mark.parametrize("unnamed", [True, False])
def test_the_reset_closes_named_and_unnamed_dblink_connections(unnamed: bool) -> None:
    cursor = Cursor(schema='"ext"', unnamed=unnamed)
    connection = DriverConnection(cursor)
    record = Record()
    tabrion_compat.reset_session(connection, record)
    assert record.invalidated == []
    assert connection.calls == ["rollback", "commit"]
    statements = _dblink_statements(cursor)
    assert statements[1] == (
        'SELECT "ext".dblink_disconnect(name) FROM pg_catalog.unnest("ext".dblink_get_connections()) AS name'
    )
    assert statements[3] == 'SELECT "ext".dblink_disconnect()'
    rolled_back = "ROLLBACK TO SAVEPOINT studio_dblink_reset" in statements
    assert rolled_back is not unnamed  # only "no unnamed connection" is expected
    assert statements[-1] == "RELEASE SAVEPOINT studio_dblink_reset"


def test_a_connection_whose_dblink_connections_cannot_be_closed_leaves_the_pool() -> None:
    cursor = Cursor(schema="public", failing="42501")
    connection = DriverConnection(cursor)
    record = Record()
    tabrion_compat.reset_session(connection, record)
    assert len(record.invalidated) == 1  # dropping the connection ends its dblink connections too
    assert "commit" not in connection.calls


# ---------------------------------------------------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------------------------------------------------

postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")


@dataclass
class Entry:
    id: str
    source: str
    profile: Profile


@dataclass
class Source:
    profile: Profile
    password: str

    def entry(self, profile_id: str) -> Entry:
        return Entry(profile_id, "user", self.profile)

    def resolve_entry_secret(self, _entry: Entry) -> str:
        return self.password

    def database_file(self, _entry: Entry) -> Any:
        raise AssertionError("no file for a PostgreSQL profile")

    def limits(self) -> Settings:
        return Settings()


def _registry(url: sqlalchemy.URL) -> ConnectionRegistry:
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
    return ConnectionRegistry(Source(profile, url.password or ""))


@pytest.fixture
def pg() -> Iterator[tuple[OpenConnection, sqlalchemy.Engine, str]]:
    """A PROD connection of the test role, an admin engine of the same role, and a table made for the test with a
    column named like a refused function."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    table = f"studio_call_forms_{secrets.token_hex(4)}"
    with admin.begin() as c:
        c.exec_driver_sql(f"CREATE TABLE {table} (id int, pg_terminate_backend int)")
        c.exec_driver_sql(f"INSERT INTO {table} VALUES (1, 2)")  # noqa: S608 (a test-made name)
    registry = _registry(url)
    try:
        yield registry.open("user:pg", "prod"), admin, table
    finally:
        registry.close_all()
        with admin.begin() as c:
            c.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
        admin.dispose()


@pytest.mark.postgres
@postgres
def test_pg_composite_and_column_reads_run(pg: tuple[OpenConnection, sqlalchemy.Engine, str]) -> None:
    connection, _, table = pg
    for _ in range(3):  # whichever of the pool's connections it lands on
        reads = f"SELECT (t).id, t.id, pg_terminate_backend FROM {table} t"  # noqa: S608 (a test-made name)
        assert connection.fetch(reads, catalog=False) == [(1, 1, 2)]
        alias = f"SELECT pg_terminate_backend AS dblink_exec FROM {table}"  # noqa: S608 (a test-made name)
        assert connection.fetch(alias, catalog=False) == [(2,)]
        # The server reads attribute notation as a call (here of a function that only computes a value), which is
        # why the check counts a refused name after "." as one.
        assert connection.fetch("SELECT ('abc'::text).upper", catalog=False) == [("ABC",)]


@pytest.mark.postgres
@postgres
@pytest.mark.parametrize("form", CALL_FORMS)
def test_pg_call_forms_are_refused_before_the_server(
    pg: tuple[OpenConnection, sqlalchemy.Engine, str], form: str
) -> None:
    connection, _, _ = pg
    with pytest.raises(StatementRefused):
        connection.fetch(form.format(f="pg_cancel_backend"), catalog=False)


def _dblink_sessions(admin: sqlalchemy.Engine, application: str) -> int:
    with admin.connect() as c:
        return int(
            c.exec_driver_sql(
                "SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE application_name = %s", (application,)
            ).scalar_one()
        )


@pytest.fixture
def dblink() -> Iterator[None]:
    """dblink in the test database: installed by the superuser if missing, and removed again only in that case."""
    assert PG_URL is not None and PG_SUPERUSER_URL is not None
    superuser = sqlalchemy.create_engine(sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"))
    created = False
    try:
        with superuser.begin() as c:
            if c.exec_driver_sql("SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'dblink'").first() is None:
                if c.exec_driver_sql("SELECT 1 FROM pg_available_extensions WHERE name = 'dblink'").first() is None:
                    pytest.skip("the server has no dblink extension")
                c.exec_driver_sql("CREATE EXTENSION dblink")
                created = True
        yield
    finally:
        if created:
            with superuser.begin() as c:
                c.exec_driver_sql("DROP EXTENSION IF EXISTS dblink")
        superuser.dispose()


@pytest.mark.postgres_superuser
@pytest.mark.skipif(
    PG_URL is None or PG_SUPERUSER_URL is None,
    reason="TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed",
)
def test_pg_the_check_in_reset_closes_dblink_connections(dblink: None) -> None:
    """Past the text check (what a database function could do): a dblink connection, named or not, does not outlive
    the read that opened it on a pooled Studio connection."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    application = f"studio-dblink-{secrets.token_hex(4)}"
    target = (
        f"host={url.host} port={url.port or 5432} dbname={url.database} user={url.username} "
        f"password={url.password} application_name={application}"
    )
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    registry = _registry(url)
    try:
        connection = registry.open("user:pg", "prod")
        for statement in (
            "SELECT dblink_connect('studio_named', %(t)s)",
            "SELECT dblink_connect(%(t)s)",
        ):
            with (  # one read, past Studio's text check
                connection._read() as transaction,
                tabrion_compat.stream(
                    transaction, statement, {"t": target}, cancel=tabrion_compat.Cancel(), server_side=False
                ) as cursor,
            ):
                cursor.fetch(1)
            # The read ended and its connection went back to the pool through the check-in reset.
            deadline = time.monotonic() + 10
            while _dblink_sessions(admin, application) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert _dblink_sessions(admin, application) == 0
        # The pool's connections still work afterwards (no unnamed connection is not an error).
        for _ in range(3):
            assert connection.fetch("SELECT 1") == [(1,)]
    finally:
        registry.close_all()
        admin.dispose()
