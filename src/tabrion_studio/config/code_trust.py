"""Code trust: whether Studio may run the project's Alembic revision files (STU-015, threat T-16).

``tabrion.migrations.status`` never runs ``env.py``, but Alembic's ``ScriptDirectory`` imports every file in
``<script_location>/versions`` to read the revision graph, and importing a Python file runs it. A project folder is
not trusted by default (AT4, T-08), so that code runs only after the user trusted it, separately from the project's
profiles and bound to exactly these files:

* the project has an ``alembic.ini`` at its root with a ``[alembic]`` ``script_location``; ``%(here)s`` is the
  project folder; the location must lie inside the project, also after links are followed;
* the digest is SHA-256 over ``alembic.ini``, every ``*.py`` file directly in ``<script_location>/versions`` (name
  and bytes, sorted by name), the import roots (the ``prepend_sys_path`` folders that exist inside the project, the
  only folders the worker adds to ``sys.path``) and every project module under them (review finding F-7): each
  ``*.py`` file in a root and in its sub-folders whose names are Python identifiers, by its path relative to the
  project (``/``-separated, sorted) and its bytes. ``__pycache__``, ``node_modules``, ``site-packages``, folders
  whose names are not identifiers (also ``.git``, ``.venv``) and virtual environments (a folder with ``pyvenv.cfg``)
  are skipped, as is bytecode: the worker loads no module from under a root that was not hashed, so a skipped file
  cannot run;
* a link, a file that is not regular, more than :data:`MAX_REVISION_FILES` revision files, more than
  :data:`MAX_MODULE_FILES` modules or :data:`MAX_MODULE_BYTES` bytes of modules, more than :data:`MAX_SCANNED_ENTRIES`
  folder entries under the roots, a module over the configuration size cap, or a link under a root that points
  outside the project makes the state ``invalid`` (fail closed: nothing runs and nothing can be trusted). A link
  under a root that stays inside the project is not followed;
* the record ``<state_dir>/code-trust-<key>.json`` (0600) holds the digest the user confirmed; any change to these
  files (also a new module) makes the state ``changed``, and nothing runs until the user trusts the new digest. A
  record of an earlier digest scheme (:data:`CODE_TRUST_VERSION`) is ``changed`` as well: it needs trust again.

Off by default: without a record, the state is ``untrusted``. The worker runs exactly the bytes it hashed, for the
revision files and for the project modules (:mod:`tabrion_studio.services.migration_worker`). What the digest does
not cover: code that the trusted code reads by other means than an import from the roots (see the threat-model entry
T-16 in ``docs/architecture/migrations.md``).

States: ``none`` (no ``alembic.ini`` or no ``script_location``), ``invalid``, ``untrusted``, ``changed``,
``trusted``. Fail closed: a record that is missing, unreadable, malformed or of another project means not trusted.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Literal

from tabrion_studio.config.files import ConfigFileError, read_plain_file
from tabrion_studio.config.locations import contained
from tabrion_studio.paths import UnsafePathError, read_private_file, remove_file, write_private_file

__all__ = [
    "MAX_MODULE_BYTES",
    "MAX_MODULE_FILES",
    "MAX_REVISION_FILES",
    "MAX_SCANNED_ENTRIES",
    "CodeTrust",
    "CodeTrustError",
    "CodeTrustState",
    "module_key",
    "scan",
    "sys_path_entries",
]

ALEMBIC_INI = "alembic.ini"
#: The digest scheme; a record of another version needs trust again (2: the project modules are covered, F-7).
CODE_TRUST_VERSION = 2
#: More revision files than any project keeps in one folder; a cap keeps the digest's cost bounded.
MAX_REVISION_FILES = 5_000
#: Caps for the project modules under the import roots (fail closed above them): files, their bytes in all, and the
#: folder entries looked at.
MAX_MODULE_FILES = 5_000
MAX_MODULE_BYTES = 32 * 1024 * 1024
MAX_SCANNED_ENTRIES = 50_000
_SKIPPED_FOLDERS = frozenset({"__pycache__", "node_modules", "site-packages"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION_NAME = re.compile(r"[A-Za-z0-9_.-]{1,200}\.py")

CodeState = Literal["none", "untrusted", "changed", "trusted", "invalid"]


class CodeTrustError(Exception):
    """Trusting failed; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Scan:
    """What the digest covers: the script location (absolute, inside the project), the revision file names, the
    import roots (the ``prepend_sys_path`` folders of ``alembic.ini`` that exist inside the project; others are left
    out) and the project modules under them (paths relative to the project, ``/``-separated)."""

    digest: str
    script_location: Path
    revision_files: tuple[str, ...]
    sys_path: tuple[Path, ...]
    module_files: tuple[str, ...] = ()
    #: The bytes that were hashed, by file name; only with ``scan(..., keep=True)`` (the worker runs these bytes).
    contents: dict[str, bytes] = field(default_factory=dict, repr=False, compare=False)
    #: The modules' hashed bytes by :func:`module_key` of their path; only with ``keep=True``.
    modules: dict[str, bytes] = field(default_factory=dict, repr=False, compare=False)


