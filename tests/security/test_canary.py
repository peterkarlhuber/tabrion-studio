"""STU-004: no secret, parameter value, path or stack trace in a problem answer or a log line (STU-T-008, -009).

Threat T-05 (03 section 7.3), SQ-04 (07 section 1.4), DoD point 4 (07 section 6). A canary value in the style of
TAB-T-110 (``tabrion-canary-<uuid>``) is put where secrets and values travel: the password of a connection URL, a
server ``DETAIL`` text, SQLAlchemy-style ``[parameters: ...]``, a chained cause, an exception note, a local variable,
the query string, headers, the body and the process's own tokens. The tests then read the HTTP answer and both log
outputs (the JSON file and the console) and require the canary to be absent from all of them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
import contextlib
import gc
import io
import json
import logging
from pathlib import Path
from typing import Any
import uuid

from fastapi import FastAPI, Request
import pytest
import uvicorn

from tabrion_studio.idle import IdleMonitor
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets, register_secret
from tabrion_studio.problems import CATALOG, PROBLEM_MEDIA_TYPE
from tests.support.access import CONTROL_AUTH, ORIGIN, TOKEN, Studio, make_studio

pytestmark = pytest.mark.security

CANARY = f"tabrion-canary-{uuid.uuid4()}"


SQL_LINE = "[SQL: INSERT INTO users (email) VALUES (%(email)s)]\n"


class FakeDriverError(Exception):
    """Looks like a psycopg error: the message carries a DETAIL line with a value (B-06-1)."""


class Logs:
    def __init__(self, setup: LogSetup, console: io.StringIO) -> None:
        self.setup = setup
        self.console = console

    def file_text(self) -> str:
        for handler in self.setup.handlers:
            handler.flush()
        assert self.setup.file is not None
        return self.setup.file.read_text(encoding="utf-8")

    def records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.file_text().splitlines() if line.strip()]

    def everything(self) -> str:
        return self.file_text() + self.console.getvalue()


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Logs]:
    console = io.StringIO()
    # The test client's own logger (the client side, not Studio) logs every URL it requests.
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    setup = configure_logging(tmp_path / "logs", "studio-test", level=logging.DEBUG, console=console)
    try:
        yield Logs(setup, console)
    finally:
        setup.close()
        forget_secrets()


def add_route(studio: Studio, path: str, endpoint: Callable[..., Any], methods: list[str] | None = None) -> None:
    """Add a test route in front of the static files mount (which matches every path)."""
    app = studio.client.app
    app.add_api_route(path, endpoint, methods=methods)  # type: ignore[attr-defined]
    routes = app.router.routes  # type: ignore[attr-defined]
    routes.insert(0, routes.pop())


def _failing_route(studio: Studio) -> None:
    async def explode(request: Request) -> None:
        password = CANARY  # a local variable: tracebacks must not show locals
        url = f"postgresql://studio_ro:{password}@db.internal:5432/parts"
        try:
            raise FakeDriverError(
                f'duplicate key value violates unique constraint "users_email_key"\n'
                f"DETAIL:  Key (email)=({CANARY}@example.com) already exists.\n"
                + SQL_LINE
                + f"[parameters: {{'email': '{CANARY}'}}]"
            )
        except FakeDriverError as exc:
            exc.add_note(f"note with {CANARY}")
            raise RuntimeError(f"cannot connect to {url} with password={password}") from exc

    add_route(studio, "/api/explode", explode, ["GET", "POST"])


def _signed_in(studio: Studio) -> dict[str, str]:
    headers = studio.sign_in()
    return {**headers, "Origin": ORIGIN}


def _assert_no_canary(text: str) -> None:
    assert CANARY not in text
    assert CANARY.removeprefix("tabrion-canary-") not in text


def test_unexpected_error_answers_a_problem_without_details(static_dir: Path, logs: Logs) -> None:
    """STU-T-008: the answer is studio.internal_error with a log ID; no message, path or traceback."""
    studio = make_studio(static_dir)
    _failing_route(studio)
    response = studio.client.post(
        f"/api/explode?password={CANARY}&q={CANARY}",
        headers={**_signed_in(studio), "X-Debug": CANARY},
        json={"password": CANARY},
    )
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    doc = response.json()
    entry = CATALOG["studio.internal_error"]
    assert doc == {
        "code": "studio.internal_error",
        "kind": "internal",
        "status": 500,
        "title": entry.title,
        "detail": entry.detail,
        "hint": entry.hint,
        "log_id": doc["log_id"],
    }
    _assert_no_canary(response.text)
    for leak in ("Traceback", ".py", "FakeDriverError", "RuntimeError", "DETAIL", "postgresql://", "db.internal"):
        assert leak not in response.text


def test_unexpected_error_is_logged_once_with_frames_but_without_values(static_dir: Path, logs: Logs) -> None:
    """STU-T-009: the log has the log ID, the exception types and frames, and no canary anywhere."""
    studio = make_studio(static_dir)
    _failing_route(studio)
    response = studio.client.get(f"/api/explode?token={CANARY}", headers=_signed_in(studio))
    log_id = response.json()["log_id"]
    _assert_no_canary(logs.everything())
    errors = [record for record in logs.records() if record.get("event") == "studio.internal_error"]
    assert len(errors) == 1
    error = errors[0]
    assert error["log_id"] == log_id
    assert error["level"] == "ERROR"
    trace = str(error["exception"])
    assert "Traceback (most recent call last)" in trace
    assert "in explode" in trace  # frames are kept
    assert f"{__name__}.FakeDriverError: [message redacted]" in trace
    assert "RuntimeError: [message redacted]" in trace
    for leak in ("DETAIL", "parameters", "note with", "db.internal"):
        assert leak not in trace
    # The console (stderr) gets the error too, redacted the same way.
    assert log_id in logs.console.getvalue()
    request = [record for record in logs.records() if record.get("event") == "http.request"][-1]
    assert request["route"] == "/api/explode"
    assert request["status"] == 500
    assert request["code"] == "studio.internal_error"
    assert request["log_id"] == log_id


def test_tokens_of_the_process_never_reach_a_log_line(static_dir: Path, logs: Logs) -> None:
    """Control token, launch tokens, session IDs and CSRF values are redacted even if code logs them by mistake."""
    studio = make_studio(static_dir)
    launch = studio.runtime.launches.issue(redirect=False).url
    launch_token = launch.split("#", 1)[1]
    response = studio.client.post("/api/session", json={"token": studio.launch_token()}, headers={"Origin": ORIGIN})
    csrf = response.json()["csrf"]
    session_id = response.cookies.get("tabrion_studio_session_1234")
    assert session_id
    log = logging.getLogger("tabrion_studio.test")
    log.warning("control %s launch %s", TOKEN, launch)
    log.warning("session %s csrf %s", session_id, csrf, extra={"route": f"/x/{csrf}", "event": session_id})
    log.warning("header Authorization: Bearer %s; Cookie: tabrion_studio_session_1234=%s", TOKEN, session_id)
    text = logs.everything()
    for secret in (TOKEN, launch_token, csrf, session_id):
        assert secret not in text


@pytest.mark.parametrize(
    "message",
    [
        f"postgresql://user:{CANARY}@db.internal/parts",
        f"postgresql+psycopg://user:{CANARY}@db.internal:5432/parts?sslmode=verify-full",
        f"host=db.internal password={CANARY} dbname=parts",
        f"host=db.internal password='{CANARY} with space' dbname=parts",
        f'{{"password": "{CANARY}"}}',
        f"secret: {CANARY}",
        f"api_key={CANARY}&x=1",
        f"Authorization: Bearer {CANARY}",
    ],
)
def test_credentials_in_messages_are_masked(logs: Logs, message: str) -> None:
    logging.getLogger("tabrion_studio.test").warning("connecting with %s", message)
    _assert_no_canary(logs.everything())


def test_registered_secret_is_redacted_in_every_field(logs: Logs) -> None:
    register_secret(CANARY)
    log = logging.getLogger("tabrion_studio.test")
    extra = {"event": CANARY, "route": f"/r/{CANARY}", "profile_id": CANARY, "query_id": CANARY, "code": CANARY}
    log.error("value %r", {"k": CANARY}, extra=extra, stack_info=True)
    try:
        raise ValueError(CANARY)
    except ValueError:
        log.exception("failed")
    _assert_no_canary(logs.everything())


def test_request_log_has_no_query_string_header_or_body(static_dir: Path, logs: Logs) -> None:
    """03 section 4.5: the request log names the route, never the query string (Uvicorn's access log is off)."""
    studio = make_studio(static_dir)
    headers = _signed_in(studio)
    studio.client.get(f"/api/heartbeat?csrf={CANARY}", headers={**headers, "X-Trace": CANARY})
    studio.client.get(f"/api/control/proof?nonce={CANARY}")
    studio.client.post(f"/api/control/{CANARY}", json={"secret": CANARY}, headers={"Authorization": f"Bearer {CANARY}"})
    studio.client.post(f"/api/control/{CANARY}", json={"secret": CANARY}, headers=CONTROL_AUTH)
    studio.client.get(f"/assets/{CANARY}.js")
    studio.client.get(f"/nowhere/{CANARY}?x={CANARY}", headers={"Host": "rebind.test"})
    _assert_no_canary(logs.everything())
    routes = {(record["route"], record["status"]) for record in logs.records() if record.get("event") == "http.request"}
    assert ("/api/heartbeat", 204) in routes
    assert ("/api/control/proof", 400) in routes
    # A wrong control token is refused by the access middleware before routing (STU-016 review F-8); with the token,
    # the unknown action reaches the route and is logged by its template.
    assert ("<unmatched>", 401) in routes
    assert ("/api/control/{action}", 404) in routes
    assert ("static", 404) in routes
    assert ("<unmatched>", 403) in routes


def test_refused_requests_log_the_code_not_the_path(static_dir: Path, logs: Logs) -> None:
    """The client chooses the path, so a refused or unmatched request is logged as <unmatched> with its code."""
    studio = make_studio(static_dir)
    studio.client.get("/a%0Ab%0D%1Bc" + "x" * 500, headers={"Host": "rebind.test"})
    record = [record for record in logs.records() if record.get("event") == "http.request"][-1]
    assert record["route"] == "<unmatched>"
    assert record["code"] == "access.host_not_allowed"
    assert record["level"] == "INFO"
    assert "xxxx" not in logs.everything()


def test_validation_errors_do_not_echo_the_input(static_dir: Path, logs: Logs) -> None:
    """FastAPI's default 422 answer repeats the offending input; Studio's names no value."""
    studio = make_studio(static_dir)

    async def typed(number: int) -> dict[str, int]:
        return {"number": number}

    add_route(studio, "/api/typed", typed)
    response = studio.client.get(f"/api/typed?number={CANARY}", headers=_signed_in(studio))
    assert response.status_code == 422
    assert response.json()["code"] == "studio.invalid_request"
    _assert_no_canary(response.text)
    _assert_no_canary(logs.everything())


# --- Review L-1 of stu-004-review.md: exception text that arrives in the message, not in exc_info --------------


def test_asyncio_reprs_never_reach_a_log_line(logs: Logs) -> None:
    """asyncio's default handler puts ``repr(task)`` (with ``exception=ValueError('...')``) into the message."""

    async def boom() -> None:
        raise ValueError(f"DETAIL:  Key (email)=({CANARY}) already exists.")

    def callback(value: str) -> None:
        raise RuntimeError(value)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        task = loop.create_task(boom())
        await asyncio.sleep(0.01)
        del task
        gc.collect()  # "Task exception was never retrieved", with the task's repr
        future = loop.create_future()
        future.set_exception(RuntimeError(f"password={CANARY}"))
        exc = future.exception()
        loop.call_exception_handler(
            {"message": "Future exception was never retrieved", "exception": exc, "future": future}
        )
        loop.call_soon(callback, CANARY)  # "Exception in callback callback('<canary>')"
        await asyncio.sleep(0.01)

    asyncio.run(main())
    gc.collect()
    _assert_no_canary(logs.everything())
    records = [record for record in logs.records() if record["logger"] == "asyncio"]
    messages = [record["message"] for record in records]
    assert "Task exception was never retrieved [details redacted]" in messages
    assert "Future exception was never retrieved [details redacted]" in messages
    assert "Exception in callback [details redacted]" in messages
    # The exception itself is still logged, frames only.
    task_record = records[messages.index("Task exception was never retrieved [details redacted]")]
    assert "in boom" in str(task_record["exception"])
    assert "ValueError: [message redacted]" in str(task_record["exception"])


def _serve_until_lifespan_fails(app: Any) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, log_level="info", lifespan="on")
    with contextlib.suppress(SystemExit):
        uvicorn.Server(config).run()


