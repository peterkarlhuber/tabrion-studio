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
* ``POST /api/profiles`` with ``{"profile": {...}, "digest": "<user file digest or null>"}``: 201 ``{"id", "digest"}``;
  adds a profile to the user's ``profiles.toml`` (STU-008). ``PUT /api/profiles`` with ``{"id", "profile", "digest"}``
  changes one (the name stays), ``DELETE /api/profiles`` with ``{"id", "digest"}`` removes one and its stored secret.
  The id is in the body, not the path: a profile name may contain ``/`` and must never be read as ``.../secret``.
  The profile has the keys of the file (``password`` is a reference such as ``"keyring:"``); a refused one gives
  ``studio.profile_invalid`` with ``issues`` (``profile.<key>`` and a ``config.*`` code). Project profiles are never
  changed (``studio.profile_read_only``). ``"overwrite": true`` confirms rewriting a file written by hand.
* ``PUT /api/settings`` with ``{"settings": {...}, "digest"}``: 200 ``{"digest"}``; writes ``settings.toml``.
* ``PUT /api/profiles/{id}/secret`` with ``{"secret": "...", "store": "keyring" | "session"}``: 204; stores an
  entered secret in the keyring or for this session only. ``DELETE`` removes it. No answer ever contains a secret;
  ``GET /api/profiles`` shows only ``secret: {source, has_secret}`` per profile.

All of them sit behind the access middleware (session cookie and CSRF header).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path, PurePath
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.config.locations import lexical_target, safe_realpath
from tabrion_studio.config.project import ProjectConfig, ProjectState, TrustError
from tabrion_studio.config.schema import SCHEMA_VERSION, Issue, Profile, Settings, validate_profiles, validate_settings
from tabrion_studio.config.secrets import SecretError, Secrets, SecretTarget, keyring_key
from tabrion_studio.config.user import UserConfig
from tabrion_studio.config.writer import PROFILE_KEYS, SaveError
from tabrion_studio.problems import problem_response

logger = logging.getLogger("tabrion_studio.config")
_MAX_BODY = 4096
#: The largest body of a profile or settings form (the largest body cap of any route but queries and exports).
MAX_FORM_BODY = 16 * 1024
_SETTINGS_KEYS = frozenset({"language", "theme", "density", "log_sql", "limits", "history"})
_LIMIT_KEYS = frozenset({"max_rows", "statement_timeout_ms", "max_result_mb"})
_HISTORY_KEYS = frozenset({"enabled", "retention_days"})


class FormError(Exception):
    """A profile or settings form that is not valid: the problem code and its issues (fixed key names)."""

    def __init__(self, code: str, issues: list[tuple[str, str]]) -> None:
        super().__init__(code)
        self.code = code
        self.issues = issues


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
                _user_target(self, p),
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

    def resolve_entry_secret(self, entry: ProfileEntry) -> str:
        """The secret of ``entry`` as read once: the registry connects to this entry's target with it (STU-009)."""
        return self.secrets.resolve(entry.secret)

    def database_file(self, entry: ProfileEntry) -> Path:
        """The real path of the SQLite profile ``entry``'s file, checked now (STU-009 opens exactly this path).

        ``entry`` is the read of the profile that the caller checked (the environment for D-02, the target): the path
        comes from it, never from a second read of the file, so a file rewritten in between cannot put another
        database behind a check that passed (C-1 of the #48 review). A project profile goes through
        ``ProjectConfig.database_file`` (trust and containment again, review T-1; :class:`TrustError`), which must
        find the same profile as ``entry`` or refuses. A relative path of the user's own profile is relative to the
        configuration folder, like its ``file:`` references; a link to a network or device path is refused
        (``studio.database_unsafe``).
        """
        if entry.profile.path is None:
            raise SecretError("studio.profile_not_found")
        if entry.source == "project":
            return self.project.database_file(entry.profile.name, expected=entry.profile)
        target = lexical_target(entry.profile.path, PurePath(os.path.abspath(self.user.config_dir)))
        real = None if target is None else safe_realpath(Path(target))
        if real is None:
            raise TrustError("studio.database_unsafe")
        return real

    def limits(self) -> Settings:
        """The effective settings (the defaults where ``settings.toml`` is missing or not valid)."""
        return self.user.settings()[0]


