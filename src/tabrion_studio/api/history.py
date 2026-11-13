"""History and favorites routes (03 sections 2 A, 2 C and 6, STU-020). Nothing here touches a database server.

* ``GET /api/history?profile_id=&q=&limit=&before=``: ``{enabled, retention_days, entries: [{id, profile_id,
  environment, engine, sql, sql_truncated, param_names, state, row_count, elapsed_ms, code, finished_at}],
  next_before}``, newest first, the entries of the user's profiles and of this project's. ``q`` finds entries whose
  text contains it; ``before`` (an entry id) reads the next page. An entry never holds a parameter value: only the
  placeholder names.
* ``DELETE /api/history`` with ``{}`` (every entry shown here), ``{"profile_id"}`` (one profile's) or ``{"id"}`` (one
  entry): 200 ``{deleted}``.
* ``GET /api/favorites``: ``{favorites: [{profile_id, added_at}], recent: [{profile_id, opened_at}]}``, only of
  profiles that exist now.
* ``PUT /api/favorites`` with ``{"profile_id", "favorite": true | false}``: 200 with the same document.

``studio.state_unavailable`` (503) when the state database cannot be used. Every route sits behind the access
middleware (session cookie and CSRF header).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.problems import problem_response
from tabrion_studio.services.favorites import Favorites, FavoritesError
from tabrion_studio.services.history import DEFAULT_PAGE, History, HistoryError
from tabrion_studio.services.state import StateUnavailable

logger = logging.getLogger("tabrion_studio.history")
_MAX_BODY = 4096


def _body(raw: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(raw) if len(raw) <= _MAX_BODY else None
    except (ValueError, RecursionError):
        doc = None
    return doc if isinstance(doc, dict) else None


async def _answer(call: Any, *args: Any, **kwargs: Any) -> Response:
    try:
        result = await asyncio.to_thread(call, *args, **kwargs)
    except (HistoryError, FavoritesError, StateUnavailable) as exc:
        return problem_response(exc.code)
    return JSONResponse(result)


def add_history_routes(app: FastAPI, history: History, favorites: Favorites) -> None:
    @app.get("/api/history")
    async def get_history(
        profile_id: str | None = None, q: str | None = None, limit: int = DEFAULT_PAGE, before: int | None = None
    ) -> Response:
        return await _answer(history.entries, profile_id=profile_id, query=q, limit=limit, before=before)

    @app.delete("/api/history")
    async def delete_history(request: Request) -> Response:
        doc = _body(await request.body())
        if doc is None or any(key not in ("id", "profile_id") for key in doc):
            return problem_response("access.bad_request")

        def delete() -> dict[str, Any]:
            return {"deleted": history.delete(entry_id=doc.get("id"), profile_id=doc.get("profile_id"))}

        return await _answer(delete)

    @app.get("/api/favorites")
    async def get_favorites() -> Response:
        return await _answer(favorites.state)

    @app.put("/api/favorites")
    async def put_favorites(request: Request) -> Response:
        doc = _body(await request.body())
        if doc is None:
            return problem_response("access.bad_request")
        return await _answer(favorites.set_favorite, doc.get("profile_id"), doc.get("favorite"))
