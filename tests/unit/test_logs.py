"""STU-004: log redaction, formats, private files and rotation (03 section 4.5, T-04, T-05)."""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
import time

import pytest

from tabrion_studio.logs import (
    BACKUP_COUNT,
    FLOOD_BURST,
    FLOOD_WINDOW_S,
    MAX_BYTES,
    MAX_TRACEBACK,
    MESSAGE_REDACTED,
    OWNED_LOGGERS,
    REDACTED,
    FloodFilter,
    PrivateRotatingFileHandler,
    RedactionFilter,
    configure_logging,
    printable,
    safe_traceback,
    scrub,
)
from tabrion_studio.paths import default_log_dir

POSIX = sys.platform != "win32"
posix_only = pytest.mark.skipif(not POSIX, reason="POSIX file modes and symlinks")


@pytest.fixture
def console() -> Iterator[io.StringIO]:
    yield io.StringIO()


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_file_gets_json_lines_and_console_gets_warnings(tmp_path: Path, console: io.StringIO) -> None:
    setup = configure_logging(tmp_path / "logs", "studio-x", console=console)
    try:
        log = logging.getLogger("tabrion_studio.test")
        log.info("ready on %d", 4711, extra={"event": "studio.ready", "duration_ms": 1.5, "secret_field": "x"})
        log.debug("not at INFO")
        log.warning("two\nlines", extra={"event": "x\ny"})
    finally:
        setup.close()
    assert setup.file == tmp_path / "logs" / "studio-x.log"
    records = _records(setup.file)
    assert [r["message"] for r in records] == ["ready on 4711", "two\nlines"]
    first = records[0]
    assert set(first) == {"time", "level", "logger", "message", "event", "duration_ms"}  # secret_field dropped
    assert first["level"] == "INFO" and first["logger"] == "tabrion_studio.test" and str(first["time"]).endswith("Z")
    assert records[1]["event"] == "x\\x0ay"
    text = console.getvalue()
    assert "ready on" not in text  # INFO stays in the file
    assert text.count("\n") == 1 and "two\\x0alines" in text  # one line per record, no log injection
    assert setup.handlers == []  # close() removed them from the root logger


def test_the_fixture_guards_exactly_the_owned_loggers() -> None:
    # PR #78 review F-5: tests/conftest.py repeats the list (the packaging tests cannot import Studio).
    from tests.conftest import _GUARDED_LOGGERS

    assert _GUARDED_LOGGERS == OWNED_LOGGERS


def test_owned_loggers_are_taken_over_and_given_back(tmp_path: Path, console: io.StringIO) -> None:
    # TD-055: Uvicorn's own handler and propagate=False (its default log_config) would bypass the root handlers.
    uvicorn_logger = logging.getLogger("uvicorn")
    foreign = logging.StreamHandler(io.StringIO())
    uvicorn_logger.addHandler(foreign)
    uvicorn_logger.propagate = False
    setup = configure_logging(tmp_path / "logs", "studio-x", console=console)
    try:
        for name in OWNED_LOGGERS:
            logger = logging.getLogger(name)
            assert logger.handlers == [] and logger.propagate and not logger.disabled
            assert any(isinstance(item, RedactionFilter) for item in logger.filters)
        logging.getLogger("uvicorn.error").error("Traceback (most recent call last):\nRuntimeError: value-55")
    finally:
        setup.close()
    assert setup.file is not None
    assert "value-55" not in setup.file.read_text(encoding="utf-8")
    assert [r["message"] for r in _records(setup.file)] == ["[traceback text redacted]"]
    assert uvicorn_logger.handlers == [foreign] and uvicorn_logger.propagate is False
    assert all(not logging.getLogger(name).filters for name in OWNED_LOGGERS)


@posix_only
def test_log_directory_and_file_are_private(tmp_path: Path, console: io.StringIO) -> None:
    setup = configure_logging(tmp_path / "logs", "studio-x", console=console)
    try:
        logging.getLogger("tabrion_studio.test").warning("x")
    finally:
        setup.close()
    assert setup.file is not None
    assert stat.S_IMODE(os.stat(tmp_path / "logs").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(setup.file).st_mode) == 0o600


