"""G2-01 of the G-S2 gate review: libpq's environment never chooses the server, the authentication or TLS of a
Studio PostgreSQL connection (read, connection test, write and admin).

libpq reads ``PG*`` variables for every option a connection leaves out. Studio gives every such option itself
(``connections.pin_libpq_environment``) or, where it cannot, refuses the connection while the variable is set
(``studio.connection_environment``). The tests without a server use a loopback listener that only counts who
connects; the ones marked ``postgres`` use the test server (``TABRION_STUDIO_TEST_POSTGRES``).
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass, field
import os
from pathlib import Path
import socket
import threading
from typing import Any

import pytest
import sqlalchemy
import tabrion
from tabrion.errors import TabrionError

from tabrion_studio.config.schema import Profile, Settings
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services.connections import (
    ConnectionRegistry,
    HealthError,
    RegistryError,
    connection_url,
    driver_options,
    pin_libpq_environment,
    read_only_database,
)
from tabrion_studio.write.connection import WriteError, open_write_connection

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
postgres = pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
CANARY = "canary-g2-01-password"
#: How a connection that cannot reach its server fails (never a connection elsewhere).
_FAILED = (RegistryError, WriteError, HealthError, TabrionError)
#: Every variable of libpq's that is read for an option, plus the service file ones.
_EVERY_VARIABLE = sorted(
    {variable for _keyword, variable, _default in connections_module._libpq_options() if variable is not None}
    | {"PGSERVICEFILE", "PGSYSCONFDIR"}
)


@pytest.fixture(autouse=True)
def _no_libpq_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts without any libpq variable, whatever the shell running the tests set."""
    for name in _EVERY_VARIABLE:
        monkeypatch.delenv(name, raising=False)


class _Listener:
    """A loopback TCP port that counts the connections it accepts and closes each at once (no PostgreSQL)."""

    def __init__(self) -> None:
        self._socket = socket.create_server(("127.0.0.1", 0))
        self._socket.settimeout(10)
        self.port = int(self._socket.getsockname()[1])
        self.accepted = 0
        self._stopped = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        with contextlib.suppress(OSError):
            while True:
                peer, _ = self._socket.accept()
                if self._stopped:
                    peer.close()
                    return
                self.accepted += 1
                peer.close()

    def close(self) -> None:
        self._stopped = True
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self.port), timeout=1):
            pass
        self._thread.join(5)
        self._socket.close()


@pytest.fixture
def listener() -> Iterator[_Listener]:
    endpoint = _Listener()
    try:
        yield endpoint
    finally:
        endpoint.close()


@dataclass
class _Entry:
    id: str
    source: str
    profile: Profile


@dataclass
class _Source:
    """The registry's configuration with one user profile and its password."""

    profile: Profile
    password: str | None = CANARY
    limit: Settings = field(default_factory=Settings)

    def entry(self, profile_id: str) -> _Entry:
        return _Entry(profile_id, "user", self.profile)

    def resolve_entry_secret(self, entry: Any) -> str:
        if self.password is None:
            from tabrion_studio.config.secrets import SecretError

            raise SecretError("studio.secret_missing")
        return self.password

    def database_file(self, entry: Any) -> Path:
        raise AssertionError("no SQLite here")

    def limits(self) -> Settings:
        return self.limit


def _pg(host: str, port: int, *, user: str | None = "app", sslmode: str = "prefer") -> Profile:
    return Profile("pg", "postgresql", "dev", host=host, port=port, database="parts", user=user, sslmode=sslmode)


def _options(profile: Profile, password: str | None = CANARY) -> dict[str, Any]:
    url = connection_url(profile, password)
    connect_args = driver_options(password)
    return {**connect_args, **pin_libpq_environment(url, connect_args)}


# --- the options ------------------------------------------------------------------------------------------------------


