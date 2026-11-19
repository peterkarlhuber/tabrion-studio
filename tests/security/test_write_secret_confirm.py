"""STU-XW-4: the I-109 j confirmation of a write or admin secret (D-15) and libpq ``require_auth`` for every write
and admin connection (review L-2 of PR #111; TX-07, N-5).

Runs with a flag on (the "flag on" path of the kill-switch proof); the flag-off answers of the new routes are in
``test_write_kill_switch.py`` (every route of ``WRITE_ROUTES``) and the "nothing imported" proof there covers the new
module. No test here needs a PostgreSQL server: the read connection of a PostgreSQL profile is a stand-in, and the
driver is replaced where a test only needs to see the options Studio gives it. The refusal by a real server that asks
for a clear-text or MD5 password is in ``test_write_postgres.py`` (``TABRION_STUDIO_TEST_POSTGRES_MD5`` and
``..._PASSWORD``).
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from typing import Any

import pytest
import tabrion

from tabrion_studio.config.schema import Profile, Settings, is_loopback, target_fingerprint
from tabrion_studio.config.write_areas import Area
from tabrion_studio.logs import LogSetup, configure_logging, forget_secrets
from tabrion_studio.services import connections as connections_module
from tabrion_studio.services.connections import RegistryError
from tabrion_studio.write import connection as write_connection
from tabrion_studio.write import unlock as unlock_module
from tabrion_studio.write.connection import (
    REQUIRE_AUTH,
    REQUIRE_AUTH_SCRAM,
    WriteError,
    open_write_connection,
    require_auth,
)
from tabrion_studio.write.secret_confirm import (
    CONFIRMATION_FILE,
    PREVIEW_TTL_SECONDS,
    SecretConfirmations,
    SecretPair,
    check_local_transport,
    secret_pair,
)
from tests.support.plans import RowExecutor
from tests.support.write import CANARY_SECRET, EVERY_AREA, WriteEnv

pytestmark = pytest.mark.security

ENV_NAME = "STUDIO_XW4_CANARY_SECRET"


class _Stop(Exception):
    """Raised by the stand-in driver: the options were seen, nothing connects."""


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[LogSetup]:
    setup = configure_logging(tmp_path / "logs", "t", level=logging.DEBUG, console=io.StringIO())
    try:
        yield setup
    finally:
        setup.close()
        forget_secrets()
        assert setup.file is not None
        assert CANARY_SECRET not in setup.file.read_text(encoding="utf-8")


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The engine options of every PostgreSQL write connection Studio tries to open; it never connects."""
    seen: list[dict[str, Any]] = []

    def from_url(url: str, *, profile: Any = None, engine_options: Any = None) -> Any:
        seen.append({"url": url, **(engine_options or {})})
        raise _Stop

    monkeypatch.setattr(tabrion.Database, "from_url", staticmethod(from_url))
    return seen


def _private(path: Path, doc: object) -> None:
    """Write a confirmation record as Studio does (0600)."""
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    path.chmod(0o600)


def _pg(host: str = "localhost", sslmode: str = "prefer", user: str = "app_writer") -> Profile:
    return Profile("pg", "postgresql", "dev", host=host, port=5432, database="parts", user=user, sslmode=sslmode)


# --- L-2: require_auth on every write and admin connection ------------------------------------------------------------


@pytest.mark.parametrize("kind", ["write", "admin"])
@pytest.mark.parametrize(
    ("host", "sslmode", "with_password", "expected"),
    [
        # N-2 of PR #116: a password goes only through SCRAM, on loopback and over a socket too.
        ("localhost", "prefer", True, REQUIRE_AUTH_SCRAM),
        ("127.0.0.1", "disable", True, REQUIRE_AUTH_SCRAM),
        ("[::1]", "disable", True, REQUIRE_AUTH_SCRAM),
        ("/var/run/postgresql", "prefer", True, REQUIRE_AUTH_SCRAM),
        ("/var/run/postgresql", "prefer", False, REQUIRE_AUTH),
        ("localhost", "prefer", False, REQUIRE_AUTH),
        ("db.example.org", "verify-full", True, REQUIRE_AUTH_SCRAM),
        ("db.example.org", "verify-full", False, REQUIRE_AUTH),
    ],
)
def test_every_write_and_admin_connection_sets_require_auth(
    captured: list[dict[str, Any]], kind: str, host: str, sslmode: str, with_password: bool, expected: str
) -> None:
    """L-2 of PR #111: the option reaches the driver for ``write`` and ``admin``, whatever the profile's owner
    (``refuse_cleartext`` False is the user profile's read rule); any host that gets a password gets SCRAM (N-2 of
    PR #116: also loopback and a socket, so a process on an occupied local port cannot pick another method)."""
    password = CANARY_SECRET if with_password else None
    with pytest.raises(_Stop):
        open_write_connection(kind, _pg(host, sslmode), Settings(), password=password, refuse_cleartext=False)
    (options,) = captured
    assert options["connect_args"]["require_auth"] == expected
    # The privilege check (check_privileges) runs on this same connection: it has no other door to the server.


@pytest.mark.parametrize("kind", ["write", "admin"])
@pytest.mark.parametrize(
    "host", ["/var/run/postgresql,127.0.0.1", "/var/run/postgresql,db.example.org", "localhost,db.example.org"]
)
def test_a_host_list_never_reaches_the_driver_on_the_write_path(
    captured: list[dict[str, Any]], kind: str, host: str
) -> None:
    """M-1 of the PR #117 review: a libpq host list (also one whose first entry is a socket folder, where a refused
    socket would fall back to TCP) is refused before any connection is built, and the refusal names no value."""
    with pytest.raises(RegistryError) as refused:
        open_write_connection(kind, _pg(host), Settings(), password=CANARY_SECRET, refuse_cleartext=False)
    assert refused.value.code == "studio.profile_invalid"
    assert CANARY_SECRET not in str(refused.value) and host not in str(refused.value)
    assert captured == []


