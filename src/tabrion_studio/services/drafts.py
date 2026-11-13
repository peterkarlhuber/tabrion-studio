"""The SQL editor's drafts in the state database (03 section 2 C, 08 section 4.4, TD-033, review F-5).

A draft is what the editor's tabs hold: each tab's id, title, profile, SQL text, the text as it was last saved and,
for a tab opened from or saved to a project script, the script's path, digest and line endings; and which tab is
open. They used to live in the browser's ``localStorage``, which belongs to the port-scoped origin
``http://127.0.0.1:<port>``: any later program serving on that port in the same browser profile could read them, and
they were outside the 0600 state folder. Now they live in ``state.sqlite`` (0600, :mod:`.state`) behind the
authenticated API, one document per project (``scope`` is the project key), as before one key per project.

* **Never parameter values or results.** A tab has a fixed set of fields; a tab with any other field is refused
  (``studio.invalid_request``), so a page that tried to send parameter values would fail instead of storing them.
  A text that is not valid Unicode (a lone surrogate, which SQLite cannot store as UTF-8) is refused the same way.
  The text is what the user typed, with its ``:name`` placeholders, like the history.
* **Caps.** At most :data:`MAX_TABS` tabs, :data:`MAX_TEXT_CHARS` per text (the statement cap) and
  :data:`MAX_TOTAL_CHARS` of text in all (``studio.drafts_too_large``); the page leaves out the largest texts first
  so that it stays below them.
* **Pages of one project merge by tab.** A save names every tab id the page has had (``known``): the stored tabs it
  never had (another page added them) are kept after its own while the caps allow; the tabs it closed go. This is the
  merge the browser storage had (L-5 of ``stu-012-review.md``), now in one write transaction.
* **Settings.** ``[history] enabled = false`` does not stop the drafts: they are the tabs the user has open, shown on
  the page, not a record of what ran (close a tab to delete its text). ``[history] retention_days`` does apply: the
  drafts of a project not saved for that many days are deleted at start, after a settings save and when they are
  read, and every save deletes other projects' drafts that old. "Delete history" leaves the drafts.
* **PROD.** Like the history, which records PROD queries too, a tab on a PROD profile keeps its draft: the text the
  user typed, never a value or a row.

* **Deleted text.** ``secure_delete`` overwrites freed pages; after a save or read that removed text (a closed tab, a
  tab left out by the caps, an expired document) and after a purge, the write-ahead log is checkpointed (TRUNCATE).

No log record holds a draft's text, a title or a script path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
import sqlite3
import time
from typing import Any

from tabrion_studio.config.schema import Settings
from tabrion_studio.services.state import StateStore, StateUnavailable

__all__ = [
    "MAX_KNOWN",
    "MAX_TABS",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_CHARS",
    "Drafts",
    "DraftsError",
]

MAX_TABS = 32
#: One text (a tab's SQL or its saved text): the statement cap of ``POST /api/queries``.
MAX_TEXT_CHARS = 512 * 1024
#: Every text of every tab together.
MAX_TOTAL_CHARS = 2 * 1024 * 1024
#: Tab ids a save may name as known to the page.
MAX_KNOWN = 1024
_ID_CHARS = 64
_TITLE_CHARS = 128
_PROFILE_CHARS = 512
_PATH_CHARS = 512
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FIELDS = frozenset({"id", "title", "profile_id", "sql", "saved_sql", "script_path", "script_digest", "script_crlf"})
_DAY_S = 86_400.0


class DraftsError(Exception):
    """A drafts request that is not valid; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise DraftsError("studio.invalid_request")
    try:
        # A JSON escape of a lone UTF-16 surrogate decodes to a text SQLite cannot store (review finding 1).
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise DraftsError("studio.invalid_request") from None
    return value


def _tab(value: object) -> dict[str, Any]:
    """One tab, checked field by field and rebuilt: nothing but the known fields is ever stored."""
    if not isinstance(value, Mapping) or any(key not in _FIELDS for key in value):
        raise DraftsError("studio.invalid_request")
    sql = value.get("sql")
    saved = value.get("saved_sql", "")
    if (isinstance(sql, str) and len(sql) > MAX_TEXT_CHARS) or (isinstance(saved, str) and len(saved) > MAX_TEXT_CHARS):
        raise DraftsError("studio.drafts_too_large")
    tab: dict[str, Any] = {
        "id": _text(value.get("id"), _ID_CHARS),
        "title": _text(value.get("title"), _TITLE_CHARS),
        "profile_id": _text(value.get("profile_id", ""), _PROFILE_CHARS),
        "sql": _text(sql, MAX_TEXT_CHARS),
        "saved_sql": _text(saved, MAX_TEXT_CHARS),
    }
    if tab["id"] == "":
        raise DraftsError("studio.invalid_request")
    path, digest, crlf = value.get("script_path"), value.get("script_digest"), value.get("script_crlf", False)
    if path is not None or digest is not None:
        path = _text(path, _PATH_CHARS)
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None or path == "":
            raise DraftsError("studio.invalid_request")
        if not isinstance(crlf, bool):
            raise DraftsError("studio.invalid_request")
        tab["script_path"] = path
        tab["script_digest"] = digest
        if crlf:
            tab["script_crlf"] = True
    elif "script_crlf" in value:
        raise DraftsError("studio.invalid_request")
    return tab


def _size(tab: Mapping[str, Any]) -> int:
    return len(tab["sql"]) + len(tab["saved_sql"])


