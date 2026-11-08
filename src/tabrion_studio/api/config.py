"""Configuration routes (03 section 6, STU-006).

* ``GET /api/project``: the project folder, the trust state of ``.tabrion/studio.toml`` (``none``, ``untrusted``,
  ``changed``, ``trusted``, ``invalid``), its SHA-256 digest, what the trust dialog D-01 shows (``review``), the
  difference to the trusted version (``diff``) and the issues (key paths and codes, never values).
* ``POST /api/project/trust`` with ``{"digest": "<the digest the dialog showed>"}``: 204, or a problem
  (``studio.trust_stale`` when the file changed meanwhile, ``studio.project_invalid``, ``studio.no_project_file``).
* ``GET /api/profiles``: the user's profiles and, only when the project file is trusted, the project's. Secrets are
  references only (``{"source": "env", "target": "PGPASSWORD"}``); the value is never read here (STU-007).
* ``GET /api/settings``: the effective settings and the issues of ``settings.toml``.
* ``GET /api/secrets``: whether a usable OS keyring exists (without one, ``keyring:`` profiles need a secret entered
  for this session; there is no plain-text fallback, STU-007).
* ``PUT /api/profiles/{id}/secret`` with ``{"secret": "...", "store": "keyring" | "session"}``: 204; stores an
  entered secret in the keyring or for this session only. ``DELETE`` removes it. No answer ever contains a secret;
  ``GET /api/profiles`` shows only ``secret: {source, has_secret}`` per profile.

All of them sit behind the access middleware (session cookie and CSRF header).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.config.project import ProjectConfig, ProjectState, TrustError
from tabrion_studio.config.schema import Issue, Profile
from tabrion_studio.config.secrets import SecretError, Secrets, SecretTarget, keyring_key
from tabrion_studio.config.user import UserConfig
from tabrion_studio.problems import problem_response

logger = logging.getLogger("tabrion_studio.config")
_MAX_BODY = 4096


@dataclass
class ProfileEntry:
    """One usable profile: its API id (``user:<name>`` or ``project:<name>``), where it comes from, its secret."""

    id: str
    source: str
    profile: Profile
    secret: SecretTarget


@dataclass
class Configuration:
    project: ProjectConfig
    user: UserConfig
    secrets: Secrets = field(default_factory=Secrets)

    def load_project(self) -> ProjectState:
        """The project's state; an unexpected error makes it invalid instead of failing the route (review T-3)."""
        try:
            return self.project.load()
        except Exception:
            logger.exception("cannot read the project profiles", extra={"event": "studio.project_unreadable"})
            return ProjectState("invalid", issues=[Issue("file", "config.unreadable")])

    def entries(self, project: ProjectState | None = None) -> list[ProfileEntry]:
        """The user's profiles and, only while the project file is trusted, the project's."""
        project = self.load_project() if project is None else project
        config_dir = self.user.config_dir
        entries = [
            ProfileEntry(
                f"user:{p.name}",
                "user",
                p,
                # The user's own profiles.toml: a relative file: path starts in the configuration folder.
                SecretTarget(p.password, "user", keyring_key("user", p, ""), config_dir, None),
            )
            for p in self.user.profiles().profiles
        ]
        if project.state == "trusted":
            folder = self.project.project
            entries += [
                ProfileEntry(
                    f"project:{p.name}",
                    "project",
                    p,
                    # C-3: a project's file: secret must stay inside the project or the configuration folder.
                    SecretTarget(
                        p.password,
                        "project",
                        keyring_key("project", p, self.project.key),
                        folder,
                        [folder, config_dir],
                    ),
                )
                for p in project.profiles
            ]
        return entries

    def entry(self, profile_id: str) -> ProfileEntry:
        for entry in self.entries():
            if entry.id == profile_id:
                return entry
        raise SecretError("studio.profile_not_found")

    def resolve_secret(self, profile_id: str) -> str:
        """The secret of a profile, resolved now (STU-009 calls this when it connects); raises :class:`SecretError`."""
        return self.secrets.resolve(self.entry(profile_id).secret)


def _json_body(raw: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(raw) if len(raw) <= _MAX_BODY else None
    except (ValueError, RecursionError):
        doc = None
    return doc if isinstance(doc, dict) else None


def add_config_routes(app: FastAPI, config: Configuration) -> None:
    load_project = config.load_project

    @app.get("/api/project")
    async def get_project() -> Response:
        return JSONResponse(load_project().to_dict(config.project.project))

    @app.post("/api/project/trust", status_code=204)
    async def post_trust(request: Request) -> Response:
        doc = _json_body(await request.body())
        if doc is None:
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
        project = load_project()  # a hostile project file never hides the user's own profiles
        profiles = [
            {
                "id": entry.id,
                "source": entry.source,
                **entry.profile.to_dict(),
                # Never the value: only where it comes from and, where that is known without reading it, whether
                # it is there (03 section 4.4).
                "secret": {
                    "source": None if entry.secret.ref is None else entry.secret.ref.source,
                    "has_secret": config.secrets.has_secret(entry.secret),
                },
            }
            for entry in config.entries(project)
        ]
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

    @app.get("/api/secrets")
    async def get_secrets() -> Response:
        return JSONResponse({"keyring": {"available": config.secrets.keyring.available}})

    @app.put("/api/profiles/{profile_id:path}/secret", status_code=204)
    async def put_secret(profile_id: str, request: Request) -> Response:
        doc = _json_body(await request.body())
        if doc is None:
            return problem_response("access.bad_request")
        try:
            config.secrets.store(config.entry(profile_id).secret, doc.get("secret"), doc.get("store"))
        except SecretError as exc:
            logger.info("secret not stored", extra={"event": "studio.secret_refused", "code": exc.code})
            return problem_response(exc.code)
        logger.info("secret stored", extra={"event": "studio.secret_stored"})
        return Response(status_code=204)

    @app.delete("/api/profiles/{profile_id:path}/secret", status_code=204)
    async def delete_secret(profile_id: str) -> Response:
        try:
            config.secrets.forget(config.entry(profile_id).secret)
        except SecretError as exc:
            return problem_response(exc.code)
        logger.info("secret removed", extra={"event": "studio.secret_removed"})
        return Response(status_code=204)
