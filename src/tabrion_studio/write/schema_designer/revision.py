"""Writing a Schema Designer plan as an Alembic revision into the trusted project (STU-XSD-3; STU-ADR-0007 section 5,
E-X4; TX-19, TX-20; GS3-06..08, GS3-16 j 5, GS3-19, GS3-27).

Imported only by :mod:`.designer`, which :mod:`tabrion_studio.write.routes` imports after it checked the
``schema_designer`` flag (GS3-01).

**The flow.** The preview (:func:`.designer.preview`) keeps the plan with the draft and says whether a revision may be
written (:func:`availability`, without running anything). :func:`.designer.revision` then takes the kept plan out of
the draft (one attempt, as a run), checks its binding and expiry, the unlock and the conditions below again, plans
**again** from a fresh snapshot and compares the new hash with the recorded one (``studio.plan_drift``), reads the
single head through the migration status (:class:`tabrion_studio.services.migrations.Migrations`, which needs the
project's code trust and reads the revision graph in its separate worker process, the 1.0 rule), and gets the text
from ``tabrion.plan.revision_text`` (a string; tabrion reads no project and writes no file). Studio checks that the
text parses (``ast.parse``), records ``run.start``, creates the file and records ``run.result``.

**Conditions** (section 5, :func:`availability`): the stored environment of the unlock is ``dev`` or ``test`` (never
``prod``: an open decision of G-S3; PROD is never unlocked anyway, E-X7) and no PROD profile of the user's store names
the unlocked database (:func:`names_prod`, the Database Manager's matching; review I-1 of #144); the project's profiles
are trusted (D-01); the project has an Alembic setup (``alembic.ini`` with a ``script_location``) whose code the user
trusted (the digest of :mod:`tabrion_studio.config.code_trust`); the plan has a step. The revision output does not need
a direct run to be allowed: it is the only output for an Alembic project.

**Never run** (GS3-06). Studio never runs ``env.py``, never imports a project module or the revision it writes, runs no
``alembic`` command and opens no ``MigrationContext``. The only project code that runs is the 1.0 migration worker's
read of the revision graph for the head, under code trust, in its own process (T-16).

**The file** (GS3-07, :func:`write_file`). Only into ``<script_location>/versions`` of the trusted scan, which must
be a folder inside the project, reached without a link at any level (each part is checked with ``lstat``, and the
real path must be the lexical one). The name is ``<revision>_<slug>.py``: the revision id is 12 random hex digits made
here, the slug at most 40 of ``[a-z0-9_]`` made from the message (:func:`slug`); no path part, no ``..``. The file is
created exclusively (``O_CREAT | O_EXCL``, ``O_NOFOLLOW``, on POSIX relative to the folder opened with
``O_NOFOLLOW``), never overwritten, with the mode the user's umask gives a new file (the project's normal mode). The
page shows its path relative to the project. A new revision file changes the code-trust digest: the migration status
asks for trust again before it reads the revisions (the answer says so).

**The text** (GS3-08). tabrion writes the revision id, the head, the message, the plan hash, the fingerprints and
every statement as Python string literals; Studio refuses a message with a control character or longer than
:data:`MAX_MESSAGE` characters and checks that the whole text parses before it writes it. The revision does not check
its source fingerprint when it runs (I-124 j 5): the preview says so and shows the source fingerprint.

**Never a value** (section 11, TX-27). The audit records carry the plan hash (its hex part), the qualified table
names, the risk classes and the step count, action ``schema_designer.revision``; the log fixed event names and problem
codes. The revision holds no connection data and no secret (GS3-19); its path is in the answer to the user only.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from tabrion.errors import UsageError
from tabrion.plan import revision_text

from tabrion_studio.config.write_areas import Area
from tabrion_studio.write import prod_targets
from tabrion_studio.write.audit import AuditError
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.unlock import Unlock, Unlocks

__all__ = [
    "ACTION_REVISION",
    "MAX_MESSAGE",
    "REVISION_ENVIRONMENTS",
    "RevisionSources",
    "availability",
    "check_message",
    "file_name",
    "head_of",
    "revision_folder",
    "slug",
    "write_file",
]

logger = logging.getLogger("tabrion_studio.write")

ACTION_REVISION = "schema_designer.revision"
#: The stored environments a revision may be written for (section 5): never PROD (an open decision of G-S3).
REVISION_ENVIRONMENTS = frozenset({"dev", "test"})
#: The longest message (characters); it becomes the revision's docstring literal and, as a slug, part of the name.
MAX_MESSAGE = 200
MAX_SLUG = 40
#: The name of every file Studio writes (GS3-07).
FILE_NAME = re.compile(r"[0-9a-f]{12}_[a-z0-9_]{1,40}\.py")
_REVISION_ID = re.compile(r"[0-9a-f]{12}")
#: Alembic's revision ids as ``revision_text`` takes them; another head is not used (``schema_revision_head_unknown``).
_HEAD = re.compile(r"[0-9a-zA-Z_]{1,64}")
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)
#: Relative creation through an opened folder (POSIX); a module constant so that the tests run both ways.
_DIR_FD = os.name == "posix" and os.open in os.supports_dir_fd
_HASH_PREFIX = "sha256:"


@dataclass(frozen=True)
class RevisionSources:
    """What the revision output reads besides the draft: the project's profile trust (D-01) and its migration status
    (code trust and the worker that reads the head). Either missing refuses the revision output (fail closed)."""

    project: Path | None
    project_trust: Any = None  # tabrion_studio.config.project.ProjectConfig
    migrations: Any = None  # tabrion_studio.services.migrations.Migrations

    def code_state(self) -> Any:
        """The code-trust state of the project (:class:`tabrion_studio.config.code_trust.CodeTrustState`), or None."""
        if self.migrations is None or self.project is None or Path(self.migrations.code.project) != self.project:
            return None
        return self.migrations.code.load()

    def profiles_trusted(self) -> bool:
        if self.project_trust is None or self.project is None or Path(self.project_trust.project) != self.project:
            return False
        return bool(self.project_trust.load().state == "trusted")


def names_prod(unlocks: Unlocks, unlock: Unlock) -> bool:
    """Whether a PROD profile of the user's store names the unlocked database (review I-1 of #144: a DEV profile may
    be an alias of a PROD server), with the matching of the Database Manager (:mod:`tabrion_studio.write.prod_targets`:
    the SQLite file by device and inode; on PostgreSQL the database name on the same server by ``system_identifier``
    or by port and resolved addresses)."""
    profiles = unlocks.config.user.profiles().profiles
    read = next((p for p in profiles if p.name == unlock.read_name and p.engine == unlock.connection.engine), None)
    return prod_targets.names_prod(unlock.connection, read, profiles, unlocks.registry.list())


def availability(unlock: Unlock, change: Any, sources: RevisionSources, *, prod_alias: bool = False) -> tuple[str, ...]:
    """Why no revision may be written for this plan (section 5); empty when one may. Runs no project code: the head
    is read only when the revision is written. ``prod_alias``: a PROD profile names the database (:func:`names_prod`),
    which refuses like a PROD environment."""
    reasons: list[str] = []
    if not change.steps:
        reasons.append("nothing_to_run")
    if unlock.environment not in REVISION_ENVIRONMENTS or prod_alias:
        reasons.append("environment")
    if not sources.profiles_trusted():
        reasons.append("project_untrusted")
    code = sources.code_state()
    if code is None or code.state == "none":
        reasons.append("no_alembic_project")
    elif code.state != "trusted" or code.scan is None:
        reasons.append("code_untrusted")
    return tuple(reasons)


def check_message(raw: object) -> str:
    """The revision message: a text of 1 to :data:`MAX_MESSAGE` characters without a control character (a line break
    included), not only blanks. Quotes and backslashes are kept: tabrion writes the message as a literal."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_MESSAGE or _CONTROL.search(raw):
        raise WriteError("studio.schema_revision_invalid")
    return raw


