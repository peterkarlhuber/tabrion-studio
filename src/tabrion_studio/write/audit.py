"""The local audit log of write and admin actions (STU-XW-2; STU-ADR-0006, threats TX-12, TX-13, TX-14; review N-4).

Part of the write package: like the rest of it, this module is not imported while every write area is off (TX-02),
and no read path imports it (import-linter contract ``read-paths-never-write``).

**What is recorded.** One record per action, in the order Studio did them: an unlock (and a refused one), a lock (with
its reason: the lock route, the expiry, a closed connection, a profile change, the server stopping), creating,
changing and removing a write or admin profile (its coupling to a read profile included), storing and removing its
secret, and every write run: ``run.start`` before the transaction begins and ``run.result`` after it committed or
rolled back, with the row counts. A record has a fixed set of fields (:data:`FIELDS`): times, profile names, the kind,
the environment, the engine, the connection ID, the area, the action, object names, row counts, the plan hash and a
result code. **Never a value**: no cell value, no parameter, no statement text, no SQL literal, no secret, no database
message (TX-13, TX-14). Each field has a validator; an unknown field or one of the wrong shape is a coding error and
refuses the record, and a text that contains a secret Studio knows (:func:`tabrion_studio.logs.scrub`) is replaced.

**Where.** ``<state folder>/audit/``: the folder 0700, every file 0600, owned by the user, never opened through a link
(``O_NOFOLLOW``; on Windows the state folder checks of :mod:`tabrion_studio.paths`). No file stays open between two
records, so nothing holds a handle on the log (Windows). Files:

* ``audit.jsonl``: the current segment, one JSON object per line, appended only (``O_APPEND``, ``fsync``);
* ``audit-<first sequence number, 12 digits>.jsonl``: older segments. Studio rotates at :data:`MAX_SEGMENT_BYTES` by
  renaming the current segment to the name of its first record; it never overwrites an existing name (a file
  planted there stops the rotation and the log, fail closed) and never deletes a segment (A-02, TX-12);
* ``head.json``: the sequence number and hash of the last record, rewritten atomically after each record (the
  anchor that makes truncation visible);
* ``pending.json``: the pending marker of review N-4 (see below), only while there is one.

**Tamper evidence.** Every record carries its sequence number, the hash of the record before it (``prev``; the first
record has :data:`GENESIS`) and its own hash: SHA-256 over its canonical JSON without ``hash``. :func:`verify_log`
recomputes the chain over every segment and checks the head, so it finds a changed record (``audit.record_changed``),
a removed, inserted or reordered one (``audit.chain_broken``), a cut tail (``audit.truncated``) and a missing or
changed anchor (``audit.head_missing``, ``audit.head_mismatch``). Without a key, someone who can write the user's
state folder can rebuild a whole consistent chain; that is malware running as the user, outside the protection goal
(threat model, residual risk 3). Studio verifies the log when it opens it and on every listing; a log that fails is
not appended to, so no write runs until the user has moved the folder aside (it is kept for inspection, never
repaired by Studio).

**Failure behavior.** :meth:`AuditLog.append` fails closed: it raises :class:`AuditError` and the caller does not do
the action. It is used for the records before an action (``run.start``, ``unlock``). A record after an action that
already happened (``run.result``, ``lock``, a saved profile) uses :meth:`AuditLog.record`: if it cannot be written,
Studio keeps it as a **pending marker** (in memory and, if the folder takes it, in ``pending.json``), logs a warning
and reports it (``pending`` in :meth:`AuditLog.status`, ``audit_pending`` in the answers), never a silent loss
(review N-4). The next record writes the pending ones first (``late: true``, with the time they happened), and a
start record is refused while they cannot be written, so writes stop until the log is writable again.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import threading
from typing import Any

from tabrion_studio.logs import REDACTED, printable, scrub
from tabrion_studio.paths import UnsafePathError, ensure_private_dir, read_private_file, remove_file, write_private_file

__all__ = [
    "EVENTS",
    "FIELDS",
    "GENESIS",
    "AuditError",
    "AuditLog",
    "Verification",
    "clean_fields",
    "verify_log",
]

logger = logging.getLogger("tabrion_studio.write.audit")

VERSION = 1
CURRENT = "audit.jsonl"
HEAD = "head.json"
PENDING = "pending.json"
SEGMENT = re.compile(r"audit-(\d{12})\.jsonl")
#: The current segment is rotated before it grows beyond this.
MAX_SEGMENT_BYTES = 4 * 1024 * 1024
#: The ``prev`` of the first record.
GENESIS = "0" * 64
#: At most this many pending records are kept; beyond, the oldest one is replaced by a count (still never silent).
MAX_PENDING = 1000
#: The records a listing returns at most.
MAX_LISTING = 1000

#: The events of the log. A later work package adds its own here (STU-XW-3, STU-026 use ``run.start``/``run.result``).
EVENTS = frozenset(
    {
        "unlock",
        "lock",
        "profile.create",
        "profile.change",
        "profile.delete",
        "secret.store",
        "secret.remove",
        "run.start",
        "run.result",
        "audit.pending_dropped",
    }
)

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_POSIX = sys.platform != "win32"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_HEX = re.compile(r"[0-9a-f]{64}")
_PLAN_HASH = re.compile(r"[0-9a-f]{16,128}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
_MAX_TEXT = 200
_MAX_ITEMS = 64
_KEYS = frozenset({"v", "seq", "time", "event", "fields", "prev", "hash"})


class AuditError(Exception):
    """The audit log cannot take a record; ``code`` is the problem code the caller answers with."""

    def __init__(self, code: str = "studio.audit_unavailable") -> None:
        super().__init__(code)
        self.code = code


# --- the fields of a record ----------------------------------------------------------------------------------------


def _token(value: object) -> str:
    if isinstance(value, str) and _TOKEN.fullmatch(value):
        return value
    raise ValueError("not a token")


def _text(value: object) -> str:
    """A name (of a profile or a database object): control characters escaped, capped; a known secret replaced."""
    if not isinstance(value, str) or not value:
        raise ValueError("not a text")
    text = printable(value, _MAX_TEXT)
    return REDACTED if scrub(text) != text else text


def _count(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    raise ValueError("not a count")


def _flag(value: object) -> bool:
    if type(value) is bool:
        return value
    raise ValueError("not a flag")


def _plan_hash(value: object) -> str:
    if isinstance(value, str) and _PLAN_HASH.fullmatch(value):
        return value
    raise ValueError("not a plan hash")


def _time(value: object) -> str:
    if isinstance(value, str) and _TIME.fullmatch(value):
        return value
    raise ValueError("not a time")


def _texts(value: object) -> list[str]:
    if not isinstance(value, list | tuple) or len(value) > _MAX_ITEMS:
        raise ValueError("not a list of names")
    return [_text(item) for item in value]


def _tokens(value: object) -> list[str]:
    if not isinstance(value, list | tuple) or len(value) > _MAX_ITEMS:
        raise ValueError("not a list of tokens")
    return [_token(item) for item in value]


def _rows(value: object) -> dict[str, int]:
    """Row counts by kind (``{"updated": 3}``): counts only, never the rows."""
    if not isinstance(value, Mapping) or len(value) > _MAX_ITEMS:
        raise ValueError("not row counts")
    return {_token(key): _count(count) for key, count in value.items()}


#: Every field a record may carry and its validator (TX-13: names, counts, codes and hashes; never a value).
FIELDS: dict[str, Callable[[object], object]] = {
    "profile": _text,  # the write or admin profile's name
    "kind": _token,  # write or admin
    "read_profile": _text,  # the read profile it is coupled to (the coupling)
    "environment": _token,
    "engine": _token,
    "connection": _token,  # the read connection's ID in the registry
    "area": _token,
    "action": _token,  # what a run does (STU-XW-3, STU-026: their action names)
    "run": _token,  # the run's ID, shared by its start and result records
    "objects": _texts,  # the names of the objects a run changes
    "plan_hash": _plan_hash,  # STU-XW-3: the hash of the confirmed plan
    "rows": _rows,
    "result": _token,  # ok, committed, failed or a problem code
    "reason": _token,  # why a lock happened
    "changed": _tokens,  # the keys of a write profile that changed
    "store": _token,  # where a secret went: keyring or session
    "findings": _count,  # the number of privilege findings at an unlock (TX-11)
    "late": _flag,  # written from a pending marker (N-4)
    "occurred": _time,  # when a late record happened
    "dropped": _count,  # pending records that did not fit (MAX_PENDING)
}


def clean_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """The fields of a record, validated (:data:`FIELDS`); ``None`` values are left out. Raises ValueError."""
    out: dict[str, object] = {}
    for key, value in fields.items():
        check = FIELDS.get(key)
        if check is None:
            raise ValueError("unknown audit field")
        if value is not None:
            out[key] = check(value)
    return out


# --- the chain -----------------------------------------------------------------------------------------------------


def _canonical(doc: Mapping[str, object]) -> bytes:
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _digest(body: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(body)).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Verification:
    """The result of :func:`verify_log`: ``ok`` and the number of records, or the first problem and where."""

    ok: bool
    records: int
    problem: str | None = None
    at: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "records": self.records, "problem": self.problem, "at": self.at}


@dataclass
class _Scan:
    verification: Verification
    last_seq: int = 0
    last_hash: str = GENESIS
    current_first: int | None = None
    current_size: int = 0
    tail: deque[dict[str, Any]] = field(default_factory=deque)


class _Broken(Exception):
    def __init__(self, problem: str, at: int | None = None) -> None:
        super().__init__(problem)
        self.problem = problem
        self.at = at


def _segments(folder: Path) -> list[tuple[int | None, Path]]:
    """The segments in order (``None``: the current one, last)."""
    found: list[tuple[int | None, Path]] = []
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return []
    for name in names:
        match = SEGMENT.fullmatch(name)
        if match is not None:
            found.append((int(match.group(1)), folder / name))
        elif name.startswith("audit") and name.endswith(".jsonl") and name != CURRENT:
            raise _Broken("audit.unexpected_file")
    found.sort(key=lambda item: item[0] or 0)
    if (folder / CURRENT).exists() or (folder / CURRENT).is_symlink():
        found.append((None, folder / CURRENT))
    return found


def _read(path: Path) -> bytes:
    try:
        data = read_private_file(path)
    except (UnsafePathError, OSError):
        raise _Broken("audit.unsafe_file") from None
    if data is None:
        raise _Broken("audit.chain_broken")
    return data


def _record(line: bytes, expected_seq: int, prev: str) -> dict[str, Any]:
    try:
        doc = json.loads(line)
    except (ValueError, RecursionError):
        raise _Broken("audit.unreadable", expected_seq) from None
    if not isinstance(doc, dict) or set(doc) != _KEYS or doc.get("v") != VERSION:
        raise _Broken("audit.unreadable", expected_seq)
    body = {key: value for key, value in doc.items() if key != "hash"}
    if not isinstance(doc["hash"], str) or _digest(body) != doc["hash"]:
        raise _Broken("audit.record_changed", doc["seq"] if type(doc["seq"]) is int else expected_seq)
    if doc["seq"] != expected_seq or doc["prev"] != prev:
        raise _Broken("audit.chain_broken", expected_seq)
    if doc["event"] not in EVENTS or not isinstance(doc["fields"], dict) or not isinstance(doc["time"], str):
        raise _Broken("audit.unreadable", expected_seq)
    return doc


def _head(folder: Path) -> tuple[int, str] | None:
    try:
        data = read_private_file(folder / HEAD)
    except (UnsafePathError, OSError):
        raise _Broken("audit.unsafe_file") from None
    if data is None:
        return None
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError):
        raise _Broken("audit.head_mismatch") from None
    if (
        not isinstance(doc, dict)
        or type(doc.get("seq")) is not int
        or doc["seq"] < 0
        or not isinstance(doc.get("hash"), str)
        or not _HEX.fullmatch(doc["hash"])
    ):
        raise _Broken("audit.head_mismatch")
    return doc["seq"], doc["hash"]


def _scan(folder: Path, keep: int = 0) -> _Scan:
    """Read and verify every segment and the head; the last ``keep`` records are kept for a listing."""
    scan = _Scan(Verification(ok=True, records=0), tail=deque(maxlen=keep))
    hashes_at: dict[int, str] = {}
    seq = 0
    try:
        head = _head(folder)
        seq, prev = 0, GENESIS
        for first, path in _segments(folder):
            data = _read(path)
            lines = data.split(b"\n")
            partial = lines.pop()  # empty when the file ends with a whole line
            if first is not None and (not lines or first != seq + 1):
                raise _Broken("audit.chain_broken", seq + 1)
            if first is None:
                scan.current_first = seq + 1 if lines else None
                scan.current_size = len(data)
            for line in lines:
                doc = _record(line, seq + 1, prev)
                seq, prev = doc["seq"], doc["hash"]
                if head is not None and seq == head[0]:
                    hashes_at[seq] = prev
                if keep:
                    scan.tail.append({k: doc[k] for k in ("seq", "time", "event", "fields")})
            if partial:
                raise _Broken("audit.unreadable", seq + 1)
        if head is None:
            if seq > 0:
                raise _Broken("audit.head_missing")
        elif head[0] > seq:
            raise _Broken("audit.truncated", seq)
        elif head[0] == 0:
            if seq > 1:
                raise _Broken("audit.head_mismatch")
        elif hashes_at.get(head[0]) != head[1] or seq - head[0] > 1:
            # The head lags by at most one record (a stop between a record and its head); anything else is a change.
            raise _Broken("audit.head_mismatch", head[0])
    except _Broken as broken:
        scan.verification = Verification(ok=False, records=seq, problem=broken.problem, at=broken.at)
        return scan
    except OSError:
        scan.verification = Verification(ok=False, records=seq, problem="audit.unreadable")
        return scan
    scan.verification = Verification(ok=True, records=seq)
    scan.last_seq, scan.last_hash = seq, prev
    return scan


def verify_log(folder: Path) -> Verification:
    """Verify the audit log in ``folder``: the hash chain over every segment, the sequence and the head (TX-12)."""
    return _scan(folder).verification


# --- writing -------------------------------------------------------------------------------------------------------


def _append_bytes(path: Path, data: bytes) -> None:
    """Append ``data`` to ``path`` (created 0600), never through a link, and flush it to disk; closed afterwards."""
    if not _NOFOLLOW and path.is_symlink():
        raise UnsafePathError("refusing to follow a link")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW | _BINARY, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePathError("not a regular file")
        if _POSIX and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise UnsafePathError("the audit file is not private")
        size = info.st_size
        view = memoryview(data)
        try:
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        except OSError:
            # A partial line would break the chain: cut the file back to where it was.
            with contextlib.suppress(OSError):
                os.ftruncate(fd, size)
            raise
    finally:
        os.close(fd)


class AuditLog:
    """The audit log of this Studio process; thread-safe. ``folder`` None: no log, every record is refused."""

    def __init__(self, folder: Path | None, clock: Callable[[], str] = _now) -> None:
        self.folder = folder
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []
        self._dropped = 0
        self._problem: str | None = "audit.unavailable"
        self._seq = 0
        self._hash = GENESIS
        self._current_first: int | None = None
        self._size = 0
        with self._lock:
            self._open()

    # --- state -----------------------------------------------------------------------------------------------------

    def _open(self) -> None:
        if self.folder is None:
            logger.warning("no audit log folder; writes stay refused", extra={"event": "studio.audit_unavailable"})
            return
        try:
            ensure_private_dir(self.folder)
        except (UnsafePathError, OSError):
            logger.warning(
                "audit log folder not usable; writes stay refused", extra={"event": "studio.audit_unavailable"}
            )
            return
        self._adopt(_scan(self.folder))
        self._load_pending()
        if self._problem is None and self._pending:
            with contextlib.suppress(AuditError):
                self._flush_pending()

    def _adopt(self, scan: _Scan) -> None:
        if not scan.verification.ok:
            self._problem = scan.verification.problem or "audit.unreadable"
            logger.warning(
                "audit log failed verification; writes stay refused",
                extra={"event": "studio.audit_broken", "code": self._problem},
            )
            return
        self._problem = None
        self._seq, self._hash = scan.last_seq, scan.last_hash
        self._current_first, self._size = scan.current_first, scan.current_size

    def _load_pending(self) -> None:
        if self.folder is None:
            return
        try:
            data = read_private_file(self.folder / PENDING)
            if data is None:
                return
            doc = json.loads(data)
            items = doc.get("pending") if isinstance(doc, dict) else None
            if not isinstance(items, list) or len(items) > MAX_PENDING:
                raise ValueError("not a pending marker")
            pending = []
            for item in items:
                if not isinstance(item, dict) or item.get("event") not in EVENTS:
                    raise ValueError("not a pending record")
                pending.append(
                    {"event": item["event"], "time": _time(item.get("time")), "fields": clean_fields(item["fields"])}
                )
            dropped = _count(doc.get("dropped", 0))
        except (UnsafePathError, OSError, ValueError, TypeError, AttributeError, RecursionError):
            self._problem = "audit.pending_unreadable"
            logger.warning(
                "audit pending marker not readable; writes stay refused",
                extra={"event": "studio.audit_broken", "code": self._problem},
            )
            return
        self._pending = pending + self._pending
        self._dropped += dropped
        logger.warning("audit records are pending from an earlier run", extra={"event": "studio.audit_pending"})

    @property
    def available(self) -> bool:
        return self._problem is None

    def check(self) -> None:
        """Raise :class:`AuditError` if the log cannot take records now (missing, unsafe or failed verification)."""
        if self._problem is not None:
            raise AuditError()

    def status(self) -> dict[str, Any]:
        """For the page: whether records can be written, and how many are pending (N-4)."""
        return {"available": self._problem is None, "problem": self._problem, "pending": self.pending_count}

    @property
    def pending_count(self) -> int:
        return len(self._pending) + self._dropped

    # --- records ---------------------------------------------------------------------------------------------------

    def append(self, event: str, **fields: object) -> int:
        """Write one record now, or raise :class:`AuditError` (fail closed: the caller does not do the action).

        Pending records are written first. Returns the record's sequence number. A ValueError (an unknown event or
        field, a field of the wrong shape) is a coding error and raised as it is."""
        if event not in EVENTS:
            raise ValueError("unknown audit event")
        clean = clean_fields(fields)
        with self._lock:
            self.check()
            self._flush_pending()
            return self._write(event, clean)

    def record(self, event: str, **fields: object) -> bool:
        """Write a record of an action that has happened; on failure keep a pending marker (N-4). False if pending."""
        if event not in EVENTS:
            raise ValueError("unknown audit event")
        clean = clean_fields(fields)
        time = self.clock()
        with self._lock:
            try:
                self.check()
                self._flush_pending()
                self._write(event, clean)
            except AuditError:
                self._keep_pending({"event": event, "time": time, "fields": clean})
                return False
        return True

    def _keep_pending(self, item: dict[str, Any]) -> None:
        self._pending.append(item)
        if len(self._pending) > MAX_PENDING:
            self._pending.pop(0)
            self._dropped += 1
        logger.warning("audit record could not be written; kept as pending", extra={"event": "studio.audit_pending"})
        self._save_pending()

    def _save_pending(self) -> None:
        if self.folder is None:
            return
        try:
            if self._pending or self._dropped:
                doc = {"v": VERSION, "pending": self._pending, "dropped": self._dropped}
                write_private_file(self.folder / PENDING, _canonical(doc))
            else:
                remove_file(self.folder / PENDING)
        except OSError:
            logger.warning(
                "audit pending marker could not be saved; it is kept in memory only",
                extra={"event": "studio.audit_pending"},
            )

    def _flush_pending(self) -> None:
        if not self._pending and not self._dropped:
            return
        try:
            while self._pending:
                item = self._pending[0]
                self._write(item["event"], {**item["fields"], "late": True, "occurred": item["time"]})
                self._pending.pop(0)
            if self._dropped:
                self._write("audit.pending_dropped", {"dropped": self._dropped, "late": True})
                self._dropped = 0
        finally:
            self._save_pending()

    def _write(self, event: str, fields: dict[str, object]) -> int:
        """Append one record and move the head (lock held). Raises :class:`AuditError`."""
        self.check()
        if self.folder is None:
            raise AuditError()
        body = {
            "v": VERSION,
            "seq": self._seq + 1,
            "time": self.clock(),
            "event": event,
            "fields": fields,
            "prev": self._hash,
        }
        digest = _digest(body)
        line = _canonical({**body, "hash": digest}) + b"\n"
        try:
            if self._size > 0 and self._size + len(line) > MAX_SEGMENT_BYTES:
                self._rotate()
            _append_bytes(self.folder / CURRENT, line)
        except (UnsafePathError, OSError) as exc:
            logger.warning(
                "audit record not written", extra={"event": "studio.audit_write_failed", "code": type(exc).__name__}
            )
            raise AuditError() from None
        if self._current_first is None:
            self._current_first = body["seq"]  # type: ignore[assignment]
        self._seq, self._hash, self._size = self._seq + 1, digest, self._size + len(line)
        try:
            write_private_file(self.folder / HEAD, _canonical({"v": VERSION, "seq": self._seq, "hash": self._hash}))
        except OSError:
            # The record is written and the chain is whole; the head lags by one, which verification accepts once.
            # Nothing more is written in this process (fail closed), so the lag never grows.
            self._problem = "audit.head_unwritable"
            logger.warning("audit head not written; writes stay refused", extra={"event": "studio.audit_write_failed"})
        return self._seq

    def _rotate(self) -> None:
        """Rename the current segment after its first record; never over an existing name (A-02, TX-12)."""
        if self.folder is None or self._current_first is None:
            raise UnsafePathError("nothing to rotate")
        target = self.folder / f"audit-{self._current_first:012d}.jsonl"
        if target.exists() or target.is_symlink():
            self._problem = "audit.unexpected_file"
            raise UnsafePathError("a segment with this name exists")
        os.rename(self.folder / CURRENT, target)
        self._current_first, self._size = None, 0

    # --- reading ---------------------------------------------------------------------------------------------------

    def listing(self, limit: int = 200) -> dict[str, Any]:
        """The newest ``limit`` records and a fresh verification of the whole log; a failure stops further records."""
        limit = max(1, min(limit, MAX_LISTING))
        with self._lock:
            if self.folder is None:
                return {**self.status(), "verification": None, "records": []}
            scan = _scan(self.folder, keep=limit)
            if not scan.verification.ok and self._problem is None:
                self._adopt(scan)
            return {
                **self.status(),
                "verification": scan.verification.to_dict(),
                "records": list(reversed(scan.tail)),
            }
