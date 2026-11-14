"""STU-009: every Studio connection is read-only, and a write attempt fails in the database (STU-T-010..013, -024).

The SQLite tests always run. The PostgreSQL tests need ``TABRION_STUDIO_TEST_POSTGRES``: a libpq URL whose role owns
the database (so that only Studio's settings, not missing privileges, stop the writes), for example
``postgresql://studio_owner:pw@127.0.0.1:5432/studio_test``. The tests create and drop their own table.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass, field, replace
import hashlib
import itertools
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import threading
import time
from typing import Any

import pytest
import sqlalchemy
import tabrion
from tabrion.errors import TabrionError
from tabrion.sql import split_statements

from tabrion_studio.config.schema import TARGET_FIELDS, Profile, SecretRef, Settings
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.logs import scrub
from tabrion_studio.problems import db_problem
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services import readonly as readonly_module
from tabrion_studio.services import tabrion_compat
from tabrion_studio.services.connections import (
    ConnectionRegistry,
    HealthError,
    OpenConnection,
    RegistryError,
    connection_url,
    driver_options,
    refuses_cleartext,
    studio_profile,
)
from tabrion_studio.services.readonly import StatementRefused, check_statement, first_word, sqlite_authorizer
from tabrion_studio.services.sqltext import placeholders

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")


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

    def database_file(self, entry: Entry) -> Path:
        self.calls.append(("database_file", entry.id))
        return self.files[entry.id]

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
        yield registry.open("project:parts", "prod"), registry, source
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
    # F-2 of the STU-016 review: the function branch is an allowlist too.
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, "fts3_tokenizer", None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, "FTS3_TOKENIZER", None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, "some_function_sqlite_adds_later", None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, None, None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, "count", None, None) == ok
    assert sqlite_authorizer(sqlite3.SQLITE_FUNCTION, None, "JSON_EXTRACT", None, None) == ok
    assert sqlite_authorizer(sqlite3.SQLITE_ATTACH, "x.db", None, None, None) == deny
    assert sqlite_authorizer(sqlite3.SQLITE_DETACH, "x", None, None, None) == deny
    assert sqlite_authorizer(9999, None, None, None, None) == deny  # an action SQLite adds later


#: SQLite functions outside the authorizer's allowlist (F-2 of the STU-016 review): both forms of fts3_tokenizer,
#: fts5, the FTS4 maintenance function, the R-tree debugging functions and the error log.
DENIED_SQLITE_FUNCTIONS = [
    ("fts3_tokenizer", "SELECT fts3_tokenizer('simple')"),
    ("fts3_tokenizer", "SELECT fts3_tokenizer('simple', x'0000000000000000')"),
    ("fts3_tokenizer", "SELECT hex(fts3_tokenizer('porter'))"),
    ("fts5", "SELECT fts5(NULL)"),
    ("optimize", "SELECT optimize(1)"),
    ("rtreenode", "SELECT rtreenode(2, x'00')"),
    ("rtreedepth", "SELECT rtreedepth(x'00')"),
    ("sqlite_log", "SELECT sqlite_log(1, 'x')"),
    ("load_extension", "SELECT load_extension('x')"),
]


def _sqlite_has_function(name: str) -> bool:
    """Whether this Python's SQLite build has the function ``name`` (FTS and R-tree are compile options)."""
    connection = sqlite3.connect(":memory:")
    try:
        row = connection.execute("SELECT count(*) FROM pragma_function_list WHERE name = ?", (name,)).fetchone()
        return int(row[0]) > 0
    finally:
        connection.close()


@pytest.mark.parametrize(("name", "sql"), DENIED_SQLITE_FUNCTIONS)
def test_f2_sqlite_functions_outside_the_allowlist_are_denied(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source], name: str, sql: str
) -> None:
    """The statement passes Studio's text check (it is a SELECT); the authorizer on the connection denies the call.
    A build without the function refuses it as unknown."""
    connection, _, _ = sqlite_connection
    check_statement(sql)
    expected = "not authorized" if _sqlite_has_function(name) else "no such function"
    for _ in range(3):  # whichever of the pool's connections it lands on
        with pytest.raises(TabrionError) as raised:
            connection.fetch(sql)
        assert expected in str(raised.value.original)
        # TD-053: a denied function is a permission problem, like a denied table or pragma, not db.unknown.
        code = "db.permission" if _sqlite_has_function(name) else "db.undefined_object"
        assert db_problem(raised.value).code == code
        with pytest.raises(TabrionError):
            below_the_check(connection, sql)
    assert connection.fetch("SELECT count(*) FROM part") == [(2,)]


def test_f2_fts_and_json_reads_still_work(tmp_path: Path) -> None:
    """The allowlist keeps what read queries use: FTS MATCH with its auxiliary functions (where the build has FTS4),
    JSON, dates, window functions and the ``->>`` operator."""
    fts = _sqlite_has_function("matchinfo")
    path = tmp_path / "docs.db"
    writer = sqlite3.connect(path)
    if fts:
        writer.executescript(
            "CREATE VIRTUAL TABLE ft USING fts4(body); INSERT INTO ft (body) VALUES ('hello world'), ('other text');"
        )
    writer.executescript(
        "CREATE TABLE doc (id INTEGER PRIMARY KEY, data TEXT, at TEXT);"
        "INSERT INTO doc VALUES (1, '{\"a\": [1, 2]}', '2026-01-02');"
        "CREATE VIEW doc_view AS SELECT id, json_extract(data, '$.a[1]') AS second, upper(at) AS at FROM doc;"
    )
    writer.commit()
    writer.close()
    path.chmod(0o600)
    source = Source(
        profiles={"project:docs": Profile("docs", "sqlite", "dev", path="docs.db")}, files={"project:docs": path}
    )
    registry = ConnectionRegistry(source)
    try:
        connection = registry.open("project:docs")
        if fts:
            assert connection.fetch(
                "SELECT snippet(ft), length(offsets(ft)) > 0, length(matchinfo(ft)) > 0 FROM ft WHERE ft MATCH 'hello'"
            ) == [("<b>hello</b> world", 1, 1)]
        assert connection.fetch(
            "SELECT data ->> '$.a[0]', json_array_length(data, '$.a'), date(at, '+1 day'), round(abs(-4.4)), "
            "row_number() OVER (ORDER BY id), coalesce(NULL, typeof(id)) FROM doc"
        ) == [(1, 2, "2026-01-03", 4.0, 1, "integer")]
        assert connection.fetch("SELECT second, at FROM doc_view") == [(2, "2026-01-02")]
        assert connection.fetch("SELECT count(*), group_concat(name, ',') FROM pragma_table_info('doc')") == [
            (3, "id,data,at")
        ]
    finally:
        registry.close_all()