def test_every_target_auth_and_tls_option_is_given_whatever_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The options libpq would take from PGHOSTADDR, PGSSLROOTCERT, PGSSLMINPROTOCOLVERSION, ... are pinned to
    libpq's own default; the profile's fields stay the profile's."""
    for name, value in {
        "PGHOSTADDR": "192.0.2.1",
        "PGSSLROOTCERT": "/elsewhere/root.crt",
        "PGSSLCERT": "/elsewhere/client.crt",
        "PGSSLKEY": "/elsewhere/client.key",
        "PGSSLMINPROTOCOLVERSION": "TLSv1",
        "PGCHANNELBINDING": "disable",
        "PGTARGETSESSIONATTRS": "standby",
        "PGHOST": "192.0.2.2",
        "PGPORT": "6543",
        "PGSSLMODE": "disable",
        "PGPASSWORD": "from-the-environment",
        "PGDATABASE": "other",
        "PGUSER": "other",
    }.items():
        monkeypatch.setenv(name, value)
    options = _options(_pg("/var/run/postgresql", 5432))
    assert options["hostaddr"] == ""
    for keyword in ("sslrootcert", "sslcert", "sslkey", "sslcrl", "sslcrldir"):
        assert options[keyword] == ""
    available = {keyword for keyword, _variable, _default in connections_module._libpq_options()}
    if "ssl_min_protocol_version" in available:
        assert options["ssl_min_protocol_version"] == "TLSv1.2"
    if "channel_binding" in available:
        assert options["channel_binding"] != "disable"
    if "target_session_attrs" in available:
        assert options["target_session_attrs"] == "any"
    # The values of the variables are nowhere in what the driver gets.
    url = connection_url(_pg("/var/run/postgresql", 5432), CANARY)
    text = repr(options) + url.render_as_string(hide_password=False)
    for value in ("192.0.2", "/elsewhere", "TLSv1'", "standby", "6543", "from-the-environment", "other"):
        assert value not in text


def test_only_options_the_loaded_libpq_knows_are_given() -> None:
    """An older libpq refuses an option it does not know; Studio pins only what ``PQconndefaults`` lists."""
    known = {keyword for keyword, _variable, _default in connections_module._libpq_options()}
    assert set(_options(_pg("localhost", 5432))) - set(driver_options(CANARY)) <= known


@pytest.mark.parametrize(
    ("variable", "profile"),
    [
        ("PGSERVICE", _pg("localhost", 5432)),
        ("PGSERVICEFILE", _pg("localhost", 5432)),
        ("PGSYSCONFDIR", _pg("localhost", 5432)),
        ("PGREQUIREPEER", _pg("/var/run/postgresql", 5432)),
        ("PGREQUIREAUTH", _pg("localhost", 5432)),
        ("PGUSER", _pg("localhost", 5432, user=None)),
    ],
)
def test_a_variable_studio_cannot_override_refuses_the_connection(
    monkeypatch: pytest.MonkeyPatch, variable: str, profile: Profile
) -> None:
    """Fail closed: a service (its file can set ``hostaddr``), a user for a profile without one, ... The refusal
    names neither the variable nor its value."""
    monkeypatch.setenv(variable, "planted-value")
    with pytest.raises(RegistryError) as refused:
        _options(profile)
    assert refused.value.code == "studio.connection_environment"
    assert "planted-value" not in str(refused.value) and variable not in str(refused.value)


@pytest.mark.parametrize("variable", ["PGCONNECT_TIMEOUT", "PGCLIENTENCODING", "PGPASSWORD", "PGPASSFILE"])
def test_variables_that_choose_nothing_or_are_overridden_are_accepted(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "5" if variable == "PGCONNECT_TIMEOUT" else "UTF8")
    assert _options(_pg("localhost", 5432))


def test_the_problem_is_in_the_catalog() -> None:
    from tabrion_studio.problems import CATALOG

    entry = CATALOG["studio.connection_environment"]
    assert entry.status == 422


# --- no connection goes elsewhere -------------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["/nonexistent-socket-folder", "db-does-not-exist.invalid"])
def test_pghostaddr_no_longer_redirects_a_read_connection(
    monkeypatch: pytest.MonkeyPatch, listener: _Listener, host: str
) -> None:
    """The review's reproduction: PGHOSTADDR pointing at a loopback port. The profile's host is a missing socket
    folder or a name that does not resolve, so the connection fails; it never reaches the port."""
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    registry = ConnectionRegistry(_Source(_pg(host, listener.port)))
    try:
        with pytest.raises(_FAILED):
            registry.open("user:pg")
        with pytest.raises(_FAILED):
            registry.test("user:pg")
    finally:
        registry.close_all()
    assert listener.accepted == 0


@pytest.mark.parametrize("kind", ["write", "admin"])
def test_pghostaddr_no_longer_redirects_a_write_connection_past_requirepeer(
    monkeypatch: pytest.MonkeyPatch, listener: _Listener, kind: str
) -> None:
    """A socket profile with requirepeer: with PGHOSTADDR set, libpq would have gone to TCP, where it does not check
    requirepeer. Now the socket is tried (and is missing): the password never leaves for the port."""
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    with pytest.raises(_FAILED) as failed:
        open_write_connection(
            kind,
            _pg("/nonexistent-socket-folder", listener.port),
            Settings(),
            password=CANARY,
            requirepeer="nobody-at-all",
        )
    assert CANARY not in str(failed.value)
    assert listener.accepted == 0


