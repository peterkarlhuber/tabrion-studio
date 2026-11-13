"""Explorer routes (03 section 6, STU-010). Every route reads; nothing here changes a database.

* ``GET /api/connections/{id}/catalog[?refresh=true]``: ``{connection_id, engine, schemas, objects: [{schema, name,
  kind}], built_at, cached}``; ``kind`` is ``table``, ``view``, ``materialized_view``, ``sequence`` or
  ``foreign_table``. Cached per connection until ``refresh``. ``?cached_only=true`` (STU-020) answers only a cached
  catalog, else ``studio.catalog_not_loaded`` (404): the SQL editor's completion never reads a PROD catalog unasked.
* ``GET /api/connections/{id}/tables/{schema}/{name}``: columns, primary key, foreign keys out (``foreign_keys``) and
  in (``referenced_by``), unique and check constraints, indexes and the row estimate (PostgreSQL). An object outside
  the catalog is ``studio.object_not_found``.
* ``GET /api/connections/{id}/table?schema=&name=``: the same detail, for names with ``/`` (I-6).
* ``GET /api/connections/{id}/extensions`` (PostgreSQL) and ``GET /api/connections/{id}/sqlite-info`` (SQLite); the
  other engine gets ``studio.explorer_engine``.

STU-022 (:mod:`tabrion_studio.services.definitions`), each also as ``/api/connections/{id}/<what>?schema=&name=`` for
names with ``/``:

* ``GET /api/connections/{id}/tables/{schema}/{name}/ddl``: ``{connection_id, schema, name, kind, engine, owner,
  statements, ddl, notes}``; PostgreSQL rebuilt from the catalog in ``pg_dump --schema-only`` form, SQLite the stored
  text.
* ``GET /api/connections/{id}/tables/{schema}/{name}/policies`` (PostgreSQL): ``{rls_enabled, rls_forced, owner,
  current_role, rls_applies, policies: [{name, permissive, command, roles, using, with_check}]}``.
* ``GET /api/connections/{id}/tables/{schema}/{name}/row-count`` (SQLite): ``count(*)`` on request, with a timeout
  (``db.timeout`` past it).
* ``GET /api/connections/{id}/row-estimates[?schema=]`` (PostgreSQL): ``pg_class.reltuples`` of every table and
  materialized view.

The connection is checked through ``ConnectionRegistry.get`` on every request; errors are the problems of the
connection routes (``studio.connection_*``, ``db.<kind>``). Reflection runs in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse
from tabrion.errors import Kind

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.problems import db_kind_response, problem_response
from tabrion_studio.services.definitions import CountTimeout, Definitions
from tabrion_studio.services.explorer import Explorer, ExplorerError

#: Longer names do not exist in PostgreSQL (63 bytes) and would only make the catalog lookup slower.
MAX_NAME = 256


def add_explorer_routes(app: FastAPI, explorer: Explorer) -> None:
    async def answer(call: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(call, *args, **kwargs))
        except ExplorerError as exc:
            return problem_response(exc.code)
        except CountTimeout:
            return db_kind_response(Kind.TIMEOUT)
        except Exception as exc:
            refused = connection_problem(exc)
            if refused is None:
                raise
            return refused

    @app.get("/api/connections/{connection_id}/catalog")
    async def get_catalog(connection_id: str, refresh: bool = False, cached_only: bool = False) -> Response:
        if refresh and cached_only:
            return problem_response("studio.invalid_request")
        return await answer(explorer.catalog, connection_id, refresh=refresh, cached_only=cached_only)

    @app.get("/api/connections/{connection_id}/tables/{schema}/{name}")
    async def get_table(connection_id: str, schema: str, name: str) -> Response:
        if len(schema) > MAX_NAME or len(name) > MAX_NAME:
            return problem_response("studio.object_not_found")
        return await answer(explorer.table, connection_id, schema, name)

    @app.get("/api/connections/{connection_id}/table")
    async def get_table_by_query(
        connection_id: str,
        schema: str = Query(..., max_length=MAX_NAME),
        name: str = Query(..., max_length=MAX_NAME),
    ) -> Response:
        # I-6 of stu-010-review.md: a name with "/" cannot be a path segment (%2F is a separator to the router).
        return await answer(explorer.table, connection_id, schema, name)

    @app.get("/api/connections/{connection_id}/extensions")
    async def get_extensions(connection_id: str) -> Response:
        return await answer(explorer.extensions, connection_id)

    @app.get("/api/connections/{connection_id}/sqlite-info")
    async def get_sqlite_info(connection_id: str) -> Response:
        return await answer(explorer.sqlite_info, connection_id)

    definitions = Definitions(explorer)

    # STU-022. The query-parameter forms (?schema=&name=) are for names with "/" (I-6 of stu-010-review.md).
    @app.get("/api/connections/{connection_id}/ddl")
    @app.get("/api/connections/{connection_id}/tables/{schema}/{name}/ddl")
    async def get_ddl(connection_id: str, schema: str, name: str) -> Response:
        if len(schema) > MAX_NAME or len(name) > MAX_NAME:
            return problem_response("studio.object_not_found")
        return await answer(definitions.ddl, connection_id, schema, name)

    @app.get("/api/connections/{connection_id}/policies")
    @app.get("/api/connections/{connection_id}/tables/{schema}/{name}/policies")
    async def get_policies(connection_id: str, schema: str, name: str) -> Response:
        if len(schema) > MAX_NAME or len(name) > MAX_NAME:
            return problem_response("studio.object_not_found")
        return await answer(definitions.policies, connection_id, schema, name)

    @app.get("/api/connections/{connection_id}/row-count")
    @app.get("/api/connections/{connection_id}/tables/{schema}/{name}/row-count")
    async def get_row_count(connection_id: str, schema: str, name: str) -> Response:
        if len(schema) > MAX_NAME or len(name) > MAX_NAME:
            return problem_response("studio.object_not_found")
        return await answer(definitions.row_count, connection_id, schema, name)

    @app.get("/api/connections/{connection_id}/row-estimates")
    async def get_row_estimates(connection_id: str, schema: str | None = Query(None, max_length=MAX_NAME)) -> Response:
        return await answer(definitions.row_estimates, connection_id, schema)