def test_f2_trusted_schema_is_off_on_every_sqlite_session(
    sqlite_connection: tuple[OpenConnection, ConnectionRegistry, Source],
) -> None:
    connection, _, _ = sqlite_connection
    assert {connection.fetch("PRAGMA trusted_schema")[0][0] for _ in range(4)} == {0}
    with pytest.raises(TabrionError):  # a setting pragma: the authorizer denies it
        below_the_check(connection, "PRAGMA trusted_schema = ON")
    raw = connection._db.engine.raw_connection()
    try:
        driver = raw.driver_connection
        assert isinstance(driver, sqlite3.Connection)
        assert driver.execute("PRAGMA trusted_schema").fetchone() == (0,)
    finally:
        raw.close()


def test_f2_the_trusted_schema_pragma_runs_before_the_authorizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Studio sets trusted_schema itself (not only through tabrion's profile), before its authorizer denies pragmas."""
    calls: list[str] = []

    class Recorder(sqlite3.Connection):
        def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
            calls.append(sql)
            return super().execute(sql, *args)

        def set_authorizer(self, authorizer: Any) -> None:
            calls.append("set_authorizer")
            super().set_authorizer(authorizer)

    connection = sqlite3.connect(":memory:", factory=Recorder)
    connection.execute("PRAGMA trusted_schema = ON")
    calls.clear()
    try:
        connections_module._install_authorizer(connection, None)
        assert calls == ["PRAGMA trusted_schema = OFF", "set_authorizer"]
        assert connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "studio.statement_empty"),
        (" ;; -- nothing\n", "studio.statement_empty"),
        ("SELECT 1; SELECT 2", "studio.statement_multiple"),
        ("COMMIT; INSERT INTO t VALUES (1)", "studio.statement_multiple"),
        ("BEGIN", "studio.statement_transaction"),
        ("start transaction read write", "studio.statement_transaction"),
        ("/* a /* nested */ comment */ COMMIT", "studio.statement_refused"),  # SQLite does not nest: no agreement
        ("/* a /* nested */ comment */ SELECT 1", "studio.statement_refused"),
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
        # F-1 of the STU-016 review: statement forms that create a table on the read-only path.
        ("SELECT * INTO t2 FROM part", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT * INTO t2 FROM part", "studio.statement_refused"),
        ("explain (analyze, costs off) select 1 as a into unlogged t2", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT 1 AS a INTO TEMPORARY t2", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT 1 AS a INTO TEMP t2", "studio.statement_refused"),
        ("EXPLAIN ANALYZE WITH a AS (SELECT 1 AS x) SELECT * INTO t2 FROM a", "studio.statement_refused"),
        ("EXPLAIN ANALYSE VERBOSE (SELECT 1 AS a INTO t2)", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT 1 AS a /* c */ INTO/**/t2", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT 1 AS a\nINTO\tt2", "studio.statement_refused"),
        ("SELECT 'x' AS v, $$y$$ AS w INTO t2", "studio.statement_refused"),
        ("SELECT 'a\\' INTO t2 --'", "studio.statement_refused"),  # INTO outside the literal in one reading
        # SQLite has no dollar quoting: in its reading, INTO is a word (fail closed, also for PostgreSQL).
        ("SELECT $q$ INTO t2 $q$", "studio.statement_refused"),
        ("EXPLAIN ANALYZE CREATE TABLE t2 AS SELECT 1", "studio.statement_refused"),
        ("EXPLAIN ANALYZE CREATE MATERIALIZED VIEW m AS SELECT 1", "studio.statement_refused"),
        ("EXPLAIN ANALYZE EXECUTE p", "studio.statement_refused"),
        ("EXPLAIN ANALYZE MERGE INTO part USING part p2 ON true WHEN MATCHED THEN DELETE", "studio.statement_refused"),
        ("COPY (SELECT 1) TO STDOUT", "studio.statement_refused"),
        ("COPY part FROM STDIN", "studio.statement_refused"),
        # F-3 of the STU-016 review: functions with effects outside Studio's read-only transaction.
        ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity", "studio.statement_refused"),
        ("SELECT pg_catalog.pg_cancel_backend(1)", "studio.statement_refused"),
        ("SELECT pg_logical_emit_message(false, 'p', 'm')", "studio.statement_refused"),
        ("SELECT txid_current()", "studio.statement_refused"),
        ("SELECT pg_current_xact_id()", "studio.statement_refused"),
        ("SELECT pg_reload_conf()", "studio.statement_refused"),
        ("SELECT pg_rotate_logfile()", "studio.statement_refused"),
        ("SELECT pg_switch_wal()", "studio.statement_refused"),
        ("SELECT pg_create_restore_point('x')", "studio.statement_refused"),
        ("SELECT pg_promote()", "studio.statement_refused"),
        ("SELECT pg_stat_reset()", "studio.statement_refused"),
        ("SELECT pg_stat_reset_shared('bgwriter')", "studio.statement_refused"),
        ("SELECT pg_stat_statements_reset()", "studio.statement_refused"),
        ("SELECT pg_replication_origin_create('x')", "studio.statement_refused"),
        ("SELECT pg_replication_origin_session_setup('x')", "studio.statement_refused"),
        ("SELECT * FROM pg_create_logical_replication_slot('s', 'test_decoding')", "studio.statement_refused"),
        ("SELECT pg_drop_replication_slot('s')", "studio.statement_refused"),
        ("SELECT * FROM pg_logical_slot_get_changes('s', NULL, NULL)", "studio.statement_refused"),
        ("SELECT lo_export(1, '/tmp/x')", "studio.statement_refused"),
        ("SELECT lo_import('/etc/hostname')", "studio.statement_refused"),
        ("SELECT dblink_connect('c', 'dbname=x')", "studio.statement_refused"),
        ('SELECT "pg_terminate_backend"(1)', "studio.statement_refused"),
        ("SELECT PG_TERMINATE_BACKEND /* x */ (1)", "studio.statement_refused"),
        # N-1 of the fix review: a number right before INTO is its own token in every reading.
        ("EXPLAIN ANALYZE SELECT 1e1into t2", "studio.statement_refused"),
        ("SELECT 1E1INTO public.t2 FROM part", "studio.statement_refused"),
        ("SELECT 1, 2e0into temp t2", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT 1.5e-3into t2", "studio.statement_refused"),
        ("EXPLAIN ANALYZE SELECT .5e1into t2", "studio.statement_refused"),
        ("SELECT 1.into t2", "studio.statement_refused"),
        ("SELECT 0x1finto t2", "studio.statement_refused"),
        ("SELECT 0o17into t2", "studio.statement_refused"),
        ("SELECT 0b101into t2", "studio.statement_refused"),
        ("SELECT 1_000into t2", "studio.statement_refused"),
        # D-1 of the delta review: a number followed directly by a word character is refused.
        ("select 1_$q$, pg_terminate_backend(pid) from pg_stat_activity", "studio.statement_refused"),
        ("select 0x_$a$, pg_advisory_lock(1)", "studio.statement_refused"),
        ("select 1_$q$, connectby('x','k','p','1',0)", "studio.statement_refused"),
        ("select 1_$q$, pg_advisory_lock(1)", "studio.statement_refused"),
        ("select 0x_$a$, pg_terminate_backend(1)", "studio.statement_refused"),
        ("select 0b_$a$, pg_terminate_backend(1)", "studio.statement_refused"),
        ("select 1e1_$q$, pg_terminate_backend(1)", "studio.statement_refused"),
        ("select .5_$q$, pg_terminate_backend(1)", "studio.statement_refused"),
        ("explain analyze select 1 as a --\r, 0x_$a$ into t", "studio.statement_refused"),
        ("explain analyze select 1 as a --\r, 1_$a$ into t", "studio.statement_refused"),
        ("explain analyze select 1 as a --\r, 0xa$a$ into t", "studio.statement_refused"),
        ("SELECT 1abc", "studio.statement_refused"),
        ("SELECT 1$", "studio.statement_refused"),
        ("SELECT 0x1f, 0o17, 0b101", "studio.statement_refused"),
        ("SELECT 1_000", "studio.statement_refused"),
        # N-2 and N-3 of the fix review: a query given as text, and index maintenance.
        ("SELECT * FROM connectby('t', 'k', 'p', '1', 0) AS t(k int, p int, l int)", "studio.statement_refused"),
        ("SELECT * FROM xpath_table('k', 'd', 't', '/a', 'true') AS t(k int, a text)", "studio.statement_refused"),
        ("SELECT brin_summarize_new_values('i')", "studio.statement_refused"),
        ("SELECT brin_summarize_range('i', 0)", "studio.statement_refused"),
        ("SELECT brin_desummarize_range('i', 0)", "studio.statement_refused"),
        ("SELECT pg_catalog.gin_clean_pending_list('i')", "studio.statement_refused"),
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
        # F-1 and F-3: INTO as a name, in a literal or a comment, and the functions that only read.
        'SELECT "into" FROM t',
        "SELECT 'select 1 into t2' AS text",
        "SELECT 1 -- into t2",
        "SELECT 1 /* into t2 */",
        "SELECT intox, x_into FROM t",
        "SELECT txid_current_if_assigned(), pg_current_xact_id_if_assigned()",
        "SELECT pg_backend_pid(), pg_stat_get_activity(NULL)",
        "SELECT * FROM pg_stat_activity",
        "SELECT pg_stat_reset_time FROM t",
        # Decimal numbers still pass.
        "SELECT 1e1 AS a, 1.5e-3, .5, 1., 1.e1, 2E+3, 10 FROM t WHERE x = 1",
        "SELECT $1, 1::int, t1.a, a1e1 FROM t1",
    ],
)
def test_statements_studio_passes_on(sql: str) -> None:
    assert check_statement(sql)


