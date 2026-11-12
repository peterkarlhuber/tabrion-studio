"""STU-025: the diagnosis report (Markdown, JSON) and the backup hints; 09's acceptance "report without secrets
(canary test)".

* Backup hints: SQLite backups next to the database (found, older than the data, readable by others, what does not
  count), PostgreSQL's ``archive_mode`` and ``pg_stat_archiver`` as rules and against the server.
* The report route: formats, ``Content-Disposition``, the problems, the session and CSRF checks.
* Canaries (TAB-T-110 style ``tabrion-canary-<uuid>``): a PostgreSQL password, the process's tokens (session, CSRF,
  launch, control), connection URLs and DSN texts, parameter values of queries run on the same connection, a value
  in the role's settings and in the data, and the database file's path and name. Neither format of the report nor
  the log may hold any of them; the report holds no host, port, user or database name either.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import decimal
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time
from typing import Any
import uuid

import pytest
import sqlalchemy
from tabrion import TabrionError
from tabrion.check import Finding, Severity

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.logs import forget_secrets, register_secret
from tabrion_studio.services import backup_hints
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.diagnosis_report import DiagnosisReports, ReportError, hide_names, markdown_text
from tabrion_studio.services.diagnostics import BACKUP_CODES, Diagnostics
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, TOKEN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")

CANARY = f"tabrion-canary-{uuid.uuid4()}"
NOW = 2_000_000_000.0
DAY = 86_400.0


def _assert_no_canary(text: str) -> None:
    assert CANARY not in text
    assert CANARY.removeprefix("tabrion-canary-") not in text


def _assert_absent(text: str, value: str) -> None:
    """``value`` is in ``text`` neither as it is nor as Markdown escapes it."""
    assert value not in text, value
    assert re.sub(r"([\\`*_\[\]<>!|~&#])", r"\\\1", value) not in text, value


@pytest.fixture(autouse=True)
def _forget() -> Iterator[None]:
    yield
    forget_secrets()


def sqlite_file(path: Path, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript("CREATE TABLE part (id INTEGER PRIMARY KEY, note TEXT); INSERT INTO part VALUES (1, 'x');")
    connection.commit()
    connection.close()
    path.chmod(mode)
    return path


def copy_of(database: Path, target: Path, *, mode: int = 0o600, at: float | None = None) -> Path:
    """A backup as tabrion.sqlite.backup would leave it: a SQLite file (here with sqlite3's own backup API)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source, copy = sqlite3.connect(database), sqlite3.connect(target)
    source.backup(copy)
    copy.close()
    source.close()
    target.chmod(mode)
    if at is not None:
        os.utime(target, (at, at))
    return target


def touch(path: Path, at: float) -> None:
    os.utime(path, (at, at))


def hint_codes(doc: dict[str, Any]) -> dict[str, str]:
    return {item["code"]: item["severity"] for item in doc["hints"]}


# SQLite backup hints.


def test_no_backup_is_a_warning(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert hint_codes(doc) == {"STU-BK-001": "warning"}
    assert doc["facts"]["backups_found"] == 0 and doc["facts"]["newest_backup_at"] is None


@posix
def test_a_fresh_private_backup_has_no_hint(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    touch(database, NOW - DAY)
    copy_of(database, tmp_path / "shop-backup.db", at=NOW - 3600)
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert doc["hints"] == []
    facts = doc["facts"]
    assert facts["backups_found"] == 1 and facts["newest_backup_at"] == NOW - 3600
    assert facts["newest_backup_age_s"] == 3600 and facts["backups_readable_by_others"] == 0


@posix
@pytest.mark.parametrize(("behind", "severity"), [(2 * 3600, "info"), (8 * DAY, "warning")])
def test_a_backup_older_than_the_data(tmp_path: Path, behind: float, severity: str) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    copy_of(database, tmp_path / "backups" / "shop.2026-11-01.db", at=NOW - behind - 60)
    touch(database, NOW - 60)
    wal = tmp_path / "shop.db-wal"
    if wal.exists():
        touch(wal, NOW - 60)
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert hint_codes(doc) == {"STU-BK-002": severity}
    assert ("8 day(s)" if behind > DAY else "2 hour(s)") in doc["hints"][0]["detail"]


@posix
def test_the_wal_file_counts_as_a_change(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    copy_of(database, tmp_path / "shop-v1.db", at=NOW - DAY)
    touch(database, NOW - 2 * DAY)  # the main file is older than the backup, but the WAL file has newer writes
    wal = tmp_path / "shop.db-wal"
    wal.write_bytes(b"\0" * 32)
    touch(wal, NOW - 600)
    assert hint_codes(backup_hints.sqlite_hints(database, clock=lambda: NOW)) == {"STU-BK-002": "info"}


@posix
def test_a_backup_readable_by_others(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    touch(database, NOW - DAY)
    copy_of(database, tmp_path / "shop.db.bak", mode=0o644, at=NOW - 60)
    copy_of(database, tmp_path / "backup" / "shop_old.db", mode=0o600, at=NOW - 120)
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert hint_codes(doc) == {"STU-BK-003": "warning"}
    assert doc["facts"]["backups_found"] == 2 and doc["facts"]["backups_readable_by_others"] == 1
    assert doc["hints"][0]["detail"].startswith("1 backup file(s)")


@posix
def test_what_is_not_a_backup(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    other = sqlite_file(tmp_path / "elsewhere" / "x.db")
    (tmp_path / "shop.db.bak").write_text("not a database\n")  # the right name, but no SQLite header
    copy_of(database, tmp_path / "shopping.db")  # another name: "shop" is not followed by . - or _
    copy_of(database, tmp_path / "shop-old.db.tmp")  # tabrion's temporary file of a running backup
    (tmp_path / "shop-link.db").symlink_to(other)  # a link is never followed
    (tmp_path / "shop-dir.db").mkdir()
    copy_of(database, tmp_path / "nested" / "backups" / "shop-1.db")  # only the database's own subfolders
    (tmp_path / "shop-fifo.db").unlink(missing_ok=True)
    os.mkfifo(tmp_path / "shop-fifo.db")  # a FIFO is not opened (it would block)
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert hint_codes(doc) == {"STU-BK-001": "warning"}


def test_the_search_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    for number in range(5):
        (tmp_path / f"a{number}.txt").write_text("x")
    monkeypatch.setattr(backup_hints, "MAX_ENTRIES", 3)
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert doc["facts"]["search_truncated"] is True
    assert hint_codes(doc) == {"STU-BK-001": "warning", "STU-BK-005": "info"}  # L-4: a hint, not only a fact


def test_an_unreadable_folder_is_a_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    (tmp_path / "backups").mkdir()
    real = backup_hints._open_folder

    def open_folder(directory: Path) -> int | None:
        if directory.name == "backups":
            raise PermissionError(13, "denied")
        return real(directory)

    monkeypatch.setattr(backup_hints, "_open_folder", open_folder)
    found = hint_codes(backup_hints.sqlite_hints(database, clock=lambda: NOW))
    assert found == {"STU-BK-001": "warning", "STU-BK-004": "info"}


@posix
def test_no_hint_or_fact_names_a_path_or_file(tmp_path: Path) -> None:
    folder = tmp_path / CANARY
    database = sqlite_file(folder / f"{CANARY}.db")
    copy_of(database, folder / f"{CANARY}.db.bak", mode=0o644, at=NOW - 8 * DAY)
    touch(database, NOW)
    text = json.dumps(backup_hints.sqlite_hints(database, clock=lambda: NOW))
    _assert_no_canary(text)
    assert str(tmp_path) not in text and "STU-BK-002" in text and "STU-BK-003" in text


@posix
@pytest.mark.parametrize(("mode", "exposed"), [(0o640, 1), (0o604, 1), (0o620, 0), (0o602, 0), (0o611, 0)])
def test_l5_readable_means_the_read_bits(tmp_path: Path, mode: int, exposed: int) -> None:
    """L-5 and L-6: group-only and others-only read count; write- or execute-only bits do not."""
    database = sqlite_file(tmp_path / "shop.db")
    touch(database, NOW - DAY)
    copy_of(database, tmp_path / "shop-1.db", mode=mode, at=NOW)
    doc = backup_hints.sqlite_hints(database, clock=lambda: NOW)
    assert doc["facts"]["backups_readable_by_others"] == exposed
    assert ("STU-BK-003" in hint_codes(doc)) is bool(exposed)


@posix
def test_l2_a_linked_backup_folder_is_not_followed(tmp_path: Path) -> None:
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    database = sqlite_file(project / "shop.db")
    copy_of(database, elsewhere / "shop-1.db", mode=0o644)
    (project / "backups").symlink_to(elsewhere, target_is_directory=True)
    (project / "backup").symlink_to(elsewhere, target_is_directory=True)
    seen = backup_hints.sqlite_backups(database)
    assert (seen["found"], seen["exposed"], seen["unreadable"]) == (0, 0, False)
    copy_of(database, project / "backups-real" / "x.db")  # control: a real subfolder of that name is searched
    (project / "backups").unlink()
    (project / "backups-real").rename(project / "backups")
    (project / "backups" / "x.db").rename(project / "backups" / "shop-2.db")
    assert backup_hints.sqlite_backups(database)["found"] == 1


@posix
def test_l6_the_lstat_check_alone_refuses_a_linked_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    target = copy_of(database, tmp_path / "elsewhere" / "x.db")
    (tmp_path / "shop-link.db").symlink_to(target)
    monkeypatch.setattr(backup_hints, "_has_sqlite_header", lambda *args, **kwargs: True)
    assert backup_hints.sqlite_backups(database)["found"] == 0


@posix
def test_l6_o_nofollow_alone_refuses_a_linked_file(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    target = copy_of(database, tmp_path / "elsewhere" / "x.db")
    (tmp_path / "shop-link.db").symlink_to(target)
    assert backup_hints._has_sqlite_header(str(target)) is True  # control
    assert backup_hints._has_sqlite_header(str(tmp_path / "shop-link.db")) is False
    folder = os.open(tmp_path, os.O_RDONLY)
    try:
        assert backup_hints._has_sqlite_header("shop-link.db", folder=folder) is False
    finally:
        os.close(folder)


@pytest.mark.parametrize(
    ("name", "counted"),
    [("shop.1.db", True), ("shop-1.db", True), ("shop_1.db", True), ("shop1.db", False), ("shopping.db", False),
     ("xshop-1.db", False)],
)  # fmt: skip
def test_l4_a_separator_follows_the_name(tmp_path: Path, name: str, counted: bool) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    copy_of(database, tmp_path / name)
    assert backup_hints.sqlite_backups(database)["found"] == int(counted)


def test_l3_the_scan_stops_between_entries(tmp_path: Path) -> None:
    database = sqlite_file(tmp_path / "shop.db")
    for number in range(3):
        (tmp_path / f"note{number}.txt").write_text("x")
    asked: list[int] = []

    def stop() -> bool:
        asked.append(1)
        return len(asked) > 1

    with pytest.raises(backup_hints.ScanStopped):
        backup_hints.sqlite_backups(database, stop=stop)
    assert len(asked) == 2


def test_l3_a_cancel_during_the_scan_is_stu_dx_008(
    lite: tuple[Diagnostics, ConnectionRegistry, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics, registry, path = lite
    for number in range(5):
        (path.parent / f"note{number}.txt").write_text("x")
    connection_id = registry.open("project:shop").id
    real = backup_hints._is_backup_name

    def cancel_then_check(name: str, database: str, stem: str) -> bool:
        if diagnostics.cancel(connection_id):
            time.sleep(0.05)  # the cancel thread sets the hook
        return real(name, database, stem)

    monkeypatch.setattr(backup_hints, "_is_backup_name", cancel_then_check)
    doc = diagnostics.run(connection_id, backup=True)
    found = {item["code"]: item["detail"] for item in doc["findings"]}
    assert "you canceled it" in found["STU-DX-008"] and "backup hints" in found["STU-DX-008"]
    assert doc["backup"]["hints"] == []


def test_l6_hints_are_sorted_by_severity_then_code(
    lite: tuple[Diagnostics, ConnectionRegistry, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics, registry, _ = lite
    unsorted = [
        {"code": code, "severity": severity, "title": "t", "detail": "d", "remedy": "r", "source": "studio"}
        for code, severity in (("STU-BK-004", "info"), ("STU-BK-003", "warning"), ("STU-BK-001", "warning"))
    ]
    monkeypatch.setattr(backup_hints, "sqlite_hints", lambda *args, **kwargs: {"hints": unsorted, "facts": {}})
    doc = diagnostics.run(registry.open("project:shop").id, backup=True)
    assert [item["code"] for item in doc["backup"]["hints"]] == ["STU-BK-001", "STU-BK-003", "STU-BK-004"]


def test_l6_json_keys_are_redacted(
    lite: tuple[Diagnostics, ConnectionRegistry, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics, registry, _ = lite
    register_secret(CANARY)
    facts = {"engine": "sqlite", f"key {CANARY}": 1, f"password={CANARY}": "x"}
    monkeypatch.setattr(backup_hints, "sqlite_hints", lambda *args, **kwargs: {"hints": [], "facts": facts})
    report = DiagnosisReports(registry, diagnostics).build(registry.open("project:shop").id, "json")
    _assert_no_canary(report.body.decode("utf-8"))


# PostgreSQL backup hints: the rules.


def _row(mode: str = "on", standby: bool = False, archived: int = 10, failed: int = 0, **times: Any) -> tuple[Any, ...]:
    return (mode, standby, archived, failed, times.get("archived_at"), times.get("failed_at"), times.get("reset"))


@pytest.mark.parametrize(
    ("row", "prod", "expected"),
    [
        (_row(archived_at=decimal.Decimal(NOW)), False, {}),
        (_row("off", archived=0), False, {"STU-BK-010": "info"}),
        (_row("off", archived=0), True, {"STU-BK-010": "warning"}),
        (_row(failed=3, archived_at=NOW - 60, failed_at=NOW), False, {"STU-BK-011": "warning"}),
        (_row(failed=3, archived_at=NOW - 60, failed_at=NOW), True, {"STU-BK-011": "error"}),
        (_row(archived=0, failed=2, failed_at=NOW), False, {"STU-BK-011": "warning"}),
        (_row(failed=3, archived_at=NOW, failed_at=NOW - 60), False, {}),  # failed once, archives again since
        (_row(archived=0), False, {"STU-BK-012": "info"}),
        (_row(standby=True, archived=0), False, {"STU-BK-013": "info"}),
        (_row("always", standby=True, archived=0), False, {"STU-BK-012": "info"}),
        (None, False, {"STU-BK-014": "info"}),
    ],
)  # fmt: skip
def test_postgres_rules(row: tuple[Any, ...] | None, prod: bool, expected: dict[str, str]) -> None:
    assert hint_codes(backup_hints.postgres_hints(row, prod=prod)) == expected


def test_postgres_facts_are_numbers_and_times() -> None:
    facts = backup_hints.postgres_hints(_row(archived_at=decimal.Decimal("1.5"), reset=NOW), prod=False)["facts"]
    assert facts == {
        "engine": "postgresql",
        "archive_mode": "on",
        "standby": False,
        "archived_count": 10,
        "failed_count": 0,
        "last_archived_at": 1.5,
        "last_failed_at": None,
        "stats_reset_at": NOW,
    }


class FakePostgres:
    """A PostgreSQL connection that answers the archiver query with ``row`` and every other query with 0."""

    id, profile_id, engine, timeout_s, database_file = "c_fake", "user:pg", "postgresql", 30.0, None

    def __init__(self, environment: str, findings: list[Finding], row: tuple[Any, ...] | None) -> None:
        self.environment, self.findings, self.row = environment, findings, row

    def tabrion_findings(self, *, cancel: Any = None) -> list[Finding]:
        return self.findings

    def fetch(self, sql: str, params: Any = None, *, cancel: Any = None) -> list[tuple[Any, ...]]:
        if sql == backup_hints.ARCHIVER_SQL:
            if self.row is None:
                raise TabrionError("db.permission")
            return [self.row]
        return [] if "pg_db_role_setting" in sql else [(0,)]


def _fake_run(environment: str, row: tuple[Any, ...] | None, *, backup: bool) -> dict[str, Any]:
    connection = FakePostgres(environment, [Finding("TAB-PG-020", Severity.INFO, "t", "d", "r")], row)
    profile = Profile(
        "pg", "postgresql", environment, host="db.example.internal", database="app", sslmode="verify-full"
    )

    class Registry:
        source = Source(profiles={"user:pg": profile})

        def get(self, _connection_id: object) -> FakePostgres:
            return connection

    return Diagnostics(Registry(), clock=lambda: NOW).run("c_fake", backup=backup)  # type: ignore[arg-type]


def test_backup_hints_replace_tabrions_archive_finding() -> None:
    """TAB-PG-020 stays in a plain diagnosis; with backup hints STU-BK-010 says it (one cause, one entry)."""
    plain = _fake_run("dev", _row("off", archived=0), backup=False)
    assert "TAB-PG-020" in {item["code"] for item in plain["findings"]} and "backup" not in plain
    full = _fake_run("prod", _row("off", archived=0), backup=True)
    assert "TAB-PG-020" not in {item["code"] for item in full["findings"]}
    assert hint_codes(full["backup"]) == {"STU-BK-010": "warning"}
    assert BACKUP_CODES == {"TAB-PG-020": "STU-BK-010"}


@pytest.mark.parametrize("row", [None, _row(archived=0), _row(archived_at=NOW)])
def test_c2_tabrions_archive_finding_stays_without_stu_bk_010(row: tuple[Any, ...] | None) -> None:
    """C-2: TAB-PG-020 goes only when STU-BK-010 is given: not for STU-BK-014, nor when tabrion and the archiver
    disagree (archive_mode changed between the two reads)."""
    doc = _fake_run("dev", row, backup=True)
    assert "TAB-PG-020" in {item["code"] for item in doc["findings"]}
    assert "STU-BK-010" not in hint_codes(doc["backup"])


@pytest.mark.parametrize("stop", ["cancel", "limit"])
def test_c2_a_stopped_archiver_keeps_tabrions_finding_and_says_so(stop: str) -> None:
    """C-2 and L-6: a cancel (or the time limit) during the archiver query is STU-DX-008 "backup hints", never
    STU-BK-014, and TAB-PG-020 stays."""
    holder: dict[str, Diagnostics] = {}

    class Stopping(FakePostgres):
        def fetch(self, sql: str, params: Any = None, *, cancel: Any = None) -> list[tuple[Any, ...]]:
            if sql == backup_hints.ARCHIVER_SQL:
                if stop == "cancel":
                    assert holder["diagnostics"].cancel("c_fake")
                deadline = time.monotonic() + 3
                while not cancel.requested and time.monotonic() < deadline:
                    time.sleep(0.005)
                raise TabrionError("db.query_canceled")
            return super().fetch(sql, params, cancel=cancel)

    connection = Stopping("dev", [Finding("TAB-PG-020", Severity.INFO, "t", "d", "r")], _row("off"))
    if stop == "limit":
        connection.timeout_s = 0.2
    profile = Profile("pg", "postgresql", "dev", host="db.example.internal", database="app", sslmode="verify-full")

    class Registry:
        source = Source(profiles={"user:pg": profile})

        def get(self, _connection_id: object) -> FakePostgres:
            return connection

    diagnostics = Diagnostics(Registry(), clock=lambda: NOW)  # type: ignore[arg-type]
    holder["diagnostics"] = diagnostics
    doc = diagnostics.run("c_fake", backup=True)
    found = {item["code"]: item["detail"] for item in doc["findings"]}
    assert "TAB-PG-020" in found and "backup hints" in found["STU-DX-008"]
    assert doc["backup"]["hints"] == []


def test_an_unreadable_archiver_is_a_hint_not_a_failure() -> None:
    doc = _fake_run("dev", None, backup=True)
    assert hint_codes(doc["backup"]) == {"STU-BK-014": "info"}
    assert backup_hints.postgres_hints((0,), prod=False)["hints"][0]["code"] == "STU-BK-014"  # an unexpected row


# The diagnosis with its backup hints.


@pytest.fixture
def lite(tmp_path: Path) -> Iterator[tuple[Diagnostics, ConnectionRegistry, Path]]:
    path = sqlite_file(tmp_path / "shop.db")
    source = Source(profiles={"project:shop": Profile("shop", "sqlite", "dev", path="shop.db")}, files={
        "project:shop": path})  # fmt: skip
    registry = ConnectionRegistry(source)
    try:
        yield Diagnostics(registry, clock=lambda: NOW), registry, path
    finally:
        registry.close_all()


def test_backup_hints_only_when_asked(lite: tuple[Diagnostics, ConnectionRegistry, Path]) -> None:
    diagnostics, registry, _ = lite
    connection_id = registry.open("project:shop").id
    assert "backup" not in diagnostics.run(connection_id)
    doc = diagnostics.run(connection_id, backup=True)
    assert hint_codes(doc["backup"]) == {"STU-BK-001": "warning"}
    assert doc["counts"] == {"error": 0, "warning": 0, "info": 0}  # hints are not findings


def test_the_time_limit_skips_the_backup_hints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = sqlite_file(tmp_path / "shop.db")
    profile = Profile("shop", "sqlite", "dev", path="shop.db", statement_timeout_ms=20)
    registry = ConnectionRegistry(Source(profiles={"project:shop": profile}, files={"project:shop": path}))

    def slow(_connection: OpenConnection, _run: Any) -> list[dict[str, str]]:
        time.sleep(0.3)  # past the 20 ms limit: the hook is set before the backup step
        return []

    monkeypatch.setattr(Diagnostics, "_sqlite", staticmethod(slow))
    try:
        doc = Diagnostics(registry).run(registry.open("project:shop").id, backup=True)
    finally:
        registry.close_all()
    found = {item["code"]: item["detail"] for item in doc["findings"]}
    assert "backup hints" in found["STU-DX-008"]
    assert doc["backup"]["hints"] == [] and doc["backup"]["facts"]["searched"] is False


# The report.


def test_report_formats(lite: tuple[Diagnostics, ConnectionRegistry, Path]) -> None:
    diagnostics, registry, _ = lite
    connection_id = registry.open("project:shop").id
    reports = DiagnosisReports(registry, diagnostics, clock=lambda: NOW)
    as_json = reports.build(connection_id, "json")
    assert as_json.media_type == "application/json"
    assert as_json.filename == "tabrion-studio-diagnosis-sqlite-dev-20330518T033320Z.json"
    doc = json.loads(as_json.body)
    assert set(doc) == {
        "report", "format_version", "generated_at", "studio_version", "tabrion_version", "connection", "diagnosis",
        "backup", "left_out",
    }  # fmt: skip
    assert doc["report"] == "tabrion-studio-diagnosis" and doc["format_version"] == 1
    assert doc["generated_at"] == "2033-05-18T03:33:20Z" and doc["diagnosis"]["ran_at"] == "2033-05-18T03:33:20Z"
    assert doc["connection"] == {
        "profile": "shop", "engine": "sqlite", "environment": "dev", "read_only": True,
    }  # fmt: skip
    assert [item["code"] for item in doc["backup"]["hints"]] == ["STU-BK-001"]
    assert doc["backup"]["facts"]["database_changed_at"].endswith("Z")
    as_markdown = reports.build(connection_id, "markdown")
    assert as_markdown.media_type == "text/markdown; charset=utf-8"
    assert as_markdown.filename.endswith(".md")
    text = as_markdown.body.decode("utf-8")
    assert text.startswith("# Tabrion Studio diagnosis report\n")
    for heading in ("## Findings", "No findings.", "## Backup hints", "### Warning · STU-BK-001 · No backup found",
                    "## Backup facts", "| backups\\_found | 0 |", "Left out of this report:"):  # fmt: skip
        assert heading in text
    with pytest.raises(ReportError) as refused:
        reports.build(connection_id, "html")
    assert refused.value.code == "studio.report_invalid"


def test_markdown_text_cannot_become_markup() -> None:
    hostile = "x](https://evil.example) <img src=x onerror=1> `code` *em* | cell |\n# heading www.evil.example &amp;"
    assert markdown_text(hostile) == (
        "x\\](https:\\/\\/evil.example) \\<img src=x onerror=1\\> \\`code\\` \\*em\\* \\| cell \\| \\# heading "
        "www\\.evil.example \\&amp;"
    )
    assert markdown_text("a\r\nb\u2028c\u202ed\x00e") == "a b c d e"
    assert markdown_text("@octocat jdoe@example.com") == "\\@octocat jdoe\\@example.com"  # L-1


def test_markdown_text_scrubs_on_its_own() -> None:
    """L-6: the Markdown side redacts too, without the JSON-side ``_clean`` before it."""
    register_secret(CANARY)
    _assert_no_canary(markdown_text(f"a {CANARY} b"))
    _assert_no_canary(markdown_text(f"postgresql://u:{CANARY}@h/db"))


def test_hide_names_whole_identifiers_only() -> None:
    names = {"app": "[role]", "shop": "[database]", "shop_owner": "[role]"}
    assert hide_names("schema(s) app, shop; application_name, shop_owner, apps, $app, app.t", names) == (
        "schema(s) [role], [database]; application_name, [role], apps, $app, [role].t"
    )


def test_the_report_route(lite: tuple[Diagnostics, ConnectionRegistry, Path], tmp_path: Path) -> None:
    diagnostics, registry, _ = lite
    connection_id = registry.open("project:shop").id
    studio = make_studio(tmp_path / "static", connections=registry, diagnostics=diagnostics)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    route = f"/api/connections/{connection_id}/diagnostics/report"
    answer = studio.client.post(route, json={"format": "markdown"}, headers=headers)
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "text/markdown; charset=utf-8"
    assert re.fullmatch(
        r'attachment; filename="tabrion-studio-diagnosis-sqlite-dev-\d{8}T\d{6}Z\.md"',
        answer.headers["content-disposition"],
    )
    assert answer.headers["cache-control"] == "no-store"
    assert "STU-BK-001" in answer.text
    as_json = studio.client.post(route, json={"format": "json"}, headers=headers)
    assert as_json.status_code == 200 and as_json.json()["connection"]["engine"] == "sqlite"
    view = studio.client.post(f"/api/connections/{connection_id}/diagnostics", json={}, headers=headers)
    assert hint_codes(view.json()["backup"]) == {"STU-BK-001": "warning"}  # the view gets the hints too
    bodies: list[object] = [{"format": "pdf"}, {}, [], {"format": ["json"]}]
    for body in bodies:
        refused = studio.client.post(route, json=body, headers=headers)
        assert (refused.status_code, refused.json()["code"]) == (400, "studio.report_invalid")
    oversized = json.dumps({"format": "json", "x": "a" * 1100}).encode()  # valid JSON over the 1 KiB cap (L-6)
    for raw in (b"{", b"x" * 2000, oversized):
        refused = studio.client.post(route, content=raw, headers={**headers, "Content-Type": "application/json"})
        assert refused.json()["code"] == "studio.report_invalid"
    gone = studio.client.post("/api/connections/c_nope/diagnostics/report", json={"format": "json"}, headers=headers)
    assert gone.json()["code"] == "studio.connection_not_found"
    no_csrf = studio.client.post(route, json={"format": "json"}, headers={"Origin": ORIGIN})
    assert no_csrf.json()["code"] == "access.csrf_invalid"
    signed_out = make_studio(tmp_path / "static2", connections=registry, diagnostics=diagnostics)
    assert signed_out.client.post(route, json={"format": "json"}, headers={"Origin": ORIGIN}).status_code in (401, 403)


# Canaries (09: "report without secrets").


def _hostile_findings(*values: str) -> list[Finding]:
    """tabrion findings whose texts carry secrets in every shape the scrubber knows, as a bug or a server might."""
    texts = [
        *values,
        f"postgresql://studio_ro:{CANARY}@db.internal:6543/parts",
        f"postgresql+psycopg://u:{CANARY}@h/db?sslmode=verify-full",
        f"host=db.internal password={CANARY} dbname=parts",
        f"password='{CANARY} with space'",
        f"Authorization: Bearer {CANARY}",
        f"api_key={CANARY}&x=1",
        f"tabrion_studio_session_1234={CANARY}",
    ]
    return [Finding("TAB-SQ-004", Severity.WARNING, text, f"detail {text}", f"remedy {text}") for text in texts]


@posix
def test_canary_sqlite_report_over_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    """The path and name of the database, a parameter value, a value in the data, the process's tokens and
    secret-shaped finding texts reach neither the report (both formats) nor the log."""
    caplog.set_level(logging.DEBUG)
    folder = tmp_path / CANARY
    database = sqlite_file(folder / f"{CANARY}.db", mode=0o644)
    copy_of(database, folder / "backups" / f"{CANARY}-1.db", mode=0o644, at=time.time() - 9 * DAY)
    writer = sqlite3.connect(database)
    writer.execute("INSERT INTO part VALUES (2, ?)", (CANARY,))
    writer.commit()
    writer.close()
    source = Source(profiles={"project:shop": Profile("shop", "sqlite", "dev", path=f"{CANARY}.db")},
                    files={"project:shop": database})  # fmt: skip
    registry = ConnectionRegistry(source)
    diagnostics = Diagnostics(registry)
    studio = make_studio(tmp_path / "static", connections=registry, diagnostics=diagnostics)
    try:
        headers = {**studio.sign_in(), "Origin": ORIGIN}
        csrf = next(iter(value for key, value in headers.items() if key != "Origin"))
        session = next(iter(studio.client.cookies.values()))
        launch = studio.launch_token()
        connection = registry.open("project:shop")
        assert connection.fetch("SELECT :v AS v", {"v": CANARY}) == [(CANARY,)]  # a parameter value on it
        hostile = _hostile_findings(csrf, session, launch, TOKEN)
        real = OpenConnection.tabrion_findings
        monkeypatch.setattr(
            OpenConnection, "tabrion_findings", lambda self, **kwargs: [*real(self, **kwargs), *hostile]
        )
        route = f"/api/connections/{connection.id}/diagnostics/report"
        texts = []
        for report_format in ("markdown", "json"):
            answer = studio.client.post(route, json={"format": report_format}, headers=headers)
            assert answer.status_code == 200
            texts.append(answer.text + answer.headers["content-disposition"])
    finally:
        registry.close_all()
    for text in texts:
        _assert_no_canary(text)
        for secret in (csrf, session, launch, TOKEN, str(tmp_path)):
            _assert_absent(text, secret)
        assert "TAB-SQ-003" in text  # the 0644 file is reported, without its path
        assert "STU-BK-002" in text and "STU-BK-003" in text  # the backup was found, without its name
        assert "[redacted]" in text or "\\[redacted\\]" in text  # the scrubber replaced them
    _assert_no_canary(caplog.text)


@pytest.fixture
def canary_role() -> Iterator[tuple[str, str, sqlalchemy.Engine]]:
    """A login role whose password and ``application_name`` setting are the canary, with a table holding it. C-1 of
    ``stu-025-review.md``: the role has its own ``"$user"`` schema (named exactly like the role) and may create in a
    schema named exactly like the database, which its ``search_path`` holds, so tabrion's TAB-PG-009 names both."""
    if PG_URL is None or PG_SUPERUSER_URL is None:
        pytest.skip("TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed")
    token = secrets.token_hex(4)
    role, data = f"stu025_report_{token}", f"stu025_data_{token}"
    admin = sqlalchemy.create_engine(sqlalchemy.make_url(PG_SUPERUSER_URL).set(drivername="postgresql+psycopg"))
    database = str(sqlalchemy.make_url(PG_URL).database)
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{CANARY}'")
        connection.exec_driver_sql(f"ALTER ROLE {role} SET application_name = '{CANARY}'")
        connection.exec_driver_sql(f'ALTER ROLE {role} SET search_path = "$user", "{database}", public')
        connection.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{database}" TO {role}')
        connection.exec_driver_sql(f"CREATE SCHEMA {role} AUTHORIZATION {role}")
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{database}"')
        connection.exec_driver_sql(f'GRANT USAGE, CREATE ON SCHEMA "{database}" TO {role}')
        connection.exec_driver_sql(f"CREATE SCHEMA {data} AUTHORIZATION {role}")
        connection.exec_driver_sql(f"CREATE TABLE {data}.secret (v text)")
        connection.exec_driver_sql(f"INSERT INTO {data}.secret VALUES ('{CANARY}')")  # noqa: S608 (test names)
        connection.exec_driver_sql(f"ALTER TABLE {data}.secret OWNER TO {role}")
    try:
        yield role, data, admin
    finally:
        with admin.begin() as connection:
            for schema in (data, role, database):
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            connection.exec_driver_sql(f"DROP OWNED BY {role}")
            connection.exec_driver_sql(f"DROP ROLE IF EXISTS {role}")
        admin.dispose()


def _pg_registry(user: str, password: str, **profile: Any) -> ConnectionRegistry:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    base = Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=user,
        password=SecretRef("keyring", ""), sslmode="prefer",
    )  # fmt: skip
    return ConnectionRegistry(Source(profiles={"user:pg": replace(base, **profile)}, passwords={"user:pg": password}))


@pytest.mark.postgres_superuser
def test_canary_postgres_report(
    canary_role: tuple[str, str, sqlalchemy.Engine], monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """The password (also inside a DSN), a parameter value, a setting's value and a stored value; and no host, port,
    login role or database name in the report, also where tabrion names them as schemas (C-1)."""
    caplog.set_level(logging.DEBUG)
    role, data, _ = canary_role
    url = sqlalchemy.make_url(PG_URL or "")
    database = str(url.database)
    registry = _pg_registry(role, CANARY, read_only_role=True)
    try:
        connection = registry.open("user:pg")
        assert connection.fetch(f"SELECT v FROM {data}.secret WHERE v = :v", {"v": CANARY}) == [(CANARY,)]  # noqa: S608
        diagnostics = Diagnostics(registry)
        # The control: the plain diagnosis does name the role's and the database's schema (TAB-PG-009).
        plain = json.dumps(diagnostics.run(connection.id))
        assert re.search(rf"\b{role}\b", plain) and re.search(rf"\b{database}\b", plain), plain
        hostile = _hostile_findings(f"host=db.internal user=app password={CANARY}", f"PGPASSWORD={CANARY}")
        real = OpenConnection.tabrion_findings
        monkeypatch.setattr(
            OpenConnection, "tabrion_findings", lambda self, **kwargs: [*real(self, **kwargs), *hostile]
        )
        reports = DiagnosisReports(registry, diagnostics)
        built = [reports.build(connection.id, report_format) for report_format in ("markdown", "json")]
    finally:
        registry.close_all()
    for report in built:
        text = report.body.decode("utf-8") + report.filename
        _assert_no_canary(text)
        for name in (role, database, str(url.host), f":{url.port}"):
            _assert_absent(text, name)
        assert "[role]" in text.replace("\\", "") and "[database]" in text.replace("\\", "")
        for code in ("STU-DX-003", "STU-DX-006", "STU-DX-010", "STU-BK-01", "TAB-PG-009"):
            assert code in text  # the diagnosis ran: an owner that the profile calls read-only, its settings
    _assert_no_canary(caplog.text)


@pytest.mark.postgres
@postgres
def test_pg_archiver_query_passes_the_statement_check_and_reads() -> None:
    """The archiver query runs as an ordinary role through Studio's read-only path; the test server does not
    archive, so the hint is STU-BK-010 (or STU-BK-011/-012 on a server that does)."""
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    registry = _pg_registry(str(url.username), url.password or "")
    try:
        doc = Diagnostics(registry).run(registry.open("user:pg").id, backup=True)
    finally:
        registry.close_all()
    facts = doc["backup"]["facts"]
    assert facts["archive_mode"] in ("off", "on", "always") and isinstance(facts["archived_count"], int)
    if facts["archive_mode"] == "off":
        assert hint_codes(doc["backup"]) == {"STU-BK-010": "info"}
    assert "STU-DX-008" not in {item["code"] for item in doc["findings"]}


def test_a_registered_secret_never_reaches_a_report(lite: tuple[Diagnostics, ConnectionRegistry, Path],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """Any secret the process registered (keyring, env:, file: values) is replaced, whatever text carries it."""
    diagnostics, registry, _ = lite
    register_secret(CANARY)
    connection_id = registry.open("project:shop").id
    hostile = [Finding("TAB-SQ-004", Severity.INFO, CANARY, f"x{CANARY}x", CANARY)]
    monkeypatch.setattr(OpenConnection, "tabrion_findings", lambda self, **kwargs: hostile)
    reports = DiagnosisReports(registry, diagnostics)
    for report_format in ("markdown", "json"):
        _assert_no_canary(reports.build(connection_id, report_format).body.decode("utf-8"))
