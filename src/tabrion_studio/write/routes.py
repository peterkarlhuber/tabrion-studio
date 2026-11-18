"""The real handlers of :data:`tabrion_studio.api.write.WRITE_ROUTES`, registered only when a write area is on.

See :mod:`tabrion_studio.api.write` for the routes. Every handler first checks that the area of the profile's kind
is on (``write``: Data Editor or Schema Designer; ``admin``: Database Manager); otherwise it answers 405 like the
refusing handler. The environment, the target and the coupling are always read from the user's stored profiles,
never from the request.

Every change of a write or admin profile, its coupling or its secret is recorded in the audit log after it was saved
(:mod:`tabrion_studio.write.audit`; a log that is known to be unusable refuses the change first, and a record that
fails afterwards is kept as a pending marker and reported as ``audit_pending``, review N-4). ``GET /api/write/audit``
lists the newest records and verifies the whole log, read only.

The plan routes (STU-XW-3) keep their plans in :class:`tabrion_studio.write.plan.Plans` (``app.state.write_plans``):
make and preview, confirm with the hash and the typed text, execute once. With the Data Editor flag on, the Data
Editor's executor (STU-026, :mod:`tabrion_studio.write.data_editor`) is registered there and its table route answers;
with that flag off neither is imported. With the Schema Designer flag on, its two database creation routes (STU-CR-2,
STU-CR-3, :mod:`tabrion_studio.write.schema_designer.create`), its draft and preview routes (STU-CR-4,
:mod:`tabrion_studio.write.schema_designer.designer`; the drafts in ``app.state.schema_drafts``, ended with their
unlock) and the confirmation and run of a previewed plan (STU-CR-5,
:mod:`tabrion_studio.write.schema_designer.execute`) answer; with that flag off they answer 405 and import nothing of
the area. With the Database Manager flag on, its backup and restore routes (STU-XDM-2,
:mod:`tabrion_studio.write.database_manager.backups`, with the tool runner :mod:`tabrion_studio.write.pgtools` and the
backup folder :mod:`tabrion_studio.write.backup_store`) answer; with that flag off they answer 405 and import nothing
of the area.

The secret confirmation routes (STU-XW-4, I-109 j, D-15) keep the confirmed pairs in
:class:`tabrion_studio.write.secret_confirm.SecretConfirmations` (``write-secret-confirmations.json`` next to the audit
folder, in the folder of ``write-areas.toml``; ``app.state.write_secret_confirmations``): preview the pair, confirm it
with its hash and the typed host, revoke it. A confirmation and a revocation are recorded in the audit log (the pair's
digest, the kind of reference and whether the host is foreign; never the host or the reference itself).
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
from tabrion_studio.api.write import MAX_PLAN_BODY, WRITE_ROUTES
from tabrion_studio.config.schema import (
    WRITE_PROFILE_KEYS,
    Profile,
    WriteProfile,
    coupling_issue,
    parse_write_profile,
)
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.config.writer import SaveError
from tabrion_studio.problems import db_problem_response, problem_response
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.plan import MAX_REQUEST_BYTES, Plans
from tabrion_studio.write.secret_confirm import CONFIRMATION_FILE, SecretConfirmations, secret_pair
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
    "config.requirepeer_socket_only": "profile.requirepeer",
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


#: The keys of the plan routes' bodies (STU-XW-3); any other key is refused.
_PLAN_KEYS = frozenset({"connection_id", "executor", "request"})
_CONFIRM_KEYS = frozenset({"connection_id", "plan_hash", "typed"})
_EXECUTE_KEYS = frozenset({"connection_id", "plan_hash"})
_DISCARD_KEYS = frozenset({"plan_hash"})
#: The keys of the secret confirmation routes' bodies (STU-XW-4).
_PAIR_KEYS = frozenset({"write_profile_id"})
_PAIR_CONFIRM_KEYS = frozenset({"confirmation_hash", "typed"})
#: The keys of the Data Editor's table route (STU-026).
_TABLE_KEYS = frozenset({"connection_id", "schema", "table"})
_MAX_PLAN_BODY = MAX_PLAN_BODY
if MAX_PLAN_BODY != MAX_REQUEST_BYTES + 4096:  # pragma: no cover - a coding error
    raise RuntimeError("MAX_PLAN_BODY does not match the plan's MAX_REQUEST_BYTES")
#: The keys of the Schema Designer's creation routes (STU-CR-2, STU-CR-3); any other key is refused.
_CREATE_SQLITE_KEYS = frozenset({"path", "typed"})
_CREATE_POSTGRES_KEYS = frozenset({"connection_id", "name", "owner", "template", "encoding", "locale", "typed"})
_MAX_CREATE_BODY = MAX_FORM_BODY
#: The keys of the Schema Designer's draft routes (STU-CR-4): never a model, a snapshot or a plan (GS3-09).
_DRAFT_START_KEYS = frozenset({"connection_id", "schema"})
_DRAFT_KEYS = frozenset({"connection_id"})
_DRAFT_EDIT_KEYS = frozenset({"connection_id", "revision", "edit"})
#: The keys of the plan confirmation and the run (STU-CR-5): never an environment, a plan, a target or a class list;
#: the server takes all of them from its own record (GS3-05, GS3-09).
_PLAN_CONFIRM_KEYS = frozenset(
    {"connection_id", "plan_hash", "typed", "tables", "steps", "locking", "backup", "backup_typed"}
)
_PLAN_RUN_KEYS = frozenset({"connection_id", "plan_hash", "dry_run"})
#: The keys of the Database Manager's routes (STU-XDM-2): never a path, a tool, an environment or a server.
_DBM_BACKUP_KEYS = frozenset({"connection_id"})
_DBM_PREVIEW_KEYS = frozenset({"connection_id", "backup_id"})
_DBM_RESTORE_KEYS = frozenset({"connection_id", "backup_id", "target", "name", "typed", "restore_profile_id"})
_DBM_DELETE_PREVIEW_KEYS = frozenset({"connection_id", "name"})
_DBM_DELETE_KEYS = frozenset({"connection_id", "name", "typed", "force", "force_typed"})

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
    log_dir: Path | None = None,
) -> Unlocks:
    audit = AuditLog(audit_dir)
    # The audit folder is <folder of write-areas.toml>/audit (review N-1 of PR #116); the confirmed pairs live next
    # to it. No folder: nothing confirmed.
    confirmations = SecretConfirmations(None if audit_dir is None else audit_dir.parent / CONFIRMATION_FILE)
    app.state.write_secret_confirmations = confirmations
    unlocks = Unlocks(config, registry, areas, audit=audit, confirmations=confirmations)
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
            return problem_response(exc.code, issues=exc.issues)
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
                pair = secret_pair(write, read)
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
                            # STU-XW-4 (I-109 j): None for SQLite (no secret); the pair itself is in the preview.
                            "confirmed": None if pair is None else confirmations.is_confirmed(pair),
                            "foreign": None if pair is None else pair.foreign,
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
        new_pair_read = next((p for p in config.user.profiles().profiles if p.name == write.read_profile), None)
        old_pair = None if read is None else secret_pair(old, read)
        new_pair = None if new_pair_read is None else secret_pair(write, new_pair_read)
        if old_pair is None or new_pair is None or old_pair.digest != new_pair.digest:
            unconfirm(write.name, write.kind, "profile_changed")
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
        unconfirm(old.name, old.kind, "profile_removed")
        removed = True if read is None else forget(config.write_target(old, read))
        logger.info("write profile removed", extra={"event": "studio.write_profile_removed"})
        recorded = audit.record(
            "profile.delete", profile=old.name, kind=old.kind, read_profile=old.read_profile, result="ok"
        )
        return JSONResponse({"digest": new_digest, "secret_removed": removed, "audit_pending": not recorded})

    def unconfirm(name: str, kind: str, reason: str) -> None:
        """Forget a pair's confirmation after the profile changed or went (a failure only leaves a stale digest,
        which no longer matches the changed pair)."""
        try:
            forgotten = confirmations.forget(name)
        except WriteError:
            logger.warning("secret confirmation not removed", extra={"event": "studio.secret_confirmation_unavailable"})
            return
        if forgotten:
            audit.record("secret.unconfirm", profile=name, kind=kind, reason=reason, result="ok")

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

    # --- plans: preview, confirmation and execution (STU-XW-3) ------------------------------------------------------

    plans = Plans(unlocks)
    app.state.write_plans = plans
    if areas.on(Area.DATA_EDITOR):
        # STU-026: imported and registered only with the Data Editor flag on (TX-02); no other area's flag opens it.
        from tabrion_studio.write.data_editor import DataEditorExecutor

        plans.register(DataEditorExecutor())

    async def plan_call(request: Request, keys: frozenset[str], limit: int, call: Callable[..., Any]) -> Response:
        doc = _json(await request.body(), limit)
        if doc is None or set(doc) - keys or request.query_params:
            # A plan is the server's: a body that carries anything else (steps, a fingerprint, an area) is refused.
            return problem_response("access.bad_request")
        try:
            session = session_of(request)
        except WriteError as exc:
            return answer(exc)
        return await run(call, session, doc)

    @route("/api/write/plans", "POST")
    async def post_plan(request: Request) -> Response:
        def create(session: str, doc: dict[str, Any]) -> Response:
            preview = plans.create(session, doc.get("connection_id"), doc.get("executor"), doc.get("request"))
            return JSONResponse(preview, status_code=201)

        return await plan_call(request, _PLAN_KEYS, _MAX_PLAN_BODY, create)

    @route("/api/write/plans", "DELETE")
    async def delete_plan(request: Request) -> Response:
        def discard(session: str, doc: dict[str, Any]) -> Response:
            plans.discard(session, doc.get("plan_hash"))
            return Response(status_code=204)

        return await plan_call(request, _DISCARD_KEYS, _MAX_BODY, discard)

    @route("/api/write/plans/confirm", "POST")
    async def confirm_plan(request: Request) -> Response:
        def confirm(session: str, doc: dict[str, Any]) -> Response:
            return JSONResponse(
                plans.confirm(session, doc.get("plan_hash"), doc.get("connection_id"), doc.get("typed"))
            )

        return await plan_call(request, _CONFIRM_KEYS, _MAX_BODY, confirm)

    @route("/api/write/plans/execute", "POST")
    async def execute_plan(request: Request) -> Response:
        def execute(session: str, doc: dict[str, Any]) -> Response:
            return JSONResponse(plans.execute(session, doc.get("plan_hash"), doc.get("connection_id")))

        return await plan_call(request, _EXECUTE_KEYS, _MAX_BODY, execute)

    # --- the Data Editor's table (STU-026) ----------------------------------------------------------------------

    @route("/api/write/data-editor/table", "POST")
    async def post_data_editor_table(request: Request) -> Response:
        if not areas.on(Area.DATA_EDITOR):
            return problem_response("studio.method_not_allowed")  # W-9: another area's flag never opens it
        from tabrion_studio.write.data_editor import table_info

        def info(session: str, doc: dict[str, Any]) -> Response:
            return JSONResponse(
                table_info(unlocks, session, doc.get("connection_id"), doc.get("schema"), doc.get("table"))
            )

        return await plan_call(request, _TABLE_KEYS, _MAX_BODY, info)

    # --- the Schema Designer's database creation (STU-CR-2, STU-CR-3) ----------------------------------------------

    # Studio's own folders: no database file is created in them or below them (the configuration, the audit log's
    # folder, the state, the log folder, the project's .tabrion folder); compared by device and inode (P128-02).
    reserved = [config.user.config_dir, config.project.record.parent, config.project.file.parent]
    if audit_dir is not None:
        reserved += [audit_dir, audit_dir.parent]
    if log_dir is not None:
        reserved.append(log_dir)

    @route("/api/write/schema-designer/create/sqlite", "POST")
    async def post_create_sqlite(request: Request) -> Response:
        if not areas.on(Area.SCHEMA_DESIGNER):
            return problem_response("studio.method_not_allowed")  # W-9: another area's flag never opens it
        # Imported only now: with the Schema Designer flag off, none of its code is loaded (GS3-01).
        from tabrion_studio.write.schema_designer.create import create_sqlite_file

        def create(_session: str, doc: dict[str, Any]) -> Response:
            created = create_sqlite_file(audit, reserved, doc.get("path"), doc.get("typed"))
            return JSONResponse(created.to_dict(), status_code=201)

        return await plan_call(request, _CREATE_SQLITE_KEYS, _MAX_CREATE_BODY, create)

    @route("/api/write/schema-designer/create/postgresql", "POST")
    async def post_create_postgresql(request: Request) -> Response:
        if not areas.on(Area.SCHEMA_DESIGNER):
            return problem_response("studio.method_not_allowed")
        from tabrion_studio.write.schema_designer.create import DriverFailure, create_postgres_database

        def create(session: str, doc: dict[str, Any]) -> Response:
            try:
                created = create_postgres_database(unlocks, session, doc)
            except DriverFailure as exc:
                return db_problem_response(exc.classification)
            return JSONResponse(created.to_dict(), status_code=201)

        return await plan_call(request, _CREATE_POSTGRES_KEYS, _MAX_BODY, create)

    # --- the Schema Designer's draft and plan preview (STU-CR-4) ---------------------------------------------------

    drafts: Any = None
    if areas.on(Area.SCHEMA_DESIGNER):
        # Imported only with the Schema Designer flag on (GS3-01); a draft ends with the unlock it belongs to.
        from tabrion_studio.write.schema_designer.draft import MAX_EDIT_BODY, Drafts

        # The project folder (an alembic.ini there means revisions only) and the folder of write-areas.toml (the
        # backups before a run go below it, STU-CR-5); no such folder: no run that needs it (fail closed).
        drafts = Drafts(config.project.project, None if audit_dir is None else audit_dir.parent)
        from tabrion_studio.write.folders import remove_stale_runs

        # The backup before a PostgreSQL run uses the tool runner (STU-CR-5 follow-up): remove a passfile a crashed
        # run left (GS4-06). The runner itself is loaded only when a backup is made.
        remove_stale_runs(drafts.write_state)
        unlocks.end_listeners.append(drafts.unlock_ended)
        app.state.schema_drafts = drafts
        draft_edit_body = MAX_EDIT_BODY
    else:
        draft_edit_body = _MAX_BODY

    def designer_route(path: str, method: str, keys: frozenset[str], limit: int, action: str) -> None:
        @route(path, method)
        async def handle(request: Request) -> Response:
            if drafts is None:
                return problem_response("studio.method_not_allowed")  # W-9: another area's flag never opens it
            from tabrion_studio.write.schema_designer import designer
            from tabrion_studio.write.schema_designer.create import DriverFailure

            def call(session: str, doc: dict[str, Any]) -> Response:
                try:
                    if action == "discard":
                        designer.discard(unlocks, drafts, session, doc)
                        return Response(status_code=204)
                    answer_doc = getattr(designer, action)(unlocks, drafts, session, doc)
                except DriverFailure as exc:
                    return db_problem_response(exc.classification)
                return JSONResponse(answer_doc, status_code=201 if action == "start" else 200)

            return await plan_call(request, keys, limit, call)

    designer_route("/api/write/schema-designer/draft", "POST", _DRAFT_START_KEYS, _MAX_BODY, "start")
    designer_route("/api/write/schema-designer/draft", "DELETE", _DRAFT_KEYS, _MAX_BODY, "discard")
    designer_route("/api/write/schema-designer/draft/view", "POST", _DRAFT_KEYS, _MAX_BODY, "view")
    designer_route("/api/write/schema-designer/draft/edits", "POST", _DRAFT_EDIT_KEYS, draft_edit_body, "edit")
    designer_route("/api/write/schema-designer/draft/preview", "POST", _DRAFT_KEYS, _MAX_BODY, "preview")
    designer_route("/api/write/schema-designer/plan/confirm", "POST", _PLAN_CONFIRM_KEYS, _MAX_BODY, "confirm")
    designer_route("/api/write/schema-designer/plan/run", "POST", _PLAN_RUN_KEYS, _MAX_BODY, "run")

    # --- the Database Manager's backup and restore (STU-XDM-2) ------------------------------------------------------

    manager: Any = None
    if areas.on(Area.DATABASE_MANAGER):
        # Imported only with the Database Manager flag on (GS4-01): the area's code, the tool runner, the backups.
        from tabrion_studio.write.backup_store import BackupStore
        from tabrion_studio.write.database_manager.backups import Manager
        from tabrion_studio.write.pgtools import Runner

        # The folder of write-areas.toml holds the tools file, the run folder and the backups; none: fail closed.
        write_state = None if audit_dir is None else audit_dir.parent
        runner = Runner(write_state)
        runner.remove_stale()  # a passfile a crashed run left (GS4-06)
        manager = Manager(unlocks, runner, BackupStore(write_state), registry, tuple(reserved))
        app.state.database_manager = manager

    def manager_route(path: str, method: str, keys: frozenset[str], action: str) -> None:
        @route(path, method)
        async def handle(request: Request) -> Response:
            if manager is None:
                return problem_response("studio.method_not_allowed")  # W-9: another area's flag never opens it
            from tabrion_studio.write.database_manager import backups

            if method == "GET":
                if request.query_params:
                    return problem_response("access.bad_request")
                return await run(lambda: JSONResponse(backups.listing(manager)))

            def call(session: str, doc: dict[str, Any]) -> Response:
                try:
                    if action.startswith("delete"):  # STU-XDM-3
                        from tabrion_studio.write.database_manager import delete

                        answer_doc = (delete.delete if action == "delete" else delete.preview)(manager, session, doc)
                    else:
                        answer_doc = getattr(backups, action)(manager, session, doc)
                except backups.DriverFailure as exc:
                    return db_problem_response(exc.classification)
                return JSONResponse(answer_doc, status_code=201 if action in ("backup", "restore") else 200)

            return await plan_call(request, keys, _MAX_BODY, call)

    manager_route("/api/write/database-manager/backups", "GET", frozenset(), "listing")
    manager_route("/api/write/database-manager/backup", "POST", _DBM_BACKUP_KEYS, "backup")
    manager_route("/api/write/database-manager/restore/preview", "POST", _DBM_PREVIEW_KEYS, "preview")
    manager_route("/api/write/database-manager/restore", "POST", _DBM_RESTORE_KEYS, "restore")
    manager_route("/api/write/database-manager/delete/preview", "POST", _DBM_DELETE_PREVIEW_KEYS, "delete_preview")
    manager_route("/api/write/database-manager/delete", "POST", _DBM_DELETE_KEYS, "delete")

    # --- the I-109 j confirmation of a secret's pair of server and reference (STU-XW-4, D-15) ----------------------

    def pair_of(write: WriteProfile, read: Profile | None) -> Any:
        if read is None:
            raise WriteError("studio.write_coupling_invalid")
        pair = secret_pair(write, read)
        if pair is None:
            raise WriteError("studio.secret_confirmation_not_needed")  # SQLite: no secret
        return pair

    @route("/api/write/secret-confirmations", "POST")
    async def post_secret_confirmation(request: Request) -> Response:
        def preview(session: str, doc: dict[str, Any]) -> Response:
            write, read = stored(doc.get("write_profile_id"))
            answer = confirmations.preview(session, pair_of(write, read))
            return JSONResponse({"write_profile_id": write_profile_id(write), "kind": write.kind, **answer})

        return await plan_call(request, _PAIR_KEYS, _MAX_BODY, preview)

    @route("/api/write/secret-confirmations/confirm", "POST")
    async def confirm_secret_confirmation(request: Request) -> Response:
        def confirm(session: str, doc: dict[str, Any]) -> Response:
            name = confirmations.pending(session, doc.get("confirmation_hash"))
            # The pair as the user's file has it now, never from the request.
            write, read = stored(f"{WRITE_ID_PREFIX}{name}")
            pair = pair_of(write, read)
            try:
                audit.check()
            except AuditError:
                raise WriteError("studio.audit_unavailable") from None
            digest = confirmations.confirm(session, doc.get("confirmation_hash"), doc.get("typed"), pair)
            recorded = audit.record(
                "secret.confirm",
                profile=write.name,
                kind=write.kind,
                source=pair.ref_source,
                foreign=pair.foreign,
                pair_hash=digest,
                result="ok",
            )
            return JSONResponse(
                {"write_profile_id": write_profile_id(write), "confirmed": True, "audit_pending": not recorded}
            )

        return await plan_call(request, _PAIR_CONFIRM_KEYS, _MAX_BODY, confirm)

    @route("/api/write/secret-confirmations", "DELETE")
    async def delete_secret_confirmation(request: Request) -> Response:
        def revoke(_session: str, doc: dict[str, Any]) -> Response:
            write, _read = stored(doc.get("write_profile_id"))
            try:
                audit.check()
            except AuditError:
                raise WriteError("studio.audit_unavailable") from None
            if confirmations.forget(write.name):
                unlocks.write_profile_changed(write.name)
                audit.record("secret.unconfirm", profile=write.name, kind=write.kind, reason="revoked", result="ok")
            return Response(status_code=204)

        return await plan_call(request, _PAIR_KEYS, _MAX_BODY, revoke)

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