# R-H1 of the delta re-review (stu-011-review.md): the first word comes from the lexer, in every reading.

#: The reviewer's probes: PostgreSQL ends ``--`` at ``\r`` too, so the server runs the statement before ``/*`` and
#: a line-based scan saw ``show``; SQLite does not nest block comments, so it runs the ``DELETE``.
R_H1_PROBES = [
    "-- x\rDECLARE c CURSOR WITH HOLD FOR SELECT pg_sleep(6) /*\nshow */",
    "-- x\rDECLARE heldc CURSOR WITH HOLD FOR SELECT 1 /*\nshow */",
    "-- x\rDO $$BEGIN PERFORM pg_advisory_lock(4242); END$$ /*\nshow */",
    "-- x\rCOPY (SELECT 'rr18d') TO '/tmp/rr18d_copy.txt' /*\nshow */",
    "-- x\rCOMMIT /*\nshow */",
    "-- x\rSET statement_timeout = 0 /*\nshow */",
    "-- x\rDELETE FROM part /*\nselect */",
    "/* /* */ DELETE FROM part -- */ select\n",
    "/* /* */ VACUUM -- */ select\n",
    "-- x\r\nSELECT 1 -- ok\n".replace("SELECT 1", "DELETE FROM part"),
    "\u00a0DELETE FROM part",  # a non-ASCII space is not white space to either server
    '"select" 1',
    "[select] 1",
    "`select` 1",
]


