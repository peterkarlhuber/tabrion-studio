"""Draft routes of the SQL editor (TD-033, review F-5): the editor's tabs kept in the state database.

* ``GET /api/drafts``: ``{drafts: {tabs: [{id, title, profile_id, sql, saved_sql, script_path?, script_digest?,
  script_crlf?}], active_tab} | null}``, this project's drafts.
* ``PUT /api/drafts`` with ``{"tabs", "active_tab", "known"?}``: 200 ``{tabs}``. ``known`` lists every tab id the page
  has had; stored tabs it does not name (another page's) are kept after the page's own.

A body with far more values than a drafts document can have is refused before it is parsed
(``access.bad_request``). A tab with any other field, or a text that is not valid Unicode (a lone surrogate), is
``studio.invalid_request``: a draft has no place for a parameter value or a result.
``studio.drafts_too_large`` (413) above the caps of :mod:`tabrion_studio.services.drafts`, and
``studio.state_unavailable`` (503) when the state database cannot be used. Both routes sit behind the access
middleware (session cookie and CSRF header; a change needs the Origin check and JSON).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.problems import problem_response
from tabrion_studio.services.drafts import MAX_KNOWN, MAX_TABS, MAX_TOTAL_CHARS, Drafts, DraftsError
from tabrion_studio.services.state import StateUnavailable

#: Every text as JSON (an escape can make a character up to six bytes long), plus the ids, titles, profiles, script
#: paths and the known ids.
MAX_BODY = 6 * MAX_TOTAL_CHARS + 6 * (MAX_TABS * 1400 + MAX_KNOWN * 70)
#: JSON strings, matched without backtracking (an unterminated one is left as it is and fails the parse).
_STRING = re.compile(rb'"(?:[^"\\]++|\\.)*+"', re.DOTALL)
#: The most structural characters (``{ [ , :`` outside strings) a valid document has: 32 tabs of 8 fields, 1024 known
#: ids and the top level, with room to spare. A body of millions of tiny values would otherwise cost hundreds of MB
#: to parse before the caps refuse it (review finding 4).
MAX_STRUCTURE = 4096


def _parse(raw: bytes) -> object:
    """The document, or None when it is not JSON or has far more values than a drafts document can have."""
    skeleton = _STRING.sub(b'""', raw)
    if sum(skeleton.count(char) for char in (b"{", b"[", b",", b":")) > MAX_STRUCTURE:
        return None
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


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
        doc = await asyncio.to_thread(_parse, raw)
        if not isinstance(doc, dict) or any(key not in ("tabs", "active_tab", "known") for key in doc):
            return problem_response("access.bad_request")
        return await _answer(drafts.save, doc.get("tabs"), doc.get("active_tab"), doc.get("known"))