def test_a_failing_lifespan_of_any_app_logs_no_traceback_text(logs: Logs) -> None:
    """Starlette sends ``traceback.format_exc()`` as text and Uvicorn logs it as the message; the filter cuts it."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        raise RuntimeError(f"cannot connect postgresql://u:x@h DETAIL: Key (email)=({CANARY})")
        yield  # pragma: no cover

    _serve_until_lifespan_fails(FastAPI(lifespan=lifespan))
    _assert_no_canary(logs.everything())
    messages = [str(record["message"]) for record in logs.records() if record["logger"] == "uvicorn.error"]
    assert "[traceback text redacted]" in messages
    assert "Application startup failed. Exiting." in messages


def _failing_lifespan_app() -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        raise RuntimeError(f"cannot connect postgresql://u:x@h DETAIL: Key (email)=({CANARY})")
        yield  # pragma: no cover

    return FastAPI(lifespan=lifespan)


def test_uvicorns_own_logging_config_from_earlier_does_not_bypass_redaction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TD-055: another Uvicorn in the process configured its loggers (own stderr handler, no propagation) first."""
    uvicorn.Config(FastAPI())  # the default log_config: logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    console = io.StringIO()
    setup = configure_logging(tmp_path / "logs", "studio-test", level=logging.DEBUG, console=console)
    try:
        _serve_until_lifespan_fails(_failing_lifespan_app())
        logs = Logs(setup, console)
        _assert_no_canary(logs.everything() + capsys.readouterr().err)
        messages = [str(record["message"]) for record in logs.records() if record["logger"] == "uvicorn.error"]
        assert "[traceback text redacted]" in messages
    finally:
        setup.close()
        forget_secrets()


