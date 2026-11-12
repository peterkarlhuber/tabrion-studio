"""Logging with redaction and rotation (03 section 4.5, threats T-04 and T-05, STU-004).

Studio logs with the standard :mod:`logging` module. :func:`configure_logging` installs two handlers on the root
logger, so that Studio's own loggers (``tabrion_studio.*``), Uvicorn's and asyncio's all pass the same redaction:

* a **file** handler: JSON lines, level INFO by default, in the log directory (0700) as
  ``<instance>.log`` (0600, symlinks refused), rotated at 5 MB with 4 older files kept (5 x 5 MB);
* a **console** handler on stderr: one text line per record, WARNING and above.

**Redaction (:class:`RedactionFilter`) runs on every record before any handler formats it:**

1. The message is formatted once and then scrubbed: every value registered with :func:`register_secret` (the
   control token, launch tokens, session IDs and CSRF values of this process) becomes ``[redacted]``; the password
   of a URL (``scheme://user:***@host``) and ``password=...``-style key values are masked; ``Bearer`` credentials
   and Studio session cookies are removed.
2. An exception is never formatted with its message. The traceback keeps the frames (file, line, function) of every
   exception in the chain, but each exception line shows only the type (``ValueError: [message redacted]``): a
   driver's message can hold ``DETAIL`` values, parameters or a connection URL with a password. Exception notes and
   local variables are never printed. Each exception keeps its first and last :data:`_EDGE_FRAMES` frames, and the
   whole traceback is capped at :data:`MAX_TRACEBACK` characters. (STU-009 adds ``tabrion.errors.safe_message`` for
   database errors.)
2a. **Exception text in the message itself** (review L-1 of ``stu-004-review.md``): asyncio's default exception
   handler writes ``repr(task)``/``repr(future)``, which contains ``exception=Type('message')``, and the repr of
   callbacks with their arguments into the message; so a record of the ``asyncio`` logger keeps only a known fixed
   first line (``Task exception was never retrieved``), never the rest. A formatted traceback passed as the message
   (Starlette's lifespan failure, which Uvicorn logs as text) is cut at ``Traceback (most recent call last):``; Studio's
   lifespan logs the exception itself with ``exc_info`` (frames only).
3. Only the fields of :data:`EXTRA_FIELDS` reach the output, scrubbed and length-capped; anything else that a caller
   passes in ``extra`` is dropped.

Studio never logs SQL parameter values, result rows, passwords or full connection URLs; SQL text is logged only at
DEBUG after the opt-in ``log_sql = true``, cut to 2 KB (STU-011). Uvicorn's access log is off; the request log of
:mod:`tabrion_studio.request_log` logs the route without the query string.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import stat
import sys
import threading
import traceback
from types import TracebackType
from typing import Any, TextIO

from tabrion.errors import DbErrorRedactionFilter

from tabrion_studio.paths import UnsafePathError, ensure_private_dir

REDACTED = "[redacted]"
MESSAGE_REDACTED = "[message redacted]"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 4
#: The fields a record may carry besides its message (``extra={...}``); everything else is dropped.
EXTRA_FIELDS = (
    "event",
    "code",
    "log_id",
    "method",
    "route",
    "status",
    "duration_ms",
    "profile_id",
    "query_id",
    "error_kind",
)
_MAX_FIELD = 256
_MAX_MESSAGE = 4096
_MAX_CHAIN = 8
#: Frames kept at each end of one exception's traceback; the frames in between are counted, not printed (L-3).
_EDGE_FRAMES = 20
#: The cap of one record's whole traceback text (L-3).
MAX_TRACEBACK = 32 * 1024
_MIN_SECRET = 8
_MAX_SECRETS = 512
#: Shorter secrets (I-1 of stu-007-review.md) are kept in a set that is never evicted. Below 4 characters a value is
#: not registered: replacing every "ab" or "1" in a log record would make the log useless, and such a password is no
#: protection anyway.
_MIN_SHORT_SECRET = 4
_MAX_SHORT_SECRETS = 256

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_POSIX = sys.platform != "win32"

#: ``scheme://user:password@host``: the password runs up to the *last* ``@`` of the address, so a password that
#: contains ``@`` or ``/`` is masked as a whole (review L-2). Every repetition is bounded and the scheme starts only
#: at a word start, so that a long hostile message is scrubbed in linear time.
_URL_PASSWORD = re.compile(
    r"(?P<head>(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://[^\s:/?#@'\"<>]{0,256}):[^\s'\"<>]{0,512}@"
)
#: A credential key, also at the end of a longer name (``db_password``, ``PGPASSWORD``, ``sslpassword``,
#: ``user_token``, ``auth_secret``, ``privatekey``), then ``=`` or ``:`` and the value (review L-2). Only the key word
#: itself is matched (no prefix), which keeps the search linear.
_KEY_VALUE = re.compile(
    r"(?i)(?P<key>(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|csrf)"
    r"(?![A-Za-z0-9])[\"']?\s{0,16}[=:]\s{0,16})"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s&;,'\"]+)"
)
_BEARER = re.compile(r"(?i)\b(?P<key>bearer\s+)[A-Za-z0-9._~+/=\-]+")
#: ``Authorization: Basic <base64>`` (and any other scheme): the scheme is kept, the credentials are removed.
_AUTHORIZATION = re.compile(
    r"(?i)\b(?P<key>(?:proxy-)?authorization[\"']?\s*[=:]\s*[\"']?(?:[A-Za-z][A-Za-z0-9_-]*\s+)?)[^\s'\",;]+"
)
_COOKIE = re.compile(r"(?P<key>tabrion_studio_session_\d*=)[^;\s,]+")
#: C0 and C1 controls (newlines and tabs included), line separators and bidi controls: escaped in every field.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


class _Secrets:
    """The secret values of this process; bounded, the oldest entries are dropped first."""

    def __init__(self) -> None:
        self._values: OrderedDict[str, None] = OrderedDict()
        self._short: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str) -> None:
        if not isinstance(value, str) or len(value) < _MIN_SHORT_SECRET:
            return
        if len(value) < _MIN_SECRET:
            with self._lock:
                if len(self._short) < _MAX_SHORT_SECRETS:
                    self._short.add(value)
            return
        with self._lock:
            self._values[value] = None
            self._values.move_to_end(value)
            while len(self._values) > _MAX_SECRETS:
                self._values.popitem(last=False)

    def snapshot(self) -> list[str]:
        with self._lock:
            # Longest first, so that a secret that contains another one is replaced as a whole.
            return sorted([*self._values, *self._short], key=len, reverse=True)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._short.clear()


_SECRETS = _Secrets()


def register_secret(value: str) -> None:
    """Redact ``value`` from every log record of this process from now on (values shorter than 4 are ignored)."""
    _SECRETS.add(value)


def forget_secrets() -> None:
    """Forget every registered secret (tests only)."""
    _SECRETS.clear()


def scrub(text: str) -> str:
    """``text`` with registered secrets, URL passwords, credential key values, bearer tokens and cookies removed."""
    for value in _SECRETS.snapshot():
        if value in text:
            text = text.replace(value, REDACTED)
    text = _URL_PASSWORD.sub(lambda m: f"{m['head']}:***@", text)
    text = _KEY_VALUE.sub(lambda m: f"{m['key']}{REDACTED}", text)
    text = _AUTHORIZATION.sub(lambda m: f"{m['key']}{REDACTED}", text)
    text = _BEARER.sub(lambda m: f"{m['key']}{REDACTED}", text)
    return _COOKIE.sub(lambda m: f"{m['key']}{REDACTED}", text)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}... [{len(text) - limit} more characters]"


def _escape(match: re.Match[str]) -> str:
    code = ord(match.group())
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def printable(text: str, limit: int = _MAX_FIELD) -> str:
    """``text`` with control characters escaped (no log injection through newlines) and capped at ``limit``."""
    return _cap(_CONTROL.sub(_escape, text), limit)


def _chain(exc: BaseException) -> list[BaseException]:
    """The exceptions of ``exc``'s chain, the first cause first (as Python prints them)."""
    found: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(found) < _MAX_CHAIN:
        seen.add(id(current))
        found.append(current)
        cause = current.__cause__
        current = cause if cause is not None else (None if current.__suppress_context__ else current.__context__)
    return list(reversed(found))