@pytest.mark.parametrize("sql", R_H1_PROBES)
def test_r_h1_the_first_word_cannot_hide_behind_a_comment(sql: str) -> None:
    with pytest.raises(StatementRefused) as raised:
        check_statement(sql)
    assert raised.value.code in ("studio.statement_refused", "studio.statement_transaction", "studio.statement_empty")
    assert first_word(sql) not in readonly_module._FIRST_WORDS


@pytest.mark.parametrize(
    "sql",
    [
        "-- a comment\r\nSELECT 1",
        "-- a comment\nSELECT 1 -- and one at the end",
        "/* one */ /* two */ (SELECT 1)",
        "\t\f\vSELECT 1",
        "EXPLAIN /* plan */ SELECT 1",
    ],
)
def test_r_h1_comments_that_all_readings_agree_on_still_pass(sql: str) -> None:
    assert check_statement(sql)


#: Pieces whose concatenations cover the lexers' corners: both line ends, nested and open comments, quotes.
_PIECES = ("--", "/*", "*/", "\r", "\n", " ", "select", "delete", "'", '"', "$$", "(", "[", "x")


def test_r_h1_the_checked_first_word_is_the_lexers_in_every_reading() -> None:
    """Property: whatever text passes the check starts, in every reading, with the same allowed word."""
    passed = 0
    for size in range(1, 6):
        for parts in itertools.product(_PIECES, repeat=size):
            sql = "".join(parts)
            try:
                check_statement(sql)
            except StatementRefused:
                continue
            passed += 1
            starts = set()
            for backslash, sqlite in readonly_module._READINGS:
                tokens = readonly_module._tokens(sql, backslash_strings=backslash, sqlite=sqlite)
                tokens = list(itertools.dropwhile(lambda token: token == "(", tokens))
                starts.add(tokens[0] if tokens else "")
            assert starts == {"select"}, sql
    assert passed > 100  # the property was exercised


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


#: R2-L1 of delta re-review 2: texts at the API's size cap (512 k characters) that made the lexer quadratic.
LONG_COMMENT_TEXTS = {
    "line comments": "select 1" + "--\n" * 174_000,
    "carriage returns": "select 1" + "-- \r" * 131_000,
    "comment lines": "-- a comment line\n" * 29_000 + "select 1",
}


@pytest.mark.parametrize("name", list(LONG_COMMENT_TEXTS))
def test_r2_l1_line_comments_are_lexed_in_linear_time(name: str) -> None:
    sql = LONG_COMMENT_TEXTS[name]
    assert len(sql) <= 512 * 1024
    started = time.perf_counter()
    assert check_statement(sql) == sql.strip()
    placeholders(sql, "postgresql")
    placeholders(sql, "sqlite")
    assert time.perf_counter() - started < 2.0  # about 0.3 s; it was 10 s before the fix


JUNK = readonly_module._NUMBER_JUNK


@pytest.mark.parametrize(
    ("sql", "tokens"),
    [
        ("select 1e1into x", ["select", "1e1", JUNK, "into", "x"]),
        ("select 1.5E-3into x", ["select", "1.5E-3", JUNK, "into", "x"]),
        ("select .5e1into x", ["select", ".5e1", JUNK, "into", "x"]),
        ("select 1.into x", ["select", "1.", JUNK, "into", "x"]),
        ("select 1einto x", ["select", "1", JUNK, "einto", "x"]),
        ("select 0x1f, 1_000", ["select", "0", JUNK, "x1f", ",", "1", JUNK, "_000"]),
        # D-1: a word character after a number never opens a dollar quote.
        ("select 1_$q$, f(1)", ["select", "1", JUNK, "_$q$", ",", "f", "(", "1", ")"]),
        (
            "select t.a, 1.5, .5e3, a1e1, 1 + 2",
            ["select", "t", ".", "a", ",", "1.5", ",", ".5e3", ",", "a1e1", ",", "1", "+", "2"],
        ),
    ],
)
def test_n1_a_numeric_literal_is_one_token_in_every_reading(sql: str, tokens: list[str]) -> None:
    """N-1 and D-1 of the fix review: a number is one token in each of the three readings, and a word character right
    after it is marked, so the text is refused."""
    for backslash, sqlite in readonly_module._READINGS:
        assert readonly_module._tokens(sql, backslash_strings=backslash, sqlite=sqlite) == tokens


@pytest.mark.parametrize(
    ("sql", "postgresql", "sqlite"),
    [
        ("select 1 -- a\r\nfrom t", ["select", "1", "from", "t"], ["select", "1", "from", "t"]),
        ("select --\n--\r 1", ["select", "1"], ["select"]),
        ("select -- a\rb\n1", ["select", "b", "1"], ["select", "1"]),
        ("select 1 --", ["select", "1"], ["select", "1"]),
    ],
)
def test_r2_l1_where_line_comments_end(sql: str, postgresql: list[str], sqlite: list[str]) -> None:
    """PostgreSQL ends ``--`` at a line feed or a carriage return, SQLite only at a line feed."""
    assert readonly_module._tokens(sql, backslash_strings=False) == postgresql
    assert readonly_module._tokens(sql, backslash_strings=False, sqlite=True) == sqlite


