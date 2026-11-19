"""Whether a write target is a database that a PROD profile names (L-1 of STU-XDM-1; review I-1 of #144).

A DEV or TEST profile can point at the same database as a PROD profile (an alias: another host name, an address of
the same server). Every path that reads a schema from a write connection or takes the stored environment for a
change matches the target against the PROD profiles of the user's store, the same way:

* **SQLite:** the file, by device and inode;
* **PostgreSQL:** a database of the same name on the same server, where the same server means the same
  ``system_identifier`` (``pg_control_system()``, read on the write connection and on any open read connection of the
  PROD profile, where the role may read it), or the same port and an overlapping set of resolved addresses
  (``localhost``, every loopback address and a socket folder are all "this computer").

A name that resolves differently later, or a server behind a port forward, is a residual risk. Shared by the Database
Manager (:mod:`.database_manager.backups`, :mod:`.database_manager.delete`) and the Schema Designer's revision output
(:mod:`.schema_designer.revision`); it imports neither area.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import ipaddress
import os
from pathlib import Path
import socket
from typing import Any

__all__ = ["SYSTEM_IDENTIFIER", "addresses", "names_prod", "system_identifier"]

SYSTEM_IDENTIFIER = "SELECT pg_catalog.pg_control_system().system_identifier::pg_catalog.text"
_CURRENT_DATABASE = "SELECT pg_catalog.current_database()::pg_catalog.text"


def addresses(host: str | None, port: int | None) -> frozenset[str]:
    """``host`` as a set of addresses: ``local`` for this computer (no host, a socket folder, ``localhost``, a
    loopback address or a name that resolves to one), else the resolved addresses (the name itself if it does not
    resolve, compared as text)."""
    if host is None or host.startswith("/") or host.lower().rstrip(".") == "localhost":
        return frozenset({"local"})
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port or 5432, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return frozenset({f"name:{host.lower().rstrip('.')}"})
        found = set()
        for info in infos:
            text = str(info[4][0]).split("%", 1)[0]
            try:
                parsed = ipaddress.ip_address(text)
            except ValueError:
                continue
            found.add("local" if parsed.is_loopback else str(parsed))
        return frozenset(found) or frozenset({f"name:{host.lower().rstrip('.')}"})
    return frozenset({"local" if address.is_loopback else str(address)})


def system_identifier(fetch: Callable[[str], list[tuple[Any, ...]]]) -> str | None:
    """The server's ``system_identifier``, or None when the role may not read ``pg_control_system()`` (then the
    addresses decide)."""
    try:
        rows = fetch(SYSTEM_IDENTIFIER)
    except Exception:
        return None
    value = rows[0][0] if rows and rows[0] else None
    return value if isinstance(value, str) and value.isdigit() else None


def names_prod(
    connection: Any,
    read: Any,
    profiles: Iterable[Any],
    open_connections: Iterable[Any],
) -> bool:
    """Whether a PROD profile of ``profiles`` names the database of the write ``connection`` (its read profile
    ``read``; ``open_connections`` are the registry's open read connections). Fail closed: a PostgreSQL target whose
    name cannot be read, or without its read profile, counts as PROD."""
    prods = [p for p in profiles if p.environment == "prod" and p.engine == connection.engine]
    if connection.engine == "sqlite":
        identity = connection.identity
        for profile in prods:
            if profile.path is None:
                continue
            try:
                info = os.stat(Path(profile.path).expanduser())
            except OSError:
                continue
            if identity is not None and (info.st_dev, info.st_ino) == tuple(identity):
                return True
        return False
    if not prods:
        return False
    if read is None:
        return True
    try:
        rows = connection.fetch(_CURRENT_DATABASE)
    except Exception:
        return True
    name = str(rows[0][0]) if rows and rows[0] else None
    if name is None:
        return True
    here = addresses(read.host, read.port)
    port = read.port or 5432
    identifier = system_identifier(connection.fetch)
    opened = {c.profile_id: c for c in open_connections}
    for profile in prods:
        if profile.database != name:
            continue
        same = (profile.port or 5432) == port and bool(addresses(profile.host, profile.port) & here)
        other = opened.get(f"user:{profile.name}")
        if not same and identifier is not None and other is not None:
            same = system_identifier(other.fetch) == identifier
        if same:
            return True
    return False
