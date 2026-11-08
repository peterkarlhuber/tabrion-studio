"""The connection registry: Studio's open database connections, always read-only (03 sections 5.1 and 6, STU-009).

Studio 1.0 never writes to a database. Every connection is opened with ``tabrion.Profile(read_only=True)``:

* **SQLite:** ``file:<path>?mode=ro`` and ``PRAGMA query_only=ON``, no attached database (tabrion), plus Studio's
  authorizer (:func:`tabrion_studio.services.readonly.sqlite_authorizer`) on every DBAPI connection of the pool. A
  project file is opened only through ``ProjectConfig.database_file`` (review T-1 of ``stu-006-review.md``), at the
  real path it returns.
* **PostgreSQL:** ``default_transaction_read_only=on`` for the session (tabrion), every statement in
  ``Database.read()`` (``BEGIN READ ONLY``, re-checked before ``COMMIT``), one statement per transaction and no
  transaction control (:func:`tabrion_studio.services.readonly.check_statement`).

A handle (:class:`OpenConnection`) exposes only reading. The engine and ``Database.transaction()`` never leave this
module.

**Where a password goes (C-1 of ``stu-007-review.md``, L-1 of ``stu-008-review.md``):** the driver gets the fields of
:data:`tabrion_studio.config.schema.TARGET_FIELDS` and the password, nothing else (:func:`connection_url`); the
password is resolved with ``Configuration.resolve_secret`` when the connection opens, and a stored one is bound to
the fingerprint of those fields. Session settings (timeouts, ``application_name``) are not connection targets. A
handle is checked again on every use: if its profile is gone, no longer trusted or points somewhere else, the
connection is closed (``studio.connection_stale``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from dataclasses import dataclass, field
import logging
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from typing import Any, Protocol

import sqlalchemy
from sqlalchemy import event, text
import tabrion
from tabrion.errors import ConfigurationError, UnsafePath
from tabrion.postgres import SessionSettings

from tabrion_studio.config.schema import TARGET_FIELDS, Profile, Settings, target_fingerprint
from tabrion_studio.config.secrets import SecretError
from tabrion_studio.logs import register_secret
from tabrion_studio.services.readonly import check_statement, sqlite_authorizer

__all__ = [
    "MAX_OPEN",
    "ConnectionRegistry",
    "OpenConnection",
    "ProfileSource",
    "RegistryError",
    "connection_url",
    "studio_profile",
]

logger = logging.getLogger("tabrion_studio.connections")

MAX_OPEN = 8
APPLICATION_NAME = "tabrion-studio"
#: 03 section 5.3: statement timeout 30 s, 15 s for production profiles; lock timeout 5 s.
PROD_STATEMENT_TIMEOUT_MS = 15_000
LOCK_TIMEOUT_MS = 5_000
_TLS = {
    None: tabrion.TLSMode.AUTO,  # a profile without sslmode is a loopback one (remote ones default to verify-full)
    "verify-full": tabrion.TLSMode.VERIFY_FULL,
    "verify-ca": tabrion.TLSMode.REQUIRE,
    "require": tabrion.TLSMode.REQUIRE,
    # Weaker modes are the user's explicit choice; D-01 marks them (weak_tls) and diagnostics (STU-014) report them.
    "prefer": tabrion.TLSMode.INSECURE,
    "allow": tabrion.TLSMode.INSECURE,
    "disable": tabrion.TLSMode.INSECURE,
}


class RegistryError(Exception):
    """A connection that cannot be opened or used; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProfileEntryLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def source(self) -> str: ...
    @property
    def profile(self) -> Profile: ...


class ProfileSource(Protocol):
    """What the registry needs from the configuration (``tabrion_studio.api.config.Configuration``)."""

    def entry(self, profile_id: str) -> ProfileEntryLike: ...
    def resolve_secret(self, profile_id: str) -> str: ...
    def database_file(self, profile_id: str) -> Path: ...
    def limits(self) -> Settings: ...


