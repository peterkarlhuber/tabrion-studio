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
#: One JSON string from its opening quote. Each part is taken possessively, and a string without a closing quote runs
#: to the end of the body, so the match never fails and no byte is looked at twice: a scan of the body is linear in
#: its size (review delta N-1).
_STRING = re.compile(rb'"(?:[^"\\]++|\\.)*+(?:"|\\?\Z)', re.DOTALL)
#: The most structural characters (``{ [ , :`` outside strings) a valid document has: 32 tabs of 8 fields, 1024 known
#: ids and the top level, with room to spare. A body of millions of tiny values would otherwise cost hundreds of MB
#: to parse before the caps refuse it (review finding 4).
MAX_STRUCTURE = 4096
#: The most strings: in a valid document each one is a key before a ``:`` or a value after ``{ [ , :`` (or the whole
#: document), so there is at most one more than the structural characters.
MAX_STRINGS = MAX_STRUCTURE + 1
_STRUCTURE = (b"{", b"[", b",", b":")


def _shape(raw: bytes) -> tuple[int, int]:
    """The structural characters outside strings and the strings of the body, counted until either limit is passed.

    One pass from the start: the bytes between strings are counted in C (``bytes.count``), and each string is skipped
    with :data:`_STRING`. It stops as soon as a limit is passed, so it does at most ``MAX_STRINGS + 1`` steps of Python
    and reads each byte once.
    """
    structure = 0
    strings = 0
    outside = 0
    for match in _STRING.finditer(raw):
        start, end = match.span()
        structure += sum(raw.count(char, outside, start) for char in _STRUCTURE)
        strings += 1
        if structure > MAX_STRUCTURE or strings > MAX_STRINGS:
            return structure, strings
        outside = end
    return structure + sum(raw.count(char, outside) for char in _STRUCTURE), strings


def _parse(raw: bytes) -> object:
    """The document, or None when it is not JSON or has far more values than a drafts document can have."""
    structure, strings = _shape(raw)
    if structure > MAX_STRUCTURE or strings > MAX_STRINGS:
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
