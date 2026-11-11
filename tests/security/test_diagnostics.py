"""STU-014: the diagnostics finding catalog against prepared databases (09 backlog: FK off, 0644, superuser, no
timeout), and the rules of ``services/diagnostics.py``.

* SQLite: a 0644 file (TAB-SQ-003), rows written with foreign keys off (STU-DX-005), a rollback journal (TAB-SQ-002);
  the findings about Studio's own session (TAB-SQ-001, -005, -009) are left out.
* PostgreSQL: no statement timeout for the role or database (STU-DX-004, gone after ``ALTER ROLE ... SET``), a
  read-only role that can write (STU-DX-003), a prod profile without a verified certificate (STU-DX-001), an owner
  role on prod as an error; a superuser (TAB-PG-001) with ``TABRION_STUDIO_TEST_POSTGRES_SUPERUSER``.
* The route sits behind the session and CSRF checks; no finding names the database file's path.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any

import pytest
import sqlalchemy
from tabrion.check import Finding, Severity

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import REPLACED_CODES, SESSION_CODES, Diagnostics, effective_settings
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def codes(doc: dict[str, Any]) -> dict[str, str]:
    return {item["code"]: item["severity"] for item in doc["findings"]}


def prepared(path: Path, *, mode: int, orphans: bool, wal: bool) -> Path:
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        "CREATE TABLE supplier (id INTEGER PRIMARY KEY);"
        "CREATE TABLE part (id INTEGER PRIMARY KEY, supplier_id INTEGER REFERENCES supplier (id));"
        "INSERT INTO supplier VALUES (1); INSERT INTO part VALUES (1, 1);"
    )
    if orphans:  # sqlite3 opens with foreign_keys off: nothing stops these rows
        connection.executemany("INSERT INTO part VALUES (?, ?)", [(i, 99) for i in range(2, 7)])
    connection.commit()
    connection.close()
    path.chmod(mode)
    return path


@pytest.fixture
def lite(tmp_path: Path) -> Iterator[tuple[Diagnostics, ConnectionRegistry, Source, Path]]:
    path = tmp_path / "shop.db"
    source = Source(
        profiles={"project:shop": Profile("shop", "sqlite", "dev", path="data/shop.db")},
        files={"project:shop": path},
    )
    registry = ConnectionRegistry(source)
    try:
        yield Diagnostics(registry, clock=lambda: 1234.0), registry, source, path
    finally:
        registry.close_all()


@posix
def test_sqlite_fk_off_and_0644_are_findings(lite: tuple[Diagnostics, ConnectionRegistry, Source, Path]) -> None:
    diagnostics, registry, _, path = lite
    prepared(path, mode=0o644, orphans=True, wal=False)
    doc = diagnostics.run(registry.open("project:shop").id)
    found = codes(doc)
    assert found["TAB-SQ-003"] == "error"  # readable by group and others
    assert found["STU-DX-005"] == "warning"  # rows written with foreign keys off
    assert found["TAB-SQ-002"] == "info"  # a rollback journal, not WAL
    assert not set(found) & SESSION_CODES
    assert doc["counts"] == {"error": 1, "warning": 1, "info": 1} and doc["ran_at"] == 1234.0
    assert [item["severity"] for item in doc["findings"]] == ["error", "warning", "info"]
    fk = next(item for item in doc["findings"] if item["code"] == "STU-DX-005")
    assert "5 row(s)" in fk["detail"] and fk["source"] == "studio"
    assert not any(str(path) in str(item) or path.name in str(item) for item in doc["findings"])


def test_a_clean_sqlite_file_has_no_finding(lite: tuple[Diagnostics, ConnectionRegistry, Source, Path]) -> None:
    diagnostics, registry, _, path = lite
    prepared(path, mode=0o600, orphans=False, wal=True)
    assert diagnostics.run(registry.open("project:shop").id)["findings"] == []


def test_the_diagnostics_route(lite: tuple[Diagnostics, ConnectionRegistry, Source, Path], tmp_path: Path) -> None:
    diagnostics, registry, _, path = lite
    prepared(path, mode=0o600, orphans=True, wal=True)
    connection_id = registry.open("project:shop").id
    studio = make_studio(tmp_path / "static", connections=registry, diagnostics=diagnostics)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    answer = studio.client.post(f"/api/connections/{connection_id}/diagnostics", json={}, headers=headers)
    assert answer.status_code == 200 and codes(answer.json()) == {"STU-DX-005": "warning"}
    gone = studio.client.post("/api/connections/c_nope/diagnostics", json={}, headers=headers)
    assert gone.json()["code"] == "studio.connection_not_found"
    assert studio.client.post(f"/api/connections/{connection_id}/diagnostics", json={}).status_code in (401, 403)


# The rules, with tabrion's findings and the facts given (no server needed).

ALL_SET = [
    (True, False, "statement_timeout=30s"),
    (True, False, "lock_timeout=5s"),
    (True, False, "idle_in_transaction_session_timeout=60s"),
    (True, False, "application_name=parts"),
]


class FakeConnection:
    """Answers Studio's queries by what they ask for: write privileges, default privileges, owned tables, settings."""

    def __init__(self, engine: str, environment: str, findings: list[Finding], facts: dict[str, Any]) -> None:
        self.id, self.profile_id, self.engine, self.environment = "c_fake", "user:pg", engine, environment
        self.timeout_s = 30.0
        self._findings, self.facts = findings, facts

    def tabrion_findings(self, *, cancel: Any = None) -> list[Finding]:
        return self._findings

    def fetch(self, sql: str, params: Any = None, *, cancel: Any = None) -> list[tuple[Any, ...]]:
        if "pg_db_role_setting" in sql:
            return list(self.facts.get("settings", ALL_SET))
        key = "default" if "pg_default_acl" in sql else "owned" if "relowner" in sql else "writable"
        return [(self.facts.get(key, 0),)]


