"""TD-034 on a real PostgreSQL server: the database goes down (here: it refuses new sessions and Studio's are ended).
Requests and the health check fail as ``db.connection``, "open" closes the lost connection and fails while the
database is down, and opens one that reads again once it is back. Needs ``TABRION_STUDIO_TEST_POSTGRES_SUPERUSER``.

A database that only ended Studio's sessions (``pg_terminate_backend``) and accepts new ones is not lost: tabrion's pool
checks a session before it lends it (``pool_pre_ping``) and opens a new one.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import os

import pytest
import sqlalchemy
from tabrion.errors import Kind, TabrionError

from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services.connections import APPLICATION_NAME, ConnectionRegistry, HealthError
from tests.security.test_read_only import Source

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
PG_SUPERUSER_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES_SUPERUSER")

pytestmark = [
    pytest.mark.postgres_superuser,
    pytest.mark.skipif(
        PG_URL is None or PG_SUPERUSER_URL is None,
        reason="TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER are needed",
    ),
]


def _profile(url: sqlalchemy.URL, environment: str) -> Profile:
    return Profile(
        "pg",
        "postgresql",
        environment,
        host=url.host,
        port=url.port,
        database=url.database,
        user=url.username,
        password=SecretRef("keyring", ""),
        sslmode="prefer",
    )


@contextlib.contextmanager
def _database_down(database: str | None) -> Iterator[None]:
    """The database refuses new sessions and Studio's sessions are ended, as when it is down; restored after."""
    assert PG_SUPERUSER_URL is not None and database is not None
    admin = sqlalchemy.create_engine(
        sqlalchemy.make_url(PG_SUPERUSER_URL).set(database="postgres", drivername="postgresql+psycopg"),
        isolation_level="AUTOCOMMIT",
    )
    name = '"' + database.replace('"', '""') + '"'
    connection = admin.connect()
    try:
        connection.exec_driver_sql(f"ALTER DATABASE {name} ALLOW_CONNECTIONS false")
        ended = connection.exec_driver_sql(
            "SELECT count(*) FILTER (WHERE pg_terminate_backend(pid)) FROM pg_stat_activity"
            " WHERE application_name = %(name)s AND datname = %(db)s",
            {"name": APPLICATION_NAME, "db": database},
        ).scalar_one()
        assert ended >= 1
        yield
    finally:
        connection.exec_driver_sql(f"ALTER DATABASE {name} ALLOW_CONNECTIONS true")
        connection.close()
        admin.dispose()


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_td034_a_database_that_went_down_is_lost_and_open_replaces_the_connection(environment: str) -> None:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    source = Source(profiles={"user:pg": _profile(url, environment)}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    confirmed = "prod" if environment == "prod" else None
    try:
        first = registry.open("user:pg", confirmed)
        assert registry.check(first.id)["state"] == "open"
        assert first.fetch("SELECT 1")[0] == (1,)
        with _database_down(url.database):
            # The page's next request fails as db.connection (the banner's trigger), and so does the health check
            # the page polls. The connection stays registered: the banner names it.
            with pytest.raises(TabrionError) as failed:
                first.fetch("SELECT 1")
            assert failed.value.classification is not None
            assert failed.value.classification.kind is Kind.CONNECTION
            with pytest.raises(HealthError) as lost:
                registry.check(first.id)
            assert lost.value.classification.kind is Kind.CONNECTION
            assert [c.id for c in registry.list()] == [first.id]
            # "Reconnect" while it is still down: the lost connection is closed, no new one opens.
            with pytest.raises(HealthError):
                registry.open("user:pg", confirmed)
            assert registry.list() == []
        # Back up: "Reconnect" opens a connection that reads again.
        second = registry.open("user:pg", confirmed)
        assert second.id != first.id
        assert [c.id for c in registry.list()] == [second.id]
        assert registry.check(second.id)["state"] == "open"
        assert second.fetch("SELECT 2")[0] == (2,)
    finally:
        registry.close_all()