def _json_body(raw: bytes, limit: int = _MAX_BODY) -> dict[str, Any] | None:
    try:
        doc = json.loads(raw) if len(raw) <= limit else None
    except (ValueError, RecursionError):
        doc = None
    return doc if isinstance(doc, dict) else None


def _user_target(config: Configuration, profile: Profile) -> SecretTarget:
    return SecretTarget(profile.password, "user", keyring_key("user", profile, ""), config.user.config_dir, None)


def form_profile(raw: object) -> Profile:
    """The profile of a form (the keys of the file); :class:`FormError` with the issues if it is not valid."""
    if not isinstance(raw, dict):
        raise FormError("studio.profile_invalid", [("profile", "config.type")])
    if any(key not in PROFILE_KEYS for key in raw):
        # Never the key itself: the pointer names only fixed keys.
        raise FormError("studio.profile_invalid", [("profile", "config.unknown_key")])
    parsed = validate_profiles(
        {"version": SCHEMA_VERSION, "profile": [{k: v for k, v in raw.items() if v is not None}]}
    )
    issues = [(_form_pointer(issue.pointer), issue.code) for issue in parsed.issues][:32]
    if issues or len(parsed.profiles) != 1:
        raise FormError("studio.profile_invalid", issues or [("profile", "config.type")])
    return parsed.profiles[0]


def _form_pointer(pointer: str) -> str:
    """``profile[0].host`` -> ``profile.host`` (the form has one profile; every key is one of PROFILE_KEYS)."""
    head, _, key = pointer.partition(".")
    return "profile" if not key or key not in PROFILE_KEYS or not head.startswith("profile") else f"profile.{key}"