@dataclass
class CodeTrustState:
    state: CodeState
    scan: Scan | None = None
    issue: str | None = None

    def to_dict(self, project: Path) -> dict[str, Any]:
        location = None
        if self.scan is not None:
            location = _relative(self.scan.script_location, project)
        return {
            "state": self.state,
            "trusted": self.state == "trusted",
            "format": None if self.state == "none" else "alembic",
            "config_file": None if self.state == "none" else ALEMBIC_INI,
            "digest": None if self.scan is None else self.scan.digest,
            "script_location": location,
            "revision_files": [] if self.scan is None else list(self.scan.revision_files),
            "import_roots": [] if self.scan is None else [_relative(path, project) for path in self.scan.sys_path],
            "module_files": [] if self.scan is None else list(self.scan.module_files),
            "issue": self.issue,
        }


def _relative(path: str | os.PathLike[str], project: str | os.PathLike[str], flavor: Any = os.path) -> str:
    """``path`` relative to ``project``, ``/``-separated on every platform (the names the digest hashes)."""
    return str(flavor.relpath(path, project)).replace(flavor.sep, "/")


def module_key(path: str | os.PathLike[str], flavor: Any = os.path) -> str:
    """The key of a module file in ``Scan.modules``: its real path, case-folded where the file system ignores case
    (Windows)."""
    return str(flavor.normcase(flavor.realpath(path)))


class _Invalid(Exception):
    def __init__(self, issue: str) -> None:
        super().__init__(issue)
        self.issue = issue


