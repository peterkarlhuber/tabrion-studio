"""STU-016 review F-4: request bodies are capped while they stream in, before any route reads them.

``POST /api/session`` is reached before a session exists, so any local process could make the server buffer a body
of any size. Every route read its whole body first and checked the size afterwards. Now
:class:`tabrion_studio.security.body.BodyLimitMiddleware` refuses a ``Content-Length`` over the route's cap before
reading anything and stops a chunked body one byte past the cap. The tests drive the full ASGI app with a ``receive``
that counts what is read (and raises long before the old code would have finished), and one test sends raw HTTP to a
real Uvicorn server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextlib
import json
from pathlib import Path
import socket
import threading
import time
import tracemalloc
from typing import Any

import pytest
import uvicorn

from tabrion_studio.app import BODY_LIMITS, DEFAULT_BODY_LIMIT
from tabrion_studio.security import body as body_module
from tabrion_studio.security.body import DRAIN_BYTES, BodyLimit, BodyLimitMiddleware
from tabrion_studio.security.sessions import CSRF_HEADER, cookie_name
from tests.support.access import CONTROL_AUTH, EXPECTED_SECURITY_HEADERS, HOST, ORIGIN, PORT, Studio, make_studio

pytestmark = pytest.mark.security

#: A body the old code would have read in full; the counting receive raises once this much more than the cap is read.
OVERSHOOT = DRAIN_BYTES + 1024 * 1024


class Exhausted(Exception):
    """The app read far past the cap: the body was buffered, not refused."""


class Body:
    """An endless request body in chunks of ``chunk`` bytes that counts what the app read."""

    def __init__(self, chunk: int, cap: int) -> None:
        self.chunk = chunk
        self.cap = cap
        self.read = 0
        self.calls = 0

    async def receive(self) -> dict[str, Any]:
        self.calls += 1
        if self.read > self.cap + OVERSHOOT:
            raise Exhausted
        self.read += self.chunk
        return {"type": "http.request", "body": b"x" * self.chunk, "more_body": True}


class Finite:
    """A request body of ``size`` bytes in chunks of ``chunk`` bytes."""

    def __init__(self, size: int, chunk: int) -> None:
        self.left = size
        self.chunk = chunk

    async def receive(self) -> dict[str, Any]:
        part = min(self.chunk, self.left)
        self.left -= part
        return {"type": "http.request", "body": b"x" * part, "more_body": self.left > 0}


def _call(app: Any, method: str, path: str, headers: list[tuple[str, str]], receive: Any) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
    }
    asyncio.run(app(scope, receive, send))
    return sent


def _answer(sent: list[dict[str, Any]]) -> tuple[int, str, dict[str, str]]:
    start = sent[0]
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    body = b"".join(message.get("body", b"") for message in sent[1:])
    return int(start["status"]), json.loads(body)["code"], headers


def _signed_in(studio: Studio) -> list[tuple[str, str]]:
    csrf = studio.sign_in()[CSRF_HEADER]
    session = studio.client.cookies[cookie_name(PORT)]
    return [("Cookie", f"{cookie_name(PORT)}={session}"), (CSRF_HEADER, csrf)]


def _base(origin: bool = True) -> list[tuple[str, str]]:
    headers = [("Host", HOST), ("Content-Type", "application/json")]
    return [*headers, ("Origin", ORIGIN)] if origin else headers


@pytest.fixture
def studio(static_dir: Path) -> Iterator[Studio]:
    yield make_studio(static_dir)


def _cases(studio: Studio) -> list[tuple[str, list[tuple[str, str]], BodyLimit]]:
    """Every route with its own cap, and a route on the default cap, each with the headers that pass the access
    checks."""
    signed_in = [*_base(), *_signed_in(studio)]
    control = [("Host", HOST), ("Content-Type", "application/json"), *CONTROL_AUTH.items()]
    cases = []
    for path, limit in sorted(BODY_LIMITS.items()):
        if path == "/api/session":
            cases.append((path, _base(), limit))
        elif path.startswith("/api/control/"):
            cases.append((path, control, limit))
        else:
            cases.append((path, signed_in, limit))
    cases.append(("/api/profiles", signed_in, DEFAULT_BODY_LIMIT))
    cases.append(("/api/connections", signed_in, DEFAULT_BODY_LIMIT))
    return cases


def test_the_caps_are_the_routes_own_caps() -> None:
    """The caps are no looser than before: 4 KB before a session, 2 MB for a statement, 16 KB for every other route."""
    assert BODY_LIMITS["/api/session"] == BodyLimit(4096, "access.body_too_large")
    assert BODY_LIMITS["/api/control/launch"].max_bytes == 4096
    assert BODY_LIMITS["/api/queries"] == BodyLimit(2 * 1024 * 1024, "studio.query_too_large")
    assert BODY_LIMITS["/api/exports"] == BodyLimit(2 * 1024 * 1024, "studio.query_too_large")
    assert BodyLimit(16 * 1024, "access.body_too_large") == DEFAULT_BODY_LIMIT


def test_a_content_length_far_over_the_cap_is_refused_before_reading(studio: Studio) -> None:
    for path, headers, limit in _cases(studio):
        for declared in (limit.max_bytes + DRAIN_BYTES + 1, 400 * 1024 * 1024, 10**30):
            body = Body(1024, limit.max_bytes)
            sent = _call(studio.client.app, "POST", path, [*headers, ("Content-Length", str(declared))], body.receive)
            status, code, answer_headers = _answer(sent)
            assert (status, code) == (413, limit.code), (path, declared)
            assert body.calls == 0, path  # not a single byte read
            assert answer_headers["connection"] == "close"
            for name, value in EXPECTED_SECURITY_HEADERS.items():
                assert answer_headers[name] == value, (path, name)
    assert len(studio.runtime.sessions) == 1  # only the sign-in of _cases
    assert studio.runtime.launches.pending == 0 and studio.shutdowns == []


def test_a_content_length_a_little_over_the_cap_is_drained_then_refused(studio: Studio) -> None:
    """Review C-1: the rest of the body is read (and discarded) before the 413, so the server never closes a socket
    with unread bytes, which would turn the answer into a TCP reset. The route is never reached."""
    for path, headers, limit in _cases(studio):
        for declared in (limit.max_bytes + 1, limit.max_bytes + DRAIN_BYTES):
            parts = Finite(declared, 64 * 1024)
            sent = _call(studio.client.app, "POST", path, [*headers, ("Content-Length", str(declared))], parts.receive)
            status, code, answer_headers = _answer(sent)
            assert (status, code, answer_headers["connection"]) == (413, limit.code, "close"), (path, declared)
            assert parts.left == 0, path  # read to the end before the answer
    assert len(studio.runtime.sessions) == 1
    assert studio.runtime.launches.pending == 0 and studio.shutdowns == []


@pytest.mark.parametrize("chunk", [1, 1000])
def test_a_chunked_body_is_kept_only_one_chunk_past_the_cap(
    studio: Studio, chunk: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the drain off, the read stops with the chunk that passes the cap (the drain is checked below)."""
    monkeypatch.setattr(body_module, "DRAIN_BYTES", 0)
    for path, headers, limit in _cases(studio):
        if chunk == 1 and limit.max_bytes > 64 * 1024:
            continue  # two million one-byte reads add nothing over the 1000-byte chunks
        body = Body(chunk, limit.max_bytes)
        sent = _call(studio.client.app, "POST", path, [*headers, ("Transfer-Encoding", "chunked")], body.receive)
        assert _answer(sent)[:2] == (413, limit.code), path
        # The read stops with the chunk that passes the cap: cap + 1 byte for one-byte chunks.
        assert limit.max_bytes < body.read <= limit.max_bytes + chunk, (path, body.read)
        if chunk == 1:
            assert body.read == limit.max_bytes + 1
    assert len(studio.runtime.sessions) == 1
    assert studio.runtime.launches.pending == 0 and studio.shutdowns == []


