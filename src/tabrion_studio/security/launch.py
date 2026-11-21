"""One-time launch tokens and redirect files (03 section 4.1, STU-003, threat T-03, review F-02), on
:class:`nodestro_session.LaunchTokens` (NS-003).

A launch URL ``http://<address>:<port>/launch#<token>`` (``<address>`` the instance's ``127.a.b.c``, A-01) carries a
random 256-bit token in its fragment, so that it never reaches a server log or a ``Referer``. The page at ``/launch``
sends the token to ``POST /api/session`` and gets a session (:mod:`.sessions`). A token works once (a second use gives
``access.launch_used``) and only for :data:`LAUNCH_TTL_SECONDS` (``access.launch_expired``).

The browser is never started with that URL (review F-02): the argv of ``xdg-open``, ``open`` or the browser is
readable by every local user. Instead a **redirect file** is written into the private state directory (0600 in a
0700 directory)::

    <meta http-equiv="refresh" content="0;url=http://<address>:<port>/launch#<token>">

and the browser is opened with its ``file://`` path, which holds no secret. The file is deleted as soon as its token
is redeemed, when the token expires and when the server stops. ``--print-url`` prints a launch URL instead, as an
explicit opt-in. Standard library and the shared library only (the CLI uses :func:`redirect_page` and
:func:`redirect_file_pattern`).

This module keeps Studio's names: the redirect files start with ``studio-<key>``, the redirect page names Tabrion
Studio, and every token is registered with the log redaction (STU-004).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import time

from nodestro_session import Launch, Redemption, redirect_file_pattern
from nodestro_session import LaunchTokens as _LaunchTokens
from nodestro_session import redirect_page as _redirect_page
from nodestro_session.tokens import LAUNCH_PATH, LAUNCH_TTL_SECONDS, TOKEN_PATTERN

from tabrion_studio.logs import register_secret

__all__ = [
    "LAUNCH_PATH",
    "LAUNCH_TTL_SECONDS",
    "PRODUCT_NAME",
    "TOKEN_PATTERN",
    "Launch",
    "LaunchTokens",
    "Redemption",
    "redirect_file_pattern",
    "redirect_page",
]

#: The name the redirect page shows while it opens Studio.
PRODUCT_NAME = "Tabrion Studio"


def redirect_page(url: str) -> bytes:
    """The content of a redirect file that sends the browser to ``url``."""
    return _redirect_page(url, PRODUCT_NAME)


class LaunchTokens(_LaunchTokens):
    """The launch tokens of one server: issued, redeemed once, expired after ``ttl`` seconds.

    ``origin`` is ``http://<address>:<port>``; redirect files go to ``redirect_dir`` with names that start with
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
        # Every token is never written to a log record (STU-004).
        super().__init__(
            origin, redirect_dir, prefix, ttl, clock, path=LAUNCH_PATH, name=PRODUCT_NAME, on_secret=register_secret
        )
