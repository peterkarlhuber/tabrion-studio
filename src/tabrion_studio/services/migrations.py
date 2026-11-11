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

* ``nodestro``, ``axentra``, ``orkexa`` (PostgreSQL, STU-024): the version tables of the Levuvo platforms compared
  with the migration files of the project folder (:mod:`.levuvo_migrations`); nothing is imported or run.

Everything reads. Studio never migrates (03 section 2 F: "Migrationen ausführen" is not in 1.0).
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Any, cast

import tabrion_studio
from tabrion_studio.config.code_trust import CodeTrust, CodeTrustState
from tabrion_studio.problems import problem
from tabrion_studio.services.connections import ConnectionRegistry, OpenConnection
from tabrion_studio.services.levuvo_migrations import levuvo_formats

__all__ = ["NOT_TRUSTED", "WORKER_TIMEOUT_S", "Migrations", "run_worker", "worker_environment"]

#: How long the worker may take, connecting included, before it is killed.
WORKER_TIMEOUT_S = 30.0
#: The largest answer the worker may give; its answer is a few revision ids.
MAX_ANSWER_BYTES = 1024 * 1024
#: How long to wait for a killed worker to end before giving up on it (it ends at once; this is never unbounded).
_REAP_S = 5.0
_PIPE_CHUNK = 65536
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
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
    """Run :mod:`.migration_worker` with ``request`` on its standard input; its answer, or ``{error}``.

    Bounded in time and size whatever the project's code does: the answer is read up to its line end or
    :data:`MAX_ANSWER_BYTES`, never until the pipe closes (a child that left the process group may keep it open), and
    after ``timeout_s`` the worker's process group is killed and nothing more is waited for."""
    package_root = str(Path(tabrion_studio.__file__).resolve().parent.parent)
    posix = os.name == "posix"
    deadline = time.monotonic() + timeout_s
    process = subprocess.Popen(  # noqa: S603 - a fixed command: this interpreter and the worker module
        [sys.executable, "-I", "-B", "-c", _BOOT, package_root],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        env=worker_environment(),
        start_new_session=posix,  # the whole process group can be killed, also children of the project's code
    )
    # Windows: the worker goes into a job that ends every process in it when it is closed (TD-024). It is assigned
    # before the worker gets its request, so no project code has run yet.
    close_job = _windows_job(process)
    data = json.dumps(request).encode("utf-8")
    try:
        output = _exchange_posix(process, data, deadline) if posix else _exchange_threads(process, data, deadline)
    finally:
        close_job()
        # The answer is in (the worker only exits after it), or the time is up: the worker's process group goes, with
        # any child the project's code left in it. Killed before it is waited for, so the group id is still its own.
        if posix or process.poll() is None:
            _kill(process, posix)
        # On Windows a reader thread may still be blocked on the answer pipe; closing it there could wait for it.
        for pipe in (process.stdin, process.stdout) if posix else (process.stdin,):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(_REAP_S)  # killed: it ends at once; never an unbounded wait
    if output is None:
        return {"error": "studio.migrations_timeout"}
    if len(output) > MAX_ANSWER_BYTES or not output.endswith(b"\n"):
        return {"error": "studio.migrations_failed"}
    try:
        doc = json.loads(output)
    except ValueError:
        return {"error": "studio.migrations_failed"}
    return doc if isinstance(doc, dict) else {"error": "studio.migrations_failed"}


