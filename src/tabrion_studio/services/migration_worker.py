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

1. The interpreter starts without :mod:`site` (``-S``). Before any folder of the environment is added, the worker
   puts an import guard (:class:`_ProjectGuard`) at the front of ``sys.meta_path``; then it runs :func:`site.main`
   itself, so the environment's ``.pth`` files, the finders they install and ``sitecustomize`` are imported under
   the guard (review C-1 of F-7). Afterwards the guard goes back to the front, since a ``.pth`` file may insert its
   finder before it (editable installs of meson-python and scikit-build-core), and the worker refuses to read the
   revisions if anything is in front of it then (review C-2).
2. The worker computes the digest of ``alembic.ini``, the revision files and the project modules under the import
   roots again and keeps the bytes it hashed; it refuses to run if the digest is not the trusted one. The folders it
   adds to ``sys.path`` are the import roots of its own scan.
3. It opens the read-only connection once before the project's folders are on ``sys.path``, so the database driver
   and what the first connection imports come from Studio's environment, not from a module of the same name in the
   project.
4. It never reads bytecode of the project: no ``__pycache__`` (``sys.pycache_prefix`` is an empty private folder,
   nothing is written), and the project's folders get a finder for ``.py`` sources only (no ``.pyc`` or extension
   module there), whose loader compiles the module bytes of step 2 and refuses a module that was not hashed (F-7).
   The guard holds every other finder to the same rule: a module whose file lies in the project folder (by its real
   path) but outside Studio's installation is loaded from its hashed bytes or refused, whichever finder found it (a
   folder on ``sys.path`` through a ``.pth`` file, an editable install's finder); an extension module, bytecode or
   a module that was not hashed is refused. Studio's installation is ``sys.prefix``, ``sys.exec_prefix``, their base
   prefixes, the environment's site folders and the folder that holds :mod:`tabrion_studio`; one of them that holds
   the project folder itself does not count. When Studio runs from a source checkout inside the project folder,
   the worker refuses to run (fail closed).
5. Alembic's ``load_python_file`` is replaced, for the revision files, by one that compiles the bytes of step 2, so
   what runs is what was hashed, and a file that was not hashed is not loaded.

What the measures do not cover: trusted code runs as the user, and what it reads or runs by other means than an
import (see T-16 in ``docs/architecture/migrations.md``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
import importlib.abc
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


def _lexically_inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path or os.curdir), root]) == root
    except ValueError:  # another drive (Windows)
        return False


class _HashedSourceLoader(importlib.machinery.SourceFileLoader):
    """A source loader that compiles the hashed bytes of a project module (by :func:`.code_trust.module_key` of its
    path, in :attr:`modules`) and refuses a module that was not hashed; it never looks for bytecode."""

    modules: Mapping[str, bytes] = {}
    key: Callable[[str], str] = staticmethod(lambda path: os.path.normcase(os.path.realpath(path)))

    def get_data(self, path: str) -> bytes:
        if path.endswith(".py"):
            data = self.modules.get(self.key(path))
            if data is None:
                raise ImportError("not a trusted project module")
            return data
        return super().get_data(path)  # a package's data file, not code

    def path_stats(self, path: str) -> Mapping[str, Any]:
        raise OSError("no bytecode")  # get_code then reads the source (get_data) and compiles it


def _hashed_modules(modules: Mapping[str, bytes]) -> type[_HashedSourceLoader]:
    """The loader class for ``modules`` (the hashed module bytes by :func:`.code_trust.module_key`)."""
    from tabrion_studio.config.code_trust import module_key

    return type("HashedSourceLoader", (_HashedSourceLoader,), {"modules": modules, "key": staticmethod(module_key)})


def _identity(path: str) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino)