def connection_url(profile: Profile, password: str | None, database_file: Path | None = None) -> sqlalchemy.URL:
    """The URL the driver gets: only :data:`TARGET_FIELDS` of ``profile`` and ``password`` (binding for STU-009)."""
    target = {key: getattr(profile, key) for key in TARGET_FIELDS}
    if target["engine"] == "sqlite":
        if database_file is None:
            raise RegistryError("studio.database_missing")
        return sqlalchemy.URL.create("sqlite", database=str(database_file))
    query = {} if target["sslmode"] is None else {"sslmode": target["sslmode"]}
    return sqlalchemy.URL.create(
        "postgresql",
        username=target["user"],
        password=password,
        host=target["host"],
        port=target["port"] or 5432,
        database=target["database"],
        query=query,
    )


def studio_profile(profile: Profile, limits: Settings) -> tabrion.Profile:
    """The tabrion profile of a Studio connection: read-only, small pool, Studio's timeouts (03 section 5.3)."""
    timeout = profile.statement_timeout_ms
    if timeout is None:
        timeout = PROD_STATEMENT_TIMEOUT_MS if profile.environment == "prod" else limits.statement_timeout_ms
    session = SessionSettings(
        statement_timeout_ms=timeout, lock_timeout_ms=LOCK_TIMEOUT_MS, application_name=APPLICATION_NAME
    )
    return tabrion.Profile(
        postgres=session,
        application_name=APPLICATION_NAME,
        pool_size=2,
        max_overflow=0,
        tls=_TLS[profile.sslmode],
        max_rows=limits.max_rows,
        read_only=True,
    )


def _install_authorizer(dbapi_connection: Any, _record: Any) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.set_authorizer(sqlite_authorizer)


@dataclass
class OpenConnection:
    """One open, read-only connection. The engine is private; only reading is offered."""

    id: str
    profile_id: str
    engine: str
    environment: str
    url: str  # tabrion.safe_url: never the password
    fingerprint: str
    database_file: Path | None
    opened_at: float
    timeout_s: float
    _db: tabrion.Database = field(repr=False)

    read_only = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.id,
            "profile_id": self.profile_id,
            "engine": self.engine,
            "environment": self.environment,
            "read_only": True,
            "url": self.url,
            "opened_at": self.opened_at,
        }

    @contextlib.contextmanager
    def _read(self) -> Iterator[Any]:
        with self._db.read(timeout=self.timeout_s) as transaction:
            yield transaction

    def fetch(self, sql: str, params: Mapping[str, Any] | None = None) -> list[tuple[Any, ...]]:
        """The rows of one read-only statement; a refused one raises ``StatementRefused``."""
        statement = check_statement(sql)
        with self._read() as transaction:
            rows = transaction.fetch_all(text(statement), params)
        return [tuple(row) for row in rows]

    def health(self) -> tabrion.Health:
        return self._db.health(timeout=min(self.timeout_s, 5.0))

    def close(self) -> None:
        self._db.close()


