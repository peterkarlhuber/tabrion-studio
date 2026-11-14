"""STU-XW-4, review L-2 of PR #111, against a real server: a write or admin connection refuses a server that asks
for the password in clear text or as an MD5 hash (libpq ``require_auth=!password,!md5``).

The CI server authenticates every role with SCRAM, so these tests need their own server configuration and are skipped
without it. ``TABRION_STUDIO_TEST_POSTGRES_MD5`` and ``TABRION_STUDIO_TEST_POSTGRES_PASSWORD`` are libpq URLs (role,
password, a loopback host, a database) for which the server asks ``md5`` (a password stored as MD5) and ``password``.
For STU-XW-4 they ran against a throwaway PostgreSQL 16 with these ``pg_hba.conf`` lines before the SCRAM default::

    host all xw4_md5 127.0.0.1/32 md5
    host all xw4_pw  127.0.0.1/32 password

Each test first shows that the same URL connects without ``require_auth``, so the refusal is Studio's option and not
a wrong password. The option itself and the libpq-16 refusal are covered without a server in
``test_write_secret_confirm.py``.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest
import sqlalchemy
import tabrion

from tabrion_studio.config.schema import Profile, Settings
from tabrion_studio.write.connection import REQUIRE_AUTH, open_write_connection

# Not marked ``postgres``: the CI PostgreSQL job requires that none of its tests is skipped, and its server cannot ask
# for MD5 or clear text. These tests run (and skip) in the other jobs and run locally with the variables set.
pytestmark = pytest.mark.security

WEAK = {
    "md5": os.environ.get("TABRION_STUDIO_TEST_POSTGRES_MD5"),
    "password": os.environ.get("TABRION_STUDIO_TEST_POSTGRES_PASSWORD"),
}


@pytest.mark.parametrize("kind", ["write", "admin"])
@pytest.mark.parametrize("method", sorted(WEAK))
def test_a_server_that_asks_for_a_clear_text_or_md5_password_is_refused(method: str, kind: str) -> None:
    raw = WEAK[method]
    if raw is None:
        pytest.skip(f"TABRION_STUDIO_TEST_POSTGRES_{method.upper()} is needed (a server asking {method})")
    url = sqlalchemy.make_url(raw)
    assert url.host is not None and url.password is not None
    conninfo: dict[str, Any] = {
        "host": url.host,
        "port": url.port or 5432,
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
        "sslmode": "disable",
    }
    # The server takes this role and password when the client allows the method ...
    with psycopg.connect(**conninfo) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    # ... and libpq refuses it with Studio's option.
    with pytest.raises(psycopg.OperationalError) as direct:
        psycopg.connect(**conninfo, require_auth=REQUIRE_AUTH)
    assert "require_auth" in str(direct.value) or "authentication method requirement" in str(direct.value)
    profile = Profile(
        "pg",
        "postgresql",
        "dev",
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        sslmode="disable",
    )
    with pytest.raises(tabrion.TabrionError) as refused:
        open_write_connection(kind, profile, Settings(), password=url.password)
    assert refused.value.code == "database.connection"
    assert url.password not in str(refused.value)