def slug(message: str) -> str:
    """The name part of the message: lower case, runs of anything but ``[a-z0-9]`` as one ``_``, at most
    :data:`MAX_SLUG` characters; ``revision`` when nothing is left."""
    text = re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")[:MAX_SLUG].strip("_")
    return text or "revision"


def file_name(revision: str, message: str) -> str:
    """``<revision>_<slug>.py``, checked against :data:`FILE_NAME`."""
    name = f"{revision}_{slug(message)}.py"
    if not _REVISION_ID.fullmatch(revision) or not FILE_NAME.fullmatch(name):  # pragma: no cover - built above
        raise WriteError("studio.schema_revision_failed")
    return name


def head_of(status: Mapping[str, Any]) -> str:
    """The single head of the project's Alembic revisions, as the migration status read it (code trust, the worker);
    several heads, none, one that could not be read or one that is not an Alembic revision id refuse
    (``studio.schema_revision_head_unknown``)."""
    formats = status.get("formats")
    found = (
        [item for item in formats if isinstance(item, dict) and item.get("format") == "alembic"]
        if isinstance(formats, list)
        else []
    )
    if len(found) != 1:
        raise WriteError("studio.schema_revision_head_unknown")
    alembic = found[0]
    heads = alembic.get("heads")
    if (
        alembic.get("code_trust") != "trusted"
        or alembic.get("revisions") != "read"
        or not isinstance(heads, list)
        or len(heads) != 1
        or not isinstance(heads[0], str)
        or not _HEAD.fullmatch(heads[0])
    ):
        raise WriteError("studio.schema_revision_head_unknown")
    return heads[0]