def _ini_values(data: bytes, project: Path) -> tuple[str, list[str]] | None:
    """``script_location`` and the ``prepend_sys_path`` entries of ``alembic.ini``; None without a location."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise _Invalid("migrations.config_invalid") from exc
    if not parser.has_option("alembic", "script_location"):
        return None
    here = str(project)

    def value(name: str) -> str:
        return parser.get("alembic", name, fallback="").replace("%(here)s", here).strip()

    location = value("script_location")
    if not location:
        return None
    return location, sys_path_entries(value("prepend_sys_path"))


def sys_path_entries(text: str, *, windows: bool = os.name == "nt") -> list[str]:
    """The folders of ``prepend_sys_path``: Alembic splits it on the path separator, on spaces or on commas, as
    ``path_separator`` says; the common forms are "." and "src". On Windows, a drive (``C:\\`` or ``C:/``) starts a
    folder instead of ending one (``%(here)s/src`` is ``C:\\project/src`` there)."""
    return re.findall(r"(?:[A-Za-z]:[\\/])?[^\s,:;]+" if windows else r"[^\s,:;]+", text)


def _inside(text: str, project: Path) -> Path | None:
    """The real path of ``text`` (relative to the project), if it stays inside the project; else None."""
    root = Path(os.path.abspath(project))
    real_root = root.resolve()
    if not contained(text, root, [root], [real_root]):
        return None
    return Path(os.path.realpath(os.path.join(root, text)))


def scan(project: Path, *, keep: bool = False) -> Scan | None:
    """The digest and what it covers, or None without ``alembic.ini``/``script_location``; raises ``_Invalid``.

    With ``keep``, ``Scan.contents`` holds the revision files' bytes as they were hashed."""
    try:
        ini = read_plain_file(project / ALEMBIC_INI)
    except ConfigFileError as exc:
        raise _Invalid(exc.code) from exc
    if ini is None:
        return None
    values = _ini_values(ini, project)
    if values is None:
        return None
    location_text, extra = values
    location = _inside(location_text, project)
    versions = None if location is None else location / "versions"
    if location is None or versions is None:
        raise _Invalid("migrations.location_outside")
    try:
        info = os.lstat(versions)
    except OSError as exc:
        raise _Invalid("migrations.location_missing") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise _Invalid("migrations.location_missing")
    names = sorted(entry.name for entry in os.scandir(versions) if entry.name.endswith(".py"))
    if len(names) > MAX_REVISION_FILES:
        raise _Invalid("migrations.too_many_files")
    digest = hashlib.sha256(b"tabrion-studio code trust %d\0" % CODE_TRUST_VERSION)
    digest.update(len(ini).to_bytes(8, "big") + ini)
    contents: dict[str, bytes] = {}
    for name in names:
        if _REVISION_NAME.fullmatch(name) is None:
            raise _Invalid("migrations.file_name")
        try:
            data = read_plain_file(versions / name)
        except ConfigFileError as exc:
            raise _Invalid(exc.code) from exc
        if data is None:
            raise _Invalid("migrations.changed")
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big") + encoded + len(data).to_bytes(8, "big") + data)
        if keep:
            contents[name] = data
    roots: list[Path] = []
    for path in (_inside(part, project) for part in extra):
        if path is not None and path not in roots and os.path.isdir(path):
            roots.append(path)
    real_project = Path(os.path.realpath(project))
    found = _modules(real_project, roots)
    digest.update(b"roots\0" + len(roots).to_bytes(8, "big"))
    for root in roots:
        _field(digest, _relative(root, real_project).encode("utf-8"))
    digest.update(b"modules\0" + len(found).to_bytes(8, "big"))
    modules: dict[str, bytes] = {}
    for name in sorted(found):
        path, data = found[name]
        _field(digest, name.encode("utf-8"))
        _field(digest, data)
        if keep:
            modules[module_key(path)] = data
    return Scan(digest.hexdigest(), location, tuple(names), tuple(roots), tuple(sorted(found)), contents, modules)


def _field(digest: Any, data: bytes) -> None:
    digest.update(len(data).to_bytes(8, "big") + data)


def _linked(path: str) -> bool:
    """Whether ``path`` (in a folder that is its real path) is a link of any kind (also a Windows junction)."""
    return os.path.normcase(os.path.realpath(path)) != os.path.normcase(path)