def _type_name(exc: BaseException) -> str:
    cls = type(exc)
    module = cls.__module__
    return cls.__qualname__ if module in ("builtins", "__main__") else f"{module}.{cls.__qualname__}"


def safe_traceback(exc: BaseException) -> str:
    """The traceback of ``exc`` and its chain with frames only: no exception message, note or local variable."""
    parts: list[str] = []
    for index, item in enumerate(_chain(exc)):
        if index:
            parts.append("\nThe above exception led to the following one:\n\n")
        if item.__traceback__ is not None:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(_frames(item.__traceback__))
        line = f"{_type_name(item)}: {MESSAGE_REDACTED}"
        if isinstance(item, BaseExceptionGroup):
            line += f" (group of {len(item.exceptions)} exceptions)"
        parts.append(line + "\n")
    return _cap(scrub("".join(parts)).rstrip("\n"), MAX_TRACEBACK)


def _frames(tb: TracebackType) -> list[str]:
    """The formatted frames of ``tb``: the first and last :data:`_EDGE_FRAMES`, the rest only counted (L-3)."""
    summary = traceback.extract_tb(tb)
    if len(summary) <= 2 * _EDGE_FRAMES:
        return summary.format()
    head = traceback.StackSummary.from_list(summary[:_EDGE_FRAMES]).format()
    tail = traceback.StackSummary.from_list(summary[-_EDGE_FRAMES:]).format()
    return [*head, f"  ... {len(summary) - 2 * _EDGE_FRAMES} more frames ...\n", *tail]


