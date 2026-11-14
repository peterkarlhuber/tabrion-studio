"""The real handlers of :data:`tabrion_studio.api.write.WRITE_ROUTES`, registered only when a write area is on.

See :mod:`tabrion_studio.api.write` for the routes. Every handler first checks that the area of the profile's kind
is on (``write``: Data Editor or Schema Designer; ``admin``: Database Manager); otherwise it answers 405 like the
refusing handler. The environment, the target and the coupling are always read from the user's stored profiles,
never from the request.

Every change of a write or admin profile, its coupling or its secret is recorded in the audit log after it was saved
(:mod:`tabrion_studio.write.audit`; a log that is known to be unusable refuses the change first, and a record that
fails afterwards is kept as a pending marker and reported as ``audit_pending``, review N-4). ``GET /api/write/audit``
lists the newest records and verifies the whole log, read only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from pathlib import Path
from typing import Any, TypeVar

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tabrion_studio.api.config import MAX_FORM_BODY, Configuration, FormError
from tabrion_studio.api.connections import connection_problem
from tabrion_studio.api.write import WRITE_ROUTES
from tabrion_studio.config.schema import (
    WRITE_PROFILE_KEYS,
    Profile,
    WriteProfile,
    coupling_issue,
    parse_write_profile,
)
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.config.write_areas import WriteAreas
from tabrion_studio.config.writer import SaveError
from tabrion_studio.problems import problem_response
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.unlock import WRITE_ID_PREFIX, Unlocks, write_profile_id

logger = logging.getLogger("tabrion_studio.write")
_MAX_BODY = 4096
_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])
#: The issue pointers of a write profile form (``profile.<key>``).
_POINTERS = {
    "config.coupling_unknown": "profile.read_profile",
    "config.coupling_prod": "profile.read_profile",
    "config.not_for_engine": "profile.user",
    "config.coupling_role_required": "profile.user",
    "config.coupling_same_role": "profile.user",
}


def _json(raw: bytes, limit: int = _MAX_BODY) -> dict[str, Any] | None:
    try:
        doc = json.loads(raw) if len(raw) <= limit else None
    except (ValueError, RecursionError):
        doc = None
    return doc if isinstance(doc, dict) else None


def session_of(request: Request) -> str:
    """The digest of the session the access middleware checked for this request (never the cookie's value)."""
    session = request.scope.get("state", {}).get("studio_session")
    if not isinstance(session, str) or not session:
        raise WriteError("studio.write_locked")  # cannot happen behind the middleware: fail closed
    return session


def form_write_profile(raw: object) -> WriteProfile:
    """A write or admin profile from a form; :class:`FormError` (``studio.write_profile_invalid``) if not valid."""
    if not isinstance(raw, dict):
        raise FormError("studio.write_profile_invalid", [("profile", "config.type")])
    if any(key not in WRITE_PROFILE_KEYS for key in raw):
        raise FormError("studio.write_profile_invalid", [("profile", "config.unknown_key")])
    write, issues = parse_write_profile({k: v for k, v in raw.items() if v is not None})
    if write is None or issues:
        pointers = []
        for issue in issues[:32]:
            key = issue.pointer.partition(".")[2]
            pointers.append((f"profile.{key}" if key in WRITE_PROFILE_KEYS else "profile", issue.code))
        raise FormError("studio.write_profile_invalid", pointers or [("profile", "config.type")])
    return write


def _coupling(write: WriteProfile, reads: list[Profile]) -> None:
    read = next((p for p in reads if p.name == write.read_profile), None)
    code = coupling_issue(write, read)
    if code is not None:
        raise FormError("studio.write_profile_invalid", [(_POINTERS.get(code, "profile"), code)])


#: The listing of ``GET /api/write/audit`` by default and at most.
AUDIT_LISTING = 200
AUDIT_LISTING_MAX = 1000


def _limit(raw: str | None) -> int | None:
    if raw is None:
        return AUDIT_LISTING
    if not raw.isascii() or not raw.isdigit() or len(raw) > 4:
        return None
    value = int(raw)
    return value if 1 <= value <= AUDIT_LISTING_MAX else None


def add_enabled_write_routes(
    app: FastAPI,
    areas: WriteAreas,
    config: Configuration,
    registry: ConnectionRegistry,
    audit_dir: Path | None = None,
) -> Unlocks:
    audit = AuditLog(audit_dir)
    unlocks = Unlocks(config, registry, areas, audit=audit)
    registered: list[tuple[str, tuple[str, ...]]] = []

    def route(path: str, method: str) -> Callable[[_Endpoint], _Endpoint]:
        def decorate(endpoint: _Endpoint) -> _Endpoint:
            app.add_api_route(path, endpoint, methods=[method], include_in_schema=False)
            registered.append((path, (method,)))
            return endpoint

        return decorate

    def answer(exc: BaseException) -> Response:
        if isinstance(exc, FormError):
            return problem_response(exc.code, issues=exc.issues)
        if isinstance(exc, AuditError):
            logger.warning("write refused: no audit record", extra={"event": "studio.audit_unavailable"})
            return problem_response("studio.audit_unavailable")
        if isinstance(exc, WriteError):
            logger.info("write refused", extra={"event": "studio.write_refused", "code": exc.code})
            return problem_response(exc.code)
        if isinstance(exc, SecretError) and exc.code == "studio.profile_not_found":
            return problem_response("studio.write_target_changed")  # never 404 for a refused write (W-9)
        if isinstance(exc, SaveError | SecretError):
            logger.info("write profile not saved", extra={"event": "studio.config_refused", "code": exc.code})
            return problem_response(exc.code)
        if isinstance(exc, ValueError):
            return problem_response("studio.config_not_storable")
        problem = connection_problem(exc)
        if problem is None:
            raise exc
        return problem

    async def run(call: Any, *args: Any) -> Response:
        try:
            result: Response = await asyncio.to_thread(call, *args)
        except Exception as exc:
            return answer(exc)
        return result

    def stored(write_id: object) -> tuple[WriteProfile, Profile | None]:
        if not isinstance(write_id, str) or not write_id.startswith(WRITE_ID_PREFIX):
            raise WriteError("studio.write_profile_unknown")
        name = write_id[len(WRITE_ID_PREFIX) :]
        user = config.user.profiles()
        write = next((w for w in user.write_profiles if w.name == name), None)
        if write is None:
            raise WriteError("studio.write_profile_unknown")
        if not unlocks.kind_open(write.kind):
            raise WriteError("studio.method_not_allowed")
        return write, next((p for p in user.profiles if p.name == write.read_profile), None)

    # --- write and admin profiles ----------------------------------------------------------------------------------

    @route("/api/write/profiles", "GET")
    async def get_write_profiles() -> Response:
        def listing() -> Response:
            user = config.user.profiles()
            reads = {p.name: p for p in user.profiles}
            items = []
            for write in user.write_profiles:
                read = reads.get(write.read_profile)
                if read is None or not unlocks.kind_open(write.kind):
                    continue
                target = config.write_target(write, read)
                items.append(
                    {
                        "id": write_profile_id(write),
                        **write.to_dict(),
                        "read_profile_id": f"user:{read.name}",
                        "engine": read.engine,
                        "environment": read.environment,
                        # Never the value (N-5): where it comes from and, if known without reading it, whether it is.
                        "secret": {
                            "source": None if write.password is None else write.password.source,
                            "has_secret": config.secrets.has_secret(target),
                        },
                    }
                )
            return JSONResponse(
                {
                    "write_profiles": items,
                    "kinds": {kind: unlocks.kind_open(kind) for kind in ("write", "admin")},
                    # L-1 of PR #111: the configuration folder came from an environment variable, so the file's write
                    # and admin profiles are not used (and none can be created) until Studio starts with that folder
                    # on the command line or uses the platform's folder.
                    "write_profiles_allowed": config.user.write_profiles_allowed,
                    "write_profiles_ignored": user.write_profiles_ignored,
                    "user_file": {"digest": user.digest, "hand_written": not user.canonical},
                }
            )

        return await run(listing)

    async def save(request: Request, action: Any) -> Response:
        doc = _json(await request.body(), MAX_FORM_BODY)
        if doc is None or "digest" not in doc:
            return problem_response("access.bad_request")
        return await run(action, doc, doc.get("digest"), doc.get("overwrite") is True)

    def environment_of(write: WriteProfile) -> str | None:
        read = next((p for p in config.user.profiles().profiles if p.name == write.read_profile), None)
        return None if read is None else read.environment

    def create(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        write = form_write_profile(doc.get("profile"))
        if not unlocks.kind_open(write.kind):
            raise WriteError("studio.method_not_allowed")
        if doc.get("confirm_no_prod_credentials") is not True:
            # N-6: the page shows the note when the profile is created; the server does not create one without it.
            raise WriteError("studio.write_warning_unconfirmed")
        audit.check()

        def change(reads: list[Profile], writes: list[WriteProfile]) -> list[WriteProfile]:
            if any(p.name == write.name for p in reads) or any(w.name == write.name for w in writes):
                raise WriteError("studio.write_profile_exists")
            if len(reads) + len(writes) >= 200:
                raise FormError("studio.write_profile_invalid", [("profile", "config.too_many")])
            _coupling(write, reads)
            return [*writes, write]

        new_digest = config.user.save_write_profiles(change, expected, overwrite=overwrite)
        logger.info("write profile created", extra={"event": "studio.write_profile_created"})
        recorded = audit.record(
            "profile.create",
            profile=write.name,
            kind=write.kind,
            read_profile=write.read_profile,
            environment=environment_of(write),
            result="ok",
        )
        return JSONResponse(
            {"id": write_profile_id(write), "digest": new_digest, "audit_pending": not recorded}, status_code=201
        )

    def update(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        old, read = stored(doc.get("id"))
        write = form_write_profile(doc.get("profile"))
        if write.name != old.name:
            raise FormError("studio.write_profile_invalid", [("profile.name", "config.rename_unsupported")])
        if not unlocks.kind_open(write.kind):
            raise WriteError("studio.method_not_allowed")
        audit.check()

        def change(reads: list[Profile], writes: list[WriteProfile]) -> list[WriteProfile]:
            if not any(w.name == old.name for w in writes):
                raise WriteError("studio.write_profile_unknown")
            _coupling(write, reads)
            return [write if w.name == old.name else w for w in writes]

        new_digest = config.user.save_write_profiles(change, expected, overwrite=overwrite)
        unlocks.write_profile_changed(write.name)
        removed = True
        if read is not None:
            before = config.write_target(old, read)
            new_read = next((p for p in config.user.profiles().profiles if p.name == write.read_profile), read)
            if before.ref != write.password or before.key != config.write_target(write, new_read).key:
                removed = forget(before)
        logger.info("write profile changed", extra={"event": "studio.write_profile_changed"})
        before_doc, after_doc = old.to_dict(), write.to_dict()
        # The keys that changed, never their values; "read_profile" among them is a change of the coupling.
        changed = sorted(key for key in set(before_doc) | set(after_doc) if before_doc.get(key) != after_doc.get(key))
        recorded = audit.record(
            "profile.change",
            profile=write.name,
            kind=write.kind,
            read_profile=write.read_profile,
            environment=environment_of(write),
            changed=changed,
            result="ok",
        )
        return JSONResponse(
            {
                "id": write_profile_id(write),
                "digest": new_digest,
                "secret_removed": removed,
                "audit_pending": not recorded,
            }
        )

    def remove(doc: dict[str, Any], expected: object, overwrite: bool) -> Response:
        old, read = stored(doc.get("id"))
        audit.check()

        def change(_reads: list[Profile], writes: list[WriteProfile]) -> list[WriteProfile]:
            if not any(w.name == old.name for w in writes):
                raise WriteError("studio.write_profile_unknown")
            return [w for w in writes if w.name != old.name]

        new_digest = config.user.save_write_profiles(change, expected, overwrite=overwrite)
        unlocks.write_profile_changed(old.name)
        removed = True if read is None else forget(config.write_target(old, read))
        logger.info("write profile removed", extra={"event": "studio.write_profile_removed"})
        recorded = audit.record(
            "profile.delete", profile=old.name, kind=old.kind, read_profile=old.read_profile, result="ok"
        )
        return JSONResponse({"digest": new_digest, "secret_removed": removed, "audit_pending": not recorded})

    def forget(target: Any) -> bool:
        try:
            config.secrets.forget(target)
        except SecretError:
            return False
        return True

    @route("/api/write/profiles", "POST")
    async def post_write_profile(request: Request) -> Response:
        return await save(request, create)

    @route("/api/write/profiles", "PUT")
    async def put_write_profile(request: Request) -> Response:
        return await save(request, update)

    @route("/api/write/profiles", "DELETE")
    async def delete_write_profile(request: Request) -> Response:
        return await save(request, remove)

    # --- the secret of a write or admin profile --------------------------------------------------------------------

    @route("/api/write/profiles/{profile_id:path}/secret", "PUT")
    async def put_write_secret(profile_id: str, request: Request) -> Response:
        doc = _json(await request.body())
        if doc is None:
            return problem_response("access.bad_request")

        def store() -> Response:
            write, read = stored(profile_id)
            if read is None:
                raise WriteError("studio.write_coupling_invalid")
            audit.check()
            config.secrets.store(config.write_target(write, read), doc.get("secret"), doc.get("store"))
            logger.info("write secret stored", extra={"event": "studio.secret_stored"})
            # Where it went (a fixed word), never the secret. A pending record shows on the audit status.
            where = doc.get("store") if doc.get("store") in ("keyring", "session") else None
            audit.record("secret.store", profile=write.name, kind=write.kind, store=where, result="ok")
            return Response(status_code=204)

        return await run(store)

    @route("/api/write/profiles/{profile_id:path}/secret", "DELETE")
    async def delete_write_secret(profile_id: str) -> Response:
        def drop() -> Response:
            write, read = stored(profile_id)
            audit.check()
            if read is not None:
                config.secrets.forget(config.write_target(write, read))
            logger.info("write secret removed", extra={"event": "studio.secret_removed"})
            audit.record("secret.remove", profile=write.name, kind=write.kind, result="ok")
            return Response(status_code=204)

        return await run(drop)

    # --- unlocking -------------------------------------------------------------------------------------------------

    @route("/api/write/unlocks", "GET")
    async def get_unlocks(request: Request) -> Response:
        try:
            session = session_of(request)
        except WriteError as exc:
            return answer(exc)
        return await run(lambda: JSONResponse({"unlocks": unlocks.list(session)}))

    @route("/api/write/unlocks", "POST")
    async def post_unlock(request: Request) -> Response:
        doc = _json(await request.body())
        if doc is None:
            return problem_response("access.bad_request")
        try:
            session = session_of(request)
        except WriteError as exc:
            return answer(exc)

        def unlock() -> Response:
            state = unlocks.unlock(
                session, doc.get("connection_id"), doc.get("write_profile_id"), doc.get("typed_name")
            )
            return JSONResponse(state)

        return await run(unlock)

    @route("/api/write/unlocks", "DELETE")
    async def delete_unlock(request: Request) -> Response:
        doc = _json(await request.body())
        if doc is None:
            return problem_response("access.bad_request")
        try:
            session = session_of(request)
        except WriteError as exc:
            return answer(exc)
        await asyncio.to_thread(unlocks.lock, session, doc.get("connection_id"))
        return Response(status_code=204)

    # --- the audit log, read only (STU-XW-2) -----------------------------------------------------------------------

    @route("/api/write/audit", "GET")
    async def get_audit(request: Request) -> Response:
        if set(request.query_params) - {"limit"}:
            return problem_response("access.bad_request")
        limit = _limit(request.query_params.get("limit"))
        if limit is None:
            return problem_response("access.bad_request")
        return await run(lambda: JSONResponse(audit.listing(limit)))

    expected = sorted((path, method) for path, methods in WRITE_ROUTES for method in methods)
    if sorted((path, methods[0]) for path, methods in registered) != expected:  # pragma: no cover - a coding error
        raise RuntimeError("the write routes do not match WRITE_ROUTES")
    return unlocks