def test_uvicorns_own_logging_config_from_later_does_not_bypass_redaction(
    logs: Logs, capsys: pytest.CaptureFixture[str]
) -> None:
    """TD-055: Uvicorn's loggers configured again after Studio's: their own handler only sees redacted records."""
    uvicorn.Config(FastAPI())
    _serve_until_lifespan_fails(_failing_lifespan_app())
    err = capsys.readouterr().err
    _assert_no_canary(logs.everything() + err)
    assert "[traceback text redacted]" in err


def test_a_failing_studio_startup_is_logged_with_frames_only(
    static_dir: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Studio's lifespan logs the exception itself (frames only), so cutting Uvicorn's text loses nothing."""

    def fail(_self: object) -> bool:
        raise RuntimeError(f"DETAIL:  Key (email)=({CANARY}) already exists.")

    monkeypatch.setattr(IdleMonitor, "enabled", property(fail))
    _serve_until_lifespan_fails(make_studio(static_dir).client.app)
    _assert_no_canary(logs.everything())
    failures = [record for record in logs.records() if record.get("event") == "studio.lifespan_failed"]
    assert len(failures) == 1
    assert failures[0]["message"] == "application startup failed"
    assert "in fail" in str(failures[0]["exception"])
    assert "RuntimeError: [message redacted]" in str(failures[0]["exception"])
