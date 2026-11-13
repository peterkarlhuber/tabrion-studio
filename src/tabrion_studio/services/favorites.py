"""Favorite and recently opened profiles (03 section 2 A "Favoriten, zuletzt geöffnet", STU-020).

Both live in the state database (:mod:`tabrion_studio.services.state`) as profile ids with their scope: a user
profile's favorite holds in every project, a project profile's only in its project. They hold ids only, never a
profile's target or secret; the page joins them with ``GET /api/profiles``, and only ids of profiles that exist now
(a project's only while its file is trusted) are answered or accepted.

* A favorite is set and cleared by the user (``PUT /api/favorites``).
* A profile is "recently opened" when ``POST /api/connections`` opened (or returned) its connection; the
  :data:`MAX_RECENT` newest per scope are kept.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import sqlite3
import time
from typing import Any

from tabrion_studio.services.state import StateStore, StateUnavailable, scope_of

__all__ = ["MAX_FAVORITES", "MAX_RECENT", "Favorites", "FavoritesError"]

logger = logging.getLogger("tabrion_studio.favorites")

MAX_RECENT = 10
MAX_FAVORITES = 100


class FavoritesError(Exception):
    """A favorites request that is not valid; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Favorites:
    store: StateStore
    project_key: str
    #: The ids of the profiles that exist now (the user's and, while trusted, the project's).
    profile_ids: Callable[[], set[str]]
    clock: Callable[[], float] = time.time

    def _scopes(self) -> tuple[str, str]:
        return ("", self.project_key)

    def state(self) -> dict[str, Any]:
        """``{favorites: [{profile_id, added_at}], recent: [{profile_id, opened_at}]}`` of the profiles that exist."""
        existing = self.profile_ids()

        def read(connection: sqlite3.Connection) -> tuple[list[Any], list[Any]]:
            favorites = connection.execute(
                "SELECT scope, profile_id, added_at FROM favorites WHERE scope IN (?, ?) ORDER BY added_at",
                self._scopes(),
            ).fetchall()
            recent = connection.execute(
                "SELECT scope, profile_id, opened_at FROM recent WHERE scope IN (?, ?) ORDER BY opened_at DESC",
                self._scopes(),
            ).fetchall()
            return favorites, recent

        favorites, recent = self.store.read(read)
        return {
            "favorites": [
                {"profile_id": profile_id, "added_at": added}
                for scope, profile_id, added in favorites
                if profile_id in existing and scope == scope_of(profile_id, self.project_key)
            ],
            "recent": [
                {"profile_id": profile_id, "opened_at": opened}
                for scope, profile_id, opened in recent
                if profile_id in existing and scope == scope_of(profile_id, self.project_key)
            ][:MAX_RECENT],
        }

    def set_favorite(self, profile_id: object, favorite: object) -> dict[str, Any]:
        """Mark or unmark a profile that exists now. Raises :class:`FavoritesError` and :class:`StateUnavailable`."""
        if not isinstance(profile_id, str) or not isinstance(favorite, bool):
            raise FavoritesError("studio.invalid_request")
        scope = scope_of(profile_id, self.project_key)
        if scope is None or profile_id not in self.profile_ids():
            raise FavoritesError("studio.profile_not_found")
        now = self.clock()

        def change(connection: sqlite3.Connection) -> None:
            if not favorite:
                connection.execute("DELETE FROM favorites WHERE scope = ? AND profile_id = ?", (scope, profile_id))
                return
            (count,) = connection.execute("SELECT count(*) FROM favorites").fetchone()
            known = connection.execute(
                "SELECT 1 FROM favorites WHERE scope = ? AND profile_id = ?", (scope, profile_id)
            ).fetchone()
            if known is None and count >= MAX_FAVORITES:
                raise FavoritesError("studio.favorites_full")
            connection.execute(
                "INSERT INTO favorites (scope, profile_id, added_at) VALUES (?, ?, ?)"
                " ON CONFLICT (scope, profile_id) DO NOTHING",
                (scope, profile_id, now),
            )

        self.store.write(change)
        logger.info("favorite changed", extra={"event": "studio.favorite_changed", "favorite": favorite})
        return self.state()

    def opened(self, profile_id: str) -> None:
        """Note that the profile's connection was opened (never raises: the connection works without it)."""
        scope = scope_of(profile_id, self.project_key)
        if scope is None:
            return
        now = self.clock()

        def note(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT INTO recent (scope, profile_id, opened_at) VALUES (?, ?, ?)"
                " ON CONFLICT (scope, profile_id) DO UPDATE SET opened_at = excluded.opened_at",
                (scope, profile_id, now),
            )
            connection.execute(
                "DELETE FROM recent WHERE scope = ? AND profile_id NOT IN"
                " (SELECT profile_id FROM recent WHERE scope = ? ORDER BY opened_at DESC LIMIT ?)",
                (scope, scope, MAX_RECENT),
            )

        try:
            self.store.write(note)
        except StateUnavailable:
            return