def test_r2_i2_a_nul_character_is_refused() -> None:
    with pytest.raises(StatementRefused) as raised:
        check_statement("select\0 1")
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
    # check (TD-034) runs Database.health() only, like health. extension_status (STU-055) reads the catalog through
    # tabrion's extension_status in a Database.read().
    assert public == {
        "check",
        "close",
        "extension_status",
        "fetch",
        "health",
        "inspect",
        "prepare",
        "read_only",
        "stream",
        "tabrion_findings",
        "to_dict",
    }
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
    again = registry.open("project:parts", "prod")
    other = tmp_path / "swapped.db"
    shutil.copyfile(sqlite_file, other)  # not a rename: Windows keeps an open file in place
    source.files["project:parts"] = other  # database_file now resolves elsewhere (a link swapped in, T-1)
    assert registry.list() == []
    with pytest.raises(RegistryError):
        registry.get(again.id)
    del source.profiles["project:parts"]
    with pytest.raises(SecretError):
        registry.open("project:parts", "prod")


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


class _AuthServer:
    """A minimal PostgreSQL wire endpoint on loopback that asks every client for its password in clear text (the
    ``password`` method) and records the password messages it receives; it never completes a login."""

    _SSL_OR_GSS_REQUEST = (80877103, 80877104)

    def __init__(self) -> None:
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(10)
        self.port = int(self._listener.getsockname()[1])
        self.password_messages: list[bytes] = []
        self.startups = 0
        self._stopped = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        with contextlib.suppress(OSError):
            while True:
                peer, _ = self._listener.accept()
                with peer:
                    if self._stopped:
                        return
                    self._one(peer)

    @staticmethod
    def _read(peer: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = peer.recv(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def _one(self, peer: socket.socket) -> None:
        peer.settimeout(5)
        with contextlib.suppress(OSError):
            while True:
                head = self._read(peer, 8)
                if len(head) < 8:
                    return
                length, code = struct.unpack(">ii", head)
                if length == 8 and code in self._SSL_OR_GSS_REQUEST:
                    peer.sendall(b"N")  # no TLS, no GSS encryption: the client goes on in plain text
                    continue
                self._read(peer, length - 8)  # the rest of the startup message
                self.startups += 1
                peer.sendall(b"R" + struct.pack(">ii", 8, 3))  # AuthenticationCleartextPassword
                kind = self._read(peer, 1)
                if kind == b"p":
                    (size,) = struct.unpack(">i", self._read(peer, 4))
                    self.password_messages.append(self._read(peer, size - 4))
                return

    def close(self) -> None:
        self._stopped = True
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self.port), timeout=1):
            pass  # wakes the accept() above
        self._thread.join(5)
        self._listener.close()


@pytest.fixture
def auth_server() -> Iterator[_AuthServer]:
    endpoint = _AuthServer()
    try:
        yield endpoint
    finally:
        endpoint.close()


def _cleartext_source(port: int, password: str) -> Source:
    profile = Profile(
        "remote",
        "postgresql",
        "dev",
        host="127.0.0.1",
        port=port,
        database="d",
        user="u",
        sslmode="disable",
        password=SecretRef("env", "STUDIO_TEST_PASSWORD"),
    )
    return Source(
        profiles={"project:remote": profile, "user:remote": profile},
        passwords={"project:remote": password, "user:remote": password},
    )


def test_a_project_profile_never_answers_a_clear_text_password_request(auth_server: _AuthServer) -> None:
    """Security assessment B-3: a project's file names the host; a connection of a project profile refuses the
    server's request for the password in clear text (libpq ``require_auth=!password``), so the password, here one
    from an ``env:`` variable, never leaves Studio that way. A user profile, the user's own choice of server, still
    answers (LDAP, PAM and RADIUS use this exchange); that also shows the endpoint asks."""
    password = f"canary-{secrets.token_hex(8)}"
    registry = ConnectionRegistry(_cleartext_source(auth_server.port, password))
    try:
        with pytest.raises((RegistryError, TabrionError, HealthError)):
            registry.open("project:remote")
        assert auth_server.startups >= 1
        assert auth_server.password_messages == []
        with pytest.raises((RegistryError, TabrionError, HealthError)):
            registry.open("user:remote")
        assert auth_server.password_messages and auth_server.password_messages[0] == password.encode() + b"\x00"
    finally:
        registry.close_all()


def test_driver_options_refuse_clear_text_for_project_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    assert driver_options("pw", refuse_cleartext=True)["require_auth"] == "!password"
    assert "require_auth" not in driver_options("pw")
    assert refuses_cleartext(Entry("project:p", "project", PG_PROFILE))
    assert not refuses_cleartext(Entry("user:p", "user", PG_PROFILE))
    # A libpq without require_auth (older than 16) does not know the option: it is left out, with a warning.
    monkeypatch.setattr(connections_module, "_libpq_version", lambda: 150000)
    assert "require_auth" not in driver_options("pw", refuse_cleartext=True)


def test_the_migration_worker_target_carries_the_clear_text_refusal(sqlite_file: Path) -> None:
    """B-3: the migration worker opens its own connection from ``worker_target``; it gets the same refusal."""
    profile = Profile("parts", "sqlite", "dev", path="data/parts.db")
    source = Source(
        profiles={"project:parts": profile, "user:parts": profile},
        files={"project:parts": sqlite_file, "user:parts": sqlite_file},
    )
    registry = ConnectionRegistry(source)
    try:
        for profile_id, refused in (("project:parts", True), ("user:parts", False)):
            opened = registry.open(profile_id)
            assert registry.worker_target(opened.id)["refuse_cleartext"] is refused
    finally:
        registry.close_all()


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


