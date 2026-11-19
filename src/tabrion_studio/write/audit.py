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

**Where.** ``audit/`` in the folder of ``write-areas.toml`` (the command line's configuration folder or the
platform's; never a state folder that ``TABRION_STUDIO_STATE_DIR`` chose, review N-1 of PR #116): the folder 0700,
every file 0600, owned by the user, never opened through a link (``O_NOFOLLOW``; on Windows the state folder checks
of :mod:`tabrion_studio.paths`). No file stays open between two
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
recomputes the chain over every segment and checks the head, so it finds a changed record (``audit.record_changed``), a
removed, inserted or reordered one (``audit.chain_broken``), a cut tail (``audit.truncated``) and a missing or changed
anchor (``audit.head_missing``, ``audit.head_mismatch``). Without a key, someone who can write the user's configuration
folder can rebuild a whole consistent chain; that is malware running as the user, outside the protection goal (threat
model, residual risk 3). Studio verifies the log when it opens it and on every listing; a log that fails is not appended
to, so no write runs until the user has moved the folder aside (it is kept for inspection, never repaired by Studio).

**Anchor outside the folder (G-S2 decision 3A).** With an :class:`AuditAnchor`, Studio also keeps the last
``(seq, hash)`` of the chain in the OS keyring (service ``tabrion-studio``, one entry per audit folder), updated after
every record under the folder lock. When Studio opens the log and the chain no longer reaches the anchor (the folder
was removed or moved aside and a new chain started, the log was cut, or a record at the anchor's position has another
hash), it writes an ``audit.reset`` record (the anchor's sequence number and hash and how many records it found) as
the first record of what follows, reports ``anchor: "reset"`` in :meth:`AuditLog.status`, and moves the anchor on. It
does not stop writes: the reset itself is now part of the chain and every listing counts such records (``resets``),
so the page shows "log missing or reset" until the user inspects it. Without a usable keyring the anchor is
``unavailable`` and the log works as before (residual risk 3); a failed keyring write makes it ``failed`` (the anchor
lags, which only weakens the check). Malware running as the user can change the keyring too: outside the protection
goal. Retention stays unlimited (documented in the security guide).

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
from collections.abc import Callable, Iterator, Mapping
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
import time
from typing import Any

from tabrion_studio.logs import REDACTED, printable, scrub
from tabrion_studio.paths import UnsafePathError, ensure_private_dir, read_private_file, remove_file, write_private_file

__all__ = [
    "EVENTS",
    "FIELDS",
    "GENESIS",
    "AuditAnchor",
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
LOCK = "audit.lock"
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
        "secret.confirm",
        "secret.unconfirm",
        "run.start",
        "run.result",
        "audit.pending_dropped",
        "audit.reset",  # G-S2 3A: the chain no longer reaches the keyring anchor (log missing or reset)
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
    "anchor_seq": _count,  # G-S2 3A: the sequence number the keyring anchor named when a reset was found
    "anchor_hash": _plan_hash,  # G-S2 3A: the hash the keyring anchor named
    "found": _count,  # G-S2 3A: the records found in the folder when the reset was found
    "source": _token,  # STU-XW-4: the kind of a confirmed secret reference (keyring, env, file, none), never its target
    "foreign": _flag,  # STU-XW-4: whether the confirmed pair's host is foreign (not loopback)
    "pair_hash": _plan_hash,  # STU-XW-4: the digest of the confirmed pair of server and reference
    "risks": _tokens,  # STU-CR-5: the risk classes of a schema change run (additive, destructive, ...)
    "steps": _count,  # STU-CR-5: the number of steps of a schema change run (planned at start, run at result)
    "backup": _token,  # STU-CR-5: made, opted_out, not_needed or dry_run; never the backup's path
    "dry_run": _flag,  # STU-CR-5: the run was a dry run, rolled back
    # STU-XDM-2 (STU-ADR-0008 section 9): backup and restore. File names Studio made, hashes, versions and names only.
    "backup_file": _token,  # the backup's file name in Studio's backup folder (Studio makes it), never a path
    "sha256": _plan_hash,  # the backup file's SHA-256
    "tool_version": _token,  # the version of pg_dump or pg_restore that ran
    "target": _text,  # the restore target's database or file name (escaped like every name)
    "restore_profile": _text,  # the admin profile whose lean role ran the restore (review M-1 of STU-XDM-1)
    "pre_backup": _token,  # the file name of the target's own backup taken before the restore
    "verified": _flag,  # whether the restored schema's fingerprint equals the backup's
    # STU-XDM-3 (STU-ADR-0008 sections 5 and 9): delete a database.
    "force": _flag,  # PostgreSQL WITH (FORCE) was chosen (its own typed confirmation)
    "sessions": _count,  # the number of other sessions on the target seen before the delete
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
    #: The number of ``audit.reset`` records in the chain (G-S2 3A).
    resets: int = 0
    #: The hash of the record at the sequence number ``_scan`` was asked for (``at``), if the chain has it.
    hash_at: str | None = None


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


def _scan(folder: Path, keep: int = 0, at: int | None = None) -> _Scan:
    """Read and verify every segment and the head; the last ``keep`` records are kept for a listing, and the hash of
    the record ``at`` (the keyring anchor's) is noted."""
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
                if seq == at:
                    scan.hash_at = prev
                if doc["event"] == "audit.reset":
                    scan.resets += 1
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


#: How long a writer waits for another Studio process's lock on the audit folder before it gives up (fail closed).
LOCK_TIMEOUT_S = 30.0
_LOCK_PAUSE_S = 0.01


@contextlib.contextmanager
def _folder_lock(folder: Path) -> Iterator[None]:
    """Hold the audit folder's lock across processes (review M-1 of PR #114).

    Several Studio processes (one per project) share ``audit`` in the flag folder. Every write, head update, rotation,
    pending marker change and verification happens under this lock: ``audit.lock`` (0600, never through a link),
    locked with ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows. The handle is closed when the lock is
    released, so nothing stays open between records. Raises :class:`UnsafePathError` or OSError (also after
    :data:`LOCK_TIMEOUT_S`)."""
    path = folder / LOCK
    if not _NOFOLLOW and path.is_symlink():
        raise UnsafePathError("refusing to follow a link")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _NOFOLLOW | _BINARY, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePathError("the audit lock is not a regular file")
        if _POSIX and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise UnsafePathError("the audit lock is not private")
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                _lock_fd(fd)
                break
            except (BlockingIOError, PermissionError, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_LOCK_PAUSE_S)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                _unlock_fd(fd)
    finally:
        os.close(fd)


if sys.platform == "win32":
    import msvcrt

    def _lock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_pending(folder: Path) -> tuple[list[dict[str, Any]], int]:
    """The pending marker's records and dropped count; ``([], 0)`` without one. Raises ValueError if damaged."""
    try:
        data = read_private_file(folder / PENDING)
    except (UnsafePathError, OSError):
        raise ValueError("pending marker not readable") from None
    if data is None:
        return [], 0
    try:
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
        return pending, _count(doc.get("dropped", 0))
    except (TypeError, AttributeError, RecursionError, KeyError):
        raise ValueError("not a pending marker") from None


class AnchorUnavailable(Exception):
    """The keyring cannot keep or give the anchor (no usable keyring, a locked or failing one)."""


class AuditAnchor:
    """The last ``(seq, hash)`` of an audit folder's chain, kept in the OS keyring (G-S2 decision 3A).

    ``store`` is the :class:`tabrion_studio.config.secrets.KeyringStore` of the process (never a plain-text fallback).
    The entry's key names the folder by a hash of its absolute path, so several configuration folders keep their
    own anchors; the value is ``1:<seq>:<hash>`` and holds nothing secret."""

    def __init__(self, store: Any, folder: Path) -> None:
        self.store = store
        path = str(folder.absolute()).encode("utf-8", "surrogatepass")
        self.key = "audit-anchor:" + hashlib.sha256(path).hexdigest()[:32]

    def read(self) -> tuple[int, str] | None:
        """The anchor, or None when there is none yet. Raises :class:`AnchorUnavailable`; an entry that is not an
        anchor counts as "no anchor here" for the comparison, never as a match (it is replaced at the next record)."""
        try:
            if not self.store.available:
                raise AnchorUnavailable()
            value = self.store.get(self.key)
        except AnchorUnavailable:
            raise
        except Exception:
            raise AnchorUnavailable() from None
        if value is None:
            return None
        parts = value.split(":") if isinstance(value, str) else []
        if len(parts) != 3 or parts[0] != "1" or not parts[1].isdigit() or not _HEX.fullmatch(parts[2]):
            # Not ours: an anchor that cannot be read cannot vouch for the log, so it reads as a reset at position 1.
            return (1, GENESIS)
        return int(parts[1]), parts[2]

    def write(self, seq: int, digest: str) -> None:
        """Keep ``(seq, digest)``. Raises :class:`AnchorUnavailable`."""
        try:
            self.store.set(self.key, f"1:{seq}:{digest}")
        except Exception:
            raise AnchorUnavailable() from None


class AuditLog:
    """The audit log of this Studio process; thread-safe, and safe with other Studio processes on the same folder
    (every change under :func:`_folder_lock`, with the chain's end read again from disk first). ``folder`` None: no
    log, every record is refused."""

    def __init__(self, folder: Path | None, clock: Callable[[], str] = _now, anchor: AuditAnchor | None = None) -> None:
        self.folder = folder
        self.clock = clock
        self.anchor = anchor
        #: ``none`` (no anchor), ``ok``, ``unavailable`` (no keyring), ``failed`` (a keyring write failed) or
        #: ``reset`` (this process found the chain no longer reaching the anchor and recorded it).
        self._anchor_state = "none" if anchor is None else "ok"
        self._lock = threading.Lock()
        #: Pending records that not even the pending marker file could take (in memory only, N-4).
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
            with _folder_lock(self.folder):
                anchor = self._read_anchor()
                scan = _scan(self.folder, at=None if anchor is None else anchor[0])
                self._adopt(scan)
                if self._problem is not None:
                    return
                self._check_anchor(anchor, scan)
                if self._problem is not None:
                    return
                try:
                    pending, dropped = _read_pending(self.folder)
                except ValueError:
                    self._broken("audit.pending_unreadable")
                    return
                if pending or dropped:
                    logger.warning("audit records are pending", extra={"event": "studio.audit_pending"})
                    with contextlib.suppress(AuditError):
                        self._flush_pending()
        except (UnsafePathError, OSError):
            self._problem = "audit.unavailable"
            logger.warning(
                "audit log folder not usable; writes stay refused", extra={"event": "studio.audit_unavailable"}
            )

    def _read_anchor(self) -> tuple[int, str] | None:
        """The keyring anchor, or None (none yet, or no usable keyring: then the state says so)."""
        if self.anchor is None:
            return None
        try:
            return self.anchor.read()
        except AnchorUnavailable:
            self._anchor_state = "unavailable"
            logger.warning(
                "no keyring anchor for the audit log; a removed log would go unnoticed",
                extra={"event": "studio.audit_anchor_unavailable"},
            )
            return None

    def _check_anchor(self, anchor: tuple[int, str] | None, scan: _Scan) -> None:
        """Compare the verified chain with the keyring anchor (folder lock held, G-S2 3A). A chain that no longer
        reaches it is recorded as ``audit.reset``; then, or when there was none, the anchor moves to the chain's end."""
        if self.anchor is None or self._anchor_state == "unavailable":
            return
        if anchor is not None and (scan.last_seq < anchor[0] or scan.hash_at != anchor[1]):
            logger.warning(
                "the audit log is missing or was reset: its chain no longer reaches the keyring anchor",
                extra={"event": "studio.audit_reset"},
            )
            self._anchor_state = "reset"
            try:
                self._write("audit.reset", {"anchor_seq": anchor[0], "anchor_hash": anchor[1], "found": scan.last_seq})
            except AuditError:
                if self._problem is None:
                    self._problem = "audit.unavailable"
            return
        if anchor is None or anchor != (self._seq, self._hash):
            self._move_anchor()

    def _move_anchor(self) -> None:
        """Keep the chain's end in the keyring (folder lock held). A failure only weakens the check; it is reported."""
        if self.anchor is None or self._anchor_state == "unavailable" or self._seq == 0:
            return
        try:
            self.anchor.write(self._seq, self._hash)
        except AnchorUnavailable:
            if self._anchor_state != "failed":
                logger.warning(
                    "the audit anchor could not be kept in the keyring", extra={"event": "studio.audit_anchor_failed"}
                )
            self._anchor_state = "failed"

    def _broken(self, problem: str) -> None:
        self._problem = problem
        logger.warning(
            "audit log failed verification; writes stay refused",
            extra={"event": "studio.audit_broken", "code": problem},
        )

    def _adopt(self, scan: _Scan) -> None:
        if not scan.verification.ok:
            self._broken(scan.verification.problem or "audit.unreadable")
            return
        self._problem = None
        self._seq, self._hash = scan.last_seq, scan.last_hash
        self._current_first, self._size = scan.current_first, scan.current_size

    def _sync(self) -> None:
        """Read the end of the chain again from disk (folder lock held): another Studio process may have written.

        The last record of the current segment (its own hash checked) or, when that segment is empty after a
        rotation, ``head.json``. The head may lag by one record; anything else, a partial line, or an end before the
        one this process saw last is a change of the log: it stops further records (fail closed)."""
        if self.folder is None:
            raise AuditError()
        try:
            head = _head(self.folder)
            data = read_private_file(self.folder / CURRENT) or b""
        except (_Broken, UnsafePathError, OSError):
            self._broken("audit.unsafe_file")
            raise AuditError() from None
        lines = data.split(b"\n")
        if lines.pop():
            self._broken("audit.unreadable")
            raise AuditError()
        if lines:
            try:
                first = json.loads(lines[0])
                last = json.loads(lines[-1])
                body = {key: value for key, value in last.items() if key != "hash"}
                valid = (
                    isinstance(first, dict)
                    and type(first.get("seq")) is int
                    and type(last.get("seq")) is int
                    and _digest(body) == last.get("hash")
                )
            except (ValueError, RecursionError, AttributeError):
                valid = False
            if not valid:
                self._broken("audit.record_changed")
                raise AuditError()
            seq, digest, current_first = last["seq"], last["hash"], first["seq"]
            if head is None or head[0] not in (seq, seq - 1):
                self._broken("audit.head_mismatch")
                raise AuditError()
        else:
            seq, digest = head if head is not None else (0, GENESIS)
            current_first = None
        if seq < self._seq or (seq == self._seq and digest != self._hash):
            self._broken("audit.truncated")
            raise AuditError()
        self._seq, self._hash, self._current_first, self._size = seq, digest, current_first, len(data)

    @property
    def available(self) -> bool:
        return self._problem is None

    def check(self) -> None:
        """Raise :class:`AuditError` if the log cannot take records now (missing, unsafe or failed verification)."""
        if self._problem is not None:
            raise AuditError()

    def status(self) -> dict[str, Any]:
        """For the page: whether records can be written, and how many are pending (N-4)."""
        return {
            "available": self._problem is None,
            "problem": self._problem,
            "pending": self.pending_count,
            "anchor": self._anchor_state,
        }

    @property
    def pending_count(self) -> int:
        stored = 0
        if self.folder is not None:
            with contextlib.suppress(ValueError):
                items, dropped = _read_pending(self.folder)
                stored = len(items) + dropped
        return len(self._pending) + self._dropped + stored

    # --- records ---------------------------------------------------------------------------------------------------

    def append(self, event: str, **fields: object) -> int:
        """Write one record now, or raise :class:`AuditError` (fail closed: the caller does not do the action).

        Pending records are written first. Returns the record's sequence number. A ValueError (an unknown event or
        field, a field of the wrong shape) is a coding error and raised as it is; nothing is written then."""
        if event not in EVENTS:
            raise ValueError("unknown audit event")
        clean = clean_fields(fields)
        with self._lock:
            return self._locked_write(event, clean)

    def record(self, event: str, **fields: object) -> bool:
        """Write a record of an action that has happened; on failure keep a pending marker (N-4). False if pending."""
        if event not in EVENTS:
            raise ValueError("unknown audit event")
        clean = clean_fields(fields)
        when = self.clock()
        with self._lock:
            try:
                self._locked_write(event, clean)
            except AuditError:
                self._keep_pending({"event": event, "time": when, "fields": clean})
                return False
        return True

    def _locked_write(self, event: str, clean: dict[str, object]) -> int:
        self.check()
        if self.folder is None:
            raise AuditError()
        try:
            with _folder_lock(self.folder):
                self._sync()
                self._flush_pending()
                return self._write(event, clean)
        except (UnsafePathError, OSError):
            logger.warning("audit folder lock not taken", extra={"event": "studio.audit_write_failed"})
            raise AuditError() from None

    def _keep_pending(self, item: dict[str, Any]) -> None:
        """Add ``item`` to the pending marker (thread lock held, folder lock not)."""
        logger.warning("audit record could not be written; kept as pending", extra={"event": "studio.audit_pending"})
        if self.folder is not None:
            try:
                with _folder_lock(self.folder):
                    items, dropped = _read_pending(self.folder)
                    items.append(item)
                    if len(items) > MAX_PENDING:
                        items.pop(0)
                        dropped += 1
                    self._save_pending(items, dropped)
                    return
            except (ValueError, UnsafePathError, OSError):
                pass
        logger.warning(
            "audit pending marker could not be saved; it is kept in memory only",
            extra={"event": "studio.audit_pending"},
        )
        self._pending.append(item)
        if len(self._pending) > MAX_PENDING:
            self._pending.pop(0)
            self._dropped += 1

    def _save_pending(self, items: list[dict[str, Any]], dropped: int) -> None:
        """Write the pending marker (folder lock held); raises OSError."""
        if self.folder is None:
            raise OSError("no folder")
        if items or dropped:
            doc = {"v": VERSION, "pending": items, "dropped": dropped}
            write_private_file(self.folder / PENDING, _canonical(doc))
        else:
            remove_file(self.folder / PENDING)

    def _flush_pending(self) -> None:
        """Write the pending records first, marked late (both locks held). Raises :class:`AuditError`."""
        if self.folder is None:
            raise AuditError()
        try:
            stored, stored_dropped = _read_pending(self.folder)
        except ValueError:
            self._broken("audit.pending_unreadable")
            raise AuditError() from None
        if not stored and not stored_dropped and not self._pending and not self._dropped:
            return
        try:
            while stored:
                item = stored[0]
                self._write(item["event"], {**item["fields"], "late": True, "occurred": item["time"]})
                stored.pop(0)
            while self._pending:
                item = self._pending[0]
                self._write(item["event"], {**item["fields"], "late": True, "occurred": item["time"]})
                self._pending.pop(0)
            dropped = stored_dropped + self._dropped
            if dropped:
                self._write("audit.pending_dropped", {"dropped": dropped, "late": True})
                stored_dropped, self._dropped = 0, 0
        finally:
            try:
                self._save_pending(stored, stored_dropped)
            except OSError:
                logger.warning("audit pending marker not updated", extra={"event": "studio.audit_pending"})

    def _write(self, event: str, fields: dict[str, object]) -> int:
        """Append one record and move the head (both locks held, :meth:`_sync` done). Raises :class:`AuditError`."""
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
            self._current_first = self._seq + 1
        self._seq, self._hash, self._size = self._seq + 1, digest, self._size + len(line)
        try:
            write_private_file(self.folder / HEAD, _canonical({"v": VERSION, "seq": self._seq, "hash": self._hash}))
        except OSError:
            # The record is written and the chain is whole; the head lags by one, which verification accepts once.
            # Nothing more is written in this process (fail closed), so the lag never grows.
            self._problem = "audit.head_unwritable"
            logger.warning("audit head not written; writes stay refused", extra={"event": "studio.audit_write_failed"})
        self._move_anchor()
        return self._seq

    def _rotate(self) -> None:
        """Move the current segment to the name of its first record, never over an existing name (A-02, TX-12;
        note N-2 of the PR #114 review): a hard link fails on an existing name, then the old name is removed. Where
        the file system has no hard links, the name is checked and renamed, under the folder lock."""
        if self.folder is None or self._current_first is None:
            raise UnsafePathError("nothing to rotate")
        current = self.folder / CURRENT
        target = self.folder / f"audit-{self._current_first:012d}.jsonl"
        try:
            os.link(current, target)
        except FileExistsError:
            self._problem = "audit.unexpected_file"
            raise UnsafePathError("a segment with this name exists") from None
        except OSError:
            if target.exists() or target.is_symlink():
                self._problem = "audit.unexpected_file"
                raise UnsafePathError("a segment with this name exists") from None
            os.rename(current, target)
        else:
            os.unlink(current)
        self._current_first, self._size = None, 0

    # --- reading ---------------------------------------------------------------------------------------------------

    def listing(self, limit: int = 200) -> dict[str, Any]:
        """The newest ``limit`` records and a fresh verification of the whole log (under the folder lock, so no other
        process is halfway through a record); a failure stops further records."""
        limit = max(1, min(limit, MAX_LISTING))
        with self._lock:
            if self.folder is None:
                return {**self.status(), "verification": None, "records": []}
            try:
                with _folder_lock(self.folder):
                    scan = _scan(self.folder, keep=limit)
            except (UnsafePathError, OSError):
                return {**self.status(), "verification": None, "records": []}
            if not scan.verification.ok and self._problem is None:
                self._adopt(scan)
            return {
                **self.status(),
                "verification": scan.verification.to_dict(),
                "resets": scan.resets,
                "records": list(reversed(scan.tail)),
            }
