"""Migration status and code trust routes (03 sections 2 F and 6, STU-015, threat T-16).

* ``GET /api/project/code-trust``: whether Studio may run the project's Alembic revision files: ``{state, trusted,
  format, config_file, digest, script_location, revision_files, issue, notice}``; ``state`` is ``none``,
  ``untrusted``, ``changed``, ``trusted`` or ``invalid``. ``notice`` is the fixed text the trust dialog shows.
* ``POST /api/project/code-trust`` with ``{"digest": "<the digest the dialog showed>"}``: 204, or a problem
  (``studio.trust_stale`` when the files changed meanwhile, ``studio.migrations_invalid``, ``studio.no_migrations``).
* ``DELETE /api/project/code-trust`` (with a JSON body, like every changing request): 204; the code is not trusted
  any more.
* ``GET /api/connections/{id}/migrations``: the status per format (:class:`tabrion_studio.services.migrations
  .Migrations`). It reads; Studio never migrates.

The routes sit behind the session and, for ``POST`` and ``DELETE``, the CSRF check of the access middleware.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.config.code_trust import CodeTrustError
from tabrion_studio.problems import problem_response
from tabrion_studio.services.migrations import Migrations

logger = logging.getLogger("tabrion_studio.migrations")

#: What the trust dialog says (fixed text; the page shows it with the file list).
NOTICE = (
    "To read the Alembic revisions, Python imports the project's revision files, which runs their code. Studio "
    "does this only when you trust this project's code, in a separate process that gets only the open "
    "connection's address and password and stops after 30 seconds. Trust only code you would run yourself with "
    "alembic heads. Any change to alembic.ini or a revision file needs your trust again."
)
_MAX_BODY = 4096


def add_migration_routes(app: FastAPI, migrations: Migrations) -> None:
    code = migrations.code

    @app.get("/api/project/code-trust")
    async def get_code_trust() -> Response:
        state = await asyncio.to_thread(code.load)
        return JSONResponse({**state.to_dict(code.project), "notice": NOTICE})

    @app.post("/api/project/code-trust", status_code=204)
    async def post_code_trust(request: Request) -> Response:
        raw = await request.body()
        try:
            doc = json.loads(raw) if len(raw) <= _MAX_BODY else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            return problem_response("access.bad_request")
        try:
            await asyncio.to_thread(code.trust, doc.get("digest"))
        except CodeTrustError as exc:
            logger.info("code trust refused", extra={"event": "studio.code_trust_refused", "code": exc.code})
            return problem_response(exc.code)
        logger.info("project code trusted", extra={"event": "studio.code_trusted"})
        return Response(status_code=204)

    @app.delete("/api/project/code-trust", status_code=204)
    async def delete_code_trust() -> Response:
        try:
            await asyncio.to_thread(code.revoke)
        except CodeTrustError as exc:
            return problem_response(exc.code)
        logger.info("project code trust revoked", extra={"event": "studio.code_trust_revoked"})
        return Response(status_code=204)

    @app.get("/api/connections/{connection_id}/migrations")
    async def get_migrations(connection_id: str) -> Response:
        try:
            doc = await asyncio.to_thread(migrations.status, connection_id)
        except Exception as exc:
            refused = connection_problem(exc)
            if refused is None:
                raise
            return refused
        logger.info(
            "migration status",
            extra={"event": "studio.migration_status", "formats": [item["format"] for item in doc["formats"]]},
        )
        return JSONResponse(doc)