#: The first lines of asyncio's exception handler contexts that are fixed texts; anything else is replaced (L-1).
_ASYNCIO_MESSAGES = (
    "Task exception was never retrieved",
    "Future exception was never retrieved",
    "Task was destroyed but it is pending!",
    "Unhandled exception in event loop",
    "Unhandled error in exception handler",
    "Exception in callback",
    "Exception in default exception handler",
    "Fatal error on transport",
    "Fatal error on SSL transport",
    "Fatal read error on socket transport",
    "Fatal write error on socket transport",
    "Fatal error: protocol.data_received() call failed.",
    "Fatal error: protocol.eof_received() call failed.",
    "Fatal error: protocol.buffer_updated() call failed.",
    "Fatal error: protocol.get_buffer() call failed.",
    "Unhandled exception during asyncio.run() shutdown",
    "unhandled exception during asyncio.run() shutdown",
)
_TRACEBACK_TEXT = "Traceback (most recent call last):"


def _asyncio_message(message: str) -> str:
    """asyncio's message without the reprs of tasks, futures, callbacks and their arguments (review L-1)."""
    first = message.split("\n", 1)[0]
    for known in _ASYNCIO_MESSAGES:
        if first.startswith(known):
            return f"{known} [details redacted]" if first != known or "\n" in message else known
    return f"asyncio: {MESSAGE_REDACTED}"


def _without_traceback_text(message: str) -> str:
    """A message cut where a formatted traceback starts (its last line is the exception's message; review L-1)."""
    index = message.find(_TRACEBACK_TEXT)
    if index < 0:
        return message
    head = message[:index].rstrip()
    return f"{head} [traceback text redacted]" if head else "[traceback text redacted]"


class _ExceptionType:
    """Stands in for an exception passed as a ``%s``/``%r`` argument: formats as its type only."""

    def __init__(self, exc: BaseException) -> None:
        self._text = f"{_type_name(exc)}: {MESSAGE_REDACTED}"

    def __repr__(self) -> str:
        return self._text

    __str__ = __repr__


def _safe_arg(value: object) -> object:
    return _ExceptionType(value) if isinstance(value, BaseException) else value


def _safe_args(args: Any) -> Any:
    """``%s``/``%r`` arguments with every exception replaced by its type: its text is never formatted (L-1)."""
    if isinstance(args, tuple):
        return tuple(_safe_arg(value) for value in args)
    if isinstance(args, dict):
        return {key: _safe_arg(value) for key, value in args.items()}
    return args


