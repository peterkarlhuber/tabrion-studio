"""Script routes (03 section 2 C, STU-020): ``.sql`` files in the project folder. Nothing here runs a script.

* ``GET /api/scripts``: ``{scripts: [{path, size, modified_at}], truncated}``; ``path`` is relative to the project
  folder, with ``/``.
* ``GET /api/scripts/file?path=``: ``{path, content, digest}`` (UTF-8 text, at most 1 MB).
* ``PUT /api/scripts/file`` with ``{"path", "content", "digest": "<what the page read>" | null, "overwrite"?}``:
  200 ``{path, digest}``.

Refusals: ``studio.script_invalid`` (not a ``.sql`` path inside the project, a link, a hidden folder),
``studio.script_not_found``, ``studio.script_unreadable`` (not UTF-8), ``studio.script_too_large``,
``studio.script_exists`` and ``studio.script_stale`` (409: the page asks before it overwrites),
``studio.script_folder_missing``. No answer names a path the request did not send; nothing is logged with a path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.problems import problem_response
from tabrion_studio.services.scripts import MAX_PATH_CHARS, MAX_SCRIPT_BYTES, ScriptError, Scripts

#: The script's text as JSON (escapes can make it up to six times as long) plus the other fields.
MAX_BODY = 6 * MAX_SCRIPT_BYTES + 8 * 1024


async def _answer(call: Any, *args: Any, **kwargs: Any) -> Response:
    try:
        result = await asyncio.to_thread(call, *args, **kwargs)
    except ScriptError as exc:
        return problem_response(exc.code)
    return JSONResponse(result)


def add_script_routes(app: FastAPI, scripts: Scripts) -> None:
    @app.get("/api/scripts")
    async def get_scripts() -> Response:
        return await _answer(scripts.list)

    @app.get("/api/scripts/file")
    async def get_script(path: str = Query(..., max_length=MAX_PATH_CHARS)) -> Response:
        return await _answer(scripts.read, path)

    @app.put("/api/scripts/file")
    async def put_script(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return problem_response("studio.script_too_large")
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError):
            doc = None
        if not isinstance(doc, dict) or any(key not in ("path", "content", "digest", "overwrite") for key in doc):
            return problem_response("access.bad_request")
        return await _answer(
            scripts.save, doc.get("path"), doc.get("content"), doc.get("digest"), overwrite=doc.get("overwrite", False)
        )
