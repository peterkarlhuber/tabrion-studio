"""Unlocking the write mode (STU-XW-1; STU-ADR-0006 "rules carried over", threats TX-03, TX-04, TX-05, TX-11, TX-16).

An unlock lives only in this server process (a restart ends every one) and is bound to:

* the **session** (the digest of the session cookie the access middleware checked, never the value), so another tab
  or browser does not inherit it;
* the **connection** (the read connection's ID in the registry), so a reconnect, a disconnect or a connection that
  turned stale ends it (the registry tells :meth:`Unlocks.connection_closed`);
* the **write or admin profile** and the read profile it is coupled to, both as stored in the user's
  ``profiles.toml`` at the moment of the unlock (their fingerprints and the read profile's environment).

It is made only on the server, by typing the write profile's name exactly (D-03), and only when the area of the
profile's kind is on (``write``: the Data Editor or the Schema Designer flag; ``admin``: the Database Manager flag).
It expires after :data:`UNLOCK_IDLE_SECONDS` without use; the expiry is checked on the server for every use
(:meth:`Unlocks.require`), never by the page.

**PROD (E-X7).** A PROD read profile cannot have a write profile coupled to it (the profile file refuses it), and the
environment is read again from the stored profile on every unlock and every use, never from the request: ``prod``
refuses (``studio.write_prod_locked``). There is no code path that opens a write connection for PROD.

**Ending.** An unlock ends on: the lock route, the expiry, the read connection closing (disconnect, reconnect,
stale), a change of the read profile, the write profile or the coupling (an edit through Studio tells
:meth:`Unlocks.profile_changed`; a hand edit of the file is seen on the next use because the fingerprints differ),
a SQLite file that is no longer the one checked at unlock, and the server stopping. A change of the **environment**
of a coupled read profile ends every unlock (TX-04).

:meth:`Unlocks.require` is the hook every later write entry point calls first (STU-XW-3, STU-026, STU-XSD, STU-XDM):
it re-checks all of the above and returns the write connection; it never hands one to a read path.
:meth:`Unlocks.audited_transaction` is the hook that runs a write: it calls ``require``, writes the ``run.start``
record of the audit log before the transaction begins (no record, no run) and the ``run.result`` record after it
committed or rolled back (a pending marker if that record fails, review N-4).

**Secret confirmation and authentication (STU-XW-4, I-109 j, D-15, TX-07).** A PostgreSQL unlock first checks
that libpq has ``require_auth`` (16 or newer; ``studio.require_auth_unavailable`` otherwise), that a foreign host has
``verify-ca`` or ``verify-full`` (``studio.write_tls_required``) and that the user confirmed the pair of server and
secret reference (``studio.secret_unconfirmed``), all before any secret is resolved
(:mod:`tabrion_studio.write.secret_confirm`). The unlock keeps the confirmed pair's digest; every use re-checks it,
so a revoked or changed confirmation ends the unlock and refuses a plan's confirmation and run (STU-XW-3).
With ``loopback_tls = "require"`` in ``write-areas.toml`` (G-S2 decision 2A; ``"accept"`` by default) a password
never goes to a server on this computer that libpq cannot check (no TLS ``verify-full``, no socket with
``requirepeer``): ``studio.write_loopback_tls_required``, before a referenced secret is read and before any password
is sent. With ``"accept"`` such an unlock goes on, its state says ``local_unverified`` and it is logged.

**Audit (STU-XW-2).** Every unlock is recorded before it takes effect (a log that cannot take the record refuses
the unlock, ``studio.audit_unavailable``); a refused unlock of a stored profile and every lock (with its reason) are
recorded after the fact (:mod:`tabrion_studio.write.audit`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import contextlib
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
import secrets
import threading
import time
from typing import Any

import tabrion

from tabrion_studio.api.config import Configuration
from tabrion_studio.config.schema import Profile, WriteProfile, coupling_issue, target_fingerprint
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.config.write_areas import Area, WriteAreas
from tabrion_studio.logs import register_secret
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection, RegistryError, refuses_cleartext
from tabrion_studio.write.audit import AuditError, AuditLog
from tabrion_studio.write.connection import (
    WriteConnection,
    WriteError,
    createdb_findings,
    file_identity,
    open_write_connection,
    require_auth_supported,
)
from tabrion_studio.write.secret_confirm import (
    SecretConfirmations,
    check_local_transport,
    check_transport,
    secret_pair,
)

logger = logging.getLogger("tabrion_studio.write")

#: D-03: an unlock ends after 15 minutes without use.
UNLOCK_IDLE_SECONDS = 15 * 60
#: The areas whose flag opens a kind of profile; one area's flag never opens another's (TX-01, TX-15).
KIND_AREAS: dict[str, frozenset[Area]] = {
    "write": frozenset({Area.DATA_EDITOR, Area.SCHEMA_DESIGNER}),
    "admin": frozenset({Area.DATABASE_MANAGER}),
}
WRITE_ID_PREFIX = "write:"


def write_profile_id(write: WriteProfile) -> str:
    return f"{WRITE_ID_PREFIX}{write.name}"


def _fingerprint(doc: dict[str, Any]) -> str:
    text = json.dumps(doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


def _read_fingerprint(read: Profile) -> str:
    """The read profile's target and environment (an environment change is a change of the coupling)."""
    return f"{target_fingerprint(read.to_dict())}:{read.environment}"


