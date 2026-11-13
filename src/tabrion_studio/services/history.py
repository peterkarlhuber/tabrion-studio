"""The query history (03 section 2 C, 08 sections 4.10 and 5, STU-020): local, per profile, can be turned off, and
**never with parameter values** (threats T-04 and T-05, 08 section 5 "Parameter nie in Historie").

The query engine reports each finished query here (:meth:`History.record`) with the statement's text as the user
wrote it (``:name`` placeholders included) and the *names* of its placeholders, read from that text again with
:func:`tabrion_studio.services.sqltext.placeholders`. The values the query ran with never reach this module: its
interface has no parameter for them. What an entry keeps:

* the profile id and its scope (:func:`tabrion_studio.services.state.scope_of`), the environment and the engine;
* the statement text, at most :data:`MAX_SQL_CHARS` characters (``sql_truncated`` says it was cut);
* the placeholder names, the final state (``done``, ``failed``, ``canceled``), the row count, the duration and the
  problem code of a failed query (a fixed ``studio.*`` or ``db.<kind>`` code, never a server message);
* when it finished.

The ``[history]`` settings decide: ``enabled = false`` records nothing (the entries already kept stay until they
expire or are deleted), and ``retention_days`` (30 by default) drops older entries; at most :data:`MAX_ENTRIES` are
kept, the oldest go first. A delete overwrites the rows (``secure_delete``) and checkpoints the write-ahead log.

A history entry is the user's own SQL text, which can hold sensitive literals (asset A5): it lives only in the state
database (0600), never in a log record (the engine logs no SQL), and is shown only to the session that owns the
server.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import logging
import sqlite3
import time
from typing import Any

from tabrion_studio.config.schema import Settings
from tabrion_studio.services.state import StateStore, StateUnavailable, scope_of

__all__ = ["DEFAULT_PAGE", "MAX_ENTRIES", "MAX_PAGE", "MAX_SQL_CHARS", "History", "HistoryError"]

logger = logging.getLogger("tabrion_studio.history")

#: How much of a statement an entry keeps (a statement may have up to 512 KB).
MAX_SQL_CHARS = 64 * 1024
#: Entries kept in all, over every profile and project; the oldest go first.
MAX_ENTRIES = 5_000
DEFAULT_PAGE = 100
MAX_PAGE = 500
#: A search text longer than this finds nothing useful and only costs time.
MAX_QUERY_CHARS = 200
_STATES = frozenset({"done", "failed", "canceled"})
_DAY_S = 86_400.0
#: Fixed statements: an absent filter is a NULL that makes its condition true.
_SELECT = (
    "SELECT id, profile_id, environment, engine, sql, sql_truncated, param_names, state, row_count, elapsed_ms, code,"
    " finished_at FROM history WHERE scope IN (?, ?) AND finished_at >= ?"
    " AND (? IS NULL OR (scope = ? AND profile_id = ?))"
    " AND (? IS NULL OR sql LIKE ? ESCAPE '\\')"
    " AND (? IS NULL OR id < ?)"
    " ORDER BY id DESC LIMIT ?"
)
_DELETE = (
    "DELETE FROM history WHERE scope IN (?, ?) AND (? IS NULL OR id = ?)"
    " AND (? IS NULL OR (scope = ? AND profile_id = ?))"
)


class HistoryError(Exception):
    """A history request that is not valid; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@dataclass