def test_the_drain_discards_and_is_bounded(studio: Studio) -> None:
    """An endless body: the drain reads at most DRAIN_BYTES past the cap, keeps none of it (peak memory far below the
    bytes read), then answers 413 and closes."""
    for path, headers, limit in _cases(studio):
        chunk = 64 * 1024
        body = Body(chunk, limit.max_bytes)
        tracemalloc.start()
        try:
            sent = _call(studio.client.app, "POST", path, [*headers, ("Transfer-Encoding", "chunked")], body.receive)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert _answer(sent)[:2] == (413, limit.code), path
        assert limit.max_bytes + DRAIN_BYTES <= body.read <= limit.max_bytes + DRAIN_BYTES + 2 * chunk, path
        assert peak < limit.max_bytes + 1024 * 1024, (path, peak)  # not the 4 MiB drained


def test_the_drain_waits_only_briefly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that stops sending in the middle of an over-cap body gets its 413 after DRAIN_SECONDS."""
    monkeypatch.setattr(body_module, "DRAIN_SECONDS", 0.05)
    reached: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append("app")

    first = [{"type": "http.request", "body": b"x" * 20, "more_body": True}]

    async def receive() -> dict[str, Any]:
        if first:
            return first.pop()
        await asyncio.Event().wait()  # never sends the rest
        raise AssertionError("unreachable")

    middleware = BodyLimitMiddleware(app, default=BodyLimit(10))
    started = time.monotonic()
    sent = _call(middleware, "POST", "/api/x", [("Transfer-Encoding", "chunked")], receive)
    assert _answer(sent)[:2] == (413, "access.body_too_large")
    assert time.monotonic() - started < 5 and reached == []


def test_a_client_that_leaves_during_the_drain_gets_no_answer() -> None:
    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": b"x" * 20, "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("reached the app")

    middleware = BodyLimitMiddleware(app, default=BodyLimit(10))
    assert _call(middleware, "POST", "/api/x", [("Transfer-Encoding", "chunked")], receive) == []


def test_a_refused_request_is_never_read(studio: Studio) -> None:
    """The access checks come first: no session, no Origin or no control token means no byte of the body is read."""
    for path, headers in (
        ("/api/queries", _base()),  # no session
        ("/api/profiles", _base()),
        ("/api/session", _base(origin=False)),  # no Origin
        ("/api/control/launch", [("Host", HOST), ("Content-Type", "application/json")]),  # no token
    ):
        for extra in (("Content-Length", "100"), ("Transfer-Encoding", "chunked")):
            body = Body(1024, 0)
            sent = _call(studio.client.app, "POST", path, [*headers, extra], body.receive)
            assert _answer(sent)[0] in (401, 403), path
            assert body.calls == 0, path


def test_a_body_within_the_cap_reaches_the_route(studio: Studio) -> None:
    """A chunked sign-in under the cap still works, and the route's own 4 KB check is unchanged."""
    token = json.dumps({"token": studio.launch_token()}).encode()
    parts = [token[:10], token[10:], b""]

    async def receive() -> dict[str, Any]:
        part = parts.pop(0)
        return {"type": "http.request", "body": part, "more_body": bool(parts)}

    sent = _call(studio.client.app, "POST", "/api/session", [*_base(), ("Transfer-Encoding", "chunked")], receive)
    assert sent[0]["status"] == 200
    assert len(studio.runtime.sessions) == 1
    exact = json.dumps({"token": "a" * 43, "pad": "x" * (4096 - 24 - 43)}).encode()
    assert len(exact) == 4096
    answer = studio.client.post("/api/session", content=exact, headers=dict(_base()))
    assert answer.json()["code"] == "access.launch_invalid"  # at the cap: read and checked by the route