def _write_fingerprint(write: WriteProfile) -> str:
    return _fingerprint(write.to_dict())


@dataclass
class Unlock:
    session: str
    connection_id: str
    write_name: str
    kind: str
    read_name: str
    read_fingerprint: str
    write_fingerprint: str
    environment: str
    connection: WriteConnection
    unlocked_at: float
    last_used: float
    #: The digest of the confirmed pair of server and secret reference (STU-XW-4); None for SQLite.
    pair_digest: str | None = None
    #: This unlock's own ID: a plan (STU-XW-3) is bound to it, so a plan made under an earlier unlock of the same
    #: session and connection never runs under a later one (review N-2). Never sent to the page.
    unlock_id: str = field(default_factory=lambda: secrets.token_hex(16))
    #: A PostgreSQL server on this computer whose identity libpq does not check (G-S2 decision 2A): no TLS
    #: ``verify-full`` on a loopback address, no ``requirepeer`` on a socket.
    local_unverified: bool = False

    def to_dict(self, now: float) -> dict[str, Any]:
        """The write state the page shows (08 section 3): profile, kind, role, environment, expiry. No secret."""
        return {
            "connection_id": self.connection_id,
            "write_profile_id": f"{WRITE_ID_PREFIX}{self.write_name}",
            "kind": self.kind,
            "role": self.connection.role,
            "environment": self.environment,
            "engine": self.connection.engine,
            "expires_in_s": max(0, round(self.last_used + UNLOCK_IDLE_SECONDS - now)),
            "findings": list(self.connection.findings),
            "facts": dict(self.connection.facts),
            "local_unverified": self.local_unverified,
        }


@dataclass
class AuditedRun:
    """One write run inside :meth:`Unlocks.audited_transaction`: its transaction and the row counts it reports.

    ``audit_pending`` is True after the run when its ``run.result`` record could not be written and is kept as a
    pending marker (review N-4): the caller's answer then carries a warning."""

    run_id: str
    transaction: tabrion.Transaction | None = None
    rows: dict[str, int] = field(default_factory=dict)
    audit_pending: bool = False
    #: The unlock's write connection while the run lasts (STU-XW-3 reads the target's catalog on it for the drift
    #: check inside the transaction); None outside the run.
    connection: WriteConnection | None = None

    def count(self, kind: str, rows: int) -> None:
        """Add ``rows`` to the count of ``kind`` (``updated``, ``inserted``, ``deleted``): counts only, never rows."""
        self.rows[kind] = self.rows.get(kind, 0) + rows


