"""Data viewer routes (03 sections 2 D and 6, STU-013). Every route reads; nothing here changes a database.

* ``GET /api/connections/{id}/tables/{schema}/{name}/rows``: one page of rows. Query parameters: ``limit`` (1 to
  1000, default 100), ``sort`` (a column), ``desc`` (``true``), ``filter`` (JSON: ``[{"column", "op", "value"}]``,
  ``op`` one of ``eq``, ``ne``, ``lt``, ``le``, ``gt``, ``ge``, ``contains``, ``starts_with``, ``is_null``,
  ``not_null``) and ``after`` (the ``next`` token of the page before; it keeps that page's sort, filters and limit).
  Answer: ``{columns, rows, truncated_cells, truncated, page, next, paging, hint, sort, desc, limit}``.
* ``GET /api/connections/{id}/tables/{schema}/{name}/cell?page=&row=&column=``: one value of a page in full (up to
  10 MB of its bytes): ``{value, size, truncated}``.
* ``GET /api/connections/{id}/rows?schema=&name=...`` and ``.../cell?schema=&name=...``: the same, for names with
  ``/``.

Errors: ``studio.viewer_*``, ``db.timeout`` (past the connection's statement timeout), ``studio.object_not_found``
and the problems of the connection routes. The work runs in a worker thread
(:class:`tabrion_studio.services.viewer.Viewer` blocks).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from tabrion.errors import Kind

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.api.explorer import MAX_NAME
from tabrion_studio.problems import db_kind_response, problem_response
from tabrion_studio.services.explorer import ExplorerError
from tabrion_studio.services.viewer import MAX_PAGE, PAGE, Viewer, ViewerError, ViewerTimeout

#: A filter of 20 entries with long texts fits easily; longer is not a filter a person types.
MAX_FILTER_CHARS = 64 * 1024
#: The session's CSRF header (``security.sessions.CSRF_HEADER``; the access middleware checked it against the session
#: cookie before any route runs). Its digest binds page tokens to the session (L-4 of ``stu-013-review.md``).
SESSION_HEADER = "X-Tabrion-CSRF"


def session_owner(request: Request) -> str | None:
    """A digest of the request's CSRF value: one per session, never the value itself."""
    value = request.headers.get(SESSION_HEADER)
    return None if value is None else hashlib.sha256(value.encode("utf-8")).hexdigest()


def add_viewer_routes(app: FastAPI, viewer: Viewer) -> None:
    async def answer(call: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(call, *args, **kwargs))
        except (ViewerError, ExplorerError) as exc:
            return problem_response(exc.code)
        except ViewerTimeout:
            return db_kind_response(Kind.TIMEOUT)
        except Exception as exc:
            refused = connection_problem(exc)
            if refused is None:
                raise
            return refused

    # The query-parameter forms (?schema=&name=) are for names with "/" (I-6 of stu-010-review.md).
    @app.get("/api/connections/{connection_id}/rows")
    @app.get("/api/connections/{connection_id}/tables/{schema}/{name}/rows")
    async def get_rows(
        request: Request,
        connection_id: str,
        schema: str,
        name: str,
        limit: int = Query(PAGE, ge=1, le=MAX_PAGE),
        sort: str | None = Query(None, max_length=MAX_NAME),
        desc: bool = False,
        filter: str | None = Query(None, max_length=MAX_FILTER_CHARS),
        after: str | None = Query(None, max_length=64),
    ) -> Response:
        if len(schema) > MAX_NAME or len(name) > MAX_NAME:
            return problem_response("studio.object_not_found")
        return await answer(
            viewer.rows,
            connection_id,
            schema,
            name,
            limit=limit,
            sort=sort,
            desc=desc,
            filters=filter,
            after=after,
            owner=session_owner(request),
        )

    @app.get("/api/connections/{connection_id}/cell")
    @app.get("/api/connections/{connection_id}/tables/{schema}/{name}/cell")
    async def get_cell(
        request: Request,
        connection_id: str,
        schema: str,
        name: str,
        page: str = Query(..., max_length=64),
        row: int = Query(..., ge=0, le=MAX_PAGE),
        column: str = Query(..., max_length=MAX_NAME),
    ) -> Response:
        if len(schema) > MAX_NAME or len(name) > MAX_NAME:
            return problem_response("studio.object_not_found")
        return await answer(viewer.cell, connection_id, schema, name, page, row, column, owner=session_owner(request))
