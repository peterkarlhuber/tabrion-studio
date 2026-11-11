"""Code trust: whether Studio may run the project's Alembic revision files (STU-015, threat T-16).

``tabrion.migrations.status`` never runs ``env.py``, but Alembic's ``ScriptDirectory`` imports every file in
``<script_location>/versions`` to read the revision graph, and importing a Python file runs it. A project folder is
not trusted by default (AT4, T-08), so that code runs only after the user trusted it, separately from the project's
profiles and bound to exactly these files:

* the project has an ``alembic.ini`` at its root with a ``[alembic]`` ``script_location``; ``%(here)s`` is the
  project folder; the location must lie inside the project, also after links are followed;
* the digest is SHA-256 over ``alembic.ini`` and every ``*.py`` file directly in ``<script_location>/versions``
  (name and bytes, sorted by name); a link, a file that is not regular, more than :data:`MAX_REVISION_FILES` files or a
  file over the configuration size cap makes the state ``invalid``;
* the record ``<state_dir>/code-trust-<key>.json`` (0600) holds the digest the user confirmed; any change to these
  files makes the state ``changed``, and nothing runs until the user trusts the new digest.

Off by default: without a record, the state is ``untrusted``. The digest covers the files Alembic imports itself; a
revision file can import other modules of the project, which the digest does not cover (the trust is for the
project's code, see the threat-model entry in ``docs/architecture/migrations.md``).

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

__all__ = ["MAX_REVISION_FILES", "CodeTrust", "CodeTrustError", "CodeTrustState", "scan"]

ALEMBIC_INI = "alembic.ini"
CODE_TRUST_VERSION = 1
#: More revision files than any project keeps in one folder; a cap keeps the digest's cost bounded.
MAX_REVISION_FILES = 5_000
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
    """What the digest covers: the script location (absolute, inside the project), the revision file names and the
    ``prepend_sys_path`` folders of ``alembic.ini`` (inside the project; others are left out)."""

    digest: str
    script_location: Path
    revision_files: tuple[str, ...]
    sys_path: tuple[Path, ...]


@dataclass
class CodeTrustState:
    state: CodeState
    scan: Scan | None = None
    issue: str | None = None

    def to_dict(self, project: Path) -> dict[str, Any]:
        location = None
        if self.scan is not None:
            location = os.path.relpath(self.scan.script_location, project).replace(os.sep, "/")
        return {
            "state": self.state,
            "trusted": self.state == "trusted",
            "format": None if self.state == "none" else "alembic",
            "config_file": None if self.state == "none" else ALEMBIC_INI,
            "digest": None if self.scan is None else self.scan.digest,
            "script_location": location,
            "revision_files": [] if self.scan is None else list(self.scan.revision_files),
            "issue": self.issue,
        }


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
    # Alembic splits prepend_sys_path on the path separator, on spaces or on commas, as path_separator says; the
    # common forms are "." and "src".
    extra = [part for part in re.split(r"[\s,:;]+", value("prepend_sys_path")) if part]
    return location, extra


def _inside(text: str, project: Path) -> Path | None:
    """The real path of ``text`` (relative to the project), if it stays inside the project; else None."""
    root = Path(os.path.abspath(project))
    real_root = root.resolve()
    if not contained(text, root, [root], [real_root]):
        return None
    return Path(os.path.realpath(os.path.join(root, text)))


def scan(project: Path) -> Scan | None:
    """The digest and what it covers, or None without ``alembic.ini``/``script_location``; raises ``_Invalid``."""
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
    digest = hashlib.sha256(b"tabrion-studio code trust 1\0")
    digest.update(len(ini).to_bytes(8, "big") + ini)
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
    sys_path = tuple(path for path in (_inside(part, project) for part in extra) if path is not None)
    return Scan(digest.hexdigest(), location, tuple(names), sys_path)


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
        if record["digest"] != found.digest:
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
            or doc.get("version") != CODE_TRUST_VERSION
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
