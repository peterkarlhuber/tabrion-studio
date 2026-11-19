"""Review L-1 of PR #153: a PostgreSQL host with a leading ``@`` (libpq on Linux: a Unix socket in the abstract
namespace) is refused everywhere, independent of the driver.

Any local process can bind such a socket, and libpq ignores ``sslmode`` on a socket, so neither the TLS rule of a
foreign host nor ``requirepeer`` would protect a password sent there. The validator refuses such a profile in every
file and for every kind (``config.host_abstract_socket``); every later path that could still see one refuses it on
its own: the loopback rule, the secret pair, both transport checks under either ``loopback_tls`` setting, the last
check before every connection, the ``pg_dump``/``pg_restore`` connection, and the profile export. No test here
connects to anything or needs a PostgreSQL driver.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pytest

from tabrion_studio.config.schema import (
    Profile,
    SecretRef,
    WriteProfile,
    coupling_issue,
    is_abstract_socket,
    is_loopback,
    refused_host,
    validate_profiles,
)
from tabrion_studio.services.connections import RegistryError, connection_url
from tabrion_studio.write import pgtools
from tabrion_studio.write.audit import AuditLog
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.profile_export import export_profiles
from tabrion_studio.write.secret_confirm import SecretPair, check_local_transport, check_transport, secret_pair

pytestmark = pytest.mark.security

#: Abstract socket names as libpq would read them; the canary must never appear in a refusal.
CANARY = "l1canary"
HOSTS = ["@x", f"@{CANARY}", "@/var/run/postgresql", "@localhost", "@127.0.0.1", "@"]
SSLMODES = [None, "disable", "prefer", "require", "verify-ca", "verify-full"]


def _read(host: str, sslmode: str | None = "verify-full") -> Profile:
    return Profile("pg", "postgresql", "dev", host=host, port=5432, database="parts", user="reader", sslmode=sslmode)


def _pair(host: str, sslmode: str | None, requirepeer: str | None = None) -> SecretPair:
    """A pair as no validated profile can produce it (built directly, past the validator and ``secret_pair``)."""
    return SecretPair("w", "write", host, 5432, "parts", sslmode, "app_writer", "env", "A", requirepeer)


# --- the validator ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_an_abstract_socket_is_recognized_and_never_loopback(host: str) -> None:
    assert is_abstract_socket(host)
    assert refused_host(host) == "config.host_abstract_socket"
    assert not is_loopback(host)


@pytest.mark.parametrize("host", ["/var/run/postgresql", "localhost", "127.0.0.1", "::1", "db.example.org", "a@b"])
def test_other_hosts_are_not_abstract_sockets(host: str) -> None:
    assert not is_abstract_socket(host)
    assert refused_host(host) is None


@pytest.mark.parametrize("sslmode", SSLMODES)
@pytest.mark.parametrize("owner", ["project", "user"])
@pytest.mark.parametrize("host", HOSTS)
def test_the_validator_refuses_an_abstract_socket_in_every_file(host: str, owner: str, sslmode: str | None) -> None:
    """Refused whatever ``sslmode`` says (libpq ignores it on a socket); no profile results, the value is not
    repeated, and no ``sslmode`` default is derived from it."""
    profile: dict[str, Any] = {
        "name": "pg",
        "engine": "postgresql",
        "host": host,
        "database": "parts",
        "user": "reader",
        "password": "keyring:",
        "environment": "dev",
    }
    if sslmode is not None:
        profile["sslmode"] = sslmode
    result = validate_profiles({"version": 1, "profile": [profile]}, owner=owner)  # type: ignore[arg-type]
    assert [(issue.pointer, issue.code, issue.severity) for issue in result.issues] == [
        ("profile[0].host", "config.host_abstract_socket", "error")
    ]
    assert result.profiles == []
    assert CANARY not in json.dumps([issue.to_dict() for issue in result.issues])


@pytest.mark.parametrize("host", HOSTS)
def test_no_write_or_admin_profile_couples_to_an_abstract_socket(host: str) -> None:
    """In the user's file the read profile is refused, so neither a write nor an admin profile couples to it;
    ``coupling_issue`` refuses such a read profile on its own as well."""
    doc = {
        "version": 1,
        "profile": [
            {"name": "pg", "engine": "postgresql", "host": host, "database": "d", "user": "r", "environment": "dev"}
        ],
        "write_profile": [
            {"name": "pg-write", "kind": "write", "read_profile": "pg", "user": "writer", "password": "keyring:"},
            {"name": "pg-admin", "kind": "admin", "read_profile": "pg", "user": "admin", "password": "keyring:"},
        ],
    }
    result = validate_profiles(doc, owner="user")
    assert result.profiles == [] and result.write_profiles == []
    assert {(i.pointer, i.code) for i in result.issues if i.severity == "error"} == {
        ("profile[0].host", "config.host_abstract_socket"),
        ("write_profile[0].read_profile", "config.coupling_unknown"),
        ("write_profile[1].read_profile", "config.coupling_unknown"),
    }
    for kind in ("write", "admin"):
        write = WriteProfile("w", kind, "pg", user="writer")
        assert coupling_issue(write, _read(host)) == "config.host_abstract_socket"


# --- every later path, past the validator ----------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_no_connection_url_is_built_for_an_abstract_socket(host: str) -> None:
    """The last check before every PostgreSQL connection (read, write, admin)."""
    with pytest.raises(RegistryError) as refused:
        connection_url(_read(host), "pw")
    assert refused.value.code == "studio.profile_invalid"
    assert CANARY not in str(refused.value)


@pytest.mark.parametrize("host", HOSTS)
def test_an_abstract_socket_has_no_secret_pair(host: str) -> None:
    write = WriteProfile("w", "write", "pg", user="u", password=SecretRef("env", "A"))
    with pytest.raises(WriteError) as refused:
        secret_pair(write, _read(host))
    assert refused.value.code == "studio.write_coupling_invalid"
    assert CANARY not in str(refused.value)


@pytest.mark.parametrize("requirepeer", [None, "postgres"])
@pytest.mark.parametrize("sslmode", SSLMODES)
@pytest.mark.parametrize("host", HOSTS)
def test_neither_transport_check_ever_accepts_an_abstract_socket(
    host: str, sslmode: str | None, requirepeer: str | None
) -> None:
    """``check_transport`` and ``check_local_transport`` under ``loopback_tls = "accept"`` and ``"require"``: refused
    with every ``sslmode``, also ``verify-full``, which a socket would not honor."""
    pair = _pair(host, sslmode, requirepeer)
    with pytest.raises(WriteError) as refused:
        check_transport(pair)
    assert refused.value.code == "studio.write_coupling_invalid"
    for required in (False, True):
        with pytest.raises(WriteError) as refused:
            check_local_transport(pair, required=required)
        assert refused.value.code == "studio.write_coupling_invalid"


@pytest.mark.parametrize("host", HOSTS)
def test_no_tool_connection_is_built_for_an_abstract_socket(host: str) -> None:
    """``pg_dump``/``pg_restore``: no :class:`pgtools.Connection` exists for such a host, so no ``--dbname=``
    connection string and no passfile line carries it."""
    with pytest.raises(WriteError) as refused:
        pgtools.Connection(host, 5432, "parts", "app_writer", "verify-full", password="pw")
    assert refused.value.code == "studio.write_coupling_invalid"
    assert CANARY not in str(refused.value)


@pytest.mark.parametrize("host", HOSTS)
def test_conninfo_refuses_an_abstract_socket_on_its_own(host: str) -> None:
    """Defense in depth: even a connection built past its own check (``object.__new__``) gets no connection string."""
    connection = object.__new__(pgtools.Connection)
    for name, value in (
        ("host", host),
        ("port", 5432),
        ("dbname", "parts"),
        ("user", "app_writer"),
        ("sslmode", "verify-full"),
        ("password", None),
        ("requirepeer", None),
    ):
        object.__setattr__(connection, name, value)
    with pytest.raises(WriteError) as refused:
        pgtools.conninfo(connection)
    assert refused.value.code == "studio.write_coupling_invalid"


def test_the_tool_environment_never_names_a_host(tmp_path: Path) -> None:
    """The tool's environment is built from nothing: no ``PGHOST`` or ``PGHOSTADDR`` reaches it, so the host comes
    only from the checked ``--dbname=`` string."""
    for passfile in (None, tmp_path / "passfile"):
        env = pgtools.child_environment(tmp_path, passfile)
        assert not {"PGHOST", "PGHOSTADDR", "PGSERVICE"} & set(env)
        assert not any(value.startswith("@") for value in env.values())


@dataclass
class _Entry:
    id: str
    profile: Profile


@pytest.mark.parametrize("host", HOSTS)
def test_the_profile_export_refuses_an_abstract_socket(tmp_path: Path, host: str) -> None:
    """Never written to a file that another program (and the coming profile import) reads."""
    target = tmp_path / "pg.tabrion-profile.toml"
    doc = {"profiles": ["user:pg"], "path": str(target), "typed": target.name}
    with pytest.raises(WriteError) as refused:
        export_profiles([_Entry("user:pg", _read(host))], AuditLog(None), [], doc, area="schema_designer", action="x")
    assert refused.value.code == "studio.profile_export_invalid"
    assert refused.value.issues == [("export.profiles", "export.host_abstract_socket")]
    assert not target.exists()
