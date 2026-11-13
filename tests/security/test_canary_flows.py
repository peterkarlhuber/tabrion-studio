"""STU-016 (G-S1 preparation), STU-T-008/-009 (T-05) across the pages: a canary value in the database, in query
parameters, in SQL text, in a viewer filter and in export parameters never reaches a log line, and never comes back
in a problem answer.

``test_canary.py`` covers the log machinery and ``test_queries.py`` the query engine on its own; this test drives the
whole application through its routes, as the page does, with every service: profiles, connections, the SQL editor's
queries (a failing one too), the data viewer (rows, a filter, one cell), exports (a working and a failing one) and the
diagnosis with its report.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

import pytest

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.project import ProjectConfig
from tabrion_studio.config.user import UserConfig
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.diagnostics import Diagnostics
from tabrion_studio.services.explorer import Explorer
from tabrion_studio.services.exports import Exports
from tabrion_studio.services.migrations import Migrations
from tabrion_studio.services.queries import QueryEngine
from tabrion_studio.services.viewer import Viewer
from tests.support.access import ORIGIN, Studio, make_studio

pytestmark = pytest.mark.security

CANARY = f"tabrion-canary-{uuid.uuid4()}"


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Path]:
    setup: LogSetup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        yield tmp_path / "logs"
    finally:
        setup.close()
        forget_secrets()


@pytest.fixture
def studio(static_dir: Path, tmp_path: Path, logs: Path) -> Iterator[Studio]:
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    state.mkdir(mode=0o700)
    configuration = Configuration(
        project=ProjectConfig(project, state, "k1", tmp_path / "config"), user=UserConfig(tmp_path / "config")
    )
    registry = ConnectionRegistry(configuration)
    explorer = Explorer(registry)
    studio = make_studio(
        static_dir,
        config=configuration,
        connections=registry,
        queries=QueryEngine(registry, configuration.limits),
        explorer=explorer,
        viewer=Viewer(explorer, configuration.limits),
        diagnostics=Diagnostics(registry),
        migrations=Migrations(registry, CodeTrust(project, state, "k1")),
        exports=Exports(registry, explorer),
    )
    try:
        yield studio
    finally:
        registry.close_all()


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, secret TEXT)")
    connection.execute("INSERT INTO notes (secret) VALUES (?), ('plain')", (CANARY,))
    connection.commit()
    connection.close()
    path.chmod(0o600)


def _finished(studio: Studio, headers: dict[str, str], query_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while True:
        doc: dict[str, Any] = studio.client.get(f"/api/queries/{query_id}", headers=headers).json()
        if doc["state"] != "running" or time.monotonic() > deadline:
            return doc
        time.sleep(0.02)


def test_a_canary_reaches_no_log_line_and_no_problem(studio: Studio, tmp_path: Path, logs: Path) -> None:
    database = tmp_path / "notes.db"
    _database(database)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    problems: list[str] = []

    def send(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
        response = studio.client.request(method, path, json=body, headers=headers, **kwargs)
        if response.status_code >= 400:
            problems.append(response.text)
        return response

    digest = send("GET", "/api/profiles").json()["user_file"]["digest"]
    profile = {"name": "notes", "engine": "sqlite", "path": str(database), "environment": "dev"}
    assert send("POST", "/api/profiles", {"profile": profile, "digest": digest}).status_code == 201
    opened = send("POST", "/api/connections", {"profile_id": "user:notes", "confirmed_environment": "dev"})
    assert opened.status_code == 201, opened.text
    connection_id = opened.json()["connection_id"]

    # The SQL editor: a parameter, a literal and a failing statement, each with the canary.
    for sql in (
        "SELECT secret, :p AS echoed FROM notes",
        f"SELECT '{CANARY}' AS literal",
        f"SELECT no_such_column, '{CANARY}' FROM notes WHERE secret = :p",  # noqa: S608 (a test canary)
    ):
        started = send("POST", "/api/queries", {"connection_id": connection_id, "sql": sql, "params": {"p": CANARY}})
        assert started.status_code == 202, started.text
        final = _finished(studio, headers, started.json()["query_id"])
        assert final["state"] in ("done", "failed"), final
        if final["state"] == "done":
            send("GET", f"/api/queries/{final['query_id']}/rows", params={"offset": 0, "limit": 10})
        else:
            problems.append(json.dumps(final))

    # The data viewer: a filter on the canary, then the cell that holds it.
    base = f"/api/connections/{connection_id}/tables/main/notes"
    filters = json.dumps([{"column": "secret", "op": "eq", "value": CANARY}])
    page = send("GET", f"{base}/rows", params={"limit": 10, "filter": filters})
    assert page.status_code == 200, page.text
    assert page.json()["rows"]
    cell = send("GET", f"{base}/cell", params={"page": page.json()["page"], "row": 0, "column": "secret"})
    assert cell.status_code == 200 and cell.json()["value"] == CANARY
    send("GET", f"{base}/rows", params={"limit": 10, "filter": json.dumps([{"column": CANARY, "op": "eq"}])})

    # Exports: one that works, one that fails on the server.
    for sql in ("SELECT secret FROM notes WHERE secret = :p", "SELECT no_such_column FROM notes WHERE secret = :p"):
        export = send(
            "POST",
            "/api/exports",
            {"connection_id": connection_id, "format": "csv", "sql": sql, "params": {"p": CANARY}},
        )
        if export.status_code == 200:
            assert CANARY in export.text  # the file itself holds the data; it is not a log
        else:
            problems.append(export.text)

    # The diagnosis and its report.
    assert send("POST", f"/api/connections/{connection_id}/diagnostics", {}).status_code == 200
    report = send("POST", f"/api/connections/{connection_id}/diagnostics/report", {"format": "json"})
    assert report.status_code == 200, report.text
    assert CANARY not in report.text

    studio.runtime.connections.close_all()  # type: ignore[union-attr]
    # The test client's own request lines (logger ``httpx``) are not Studio's: the server runs uvicorn with
    # ``access_log=False`` and logs the route template only (``tabrion_studio.request``).
    lines = [
        line
        for path in logs.glob("*")
        if path.is_file()
        for line in path.read_text(encoding="utf-8").splitlines()
        if not json.loads(line).get("logger", "").startswith("httpx")
    ]
    assert any("studio.query_finished" in line for line in lines)
    assert [line[:300] for line in lines if CANARY in line] == []
    assert problems, "the failing statement and export must have answered a problem"
    for problem in problems:
        assert CANARY not in problem