def _stored(doc: str) -> list[dict[str, Any]]:
    """The tabs of a stored document; one that cannot be read is no draft (it is replaced at the next save)."""
    try:
        parsed = json.loads(doc)
        tabs = parsed.get("tabs") if isinstance(parsed, dict) else None
        return [_tab(item) for item in tabs] if isinstance(tabs, list) else []
    except (ValueError, RecursionError, DraftsError):
        return []


@dataclass
class Drafts:
    """The drafts of one project's editor tabs."""

    store: StateStore
    project_key: str
    settings: Callable[[], Settings] = Settings
    clock: Callable[[], float] = time.time

    def _cutoff(self) -> float:
        return self.clock() - self.settings().history_retention_days * _DAY_S

    def purge(self) -> int:
        """Delete the drafts the retention period expires, of every project (never raises); the number deleted.

        Runs at start and after a settings save, like the history's purge: the retention period holds on disk, also
        for a project that is not opened again."""
        cutoff = self._cutoff()
        try:
            deleted = int(
                self.store.write(
                    lambda connection: connection.execute("DELETE FROM drafts WHERE updated_at < ?", (cutoff,)).rowcount
                )
            )
        except StateUnavailable:
            return 0
        if deleted:
            self.store.checkpoint()
        return deleted

    def read(self) -> dict[str, Any]:
        """``{drafts: {tabs, active_tab} | null}``. Raises :class:`StateUnavailable`."""
        cutoff = self._cutoff()

        def load(connection: sqlite3.Connection) -> tuple[str, float] | None:
            row = connection.execute(
                "SELECT doc, updated_at FROM drafts WHERE scope = ?", (self.project_key,)
            ).fetchone()
            if row is not None and row[1] < cutoff:
                # Older than the retention: deleted, never answered.
                connection.execute("DELETE FROM drafts WHERE scope = ?", (self.project_key,))
                return ("", -1.0)
            return None if row is None else (str(row[0]), float(row[1]))

        row = self.store.write(load)
        if row is not None and row[1] < 0:
            # The deleted text must not stay in the write-ahead log (review finding 2).
            self.store.checkpoint()
            row = None
        if row is None:
            return {"drafts": None}
        tabs = _stored(row[0])
        if not tabs:
            return {"drafts": None}
        try:
            active = json.loads(row[0]).get("active_tab")
        except (ValueError, AttributeError):
            active = None
        if not any(tab["id"] == active for tab in tabs):
            active = tabs[0]["id"]
        return {"drafts": {"tabs": tabs, "active_tab": active}}

    def save(self, tabs: object, active_tab: object, known: object = None) -> dict[str, Any]:
        """Store the page's tabs, keeping the stored tabs the page never had (``known``) after them while the caps
        allow; ``{tabs}``, the number stored. Raises :class:`DraftsError` and :class:`StateUnavailable`."""
        if not isinstance(tabs, Sequence) or isinstance(tabs, str) or len(tabs) > MAX_TABS:
            raise DraftsError("studio.invalid_request")
        own = [_tab(item) for item in tabs]
        ids = [tab["id"] for tab in own]
        if len(set(ids)) != len(ids):
            raise DraftsError("studio.invalid_request")
        if sum(_size(tab) for tab in own) > MAX_TOTAL_CHARS:
            raise DraftsError("studio.drafts_too_large")
        active = _text(active_tab, _ID_CHARS) if active_tab is not None else ""
        seen: set[str] = set(ids)
        if known is not None:
            if not isinstance(known, Sequence) or isinstance(known, str) or len(known) > MAX_KNOWN:
                raise DraftsError("studio.invalid_request")
            seen.update(_text(item, _ID_CHARS) for item in known)
        now = self.clock()
        cutoff = self._cutoff()

        def write(connection: sqlite3.Connection) -> tuple[int, bool]:
            row = connection.execute(
                "SELECT doc, updated_at FROM drafts WHERE scope = ?", (self.project_key,)
            ).fetchone()
            expired = row is not None and row[1] < cutoff
            stored = [] if row is None or expired else _stored(str(row[0]))
            others = [] if known is None else stored
            merged = list(own)
            total = sum(_size(tab) for tab in merged)
            for tab in others:
                if tab["id"] in seen or len(merged) >= MAX_TABS or total + _size(tab) > MAX_TOTAL_CHARS:
                    continue
                merged.append(tab)
                total += _size(tab)
            if not merged:
                connection.execute("DELETE FROM drafts WHERE scope = ?", (self.project_key,))
            else:
                chosen = active if any(tab["id"] == active for tab in merged) else merged[0]["id"]
                doc = json.dumps({"tabs": merged, "active_tab": chosen}, ensure_ascii=False, separators=(",", ":"))
                connection.execute(
                    "INSERT INTO drafts (scope, doc, updated_at) VALUES (?, ?, ?)"
                    " ON CONFLICT (scope) DO UPDATE SET doc = excluded.doc, updated_at = excluded.updated_at",
                    (self.project_key, doc, now),
                )
            # Other projects' drafts older than the retention.
            purged = connection.execute("DELETE FROM drafts WHERE updated_at < ?", (cutoff,)).rowcount
            # Text went away: a closed tab, a tab the caps left out, an expired document, another project's drafts.
            kept = {tab["id"] for tab in merged}
            removed = expired or purged > 0 or any(tab["id"] not in kept for tab in stored)
            return len(merged), removed

        count, removed = self.store.write(write)
        if removed:
            # secure_delete overwrites the freed pages of the file; the checkpoint empties the write-ahead log, which
            # would otherwise keep the old document until the next checkpoint (review finding 2).
            self.store.checkpoint()
        return {"tabs": count}