class FakeRegistry:
    def __init__(self, connection: FakeConnection, profile: Profile) -> None:
        self.connection = connection
        self.source = Source(profiles={"user:pg": profile})

    def get(self, connection_id: object) -> FakeConnection:
        return self.connection


def tabrion_finding(code: str, severity: Severity) -> Finding:
    return Finding(code, severity, "title", "detail", "remedy")


def run_fake(
    environment: str, findings: list[Finding] | None = None, facts: dict[str, Any] | None = None, **profile: Any
) -> dict[str, str]:
    base = Profile("pg", "postgresql", environment, host="db.example.internal", database="app", sslmode="verify-full")
    connection = FakeConnection("postgresql", environment, findings or [], facts or {})
    diagnostics = Diagnostics(FakeRegistry(connection, replace(base, **profile)))  # type: ignore[arg-type]
    return codes(diagnostics.run("c_fake"))


def test_session_findings_are_left_out_and_the_owner_check_is_studios() -> None:
    findings = [
        tabrion_finding("TAB-PG-001", Severity.ERROR),
        tabrion_finding("TAB-PG-003", Severity.WARNING),  # current schema only: STU-DX-006 replaces it (M-3)
        tabrion_finding("TAB-PG-004", Severity.WARNING),  # Studio's own session has a timeout; the app's may not
        tabrion_finding("TAB-PG-007", Severity.INFO),
        tabrion_finding("TAB-PG-021", Severity.WARNING),  # I-2: reads Studio's default_transaction_read_only
    ]
    assert "TAB-PG-021" in SESSION_CODES and "TAB-PG-003" in REPLACED_CODES
    assert run_fake("dev", findings) == {"TAB-PG-001": "error"}
    assert run_fake("dev", findings, {"owned": 2}) == {"TAB-PG-001": "error", "STU-DX-006": "warning"}
    assert run_fake("prod", findings, {"owned": 2}) == {"TAB-PG-001": "error", "STU-DX-006": "error"}


@pytest.mark.parametrize(
    ("environment", "sslmode", "host", "expected"),
    [
        ("prod", "prefer", "db.example.internal", {"STU-DX-001": "error", "STU-DX-002": "warning"}),
        ("prod", "require", "db.example.internal", {"STU-DX-001": "error"}),
        ("prod", "verify-ca", "db.example.internal", {}),
        ("prod", "prefer", "/var/run/postgresql", {}),  # L-3: a Unix socket has no TLS to verify
        ("dev", "disable", "10.0.0.5", {"STU-DX-002": "warning"}),
        ("dev", "prefer", "localhost", {}),  # T-10 is about the network; loopback is fine
        ("dev", "prefer", "127.0.0.1", {}),
        ("dev", "prefer", "/var/run/postgresql", {}),  # L-6: no STU-DX-002 for a socket folder
    ],
)
def test_t10_tls_findings(environment: str, sslmode: str, host: str, expected: dict[str, str]) -> None:
    """STU-T-021 (T-10): a diagnostic finding for disable and prefer to a remote host; prod needs verification."""
    assert run_fake(environment, sslmode=sslmode, host=host) == expected


