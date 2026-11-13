"""Draft routes of the SQL editor (TD-033, review F-5): the editor's tabs kept in the state database.

* ``GET /api/drafts``: ``{drafts: {tabs: [{id, title, profile_id, sql, saved_sql, script_path?, script_digest?,
  script_crlf?}], active_tab} | null}``, this project's drafts.
* ``PUT /api/drafts`` with ``{"tabs", "active_tab", "known"?}``: 200 ``{tabs}``. ``known`` lists every tab id the page
  has had; stored tabs it does not name (another page's) are kept after the page's own.

A tab with any other field is ``studio.invalid_request``: a draft has no place for a parameter value or a result.
``studio.drafts_too_large`` (413) above the caps of :mod:`tabrion_studio.services.drafts`, and
``studio.state_unavailable`` (503) when the state database cannot be used. Both routes sit behind the access
middleware (session cookie and CSRF header; a change needs the Origin check and JSON).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.problems import problem_response
from tabrion_studio.services.drafts import MAX_KNOWN, MAX_TABS, MAX_TOTAL_CHARS, Drafts, DraftsError
from tabrion_studio.services.state import StateUnavailable

#: Every text as JSON (an escape can make a character up to six bytes long), plus the ids, titles, profiles, script
#: paths and the known ids.
MAX_BODY = 6 * MAX_TOTAL_CHARS + 6 * (MAX_TABS * 1400 + MAX_KNOWN * 70)


async def _answer(call: Any, *args: Any) -> Response:
    try:
        result = await asyncio.to_thread(call, *args)
    except (DraftsError, StateUnavailable) as exc:
        return problem_response(exc.code)
    return JSONResponse(result)


def add_draft_routes(app: FastAPI, drafts: Drafts) -> None:
    @app.get("/api/drafts")
    async def get_drafts() -> Response:
        return await _answer(drafts.read)

    @app.put("/api/drafts")
    async def put_drafts(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return problem_response("studio.drafts_too_large")
        try:
            doc = await asyncio.to_thread(json.loads, raw)
        except (ValueError, RecursionError):
            doc = None
        if not isinstance(doc, dict) or any(key not in ("tabs", "active_tab", "known") for key in doc):
            return problem_response("access.bad_request")
        return await _answer(drafts.save, doc.get("tabs"), doc.get("active_tab"), doc.get("known"))
