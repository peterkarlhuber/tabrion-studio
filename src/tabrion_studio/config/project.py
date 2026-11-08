"""Project profiles and project trust (03 sections 4.3 and 7.3 T-08, STU-006).

``<project>/.tabrion/studio.toml`` is checked into a repository, so Studio does not trust it (AT4). Its profiles are
used only after the user confirmed exactly these bytes in the trust dialog D-01. The confirmation is stored as the
SHA-256 digest of the file, plus a snapshot of what the dialog showed, in ``<state_dir>/trust-<key>.json`` (0600).

**Fail closed.** The project profiles stay unused when the file is missing, unreadable, a link, larger than 256 KB,
not UTF-8, not valid TOML or not valid schema version 1; when a ``file:`` secret reference points outside the
project folder and the user's configuration folder; when the trust record is missing, unreadable, malformed, of
another project or of other bytes; and when the file changed between the dialog and the confirmation (the client
sends the digest it showed, and it must still match the file on disk).

What the dialog shows (:class:`Review`): every connection, every host, every file reference (database files and
``file:`` secrets, marked when outside the project), every ``env:`` variable a password would be read from (an
attacker's file could name ``AWS_SECRET_ACCESS_KEY`` and send it to its own host) and every unknown key. When a
trusted file changed, it also shows the difference to what was trusted (T-08, review U-1d).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePath
import re
import time
from typing import Any, Literal

from tabrion_studio.config.files import ConfigFileError, parse_toml, read_plain_file
from tabrion_studio.config.schema import Issue, Profile, ProfileFile, validate_profiles
from tabrion_studio.paths import UnsafePathError, is_within, read_private_file, write_private_file

PROJECT_DIR = ".tabrion"
PROJECT_FILE = "studio.toml"
TRUST_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}")

TrustState = Literal["none", "untrusted", "changed", "trusted", "invalid"]


def _inside(path: Path, roots: list[Path]) -> bool:
    resolved = Path(os.path.abspath(path))
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        return False
    return is_within(resolved, roots) and is_within(real, roots)


def _roots(*folders: Path | None) -> list[Path]:
    roots: list[Path] = []
    for folder in folders:
        if folder is not None:
            roots += [Path(os.path.abspath(folder))]
            with contextlib.suppress(OSError, RuntimeError):
                roots.append(folder.resolve())
    return roots


@dataclass
class Review:
    """What D-01 shows: one entry per item, never joined (review U-1a)."""

    connections: list[dict[str, Any]] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connections": self.connections,
            "hosts": self.hosts,
            "files": self.files,
            "env": self.env,
            "unknown_keys": self.unknown_keys,
        }


def _add(items: list[Any], value: Any) -> None:
    if value not in items:
        items.append(value)


def review_of(parsed: ProfileFile, project: Path) -> Review:
    review = Review(unknown_keys=list(parsed.unknown_keys))
    project_roots = _roots(project)
    for profile in parsed.profiles:
        location = profile.path if profile.engine == "sqlite" else f"{profile.host}:{profile.port or 5432}"
        review.connections.append(
            {
                "name": profile.name,
                "engine": profile.engine,
                "environment": profile.environment,
                "location": location,
                # Every field (secret references only), so that any change shows up as "changed" (U-1d).
                "profile": profile.to_dict(),
            }
        )
        if profile.host is not None:
            _add(review.hosts, f"{profile.host}:{profile.port or 5432}")
        if profile.path is not None:
            outside = not _inside(project / profile.path, project_roots)
            _add(review.files, {"path": profile.path, "kind": "database", "outside": outside})
        if profile.password is not None and profile.password.source == "file":
            target = profile.password.target
            _add(
                review.files,
                {"path": target, "kind": "secret", "outside": not _inside(project / target, project_roots)},
            )
        if profile.password is not None and profile.password.source == "env":
            _add(review.env, profile.password.target)
    return review


def diff_reviews(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Added, removed and changed connections; added and removed hosts, files, variables and unknown keys."""
    before = {c["name"]: c for c in old.get("connections", []) if isinstance(c, dict) and "name" in c}
    after = {c["name"]: c for c in new.get("connections", [])}
    result: dict[str, Any] = {
        "connections": {
            "added": [name for name in after if name not in before],
            "removed": [name for name in before if name not in after],
            "changed": [name for name in after if name in before and before[name] != after[name]],
        }
    }
    for key in ("hosts", "files", "env", "unknown_keys"):
        was = [item for item in old.get(key, []) if isinstance(item, str | dict)]
        now = new.get(key, [])
        result[key] = {"added": [i for i in now if i not in was], "removed": [i for i in was if i not in now]}
    return result


