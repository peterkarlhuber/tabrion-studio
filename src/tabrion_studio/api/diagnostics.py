"""Diagnostics routes (03 sections 2 G and 6, STU-014, STU-025). They read; nothing here changes a database.

* ``POST /api/connections/{id}/diagnostics``: runs the diagnosis of an open connection and answers
  ``{connection_id, engine, environment, ran_at, findings: [{code, severity, title, detail, remedy, source}],
  counts: {error, warning, info}, backup: {hints: [...], facts: {...}}}``. A ``POST`` because it runs checks against
  the database (``PRAGMA quick_check`` on SQLite can take a while), never because it changes anything; the access
  middleware asks for the CSRF header. A diagnosis stops at the connection's statement timeout; the checks it could
  not run are named in ``STU-DX-008``. ``backup`` holds the backup hints (``STU-BK-*``, STU-025), which are not
  findings and not in ``counts``. The page shows the hints (TD-036), so tabrion's ``TAB-PG-020`` gives way to
  ``STU-BK-010`` here as in the report (D-2 of the delta review of STU-025).
* ``POST /api/connections/{id}/diagnostics/cancel``: 202 ``{"canceled": true|false}``; stops the running diagnoses
  of that connection within a second (M-1 of ``stu-014-review.md``). Their answers then carry ``STU-DX-008``.
* ``POST /api/connections/{id}/diagnostics/report`` with ``{"format": "markdown" | "json"}`` (STU-025): runs a
  diagnosis with its backup hints and answers the report as a file (``Content-Disposition: attachment``), built
  without secrets (:mod:`tabrion_studio.services.diagnosis_report`). ``studio.report_invalid`` for another format or
  body, ``studio.report_unavailable`` when the names it hides cannot be read; the cancel route stops it like a
  diagnosis.

Errors are the problems of the connection routes. The work runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.api.connections import connection_problem
from tabrion_studio.problems import problem_response
from tabrion_studio.services.diagnosis_report import DiagnosisReports, ReportError
from tabrion_studio.services.diagnostics import Diagnostics

logger = logging.getLogger("tabrion_studio.diagnostics")

#: A report request is ``{"format": ...}``; anything larger is refused before it is parsed.
MAX_REPORT_BODY = 1024


def add_diagnostics_routes(app: FastAPI, diagnostics: Diagnostics) -> None:
    reports = DiagnosisReports(diagnostics.registry, diagnostics, clock=diagnostics.clock)

    @app.post("/api/connections/{connection_id}/diagnostics")
    async def run_diagnostics(connection_id: str) -> Response:
        try:
            # TD-036: the view shows backup.hints, so TAB-PG-020 gives way to STU-BK-010 as in the report (D-2).
            doc = await asyncio.to_thread(diagnostics.run, connection_id, backup=True, replace=True)
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

    @app.post("/api/connections/{connection_id}/diagnostics/report")
    async def diagnosis_report(connection_id: str, request: Request) -> Response:
        raw = await request.body()
        try:
            doc = json.loads(raw) if len(raw) <= MAX_REPORT_BODY else None
        except (ValueError, RecursionError):
            doc = None
        if not isinstance(doc, dict):
            return problem_response("studio.report_invalid")
        try:
            report = await asyncio.to_thread(reports.build, connection_id, doc.get("format"))
        except ReportError as exc:
            return problem_response(exc.code)
        except Exception as exc:
            refused = connection_problem(exc)
            if refused is None:
                raise
            return refused
        logger.info("diagnosis report", extra={"event": "studio.diagnosis_report"})
        return Response(
            report.body,
            media_type=report.media_type,
            headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
        )
