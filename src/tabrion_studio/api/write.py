"""The routes of the shared write foundation (STU-XW-1, STU-ADR-0006, W-9).

* ``GET /api/write/areas``: which write areas are on, read only (``{"areas": {"data_editor": false, ...}}``). The
  page uses it to show or hide controls; hiding a control is UX, the server refuses regardless. No route reads,
  sets or toggles a flag (TX-01).
* ``GET``, ``POST``, ``PUT``, ``DELETE /api/write/profiles``: the user's write and admin profiles (``profiles.toml``
  only, K-1). Creating one needs ``"confirm_no_prod_credentials": true``: the page shows, at that moment, that Studio
  relies on the environment the user gives the profile and that write credentials of a production database do not
  belong in Studio (review N-6).
* ``PUT``, ``DELETE /api/write/profiles/{id}/secret``: the write or admin profile's own secret (keyring or session).
* ``GET``, ``POST``, ``DELETE /api/write/unlocks``: this session's unlocks; unlock with
  ``{"connection_id", "write_profile_id", "typed_name"}``, lock with ``{"connection_id"}``.

**Kill switch (TX-02).** :data:`WRITE_ROUTES` is the one table of these routes. When no write area is on, each of
them is a refusing handler that answers ``405 studio.method_not_allowed`` and neither imports, constructs nor calls
any of the write code (the package :mod:`tabrion_studio.write` is not even imported). When an area is on, the real
handlers come from :mod:`tabrion_studio.write.routes`, which registers exactly the same table; a route of a kind
whose area is off still answers 405 there. The routes are left out of the OpenAPI document (as ``/api/session``
is): the 1.0 acceptance check of the documented API stays as it was, and ``tests/security/test_every_route.py``
lists every one of them in ``CHANGING_ROUTES``.

Every refusal is a catalog problem: 400 or 405 for a refused write, 403 only for a write mode (W-9), never a value,
a name from the request, a secret or a database message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.write_areas import ALL_OFF, WriteAreas
from tabrion_studio.problems import problem_response

if TYPE_CHECKING:
    from tabrion_studio.services.connections import ConnectionRegistry
    from tabrion_studio.write.unlock import Unlocks

#: Every route of the write foundation: (path, methods). The refusing and the real handlers serve exactly these.
WRITE_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/api/write/profiles", ("GET", "POST", "PUT", "DELETE")),
    ("/api/write/profiles/{profile_id:path}/secret", ("PUT", "DELETE")),
    ("/api/write/unlocks", ("GET", "POST", "DELETE")),
)


async def _refused() -> Response:
    """The handler of every write route while its area is off: 405, nothing read, nothing imported (W-9, TX-02)."""
    return problem_response("studio.method_not_allowed")


def add_write_routes(
    app: FastAPI,
    areas: WriteAreas = ALL_OFF,
    config: Configuration | None = None,
    registry: ConnectionRegistry | None = None,
) -> Unlocks | None:
    """Register the write foundation's routes; the unlocks when an area is on (closed by the app on shutdown)."""
    snapshot = areas.to_dict()

    @app.get("/api/write/areas")
    async def get_write_areas() -> Response:
        # Read only: the state read at start. No query parameter, header or body is looked at.
        return JSONResponse({"areas": dict(snapshot)})

    if not areas.enabled or config is None or registry is None:
        for path, methods in WRITE_ROUTES:
            app.add_api_route(path, _refused, methods=list(methods), include_in_schema=False)
        return None
    # Imported only now: with every flag off, no write code is loaded.
    from tabrion_studio.write.routes import add_enabled_write_routes

    return add_enabled_write_routes(app, areas, config, registry)