class History:
    """History entries in the state database, for the profiles of one project (and the user's own profiles)."""

    store: StateStore
    project_key: str
    settings: Callable[[], Settings] = Settings
    clock: Callable[[], float] = time.time
    _warned: bool = field(default=False, repr=False)

    def _scopes(self) -> tuple[str, str]:
        return ("", self.project_key)

    # Recording

    def record(
        self,
        *,
        profile_id: str,
        environment: str,
        engine: str,
        sql: str,
        param_names: Sequence[str],
        state: str,
        row_count: int | None,
        elapsed_ms: float | None,
        code: str | None,
    ) -> bool:
        """Keep one finished query; False when the history is off or the store is unavailable (never raises).

        There is deliberately no argument for parameter values (08 section 5): only the placeholder names.
        """
        settings = self.settings()
        scope = scope_of(profile_id, self.project_key)
        if not settings.history_enabled or scope is None or state not in _STATES or not sql.strip():
            return False
        now = self.clock()
        cutoff = now - settings.history_retention_days * _DAY_S
        names = json.dumps(sorted(set(param_names)))
        text = sql[:MAX_SQL_CHARS]

        def insert(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT INTO history (scope, profile_id, environment, engine, sql, sql_truncated, param_names, state,"
                " row_count, elapsed_ms, code, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scope,
                    profile_id,
                    environment,
                    engine,
                    text,
                    int(len(sql) > MAX_SQL_CHARS),
                    names,
                    state,
                    row_count,
                    elapsed_ms,
                    code,
                    now,
                ),
            )
            connection.execute("DELETE FROM history WHERE finished_at < ?", (cutoff,))
            connection.execute(
                "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                (MAX_ENTRIES,),
            )

        try:
            self.store.write(insert)
        except StateUnavailable:
            if not self._warned:
                self._warned = True
                logger.warning("query history unavailable", extra={"event": "studio.history_unavailable"})
            return False
        return True

    # Reading

    def entries(
        self,
        *,
        profile_id: object = None,
        query: object = None,
        limit: object = DEFAULT_PAGE,
        before: object = None,
    ) -> dict[str, Any]:
        """The newest entries first (``before``: an entry id, for the next page). Raises :class:`HistoryError` and
        :class:`StateUnavailable`."""
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise HistoryError("studio.invalid_request")
        if before is not None and (type(before) is not int or before < 1):
            raise HistoryError("studio.invalid_request")
        if profile_id is not None and (not isinstance(profile_id, str) or len(profile_id) > 512):
            raise HistoryError("studio.invalid_request")
        if query is not None and (not isinstance(query, str) or len(query) > MAX_QUERY_CHARS):
            raise HistoryError("studio.invalid_request")
        settings = self.settings()
        cutoff = self.clock() - settings.history_retention_days * _DAY_S
        scope = None
        if profile_id is not None:
            scope = scope_of(profile_id, self.project_key)
            if scope is None:
                raise HistoryError("studio.invalid_request")
        pattern = _like(query) if query else None
        args = (*self._scopes(), cutoff, scope, scope, profile_id, pattern, pattern, before, before, limit + 1)
        rows = self.store.read(lambda connection: connection.execute(_SELECT, args).fetchall())
        entries = [_entry(row) for row in rows[:limit]]
        return {
            "enabled": settings.history_enabled,
            "retention_days": settings.history_retention_days,
            "entries": entries,
            "next_before": entries[-1]["id"] if len(rows) > limit and entries else None,
        }

    # Deleting

    def delete(self, *, entry_id: object = None, profile_id: object = None) -> int:
        """Delete one entry, a profile's entries or (neither given) every entry of this project and of the user's
        profiles; the number deleted. Raises :class:`HistoryError` and :class:`StateUnavailable`."""
        if entry_id is not None and profile_id is not None:
            raise HistoryError("studio.invalid_request")
        scope = None
        if entry_id is not None and (type(entry_id) is not int or entry_id < 1):
            raise HistoryError("studio.invalid_request")
        if profile_id is not None:
            scope = scope_of(profile_id, self.project_key) if isinstance(profile_id, str) else None
            if scope is None or len(str(profile_id)) > 512:
                raise HistoryError("studio.invalid_request")
        args = (*self._scopes(), entry_id, entry_id, scope, scope, profile_id)
        deleted = self.store.write(lambda connection: connection.execute(_DELETE, args).rowcount)
        # The deleted pages are overwritten (secure_delete); the checkpoint empties the write-ahead log as well.
        self.store.checkpoint()
        logger.info("history deleted", extra={"event": "studio.history_deleted", "count": deleted})
        return int(deleted)


def _entry(row: tuple[Any, ...]) -> dict[str, Any]:
    try:
        names = json.loads(row[6])
    except ValueError:
        names = []
    return {
        "id": row[0],
        "profile_id": row[1],
        "environment": row[2],
        "engine": row[3],
        "sql": row[4],
        "sql_truncated": bool(row[5]),
        "param_names": [name for name in names if isinstance(name, str)] if isinstance(names, list) else [],
        "state": row[7],
        "row_count": row[8],
        "elapsed_ms": row[9],
        "code": row[10],
        "finished_at": row[11],
    }