@dataclass
class ProjectState:
    state: TrustState
    digest: str | None = None
    review: Review | None = None
    diff: dict[str, Any] | None = None
    issues: list[Issue] = field(default_factory=list)
    profiles: list[Profile] = field(default_factory=list)

    def to_dict(self, project: Path) -> dict[str, Any]:
        return {
            "path": str(project),
            "profiles_file": f"{PROJECT_DIR}/{PROJECT_FILE}",
            "state": self.state,
            "trusted": self.state == "trusted",
            "digest": self.digest,
            "review": None if self.review is None else self.review.to_dict(),
            "diff": self.diff,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class TrustError(Exception):
    """Trusting failed; ``code`` is a problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProjectConfig:
    """The project folder's profile file and its trust record."""

    def __init__(self, project: Path, state_dir: Path, key: str, config_dir: Path | None = None) -> None:
        self.project = project
        self.file = project / PROJECT_DIR / PROJECT_FILE
        self.record = state_dir / f"trust-{key}.json"
        self.config_dir = config_dir

    def load(self) -> ProjectState:
        try:
            data = read_plain_file(self.file, folders=(self.project / PROJECT_DIR,))
            if data is None:
                return ProjectState("none")
            doc = parse_toml(data)
        except ConfigFileError as exc:
            pointer = "file" if exc.line is None else f"line {exc.line}"
            return ProjectState("invalid", issues=[Issue(pointer, exc.code)])
        digest = hashlib.sha256(data).hexdigest()
        parsed = validate_profiles(doc)
        self._check_secret_files(parsed)
        if not parsed.ok:
            return ProjectState("invalid", digest=digest, issues=parsed.issues)
        review = review_of(parsed, self.project)
        record = self._read_record()
        if record is not None and record["digest"] == digest:
            return ProjectState("trusted", digest, review, issues=parsed.issues, profiles=parsed.profiles)
        if record is not None:
            changes = diff_reviews(record["review"], review.to_dict())
            return ProjectState("changed", digest, review, changes, parsed.issues)
        return ProjectState("untrusted", digest, review, issues=parsed.issues)

    def _check_secret_files(self, parsed: ProfileFile) -> None:
        """T-08: a ``file:`` reference only inside the project or the user's configuration folder (else invalid)."""
        roots = _roots(self.project, self.config_dir)
        for index, profile in enumerate(parsed.profiles):
            ref = profile.password
            if ref is None or ref.source != "file":
                continue
            target = PurePath(ref.target)
            path = Path(target) if target.is_absolute() else self.project / target
            if not _inside(path, roots):
                parsed.issues.append(Issue(f"profile[{index}].password", "config.file_outside"))

    def _read_record(self) -> dict[str, Any] | None:
        """The trust record, or None if it is missing or not exactly what Studio writes (fail closed)."""
        try:
            raw = read_private_file(self.record)
            if raw is None:
                return None
            doc = json.loads(raw)
        except (OSError, UnsafePathError, ValueError):
            return None
        if (
            not isinstance(doc, dict)
            or doc.get("version") != TRUST_VERSION
            or doc.get("project") != str(self.project)
            or not isinstance(doc.get("digest"), str)
            or _DIGEST.fullmatch(doc["digest"]) is None
            or not isinstance(doc.get("review"), dict)
        ):
            return None
        return doc

    def trust(self, digest: object) -> None:
        """Trust the file if it is still the one with ``digest`` (the one the dialog showed)."""
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise TrustError("studio.bad_request")
        current = self.load()
        if current.state == "none":
            raise TrustError("studio.no_project_file")
        if current.state == "invalid" or current.review is None:
            raise TrustError("studio.project_invalid")
        if current.digest != digest:
            raise TrustError("studio.trust_stale")
        record = {
            "version": TRUST_VERSION,
            "project": str(self.project),
            "digest": digest,
            "review": current.review.to_dict(),
            "trusted_at": time.time(),
        }
        try:
            write_private_file(self.record, json.dumps(record).encode("utf-8"))
        except OSError as exc:
            raise TrustError("studio.trust_unavailable") from exc