def test_a_host_list_is_never_loopback_and_a_password_gets_scram_only() -> None:
    """Defense in depth (M-1 of the PR #117 review): a list is never loopback, and any password needs SCRAM."""
    assert not is_loopback("/var/run/postgresql,db.example.org")
    assert not is_loopback("localhost,db.example.org")
    assert require_auth(CANARY_SECRET) == REQUIRE_AUTH_SCRAM
    assert require_auth(None) == REQUIRE_AUTH


@pytest.mark.parametrize("host", ["/var/run/postgresql,127.0.0.1", "/var/run/postgresql,db.example.org"])
def test_requirepeer_with_a_socket_and_tcp_host_list_is_refused(captured: list[dict[str, Any]], host: str) -> None:
    """Combination of N-2 of PR #116 and M-1 of the PR #117 review: a refused socket can never fall back to TCP."""
    with pytest.raises((RegistryError, WriteError)) as refused:
        open_write_connection("write", _pg(host), Settings(), password=CANARY_SECRET, requirepeer="postgres")
    assert CANARY_SECRET not in str(refused.value) and host not in str(refused.value)
    assert captured == []


def test_require_auth_is_never_weaker_than_no_cleartext_no_md5() -> None:
    assert set(REQUIRE_AUTH.split(",")) == {"!password", "!md5"}
    assert REQUIRE_AUTH_SCRAM == "scram-sha-256"


# --- N-2 of PR #116: requirepeer on a Unix socket ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["write", "admin"])
def test_requirepeer_reaches_libpq_on_a_socket(captured: list[dict[str, Any]], kind: str) -> None:
    """The server's OS user, when the write profile names it, is checked by libpq before it authenticates."""
    with pytest.raises(_Stop):
        open_write_connection(
            kind, _pg("/var/run/postgresql"), Settings(), password=CANARY_SECRET, requirepeer="postgres"
        )
    (options,) = captured
    assert options["connect_args"]["requirepeer"] == "postgres"
    assert options["connect_args"]["require_auth"] == REQUIRE_AUTH_SCRAM


def test_without_requirepeer_none_is_set(captured: list[dict[str, Any]]) -> None:
    with pytest.raises(_Stop):
        open_write_connection("write", _pg("/var/run/postgresql"), Settings(), password=CANARY_SECRET)
    (options,) = captured
    assert "requirepeer" not in options["connect_args"]


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "db.example.org"])
def test_requirepeer_on_a_tcp_host_refuses_the_connection(captured: list[dict[str, Any]], host: str) -> None:
    """libpq ignores requirepeer over TCP: Studio never drops the check silently (fail closed)."""
    with pytest.raises(WriteError) as refused:
        open_write_connection(
            "write", _pg(host, "verify-full"), Settings(), password=CANARY_SECRET, requirepeer="postgres"
        )
    assert refused.value.code == "studio.write_coupling_invalid"
    assert captured == []


