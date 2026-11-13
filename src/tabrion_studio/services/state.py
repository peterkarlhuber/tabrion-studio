"""Studio's own state database (03 section 4.3): query history, favorite and recently opened profiles (STU-020) and the
SQL editor's drafts (TD-033, review F-5).

``<state_dir>/state.sqlite`` is opened with ``tabrion.sqlite.connect`` (threat T-04): a new file is created 0600 with
``O_CREAT | O_EXCL | O_NOFOLLOW`` whatever the umask, a link at the file or at its ``-wal``, ``-shm`` or
``-journal`` file is refused, and a file that another user owns or that others may write is refused too. The state
folder itself is checked by the CLI before the server starts (0700, owned by the user, no link). The profile keeps
tabrion's defaults (WAL, ``synchronous=FULL``, ``foreign_keys``, ``trusted_schema=OFF``) with ``secure_delete=ON``:
deleted history entries are overwritten in the file, not only unlinked from the b-tree.

The schema is versioned with ``PRAGMA user_version`` and migrated with ``tabrion.sqlite.migrate`` in one write
transaction. Every statement here is a fixed text with bound values. Version 2 adds the ``drafts`` table. Before a file
that holds data is migrated, ``tabrion.sqlite.backup`` copies it to ``state.sqlite.v<n>.bak`` (0600, same folder); the
copy is removed once the migration succeeded, so no second copy of the history stays behind.

One store is shared by every Studio instance of this user (one per project folder): SQLite's locking and the busy
timeout serialize the writers. Rows of project profiles carry the project's key (``scope``), so the history and the
favorites of ``project:parts`` in one project never show in another; user profiles have the empty scope.

The store opens lazily, on first use. When it cannot be opened (a refused link, a corrupt file, a schema of a newer
Studio, a full disk), the features that need it answer ``studio.state_unavailable`` for the life of the process and
queries run on without a history entry. To recover, stop Studio and move ``state.sqlite`` (with its ``-wal`` and
``-shm`` files) aside: the next start creates an empty one (the history and the favorites start over).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import TypeVar

from tabrion.sqlite import SQLiteProfile, backup, connect, migrate, write_transaction

__all__ = ["SCHEMA_VERSION", "STATE_FILE", "StateStore", "StateUnavailable", "scope_of"]

logger = logging.getLogger("tabrion_studio.state")
_T = TypeVar("_T")

STATE_FILE = "state.sqlite"
SCHEMA_VERSION = 2
#: How long a write waits for another instance's write lock.
BUSY_TIMEOUT_MS = 5_000
#: The pause between two tries of a write that waits briefly (:meth:`StateStore.write` with ``busy_timeout_ms``).
BUSY_PAUSE_S = 0.025
CHECKPOINT_ATTEMPTS = 3
CHECKPOINT_PAUSE_S = 0.05

_V1 = (
    "CREATE TABLE history ("
    " id INTEGER PRIMARY KEY,"
    " scope TEXT NOT NULL,"
    " profile_id TEXT NOT NULL,"
    " environment TEXT NOT NULL,"
    " engine TEXT NOT NULL,"
    " sql TEXT NOT NULL,"
    " sql_truncated INTEGER NOT NULL,"
    " param_names TEXT NOT NULL,"
    " state TEXT NOT NULL,"
    " row_count INTEGER,"
    " elapsed_ms REAL,"
    " code TEXT,"
    " finished_at REAL NOT NULL)",
    "CREATE INDEX history_scope_time ON history (scope, finished_at)",
    "CREATE INDEX history_profile_time ON history (scope, profile_id, finished_at)",
    "CREATE TABLE favorites ("
    " scope TEXT NOT NULL,"
    " profile_id TEXT NOT NULL,"
    " added_at REAL NOT NULL,"
    " PRIMARY KEY (scope, profile_id))",
    "CREATE TABLE recent ("
    " scope TEXT NOT NULL,"
    " profile_id TEXT NOT NULL,"
    " opened_at REAL NOT NULL,"
    " PRIMARY KEY (scope, profile_id))",
)


#: The SQL editor's drafts (TD-033): one document per project (``scope`` is the project key).
_V2 = ("CREATE TABLE drafts (scope TEXT PRIMARY KEY, doc TEXT NOT NULL, updated_at REAL NOT NULL)",)


def _migrate_v1(connection: sqlite3.Connection) -> None:
    for statement in _V1:
        connection.execute(statement)


def _migrate_v2(connection: sqlite3.Connection) -> None:
    for statement in _V2:
        connection.execute(statement)


class StateUnavailable(Exception):
    """The state database cannot be used; ``code`` is ``studio.state_unavailable``."""

    code = "studio.state_unavailable"

    def __init__(self) -> None:
        super().__init__(self.code)


def _busy(exc: sqlite3.OperationalError) -> bool:
    """Whether SQLite refused because another connection holds the lock (``SQLITE_BUSY``, ``SQLITE_LOCKED``)."""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in (5, 6)
    return "locked" in str(exc) or "busy" in str(exc)


def scope_of(profile_id: str, project_key: str) -> str | None:
    """The scope of a profile's rows: ``""`` for a user profile, the project key for a project's; None otherwise."""
    if profile_id.startswith("user:"):
        return ""
    if profile_id.startswith("project:"):
        return project_key
    return None


def _log_error(exc: sqlite3.Error) -> None:
    """Log a refusal of the state database. Another instance holding the lock is expected (each history read retries
    the purge while it does): it is logged at info level without a traceback. Every other error keeps its warning
    and traceback. Neither names a path or SQL."""
    if isinstance(exc, sqlite3.OperationalError) and _busy(exc):
        logger.info("state database busy", extra={"event": "studio.state_busy"})
        return
    logger.warning("state database error", exc_info=True, extra={"event": "studio.state_error"})


class StateStore:
    """The state database. Thread-safe: one connection, used under a lock; opened on first use."""

    def __init__(self, state_dir: Path, *, opener: Callable[[Path], sqlite3.Connection] | None = None) -> None:
        self.path = state_dir / STATE_FILE
        self._opener = opener or _open
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._failed = False
        self._checkpoint_pending = False

    def _get(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if self._failed:
            raise StateUnavailable
        try:
            self._connection = self._opener(self.path)
        except Exception:
            # Once per process: the log names the event, never the path's content.
            self._failed = True
            logger.warning("cannot open the state database", exc_info=True, extra={"event": "studio.state_unavailable"})
            raise StateUnavailable from None
        return self._connection

    def read(self, call: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run ``call`` on the connection (autocommit reads)."""
        with self._lock:
            connection = self._get()
            try:
                return call(connection)
            except sqlite3.Error as exc:
                _log_error(exc)
                raise StateUnavailable from None

    def write(self, call: Callable[[sqlite3.Connection], _T], *, busy_timeout_ms: int | None = None) -> _T:
        """Run ``call`` in one write transaction (``BEGIN IMMEDIATE``; rolled back on any error).

        ``busy_timeout_ms`` waits less than the usual :data:`BUSY_TIMEOUT_MS` for another instance's write lock (a
        query's history entry must not hold up the query's end). That wait is Studio's own loop with short pauses,
        not SQLite's busy handler: a SQLite built without ``usleep`` (as on some macOS builds) sleeps whole seconds
        there."""
        with self._lock:
            connection = self._get()
            try:
                if busy_timeout_ms is None:
                    with write_transaction(connection):
                        return call(connection)
                deadline = time.monotonic() + busy_timeout_ms / 1000
                connection.execute("PRAGMA busy_timeout = 0")
                try:
                    while True:
                        try:
                            with write_transaction(connection):
                                return call(connection)
                        except sqlite3.OperationalError as exc:
                            if not _busy(exc) or time.monotonic() + BUSY_PAUSE_S > deadline:
                                raise
                        time.sleep(BUSY_PAUSE_S)
                finally:
                    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            except sqlite3.Error as exc:
                _log_error(exc)
                raise StateUnavailable from None

    def checkpoint(self, *, attempts: int = 1) -> bool:
        """Move the write-ahead log into the file and truncate it (after a delete, no old page stays in the log).

        True when the log is empty now. A checkpoint never waits for another connection: each try runs with
        ``busy_timeout = 0``, so SQLite's busy handler is not called and the store lock is held only for the try
        itself. Another Studio instance (or any other program) that is reading at that moment keeps the log in use;
        ``attempts`` tries are made :data:`CHECKPOINT_PAUSE_S` apart, the pause outside the lock. When the log is
        still in use, the checkpoint is pending and :meth:`retry_checkpoint` (on every purge, so on every read of the
        history and every recorded query) tries it again until it succeeds. The log is 0600 like the file, and
        closing the last connection removes it."""
        for attempt in range(attempts):
            if attempt:
                time.sleep(CHECKPOINT_PAUSE_S)
            with self._lock:
                try:
                    connection = self._get()
                    connection.execute("PRAGMA busy_timeout = 0")
                    try:
                        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    finally:
                        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
                except (sqlite3.Error, StateUnavailable):
                    row = None
                # (busy, frames in the log, frames moved): busy 0 means the log was moved and truncated.
                if row is not None and row[0] == 0:
                    self._checkpoint_pending = False
                    return True
        with self._lock:
            if not self._checkpoint_pending:
                logger.info("state log still in use", extra={"event": "studio.state_checkpoint_partial"})
            self._checkpoint_pending = True
        return False

    @property
    def checkpoint_pending(self) -> bool:
        """A checkpoint after a delete found the log in use and has not succeeded since."""
        return self._checkpoint_pending

    def retry_checkpoint(self) -> None:
        """Try a pending checkpoint again (never waits; see :meth:`checkpoint`)."""
        if self._checkpoint_pending:
            self.checkpoint()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.close()
                self._connection = None

    @contextlib.contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """The raw connection under the lock (tests)."""
        with self._lock:
            yield self._get()


def _open(path: Path) -> sqlite3.Connection:
    connection = connect(path, profile=SQLiteProfile(secure_delete=True, busy_timeout=BUSY_TIMEOUT_MS / 1000))
    copies: list[Path] = []

    def keep_copy(found: int, target: int) -> None:
        # A file with data (an older version) is copied before it is migrated; tabrion.sqlite.backup writes the copy
        # 0600 through a temporary file and refuses a link.
        copy = path.with_name(f"{path.name}.v{found}.bak")
        backup(connection, copy, overwrite=True)
        copies.append(copy)

    try:
        migrate(connection, {1: _migrate_v1, 2: _migrate_v2}, target=SCHEMA_VERSION, backup=keep_copy)
    except BaseException:
        connection.close()
        raise
    # The steps only add tables: once they are in, the copy is not needed, and it would keep deleted history text.
    for copy in copies:
        with contextlib.suppress(OSError):
            copy.unlink()
    return connection