def _modules(real_project: Path, roots: list[Path]) -> dict[str, tuple[Path, bytes]]:
    """The project modules under ``roots`` (real paths): ``{path relative to the project: (path, bytes)}``; raises
    ``_Invalid``.

    Links are never followed: one that points outside the project makes the state invalid, one inside is skipped
    (the worker does not load what was not hashed)."""
    found: dict[str, tuple[Path, bytes]] = {}
    entries = 0
    total = 0
    for root in roots:
        pending = [root]
        while pending:
            folder = pending.pop()
            try:
                with os.scandir(folder) as scanned:
                    listing = sorted(scanned, key=lambda entry: entry.name)
            except OSError as exc:
                raise _Invalid("migrations.unreadable") from exc
            entries += len(listing)
            if entries > MAX_SCANNED_ENTRIES:
                raise _Invalid("migrations.too_many_modules")
            if folder != root and any(entry.name == "pyvenv.cfg" for entry in listing):
                continue  # a virtual environment: its packages are imported from its site-packages, not from here
            for entry in listing:
                is_module = entry.name.endswith(".py")
                if not is_module and (not entry.name.isidentifier() or entry.name in _SKIPPED_FOLDERS):
                    continue
                if _linked(entry.path):
                    if (is_module or entry.is_dir()) and not Path(os.path.realpath(entry.path)).is_relative_to(
                        real_project
                    ):
                        raise _Invalid("migrations.link_outside")
                    continue
                if not is_module:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    continue
                path = Path(entry.path)
                name = _relative(path, real_project)
                if name in found:
                    continue  # also under an earlier root
                if len(found) >= MAX_MODULE_FILES:
                    raise _Invalid("migrations.too_many_modules")
                try:
                    data = read_plain_file(path)
                except ConfigFileError as exc:
                    raise _Invalid(
                        "migrations.modules_too_large" if exc.code == "config.too_large" else exc.code
                    ) from exc
                if data is None:
                    raise _Invalid("migrations.changed")
                total += len(data)
                if total > MAX_MODULE_BYTES:
                    raise _Invalid("migrations.modules_too_large")
                found[name] = (path, data)
    return found


@dataclass
class CodeTrust:
    """The project's Alembic code and its trust record."""

    project: Path
    state_dir: Path
    key: str
    record: Path = field(init=False)

    def __post_init__(self) -> None:
        self.record = self.state_dir / f"code-trust-{self.key}.json"

    def load(self) -> CodeTrustState:
        """The trust state now; never raises for what the files or the record contain."""
        try:
            found = scan(self.project)
        except _Invalid as exc:
            return CodeTrustState("invalid", issue=exc.issue)
        except (OSError, RecursionError, ValueError):
            return CodeTrustState("invalid", issue="migrations.unreadable")
        if found is None:
            return CodeTrustState("none")
        record = self._read_record()
        if record is None:
            return CodeTrustState("untrusted", found)
        if record["version"] != CODE_TRUST_VERSION or record["digest"] != found.digest:
            return CodeTrustState("changed", found)
        return CodeTrustState("trusted", found)

    def _read_record(self) -> dict[str, Any] | None:
        try:
            raw = read_private_file(self.record)
            if raw is None:
                return None
            doc = json.loads(raw)
        except (OSError, UnsafePathError, ValueError, RecursionError):
            return None
        if (
            not isinstance(doc, dict)
            or type(doc.get("version")) is not int
            or not 1 <= doc["version"] <= CODE_TRUST_VERSION
            or doc.get("project") != str(self.project)
            or not isinstance(doc.get("digest"), str)
            or _DIGEST.fullmatch(doc["digest"]) is None
        ):
            return None
        return doc

    def trust(self, digest: object) -> None:
        """Trust the code if it is still the one with ``digest`` (the one the dialog showed)."""
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise CodeTrustError("studio.bad_request")
        current = self.load()
        if current.state == "none":
            raise CodeTrustError("studio.no_migrations")
        if current.state == "invalid" or current.scan is None:
            raise CodeTrustError("studio.migrations_invalid")
        if current.scan.digest != digest:
            raise CodeTrustError("studio.trust_stale")
        record = {
            "version": CODE_TRUST_VERSION,
            "project": str(self.project),
            "digest": digest,
            "trusted_at": time.time(),
        }
        try:
            write_private_file(self.record, json.dumps(record).encode("utf-8"))
        except OSError as exc:
            raise CodeTrustError("studio.trust_unavailable") from exc

    def revoke(self) -> None:
        """Forget the trust (the state is ``untrusted`` again)."""
        try:
            remove_file(self.record)
        except OSError as exc:
            raise CodeTrustError("studio.trust_unavailable") from exc