def test_a_libpq_older_than_16_refuses_the_write_connection(
    captured: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: without require_auth (libpq 15) no write or admin connection is even built."""
    monkeypatch.setattr(connections_module, "_libpq_version", lambda: 150013)
    for kind in ("write", "admin"):
        with pytest.raises(WriteError) as refused:
            open_write_connection(kind, _pg(), Settings(), password=CANARY_SECRET)
        assert refused.value.code == "studio.require_auth_unavailable"
    assert captured == []


def test_libpq_16_is_enough(captured: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections_module, "_libpq_version", lambda: 160000)
    with pytest.raises(_Stop):
        open_write_connection("write", _pg(), Settings(), password=CANARY_SECRET)
    assert captured[0]["connect_args"]["require_auth"] == REQUIRE_AUTH_SCRAM


def test_the_read_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read connection of a user profile keeps every method; a project profile's stays ``!password`` (B-3)."""
    assert "require_auth" not in connections_module.driver_options("x")
    assert connections_module.driver_options("x", refuse_cleartext=True)["require_auth"] == "!password"


# --- the pair -----------------------------------------------------------------------------------------------------


def test_the_pair_digest_covers_server_role_and_reference() -> None:
    from tabrion_studio.config.schema import SecretRef, WriteProfile

    read = _pg()
    write = WriteProfile("w", "write", "pg", user="app_writer", password=SecretRef("env", "A"))
    base = secret_pair(write, read)
    assert base is not None and not base.foreign
    variants = [
        secret_pair(write, _pg(host="db.example.org", sslmode="verify-full")),
        secret_pair(write, _pg(sslmode="require")),
        secret_pair(WriteProfile("w", "write", "pg", user="other", password=SecretRef("env", "A")), read),
        secret_pair(WriteProfile("w", "write", "pg", user="app_writer", password=SecretRef("env", "B")), read),
        secret_pair(WriteProfile("w", "write", "pg", user="app_writer", password=SecretRef("file", "A")), read),
        secret_pair(WriteProfile("w", "admin", "pg", user="app_writer", password=SecretRef("env", "A")), read),
        secret_pair(WriteProfile("w", "write", "pg", user="app_writer"), read),
        # N-2 of PR #116: naming the server's OS user (or another one) is another pair.
        secret_pair(
            WriteProfile("w", "write", "pg", user="app_writer", password=SecretRef("env", "A"), requirepeer="postgres"),
            read,
        ),
        secret_pair(
            WriteProfile("w", "write", "pg", user="app_writer", password=SecretRef("env", "A"), requirepeer="other"),
            read,
        ),
    ]
    digests = {base.digest} | {pair.digest for pair in variants if pair is not None}
    assert len(digests) == len(variants) + 1
    sqlite = Profile("db", "sqlite", "dev", path="x.db")
    assert secret_pair(WriteProfile("w", "write", "db"), sqlite) is None


@pytest.mark.parametrize("host", ["db.example.org", "127.evil.example", "10.0.0.5", "localhost.example"])
def test_a_host_that_is_not_loopback_is_foreign(host: str) -> None:
    from tabrion_studio.config.schema import WriteProfile

    pair = secret_pair(WriteProfile("w", "write", "pg", user="u"), _pg(host=host, sslmode="verify-full"))
    assert pair is not None and pair.foreign


@pytest.mark.parametrize("host", ["/var/run/postgresql,db.example.org", "localhost,127.0.0.1"])
def test_a_host_list_has_no_secret_pair(host: str) -> None:
    """M-1 of the PR #117 review: no pair (and so no confirmation) is ever built for a libpq host list."""
    from tabrion_studio.config.schema import SecretRef, WriteProfile

    write = WriteProfile("w", "write", "pg", user="u", password=SecretRef("env", "A"))
    with pytest.raises(WriteError) as refused:
        secret_pair(write, _pg(host=host, sslmode="verify-full"))
    assert refused.value.code == "studio.write_coupling_invalid"
    assert host not in str(refused.value)


def test_a_record_that_is_missing_malformed_or_a_link_confirms_nothing(tmp_path: Path) -> None:
    from tabrion_studio.config.schema import WriteProfile

    pair = secret_pair(WriteProfile("w", "write", "pg", user="u"), _pg())
    assert pair is not None
    record = tmp_path / CONFIRMATION_FILE
    confirmations = SecretConfirmations(record)
    assert not confirmations.is_confirmed(pair)
    for text in (
        "not json",
        "[]",
        json.dumps({"version": 2, "confirmed": {"w": pair.digest}}),
        json.dumps({"version": 1, "confirmed": {"w": pair.digest.upper()}}),
        json.dumps({"version": 1, "confirmed": {"w": 1}}),
    ):
        _private(record, text)
        assert not confirmations.is_confirmed(pair), text
    _private(record, {"version": 1, "confirmed": {"w": pair.digest}})
    assert confirmations.is_confirmed(pair)
    if os.name != "nt":
        record.chmod(0o644)  # readable by others: not Studio's private record
        assert not confirmations.is_confirmed(pair)
        record.chmod(0o600)
    if sys.platform != "win32":
        elsewhere = tmp_path / "elsewhere.json"
        record.rename(elsewhere)
        record.symlink_to(elsewhere)
        assert not confirmations.is_confirmed(pair)
    assert not SecretConfirmations(None).is_confirmed(pair)


# --- the confirmation through the API -----------------------------------------------------------------------------


def _pg_read(env: WriteEnv, host: str = "localhost", sslmode: str = "prefer") -> dict[str, Any]:
    read = {
        "name": "pg",
        "engine": "postgresql",
        "host": host,
        "port": 5432,
        "database": "parts",
        "user": "app_reader",
        "environment": "dev",
        "sslmode": sslmode,
    }
    response = env.send("POST", "/api/profiles", {"profile": read, "digest": env.user_digest()})
    assert response.status_code == 201, response.text
    return read


def _stand_in_connection(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> str:
    """The open read connection of ``user:pg``, as the registry would hand it to the unlock (no server)."""
    read = next(p for p in env.config.user.profiles().profiles if p.name == "pg")
    stand_in = SimpleNamespace(
        id="c_pg",
        profile_id="user:pg",
        engine="postgresql",
        environment=read.environment,
        fingerprint=target_fingerprint(read.to_dict()),
        database_file=None,
    )

    def get(connection_id: object) -> Any:
        if connection_id != "c_pg":
            raise connections_module.RegistryError("studio.connection_not_found")
        current = next((p for p in env.config.user.profiles().profiles if p.name == "pg"), None)
        if current is None or target_fingerprint(current.to_dict()) != stand_in.fingerprint:
            raise connections_module.RegistryError("studio.connection_stale")
        return stand_in

    monkeypatch.setattr(env.registry, "get", get)
    return "c_pg"


@pytest.fixture
def env(tmp_path: Path, static_dir: Path, logs: LogSetup, monkeypatch: pytest.MonkeyPatch) -> Iterator[WriteEnv]:
    """Every area on; the read profile ``pg`` (localhost) and the write profile ``pg-write`` with ``env:`` secret."""
    environment = WriteEnv(tmp_path, static_dir, EVERY_AREA)
    environment.config.secrets._environ = {ENV_NAME: CANARY_SECRET}
    try:
        _pg_read(environment)
        created = environment.create_write(name="pg-write", read="pg", user="app_writer", password=f"env:{ENV_NAME}")
        assert created.status_code == 201, created.text
        yield environment
    finally:
        environment.close()


@pytest.fixture
def resolved(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every secret Studio resolves (a spy on ``Secrets.resolve``)."""
    calls: list[object] = []
    original = env.config.secrets.resolve

    def spy(target: Any) -> str:
        calls.append(target)
        return original(target)

    monkeypatch.setattr(env.config.secrets, "resolve", spy)
    return calls


def _preview(env: WriteEnv, write_id: str = "write:pg-write") -> Any:
    return env.send("POST", "/api/write/secret-confirmations", {"write_profile_id": write_id})


def _confirm(env: WriteEnv, confirmation_hash: str, typed: object) -> Any:
    return env.send(
        "POST", "/api/write/secret-confirmations/confirm", {"confirmation_hash": confirmation_hash, "typed": typed}
    )


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


def _listed(env: WriteEnv) -> dict[str, Any]:
    items = env.send("GET", "/api/write/profiles").json()["write_profiles"]
    secret: dict[str, Any] = next(item for item in items if item["id"] == "write:pg-write")["secret"]
    return secret


def test_the_preview_names_the_pair_and_never_the_secret(env: WriteEnv) -> None:
    preview = _preview(env)
    assert preview.status_code == 200, preview.text
    doc = preview.json()
    assert doc["pair"] == {
        "host": "localhost",
        "port": 5432,
        "database": "parts",
        "sslmode": "prefer",
        "role": "app_writer",
        "reference": {"source": "env", "target": ENV_NAME},
        "requirepeer": None,
        "foreign": False,
        "local_unverified": True,  # localhost without verify-full (G-S2 decision 2A)
    }
    assert doc["confirm_text"] == "localhost" and doc["confirmed"] is False
    assert doc["write_profile_id"] == "write:pg-write" and len(doc["confirmation_hash"]) == 64
    assert CANARY_SECRET not in preview.text
    assert _listed(env) == {"source": "env", "has_secret": True, "confirmed": False, "foreign": False}


def test_an_unconfirmed_pair_refuses_the_unlock_before_any_secret_is_read(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    connection_id = _stand_in_connection(env, monkeypatch)
    refused = env.unlock(connection_id, "write:pg-write", "pg-write")
    assert _code(refused) == (400, "studio.secret_unconfirmed")
    assert resolved == [] and captured == []
    assert CANARY_SECRET not in refused.text


def test_a_confirmed_pair_unlocks_with_require_auth(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    assert _listed(env)["confirmed"] is True
    with pytest.raises(_Stop):
        env.unlocks.unlock(env.session(), connection_id, "write:pg-write", "pg-write")
    assert len(resolved) == 1
    (options,) = captured
    # The pair sends a password to localhost: SCRAM only (N-2 of PR #116).
    assert options["connect_args"]["require_auth"] == REQUIRE_AUTH_SCRAM
    assert "requirepeer" not in options["connect_args"]


def _move_to_socket(env: WriteEnv, requirepeer: str | None = "postgres") -> Any:
    """Point ``pg`` at a socket folder and give ``pg-write`` the server's OS user; the answer of the second save."""
    current = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    socket = {
        "name": "pg",
        "engine": "postgresql",
        "host": "/var/run/postgresql",
        "port": 5432,
        "database": "parts",
        "user": "app_reader",
        "environment": "dev",
        "sslmode": "prefer",
    }
    moved = env.send("PUT", "/api/profiles", {"id": "user:pg", "profile": socket, "digest": current})
    assert moved.status_code == 200, moved.text
    profile: dict[str, Any] = {
        "name": "pg-write",
        "kind": "write",
        "read_profile": "pg",
        "user": "app_writer",
        "password": f"env:{ENV_NAME}",
    }
    if requirepeer is not None:
        profile["requirepeer"] = requirepeer
    current = env.send("GET", "/api/write/profiles").json()["user_file"]["digest"]
    return env.send("PUT", "/api/write/profiles", {"id": "write:pg-write", "profile": profile, "digest": current})


def test_requirepeer_of_the_write_profile_reaches_the_unlock(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    """N-2 of PR #116: a socket profile with the server's OS user: the pair shows it, and the unlock gives it to
    libpq together with SCRAM."""
    assert _move_to_socket(env).status_code == 200
    preview = _preview(env)
    assert preview.status_code == 200, preview.text
    assert preview.json()["pair"]["requirepeer"] == "postgres"
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    with pytest.raises(_Stop):
        env.unlocks.unlock(env.session(), connection_id, "write:pg-write", "pg-write")
    (options,) = captured
    assert options["connect_args"]["requirepeer"] == "postgres"
    assert options["connect_args"]["require_auth"] == REQUIRE_AUTH_SCRAM


def test_changing_requirepeer_needs_a_new_confirmation(env: WriteEnv) -> None:
    assert _move_to_socket(env).status_code == 200
    assert env.confirm_pair("write:pg-write").status_code == 200
    assert _listed(env)["confirmed"] is True
    assert _move_to_socket(env, None).status_code == 200  # dropping the check is a change of the pair
    assert _listed(env)["confirmed"] is False


def test_requirepeer_needs_a_socket_folder(env: WriteEnv) -> None:
    """libpq checks the peer only on a Unix socket: on a TCP host the profile is refused, and a coupled socket
    profile cannot be moved to a TCP host while the write profile names the server's OS user."""
    refused = env.create_write(
        name="tcp-write", read="pg", user="app_other", password=f"env:{ENV_NAME}", requirepeer="postgres"
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["issues"] == [{"pointer": "profile.requirepeer", "code": "config.requirepeer_socket_only"}]
    assert _move_to_socket(env).status_code == 200
    current = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    tcp = {
        "name": "pg",
        "engine": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "parts",
        "user": "app_reader",
        "environment": "dev",
        "sslmode": "prefer",
    }
    moved = env.send("PUT", "/api/profiles", {"id": "user:pg", "profile": tcp, "digest": current})
    assert moved.status_code == 422, moved.text  # the read profile form's answer (studio.profile_invalid)
    assert {"pointer": "profile.host", "code": "config.requirepeer_socket_only"} in moved.json()["issues"]


@pytest.mark.parametrize("name", ["", "post gres", "x;y", "a" * 65, "-postgres", "$", "pg\\x", 5])
def test_requirepeer_is_a_plain_os_user_name(env: WriteEnv, name: object) -> None:
    response = _move_to_socket(env, name)  # type: ignore[arg-type]
    assert response.status_code == 400, response.text
    codes = {issue["code"] for issue in response.json()["issues"]}
    assert codes & {"config.os_user_name", "config.length", "config.type"}, codes


def test_a_libpq_older_than_16_refuses_the_unlock_before_any_secret_is_read(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    """L-2 proof: an older libpq refuses the unlock (fail closed), even for a confirmed pair."""
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    monkeypatch.setattr(connections_module, "_libpq_version", lambda: 150013)
    refused = env.unlock(connection_id, "write:pg-write", "pg-write")
    assert _code(refused) == (405, "studio.require_auth_unavailable")
    assert resolved == [] and captured == []
    # The admin kind too.
    created = env.create_write(name="pg-admin", read="pg", kind="admin", user="app_admin", password=f"env:{ENV_NAME}")
    assert created.status_code == 201
    monkeypatch.setattr(connections_module, "_libpq_version", lambda: 160004)
    assert env.confirm_pair("write:pg-admin").status_code == 200
    monkeypatch.setattr(connections_module, "_libpq_version", lambda: 150013)
    refused = env.unlock(connection_id, "write:pg-admin", "pg-admin")
    assert _code(refused) == (405, "studio.require_auth_unavailable")
    assert resolved == [] and captured == []


def test_the_typed_host_must_match(env: WriteEnv) -> None:
    doc = _preview(env).json()
    for typed in ("", "LOCALHOST", "localhost ", "pg-write", None, 1, ["localhost"]):
        assert _code(_confirm(env, doc["confirmation_hash"], typed)) == (400, "studio.secret_confirmation_mismatch")
    assert _listed(env)["confirmed"] is False
    # A wrong text may be typed again; the right one confirms, once.
    assert _confirm(env, doc["confirmation_hash"], "localhost").status_code == 200
    assert _code(_confirm(env, doc["confirmation_hash"], "localhost")) == (400, "studio.secret_confirmation_unknown")
    assert _listed(env)["confirmed"] is True


@pytest.mark.parametrize("bad", ["", "0" * 64, "x" * 64, None, 5, "A" * 64])
def test_an_unknown_or_malformed_hash_is_refused(env: WriteEnv, bad: object) -> None:
    assert _code(_confirm(env, bad, "localhost")) == (400, "studio.secret_confirmation_unknown")  # type: ignore[arg-type]


def test_another_sessions_preview_cannot_be_confirmed(env: WriteEnv) -> None:
    doc = _preview(env).json()
    confirmations = env.studio.client.app.state.write_secret_confirmations  # type: ignore[attr-defined]
    with pytest.raises(WriteError) as refused:
        confirmations.pending("another-session", doc["confirmation_hash"])
    assert refused.value.code == "studio.secret_confirmation_unknown"


def test_an_expired_preview_is_refused(env: WriteEnv) -> None:
    confirmations = env.studio.client.app.state.write_secret_confirmations  # type: ignore[attr-defined]
    now = [1000.0]
    confirmations.clock = lambda: now[0]
    doc = _preview(env).json()
    now[0] += PREVIEW_TTL_SECONDS + 1
    assert _code(_confirm(env, doc["confirmation_hash"], "localhost")) == (400, "studio.secret_confirmation_unknown")


def test_a_pair_changed_after_the_preview_is_not_confirmed(env: WriteEnv) -> None:
    doc = _preview(env).json()
    current = env.send("GET", "/api/write/profiles").json()["user_file"]["digest"]
    changed = env.send(
        "PUT",
        "/api/write/profiles",
        {
            "id": "write:pg-write",
            "profile": {
                "name": "pg-write",
                "kind": "write",
                "read_profile": "pg",
                "user": "app_writer",
                "password": "env:OTHER_SECRET",
            },
            "digest": current,
        },
    )
    assert changed.status_code == 200, changed.text
    assert _code(_confirm(env, doc["confirmation_hash"], "localhost")) == (400, "studio.secret_confirmation_stale")
    assert _listed(env)["confirmed"] is False


def test_a_change_of_the_server_or_the_reference_needs_a_new_confirmation(env: WriteEnv) -> None:
    assert env.confirm_pair("write:pg-write").status_code == 200
    assert _listed(env)["confirmed"] is True
    current = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    moved = {
        "name": "pg",
        "engine": "postgresql",
        "host": "127.0.0.2",
        "port": 5432,
        "database": "parts",
        "user": "app_reader",
        "environment": "dev",
        "sslmode": "prefer",
    }
    response = env.send("PUT", "/api/profiles", {"id": "user:pg", "profile": moved, "digest": current})
    assert response.status_code == 200, response.text
    assert _listed(env)["confirmed"] is False


def test_a_foreign_host_needs_verify_ca_or_verify_full(env: WriteEnv) -> None:
    current = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    for sslmode, ok in (
        ("require", False),
        ("prefer", False),
        ("disable", False),
        ("verify-ca", True),
        ("verify-full", True),
    ):
        profile = {
            "name": "pg",
            "engine": "postgresql",
            "host": "db.example.org",
            "port": 5432,
            "database": "parts",
            "user": "app_reader",
            "environment": "dev",
            "sslmode": sslmode,
        }
        response = env.send("PUT", "/api/profiles", {"id": "user:pg", "profile": profile, "digest": current})
        assert response.status_code == 200, response.text
        current = response.json()["digest"]
        preview = _preview(env)
        if ok:
            assert preview.status_code == 200 and preview.json()["pair"]["foreign"] is True
            assert preview.json()["confirm_text"] == "db.example.org"
        else:
            assert _code(preview) == (400, "studio.write_tls_required")


def test_a_foreign_host_with_a_weak_tls_mode_refuses_the_unlock(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    """Also when a confirmation exists (a record from before the rule, or written by hand): no secret is read."""
    current = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    profile = {
        "name": "pg",
        "engine": "postgresql",
        "host": "db.example.org",
        "port": 5432,
        "database": "parts",
        "user": "app_reader",
        "environment": "dev",
        "sslmode": "require",
    }
    assert env.send("PUT", "/api/profiles", {"id": "user:pg", "profile": profile, "digest": current}).status_code == 200
    write = next(w for w in env.config.user.profiles().write_profiles if w.name == "pg-write")
    read = next(p for p in env.config.user.profiles().profiles if p.name == "pg")
    pair = secret_pair(write, read)
    assert pair is not None
    _private(env.confirmations_file, {"version": 1, "confirmed": {"pg-write": pair.digest}})
    connection_id = _stand_in_connection(env, monkeypatch)
    assert _code(env.unlock(connection_id, "write:pg-write", "pg-write")) == (400, "studio.write_tls_required")
    assert resolved == [] and captured == []


def test_the_confirmation_record_is_private_and_holds_no_secret(env: WriteEnv) -> None:
    assert env.confirm_pair("write:pg-write").status_code == 200
    record = env.confirmations_file
    data = record.read_bytes()
    assert CANARY_SECRET.encode() not in data and ENV_NAME.encode() not in data and b"localhost" not in data
    doc = json.loads(data)
    assert doc["version"] == 1 and set(doc["confirmed"]) == {"pg-write"}
    if os.name != "nt":
        assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_confirming_and_revoking_are_audited_without_host_or_reference(env: WriteEnv) -> None:
    assert env.confirm_pair("write:pg-write").status_code == 200
    revoked = env.send("DELETE", "/api/write/secret-confirmations", {"write_profile_id": "write:pg-write"})
    assert revoked.status_code == 204
    assert _listed(env)["confirmed"] is False
    records = env.send("GET", "/api/write/audit").json()["records"]
    events = [record["event"] for record in records]
    assert "secret.confirm" in events and "secret.unconfirm" in events
    confirm = next(record for record in records if record["event"] == "secret.confirm")
    assert confirm["fields"]["source"] == "env" and confirm["fields"]["foreign"] is False
    assert len(confirm["fields"]["pair_hash"]) == 64
    audit = b"".join(p.read_bytes() for p in sorted(env.audit_dir.iterdir()) if p.is_file())
    assert b"localhost" not in audit and ENV_NAME.encode() not in audit and CANARY_SECRET.encode() not in audit


def test_removing_the_write_profile_forgets_its_confirmation(env: WriteEnv) -> None:
    assert env.confirm_pair("write:pg-write").status_code == 200
    current = env.send("GET", "/api/write/profiles").json()["user_file"]["digest"]
    removed = env.send("DELETE", "/api/write/profiles", {"id": "write:pg-write", "digest": current})
    assert removed.status_code == 200, removed.text
    doc = json.loads((env.confirmations_file).read_bytes())
    assert doc["confirmed"] == {}
    # The same name with the same pair again: not confirmed (I-109 j: a new profile is a new pair to confirm).
    assert (
        env.create_write(name="pg-write", read="pg", user="app_writer", password=f"env:{ENV_NAME}").status_code == 201
    )
    assert _listed(env)["confirmed"] is False


def test_a_sqlite_write_profile_needs_no_confirmation(env: WriteEnv) -> None:
    env.create_read()
    assert env.create_write().status_code == 201
    assert _code(_preview(env, "write:parts-write")) == (400, "studio.secret_confirmation_not_needed")
    items = env.send("GET", "/api/write/profiles").json()["write_profiles"]
    sqlite = next(item for item in items if item["id"] == "write:parts-write")
    assert sqlite["secret"]["confirmed"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"write_profile_id": "write:pg-write", "host": "evil.example"},
        {"write_profile_id": "write:pg-write", "pair": {"host": "evil.example"}},
        {},
    ],
)
def test_the_pair_comes_from_the_users_file_never_from_the_request(env: WriteEnv, body: dict[str, Any]) -> None:
    response = env.send("POST", "/api/write/secret-confirmations", body)
    assert response.status_code == 400
    assert "evil.example" not in response.text


def test_an_unknown_write_profile_is_refused(env: WriteEnv) -> None:
    assert _code(_preview(env, "write:nope")) == (400, "studio.write_profile_unknown")
    assert _code(_preview(env, "user:pg")) == (400, "studio.write_profile_unknown")


def test_without_a_state_folder_nothing_can_be_confirmed(tmp_path: Path) -> None:
    from tabrion_studio.config.schema import WriteProfile

    pair = secret_pair(WriteProfile("w", "write", "pg", user="u"), _pg())
    assert pair is not None
    confirmations = SecretConfirmations(None)
    with pytest.raises(WriteError) as refused:
        confirmations.preview("s", pair)
    assert refused.value.code == "studio.secret_confirmation_unavailable"


# --- integration with the unlock and the plan framework (STU-XW-3) -------------------------------------------------


class _FakeWrite:
    """A write connection stand-in: the unlock keeps it; nothing connects."""

    kind = "write"
    engine = "postgresql"
    role = "app_writer"
    closed = False

    def __init__(self) -> None:
        self.findings: list[dict[str, str]] = []
        self.facts: dict[str, Any] = {}

    def verify(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _unlocked(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> tuple[str, _FakeWrite]:
    connection_id = _stand_in_connection(env, monkeypatch)
    fake = _FakeWrite()
    seen: list[dict[str, Any]] = []

    def opener(kind: str, profile: Profile, limits: Any, **kwargs: Any) -> _FakeWrite:
        seen.append(kwargs)
        return fake

    monkeypatch.setattr(unlock_module, "open_write_connection", opener)
    assert env.confirm_pair("write:pg-write").status_code == 200
    unlocked = env.unlock(connection_id, "write:pg-write", "pg-write")
    assert unlocked.status_code == 200, unlocked.text
    assert seen and seen[0]["password"] == CANARY_SECRET  # resolved only now, after the confirmation
    assert CANARY_SECRET not in unlocked.text
    return connection_id, fake


def test_revoking_the_confirmation_ends_the_unlock_and_refuses_a_plan(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canary of the XW-3 integration: every use re-checks the pair, so the plan step is refused once the
    confirmation is gone, before any catalog is read or anything is planned."""
    connection_id, fake = _unlocked(env, monkeypatch)
    assert env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR) is fake
    record = env.confirmations_file
    _private(record, {"version": 1, "confirmed": {}})  # gone, e.g. revoked by another Studio process
    env.studio.client.app.state.write_plans.register(RowExecutor())  # type: ignore[attr-defined]
    request = {"schema": "public", "table": "parts", "rows": [{"id": 1, "material": "Iron"}]}
    plan = env.send(
        "POST", "/api/write/plans", {"connection_id": connection_id, "executor": "test.rows", "request": request}
    )
    assert _code(plan) == (400, "studio.secret_unconfirmed")
    assert fake.closed
    with pytest.raises(WriteError) as refused:
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert refused.value.code == "studio.write_locked"


def test_a_revoke_through_the_route_ends_the_unlock(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    connection_id, fake = _unlocked(env, monkeypatch)
    revoked = env.send("DELETE", "/api/write/secret-confirmations", {"write_profile_id": "write:pg-write"})
    assert revoked.status_code == 204
    assert fake.closed
    assert env.send("GET", "/api/write/unlocks").json()["unlocks"] == []
    confirm = env.send(
        "POST", "/api/write/plans/confirm", {"connection_id": connection_id, "plan_hash": "0" * 64, "typed": "x"}
    )
    assert confirm.status_code == 400


def test_a_tampered_confirmation_does_not_carry_over_to_another_pair(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unlock keeps the digest it was confirmed for: a record swapped for another pair's digest ends it."""
    connection_id, fake = _unlocked(env, monkeypatch)
    _private(env.confirmations_file, {"version": 1, "confirmed": {"pg-write": "a" * 64}})
    with pytest.raises(WriteError) as refused:
        env.unlocks.require(env.session(), connection_id, Area.DATA_EDITOR)
    assert refused.value.code == "studio.secret_unconfirmed"
    assert fake.closed


# --- N-5: no write or admin secret in a listing, a preview or a support output ---------------------------------------


def test_no_write_secret_in_listings_previews_or_the_diagnostics_report(env: WriteEnv) -> None:
    secret = env.send("PUT", "/api/write/profiles/write:pg-write/secret", {"secret": CANARY_SECRET, "store": "session"})
    assert _code(secret) == (409, "studio.secret_not_storable")  # an env: profile names its own source
    created = env.create_write(name="pg-kr", read="pg", user="app_kr", password="keyring:")
    assert created.status_code == 201
    stored = env.send("PUT", "/api/write/profiles/write:pg-kr/secret", {"secret": CANARY_SECRET, "store": "session"})
    assert stored.status_code == 204
    texts = [
        env.send("GET", "/api/write/profiles").text,
        env.send("GET", "/api/profiles").text,
        _preview(env).text,
        _preview(env, "write:pg-kr").text,
        env.confirm_pair("write:pg-kr").text,
        env.send("GET", "/api/write/audit").text,
        env.send("GET", "/api/secrets").text,
    ]
    for text in texts:
        assert CANARY_SECRET not in text
    # The diagnosis report of a read connection is covered in test_write_profiles.py (STU-XW-1 part of N-5).


def test_the_module_is_part_of_the_write_package() -> None:
    """Not reachable from a read path (import-linter ``read-paths-never-write``) and not loaded with every flag off
    (``test_write_kill_switch.py``)."""
    import tabrion_studio.write.secret_confirm as module

    assert module.__name__.startswith("tabrion_studio.write.")
    assert write_connection.REQUIRE_AUTH == REQUIRE_AUTH


# --- G-S2 decision 2A: a server on this computer, loopback_tls in write-areas.toml ------------------------------------


def _local_pair(host: str, sslmode: str | None, requirepeer: str | None = None) -> SecretPair:
    return SecretPair("w", "write", host, 5432, "parts", sslmode, "app_writer", "env", ENV_NAME, requirepeer)


@pytest.mark.parametrize(
    ("host", "sslmode", "requirepeer", "unverified"),
    [
        ("localhost", "prefer", None, True),
        ("127.0.0.1", "disable", None, True),
        ("127.0.0.1", "require", None, True),
        ("127.0.0.1", "verify-ca", None, True),  # verify-ca does not check the name: any local certificate passes
        ("[::1]", "verify-full", None, False),
        ("localhost", "verify-full", None, False),
        ("/var/run/postgresql", "prefer", None, True),
        ("/var/run/postgresql", "prefer", "postgres", False),
        ("db.example.org", "verify-full", None, False),  # foreign: check_transport's rule, not this one
    ],
)
def test_which_pairs_reach_an_unverified_local_server(
    host: str, sslmode: str, requirepeer: str | None, unverified: bool
) -> None:
    pair = _local_pair(host, sslmode, requirepeer)
    assert pair.local_unverified is unverified
    assert pair.to_dict()["local_unverified"] is unverified
    check_local_transport(pair, required=False)  # "accept" (the G-S2 default) never refuses
    if unverified:
        with pytest.raises(WriteError) as refused:
            check_local_transport(pair, required=True)
        assert refused.value.code == "studio.write_loopback_tls_required"
    else:
        check_local_transport(pair, required=True)


def test_local_unverified_is_not_part_of_the_digest() -> None:
    """It follows from the pair; the confirmations of every existing pair stay valid."""
    pair = _local_pair("localhost", "prefer")
    fields = [f.name for f in dataclasses.fields(SecretPair)]
    assert "local_unverified" not in fields
    assert pair.digest == _local_pair("localhost", "prefer").digest


def _require(env: WriteEnv) -> None:
    """``loopback_tls = "require"`` as ``write-areas.toml`` would give it (the areas stay as they are)."""
    env.unlocks.areas = dataclasses.replace(env.unlocks.areas, loopback_tls="require")


def test_require_refuses_a_password_to_an_unverified_local_server_before_it_is_read(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    _require(env)
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    refused = env.unlock(connection_id, "write:pg-write", "pg-write")
    assert _code(refused) == (400, "studio.write_loopback_tls_required")
    assert resolved == [] and captured == []
    assert CANARY_SECRET not in refused.text and "localhost" not in refused.text


def test_require_refuses_a_password_without_a_reference_before_it_is_sent(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, captured: list[dict[str, Any]]
) -> None:
    """A password that comes without a reference (resolved by the secret store) is refused before any connection."""
    created = env.create_write(name="bare-write", read="pg", user="app_bare")
    assert created.status_code == 201, created.text
    _require(env)
    monkeypatch.setattr(env.config.secrets, "resolve", lambda target: CANARY_SECRET)
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:bare-write").status_code == 200
    refused = env.unlock(connection_id, "write:bare-write", "bare-write")
    assert _code(refused) == (400, "studio.write_loopback_tls_required")
    assert captured == [] and CANARY_SECRET not in refused.text


def test_require_lets_verify_full_on_loopback_through(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    current = env.send("GET", "/api/profiles").json()["user_file"]["digest"]
    profile = {
        "name": "pg",
        "engine": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "parts",
        "user": "app_reader",
        "environment": "dev",
        "sslmode": "verify-full",
    }
    assert env.send("PUT", "/api/profiles", {"id": "user:pg", "profile": profile, "digest": current}).status_code == 200
    _require(env)
    preview = _preview(env)
    assert preview.status_code == 200 and preview.json()["pair"]["local_unverified"] is False
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    with pytest.raises(_Stop):
        env.unlocks.unlock(env.session(), connection_id, "write:pg-write", "pg-write")
    (options,) = captured
    assert options["connect_args"]["require_auth"] == REQUIRE_AUTH_SCRAM and len(resolved) == 1


def test_require_lets_a_socket_with_requirepeer_through(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, captured: list[dict[str, Any]]
) -> None:
    assert _move_to_socket(env).status_code == 200
    _require(env)
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    with pytest.raises(_Stop):
        env.unlocks.unlock(env.session(), connection_id, "write:pg-write", "pg-write")
    (options,) = captured
    assert options["connect_args"]["requirepeer"] == "postgres"


def test_require_refuses_a_socket_without_requirepeer(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, resolved: list[object], captured: list[dict[str, Any]]
) -> None:
    assert _move_to_socket(env, None).status_code == 200
    _require(env)
    connection_id = _stand_in_connection(env, monkeypatch)
    assert env.confirm_pair("write:pg-write").status_code == 200
    refused = env.unlock(connection_id, "write:pg-write", "pg-write")
    assert _code(refused) == (400, "studio.write_loopback_tls_required")
    assert resolved == [] and captured == []


def test_accept_unlocks_and_says_the_local_server_is_unverified(
    env: WriteEnv, monkeypatch: pytest.MonkeyPatch, logs: LogSetup
) -> None:
    """The G-S2 default: the unlock goes on, its state and the log say that nothing checks the server."""
    assert env.unlocks.areas.loopback_tls == "accept"
    connection_id, _fake = _unlocked(env, monkeypatch)
    (state,) = env.send("GET", "/api/write/unlocks").json()["unlocks"]
    assert state["connection_id"] == connection_id and state["local_unverified"] is True
    assert state["findings"] == []  # a transport note, never a privilege finding
    assert logs.file is not None
    assert "studio.write_loopback_unverified" in logs.file.read_text(encoding="utf-8")


def test_accept_says_nothing_for_a_socket_with_requirepeer(env: WriteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _move_to_socket(env).status_code == 200
    _connection_id, _fake = _unlocked(env, monkeypatch)
    (state,) = env.send("GET", "/api/write/unlocks").json()["unlocks"]
    assert state["local_unverified"] is False
