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

import pytest

from tabrion_studio.logs import (
    BACKUP_COUNT,
    MAX_BYTES,
    MESSAGE_REDACTED,
    REDACTED,
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