class _ProjectGuard(importlib.abc.MetaPathFinder):
    """The first finder on ``sys.meta_path`` (review C-1 of F-7): it asks the other finders for a module and checks
    where the module lies. A module whose file (by its real path) lies in the project folder but not in a folder of
    Studio's installation is project code: it is loaded through the hashed loader when its bytes were hashed, and
    refused otherwise (an extension module, bytecode, a module that was not hashed, a loader without a file).

    Studio's installation: ``sys.prefix``, ``sys.exec_prefix``, their base prefixes, :func:`site.getsitepackages`
    and the folder that holds :mod:`tabrion_studio` (``package_root``); read when a module is checked, since
    :func:`site.main` sets the prefixes of a virtual environment. A folder is matched by its identity (device and
    inode) as well as its case-folded path, so a link or another spelling of the project folder is the project. The
    walk from a module's file up to the root meets the project folder or a folder of the installation first; an
    installation folder that holds the project folder (or is it) is met after it, so it never makes project code
    Studio's."""

    def __init__(self, project: str, package_root: str | None) -> None:
        self.project = os.path.realpath(project)
        self.project_id = _identity(self.project)
        self.package_root = package_root
        self.loader: type[_HashedSourceLoader] = _HashedSourceLoader  # refuses every module until the scan is in
        self._prefixes: tuple[str, ...] = ()
        self._installation: tuple[set[tuple[int, int]], set[str]] = (set(), set())

    def installation(self, *, package_root: bool = True) -> tuple[set[tuple[int, int]], set[str]]:
        import site

        prefixes = (sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix)
        if prefixes != self._prefixes:
            folders = [*prefixes, *site.getsitepackages()]
            ids = {ident for ident in map(_identity, folders) if ident is not None}
            self._installation = (ids, {os.path.normcase(os.path.realpath(folder)) for folder in folders})
            self._prefixes = prefixes
        if not package_root or self.package_root is None:
            return self._installation
        ids, names = self._installation
        root = self.package_root
        ident = _identity(root)
        return (ids | ({ident} if ident is not None else set()), names | {os.path.normcase(os.path.realpath(root))})

    def in_project(self, path: str, *, package_root: bool = True) -> bool:
        """Whether ``path`` is project code: in the project folder and not in a folder of Studio's installation."""
        ids, names = self.installation(package_root=package_root)
        project = os.path.normcase(self.project)
        current = os.path.realpath(path)
        while True:
            ident = _identity(current)
            name = os.path.normcase(current)
            if name == project or (ident is not None and ident == self.project_id):
                return True
            if name in names or (ident is not None and ident in ids):
                return False
            parent = os.path.dirname(current)
            if parent == current:
                return False
            current = parent

    def studio_in_project(self) -> bool:
        """Whether Studio runs from a folder inside the project that is not part of its installation (a source
        checkout): then the worker refuses to run, since that folder could not be told apart from the project."""
        return self.package_root is not None and self.in_project(self.package_root, package_root=False)

    def find_spec(
        self, fullname: str, path: Any = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        for finder in list(sys.meta_path):
            find = getattr(finder, "find_spec", None)
            if finder is self or find is None:
                continue
            spec = find(fullname, path, target)
            if spec is not None:
                return self.checked(spec)
        return None

    def checked(self, spec: importlib.machinery.ModuleSpec) -> importlib.machinery.ModuleSpec:
        origin = spec.origin
        if isinstance(origin, str) and os.path.isabs(origin):
            if not self.in_project(origin):
                return spec
            if self.loader.key(origin) in self.loader.modules:
                return importlib.util.spec_from_file_location(
                    spec.name,
                    origin,
                    loader=self.loader(spec.name, origin),
                    submodule_search_locations=spec.submodule_search_locations,
                ) or self.refuse(spec)
            return self.refuse(spec)
        if isinstance(spec.loader, importlib.machinery.NamespaceLoader) or (
            spec.loader is None and origin is None and spec.submodule_search_locations is not None
        ):
            # A namespace package (PathFinder gives it no loader; the import system adds one): it runs no code, and
            # each module in it is checked when it is imported (review N-1).
            return spec
        if any(self.in_project(str(location)) for location in spec.submodule_search_locations or ()):
            return self.refuse(spec)  # a loader without a file that would read from the project
        return spec

    @staticmethod
    def refuse(spec: importlib.machinery.ModuleSpec) -> importlib.machinery.ModuleSpec:
        raise ImportError("not a trusted project module", name=spec.name)


def _source_only(folders: list[str], guard: _ProjectGuard) -> Callable[[str], Any]:
    """A path hook: the project's ``sys_path`` folders and the packages in them get a finder for ``.py`` sources only,
    whose loader (``guard.loader``) runs the hashed bytes and refuses any other module.

    A folder counts as the project's when its path or its real path lies under a root, so a link under a root does
    not lead to the interpreter's finder. Folders of Studio's installation (also a virtual environment inside the
    project) keep the interpreter's finder."""
    roots = [os.path.realpath(folder) for folder in folders]
    hook = importlib.machinery.FileFinder.path_hook((guard.loader, [".py"]))

    def find(path: str) -> Any:
        if not guard.in_project(path) or not any(
            _inside(path, root) or _lexically_inside(path, root) for root in roots
        ):
            raise ImportError("not a project folder")  # the next hook (the interpreter's own) takes it
        return hook(path)

    return find


def _no_bytecode(folders: list[str], modules: Mapping[str, bytes], guard: _ProjectGuard) -> str:
    """No bytecode is read or written from here on, and none from the project's folders; the private, empty cache
    folder to remove afterwards. Every finder loads the project modules from ``modules``, the hashed bytes."""
    cache = tempfile.mkdtemp(prefix="tabrion-studio-worker-")
    sys.dont_write_bytecode = True
    sys.pycache_prefix = os.path.join(cache, "none")  # never created: every later import compiles its source
    guard.loader = _hashed_modules(modules)
    sys.path_hooks.insert(0, _source_only(folders, guard))
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


def _guarded(project: str) -> _ProjectGuard | None:
    """Put the import guard first on ``sys.meta_path``, then add the environment's site folders (``.pth`` files and
    ``sitecustomize`` included) under it; None when Studio runs from a folder inside the project (fail closed)."""
    import site

    guard = _ProjectGuard(project, str(Path(__file__).resolve().parents[2]))
    sys.meta_path.insert(0, guard)
    site.main()
    # A .pth file may have put its finder in front (editable installs of meson-python, scikit-build-core): the guard
    # goes back to the front, so it sees what every finder returns (review C-2).
    sys.meta_path.remove(guard)
    sys.meta_path.insert(0, guard)
    if guard.studio_in_project():  # with the prefixes of a virtual environment, which site.main sets
        return None
    return guard


def _status(request: dict[str, Any]) -> dict[str, Any]:
    guard = _guarded(request["project"])
    if guard is None:
        return {"error": "studio.migrations_failed"}
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
        folders = [str(folder) for folder in found.sys_path if os.path.isdir(folder)]
        cache = _no_bytecode(folders, found.modules, guard)
        alembic_util.load_python_file = _hashed_loader(found.script_location / "versions", found.contents)
        for folder in reversed(folders):
            sys.path.insert(0, folder)
        try:
            if not sys.meta_path or sys.meta_path[0] is not guard:
                return {"error": "studio.migrations_failed"}  # a finder got in front of the guard: fail closed (C-2)
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
