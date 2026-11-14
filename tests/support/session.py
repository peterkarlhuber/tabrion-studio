"""Sign in to a running Studio like the launch page does, without a browser (STU-003 process tests)."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json

from tabrion_studio.instance import InstanceInfo, control
from tabrion_studio.security.sessions import CSRF_HEADER, cookie_name


@dataclass(frozen=True)
class Session:
    port: int
    cookie: str
    csrf: str
    #: The instance's loopback address (a random 127.a.b.c since A-01).
    host: str = "127.0.0.1"

    def headers(self) -> dict[str, str]:
        return {"Cookie": self.cookie, CSRF_HEADER: self.csrf, "Sec-Fetch-Site": "same-origin"}


def request(
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: object = None,
    host: str = "127.0.0.1",
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        data = None if body is None else json.dumps(body).encode()
        all_headers = dict(headers or {})
        if data is not None:
            all_headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=all_headers)
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


def redeem(port: int, token: str, host: str = "127.0.0.1") -> tuple[int, dict[str, str], bytes]:
    """``POST /api/session`` as the page at /launch sends it."""
    origin = f"http://{host}:{port}"
    headers = {"Origin": origin, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
    return request(port, "POST", "/api/session", headers, {"token": token}, host=host)


def session_from(port: int, status: int, headers: dict[str, str], body: bytes, host: str = "127.0.0.1") -> Session:
    assert status == 200, body
    name, _, rest = headers["set-cookie"].partition("=")
    assert name == cookie_name(port)
    return Session(port, f"{name}={rest.split(';', 1)[0]}", json.loads(body)["csrf"], host)


def sign_in(info: InstanceInfo) -> Session:
    """Ask the instance for a launch over the control channel and redeem its token."""
    url = control(info, "launch", body={"redirect": False})["url"]
    return session_from(info.port, *redeem(info.port, url.split("#", 1)[1], host=info.host), host=info.host)


def heartbeat(session: Session) -> int:
    return request(session.port, "GET", "/api/heartbeat", session.headers(), host=session.host)[0]
