"""Request body limits: no route ever holds more of a request body than its cap (STU-016 review F-4).

Every route of Studio reads its whole JSON body and then checks the size. Without a limit in front of them, the server
would buffer a body of any size first, before the session check on ``/api/session`` and on every authenticated route.
:class:`BodyLimitMiddleware` sits inside the access middleware (so a refused request is never read at all) and in
front of the routes:

- A ``Content-Length`` over the route's cap is refused before a single byte is read. A ``Content-Length`` that is not
  one plain decimal number is ``access.bad_request``.
- A body without ``Content-Length`` (``Transfer-Encoding: chunked``) is read chunk by chunk and refused as soon as it
  passes the cap, so the server holds at most the cap plus one chunk.
- A body within the cap is read here and handed to the route unchanged; the route's own, often smaller, cap still
  applies.

The refusal is the route's :class:`BodyLimit` code (status 413) with ``Connection: close``: the server closes the
connection instead of reading the rest of the body. A request without a body (no ``Content-Length`` and no
``Transfer-Encoding``) passes untouched, so a streaming answer still sees the client's disconnect.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tabrion_studio.problems import problem, send_problem


@dataclass(frozen=True)
class BodyLimit:
    """The largest body a route accepts, in bytes, and the problem code for a larger one (a 413 code)."""

    max_bytes: int
    code: str = "access.body_too_large"


class BodyLimitMiddleware:
    """Refuses a request body over its route's :class:`BodyLimit` while it streams in (see the module docstring)."""

    def __init__(self, app: ASGIApp, *, default: BodyLimit, routes: Mapping[str, BodyLimit] | None = None) -> None:
        self.app = app
        self.default = default
        #: Exact request paths with a cap other than ``default``.
        self.routes = dict(routes or {})

    def limit(self, path: str) -> BodyLimit:
        """The cap of the request path ``path``."""
        return self.routes.get(path, self.default)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
        chunked = any(key.lower() == b"transfer-encoding" for key, _ in scope["headers"])
        if not lengths and not chunked:
            await self.app(scope, receive, send)
            return
        limit = self.limit(scope.get("path", ""))
        if lengths:
            declared = lengths[0].strip()
            if len(lengths) != 1 or not declared.isdigit():
                await send_problem(send, problem("access.bad_request"), close=True)
                return
            # More than 18 digits is over every cap; int() of a very long digit string would raise.
            if len(declared) > 18 or int(declared) > limit.max_bytes:
                await send_problem(send, problem(limit.code), close=True)
                return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away; nobody is left to answer
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit.max_bytes:
                await send_problem(send, problem(limit.code), close=True)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        await self.app(scope, _replay(b"".join(chunks), receive), send)


def _replay(body: bytes, receive: Receive) -> Receive:
    """``receive`` that hands out ``body`` once and then waits on the real ``receive`` (for the disconnect)."""
    pending = True

    async def replayed() -> Message:
        nonlocal pending
        if pending:
            pending = False
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replayed