def test_tabrion_tls_finding_replaces_the_profile_rule() -> None:
    """L-2: one finding per TLS cause; tabrion's (from the live connection) wins over the profile's."""
    tls = [tabrion_finding("TAB-PG-011", Severity.ERROR)]
    assert run_fake("dev", tls, sslmode="disable", host="10.0.0.5") == {"TAB-PG-011": "error"}


def test_read_only_role_and_default_privileges() -> None:
    assert run_fake("dev", facts={"writable": 3}, read_only_role=True) == {"STU-DX-003": "warning"}
    assert run_fake("prod", facts={"writable": 1000}, read_only_role=True) == {"STU-DX-003": "error"}
    assert run_fake("dev", facts={"default": 1}, read_only_role=True) == {"STU-DX-007": "info"}
    assert run_fake("dev", facts={"writable": 3, "default": 1}, read_only_role=None) == {}  # no expectation


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (ALL_SET, {}),
        (ALL_SET[1:], {"STU-DX-004": "warning"}),
        ([], {"STU-DX-004": "warning", "STU-DX-010": "warning"}),
        ([*ALL_SET[:2], (True, False, "idle_in_transaction_session_timeout=5min")], {"STU-DX-010": "info"}),
        # L-4: the most specific setting wins: the role's 0 over the database's 30s
        ([(False, True, "statement_timeout=30s"), (True, False, "statement_timeout=0"), *ALL_SET[1:]],
         {"STU-DX-004": "warning"}),
        ([(True, False, "statement_timeout=0"), (True, True, "statement_timeout=10s"), *ALL_SET[1:]], {}),
        ([(False, True, "statement_timeout=30s"), *ALL_SET[1:]], {}),
    ],
)  # fmt: skip
def test_role_settings_with_precedence(settings: list[tuple[Any, ...]], expected: dict[str, str]) -> None:
    assert run_fake("dev", facts={"settings": settings}) == expected


def test_effective_settings_order() -> None:
    rows = [(False, True, "lock_timeout=1s"), (True, True, "lock_timeout=2s"), (True, False, "lock_timeout=3s")]
    assert effective_settings(rows) == {"lock_timeout": "2s"}
    assert effective_settings([(True, False, "a=1"), (False, True, "a=2")]) == {"a": "1"}


# PostgreSQL, against the server.