class RedactionFilter(logging.Filter):
    """Scrubs every record in place (message, exception, stack, extra fields); never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_tabrion_redacted", False):
            return True
        record.args = _safe_args(record.args)
        try:
            message = record.getMessage()
        except Exception:  # a broken format string or a failing __str__ of an argument
            message = f"{record.msg!s} [arguments not shown]" if isinstance(record.msg, str) else "[message not shown]"
        if record.name == "asyncio" or record.name.startswith("asyncio."):
            message = _asyncio_message(message)
        record.msg = _cap(scrub(_without_traceback_text(message)), _MAX_MESSAGE)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = safe_traceback(record.exc_info[1])
            record.studio_safe_traceback = True
        elif record.exc_text:
            record.exc_text = _cap(scrub(record.exc_text), MAX_TRACEBACK)
        record.exc_info = None
        if record.stack_info:
            record.stack_info = _cap(scrub(record.stack_info), MAX_TRACEBACK)
        for name in EXTRA_FIELDS:
            value = record.__dict__.get(name)
            if value is None or (isinstance(value, int | float) and not isinstance(value, bool)):
                continue
            record.__dict__[name] = printable(scrub(value if isinstance(value, str) else str(value)))
        record._tabrion_redacted = True
        return True


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {name: record.__dict__[name] for name in EXTRA_FIELDS if record.__dict__.get(name) is not None}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, the allowed extra fields and the safe traceback."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "time": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_text:
            doc["exception"] = record.exc_text
        if record.stack_info:
            doc["stack"] = record.stack_info
        return json.dumps(doc, ensure_ascii=True, default=str)


class TextFormatter(logging.Formatter):
    """``time level logger: message key=value ...`` with control characters escaped, then the safe traceback."""

    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(f"{key}={value}" for key, value in _extras(record).items())
        line = f"{_timestamp(record)} {record.levelname} {record.name}: {printable(record.getMessage(), _MAX_MESSAGE)}"
        if fields:
            line = f"{line} [{fields}]"
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        return line


class PrivateRotatingFileHandler(RotatingFileHandler):
    """A rotating file handler whose files are created 0600, never through a symlink, and only ever owned by us."""

    def _open(self) -> io.TextIOWrapper:
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW | _BINARY, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafePathError(f"the log file is not a regular file: {self.baseFilename}")
            if _POSIX:
                if info.st_uid != os.getuid():
                    raise UnsafePathError(f"the log file belongs to another user: {self.baseFilename}")
                if stat.S_IMODE(info.st_mode) & 0o077:
                    os.fchmod(fd, 0o600)
            return open(fd, "a", encoding="utf-8", errors="backslashreplace")
        except BaseException:
            os.close(fd)
            raise


@dataclass
class LogSetup:
    """The handlers :func:`configure_logging` installed; :meth:`close` removes them again."""

    file: Path | None
    handlers: list[logging.Handler] = field(default_factory=list)
    previous_level: int = logging.WARNING

    def close(self) -> None:
        root = logging.getLogger()
        for handler in self.handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(self.previous_level)
        self.handlers.clear()


class _DbMessageFilter(DbErrorRedactionFilter):
    """tabrion's database-error filter for arguments and messages, after :class:`RedactionFilter` (TS-001).

    Where :class:`RedactionFilter` replaced the exception with its types and frames (no message at all), that text is
    kept as it is: tabrion's filter would cut it at the first source line that names ``DETAIL``. A traceback that
    arrived as text (``exc_text``) goes through tabrion's filter as well.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        safe_traceback_text = record.exc_text if getattr(record, "studio_safe_traceback", False) else None
        super().filter(record)
        if safe_traceback_text is not None:
            record.exc_text = safe_traceback_text
        return True


def configure_logging(
    log_dir: Path | None,
    name: str,
    *,
    level: int = logging.INFO,
    console: TextIO | None = None,
    console_level: int = logging.WARNING,
) -> LogSetup:
    """Install the file handler (``log_dir/<name>.log``, if ``log_dir`` is given) and the console handler.

    An unusable log directory is not fatal: Studio then logs to the console only and says so there.
    """
    root = logging.getLogger()
    setup = LogSetup(file=None, previous_level=root.level)
    redaction = RedactionFilter()
    # Studio's filter first: it replaces an exception with its types and frames only (no message at all), stricter
    # than tabrion's safe messages. tabrion's then rewrites database errors in the arguments and strips DETAIL,
    # HINT, CONTEXT and parameter parts of the message (TS-001, STU-009). The other order leaves exception messages
    # in exc_text (test_canary).
    db_redaction = _DbMessageFilter()
    stream = sys.stderr if console is None else console
    console_handler = logging.StreamHandler(stream)
    console_handler.setLevel(console_level)
    console_handler.addFilter(redaction)
    console_handler.addFilter(db_redaction)
    console_handler.setFormatter(TextFormatter())
    handlers: list[logging.Handler] = [console_handler]
    failure: str | None = None
    if log_dir is not None:
        try:
            ensure_private_dir(log_dir)
            path = log_dir / f"{name}.log"
            file_handler = PrivateRotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=False
            )
        except (OSError, UnsafePathError) as exc:
            failure = f"{type(exc).__name__}: {scrub(str(exc))}"
        else:
            file_handler.setLevel(level)
            file_handler.addFilter(redaction)
            file_handler.addFilter(db_redaction)
            file_handler.setFormatter(JsonFormatter())
            handlers.append(file_handler)
            setup.file = path
    for handler in handlers:
        root.addHandler(handler)
    setup.handlers = handlers
    root.setLevel(min(level, console_level))
    if failure is not None:
        logging.getLogger("tabrion_studio.logs").warning(
            "cannot write the log file in %s; logging to the console only (%s)",
            log_dir,
            failure,
            extra={"event": "studio.log_unavailable"},
        )
    return setup