@posix_only
def test_an_existing_loose_log_file_is_tightened(tmp_path: Path, console: io.StringIO) -> None:
    folder = tmp_path / "logs"
    folder.mkdir(mode=0o700)
    loose = folder / "studio-x.log"
    loose.write_text("")
    loose.chmod(0o644)
    configure_logging(folder, "studio-x", console=console).close()
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o600


@posix_only
def test_a_symlinked_log_file_is_refused(tmp_path: Path, console: io.StringIO) -> None:
    """A symlink planted as the log file would make Studio write elsewhere; logging falls back to the console."""
    folder = tmp_path / "logs"
    folder.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    (folder / "studio-x.log").symlink_to(target)
    setup = configure_logging(folder, "studio-x", console=console)
    try:
        logging.getLogger("tabrion_studio.test").warning("after")
    finally:
        setup.close()
    assert setup.file is None
    assert target.read_text() == "keep"
    assert "logging to the console only" in console.getvalue()


@posix_only
def test_an_unsafe_log_directory_is_not_used(tmp_path: Path, console: io.StringIO) -> None:
    folder = tmp_path / "logs"
    folder.mkdir()
    folder.chmod(0o777)
    setup = configure_logging(folder, "studio-x", console=console)
    setup.close()
    assert setup.file is None
    assert list(folder.iterdir()) == []


def test_rotation_keeps_five_private_files(tmp_path: Path) -> None:
    assert (MAX_BYTES, BACKUP_COUNT) == (5 * 1024 * 1024, 4)  # 03 section 4.3: 5 x 5 MB
    handler = PrivateRotatingFileHandler(tmp_path / "s.log", maxBytes=200, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.addFilter(RedactionFilter())
    try:
        for index in range(100):
            handler.emit(logging.LogRecord("t", logging.INFO, __file__, 1, f"line {index:03d} " + "x" * 50, None, None))
    finally:
        handler.close()
    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == ["s.log", "s.log.1", "s.log.2", "s.log.3", "s.log.4"]
    if POSIX:
        assert {stat.S_IMODE(os.stat(tmp_path / name).st_mode) for name in files} == {0o600}
    assert "line 099" in (tmp_path / "s.log").read_text(encoding="utf-8")


def test_scrub_patterns() -> None:
    assert scrub("postgresql://u:pw@h/db") == "postgresql://u:***@h/db"
    assert scrub("sqlite:///data/parts.db") == "sqlite:///data/parts.db"
    assert scrub("https://example.org/a:b@c") == "https://example.org/a:b@c"  # no userinfo, nothing to mask
    assert scrub("password=hunter2 user=x") == f"password={REDACTED} user=x"
    assert scrub("PWD: 'a b'") == f"PWD: {REDACTED}"
    assert scrub("Bearer abc.def") == f"Bearer {REDACTED}"
    assert scrub("tabrion_studio_session_8765=abc; other=1") == f"tabrion_studio_session_8765={REDACTED}; other=1"
    assert scrub("the password is wrong") == "the password is wrong"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Review L-2 (stu-004-review.md, probe P-5): the key as a suffix of a longer name.
        ("db_password=value-1", f"db_password={REDACTED}"),
        ("PGPASSWORD=value-2 psql", f"PGPASSWORD={REDACTED} psql"),
        ("user_token=value-3", f"user_token={REDACTED}"),
        ("auth_secret: value-4", f"auth_secret: {REDACTED}"),
        ("sslpassword=value-5", f"sslpassword={REDACTED}"),
        ("privatekey=value-6", f"privatekey={REDACTED}"),
        ("x-tabrion-csrf: value-7", f"x-tabrion-csrf: {REDACTED}"),
        ("Authorization: Basic dmFsdWUtOA==", f"Authorization: Basic {REDACTED}"),
        ("{'authorization': 'Basic dmFsdWUtOA=='}", f"{{'authorization': 'Basic {REDACTED}'}}"),
        # A URL password up to the last @, also with @ or / in it.
        ("postgresql://u:p@ss-value-9@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u:ab/value-10@h:5432/db", "postgresql://u:***@h:5432/db"),
        ("'postgresql://u:pw@h/db' and x@y", "'postgresql://u:***@h/db' and x@y"),
        # Words that only contain a key are left alone.
        ("max_tokens: 5", "max_tokens: 5"),
        ("tokenizer=x", "tokenizer=x"),
    ],
)
def test_scrub_patterns_of_the_review(text: str, expected: str) -> None:
    assert scrub(text) == expected