@pytest.mark.parametrize("value", ["abc", "-1", "1, 2", "0x10", ""])
def test_a_malformed_content_length_is_refused(studio: Studio, value: str) -> None:
    body = Body(1024, 0)
    sent = _call(studio.client.app, "POST", "/api/session", [*_base(), ("Content-Length", value)], body.receive)
    assert _answer(sent)[:2] == (400, "access.bad_request")
    assert body.calls == 0


def test_two_content_lengths_are_refused(studio: Studio) -> None:
    body = Body(1024, 0)
    headers = [*_base(), ("Content-Length", "10"), ("Content-Length", "10")]
    assert _answer(_call(studio.client.app, "POST", "/api/session", headers, body.receive))[:2] == (
        400,
        "access.bad_request",
    )
    assert body.calls == 0


def test_a_request_without_a_body_passes_untouched() -> None:
    """No Content-Length and no Transfer-Encoding: the route's own receive (and the client's disconnect) is used."""
    seen: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(receive)

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        raise AssertionError("the middleware answered")

    middleware = BodyLimitMiddleware(app, default=BodyLimit(1))
    asyncio.run(middleware({"type": "http", "path": "/api/queries/q1/events", "headers": []}, receive, send))
    assert seen == [receive]


def test_a_client_that_leaves_while_sending_gets_no_answer() -> None:
    reached: list[str] = []
    sent: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append("app")

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        sent.append(message)

    middleware = BodyLimitMiddleware(app, default=BodyLimit(10))
    scope = {"type": "http", "path": "/api/session", "headers": [(b"content-length", b"5")]}
    asyncio.run(middleware(scope, receive, send))
    assert reached == [] and sent == []


