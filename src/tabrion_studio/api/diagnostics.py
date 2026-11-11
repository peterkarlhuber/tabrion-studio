"""Diagnostics route (03 sections 2 G and 6, STU-014). It reads; nothing here changes a database.

* ``POST /api/connections/{id}/diagnostics``: runs the diagnosis of an open connection and answers
  ``{connection_id, engine, environment, ran_at, findings: [{code, severity, title, detail, remedy, source}],
  counts: {error, warning, info}}``. A ``POST`` because it runs checks against the database (``PRAGMA quick_check``
  on SQLite can take a while), never because it changes anything; the access middleware asks for the CSRF header.
  A diagnosis stops at the connection's statement timeout; the checks it could not run are named in ``STU-DX-008``.
* ``POST /api/connections/{id}/diagnostics/cancel``: 202 ``{"canceled": true|false}``; stops the running diagnoses
  of that connection within a second (M-1 of ``stu-014-review.md``). Their answers then carry ``STU-DX-008``.

Errors are the problems of the connection routes. The work runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.services.diagnostics import Diagnostics

logger = logging.getLogger("tabrion_studio.diagnostics")


def add_diagnostics_routes(app: FastAPI, diagnostics: Diagnostics) -> None:
    @app.post("/api/connections/{connection_id}/diagnostics")
    async def run_diagnostics(connection_id: str) -> Response:
        try:
            doc = await asyncio.to_thread(diagnostics.run, connection_id)
        except Exception as exc:
            refused = connection_problem(exc)
            if refused is None:
                raise
            return refused
        logger.info("diagnostics", extra={"event": "studio.diagnostics", **doc["counts"]})
        return JSONResponse(doc)

    @app.post("/api/connections/{connection_id}/diagnostics/cancel", status_code=202)
    async def cancel_diagnostics(connection_id: str) -> Response:
        return JSONResponse({"canceled": diagnostics.cancel(connection_id)}, status_code=202)