@pytest.mark.parametrize("unit", ["y", "a:", "a://", "a://b:c", "-", "a_", "password", "password='", "x@"])
def test_scrub_takes_linear_time_on_hostile_text(unit: str) -> None:
    """Bounded repetitions: 200 KB of a repeated pattern is scrubbed quickly (the scrub runs before the caps)."""
    text = unit * (200_000 // len(unit))
    started = time.perf_counter()
    scrub(text)
    assert time.perf_counter() - started < 2.0


def test_a_deep_traceback_is_capped() -> None:
    """Review L-3: a RecursionError keeps its first and last frames only, and every traceback is capped."""

    value = "-".join(("value", "11"))  # a runtime value, not source text (the frames show source lines)

    # Two functions in turn: Python collapses only repeats of the same line, so these 500 frames are all printed.
    def ping(n: int) -> None:
        if n == 0:
            raise RecursionError(value)
        pong(n - 1)

    def pong(n: int) -> None:
        ping(n - 1)

    try:
        ping(500)
    except RecursionError as exc:
        text = safe_traceback(exc)
    assert "  ... 462 more frames ...\n" in text
    assert text.count("in ping") + text.count("in pong") == 39  # 40 frames, the first one is the test
    assert len(text) < 16 * 1024 and value not in text
    long = ValueError("x")
    chain: BaseException = long
    for index in range(20):  # a chain of 8 exceptions with 40 frames each still stays below the cap
        try:
            raise ValueError(str(index)) from chain
        except ValueError as exc:
            chain = exc
    assert len(safe_traceback(chain)) <= MAX_TRACEBACK + 64
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "x", None, None)
    record.exc_text = "y" * (MAX_TRACEBACK * 3)
    RedactionFilter().filter(record)
    assert record.exc_text is not None and len(record.exc_text) < MAX_TRACEBACK + 64


@pytest.mark.parametrize(
    ("name", "message", "expected"),
    [
        (
            "asyncio",
            "Task exception was never retrieved\nfuture: <Task finished exception=ValueError('value-12')>",
            "Task exception was never retrieved [details redacted]",
        ),
        ("asyncio", "Exception in callback f('value-13')\nhandle: <Handle f('value-13')>", None),
        ("asyncio", "something new with value-14", f"asyncio: {MESSAGE_REDACTED}"),
        ("asyncio", "Unhandled exception in event loop", "Unhandled exception in event loop"),
        ("uvicorn.error", "Traceback (most recent call last):\n  File x\nRuntimeError: value-15", None),
        ("tabrion_studio.x", "before\nTraceback (most recent call last):\nValueError: value-16", None),
    ],
)
def test_exception_text_in_the_message_is_removed(name: str, message: str, expected: str | None) -> None:
    """Review L-1: asyncio's reprs and formatted tracebacks in the message never reach the output."""
    record = logging.LogRecord(name, logging.ERROR, __file__, 1, message, None, None)
    RedactionFilter().filter(record)
    assert "value-1" not in record.msg
    if expected is not None:
        assert record.msg == expected
    if "Traceback" in message:
        assert record.msg.endswith("[traceback text redacted]")


def test_exception_arguments_are_formatted_as_their_type() -> None:
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed: %r %s", (ValueError("value-17"), 3), None)
    RedactionFilter().filter(record)
    assert record.msg == f"failed: ValueError: {MESSAGE_REDACTED} 3"


def test_safe_traceback_keeps_frames_drops_messages() -> None:
    first, second = "-".join(("value", "1")), "-".join(("value", "2"))  # runtime values, not source text

    def inner() -> None:
        raise KeyError(first)

    try:
        try:
            inner()
        except KeyError as exc:
            raise ValueError(second) from exc
    except ValueError as exc:
        text = safe_traceback(exc)
    assert "value-1" not in text and "value-2" not in text
    assert f"KeyError: {MESSAGE_REDACTED}" in text and f"ValueError: {MESSAGE_REDACTED}" in text
    assert text.index("KeyError") < text.index("ValueError")  # the cause first, as Python prints it
    assert "in inner" in text


