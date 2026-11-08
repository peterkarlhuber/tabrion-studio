"""STU-004 with a real ``tabrion-studio`` process: the log file, the request log and the canary (STU-T-009).

The process writes JSON lines to ``<log dir>/studio-<key>.log`` (0600, in a 0700 folder). Requests with a canary in
the query string, in headers, as a bogus launch token and in the body leave no trace of it in the log file or in the
terminal output, and Uvicorn's own records pass the same redaction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import uuid

import pytest

from tests.support.session import redeem, request, sign_in
from tests.support.studio import start_studio

pytestmark = pytest.mark.security

CANARY = f"tabrion-canary-{uuid.uuid4()}"


def test_log_file_and_request_log_without_canary(project: Path, state_dir: Path, tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    with start_studio(project, state_dir, "--log-dir", str(log_dir)) as studio:
        info, _ = studio.wait_ready()
        port = info.port
        status, _, body = redeem(port, CANARY)  # a canary as the launch token
        assert status == 401 and CANARY not in body.decode()
        session = sign_in(info)
        status, headers, body = request(port, "GET", f"/api/heartbeat?token={CANARY}", session.headers())
        assert status == 204
        status, headers, body = request(port, "GET", f"/api/nowhere?password={CANARY}", {"X-Canary": CANARY})
        assert status == 401
        assert headers["content-type"] == "application/problem+json"
        assert json.loads(body)["code"] == "access.session_required"
        studio.interrupt()
        assert studio.wait_exit() == 0
        output = studio.output()
    logs = list(log_dir.glob("studio-*.log"))
    assert len(logs) == 1, logs
    text = logs[0].read_text(encoding="utf-8")
    assert CANARY not in text and CANARY not in output
    assert f"Log: {logs[0]}" in output
    records = [json.loads(line) for line in text.splitlines()]
    events = [record.get("event") for record in records]
    assert "studio.ready" in events and "studio.stopped" in events
    requests = [record for record in records if record.get("event") == "http.request"]
    assert {"route": "/api/session", "status": 401, "code": "access.launch_invalid"}.items() <= requests[0].items()
    assert any(r["route"] == "<unmatched>" and r["code"] == "access.session_required" for r in requests)
    # The heartbeat is at DEBUG, below the default INFO of the file.
    assert not any(r["route"] == "/api/heartbeat" for r in requests)
    # Uvicorn's records arrive through the same handler; its access log stays off.
    assert any(record["logger"].startswith("uvicorn") for record in records)
    assert not any(record["logger"] == "uvicorn.access" for record in records)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(log_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(logs[0]).st_mode) == 0o600


def test_default_log_dir_with_a_state_dir(project: Path, state_dir: Path) -> None:
    """With --state-dir and no --log-dir, logs go to STATE_DIR/logs (tests never write to the real profile)."""
    with start_studio(project, state_dir) as studio:
        studio.wait_ready()
        studio.interrupt()
        assert studio.wait_exit() == 0
    assert len(list((state_dir / "logs").glob("studio-*.log"))) == 1
