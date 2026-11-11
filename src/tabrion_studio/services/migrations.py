"""Migration status (03 section 2 F, STU-015): Alembic and SQLite ``user_version`` on one open connection.

``Migrations.status`` answers ``{connection_id, engine, formats: [...]}`` with one entry per format found:

* ``sqlite_user_version`` (SQLite): ``PRAGMA user_version``, the version tabrion's ``sqlite.migrate`` keeps (its
  migrations are Python callables of the application, so there is no folder to compare with in tabrion 0.1).
* ``alembic`` (the project has ``alembic.ini`` with a ``script_location``): ``current`` is read from the version table
  ``alembic_version`` with plain SQL, which runs no project code (``null`` when the table does not exist).
  ``heads``, ``pending``, ``is_current`` and ``unknown`` need the revision graph, and reading it imports the
  revision files (threat T-16). So they are read only when the user trusted the project's code
  (:class:`tabrion_studio.config.code_trust.CodeTrust`), in a separate process with only this connection's target
  (:func:`run_worker`, :mod:`.migration_worker`). Without that trust they are ``null`` and ``revisions`` says
  ``unknown (project not trusted)``; ``code_trust`` gives the state (``untrusted``, ``changed``, ``invalid``,
  ``trusted``).

Everything reads. Studio never migrates (03 section 2 F: "Migrationen ausführen" is not in 1.0).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any

import tabrion_studio
from tabrion_studio.config.code_trust import CodeTrust, CodeTrustState
from tabrion_studio.problems import problem
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection

__all__ = ["NOT_TRUSTED", "WORKER_TIMEOUT_S", "Migrations", "run_worker", "worker_environment"]

#: How long the worker may take, connecting included, before it is killed.
WORKER_TIMEOUT_S = 30.0
#: The largest answer the worker may give; its answer is a few revision ids.
MAX_ANSWER_BYTES = 1024 * 1024
NOT_TRUSTED = "unknown (project not trusted)"
_REVISIONS_TEXT = {
    "untrusted": NOT_TRUSTED,
    "changed": NOT_TRUSTED,
    "invalid": "unknown (migrations folder not valid)",
}
#: The only variables the worker inherits: where programs and temporary files are, the home folder, the locale.
#: Nothing else of Studio's environment, so no ``env:`` secret of another profile and no ``PG*`` variable.
_WORKER_VARIABLES = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)
#: Starts the worker from the same ``tabrion_studio`` package as Studio, in isolated mode (``-I``: no
#: ``PYTHON*`` variables, no user site, no current folder on ``sys.path``).
_BOOT = "import sys; sys.path.insert(0, sys.argv[1]); from tabrion_studio.services.migration_worker import main; main()"
_ALEMBIC_TABLE = {
    "sqlite": "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'",
    "postgresql": (
        "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = 'alembic_version' AND c.relkind IN ('r', 'p') "
        "AND n.nspname = ANY (pg_catalog.current_schemas(false))"
    ),
}
_ALEMBIC_CURRENT = "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 100"


def worker_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The worker's environment: :data:`_WORKER_VARIABLES` of ``environ`` (default: Studio's), nothing else."""
    source = os.environ if environ is None else environ
    return {name: source[name] for name in _WORKER_VARIABLES if name in source}


def run_worker(request: dict[str, Any], *, cwd: Path, timeout_s: float = WORKER_TIMEOUT_S) -> dict[str, Any]:
    """Run :mod:`.migration_worker` with ``request`` on its standard input; its answer, or ``{error}``."""
    package_root = str(Path(tabrion_studio.__file__).resolve().parent.parent)
    posix = os.name == "posix"
    process = subprocess.Popen(  # noqa: S603 - a fixed command: this interpreter and the worker module
        [sys.executable, "-I", "-c", _BOOT, package_root],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        env=worker_environment(),
        start_new_session=posix,  # the whole process group can be killed, also children of the project's code
    )
    try:
        output, _ = process.communicate(json.dumps(request).encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(process, posix)
        process.communicate()
        return {"error": "studio.migrations_timeout"}
    finally:
        if process.poll() is None:
            _kill(process, posix)
            process.wait()
    if len(output) > MAX_ANSWER_BYTES:
        return {"error": "studio.migrations_failed"}
    try:
        doc = json.loads(output)
    except ValueError:
        return {"error": "studio.migrations_failed"}
    return doc if isinstance(doc, dict) else {"error": "studio.migrations_failed"}


def _kill(process: subprocess.Popen[bytes], posix: bool) -> None:
    try:
        if posix:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _revisions(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) and len(item) <= 200 for item in value):
        return None
    return value[:1000]


@dataclass
class Migrations:
    """The migration status of open connections. Blocking (call it off the event loop)."""

    registry: ConnectionRegistry
    code: CodeTrust
    runner: Callable[..., dict[str, Any]] = field(default=run_worker)

    def status(self, connection_id: object) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        formats: list[dict[str, Any]] = []
        if connection.engine == "sqlite":
            formats.append({"format": "sqlite_user_version", "current": connection.fetch("PRAGMA user_version")[0][0]})
        code = self.code.load()
        if code.state != "none":
            formats.append(self._alembic(connection, code))
        return {"connection_id": connection.id, "engine": connection.engine, "formats": formats}

    def _alembic(self, connection: OpenConnection, code: CodeTrustState) -> dict[str, Any]:
        current = None
        if connection.fetch(_ALEMBIC_TABLE[connection.engine])[0][0]:
            current = [str(row[0]) for row in connection.fetch(_ALEMBIC_CURRENT)]
        trust = code.to_dict(self.code.project)
        doc: dict[str, Any] = {
            "format": "alembic",
            "code_trust": code.state,
            "code_issue": trust["issue"],
            "script_location": trust["script_location"],
            "current": current,
            "heads": None,
            "pending": None,
            "is_current": None,
            "unknown": None,
            "revisions": _REVISIONS_TEXT.get(code.state, NOT_TRUSTED),
            "problem": None,
        }
        if code.state != "trusted" or code.scan is None:
            return doc
        request = {
            "target": self.registry.worker_target(connection.id),
            "project": str(self.code.project),
            "script_location": str(code.scan.script_location),
            "sys_path": [str(path) for path in code.scan.sys_path],
            "digest": code.scan.digest,
        }
        answer = self.runner(request, cwd=self.code.project)
        error = answer.get("error")
        heads, pending = _revisions(answer.get("heads")), _revisions(answer.get("pending"))
        found, unknown = _revisions(answer.get("current")), _revisions(answer.get("unknown"))
        if error is not None or heads is None or pending is None or found is None or unknown is None:
            code_text = error if isinstance(error, str) else "studio.migrations_failed"
            try:
                doc["problem"] = problem(code_text).to_dict()
            except ValueError:
                doc["problem"] = problem("studio.migrations_failed").to_dict()
            doc["revisions"] = "unknown (could not be read)"
            return doc
        doc.update(
            current=found,
            heads=heads,
            pending=pending,
            is_current=answer.get("is_current") is True,
            unknown=unknown,
            revisions="read",
        )
        return doc