@dataclass
class ConnectionRegistry:
    """Open connections by id. Thread-safe; opening and closing block (call them off the event loop)."""

    source: ProfileSource
    max_open: int = MAX_OPEN
    clock: Callable[[], float] = time.time
    _open: dict[str, OpenConnection] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _connect(self, profile_id: str) -> OpenConnection:
        entry = self.source.entry(profile_id)
        profile = entry.profile
        limits = self.source.limits()
        database_file: Path | None = None
        password: str | None = None
        if profile.engine == "sqlite":
            # T-1: a project file only through database_file, which re-checks the trust and the containment now.
            database_file = self.source.database_file(profile_id)
        else:
            try:
                password = self.source.resolve_secret(profile_id)
            except SecretError as exc:
                # No reference and nothing entered: connect without one (trust or peer authentication, a .pgpass
                # entry, which libpq matches by host, port, database and user).
                if profile.password is not None or exc.code != "studio.secret_missing":
                    raise
            if password is not None:
                register_secret(password)
        url = connection_url(profile, password, database_file)
        try:
            db = tabrion.Database.from_url(url, profile=studio_profile(profile, limits))
        except FileNotFoundError:
            raise RegistryError("studio.database_missing") from None
        except UnsafePath:
            raise RegistryError("studio.database_unsafe") from None
        except ConfigurationError as exc:
            raise RegistryError(
                "studio.connection_tls" if exc.code == "config.tls" else "studio.connection_config"
            ) from None
        if db.dialect == "sqlite":
            event.listen(db.engine, "connect", _install_authorizer)
        timeout = profile.statement_timeout_ms or (
            PROD_STATEMENT_TIMEOUT_MS if profile.environment == "prod" else limits.statement_timeout_ms
        )
        connection = OpenConnection(
            id=f"c_{secrets.token_urlsafe(12)}",
            profile_id=entry.id,
            engine=profile.engine,
            environment=profile.environment,
            url=tabrion.safe_url(db.safe_url),
            fingerprint=target_fingerprint(profile.to_dict()),
            database_file=database_file,
            opened_at=self.clock(),
            timeout_s=timeout / 1000,
            _db=db,
        )
        try:
            # Connect now, so that a wrong password or an unreachable host is the answer to "open".
            health = connection.health()
        except BaseException:
            connection.close()
            raise
        if not health.ok:
            connection.close()
            raise _health_error(health)
        return connection

    def open(self, profile_id: str) -> OpenConnection:
        """Open (or return the open) connection of ``profile_id``."""
        with self._lock:
            for connection in self._open.values():
                if connection.profile_id == profile_id:
                    break
            else:
                connection = None
            if connection is None and len(self._open) >= self.max_open:
                raise RegistryError("studio.connection_limit")
        if connection is not None:
            return self.get(connection.id)
        connection = self._connect(profile_id)
        with self._lock:
            if len(self._open) >= self.max_open:
                connection.close()
                raise RegistryError("studio.connection_limit")
            self._open[connection.id] = connection
        logger.info(
            "connection opened", extra={"event": "studio.connection_opened", "profile_id": connection.profile_id}
        )
        return connection

    def get(self, connection_id: object) -> OpenConnection:
        """The open connection ``connection_id``, checked against its profile now (else closed and refused)."""
        with self._lock:
            connection = self._open.get(connection_id) if isinstance(connection_id, str) else None
        if connection is None:
            raise RegistryError("studio.connection_not_found")
        if not self._still_valid(connection):
            self.close(connection.id)
            raise RegistryError("studio.connection_stale")
        return connection

    def _still_valid(self, connection: OpenConnection) -> bool:
        try:
            profile = self.source.entry(connection.profile_id).profile
            if target_fingerprint(profile.to_dict()) != connection.fingerprint:
                return False
            if profile.environment != connection.environment:
                return False
            if connection.database_file is not None:
                return self.source.database_file(connection.profile_id) == connection.database_file
        except Exception:  # gone, no longer trusted, damaged: the connection must not be used
            return False
        return True

    def list(self) -> list[OpenConnection]:
        with self._lock:
            connections = list(self._open.values())
        return [c for c in connections if self._valid_or_closed(c)]

    def _valid_or_closed(self, connection: OpenConnection) -> bool:
        if self._still_valid(connection):
            return True
        self.close(connection.id)
        return False

    def close(self, connection_id: object) -> bool:
        with self._lock:
            connection = self._open.pop(connection_id, None) if isinstance(connection_id, str) else None
        if connection is None:
            return False
        connection.close()
        logger.info("connection closed", extra={"event": "studio.connection_closed"})
        return True

    def close_all(self) -> None:
        """Dispose every engine (on shutdown)."""
        with self._lock:
            connections = list(self._open.values())
            self._open.clear()
        for connection in connections:
            with contextlib.suppress(Exception):
                connection.close()

    def test(self, profile_id: str) -> dict[str, Any]:
        """The connection test (TD-011): open, check, close; never keeps the connection."""
        connection = self._connect(profile_id)
        try:
            health = connection.health()
            return {
                "ok": health.ok,
                "engine": connection.engine,
                "server_version": health.server_version,
                "latency_ms": None if health.latency_ms is None else round(health.latency_ms, 1),
                "read_only": True,
            }
        finally:
            connection.close()


class HealthError(Exception):
    """A failed health check: carries tabrion's classification (for the ``db.<kind>`` problem)."""

    def __init__(self, classification: Any) -> None:
        super().__init__("health check failed")
        self.classification = classification


def _health_error(health: tabrion.Health) -> Exception:
    if health.classification is None:
        return RegistryError("studio.connection_failed")
    return HealthError(health.classification)
