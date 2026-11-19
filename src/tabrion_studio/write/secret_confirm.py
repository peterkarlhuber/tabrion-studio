"""The I-109 j confirmation of a write or admin secret (STU-XW-4; dialog D-15; threat TX-07; tabrion contract
section 5.9 I-109 j; review L-2 of PR #111).

Part of the write package: not imported while every write area is off (TX-02), never imported by a read path.

**The rule (I-109 j).** A profile file names both the host and where the secret comes from, and it may come from
somewhere the user does not control (a hand edit, a copied file, the later profile import of STU-XDM-5). So Studio
resolves the secret of a PostgreSQL write or admin profile only after the user has confirmed the **pair**: the
server the secret goes to (host, port, database, TLS mode, the write role) and the secret reference (``keyring:``,
``env:NAME``, ``file:PATH`` or none). Studio never resolves an ``env:`` or ``file:`` reference, or a keyring or
session secret, of an unconfirmed pair: the unlock is refused with ``studio.secret_unconfirmed`` before any secret is
read. A SQLite write profile has no secret and needs no confirmation.

**The flow** (the same shape as the plan confirmation of STU-XW-3):

1. **Preview** (``POST /api/write/secret-confirmations`` with ``{"write_profile_id"}``): the server reads the write
   profile and its read profile from the user's file (never from the request) and answers with the pair, whether the
   host is foreign, and a ``confirmation_hash``. The hash covers the session, a random nonce and the pair's digest;
   it is valid for :data:`PREVIEW_TTL_SECONDS` and in this session only.
2. **Confirm** (``POST /api/write/secret-confirmations/confirm`` with ``{"confirmation_hash", "typed"}``): the
   typed text must be the host exactly (the user reads where the secret goes, D-15). The server reads the pair again;
   if it is no longer the one previewed, the confirmation is refused (``studio.secret_confirmation_stale``). The
   confirmed pair digest is kept in ``write-secret-confirmations.json`` in the folder of ``write-areas.toml``, never in
   a state folder an environment variable chose (review N-1 of PR #116; 0600, written atomically, read without
   following a link), recorded in the audit log (``secret.confirm``; no host, no reference, only the pair digest).
3. **Revoke** (``DELETE /api/write/secret-confirmations`` with ``{"write_profile_id"}``): forgets it and ends the
   profile's unlocks.

Any change of the pair (another host, port, database, TLS mode, role or reference, a rename of the coupled profile
that changes its target) changes its digest, so the old confirmation no longer matches and the unlock is refused
until the user confirms the new pair. Every use of an unlock re-checks the confirmation (:meth:`Unlocks._recheck`),
so the plan confirmation and execution of STU-XW-3 refuse a run once the pair's confirmation is gone. A record that
is missing, unreadable, malformed, of another version or behind a link confirms nothing (fail closed).

**Foreign hosts.** A host that is not loopback (:func:`tabrion_studio.config.schema.is_loopback`: a socket folder,
``localhost`` or a loopback address; never a DNS name) is *foreign*. For a foreign host Studio requires the TLS mode
``verify-ca`` or ``verify-full`` (``studio.write_tls_required``, at preview, confirmation and unlock), and a write
connection that sends a password uses libpq ``require_auth=scram-sha-256``, there and on loopback alike (I-109 j,
review N-2 of PR #116). A write or admin connection without a password uses ``require_auth=!password,!md5``
(:mod:`tabrion_studio.write.connection`). The optional ``requirepeer`` of the write profile is part of the pair.

**A server on this computer (G-S2 decision 2A, residual risk 4).** A loopback host is not foreign, but libpq checks
who answers there only with TLS ``verify-full`` or, on a Unix socket, with ``requirepeer``. Without either, the pair
is :attr:`SecretPair.local_unverified`: a process that took the port or the socket first gets the SCRAM exchange
(no password, but an offline guess at a weak one). The D-15 dialog says so. With ``loopback_tls = "require"`` in
``write-areas.toml`` (:mod:`tabrion_studio.config.write_areas`; off by default) :func:`check_local_transport`
refuses to send a password over such a pair (``studio.write_loopback_tls_required``), before the secret is read
where the profile names a reference and in any case before it is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any

from tabrion_studio.config.schema import Profile, WriteProfile, is_loopback, refused_host
from tabrion_studio.paths import UnsafePathError, read_private_file, write_private_file
from tabrion_studio.write.connection import WriteError

__all__ = [
    "CONFIRMATION_FILE",
    "PREVIEW_TTL_SECONDS",
    "STRICT_SSLMODES",
    "SecretConfirmations",
    "SecretPair",
    "check_local_transport",
    "check_transport",
    "secret_pair",
]

logger = logging.getLogger("tabrion_studio.write")

#: The record's file name in Studio's state folder.
CONFIRMATION_FILE = "write-secret-confirmations.json"
CONFIRMATION_VERSION = 1
#: A preview's hash is valid this long (the plan previews of STU-XW-3 have the same limit).
PREVIEW_TTL_SECONDS = 5 * 60
#: The TLS modes a foreign host needs (I-109 j, TX-07).
STRICT_SSLMODES = frozenset({"verify-ca", "verify-full"})
#: Open previews per session and in all (older ones are dropped first).
MAX_PREVIEWS_PER_SESSION = 8
MAX_PREVIEWS = 128
#: Confirmed pairs kept at most (one per write or admin profile; profiles are capped at 200).
MAX_CONFIRMED = 256
_HASH = re.compile(r"[0-9a-f]{64}")
_NAME_LIMIT = 200


@dataclass(frozen=True)
class SecretPair:
    """The pair a write or admin secret belongs to: where it goes and where it comes from (I-109 j)."""

    write_name: str
    kind: str
    host: str
    port: int
    database: str | None
    sslmode: str | None
    user: str | None
    ref_source: str  # keyring, env, file or none
    ref_target: str  # the variable name or the path as written; "" for keyring and none
    #: The server's OS user libpq checks on a Unix socket (N-2 of PR #116); None when the profile names none.
    requirepeer: str | None = None

    @property
    def foreign(self) -> bool:
        """A host Studio cannot tell is this machine: anything but a socket folder, ``localhost`` or loopback IP."""
        return not is_loopback(self.host)

    @property
    def local_unverified(self) -> bool:
        """A server on this computer whose identity libpq does not check (G-S2 decision 2A): a loopback address or
        ``localhost`` without TLS ``verify-full``, or a Unix socket folder without ``requirepeer``."""
        if self.foreign:
            return False
        if self.host.startswith("/"):
            return self.requirepeer is None
        return self.sslmode != "verify-full"

    @property
    def digest(self) -> str:
        doc = [
            CONFIRMATION_VERSION,
            self.write_name,
            self.kind,
            self.host,
            self.port,
            self.database,
            self.sslmode,
            self.user,
            self.ref_source,
            self.ref_target,
        ]
        if self.requirepeer is not None:
            # Only when set, so the digest of a pair without it stays as it was; adding or removing it changes it.
            doc.append({"requirepeer": self.requirepeer})
        text = json.dumps(doc, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """What the dialog shows (D-15): the target and the reference, never a secret."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "sslmode": self.sslmode,
            "role": self.user,
            "reference": {"source": self.ref_source, "target": self.ref_target},
            "requirepeer": self.requirepeer,
            "foreign": self.foreign,
            "local_unverified": self.local_unverified,
        }


