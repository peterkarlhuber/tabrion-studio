"""Browser sessions (STU-003, SQ-03, review F-09), on :class:`nodestro_session.SessionStore` (NS-003).

``POST /api/session`` exchanges a one-time launch token for a session. A session has two random values:

* the **session ID** in the cookie ``tabrion_studio_session_<port>`` (``HttpOnly; SameSite=Strict; Path=/``, no
  ``Secure`` on http loopback). The port is part of the name because cookies are not port-scoped: two Studio
  instances on ``127.0.0.1`` would otherwise overwrite each other's cookie;
* the **CSRF value**, returned once in the JSON answer. The page keeps it in ``sessionStorage``, which is scoped to
  the origin including the port, and sends it as the header ``X-Tabrion-CSRF`` with every API request.

Every request with a session needs both. A program on another local port that the browser visits receives the
Studio cookie (the browser sends cookies to every port of a host), but never the CSRF value, so the cookie alone is
useless. Only SHA-256 digests of the session IDs are kept, and the CSRF value is compared in constant time.

The store itself is the shared library's; this module keeps Studio's names (cookie prefix, CSRF header, bound) and
registers every new value with the log redaction (STU-004). Studio's sessions have no idle time or maximum age of
their own: the server's idle timeout ends them with the process.
"""

from __future__ import annotations

from nodestro_session import SessionStore
from nodestro_session import cookie_name as _cookie_name

from tabrion_studio.logs import register_secret

CSRF_HEADER = "X-Tabrion-CSRF"
COOKIE_PREFIX = "tabrion_studio_session_"
MAX_SESSIONS = 32


def cookie_name(port: int) -> str:
    """The session cookie of the instance on ``port``."""
    return _cookie_name(COOKIE_PREFIX, port)


class Sessions(SessionStore):
    """The open sessions of this process (in memory only; a restart ends them). The oldest is dropped beyond
    ``max_sessions``."""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        # Every new session ID and CSRF value is never written to a log record (STU-004).
        super().__init__(max_sessions, on_secret=register_secret)