@pytest.mark.parametrize("confirmed", [None, "dev", "test"])
def test_td_038_a_prod_profile_opens_only_when_prod_was_confirmed(sqlite_file: Path, confirmed: str | None) -> None:
    """D-02 on the server (TD-038): open and test of a PROD profile need ``confirmed_environment="prod"``; nothing
    sent means "not confirmed". Other environments need nothing."""
    source = Source(
        profiles={
            "user:p": Profile("p", "sqlite", "prod", path="p.db"),
            "user:d": Profile("d", "sqlite", "dev", path="d.db"),
        },
        files={"user:p": sqlite_file, "user:d": sqlite_file},
    )
    registry = ConnectionRegistry(source)
    try:
        for call in (registry.open, registry.test):
            with pytest.raises(RegistryError) as raised:
                call("user:p", confirmed)
            assert raised.value.code == "studio.prod_confirmation_required"
        assert registry.list() == []
        # Refused before anything was resolved or opened.
        assert ("database_file", "user:p") not in source.calls
        assert registry.open("user:d", confirmed).environment == "dev"
        assert registry.test("user:p", "prod")["environment"] == "prod"
        opened = registry.open("user:p", "prod")
        # An open PROD connection is not handed out without the confirmation either.
        with pytest.raises(RegistryError) as raised:
            registry.open("user:p", confirmed)
        assert raised.value.code == "studio.prod_confirmation_required"
        assert registry.open("user:p", "prod") is opened
    finally:
        registry.close_all()


def test_td_038_the_confirmation_is_checked_on_the_profile_read_that_connects(sqlite_file: Path) -> None:
    """The profile flips from dev to prod between reads (another tab, profiles.toml): the check and the connection use
    one read, so a connection never opens as PROD for a caller that confirmed dev."""

    class Flipping(Source):
        def entry(self, profile_id: str) -> Entry:
            entry = super().entry(profile_id)
            current = self.profiles[profile_id]
            self.profiles[profile_id] = replace(current, environment="prod" if current.environment == "dev" else "dev")
            return entry

    source = Flipping(profiles={"user:p": Profile("p", "sqlite", "dev", path="p.db")}, files={"user:p": sqlite_file})
    registry = ConnectionRegistry(source)
    try:
        opened = registry.open("user:p", "dev")
        assert opened.environment == "dev"
        # The next read says prod: refused, never opened as prod.
        with pytest.raises(RegistryError) as raised:
            registry.test("user:p", "dev")
        assert raised.value.code == "studio.prod_confirmation_required"
    finally:
        registry.close_all()


def test_i2_the_worker_target_refuses_a_profile_that_became_prod_after_the_check(sqlite_file: Path) -> None:
    """I-2 of the #48 delta review: ``worker_target`` reads the profile once more after ``get``; if that read is PROD,
    the DEV connection's target is not handed to a migration worker."""

    class LaterProd(Source):
        def entry(self, profile_id: str) -> Entry:
            entry = super().entry(profile_id)
            if len(self.entries) == 2:  # open, then get's check: every read after them says prod
                self.profiles[profile_id] = replace(self.profiles[profile_id], environment="prod")
            return entry

    source = LaterProd(profiles={"user:p": Profile("p", "sqlite", "dev", path="p.db")}, files={"user:p": sqlite_file})
    registry = ConnectionRegistry(source)
    try:
        opened = registry.open("user:p", "dev")
        with pytest.raises(RegistryError) as raised:
            registry.worker_target(opened.id)
        assert raised.value.code == "studio.connection_stale"
        assert [entry.profile.environment for entry in source.entries] == ["dev", "dev", "prod"]
        # The file was resolved for the open and for get's check, never for the worker.
        assert source.calls == [("database_file", "user:p")] * 2
    finally:
        registry.close_all()


def test_stu_008_the_connection_test_reports_the_sqlite_facts(sqlite_file: Path) -> None:
    """STU-008 (03 section 2 A): the test answers the health check and the file's facts, and keeps nothing open."""
    with sqlite3.connect(sqlite_file) as raw:
        raw.execute("PRAGMA journal_mode=WAL")
    raw.close()
    before = _sha(sqlite_file)
    source = Source(profiles={"user:p": Profile("p", "sqlite", "test", path="p.db")}, files={"user:p": sqlite_file})
    registry = ConnectionRegistry(source)
    result = registry.test("user:p")
    assert result["ok"] is True and result["read_only"] is True
    assert (result["engine"], result["environment"]) == ("sqlite", "test")
    assert result["server_version"] == sqlite3.sqlite_version
    assert result["facts"] == {"foreign_keys": True, "journal_mode": "wal", "encoding": "UTF-8"}
    assert registry.list() == [] and _sha(sqlite_file) == before


