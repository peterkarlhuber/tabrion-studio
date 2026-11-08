"""A program that took over the port of a Studio instance (review F-01 regression tests)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]


@dataclass
class FakeStudio:
    """Answers like a Studio: health with ``instance_id``, a proof computed by ``prove`` and ``launch_url``."""

    instance_id: str
    prove: Callable[[str], str | None]
    launch_url: str = ""
    seen: list[Seen] = field(default_factory=list)
    port: int = 0

    @property
    def authorization_seen(self) -> bool:
        return any("authorization" in {k.lower() for k in r.headers} for r in self.seen)

    @property
    def posts(self) -> list[Seen]:
        return [r for r in self.seen if r.method == "POST"]


@contextlib.contextmanager
def fake_studio(instance_id: str, prove: Callable[[str], str | None], launch_url: str = "") -> Iterator[FakeStudio]:
    fake = FakeStudio(instance_id, prove, launch_url)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _answer(self, status: int, doc: object) -> None:
            body = json.dumps(doc).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _record(self) -> str:
            fake.seen.append(Seen(self.command, self.path, dict(self.headers.items())))
            return urlsplit(self.path).path

        def do_GET(self) -> None:
            path = self._record()
            if path == "/api/health":
                self._answer(200, {"status": "ok", "version": "0.1.0.dev0", "instance": fake.instance_id})
            elif path == "/api/control/proof":
                nonce = parse_qs(urlsplit(self.path).query).get("nonce", [""])[0]
                proof = fake.prove(nonce)
                if proof is None:
                    self._answer(404, {"detail": "Not Found"})
                else:
                    self._answer(200, {"proof": proof})
            else:
                self._answer(404, {"detail": "Not Found"})

        def do_POST(self) -> None:
            path = self._record()
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if path == "/api/control/launch":
                self._answer(200, {"url": fake.launch_url})
            elif path == "/api/control/shutdown":
                self._answer(202, {"status": "stopping"})
            else:
                self._answer(404, {"detail": "Not Found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