def _exchange_posix(process: subprocess.Popen[bytes], data: bytes, deadline: float) -> bytes | None:
    """Write ``data``, read up to the first line end (at most ``MAX_ANSWER_BYTES + 1`` bytes); None at the deadline."""
    if process.stdin is None or process.stdout is None:
        raise ValueError("the worker's pipes are missing")
    stdin, stdout = process.stdin.fileno(), process.stdout.fileno()
    os.set_blocking(stdin, False)
    os.set_blocking(stdout, False)
    output = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(stdin, selectors.EVENT_WRITE)
        selector.register(stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            for key, _ in selector.select(remaining):
                if key.fd == stdin:
                    try:
                        data = data[os.write(stdin, data[:_PIPE_CHUNK]) :]
                    except BlockingIOError:
                        continue
                    except OSError:  # BrokenPipeError: the worker stopped reading; its answer tells why
                        data = b""
                    if not data:
                        selector.unregister(stdin)
                        process.stdin.close()
                    continue
                try:
                    chunk = os.read(stdout, _PIPE_CHUNK)
                except BlockingIOError:
                    continue
                output += chunk
                if not chunk or b"\n" in chunk or len(output) > MAX_ANSWER_BYTES:
                    return bytes(output[: MAX_ANSWER_BYTES + 1])


def _exchange_threads(process: subprocess.Popen[bytes], data: bytes, deadline: float) -> bytes | None:
    """Like :func:`_exchange_posix` with a reader thread (Windows: no ``select`` on pipes). After a timeout the
    thread may stay blocked while a child of the worker that survived its kill holds the pipe (see docs/TODO.md)."""
    if process.stdin is None or process.stdout is None:
        raise ValueError("the worker's pipes are missing")
    stdout = process.stdout.fileno()
    output = bytearray()
    done = threading.Event()

    def read() -> None:
        try:
            while not done.is_set():
                chunk = os.read(stdout, _PIPE_CHUNK)
                output.extend(chunk)
                if not chunk or b"\n" in chunk or len(output) > MAX_ANSWER_BYTES:
                    break
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    reader = threading.Thread(target=read, name="tabrion-studio-migration-worker", daemon=True)
    reader.start()
    try:
        process.stdin.write(data)
        process.stdin.close()
    except OSError:
        pass
    if not done.wait(max(deadline - time.monotonic(), 0.0)):
        return None
    return bytes(output[: MAX_ANSWER_BYTES + 1])


def _windows_job(process: subprocess.Popen[bytes]) -> Callable[[], None]:
    """On Windows, put the worker into a job object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and return what closes
    it, which ends the worker and every process it started (children cannot leave the job). Elsewhere, or when the
    job cannot be made, a no-op: then only the worker itself is killed."""
    if sys.platform != "win32":
        return lambda: None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [  # the layout of JOBOBJECT_BASIC_LIMIT_INFORMATION
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Counters(ctypes.Structure):
        _fields_ = [  # IO_COUNTERS
            (name, ctypes.c_uint64) for name in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes", "OtherBytes")
        ]

    class Extended(ctypes.Structure):
        _fields_ = [  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            ("BasicLimitInformation", Basic),
            ("IoInfo", Counters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return lambda: None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    handle = int(cast(Any, process)._handle)  # Popen's process handle on Windows
    if not (
        kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        )
        and kernel32.AssignProcessToJobObject(job, handle)
    ):
        kernel32.CloseHandle(job)
        return lambda: None
    return lambda: kernel32.CloseHandle(job) and None


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
    #: One worker at a time: a second request while one runs gets ``studio.migrations_busy`` (review L-3 of #29).
    _busy: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def status(self, connection_id: object) -> dict[str, Any]:
        connection = self.registry.get(connection_id)
        formats: list[dict[str, Any]] = []
        if connection.engine == "sqlite":
            formats.append({"format": "sqlite_user_version", "current": connection.fetch("PRAGMA user_version")[0][0]})
        code = self.code.load()
        if code.state != "none":
            formats.append(self._alembic(connection, code))
        formats += levuvo_formats(connection, self.code.project)  # STU-024: reads only, runs nothing
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
        if not self._busy.acquire(blocking=False):
            answer: dict[str, Any] = {"error": "studio.migrations_busy"}
        else:
            try:
                answer = self.runner(request, cwd=self.code.project)
            finally:
                self._busy.release()
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
