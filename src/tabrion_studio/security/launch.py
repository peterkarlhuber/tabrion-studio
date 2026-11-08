"""One-time launch tokens and redirect files (03 section 4.1, STU-003, threat T-03, review F-02).

A launch URL ``http://127.0.0.1:<port>/launch#<token>`` carries a random 256-bit token in its fragment, so that it
never reaches a server log or a ``Referer``. The page at ``/launch`` sends the token to ``POST /api/session`` and gets
a session (:mod:`.sessions`). A token works once (a second use gives ``access.launch_used``) and only for
:data:`LAUNCH_TTL_SECONDS` (``access.launch_expired``).

The browser is never started with that URL (review F-02): the argv of ``xdg-open``, ``open`` or the browser is
readable by every local user. Instead a **redirect file** is written into the private state directory (0600 in a
0700 directory)::

    <meta http-equiv="refresh" content="0;url=http://127.0.0.1:<port>/launch#<token>">

and the browser is opened with its ``file://`` path, which holds no secret. The file is deleted as soon as its token
is redeemed, when the token expires and when the server stops. ``--print-url`` prints a launch URL instead, as an
explicit opt-in. Standard library only (the CLI uses :func:`redirect_page` and :func:`redirect_file_pattern`).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import hashlib
import html
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Literal

from tabrion_studio.paths import remove_file, write_private_file

LAUNCH_PATH = "/launch"
LAUNCH_TTL_SECONDS = 120.0
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_SPENT_KEPT = 256

Redemption = Literal["ok", "used", "expired", "invalid"]


def redirect_page(url: str) -> bytes:
    """The content of a redirect file that sends the browser to ``url``."""
    target = html.escape(url, quote=True)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        f'<meta http-equiv="refresh" content="0;url={target}">'
        "<title>Tabrion Studio</title></head>"
        "<body><p>Opening Tabrion Studio…</p></body></html>\n"
    ).encode()


def redirect_file_pattern(prefix: str) -> re.Pattern[str]:
    """The names of the redirect files of the instance whose state files start with ``prefix``."""
    return re.compile(re.escape(prefix) + r"-launch-[0-9a-f]{16}\.html")


@dataclass(frozen=True)
class Launch:
    """An issued launch: the URL with the token, and the redirect file if one was written."""

    url: str
    redirect: Path | None


@dataclass
class _Pending:
    expires: float
    redirect: Path | None


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class LaunchTokens:
    """The launch tokens of one server: issued, redeemed once, expired after ``ttl`` seconds.

    ``origin`` is ``http://127.0.0.1:<port>``; redirect files go to ``redirect_dir`` with names that start with
    ``prefix`` (``studio-<key>``, next to the instance's lock and info files). Only SHA-256 digests of the tokens are
    kept.
    """

    def __init__(
        self,
        origin: str,
        redirect_dir: Path | None = None,
        prefix: str = "studio",
        ttl: float = LAUNCH_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.origin = origin.rstrip("/")
        self.redirect_dir = redirect_dir
        self.prefix = prefix
        self.ttl = ttl
        self._clock = clock
        self._pending: dict[bytes, _Pending] = {}
        self._spent: OrderedDict[bytes, Redemption] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        return len(self._pending)

    def issue(self, *, redirect: bool) -> Launch:
        """A new one-time launch URL; with ``redirect``, also a redirect file that opens it."""
        token = secrets.token_urlsafe(32)
        url = f"{self.origin}{LAUNCH_PATH}#{token}"
        path: Path | None = None
        if redirect:
            if self.redirect_dir is None:
                raise RuntimeError("no directory for redirect files")
            path = self.redirect_dir / f"{self.prefix}-launch-{secrets.token_hex(8)}.html"
            write_private_file(path, redirect_page(url))
        with self._lock:
            self._pending[_digest(token)] = _Pending(self._clock() + self.ttl, path)
        return Launch(url, path)

    def redeem(self, token: object) -> Redemption:
        """Use ``token`` once: ``ok``, or why it is refused (``used``, ``expired``, ``invalid``)."""
        if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
            return "invalid"
        key = _digest(token)
        with self._lock:
            entry = self._pending.pop(key, None)
            if entry is None:
                return self._spent.get(key, "invalid")
            result: Redemption = "ok" if self._clock() < entry.expires else "expired"
            self._remember(key, "used" if result == "ok" else "expired")
        self._remove(entry.redirect)
        return result

    def sweep(self) -> int:
        """Expire old tokens and delete their redirect files; returns how many expired."""
        now = self._clock()
        with self._lock:
            expired = [(key, entry) for key, entry in self._pending.items() if now >= entry.expires]
            for key, _ in expired:
                del self._pending[key]
                self._remember(key, "expired")
        for _, entry in expired:
            self._remove(entry.redirect)
        return len(expired)

    def close(self) -> None:
        """Invalidate every pending token and delete every redirect file (the server stops)."""
        with self._lock:
            entries = list(self._pending.items())
            self._pending.clear()
            for key, _ in entries:
                self._remember(key, "expired")
        for _, entry in entries:
            self._remove(entry.redirect)

    def _remember(self, key: bytes, reason: Redemption) -> None:
        self._spent[key] = reason
        while len(self._spent) > _SPENT_KEPT:
            self._spent.popitem(last=False)

    @staticmethod
    def _remove(path: Path | None) -> None:
        if path is not None:
            # A leftover file holds a dead token; the next start of this instance removes it.
            with contextlib.suppress(OSError):
                remove_file(path)
