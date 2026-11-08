"""Configuration routes (03 section 6, STU-006).

* ``GET /api/project``: the project folder, the trust state of ``.tabrion/studio.toml`` (``none``, ``untrusted``,
  ``changed``, ``trusted``, ``invalid``), its SHA-256 digest, what the trust dialog D-01 shows (``review``), the
  difference to the trusted version (``diff``) and the issues (key paths and codes, never values).
* ``POST /api/project/trust`` with ``{"digest": "<the digest the dialog showed>"}``: 204, or a problem
  (``studio.trust_stale`` when the file changed meanwhile, ``studio.project_invalid``, ``studio.no_project_file``).
* ``GET /api/profiles``: the user's profiles and, only when the project file is trusted, the project's. Secrets are
  references only (``{"source": "env", "target": "PGPASSWORD"}``); the value is never read here (STU-007).
* ``GET /api/settings``: the effective settings and the issues of ``settings.toml``.

All of them sit behind the access middleware (session cookie and CSRF header).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.config.project import ProjectConfig, TrustError
from tabrion_studio.config.user import UserConfig
from tabrion_studio.problems import problem_response

logger = logging.getLogger("tabrion_studio.config")
_MAX_BODY = 4096


@dataclass
class Configuration:
    project: ProjectConfig
    user: UserConfig


def _profile_entries(source: str, profiles: list[Any]) -> list[dict[str, Any]]:
    return [{"id": f"{source}:{p.name}", "source": source, **p.to_dict()} for p in profiles]


def add_config_routes(app: FastAPI, config: Configuration) -> None:
    @app.get("/api/project")
    async def get_project() -> Response:
        return JSONResponse(config.project.load().to_dict(config.project.project))

    @app.post("/api/project/trust", status_code=204)
    async def post_trust(request: Request) -> Response:
        raw = await request.body()
        try:
            doc = json.loads(raw) if len(raw) <= _MAX_BODY else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            return problem_response("access.bad_request")
        try:
            config.project.trust(doc.get("digest"))
        except TrustError as exc:
            logger.info("project trust refused", extra={"event": "studio.trust_refused", "code": exc.code})
            return problem_response(exc.code)
        logger.info("project profiles trusted", extra={"event": "studio.project_trusted"})
        return Response(status_code=204)

    @app.get("/api/profiles")
    async def get_profiles() -> Response:
        user = config.user.profiles()
        project = config.project.load()
        profiles = _profile_entries("user", user.profiles)
        if project.state == "trusted":
            profiles += _profile_entries("project", project.profiles)
        return JSONResponse(
            {
                "profiles": profiles,
                "project_state": project.state,
                "issues": {"user": [issue.to_dict() for issue in user.issues]},
            }
        )

    @app.get("/api/settings")
    async def get_settings() -> Response:
        settings, issues = config.user.settings()
        return JSONResponse({"settings": settings.to_dict(), "issues": [issue.to_dict() for issue in issues]})