# --------------------------------------------------------------------------------------------------------------------
# On the wire: a real Uvicorn server answers 413 and closes the connection instead of reading the body.
# --------------------------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def _server(app: Any) -> Iterator[int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.01)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def _read_answer(conn: socket.socket) -> bytes:
    """Everything the server sends until it closes the connection."""
    conn.settimeout(10)
    data = b""
    while chunk := conn.recv(65536):
        data += chunk
    return data


HEAD = (
    f"POST /api/session HTTP/1.1\r\nHost: {HOST}\r\nOrigin: {ORIGIN}\r\nContent-Type: application/json\r\n"
).encode()


def test_uvicorn_answers_a_declared_huge_body_at_once_and_closes(studio: Studio) -> None:
    with _server(studio.client.app) as port, socket.create_connection(("127.0.0.1", port)) as conn:
        # 400 MB announced, none sent: the answer comes without waiting for the body.
        conn.sendall(HEAD + b"Content-Length: 400000000\r\n\r\n")
        answer = _read_answer(conn)
    assert answer.startswith(b"HTTP/1.1 413 ")
    assert b"access.body_too_large" in answer
    assert b"\r\nconnection: close\r\n" in answer.lower()


CHUNK = b"400\r\n" + b"x" * 1024 + b"\r\n"  # one chunk of 1 KiB in chunked encoding
ROUNDS = 20


def test_uvicorn_answers_a_chunked_body_over_the_cap_with_413(studio: Studio) -> None:
    """8 KiB, twice the 4 KiB cap, in many small writes, then the end of the body: every time the client reads a 413,
    never a reset (review C-1: the server drains the body before it answers and closes)."""
    with _server(studio.client.app) as port:
        for _ in range(ROUNDS):
            with socket.create_connection(("127.0.0.1", port)) as conn:
                conn.sendall(HEAD + b"Transfer-Encoding: chunked\r\n\r\n")
                for _ in range(8):
                    conn.sendall(CHUNK)
                conn.sendall(b"0\r\n\r\n")
                answer = _read_answer(conn)
            assert answer.startswith(b"HTTP/1.1 413 ")
            assert b"access.body_too_large" in answer
            assert b"\r\nconnection: close\r\n" in answer.lower()


def test_uvicorn_answers_a_statement_over_the_cap_with_413(studio: Studio) -> None:
    """The page's case: a 3 MB body to /api/queries (cap 2 MB) with Content-Length, sent in full, then read."""
    signed_in = "".join(f"{key}: {value}\r\n" for key, value in _signed_in(studio))
    size = 3 * 1024 * 1024
    head = (
        f"POST /api/queries HTTP/1.1\r\nHost: {HOST}\r\nOrigin: {ORIGIN}\r\nContent-Type: application/json\r\n"
        f"{signed_in}Content-Length: {size}\r\n\r\n"
    ).encode()
    with _server(studio.client.app) as port:
        for _ in range(5):
            with socket.create_connection(("127.0.0.1", port)) as conn:
                conn.settimeout(10)
                conn.sendall(head)
                for _ in range(size // (64 * 1024)):
                    conn.sendall(b"x" * (64 * 1024))
                answer = _read_answer(conn)
            assert answer.startswith(b"HTTP/1.1 413 ")
            assert b"studio.query_too_large" in answer


def test_uvicorn_stops_reading_an_endless_chunked_body(studio: Studio) -> None:
    """A client that never stops sending: after the bounded drain the server answers and closes, so the client's
    writes fail long before the bound. Without the cap the route would buffer all of it."""
    bound = 64 * 1024 * 1024
    sent = 0
    with _server(studio.client.app) as port, socket.create_connection(("127.0.0.1", port)) as conn:
        conn.settimeout(10)
        conn.sendall(HEAD + b"Transfer-Encoding: chunked\r\n\r\n")
        block = CHUNK * 64
        with pytest.raises((BrokenPipeError, ConnectionResetError)):
            while sent < bound:
                conn.sendall(block)
                sent += len(block)
    assert sent < bound