@pytest.mark.parametrize("variable", ["PGSERVICE", "PGSERVICEFILE", "PGSYSCONFDIR"])
def test_a_service_variable_refuses_read_test_and_write_before_any_connection(
    monkeypatch: pytest.MonkeyPatch, listener: _Listener, tmp_path: Path, variable: str
) -> None:
    """A service file can set ``hostaddr``: with any of its variables set, no connection is even built."""
    service_file = tmp_path / "pg_service.conf"
    service_file.write_text(f"[planted]\nhostaddr=127.0.0.1\nport={listener.port}\n", encoding="utf-8")
    monkeypatch.setenv(
        variable, {"PGSERVICE": "planted", "PGSERVICEFILE": str(service_file), "PGSYSCONFDIR": str(tmp_path)}[variable]
    )
    built: list[Any] = []
    real = tabrion.Database.from_url

    def from_url(*args: Any, **kwargs: Any) -> Any:
        built.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(tabrion.Database, "from_url", staticmethod(from_url))
    registry = ConnectionRegistry(_Source(_pg("/nonexistent-socket-folder", listener.port)))
    try:
        for attempt in (lambda: registry.open("user:pg"), lambda: registry.test("user:pg")):
            with pytest.raises(RegistryError) as refused:
                attempt()
            assert refused.value.code == "studio.connection_environment"
    finally:
        registry.close_all()
    with pytest.raises(RegistryError) as refused:
        open_write_connection(
            "write", _pg("/nonexistent-socket-folder", listener.port), Settings(), password=CANARY, requirepeer="x"
        )
    assert refused.value.code == "studio.connection_environment"
    assert built == [] and listener.accepted == 0


def test_the_migration_worker_path_pins_the_same_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """``read_only_database`` (the registry's and the migration worker's) gives the pinned options to the driver."""
    seen: list[dict[str, Any]] = []

    def from_url(url: Any, *, profile: Any = None, engine_options: Any = None) -> Any:
        seen.append(engine_options)
        raise RuntimeError("stop")

    monkeypatch.setattr(tabrion.Database, "from_url", staticmethod(from_url))
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    with pytest.raises(RuntimeError):
        read_only_database(_pg("/var/run/postgresql", 5432), Settings(), CANARY, None)
    assert seen[0]["connect_args"]["hostaddr"] == ""


# --- against the test server ------------------------------------------------------------------------------------------


def _server_profile(host: str | None = None) -> tuple[Profile, str]:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    profile = Profile(
        "pg",
        "postgresql",
        "dev",
        host=host if host is not None else url.host,
        port=url.port or 5432,
        database=url.database,
        user=url.username,
        sslmode="prefer",
    )
    return profile, url.password or ""


@pytest.mark.postgres
@postgres
def test_a_normal_profile_still_connects_with_overridden_variables_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """PGHOSTADDR at an address nobody listens on, a missing PGSSLROOTCERT and a PGTARGETSESSIONATTRS that the
    server cannot meet: none of them applies, so the profile's server is reached as before."""
    profile, password = _server_profile()
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGSSLROOTCERT", "/nonexistent/root.crt")
    monkeypatch.setenv("PGTARGETSESSIONATTRS", "standby")
    monkeypatch.setenv("PGPORT", "1")
    registry = ConnectionRegistry(_Source(profile, password))
    try:
        assert registry.test("user:pg")["ok"] is True
        connection = registry.open("user:pg")
        assert connection.fetch("SELECT 1") == [(1,)]
    finally:
        registry.close_all()


@pytest.mark.postgres
@postgres
def test_pghostaddr_at_the_test_server_does_not_reach_it_for_another_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review's reproduction against a real server: PGHOSTADDR names the server's address, the profile a socket
    folder that does not exist. Before G2-01 the connection opened (over TCP); now it fails."""
    profile, password = _server_profile("/nonexistent-socket-folder")
    assert PG_URL is not None
    address = socket.gethostbyname(sqlalchemy.make_url(PG_URL).host or "127.0.0.1")
    monkeypatch.setenv("PGHOSTADDR", address)
    registry = ConnectionRegistry(_Source(profile, password))
    try:
        with pytest.raises(_FAILED):
            registry.open("user:pg")
    finally:
        registry.close_all()
    with pytest.raises(_FAILED) as failed:
        open_write_connection("write", profile, Settings(), password=password, requirepeer="nobody-at-all")
    assert password not in str(failed.value)
