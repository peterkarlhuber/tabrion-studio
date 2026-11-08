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
import sqlite3
import sys
from typing import Any

import pytest
import sqlalchemy
from tabrion.check import Finding, Severity

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import SESSION_CODES, Diagnostics
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


class FakeConnection:
    def __init__(self, engine: str, environment: str, findings: list[Finding], answers: list[int]) -> None:
        self.id, self.profile_id, self.engine, self.environment = "c_fake", "user:pg", engine, environment
        self._findings, self._answers = findings, answers

    def tabrion_findings(self) -> list[Finding]:
        return self._findings

    def fetch(self, sql: str, params: Any = None) -> list[tuple[Any, ...]]:
        return [(self._answers.pop(0),)]


class FakeRegistry:
    def __init__(self, connection: FakeConnection, profile: Profile) -> None:
        self.connection = connection
        self.source = Source(profiles={"user:pg": profile})

    def get(self, connection_id: object) -> FakeConnection:
        return self.connection


def tabrion_finding(code: str, severity: Severity) -> Finding:
    return Finding(code, severity, "title", "detail", "remedy")


def run_fake(environment: str, findings: list[Finding], answers: list[int], **profile: Any) -> dict[str, str]:
    base = Profile("pg", "postgresql", environment, host="db.example.internal", database="app", sslmode="verify-full")
    connection = FakeConnection("postgresql", environment, findings, answers)
    diagnostics = Diagnostics(FakeRegistry(connection, replace(base, **profile)))  # type: ignore[arg-type]
    return codes(diagnostics.run("c_fake"))


def test_tabrion_session_findings_are_left_out_and_prod_raises_the_owner_finding() -> None:
    findings = [
        tabrion_finding("TAB-PG-001", Severity.ERROR),
        tabrion_finding("TAB-PG-003", Severity.WARNING),
        tabrion_finding("TAB-PG-004", Severity.WARNING),  # Studio's own session has a timeout; the app's may not
        tabrion_finding("TAB-PG-007", Severity.INFO),
    ]
    assert run_fake("dev", findings, [1]) == {"TAB-PG-001": "error", "TAB-PG-003": "warning"}
    assert run_fake("prod", findings, [1]) == {"TAB-PG-001": "error", "TAB-PG-003": "error"}


@pytest.mark.parametrize(
    ("environment", "sslmode", "host", "expected"),
    [
        ("prod", "prefer", "db.example.internal", {"STU-DX-001": "error", "STU-DX-002": "warning"}),
        ("prod", "require", "db.example.internal", {"STU-DX-001": "error"}),
        ("prod", "verify-ca", "db.example.internal", {}),
        ("dev", "disable", "10.0.0.5", {"STU-DX-002": "warning"}),
        ("dev", "prefer", "localhost", {}),  # T-10 is about the network; loopback is fine
        ("dev", "prefer", "127.0.0.1", {}),
    ],
)
def test_t10_tls_findings(environment: str, sslmode: str, host: str, expected: dict[str, str]) -> None:
    """STU-T-021 (T-10): a diagnostic finding for disable and prefer to a remote host; prod needs verification."""
    assert run_fake(environment, [], [1], sslmode=sslmode, host=host) == expected


def test_read_only_role_and_role_timeout_findings() -> None:
    # answers: writable tables, then role/database timeout settings
    assert run_fake("dev", [], [3, 0], read_only_role=True) == {"STU-DX-003": "warning", "STU-DX-004": "warning"}
    assert run_fake("prod", [], [1000, 1], read_only_role=True) == {"STU-DX-003": "error"}
    assert run_fake("dev", [], [0, 1], read_only_role=True) == {}
    assert run_fake("dev", [], [1], read_only_role=None) == {}  # no expectation, no check


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
        assert found.get("TAB-PG-003") == "warning"  # it owns tables
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
        assert prod["STU-DX-003"] == "error" and prod["TAB-PG-003"] == "error"
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