def form_settings(raw: object, current: Settings) -> Settings:
    """``current`` with the values of the form; :class:`FormError` if one is not valid."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("limits", {}), dict)
        or not isinstance(raw.get("history", {}), dict)
    ):
        raise FormError("studio.settings_invalid", [("settings", "config.type")])
    limits = raw.get("limits", {})
    history = raw.get("history", {})
    if (
        any(key not in _SETTINGS_KEYS for key in raw)
        or any(key not in _LIMIT_KEYS for key in limits)
        or any(key not in _HISTORY_KEYS for key in history)
    ):
        raise FormError("studio.settings_invalid", [("settings", "config.unknown_key")])
    doc: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "language": raw.get("language", current.language),
        "theme": raw.get("theme", current.theme),
        "density": raw.get("density", current.density),
        "log_sql": raw.get("log_sql", current.log_sql),
        "limits": {
            "max_rows": limits.get("max_rows", current.max_rows),
            "statement_timeout_ms": limits.get("statement_timeout_ms", current.statement_timeout_ms),
            "max_result_mb": limits.get("max_result_mb", current.max_result_mb),
        },
        "history": {
            "enabled": history.get("enabled", current.history_enabled),
            "retention_days": history.get("retention_days", current.history_retention_days),
        },
    }
    settings, issues = validate_settings(doc)
    if issues:
        raise FormError("studio.settings_invalid", [(issue.pointer, issue.code) for issue in issues][:32])
    return settings


def _user_name(config: Configuration, profile_id: object) -> str:
    """The name in a user profile id; project profiles are read-only."""
    if isinstance(profile_id, str) and profile_id.startswith("user:"):
        return profile_id[5:]
    if isinstance(profile_id, str) and profile_id.startswith("project:"):
        raise SaveError("studio.profile_read_only")
    raise SaveError("studio.profile_not_found")


def add_config_routes(
    app: FastAPI, config: Configuration, *, settings_saved: Callable[[], object] | None = None
) -> None:
    """``settings_saved`` runs after ``settings.toml`` was written (the history drops what the new retention
    expires)."""
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
                # A save names the digest it read (studio.config_stale otherwise, STU-008).
                "user_file": {"digest": user.digest, "hand_written": not user.canonical},
            }
        )

    @app.get("/api/settings")
    async def get_settings() -> Response:
        settings, issues, file_digest, canonical = config.user.settings_file()
        return JSONResponse(
            {
                "settings": settings.to_dict(),
                "issues": [issue.to_dict() for issue in issues],
                "file": {"digest": file_digest, "hand_written": not canonical},
            }
        )

    async def save(request: Request, action: Any) -> Response:
        """Run one save of the user's files; every refusal is a problem with a fixed text."""
        doc = _json_body(await request.body(), MAX_FORM_BODY)
        if doc is None or "digest" not in doc:
            return problem_response("access.bad_request")
        try:
            result: Response = action(doc, doc.get("digest"), doc.get("overwrite") is True)
        except FormError as exc:
            return problem_response(exc.code, issues=exc.issues)
        except (SaveError, SecretError) as exc:
            logger.info("configuration not saved", extra={"event": "studio.config_refused", "code": exc.code})
            return problem_response(exc.code)
        except ValueError:
            # The file would not read back (or not encode): nothing was written (L-2 of stu-008-review.md).
            logger.warning("configuration not written", extra={"event": "studio.config_not_storable"})
            return problem_response("studio.config_not_storable")
        return result

    def forget_after_save(target: SecretTarget) -> bool:
        """Remove a saved password after the file was written; False if the keyring kept it (L-3)."""
        try:
            config.secrets.forget(target)
        except SecretError:
            return False
        return True

    def create(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        profile = form_profile(doc.get("profile"))

        def change(profiles: list[Profile]) -> list[Profile]:
            if any(p.name == profile.name for p in profiles):
                raise SaveError("studio.profile_exists")
            if len(profiles) >= 200:
                raise FormError("studio.profile_invalid", [("profile", "config.too_many")])
            return [*profiles, profile]

        new_digest = config.user.save_profiles(change, expected, overwrite=overwrite)
        logger.info("profile created", extra={"event": "studio.profile_created"})
        return JSONResponse({"id": f"user:{profile.name}", "digest": new_digest}, status_code=201)

    def update(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        name = _user_name(config, doc.get("id"))
        profile = form_profile(doc.get("profile"))
        if profile.name != name:
            raise FormError("studio.profile_invalid", [("profile.name", "config.rename_unsupported")])
        old: list[Profile] = []

        def change(profiles: list[Profile]) -> list[Profile]:
            old.extend(p for p in profiles if p.name == name)
            if not old:
                raise SaveError("studio.profile_not_found")
            return [profile if p.name == name else p for p in profiles]

        new_digest = config.user.save_profiles(change, expected, overwrite=overwrite)
        before = _user_target(config, old[0])
        if before.ref != profile.password or before.key != _user_target(config, profile).key:
            # The stored secret belonged to the old reference or target (C-1): it never serves the new one.
            removed = forget_after_save(before)
        else:
            removed = True
        logger.info("profile changed", extra={"event": "studio.profile_changed"})
        return JSONResponse({"id": f"user:{name}", "digest": new_digest, "secret_removed": removed})

    def remove(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        name = _user_name(config, doc.get("id"))
        old: list[Profile] = []

        def change(profiles: list[Profile]) -> list[Profile]:
            old.extend(p for p in profiles if p.name == name)
            if not old:
                raise SaveError("studio.profile_not_found")
            return [p for p in profiles if p.name != name]

        new_digest = config.user.save_profiles(change, expected, overwrite=overwrite)
        removed = forget_after_save(_user_target(config, old[0]))
        logger.info("profile removed", extra={"event": "studio.profile_removed"})
        return JSONResponse({"digest": new_digest, "secret_removed": removed})

    def put_settings_action(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        current, _ = config.user.settings()
        settings = form_settings(doc.get("settings"), current)
        new_digest = config.user.save_settings(settings, expected, overwrite=overwrite)
        logger.info("settings saved", extra={"event": "studio.settings_saved"})
        if settings_saved is not None:
            settings_saved()
        return JSONResponse({"digest": new_digest, "settings": settings.to_dict()})

    @app.post("/api/profiles", status_code=201)
    async def post_profile(request: Request) -> Response:
        return await save(request, create)

    @app.put("/api/profiles")
    async def put_profile(request: Request) -> Response:
        return await save(request, update)

    @app.delete("/api/profiles")
    async def delete_profile(request: Request) -> Response:
        return await save(request, remove)

    @app.put("/api/settings")
    async def put_settings(request: Request) -> Response:
        return await save(request, put_settings_action)

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