def secret_pair(write: WriteProfile, read: Profile) -> SecretPair | None:
    """The pair of ``write`` coupled to ``read``; None for SQLite (no secret, nothing to confirm)."""
    if read.engine != "postgresql":
        return None
    if read.host is None or refused_host(read.host) is not None:
        # A PostgreSQL profile always has one host (schema); a libpq host list is refused there too (M-1, PR #117).
        raise WriteError("studio.write_coupling_invalid")
    ref = write.password
    return SecretPair(
        write_name=write.name,
        kind=write.kind,
        host=read.host,
        port=read.port if read.port is not None else 5432,
        database=read.database,
        sslmode=read.sslmode,
        user=write.user,
        ref_source="none" if ref is None else ref.source,
        ref_target="" if ref is None else ref.target,
        requirepeer=write.requirepeer,
    )


def check_transport(pair: SecretPair) -> None:
    """A foreign host needs ``verify-ca`` or ``verify-full`` (I-109 j, TX-07); ``studio.write_tls_required`` else."""
    if refused_host(pair.host) is not None:
        # L-1 of #153: an abstract socket or a host list never gets a secret, whatever its sslmode says.
        raise WriteError("studio.write_coupling_invalid")
    if pair.foreign and pair.sslmode not in STRICT_SSLMODES:
        raise WriteError("studio.write_tls_required")