def test_safe_traceback_of_a_group_and_a_cycle() -> None:
    group = ExceptionGroup("value-3", [ValueError("value-4"), TypeError("value-5")])
    text = safe_traceback(group)
    assert "value-" not in text and "(group of 2 exceptions)" in text
    first, second = ValueError("a"), ValueError("b")
    first.__context__, second.__context__ = second, first
    assert safe_traceback(first).count("ValueError") == 2  # a cycle ends


def test_redaction_survives_broken_records() -> None:
    class Hostile:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "value %s %s", (Hostile(),), None)
    assert RedactionFilter().filter(record)
    assert record.args is None and "value %s %s" in record.msg
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "x" * 10_000, None, None)
    RedactionFilter().filter(record)
    assert len(record.msg) < 4200
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "ok", None, None)
    record.__dict__.update(status=200, route=["not", "a", "string"], method=True)
    RedactionFilter().filter(record)
    fields = record.__dict__
    assert fields["status"] == 200 and fields["route"] == "['not', 'a', 'string']" and fields["method"] == "True"


def test_printable() -> None:
    assert printable("a\nb\tc\x1b\u0085\u202e\u2028") == "a\\x0ab\\x09c\\x1b\\x85\\u202e\\u2028"
    assert printable("x" * 300, 10).startswith("x" * 10 + "...")


def test_default_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TABRION_STUDIO_LOG_DIR", raising=False)
    assert default_log_dir(tmp_path) == tmp_path / "logs"
    assert "tabrion-studio" in str(default_log_dir())
    monkeypatch.setenv("TABRION_STUDIO_LOG_DIR", str(tmp_path / "x"))
    assert default_log_dir(tmp_path) == tmp_path / "x"


# --- A-02: a flood of Uvicorn's or asyncio's records cannot rotate the log away ----------------------------------


def test_a_flood_of_asyncio_records_is_cut_and_counted(tmp_path: Path, console: io.StringIO) -> None:
    setup = configure_logging(tmp_path / "logs", "studio-x", console=console)
    try:
        flood = logging.getLogger("asyncio")
        for _ in range(5000):
            try:
                raise OSError(24, "Too many open files")
            except OSError:
                flood.error("socket.accept() out of system resource\nsocket: <socket fd=7>", exc_info=True)
        for index in range(50):
            logging.getLogger("uvicorn.error").warning("Invalid HTTP request received.")
            logging.getLogger("tabrion_studio.test").warning("own record %d", index)  # Studio's own: not limited
    finally:
        setup.close()
    assert setup.file is not None
    records = _records(setup.file)
    assert sum(1 for r in records if r["logger"] == "asyncio") == FLOOD_BURST
    assert sum(1 for r in records if r["logger"] == "uvicorn.error") == FLOOD_BURST
    assert sum(1 for r in records if r["logger"] == "tabrion_studio.test") == 50
    assert setup.file.stat().st_size < 1024 * 1024


def test_the_flood_filter_reports_what_it_dropped_in_the_next_window() -> None:
    now = [0.0]
    limit = FloodFilter(clock=lambda: now[0])

    def record(message: str, name: str = "asyncio") -> logging.LogRecord:
        return logging.LogRecord(name, logging.ERROR, __file__, 1, message, None, None)

    passed = [limit.filter(record("Fatal error on transport\ndetails")) for _ in range(FLOOD_BURST + 30)]
    assert passed.count(True) == FLOOD_BURST
    assert limit.filter(record("Fatal error on transport\nother details")) is False  # the same first line
    assert limit.filter(record("Exception in callback")) is True  # another message has its own count
    assert limit.filter(record("Fatal error on transport", name="uvicorn.error")) is True  # and another logger
    now[0] += FLOOD_WINDOW_S
    after = record("Fatal error on transport\nlater")
    assert limit.filter(after) is True
    assert after.getMessage().endswith("[31 similar records dropped]")


def test_the_flood_filter_keeps_a_bounded_number_of_keys() -> None:
    limit = FloodFilter(clock=lambda: 0.0)
    for index in range(10_000):
        assert limit.filter(logging.LogRecord("asyncio", logging.ERROR, __file__, 1, f"m{index}", None, None))
    assert len(limit._seen) <= 256