def _folder_parts(project: Path, folder: Path) -> list[Path]:
    """Every path from the project folder down to ``folder`` (both included)."""
    relative = folder.relative_to(project)
    parts = [project]
    for name in relative.parts:
        if name in ("", ".", ".."):
            raise WriteError("studio.schema_revision_folder")
        parts.append(parts[-1] / name)
    return parts


def revision_folder(project: Path, script_location: Path) -> tuple[Path, os.stat_result]:
    """``<script_location>/versions``, checked (GS3-07): inside the project, every level from the project folder down
    a real folder and no link, and its real path the lexical one. Returns the folder and its ``lstat``."""
    project = Path(os.path.abspath(project))
    folder = Path(os.path.abspath(Path(script_location) / "versions"))
    try:
        parts = _folder_parts(project, folder)
    except ValueError:
        raise WriteError("studio.schema_revision_folder") from None
    found: os.stat_result | None = None
    for part in parts:
        try:
            found = os.lstat(part)
        except OSError:
            raise WriteError("studio.schema_revision_folder") from None
        if stat.S_ISLNK(found.st_mode) or not stat.S_ISDIR(found.st_mode):
            raise WriteError("studio.schema_revision_folder")
    if found is None or os.path.normcase(os.path.realpath(folder)) != os.path.normcase(str(folder)):
        raise WriteError("studio.schema_revision_folder")
    return folder, found