def check_local_transport(pair: SecretPair, *, required: bool) -> None:
    """With ``loopback_tls = "require"`` (``required``), a password never goes to a server on this computer whose
    identity libpq does not check (:attr:`SecretPair.local_unverified`): ``studio.write_loopback_tls_required``.
    The caller calls it only for a connection that sends a password (G-S2 decision 2A)."""
    if refused_host(pair.host) is not None:
        raise WriteError("studio.write_coupling_invalid")  # L-1 of #153, under either setting
    if required and pair.local_unverified:
        raise WriteError("studio.write_loopback_tls_required")


@dataclass(frozen=True)
class _Preview:
    session: str
    write_name: str
    pair_digest: str
    host: str
    expires_at: float


class SecretConfirmations:
    """The confirmed pairs (persisted in ``record``) and this process's open previews. Thread-safe.

    Without a ``record`` (no state folder) nothing can be confirmed: every PostgreSQL unlock is refused."""

    def __init__(self, record: Path | None, clock: Callable[[], float] = time.monotonic) -> None:
        self.record = record
        self.clock = clock
        self._previews: dict[str, _Preview] = {}
        self._lock = threading.Lock()

    # --- the record ----------------------------------------------------------------------------------------------

    def _read(self) -> dict[str, str]:
        """The confirmed digests by write profile name; empty for a missing or unusable record (fail closed)."""
        if self.record is None:
            return {}
        try:
            raw = read_private_file(self.record)
            if raw is None:
                return {}
            doc = json.loads(raw)
        except (OSError, UnsafePathError, ValueError, RecursionError):
            logger.warning("secret confirmations unreadable", extra={"event": "studio.secret_confirmations_unreadable"})
            return {}
        confirmed = doc.get("confirmed") if isinstance(doc, dict) else None
        if (
            not isinstance(doc, dict)
            or doc.get("version") != CONFIRMATION_VERSION
            or not isinstance(confirmed, dict)
            or len(confirmed) > MAX_CONFIRMED
        ):
            return {}
        return {
            name: value
            for name, value in confirmed.items()
            if isinstance(name, str)
            and 0 < len(name) <= _NAME_LIMIT
            and isinstance(value, str)
            and _HASH.fullmatch(value)
        }

    def _write(self, confirmed: dict[str, str]) -> None:
        if self.record is None:
            raise WriteError("studio.secret_confirmation_unavailable")
        doc = {"version": CONFIRMATION_VERSION, "confirmed": dict(sorted(confirmed.items()))}
        try:
            write_private_file(self.record, json.dumps(doc, ensure_ascii=True).encode("ascii"))
        except (OSError, UnsafePathError):
            raise WriteError("studio.secret_confirmation_unavailable") from None

    def confirmed_digest(self, write_name: str) -> str | None:
        return self._read().get(write_name)

    def is_confirmed(self, pair: SecretPair) -> bool:
        stored = self.confirmed_digest(pair.write_name)
        return stored is not None and secrets.compare_digest(stored, pair.digest)

    def require(self, pair: SecretPair) -> None:
        """Refuse (``studio.secret_unconfirmed``) unless the user confirmed exactly this pair."""
        if not self.is_confirmed(pair):
            raise WriteError("studio.secret_unconfirmed")

    def forget(self, write_name: str) -> bool:
        """Forget the confirmation of ``write_name``; True if there was one."""
        with self._lock:
            confirmed = self._read()
            if confirmed.pop(write_name, None) is None:
                return False
            self._write(confirmed)
            self._previews = {k: p for k, p in self._previews.items() if p.write_name != write_name}
        return True

    # --- preview and confirm -------------------------------------------------------------------------------------

    def preview(self, session: str, pair: SecretPair) -> dict[str, Any]:
        """A preview of ``pair`` for ``session``: the pair, the text to type and the confirmation hash."""
        check_transport(pair)
        if self.record is None:
            raise WriteError("studio.secret_confirmation_unavailable")
        now = self.clock()
        nonce = secrets.token_hex(16)
        text = json.dumps([session, nonce, pair.digest], ensure_ascii=True, separators=(",", ":"))
        confirmation_hash = hashlib.sha256(text.encode("ascii")).hexdigest()
        with self._lock:
            self._evict(session, now)
            self._previews[confirmation_hash] = _Preview(
                session, pair.write_name, pair.digest, pair.host, now + PREVIEW_TTL_SECONDS
            )
        return {
            "confirmation_hash": confirmation_hash,
            "pair": pair.to_dict(),
            "confirmed": self.is_confirmed(pair),
            "confirm_text": pair.host,
            "expires_in_s": PREVIEW_TTL_SECONDS,
        }

    def _evict(self, session: str, now: float) -> None:
        self._previews = {k: p for k, p in self._previews.items() if p.expires_at > now}
        own = [k for k, p in self._previews.items() if p.session == session]
        for key in own[: max(0, len(own) - MAX_PREVIEWS_PER_SESSION + 1)]:
            del self._previews[key]
        while len(self._previews) >= MAX_PREVIEWS:
            del self._previews[next(iter(self._previews))]

    def pending(self, session: str, confirmation_hash: object) -> str:
        """The write profile name of this session's open preview ``confirmation_hash``.

        ``studio.secret_confirmation_unknown`` for a malformed, unknown, expired or another session's hash."""
        if not isinstance(confirmation_hash, str) or _HASH.fullmatch(confirmation_hash) is None:
            raise WriteError("studio.secret_confirmation_unknown")
        with self._lock:
            preview = self._previews.get(confirmation_hash)
            if preview is None or not secrets.compare_digest(preview.session, session):
                raise WriteError("studio.secret_confirmation_unknown")
            if preview.expires_at <= self.clock():
                del self._previews[confirmation_hash]
                raise WriteError("studio.secret_confirmation_unknown")
        return preview.write_name

    def confirm(self, session: str, confirmation_hash: object, typed: object, pair: SecretPair) -> str:
        """Confirm the previewed pair; ``pair`` is the one the user's file has now (read again by the caller).

        A wrong typed text may be typed again; a pair that changed since the preview drops the preview
        (``studio.secret_confirmation_stale``). Returns the confirmed pair's digest."""
        self.pending(session, confirmation_hash)
        key = str(confirmation_hash)  # a hash of this session's open preview (checked by pending)
        with self._lock:
            preview = self._previews.get(key)
            if preview is None:
                raise WriteError("studio.secret_confirmation_unknown")
            if preview.write_name != pair.write_name or not secrets.compare_digest(preview.pair_digest, pair.digest):
                del self._previews[key]
                raise WriteError("studio.secret_confirmation_stale")
            try:
                check_transport(pair)
            except WriteError:
                del self._previews[key]
                raise
            if not isinstance(typed, str) or not secrets.compare_digest(
                typed.encode("utf-8", "surrogatepass"), preview.host.encode("utf-8", "surrogatepass")
            ):
                raise WriteError("studio.secret_confirmation_mismatch")
            confirmed = self._read()
            confirmed[pair.write_name] = pair.digest
            if len(confirmed) > MAX_CONFIRMED:
                raise WriteError("studio.secret_confirmation_unavailable")
            self._write(confirmed)
            del self._previews[key]
        logger.info("write secret reference confirmed", extra={"event": "studio.secret_confirmed"})
        return pair.digest