class Unlocks:
    """The unlocks of this process, by session and connection. Thread-safe; unlocking connects (off the loop)."""

    def __init__(
        self,
        config: Configuration,
        registry: ConnectionRegistry,
        areas: WriteAreas,
        clock: Callable[[], float] = time.monotonic,
        audit: AuditLog | None = None,
        confirmations: SecretConfirmations | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.areas = areas
        self.clock = clock
        #: No audit log: every unlock and run is refused (fail closed).
        self.audit = audit if audit is not None else AuditLog(None)
        #: No record of confirmations: no PostgreSQL unlock (fail closed, I-109 j).
        self.confirmations = confirmations if confirmations is not None else SecretConfirmations(None)
        self._unlocks: dict[tuple[str, str], Unlock] = {}
        self._lock = threading.Lock()
        #: Called with an unlock's ``unlock_id`` after it ended, for whatever reason: what is bound to an unlock (the
        #: Schema Designer's drafts, STU-CR-4) ends with it. A listener never stops the lock.
        self.end_listeners: list[Callable[[str], None]] = []
        registry.on_close(self.connection_closed)
        config.listeners.append(self.profile_changed)

    # --- the profile store, read now -----------------------------------------------------------------------------

    def kind_open(self, kind: str) -> bool:
        return self.areas.any_on(tuple(KIND_AREAS.get(kind, frozenset())))

    def _stored(self, write_name: str) -> tuple[WriteProfile, Profile | None]:
        """The write profile and its read profile as the user's file has them now (never a project profile, K-1)."""
        user = self.config.user.profiles()
        write = next((w for w in user.write_profiles if w.name == write_name), None)
        if write is None:
            raise WriteError("studio.write_profile_unknown")
        read = next((p for p in user.profiles if p.name == write.read_profile), None)
        return write, read

    # --- unlocking -------------------------------------------------------------------------------------------------

    def unlock(self, session: str, connection_id: object, write_id: object, typed_name: object) -> dict[str, Any]:
        """Unlock the write mode of ``connection_id`` for this session with the write profile ``write_id``.

        Raises :class:`WriteError` (or :class:`RegistryError`, ``SecretError``, a tabrion error when connecting).
        """
        if not isinstance(write_id, str) or not write_id.startswith(WRITE_ID_PREFIX):
            raise WriteError("studio.write_profile_unknown")
        if not isinstance(connection_id, str):
            raise WriteError("studio.write_target_unknown")
        write, read = self._stored(write_id[len(WRITE_ID_PREFIX) :])
        if not self.kind_open(write.kind):
            raise WriteError("studio.method_not_allowed")  # W-9: the area of this kind is off
        try:
            return self._unlock(session, connection_id, write, read, typed_name)
        except Exception as exc:
            code = exc.code if isinstance(exc, WriteError) else "failed"
            if code != "studio.audit_unavailable":
                # A refused unlock of a stored profile, recorded after the fact: the stored names only, never what
                # the request typed or named (TX-13).
                # A coding error in the record must not hide the refusal (note N-3 of the PR #114 review).
                with contextlib.suppress(ValueError):
                    self.audit.record("unlock", profile=write.name, kind=write.kind, result=code)
            raise

    def _unlock(
        self, session: str, connection_id: str, write: WriteProfile, read: Profile | None, typed_name: object
    ) -> dict[str, Any]:
        try:
            self.audit.check()
        except AuditError:
            raise WriteError("studio.audit_unavailable") from None
        connection = self._read_connection(connection_id)
        if connection.profile_id != f"user:{write.read_profile}" or read is None:
            # K-1: only the user-local read profile it is coupled to; a project profile never.
            raise WriteError("studio.write_coupling_invalid")
        # E-X7, from the stored profile and the open connection, never from the request.
        if read.environment == "prod" or connection.environment == "prod":
            raise WriteError("studio.write_prod_locked")
        if target_fingerprint(read.to_dict()) != connection.fingerprint or read.environment != connection.environment:
            raise WriteError("studio.write_target_changed")
        if not isinstance(typed_name, str) or typed_name != write.name:
            raise WriteError("studio.unlock_name_mismatch")
        opened, pair_digest = self._open(write, read, connection)
        pair = secret_pair(write, read)  # None for SQLite
        local_unverified = pair is not None and pair.local_unverified
        # Open point 1 of the PR #128 review (b): CREATEDB where Studio does not need it is a warning, never a refusal.
        opened.findings += createdb_findings(
            opened.facts,
            kind=write.kind,
            environment=read.environment,
            data_editor_on=self.areas.on(Area.DATA_EDITOR),
        )
        try:
            # Before the unlock takes effect: no record, no unlock (fail closed).
            self.audit.append(
                "unlock",
                profile=write.name,
                kind=write.kind,
                read_profile=read.name,
                environment=read.environment,
                engine=read.engine,
                connection=connection.id,
                findings=len(opened.findings),
                result="ok",
            )
        except AuditError:
            with contextlib.suppress(Exception):
                opened.close()
            raise WriteError("studio.audit_unavailable") from None
        now = self.clock()
        record = Unlock(
            session=session,
            connection_id=connection.id,
            write_name=write.name,
            kind=write.kind,
            read_name=read.name,
            read_fingerprint=_read_fingerprint(read),
            write_fingerprint=_write_fingerprint(write),
            environment=read.environment,
            connection=opened,
            unlocked_at=now,
            last_used=now,
            pair_digest=pair_digest,
            local_unverified=local_unverified,
        )
        with self._lock:
            previous = self._unlocks.pop((session, connection.id), None)
            self._unlocks[(session, connection.id)] = record
        if previous is not None:
            self._close(previous, "replaced")
        profile_id = write_profile_id(write)
        logger.info("write mode unlocked", extra={"event": "studio.write_unlocked", "profile_id": profile_id})
        if local_unverified:
            # G-S2 decision 2A: accepted (loopback_tls = "accept"), and said each time.
            logger.warning(
                "the write or admin server is on this computer and its identity is not checked "
                "(no TLS verify-full, no requirepeer)",
                extra={"event": "studio.write_loopback_unverified", "profile_id": profile_id},
            )
        if opened.findings:
            logger.warning(
                "the write or admin role has more rights than Studio needs",
                extra={"event": "studio.write_role_over_privileged", "profile_id": profile_id},
            )
        return record.to_dict(now)

    def _read_connection(self, connection_id: str) -> OpenConnection:
        try:
            return self.registry.get(connection_id)
        except RegistryError as exc:
            raise WriteError(
                "studio.write_target_unknown"
                if exc.code == "studio.connection_not_found"
                else "studio.write_target_changed"
            ) from None

    def _open(
        self, write: WriteProfile, read: Profile, connection: OpenConnection
    ) -> tuple[WriteConnection, str | None]:
        profile = write.connection_profile(read)
        pair_digest: str | None = None
        limits = self.config.limits()
        if read.engine == "sqlite":
            entry = self.config.entry(connection.profile_id)
            path = self.config.database_file(entry)
            if path != connection.database_file:
                raise WriteError("studio.write_target_changed")
            identity = file_identity(path)
            if identity != self.registry.database_identity(connection):
                # TX-16: the file at the path is no longer the one the read connection opened.
                raise WriteError("studio.write_target_changed")
            rows = connection.fetch("PRAGMA journal_mode")
            mode = rows[0][0] if rows and rows[0] else None
            opened = open_write_connection(
                write.kind,
                profile,
                limits,
                database_file=path,
                journal_mode=mode if isinstance(mode, str) else None,
                identity=identity,
            )
        else:
            pair_digest, password = self._secret(write, read)
            entry = self.config.entry(connection.profile_id)
            opened = open_write_connection(
                write.kind,
                profile,
                limits,
                password=password,
                refuse_cleartext=refuses_cleartext(entry),
                requirepeer=write.requirepeer,
            )
        return opened, pair_digest

    def _secret(self, write: WriteProfile, read: Profile) -> tuple[str, str | None]:
        """The confirmed pair's digest and the PostgreSQL write or admin profile's password (None without one)."""
        # STU-XW-4, before any secret is read: libpq with require_auth, TLS for a foreign host, the confirmed pair.
        require_auth_supported()
        pair = secret_pair(write, read)
        if pair is None:  # pragma: no cover - a PostgreSQL profile always has a pair
            raise WriteError("studio.write_coupling_invalid")
        check_transport(pair)
        self.confirmations.require(pair)
        required = self.areas.loopback_tls_required
        if write.password is not None:
            # A reference names a password: refused before it is read (G-S2 decision 2A, loopback_tls = "require").
            check_local_transport(pair, required=required)
        password: str | None = None
        try:
            password = self.config.secrets.resolve(self.config.write_target(write, read))
        except SecretError as exc:
            # No reference and nothing entered: certificate, peer or trust authentication of the write role.
            if write.password is not None or exc.code != "studio.secret_missing":
                raise
        if password is not None:
            register_secret(password, lasting=True)
            # A password from the keyring or entered for the session: refused before it is sent.
            check_local_transport(pair, required=required)
        return pair.digest, password

    def credentials(self, write_name: str) -> tuple[WriteProfile, Profile, str | None]:
        """A stored PostgreSQL write or admin profile, its read profile and its password, with every check of an
        unlock before the secret is read (``require_auth``, the transport, the I-109 j confirmation; STU-XW-4).

        For the tools of the Database Manager (STU-XDM-2), which connect on their own: the password goes only into
        the tool's temporary passfile. Raises :class:`WriteError` (or ``SecretError``)."""
        write, read = self._stored(write_name)
        if read is None or read.engine != "postgresql" or coupling_issue(write, read) is not None:
            raise WriteError("studio.write_coupling_invalid")
        if read.environment == "prod":
            raise WriteError("studio.write_prod_locked")
        _digest, password = self._secret(write, read)
        return write, read, password

    def unlock_credentials(self, record: Unlock) -> tuple[WriteProfile, Profile, str | None]:
        """:meth:`credentials` of the profiles ``record`` was unlocked with, and only while they are still the ones it
        checked (their fingerprints): the Schema Designer's backup before a PostgreSQL run (the STU-CR-5 follow-up)
        dumps the database the run changes, never one a profile edit pointed elsewhere since.
        ``studio.write_target_changed`` otherwise."""
        write, read, password = self.credentials(record.write_name)
        if _write_fingerprint(write) != record.write_fingerprint or _read_fingerprint(read) != record.read_fingerprint:
            raise WriteError("studio.write_target_changed")
        return write, read, password

    def open_checked(self, write_name: str, database: str | None = None) -> WriteConnection:
        """A checked connection (``open_write_connection``) of the stored PostgreSQL write or admin profile
        ``write_name``, to its read profile's server and ``database`` (default: the read profile's own). Not an
        unlock: the caller closes it. For the Database Manager's restore role (its findings before a restore, review
        M-1 of STU-XDM-1) and the fingerprint of a restored database (STU-XDM-2)."""
        write, read, password = self.credentials(write_name)
        profile = write.connection_profile(read)
        if database is not None:
            profile = replace(profile, database=database)
        return open_write_connection(
            write.kind,
            profile,
            self.config.limits(),
            password=password,
            refuse_cleartext=False,  # a user profile (K-1): the write rules set require_auth anyway
            requirepeer=write.requirepeer,
        )

    # --- the hook of every write -----------------------------------------------------------------------------------

    def require(self, session: str, connection_id: object, area: Area) -> WriteConnection:
        """The write connection of this session's unlock of ``connection_id`` for ``area``, re-checked now.

        Every write entry point of a later work package calls this first. Raises :class:`WriteError`:
        ``studio.method_not_allowed`` (the area is off or not one of the profile's kind), ``studio.write_locked``
        (no unlock, or it expired), ``studio.write_prod_locked``, ``studio.write_target_changed``.
        """
        return self._require(session, connection_id, area).connection

    def require_unlock(self, session: str, connection_id: object, area: Area) -> Unlock:
        """Like :meth:`require`, but the unlock itself: STU-XW-3 binds a plan to its ``unlock_id`` and reads the
        target's catalog on its write connection."""
        return self._require(session, connection_id, area)

    def _require(self, session: str, connection_id: object, area: Area, unlock_id: str | None = None) -> Unlock:
        if not self.areas.on(area):
            raise WriteError("studio.method_not_allowed")
        key = (session, connection_id) if isinstance(connection_id, str) else None
        with self._lock:
            record = self._unlocks.get(key) if key is not None else None
        if record is None:
            raise WriteError("studio.write_locked")
        now = self.clock()
        if now - record.last_used > UNLOCK_IDLE_SECONDS:
            self._end(record, "expired")
            raise WriteError("studio.write_locked")
        if area not in KIND_AREAS[record.kind]:
            raise WriteError("studio.method_not_allowed")
        if unlock_id is not None and not secrets.compare_digest(record.unlock_id, unlock_id):
            # STU-XW-3, review N-2: a plan of an earlier unlock (locked and unlocked again) never runs.
            raise WriteError("studio.plan_binding")
        self._recheck(record)
        record.last_used = now
        return record

    @contextlib.contextmanager
    def audited_transaction(
        self,
        session: str,
        connection_id: object,
        area: Area,
        *,
        action: str,
        objects: Iterable[str] = (),
        plan_hash: str | None = None,
        unlock_id: str | None = None,
    ) -> Iterator[AuditedRun]:
        """Run one write: the hook of STU-XW-3 (plan execution) and STU-026 (changesets).

        1. :meth:`require` (flag, unlock, expiry, PROD, target, file identity);
        2. the ``run.start`` record (area, action, profile, environment, the names of ``objects``, ``plan_hash``):
           if it cannot be written, :class:`WriteError` ``studio.audit_unavailable`` and nothing runs (fail closed);
        3. one transaction of the write connection (``run.transaction``); the caller reports row counts with
           :meth:`AuditedRun.count`;
        4. the ``run.result`` record (``committed``, the problem code of a :class:`WriteError`, or ``failed``) with
           the counts. If it cannot be written, the run has happened: a pending marker is kept and
           ``run.audit_pending`` is True, so the caller shows a warning (review N-4).

        ``action`` is a token (``data_editor.changeset``), ``objects`` are object names and ``plan_hash`` is the
        confirmed plan's hash (STU-XW-3 binds it): never a value, a parameter or a statement (TX-13). ``unlock_id``
        is the unlock a plan was made under (STU-XW-3): another unlock refuses the run (``studio.plan_binding``)
        before anything is recorded or begins."""
        record = self._require(session, connection_id, area, unlock_id)
        run = AuditedRun(run_id=secrets.token_hex(8))
        common: dict[str, object] = {
            "run": run.run_id,
            "area": area.value,
            "action": action,
            "profile": record.write_name,
        }
        try:
            self.audit.append(
                "run.start",
                **common,
                kind=record.kind,
                environment=record.environment,
                engine=record.connection.engine,
                connection=record.connection_id,
                objects=list(objects),
                plan_hash=plan_hash,
            )
        except AuditError:
            logger.warning("write refused: no audit record", extra={"event": "studio.audit_unavailable"})
            raise WriteError("studio.audit_unavailable") from None
        except ValueError:
            # An action or object name the log does not take (empty, too many, not a token): a catalog problem,
            # nothing runs (note N-3 of the PR #114 review). STU-XW-3 and STU-026 validate and cap names before.
            logger.warning("write refused: audit record not valid", extra={"event": "studio.audit_record_invalid"})
            raise WriteError("studio.audit_record_invalid") from None
        result = "failed"
        try:
            with record.connection.transaction() as transaction:
                run.transaction = transaction
                run.connection = record.connection
                yield run
            result = "committed"
        except WriteError as exc:
            result = exc.code
            raise
        finally:
            run.transaction = None
            run.connection = None
            run.audit_pending = not self.audit.record("run.result", **common, result=result, rows=dict(run.rows))
            if run.audit_pending:
                logger.warning(
                    "the record of a write run is pending; the write has happened",
                    extra={"event": "studio.audit_pending"},
                )

    def _recheck(self, record: Unlock) -> None:
        try:
            write, read = self._stored(record.write_name)
            if read is None:
                raise WriteError("studio.write_target_changed")
            if read.environment == "prod":
                raise WriteError("studio.write_prod_locked")
            if (
                _write_fingerprint(write) != record.write_fingerprint
                or _read_fingerprint(read) != record.read_fingerprint
            ):
                raise WriteError("studio.write_target_changed")
            self._recheck_pair(record, write, read)
            self._read_connection(record.connection_id)
            record.connection.verify()
        except WriteError:
            self._end(record, "changed")
            raise
        except Exception:
            self._end(record, "changed")
            raise WriteError("studio.write_target_changed") from None

    def _recheck_pair(self, record: Unlock, write: WriteProfile, read: Profile) -> None:
        """The pair the unlock was confirmed for is still the stored one and still confirmed (STU-XW-4)."""
        pair = secret_pair(write, read)
        if pair is None and record.pair_digest is None:
            return
        if pair is None or record.pair_digest is None or pair.digest != record.pair_digest:
            raise WriteError("studio.write_target_changed")
        check_transport(pair)
        self.confirmations.require(pair)

    # --- the state, for the page ---------------------------------------------------------------------------------

    def list(self, session: str) -> list[dict[str, Any]]:
        """This session's unlocks that are still valid (an expired or changed one ends here)."""
        with self._lock:
            records = [record for (owner, _), record in self._unlocks.items() if owner == session]
        now = self.clock()
        alive = []
        for record in records:
            if now - record.last_used > UNLOCK_IDLE_SECONDS:
                self._end(record, "expired")
                continue
            try:
                self._recheck(record)
            except WriteError:
                continue
            alive.append(record.to_dict(now))
        return alive

    def lock(self, session: str, connection_id: object) -> bool:
        key = (session, connection_id) if isinstance(connection_id, str) else None
        with self._lock:
            record = self._unlocks.get(key) if key is not None else None
        if record is None:
            return False
        self._end(record, "locked")
        return True

    # --- ending ----------------------------------------------------------------------------------------------------

    def _end(self, record: Unlock, reason: str) -> None:
        with self._lock:
            current = self._unlocks.get((record.session, record.connection_id))
            if current is not record:
                return
            del self._unlocks[(record.session, record.connection_id)]
        self._close(record, reason)

    def _close(self, record: Unlock, reason: str) -> None:
        with contextlib.suppress(Exception):
            record.connection.close()
        for listener in list(self.end_listeners):
            with contextlib.suppress(Exception):
                listener(record.unlock_id)
        logger.info("write mode locked (%s)", reason, extra={"event": "studio.write_locked"})
        # After the fact: the lock has happened; a failed record is kept as pending (N-4).
        self.audit.record(
            "lock", profile=record.write_name, kind=record.kind, connection=record.connection_id, reason=reason
        )

    def _end_where(self, match: Callable[[Unlock], bool], reason: str) -> int:
        with self._lock:
            records = [record for record in self._unlocks.values() if match(record)]
        for record in records:
            self._end(record, reason)
        return len(records)

    def connection_closed(self, connection_id: str) -> None:
        """The registry closed a read connection (disconnect, reconnect, stale): its unlocks end."""
        self._end_where(lambda record: record.connection_id == connection_id, "connection_closed")

    def write_profile_changed(self, write_name: str) -> None:
        """A write or admin profile was saved or removed through Studio: its unlocks end."""
        self._end_where(lambda record: record.write_name == write_name, "write_profile_changed")

    def profile_changed(self, name: str, old: Profile | None, new: Profile | None) -> None:
        """A user read profile was saved or removed through Studio (``Configuration.listeners``).

        Any change of a coupled read profile ends its unlocks; a change of a coupled read profile's environment ends
        every unlock (TX-04)."""
        coupled = self._end_where(lambda record: record.read_name == name, "profile_changed")
        environment_changed = old is not None and (new is None or new.environment != old.environment)
        if environment_changed and (coupled or self._is_coupled(name)):
            self.close_all("environment_changed")

    def _is_coupled(self, name: str) -> bool:
        try:
            return any(write.read_profile == name for write in self.config.user.profiles().stored_write_profiles)
        except Exception:
            return True  # cannot tell: fail closed

    def close_all(self, reason: str = "server_stopped") -> None:
        self._end_where(lambda _record: True, reason)
