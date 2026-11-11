"""The migration worker: ``tabrion.migrations.status`` in a process of its own (STU-015, threat T-16).

Reading Alembic's revision graph imports the project's revision files, so their code runs. Studio runs it only for a
project whose code the user trusted (:mod:`tabrion_studio.config.code_trust`), and never in its own process: the
worker gets one connection's target (its fields, the password or the database file, the timeouts) on standard input
and nothing else. It does not inherit Studio's environment (only ``PATH``, the home and temporary folders and the
locale), its standard output and error are closed before any project code runs (the answer goes out on a copy of
the standard output taken before), and :func:`tabrion_studio.services.migrations.run_worker` kills it after a
timeout. Its connection is read-only like every Studio connection (:func:`.connections.read_only_database`).

Input (JSON): ``{target, project, script_location, sys_path, digest}``. Output (JSON, one line): ``{current, heads,
pending, is_current, unknown}`` or ``{error: <problem code>}``. The order matters (review of #29):

1. The worker computes the digest of ``alembic.ini`` and the revision files again and keeps the bytes it hashed; it
   refuses to run if the digest is not the trusted one.
2. It opens the read-only connection once before the project's folders are on ``sys.path``, so the database driver
   and what the first connection imports come from Studio's environment, not from a module of the same name in the
   project.
3. It never reads bytecode of the project: no ``__pycache__`` (``sys.pycache_prefix`` is an empty private folder,
   nothing is written), and the project's folders get a finder for ``.py`` sources only (no ``.pyc`` or extension
   module there).
4. Alembic's ``load_python_file`` is replaced, for the revision files, by one that compiles the bytes of step 1, so
   what runs is what was hashed, and a file that was not hashed is not loaded.

What the measures do not cover: a revision file may import other modules of the project (``prepend_sys_path``), whose
sources the digest does not cover; and trusted code runs as the user (see T-16 in ``docs/architecture/migrations.md``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType
from typing import Any

__all__ = ["main"]


def _answer(fd: int, doc: dict[str, Any]) -> None:
    data = json.dumps(doc).encode("utf-8") + b"\n"
    while data:
        data = data[os.write(fd, data) :]


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path or os.curdir), root]) == root
    except ValueError:  # another drive (Windows)
        return False


def _source_only(folders: list[str]) -> Callable[[str], Any]:
    """A path hook: the project's ``sys_path`` folders and the packages in them get a finder for ``.py`` sources only.

    Folders that were on ``sys.path`` before (Studio's own environment, also a virtual environment inside the project)
    keep the interpreter's finder."""
    roots = [os.path.realpath(folder) for folder in folders]
    own = [os.path.realpath(entry) for entry in sys.path if entry]
    hook = importlib.machinery.FileFinder.path_hook(
        (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES)
    )

    def find(path: str) -> Any:
        if not any(_inside(path, root) for root in roots) or any(_inside(path, entry) for entry in own):
            raise ImportError("not a project folder")  # the next hook (the interpreter's own) takes it
        return hook(path)

    return find


def _no_bytecode(folders: list[str]) -> str:
    """No bytecode is read or written from here on, and none from the project's folders; the private, empty cache
    folder to remove afterwards."""
    cache = tempfile.mkdtemp(prefix="tabrion-studio-worker-")
    sys.dont_write_bytecode = True
    sys.pycache_prefix = os.path.join(cache, "none")  # never created: every later import compiles its source
    sys.path_hooks.insert(0, _source_only(folders))
    sys.path_importer_cache.clear()
    return cache


def _hashed_loader(versions: Path, contents: Mapping[str, bytes]) -> Callable[..., ModuleType]:
    """Alembic's ``load_python_file`` for the revision files: the bytes that were hashed, compiled, nothing else."""
    root = os.path.realpath(versions)

    def load_python_file(dir_: str | os.PathLike[str], filename: str | os.PathLike[str]) -> ModuleType:
        name = Path(filename).name
        if os.path.realpath(dir_) != root or name not in contents:
            raise ImportError("not a trusted revision file")
        path = os.path.join(root, name)
        spec = importlib.util.spec_from_file_location(re.sub(r"\W", "_", name), path)
        if spec is None:
            raise ImportError("not a trusted revision file")
        module = importlib.util.module_from_spec(spec)
        exec(compile(contents[name], path, "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - trusted code
        return module

    return load_python_file


def _status(request: dict[str, Any]) -> dict[str, Any]:
    from alembic import util as alembic_util
    import alembic.runtime.migration  # noqa: F401 - loaded before the project's folders are on sys.path
    from tabrion.errors import ConfigurationError, TabrionError
    from tabrion.migrations import status

    from tabrion_studio.config.code_trust import scan
    from tabrion_studio.config.schema import Profile, Settings
    from tabrion_studio.services.connections import RegistryError, read_only_database

    project = Path(request["project"])
    found = scan(project, keep=True)
    if found is None or found.digest != request["digest"] or str(found.script_location) != request["script_location"]:
        return {"error": "studio.migrations_changed"}
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
        # Connect once while sys.path is still Studio's own: the driver, the dialect and what the first connection
        # imports are loaded from Studio's environment (M-1 of the review). Alembic's own modules are imported above.
        with db.read():
            pass
        # A folder only: no archive (zipimport reads bytecode).
        folders = [folder for folder in request["sys_path"] if os.path.isdir(folder)]
        cache = _no_bytecode(folders)
        alembic_util.load_python_file = _hashed_loader(found.script_location / "versions", found.contents)
        for folder in reversed(folders):
            sys.path.insert(0, folder)
        try:
            result = status(db, script_location=request["script_location"])
        finally:
            with contextlib.suppress(OSError):
                os.rmdir(cache)
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
