"""Idle exit (03 section 4.1): the server ends after a period without activity.

Activity is a request with a valid session (STU-003) or an authenticated control request (review F-06); health
checks, static files and refused requests are not. The open frontend tab sends a heartbeat (``GET /api/heartbeat``
with its session) every :data:`HEARTBEAT_SECONDS`, so the instance stays up while a tab is open and ends about
``timeout`` after the last tab closed. The SSE heartbeat of the query API (STU-011) will replace the polling. A
timeout of 0 turns it off.
"""

from __future__ import annotations

from collections.abc import Callable
import time

DEFAULT_IDLE_MINUTES = 30.0
HEARTBEAT_SECONDS = 60


class IdleMonitor:
    def __init__(self, timeout_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if timeout_seconds < 0:
            raise ValueError("the idle timeout must not be negative")
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._last = clock()

    @property
    def enabled(self) -> bool:
        return self.timeout_seconds > 0

    def touch(self) -> None:
        self._last = self._clock()

    def idle_for(self) -> float:
        return self._clock() - self._last

    def expired(self) -> bool:
        return self.enabled and self.idle_for() >= self.timeout_seconds

    def check_interval(self) -> float:
        """How often to check: a tenth of the timeout, between 0.2 and 30 seconds."""
        return min(max(self.timeout_seconds / 10, 0.2), 30.0)
