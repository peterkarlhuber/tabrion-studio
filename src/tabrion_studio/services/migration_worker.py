"""The migration worker: ``tabrion.migrations.status`` in a process of its own (STU-015, threat T-16).

Reading Alembic's revision graph imports the project's revision files, so their code runs. Studio runs it only for a
project whose code the user trusted (:mod:`tabrion_studio.config.code_trust`), and never in its own process: the
worker gets one connection's target (its fields, the password or the database file, the timeouts) on standard input
and nothing else. It does not inherit Studio's environment (only ``PATH``, the home and temporary folders and the
locale), its standard output and error are closed before any project code runs (the answer goes out on a copy of
the standard output taken before), and :func:`tabrion_studio.services.migrations.run_worker` kills it after a
timeout. Its connection is read-only like every Studio connection (:func:`.connections.read_only_database`).

Input (JSON): ``{target, project, script_location, sys_path, digest}``. Output (JSON, one line): ``{current, heads,
pending, is_current, unknown}`` or ``{error: <problem code>}``. Before it imports anything of the project, the worker
computes the digest of ``alembic.ini`` and the revision files again and refuses to run if it is not the trusted one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

__all__ = ["main"]


def _answer(fd: int, doc: dict[str, Any]) -> None:
    data = json.dumps(doc).encode("utf-8") + b"\n"
    while data:
        data = data[os.write(fd, data) :]


def _status(request: dict[str, Any]) -> dict[str, Any]:
    from tabrion.errors import ConfigurationError, TabrionError
    from tabrion.migrations import status

    from tabrion_studio.config.code_trust import scan
    from tabrion_studio.config.schema import Profile, Settings
    from tabrion_studio.services.connections import RegistryError, read_only_database

    project = Path(request["project"])
    found = scan(project)
    if found is None or found.digest != request["digest"] or str(found.script_location) != request["script_location"]:
        return {"error": "studio.migrations_changed"}
    for folder in reversed(request["sys_path"]):
        sys.path.insert(0, folder)
    target = request["target"]
    fields = target["profile"]
    profile = Profile(
        fields["name"],
        fields["engine"],
        fields["environment"],
        path=fields["path"],
        host=fields["host"],
        port=fields["port"],
        database=fields["database"],
        user=fields["user"],
        sslmode=fields["sslmode"],
        statement_timeout_ms=fields["statement_timeout_ms"],
    )
    limits = Settings(**target["limits"])
    database_file = None if target["database_file"] is None else Path(target["database_file"])
    try:
        db = read_only_database(profile, limits, target["password"], database_file)
    except RegistryError as exc:
        return {"error": exc.code}
    try:
        result = status(db, script_location=request["script_location"])
    except ConfigurationError:
        return {"error": "studio.migrations_revisions"}
    except TabrionError:
        return {"error": "studio.migrations_database"}
    finally:
        db.close()
    return {
        "current": list(result.current),
        "heads": list(result.heads),
        "pending": list(result.pending),
        "is_current": result.is_current,
        "unknown": list(result.unknown),
    }


def main() -> None:
    answer = os.dup(1)
    request = json.loads(sys.stdin.buffer.read())
    # Nothing the project's code prints reaches Studio, and nothing it reads comes from Studio.
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    try:
        doc = _status(request)
    except Exception:
        doc = {"error": "studio.migrations_failed"}
    _answer(answer, doc)
    os._exit(0)  # no atexit handler or finalizer of the project's code runs after the answer


if __name__ == "__main__":
    main()