def test_stu_008_facts_that_cannot_be_read_leave_the_test_answer(
    sqlite_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database error while reading the facts is no failed test: the health check is the answer, the facts are
    left out (and the error is not shown)."""

    def refuse(self: OpenConnection, sql: str, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        raise TabrionError("no facts")

    monkeypatch.setattr(OpenConnection, "fetch", refuse)
    source = Source(profiles={"user:p": Profile("p", "sqlite", "dev", path="p.db")}, files={"user:p": sqlite_file})
    result = ConnectionRegistry(source).test("user:p")
    assert result["ok"] is True and result["facts"] == {}


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            ("ro", True, False, "UTF8", "UTC", True, "16.15 (Debian 16.15-1)"),
            {"superuser": True, "bypass_rls": False, "tls": True, "server_version": "16.15"},
        ),
        (
            ("ro", False, True, "SQL_ASCII", "Europe/Vienna", False, "17.6"),
            {"superuser": False, "bypass_rls": True, "tls": False, "server_version": "17.6"},
        ),
        (
            ("ro", None, "yes", "UTF8", "UTC", None, ""),
            {"superuser": None, "bypass_rls": None, "tls": None, "server_version": None},
        ),
    ],
)
def test_stu_008_each_postgres_fact_comes_from_its_own_column(row: tuple[Any, ...], expected: dict[str, Any]) -> None:
    """L-6 of the connect review: superuser, bypass RLS, TLS and the version each come from their own column, and a
    value of the wrong type is "unknown" (``None``)."""

    class Fake:
        engine = "postgresql"

        def fetch(self, sql: str) -> list[tuple[Any, ...]]:
            assert "pg_stat_ssl" in sql and "server_version" in sql
            return [row]

    facts = connections_module.connection_facts(Fake())  # type: ignore[arg-type]
    assert {key: facts[key] for key in expected} == expected
    assert facts["encoding"] == row[3] and facts["timezone"] == row[4]


@pytest.mark.parametrize("error", [TabrionError("no facts"), StatementRefused("studio.statement_refused")])
def test_stu_008_a_refused_or_failed_facts_query_leaves_no_facts(error: Exception) -> None:
    """L-6 (mutant P6): a refused statement is handled like a database error, not raised."""

    class Fake:
        engine = "postgresql"

        def fetch(self, sql: str) -> list[tuple[Any, ...]]:
            raise error

    assert connections_module.connection_facts(Fake()) == {}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("UTF8", "UTF8"),
        ("Europe/Vienna", "Europe/Vienna"),
        ("x" * 64, "x" * 64),
        ("x" * 65, None),
        ("a\nb", None),
        ("\u202e", None),
        (None, None),
        (3, None),
    ],
)
def test_stu_008_a_fact_is_short_printable_text(value: object, expected: str | None) -> None:
    """Facts come from the server: only short printable ASCII reaches the page, anything else is "unknown"."""
    assert connections_module._fact_text(value) == expected


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
        yield registry.open("user:pg", "prod"), admin
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
    # An unqualified user table is found through the session's search_path (not Studio's catalog path).
    assert connection.fetch("SELECT count(*) FROM studio_ro_probe", catalog=False) == [(1,)]
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


#: F-1 of the STU-016 review: statement forms that would create a table through the read-only session; Studio's check
#: refuses them. ``{t}`` is a name made for the test.
INTO_FORMS = [
    "EXPLAIN ANALYZE SELECT * INTO {t} FROM studio_ro_probe",
    "explain (analyze, costs off) select g, repeat('x', 10) as pad into unlogged {t} from generate_series(1, 10) g",
    "EXPLAIN ANALYZE SELECT 1 AS a INTO TEMPORARY {t}",
    "EXPLAIN ANALYZE WITH a AS (SELECT 1 AS x) SELECT * INTO {t} FROM a",
    "EXPLAIN ANALYZE SELECT 1 AS a INTO public.{t}",
    "SELECT 1 AS a INTO {t}",
]


def _relations(admin: sqlalchemy.Engine, name: str) -> int:
    """How many relations of any schema, temporary ones included, have the name ``name``."""
    with admin.connect() as c:
        return int(
            c.exec_driver_sql("SELECT count(*) FROM pg_catalog.pg_class WHERE relname = %s", (name,)).scalar_one()
        )


def _drop_test_tables(admin: sqlalchemy.Engine, name: str) -> None:
    """Drops what a failing run of the test made (tables of the test's own name, in the test role's schemas)."""
    with admin.begin() as c:
        c.exec_driver_sql(f"DROP TABLE IF EXISTS public.{name}")


@pytest.mark.postgres
@postgres
@pytest.mark.parametrize("form", INTO_FORMS)
def test_f1_statements_that_create_a_table_are_refused(pg: tuple[OpenConnection, sqlalchemy.Engine], form: str) -> None:
    connection, admin = pg
    name = f"studio_into_{secrets.token_hex(4)}"
    sql = form.format(t=name)
    try:
        for _ in range(3):  # whichever of the pool's connections it lands on
            with pytest.raises(StatementRefused) as raised:
                connection.fetch(sql, catalog=False)  # the session's search_path, as for the editor
            assert raised.value.code == "studio.statement_refused"
        assert _relations(admin, name) == 0
    finally:
        _drop_test_tables(admin, name)
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_f1_a_write_under_explain_analyze_fails_in_the_database(pg: tuple[OpenConnection, sqlalchemy.Engine]) -> None:
    """A writable CTE passes the text check (its first word is a query's); PostgreSQL refuses it in the read-only
    transaction, also under EXPLAIN ANALYZE."""
    connection, admin = pg
    for sql in (
        "EXPLAIN ANALYZE WITH d AS (DELETE FROM studio_ro_probe RETURNING *) SELECT * FROM d",
        "EXPLAIN ANALYZE WITH u AS (UPDATE studio_ro_probe SET id = 7 RETURNING *) SELECT * FROM u",
        "EXPLAIN ANALYZE SELECT * FROM studio_ro_probe FOR UPDATE",
    ):
        assert check_statement(sql)
        with pytest.raises(TabrionError) as raised:
            connection.fetch(sql, catalog=False)
        assert raised.value.classification is not None
        assert raised.value.classification.kind.value == "read_only"
    assert _rows(admin) == 1


@pytest.mark.postgres
@postgres
def test_f1_check_in_drops_temporary_tables(pg: tuple[OpenConnection, sqlalchemy.Engine]) -> None:
    """Past the text check (what a function of the database could do): a temporary table made in a Studio session
    does not stay on the pooled connection, where it would come before pg_catalog in the next statements."""
    connection, admin = pg
    name = f"studio_temp_{secrets.token_hex(4)}"
    for _ in range(3):  # whichever of the pool's connections it lands on
        below_the_check(connection, f"EXPLAIN ANALYZE SELECT 1 AS a INTO TEMPORARY {name}")
        assert connection.fetch("SELECT count(*) FROM pg_catalog.pg_class WHERE relname = :n", {"n": name}) == [(0,)]
    assert _relations(admin, name) == 0
    # Nor one named like a catalog (pg_temp comes first for user SQL).
    below_the_check(connection, "EXPLAIN ANALYZE SELECT 1 AS nspname INTO TEMPORARY pg_namespace")
    for _ in range(4):
        assert connection.fetch("SELECT count(*) > 1 FROM pg_namespace", catalog=False) == [(True,)]


@pytest.mark.postgres
@postgres
def test_f3_functions_that_reach_other_sessions_or_the_wal_are_refused(
    pg: tuple[OpenConnection, sqlalchemy.Engine],
) -> None:
    """F-3 of the STU-016 review: a session of the same role (an application sharing it) is not ended or canceled
    through a Studio connection; no WAL message and no transaction ID come from it."""
    connection, admin = pg
    application = f"studio-f3-{secrets.token_hex(4)}"
    other = admin.connect()
    try:
        other.exec_driver_sql(f"SET application_name = '{application}'")
        other.commit()
        where = f"FROM pg_stat_activity WHERE application_name = '{application}'"
        for sql in (
            f"SELECT pg_terminate_backend(pid) {where}",
            f"SELECT pg_cancel_backend(pid) {where}",
            "SELECT pg_logical_emit_message(false, 'studio', 'x')",
            "SELECT pg_logical_emit_message(true, 'studio', 'x')",
            "SELECT txid_current()",
            "SELECT pg_current_xact_id()",
        ):
            with pytest.raises(StatementRefused):
                connection.fetch(sql)
        assert other.exec_driver_sql("SELECT 1").scalar_one() == 1  # still alive
        assert connection.fetch("SELECT pg_current_xact_id_if_assigned() IS NULL") == [(True,)]
    finally:
        other.invalidate()  # never back into the admin pool, whatever happened to the session
        other.close()


def _pg_facts(url_text: str) -> dict[str, Any]:
    """The connection test of a TEST profile for ``url_text`` (any role), through a registry of its own."""
    url = sqlalchemy.make_url(url_text)
    profile = Profile(
        "pg",
        "postgresql",
        "test",
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        password=SecretRef("keyring", ""),
    )
    registry = ConnectionRegistry(Source(profiles={"user:t": profile}, passwords={"user:t": url.password or ""}))
    result = registry.test("user:t")
    assert registry.list() == []
    return result


@pytest.mark.postgres
@postgres
def test_stu_008_the_connection_test_reports_the_postgres_facts(pg: tuple[OpenConnection, sqlalchemy.Engine]) -> None:
    """STU-008 (03 section 2 A): version, role, superuser, encoding, time zone and TLS of Studio's own session."""
    _, admin = pg
    assert PG_URL is not None
    result = _pg_facts(PG_URL)
    with admin.connect() as c:
        expected = c.exec_driver_sql(
            "SELECT rolsuper, rolbypassrls, current_setting('server_encoding'), current_setting('server_version'),"
            " current_setting('ssl') FROM pg_roles WHERE rolname = current_user"
        ).one()
    assert result["ok"] is True and result["engine"] == "postgresql" and result["environment"] == "test"
    # C-3 of stu-008-connect-review.md: the version as people write it ("16.15"), not server_version_num ("160015").
    assert result["server_version"] == expected[3].split()[0]
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?\w*", result["server_version"])
    facts = result["facts"]
    assert facts["role"] == sqlalchemy.make_url(PG_URL).username
    assert (facts["superuser"], facts["bypass_rls"], facts["encoding"]) == tuple(expected[:3])
    assert isinstance(facts["timezone"], str) and facts["timezone"]
    # The URL sets no sslmode (libpq's default prefer): TLS is on exactly when the server offers it.
    assert facts["tls"] is (expected[4] == "on")


@pytest.mark.postgres_superuser
@pytest.mark.skipif(
    PG_URL is None or PG_SUPERUSER_URL is None,
    reason="TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed",
)
def test_stu_008_the_privileged_facts_come_from_the_role() -> None:
    """L-6 of the connect review: a superuser, and a role that only bypasses row-level security, are reported as
    they are (each fact read on its own, not one from the other). The role is made here and dropped again."""
    assert PG_URL is not None and PG_SUPERUSER_URL is not None
    superuser = _pg_facts(PG_SUPERUSER_URL)["facts"]
    assert (superuser["superuser"], superuser["bypass_rls"]) == (True, True)
    role = f"stu008_bypass_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    admin = sqlalchemy.create_engine(sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"))
    database = sqlalchemy.make_url(PG_URL).database
    try:
        with admin.begin() as c:
            c.exec_driver_sql(f"CREATE ROLE {role} LOGIN BYPASSRLS PASSWORD '{password}'")
            c.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{database}" TO {role}')
        url = sqlalchemy.make_url(PG_URL).set(username=role, password=password)
        facts = _pg_facts(url.render_as_string(hide_password=False))["facts"]
        assert (facts["role"], facts["superuser"], facts["bypass_rls"]) == (role, False, True)
    finally:
        with admin.begin() as c:
            c.exec_driver_sql(f'REVOKE CONNECT ON DATABASE "{database}" FROM {role}')
            c.exec_driver_sql(f"DROP ROLE IF EXISTS {role}")
        admin.dispose()


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
    assert len(split_statements(ESCAPE)) == 1  # the splitter sees one statement
    for _ in range(3):
        with pytest.raises(TabrionError) as raised, connection._read() as transaction:
            transaction.fetch_all(sqlalchemy.text(ESCAPE))
        assert raised.value.classification is not None and raised.value.classification.sqlstate == "42601"
    # Since F-1 of the STU-016 review the text check refuses it too: one lexing sees the INSERT's INTO.
    with pytest.raises(StatementRefused):
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


def test_the_catalog_pin_needs_the_inspector_of_a_transaction() -> None:
    """TD-013: reflection runs on tabrion 0.2's ``Database.inspector()``; ``pin_catalog_path`` refuses an inspector
    that is not bound to a connection (an engine's would run outside that read-only transaction)."""
    engine = sqlalchemy.create_engine("sqlite://")
    try:
        with pytest.raises(TypeError, match=r"Database\.inspector\(\)"):
            tabrion_compat.pin_catalog_path(sqlalchemy.inspect(engine))
    finally:
        engine.dispose()