def _pg_registry(url_text: str, **profile: Any) -> tuple[ConnectionRegistry, Source]:
    url = sqlalchemy.make_url(url_text)
    base = Profile(
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
    source = Source(profiles={"user:pg": replace(base, **profile)}, passwords={"user:pg": url.password or ""})
    return ConnectionRegistry(source), source


@pytest.mark.postgres
@postgres
def test_pg_no_timeout_writable_read_only_role_and_prod_rules() -> None:
    assert PG_URL is not None
    registry, _ = _pg_registry(PG_URL, read_only_role=True)
    admin = sqlalchemy.create_engine(sqlalchemy.make_url(PG_URL).set(drivername="postgresql+psycopg"))
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS public.studio_diag_probe (id int)")
        diagnostics = Diagnostics(registry)
        connection_id = registry.open("user:pg").id
        found = codes(diagnostics.run(connection_id))
        assert found["STU-DX-004"] == "warning"  # the test role and database set no statement_timeout
        assert found["STU-DX-003"] == "warning"  # the owner role can write, though the profile says read-only
        assert found.get("STU-DX-006") == "warning"  # it owns tables (Studio's check; TAB-PG-003 is left out)
        assert "TAB-PG-001" not in found  # studio_owner is no superuser
        assert not set(found) & SESSION_CODES
        with admin.begin() as connection:
            connection.exec_driver_sql("ALTER ROLE CURRENT_USER SET statement_timeout = '45s'")
        try:
            assert "STU-DX-004" not in codes(diagnostics.run(connection_id))
        finally:
            with admin.begin() as connection:
                connection.exec_driver_sql("ALTER ROLE CURRENT_USER RESET statement_timeout")
        registry.close_all()
        prod_registry, _ = _pg_registry(PG_URL, environment="prod", read_only_role=True)
        try:
            prod = codes(Diagnostics(prod_registry).run(prod_registry.open("user:pg").id))
        finally:
            prod_registry.close_all()
        assert prod["STU-DX-001"] == "error"  # prefer on prod
        assert prod["STU-DX-003"] == "error" and prod["STU-DX-006"] == "error"
    finally:
        registry.close_all()
        with admin.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS public.studio_diag_probe")
        admin.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(PG_SUPERUSER_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES_SUPERUSER is not set")
def test_pg_a_superuser_is_an_error() -> None:
    assert PG_SUPERUSER_URL is not None
    registry, _ = _pg_registry(PG_SUPERUSER_URL)
    try:
        found = codes(Diagnostics(registry).run(registry.open("user:pg").id))
    finally:
        registry.close_all()
    assert found["TAB-PG-001"] == "error"


# M-2, M-3, L-4 against the server: roles made for the test by the superuser, dropped at the end.


@pytest.fixture
def pg_roles() -> Iterator[tuple[sqlalchemy.Engine, str, str]]:
    """A role ``<prefix>_ro`` with only SELECT on ``<prefix>.t``, a writer role, and the superuser's engine."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    prefix = f"studio_diag_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    admin = sqlalchemy.create_engine(sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"))
    database = sqlalchemy.make_url(PG_URL).database
    setup = [
        f"CREATE ROLE {prefix}_ro LOGIN PASSWORD '{password}'",
        f"CREATE ROLE {prefix}_writer NOLOGIN",
        f"CREATE SCHEMA {prefix}",
        f"CREATE TABLE {prefix}.t (id int PRIMARY KEY, v int)",
        f"CREATE VIEW {prefix}.v AS SELECT id, v FROM {prefix}.t",  # noqa: S608 (a test-made name)
        f"CREATE SEQUENCE {prefix}.s",
        f"GRANT USAGE ON SCHEMA {prefix} TO {prefix}_ro, {prefix}_writer",
        f"GRANT SELECT ON {prefix}.t, {prefix}.v TO {prefix}_ro",
        f"GRANT INSERT, UPDATE, DELETE ON {prefix}.t TO {prefix}_writer",
        f'GRANT CONNECT ON DATABASE "{database}" TO {prefix}_ro',
    ]
    with admin.begin() as connection:
        for statement in setup:
            connection.exec_driver_sql(statement)
    try:
        yield admin, prefix, password
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {prefix} CASCADE")
            for role in (f"{prefix}_ro", f"{prefix}_writer"):
                connection.exec_driver_sql(f"DROP OWNED BY {role}")
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {role}")
        admin.dispose()


def _run_as(user: str, password: str, **profile: Any) -> dict[str, str]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL).set(username=user, password=password)
    registry, _ = _pg_registry(url.render_as_string(hide_password=False), **profile)
    try:
        return codes(Diagnostics(registry).run(registry.open("user:pg").id))
    finally:
        registry.close_all()


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("grant", "revoke"),
    [
        ("GRANT UPDATE (v) ON {p}.t TO {p}_ro", "REVOKE UPDATE (v) ON {p}.t FROM {p}_ro"),  # a column grant
        ("GRANT TRUNCATE ON {p}.t TO {p}_ro", "REVOKE TRUNCATE ON {p}.t FROM {p}_ro"),
        ("GRANT INSERT ON {p}.v TO {p}_ro", "REVOKE INSERT ON {p}.v FROM {p}_ro"),  # an auto-updatable view
        ("GRANT UPDATE ON SEQUENCE {p}.s TO {p}_ro", "REVOKE UPDATE ON SEQUENCE {p}.s FROM {p}_ro"),  # setval
        ("GRANT {p}_writer TO {p}_ro", "REVOKE {p}_writer FROM {p}_ro"),  # INHERIT membership
        ("ALTER ROLE {p}_ro NOINHERIT; GRANT {p}_writer TO {p}_ro",  # one SET ROLE away
         "REVOKE {p}_writer FROM {p}_ro; ALTER ROLE {p}_ro INHERIT"),
    ],
)  # fmt: skip
def test_pg_read_only_role_write_paths(pg_roles: tuple[sqlalchemy.Engine, str, str], grant: str, revoke: str) -> None:
    """M-2: every way the "read-only" role can write is STU-DX-003; SELECT alone is not."""
    admin, prefix, password = pg_roles
    assert "STU-DX-003" not in _run_as(f"{prefix}_ro", password, read_only_role=True)
    with admin.begin() as connection:
        connection.exec_driver_sql(grant.format(p=prefix))
    try:
        assert _run_as(f"{prefix}_ro", password, read_only_role=True)["STU-DX-003"] == "warning"
        prod = _run_as(f"{prefix}_ro", password, read_only_role=True, environment="prod")
        assert prod["STU-DX-003"] == "error"
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(revoke.format(p=prefix))


@pytest.mark.postgres
def test_pg_default_privileges_are_an_info(pg_roles: tuple[sqlalchemy.Engine, str, str]) -> None:
    admin, prefix, password = pg_roles
    statement = "ALTER DEFAULT PRIVILEGES IN SCHEMA {p} {verb} INSERT ON TABLES {to} {p}_ro"
    with admin.begin() as connection:
        connection.exec_driver_sql(statement.format(p=prefix, verb="GRANT", to="TO"))
    try:
        found = _run_as(f"{prefix}_ro", password, read_only_role=True)
        assert found["STU-DX-007"] == "info" and "STU-DX-003" not in found  # no table is writable yet
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(statement.format(p=prefix, verb="REVOKE", to="FROM"))


@pytest.mark.postgres
def test_pg_owner_outside_the_current_schema(pg_roles: tuple[sqlalchemy.Engine, str, str]) -> None:
    """M-3: a role that owns a table in another schema than its search_path is an owner, an error on prod."""
    admin, prefix, password = pg_roles
    assert "STU-DX-006" not in _run_as(f"{prefix}_ro", password)
    with admin.begin() as connection:
        connection.exec_driver_sql(f"ALTER TABLE {prefix}.t OWNER TO {prefix}_ro")
    found = _run_as(f"{prefix}_ro", password)
    assert found["STU-DX-006"] == "warning" and "TAB-PG-003" not in found
    assert _run_as(f"{prefix}_ro", password, environment="prod")["STU-DX-006"] == "error"


@pytest.mark.postgres
def test_pg_role_setting_precedence(pg_roles: tuple[sqlalchemy.Engine, str, str]) -> None:
    """L-4 and L-1: the role's 0 wins over the database's 30s; the other settings are STU-DX-010."""
    admin, prefix, password = pg_roles
    database = sqlalchemy.make_url(PG_URL or "").database
    with admin.begin() as connection:
        connection.exec_driver_sql(f"ALTER ROLE {prefix}_ro IN DATABASE \"{database}\" SET statement_timeout = '30s'")
    try:
        found = _run_as(f"{prefix}_ro", password)
        assert "STU-DX-004" not in found and found["STU-DX-010"] == "warning"
        with admin.begin() as connection:
            connection.exec_driver_sql(f'ALTER ROLE {prefix}_ro IN DATABASE "{database}" SET statement_timeout = 0')
            for name, value in (("lock_timeout", "'5s'"), ("idle_in_transaction_session_timeout", "'60s'"),
                                ("application_name", "'parts'")):  # fmt: skip
                connection.exec_driver_sql(f"ALTER ROLE {prefix}_ro SET {name} = {value}")
        found = _run_as(f"{prefix}_ro", password)
        assert found["STU-DX-004"] == "warning" and "STU-DX-010" not in found
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'ALTER ROLE {prefix}_ro IN DATABASE "{database}" RESET ALL')
            connection.exec_driver_sql(f"ALTER ROLE {prefix}_ro RESET ALL")


# M-1: the time limit and the cancel; L-5: a broken foreign key definition.


def big_sqlite(path: Path, rows: int = 400_000) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE supplier (id INTEGER PRIMARY KEY);"
        "CREATE TABLE part (id INTEGER PRIMARY KEY, supplier_id INTEGER REFERENCES supplier (id), note TEXT);"
    )
    connection.executemany("INSERT INTO part VALUES (?, ?, 'x')", ((i, i % 7) for i in range(rows)))
    connection.commit()
    connection.close()
    path.chmod(0o600)
    return path


@posix
def test_sqlite_diagnosis_stops_at_the_time_limit(tmp_path: Path) -> None:
    path = big_sqlite(tmp_path / "big.db")
    source = Source(
        profiles={"project:big": Profile("big", "sqlite", "dev", path="big.db", statement_timeout_ms=1)},
        files={"project:big": path},
    )
    registry = ConnectionRegistry(source)
    try:
        started = time.monotonic()
        doc = Diagnostics(registry).run(registry.open("project:big").id)
        assert time.monotonic() - started < 3
    finally:
        registry.close_all()
    found = {item["code"]: item for item in doc["findings"]}
    assert found["STU-DX-008"]["severity"] == "info"
    assert "time limit" in found["STU-DX-008"]["detail"] and "STU-DX-005" in found["STU-DX-008"]["detail"]
    assert "STU-DX-005" not in found  # not counted, so not claimed
    # The limit reached tabrion's quick_check too: "not checked", or the whole tabrion step named as not run.
    assert "TAB-SQ-006" in found or "tabrion's checks" in found["STU-DX-008"]["detail"]
    assert found.get("TAB-SQ-006", {"severity": "info"})["severity"] == "info"  # never "damaged"


@posix
def test_sqlite_diagnosis_can_be_canceled(tmp_path: Path) -> None:
    path = big_sqlite(tmp_path / "big.db", rows=1_500_000)
    source = Source(
        profiles={"project:big": Profile("big", "sqlite", "dev", path="big.db")}, files={"project:big": path}
    )
    registry = ConnectionRegistry(source)
    diagnostics = Diagnostics(registry)
    connection_id = registry.open("project:big").id
    result: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: result.update(diagnostics.run(connection_id)))
    try:
        started = time.monotonic()
        worker.start()
        while not diagnostics.cancel(connection_id):
            assert worker.is_alive(), "the diagnosis ended before it could be canceled"
            time.sleep(0.001)
        worker.join(5)
        assert not worker.is_alive() and time.monotonic() - started < 5
    finally:
        registry.close_all()
    details = {item["code"]: item["detail"] for item in result["findings"]}
    assert "you canceled it" in details["STU-DX-008"]
    assert "TAB-SQ-006" in details or "tabrion's checks" in details["STU-DX-008"]  # quick_check was stopped
    assert diagnostics.cancel(connection_id) is False  # nothing runs any more


@posix
def test_a_broken_foreign_key_keeps_the_other_findings(
    lite: tuple[Diagnostics, ConnectionRegistry, Source, Path],
) -> None:
    """L-5: "foreign key mismatch" (a parent column without a unique index) is STU-DX-009, not a failed route."""
    diagnostics, registry, _, path = lite
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE parent (code TEXT); CREATE TABLE child (code TEXT REFERENCES parent (code));"
        "INSERT INTO parent VALUES ('a'); INSERT INTO child VALUES ('a');"
    )
    connection.close()
    path.chmod(0o644)
    found = codes(diagnostics.run(registry.open("project:shop").id))
    assert found["STU-DX-009"] == "warning" and found["TAB-SQ-003"] in ("warning", "error")


def test_the_cancel_route_and_the_csrf_check(
    lite: tuple[Diagnostics, ConnectionRegistry, Source, Path], tmp_path: Path
) -> None:
    diagnostics, registry, _, path = lite
    prepared(path, mode=0o600, orphans=False, wal=True)
    connection_id = registry.open("project:shop").id
    studio = make_studio(tmp_path / "static", connections=registry, diagnostics=diagnostics)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    answer = studio.client.post(f"/api/connections/{connection_id}/diagnostics/cancel", json={}, headers=headers)
    assert answer.status_code == 202 and answer.json() == {"canceled": False}
    no_csrf = {"Origin": ORIGIN}  # signed in (the cookie), but no CSRF header
    for route in ("diagnostics", "diagnostics/cancel"):
        refused = studio.client.post(f"/api/connections/{connection_id}/{route}", json={}, headers=no_csrf)
        assert refused.status_code == 403 and refused.json()["code"] == "access.csrf_invalid"
