"""Browser sessions (STU-003, SQ-03, review F-09).

``POST /api/session`` exchanges a one-time launch token for a session. A session has two random values:

* the **session ID** in the cookie ``tabrion_studio_session_<port>`` (``HttpOnly; SameSite=Strict; Path=/``, no
  ``Secure`` on http loopback). The port is part of the name because cookies are not port-scoped: two Studio
  instances on ``127.0.0.1`` would otherwise overwrite each other's cookie;
* the **CSRF value**, returned once in the JSON answer. The page keeps it in ``sessionStorage``, which is scoped to
  the origin including the port, and sends it as the header ``X-Tabrion-CSRF`` with every API request.

Every request with a session needs both. A program on another local port that the browser visits receives the
Studio cookie (the browser sends cookies to every port of a host), but never the CSRF value, so the cookie alone is
useless. Only SHA-256 digests of the session IDs are kept, and the CSRF value is compared in constant time.
"""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import hmac
import secrets
import threading

CSRF_HEADER = "X-Tabrion-CSRF"
COOKIE_PREFIX = "tabrion_studio_session_"
MAX_SESSIONS = 32
_MAX_VALUE_LENGTH = 128


def cookie_name(port: int) -> str:
    """The session cookie of the instance on ``port``."""
    return f"{COOKIE_PREFIX}{port}"


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class Sessions:
    """The open sessions of this process (in memory only; a restart ends them). The oldest is dropped beyond
    ``max_sessions``."""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._max = max_sessions
        self._csrf: OrderedDict[bytes, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._csrf)

    def create(self) -> tuple[str, str]:
        """Open a session; returns (session ID for the cookie, CSRF value for the page)."""
        session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            self._csrf[_digest(session_id)] = csrf.encode("ascii")
            while len(self._csrf) > self._max:
                self._csrf.popitem(last=False)
        return session_id, csrf

    def known(self, session_id: str | None) -> bool:
        """Whether ``session_id`` names an open session (the CSRF value is not checked)."""
        if not session_id or len(session_id) > _MAX_VALUE_LENGTH:
            return False
        with self._lock:
            return _digest(session_id) in self._csrf

    def check(self, session_id: str | None, csrf: str | None) -> bool:
        """Whether ``session_id`` names an open session and ``csrf`` is its CSRF value."""
        if not session_id or not csrf or len(session_id) > _MAX_VALUE_LENGTH or len(csrf) > _MAX_VALUE_LENGTH:
            return False
        with self._lock:
            expected = self._csrf.get(_digest(session_id))
        return expected is not None and hmac.compare_digest(expected, csrf.encode("utf-8"))
