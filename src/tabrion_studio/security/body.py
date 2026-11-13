"""Request body limits: no route ever holds more of a request body than its cap (STU-016 review F-4).

Every route of Studio reads its whole JSON body and then checks the size. Without a limit in front of them, the server
would buffer a body of any size first, before the session check on ``/api/session`` and on every authenticated route.
:class:`BodyLimitMiddleware` sits inside the access middleware (so a refused request is never read at all) and in
front of the routes:

- A ``Content-Length`` over the route's cap is refused without keeping a byte of the body. A ``Content-Length`` that
  is not one plain decimal number is ``access.bad_request``.
- A body without ``Content-Length`` (``Transfer-Encoding: chunked``) is read chunk by chunk and refused as soon as it
  passes the cap, so the server holds at most the cap plus one chunk.
- A body within the cap is read here and handed to the route unchanged; the route's own, often smaller, cap still
  applies.

The refusal is the route's :class:`BodyLimit` code (status 413) with ``Connection: close``.

**Bounded drain (review C-1 of ``sec-limits-review.md``).** A server that closes a socket with unread request bytes
makes the kernel send a TCP reset instead of a normal close, and the reset can destroy the 413 before the client
reads it: a page that sends a statement a little over the cap would see a network error. So before it answers, the
middleware reads the rest of the body and **discards** it, chunk by chunk, keeping nothing, for at most
:data:`DRAIN_BYTES` and :data:`DRAIN_SECONDS`. A declared ``Content-Length`` more than :data:`DRAIN_BYTES` over the cap
is answered at once without reading: such a client may see the reset, but the server never spends more than the bound
on it. Uvicorn closes the connection when the answer is complete, so the drain cannot come after the answer.

A request without a body (no ``Content-Length`` and no ``Transfer-Encoding``) passes untouched, so a streaming answer
still sees the client's disconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tabrion_studio.problems import problem, send_problem

#: How much of a refused body is read and discarded at most before the 413 (beyond what was read up to the cap).
DRAIN_BYTES = 4 * 1024 * 1024
#: How long the drain waits at most for the rest of a refused body.
DRAIN_SECONDS = 2.0


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
            size = limit.max_bytes + DRAIN_BYTES + 1 if len(declared) > 18 else int(declared)
            if size > limit.max_bytes:
                if size <= limit.max_bytes + DRAIN_BYTES and not await _drain(receive, size):
                    return  # the client went away
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
                if message.get("more_body", False) and not await _drain(receive, DRAIN_BYTES):
                    return  # the client went away
                await send_problem(send, problem(limit.code), close=True)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        await self.app(scope, _replay(b"".join(chunks), receive), send)


async def _drain(receive: Receive, budget: int) -> bool:
    """Read and discard the rest of a refused body: until its end, ``budget`` bytes or :data:`DRAIN_SECONDS`.

    False if the client went away (then nobody is left to answer).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DRAIN_SECONDS
    drained = 0
    while drained < budget:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return True
        try:
            message = await asyncio.wait_for(receive(), remaining)
        except TimeoutError:
            return True
        if message["type"] != "http.request":
            return False
        drained += len(message.get("body", b""))  # counted, never kept
        if not message.get("more_body", False):
            return True
    return True


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