def write_file(folder: Path, expected: os.stat_result, name: str, text: str) -> Path:
    """Create ``folder/name`` exclusively with ``text`` (never overwrite, never through a link). On POSIX the folder
    is opened with ``O_NOFOLLOW`` and must still be the checked one (device and inode), and the file is created
    relative to it."""
    if not FILE_NAME.fullmatch(name):  # pragma: no cover - file_name made it
        raise WriteError("studio.schema_revision_failed")
    data = text.encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY
    folder_fd: int | None = None
    try:
        if _DIR_FD:
            try:
                folder_fd = os.open(folder, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
            except OSError:  # a link (ELOOP), not a folder any more, or gone
                raise WriteError("studio.schema_revision_folder") from None
            opened = os.fstat(folder_fd)
            if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino) or not stat.S_ISDIR(opened.st_mode):
                raise WriteError("studio.schema_revision_folder")
            fd = os.open(name, flags, 0o666, dir_fd=folder_fd)
        else:
            now = os.lstat(folder)
            if stat.S_ISLNK(now.st_mode) or (now.st_dev, now.st_ino) != (expected.st_dev, expected.st_ino):
                raise WriteError("studio.schema_revision_folder")
            fd = os.open(folder / name, flags, 0o666)
    except FileExistsError:
        raise WriteError("studio.schema_revision_exists") from None
    except WriteError:
        raise
    except OSError:
        raise WriteError("studio.schema_revision_failed") from None
    finally:
        if folder_fd is not None:
            os.close(folder_fd)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    except OSError:
        raise WriteError("studio.schema_revision_failed") from None
    finally:
        os.close(fd)
    return folder / name


def make_text(change: Any, *, revision: str, down_revision: str, message: str) -> str:
    """``tabrion.plan.revision_text``, checked: a refused id is ``schema_revision_head_unknown``, and the text must
    parse as Python (GS3-08) and be ASCII."""
    try:
        text = revision_text(change, revision=revision, down_revision=down_revision, message=message)
    except UsageError:
        raise WriteError("studio.schema_revision_head_unknown") from None
    try:
        ast.parse(text)
        text.encode("ascii")
    except (SyntaxError, ValueError, UnicodeEncodeError):
        raise WriteError("studio.schema_revision_failed") from None
    return text


def _audit_hash(plan_hash: str) -> str:
    return plan_hash[len(_HASH_PREFIX) :] if plan_hash.startswith(_HASH_PREFIX) else plan_hash


@dataclass
class Written:
    path: Path
    relative: str
    revision: str
    down_revision: str
    audit_pending: bool


def write(
    unlocks: Unlocks,
    unlock: Unlock,
    *,
    change: Any,
    tables: list[str],
    project: Path,
    script_location: Path,
    head: str,
    message: str,
) -> Written:
    """Record ``run.start``, write the revision file and record ``run.result`` (the caller checked the binding, the
    conditions, the re-plan and the head)."""
    folder, found = revision_folder(project, script_location)
    revision = secrets.token_hex(6)
    name = file_name(revision, message)
    text = make_text(change, revision=revision, down_revision=head, message=message)
    common: dict[str, object] = {
        "run": secrets.token_hex(8),
        "area": Area.SCHEMA_DESIGNER.value,
        "action": ACTION_REVISION,
        "profile": unlock.write_name,
    }
    try:
        unlocks.audit.append(
            "run.start",
            **common,
            kind=unlock.kind,
            environment=unlock.environment,
            engine=unlock.connection.engine,
            connection=unlock.connection_id,
            objects=tables,
            plan_hash=_audit_hash(change.plan_hash),
            risks=sorted(risk.value for risk in change.risks),
            steps=len(change.steps),
        )
    except AuditError:
        logger.warning("revision not written: no audit record", extra={"event": "studio.audit_unavailable"})
        raise WriteError("studio.audit_unavailable") from None
    except ValueError:
        logger.warning("revision not written: audit record not valid", extra={"event": "studio.audit_record_invalid"})
        raise WriteError("studio.audit_record_invalid") from None
    result = "failed"
    path: Path | None = None
    try:
        path = write_file(folder, found, name, text)
        result = "written"
    except WriteError as exc:
        result = exc.code
        raise
    finally:
        pending = not unlocks.audit.record(
            "run.result", **common, result=result, steps=len(change.steps) if path is not None else 0
        )
    logger.info("schema revision written", extra={"event": "studio.schema_revision_written"})
    relative = Path(os.path.relpath(path, os.path.abspath(project))).as_posix()
    return Written(path=path, relative=relative, revision=revision, down_revision=head, audit_pending=pending)
