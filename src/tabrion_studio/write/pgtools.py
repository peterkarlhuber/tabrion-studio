"""The verified ``pg_dump`` and ``pg_restore`` runner (STU-XDM-2; STU-ADR-0008 section 3; TX-32..TX-36; GS4-04..07).

Tabrion has no ``pg_dump`` wrapper (tabrion ADR 0015 section 6): running the PostgreSQL tools is Studio's. This module
runs them and nothing else; it knows no route, no area and no unlock, so the Database Manager (STU-XDM-2) and, in a
the Schema Designer's backup before a destructive PostgreSQL run (the STU-CR-5 follow-up, which closes its open point 1;
review L-2 of STU-XDM-1) use the same code. It is part of the write package and is imported only by code that an
area's flag opened (``database_manager`` and ``schema_designer``, where GS4-04..07 and GS4-22 are G-S3 conditions
too).

**Which tool (TX-32).** Only the absolute paths of :data:`TOOLS_FILE` (``database-tools.toml``) in the folder of
``write-areas.toml``, read with that file's checks (a regular file, no link, owned by the user, 0600, in a folder only
the user may write; :func:`load_tools`)::

    version = 1
    [postgresql]
    pg_dump = "/usr/lib/postgresql/17/bin/pg_dump"
    pg_restore = "/usr/lib/postgresql/17/bin/pg_restore"

Never ``PATH``, the page, a settings save, a project file, an environment variable or a profile: nothing else builds a
:class:`Tools`. Before **every** run :func:`verify_tool` checks the path again: absolute and its own real path (no link
anywhere on it), a regular file, executable, not writable by group or others, owned by the user or root, in a folder
with the same rules, and every folder above that one of root or the user and not writable by group or others unless
sticky (review L-1 of PR #136); then it runs ``<tool> --version`` (an argument list, no shell, the clean environment
below), which must name the expected program, and reads its major version. The client's major version must be at least
16 (``require_auth`` of libpq 16, which every connection sets) and at least the server's; ``pg_restore`` also at least
the archive's ``pg_dump``. The tool's file name, version and SHA-256 go into the backup's metadata. Windows is refused
(``tool.platform``): the passfile and the mode checks are POSIX rules (an open point).

**Process (GS4-05).** :func:`run_tool`: ``subprocess.Popen`` with an argument list Studio builds (``shell=False``; no
user text in an option: the connection is one ``--dbname=<connection string>`` argument with every value quoted, the
archive is ``stdin``), ``--no-password``, ``stdin`` closed or the archive Studio opened and checked, a timeout, its
own process group, which is killed as a whole on the timeout (review L-3). ``psql`` is never started, no plain SQL
script is ever made, and ``--enable-row-security`` is never passed (review L-2 c: it would make an incomplete backup
without a word; without it ``pg_dump`` fails on a table with row security for a role without BYPASSRLS, fail closed).

**Environment (TX-34, G2-01).** Built from nothing (:func:`child_environment`): ``LC_ALL=C``, ``PGPASSFILE`` (the
run's passfile or a path that does not exist), ``PGSERVICEFILE`` and ``PGSYSCONFDIR`` in an empty private folder;
no ``PATH``, no ``HOME``, no inherited ``PG*`` variable, never ``PGPASSWORD``. Studio's own environment is checked
with :func:`tabrion_studio.services.connections.pin_libpq_environment` first, so a variable that refuses Studio's own
connections (``PGSERVICEFILE``, ``PGSYSCONFDIR``, a ``PGSERVICE`` that would name the target) refuses the tool too;
the variables it lets pass never reach the child. The connection string carries ``host``, ``port``, ``dbname``,
``user``, ``sslmode`` and ``requirepeer`` from the stored profiles, ``require_auth`` as every write and admin
connection (``scram-sha-256`` when a password is sent, else ``!password,!md5``; STU-XW-4), ``connect_timeout`` and
``application_name``; never the password.

**Secret (TX-33, GS4-06).** The password only in a temporary passfile: one exact entry (``:`` and ``\\`` escaped),
0600, in a 0700 folder of its own below the run folder (:data:`RUN_FOLDER` in the folder of ``write-areas.toml``),
removed in a ``finally`` block; :meth:`Runner.remove_stale` removes what a crashed run left when the area starts.

**Output (TX-33, L-3).** ``pg_dump -Fc`` writes into a temporary file Studio opened itself (exclusive, 0600, no link)
in the target folder. Only after exit status 0 **and** a successful ``pg_restore --list`` of that file (the archive's
table of contents is readable with the verified ``pg_restore``) is it moved to its final name, without overwriting
(a hard link, then the temporary name removed). Otherwise the temporary file is removed and the caller does nothing
that depends on the backup. ``stderr`` is never logged or answered: it can hold names and values from server
messages. Studio keeps only a fixed class of it (``auth``, ``connect``, ``version``, ``permission``, ``other``) for
the problem's issue and the log.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import contextlib
from dataclasses import dataclass
import errno
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
from typing import IO, Any

import sqlalchemy

from tabrion_studio.config.files import ConfigFileError, parse_toml, read_plain_file
from tabrion_studio.config.schema import refused_host
from tabrion_studio.paths import UnsafePathError, check_private_dir, ensure_private_dir
from tabrion_studio.services.connections import APPLICATION_NAME, RegistryError, pin_libpq_environment
from tabrion_studio.write.connection import WriteError, require_auth
from tabrion_studio.write.folders import RUN_FOLDER, ancestor_is_safe, folders_up, remove_stale_runs

__all__ = [
    "MIN_TOOL_MAJOR",
    "RUN_FOLDER",
    "TOOLS_FILE",
    "Archive",
    "Connection",
    "Runner",
    "Tool",
    "Tools",
    "child_environment",
    "conninfo",
    "load_tools",
    "passfile_line",
    "verify_tool",
]

logger = logging.getLogger("tabrion_studio.write")

#: The owner's tool paths, next to ``write-areas.toml`` (STU-ADR-0008 section 3: "STU-XDM-2 fixes the key names").
TOOLS_FILE = "database-tools.toml"
TOOLS_VERSION = 1
TOOL_NAMES = ("pg_dump", "pg_restore")
#: libpq 16: ``require_auth``, which every write and admin connection sets (STU-XW-4).
MIN_TOOL_MAJOR = 16
#: Seconds: ``--version``, ``pg_restore --list`` and a whole dump or restore.
VERSION_TIMEOUT_S = 10.0
LIST_TIMEOUT_S = 120.0
RUN_TIMEOUT_S = 3600.0
#: ``connect_timeout`` of the tool's connection (seconds) and ``--lock-wait-timeout`` of ``pg_dump`` (milliseconds).
CONNECT_TIMEOUT_S = 10
LOCK_WAIT_TIMEOUT_MS = 60_000
#: How much of ``stderr`` Studio reads to classify a failure (never logged or answered).
STDERR_LIMIT = 64 * 1024
#: The first bytes of a custom-format archive (``pg_dump -Fc``).
ARCHIVE_MAGIC = b"PGDMP"
_POSIX = sys.platform != "win32"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_VERSION = re.compile(r"(pg_dump|pg_restore) \(PostgreSQL\) (\d{1,3})(?:\.(\d{1,3}))?(?:[^\n]*)\n?")
#: A ``pg_restore --list`` line of an extension: ``<id>; <class oid> <oid> EXTENSION - <name>``.
_TOC_EXTENSION = re.compile(rb"^\d+; \d+ \d+ EXTENSION ", re.MULTILINE)
#: The keys of the tool's connection string, in this order; nothing else is ever passed.
CONNINFO_KEYS = (
    "host",
    "port",
    "dbname",
    "user",
    "sslmode",
    "require_auth",
    "requirepeer",
    "connect_timeout",
    "application_name",
)
#: Classes of a tool's ``stderr`` (lowercase text matched; the text itself goes nowhere).
_STDERR_CLASSES = (
    ("auth", (b"authentication", b"password", b"require_auth", b"no pg_hba.conf entry")),
    ("version", (b"server version", b"aborting because of server version mismatch", b"unsupported version")),
    ("connect", (b"could not connect", b"connection to server", b"could not translate host name")),
    ("permission", (b"permission denied", b"must be owner", b"must be superuser")),
)


def _tool_error(code: str, tool: str, reason: str) -> WriteError:
    return WriteError(code, [(f"tool.{tool}", f"tool.{reason}")])


# --- the owner's configuration ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tools:
    """The tool paths of the owner's ``database-tools.toml``; None where a tool is not set (never a default)."""

    pg_dump: Path | None = None
    pg_restore: Path | None = None

    def path(self, name: str) -> Path:
        value = self.pg_dump if name == "pg_dump" else self.pg_restore if name == "pg_restore" else None
        if value is None:
            raise _tool_error("studio.backup_tool_unavailable", name, "not_configured")
        return value


NO_TOOLS = Tools()


def load_tools(folder: Path | None) -> Tools:
    """``folder/database-tools.toml`` with the checks of ``write-areas.toml``; :data:`NO_TOOLS` for anything else.

    Read every time a tool is needed (a change takes effect without a restart; the file is the owner's, as the flags).
    Only absolute paths are kept; the keys are fixed. Never raises; a refusal is logged with its code only."""
    if folder is None:
        return NO_TOOLS
    try:
        check_private_dir(folder)
        data = read_plain_file(folder / TOOLS_FILE, private=True)
        if data is None:
            return NO_TOOLS
        doc = parse_toml(data)
    except (ConfigFileError, UnsafePathError, OSError) as exc:
        code = exc.code if isinstance(exc, ConfigFileError) else "config.unsafe_file"
        logger.warning("database tools file not used", extra={"event": "studio.backup_tools_unusable", "code": code})
        return NO_TOOLS
    if (
        type(doc.get("version")) is not int
        or doc.get("version") != TOOLS_VERSION
        or set(doc) - {"version", "postgresql"}
    ):
        logger.warning("database tools file is not valid", extra={"event": "studio.backup_tools_unusable"})
        return NO_TOOLS
    table = doc.get("postgresql", {})
    if not isinstance(table, dict) or set(table) - set(TOOL_NAMES):
        logger.warning("database tools file is not valid", extra={"event": "studio.backup_tools_unusable"})
        return NO_TOOLS
    found: dict[str, Path | None] = {}
    for name in TOOL_NAMES:
        value = table.get(name)
        # A relative path is kept as it is, so verify_tool refuses it with its own reason (never resolved here).
        found[name] = Path(value) if isinstance(value, str) and value and "\x00" not in value else None
    return Tools(pg_dump=found["pg_dump"], pg_restore=found["pg_restore"])


# --- the tool, checked before every run ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """A verified tool: its path, its major and full version (``--version``) and the SHA-256 of its file."""

    name: str
    path: Path
    major: int
    version: str
    sha256: str

    def metadata(self) -> dict[str, Any]:
        return {"name": self.name, "file": self.path.name, "version": self.version, "sha256": self.sha256}


def _safe_owner(info: os.stat_result) -> bool:
    return info.st_uid in (os.getuid(), 0)


def _check_file(name: str, path: Path) -> os.stat_result:
    if not path.is_absolute():
        raise _tool_error("studio.backup_tool_unavailable", name, "relative")
    try:
        info = os.lstat(path)
        real = Path(os.path.realpath(path))
    except OSError:
        raise _tool_error("studio.backup_tool_unavailable", name, "missing") from None
    if stat.S_ISLNK(info.st_mode) or real != path:
        raise _tool_error("studio.backup_tool_unavailable", name, "link")
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR:
        raise _tool_error("studio.backup_tool_unavailable", name, "not_a_program")
    if info.st_mode & 0o022:
        raise _tool_error("studio.backup_tool_unavailable", name, "writable")
    if not _safe_owner(info):
        raise _tool_error("studio.backup_tool_unavailable", name, "owner")
    folder = os.lstat(path.parent)
    if stat.S_ISLNK(folder.st_mode) or folder.st_mode & 0o022 or not _safe_owner(folder):
        raise _tool_error("studio.backup_tool_unavailable", name, "folder")
    # Every folder above it too (review L-1 of PR #136): one that others may write lets them put their own folder,
    # and so their own program, in place of the checked one between the check and the start.
    uid = os.getuid()
    for above in list(folders_up(str(path.parent)))[1:]:
        try:
            ok = ancestor_is_safe(os.lstat(above), uid)
        except OSError:
            ok = False
        if not ok:
            raise _tool_error("studio.backup_tool_unavailable", name, "folder")
    return info


def _sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def verify_tool(name: str, tools: Tools, scratch: Path, *, at_least: int = MIN_TOOL_MAJOR) -> Tool:
    """The checks of the module text for the tool ``name`` (``pg_dump`` or ``pg_restore``); :class:`WriteError`
    ``studio.backup_tool_unavailable`` with a ``tool.<name>`` issue that says why. ``scratch`` is an empty private
    folder for the environment of ``--version``. ``at_least`` is the lowest major version allowed (the server's,
    the archive's), never below :data:`MIN_TOOL_MAJOR`."""
    if name not in TOOL_NAMES:  # pragma: no cover - a coding error
        raise ValueError("unknown tool")
    if not _POSIX:
        raise _tool_error("studio.backup_tool_unavailable", name, "platform")
    path = tools.path(name)
    info = _check_file(name, path)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
    except OSError:
        raise _tool_error("studio.backup_tool_unavailable", name, "missing") from None
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise _tool_error("studio.backup_tool_unavailable", name, "link")
        digest = _sha256(descriptor)
    finally:
        os.close(descriptor)
    env = child_environment(scratch, None)
    try:
        done = subprocess.run(  # noqa: S603 - an absolute, checked path and fixed arguments; no shell
            [str(path), "--version"],
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=env,
            cwd=scratch,
            timeout=VERSION_TIMEOUT_S,
            check=False,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise _tool_error("studio.backup_tool_unavailable", name, "wrong_program") from None
    text = done.stdout[:256].decode("ascii", "replace")
    match = _VERSION.fullmatch(text)
    if done.returncode != 0 or match is None or match.group(1) != name:
        raise _tool_error("studio.backup_tool_unavailable", name, "wrong_program")
    major = int(match.group(2))
    version = match.group(2) if match.group(3) is None else f"{match.group(2)}.{match.group(3)}"
    if major < max(MIN_TOOL_MAJOR, at_least):
        raise _tool_error("studio.backup_tool_unavailable", name, "too_old")
    return Tool(name=name, path=path, major=major, version=version, sha256=digest)


# --- the connection, the environment and the passfile -------------------------------------------------------------


@dataclass(frozen=True)
class Connection:
    """Where the tool connects, from the stored profiles only: never the password (that is in the passfile)."""

    host: str | None
    port: int | None
    dbname: str
    user: str
    sslmode: str | None
    password: str | None = None
    requirepeer: str | None = None

    def __post_init__(self) -> None:
        # L-1 of #153: a host list or an abstract Unix socket (a leading "@") never becomes a tool's connection,
        # whatever built it (the validator refuses both already; conninfo checks again).
        if self.host is not None and refused_host(self.host) is not None:
            raise WriteError("studio.write_coupling_invalid")

    def __repr__(self) -> str:  # never the password, also not in a traceback's repr
        return f"Connection(host={self.host!r}, port={self.port!r}, dbname={self.dbname!r}, user={self.user!r})"

    def with_database(self, dbname: str) -> Connection:
        return Connection(self.host, self.port, dbname, self.user, self.sslmode, self.password, self.requirepeer)


def _quote(value: str) -> str:
    if "\x00" in value:
        raise WriteError("studio.write_coupling_invalid")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def conninfo(connection: Connection) -> str:
    """The libpq connection string the tool gets as its one ``--dbname=`` argument (keys :data:`CONNINFO_KEYS`).

    A host list or an abstract Unix socket (a leading ``@``) never reaches the tool (L-1 of #153):
    ``studio.write_coupling_invalid``."""
    if connection.host is not None and refused_host(connection.host) is not None:
        raise WriteError("studio.write_coupling_invalid")
    values: dict[str, str | None] = {
        "host": connection.host,
        "port": None if connection.port is None else str(connection.port),
        "dbname": connection.dbname,
        "user": connection.user,
        "sslmode": connection.sslmode,
        "require_auth": require_auth(connection.password),
        "requirepeer": connection.requirepeer,
        "connect_timeout": str(CONNECT_TIMEOUT_S),
        "application_name": APPLICATION_NAME,
    }
    return " ".join(f"{key}={_quote(value)}" for key in CONNINFO_KEYS if (value := values[key]) is not None)


def _pass_field(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise WriteError("studio.write_coupling_invalid")
    return value.replace("\\", "\\\\").replace(":", "\\:")


def passfile_line(connection: Connection) -> str:
    """The passfile's one exact entry: ``host:port:database:user:password``, ``:`` and ``\\`` escaped."""
    if connection.password is None:
        return ""
    host = connection.host or "localhost"
    port = str(connection.port or 5432)
    fields = (host, port, connection.dbname, connection.user, connection.password)
    return ":".join(_pass_field(field) for field in fields) + "\n"


def child_environment(private: Path, passfile: Path | None) -> dict[str, str]:
    """The tool's whole environment, built from nothing (TX-34): see the module text."""
    sysconf = private / "sysconf"
    return {
        "LC_ALL": "C",
        "PGPASSFILE": str(passfile if passfile is not None else private / "no-passfile"),
        "PGSYSCONFDIR": str(sysconf),
        "PGSERVICEFILE": str(sysconf / "pg_service.conf"),
    }


def _check_studio_environment(connection: Connection) -> None:
    """Studio's own libpq environment rules (G2-01, ``pin_libpq_environment``): a variable that would refuse Studio's
    own connection to this target refuses the tool's too (``studio.connection_environment``). The variables it lets
    pass never reach the child, whose environment is built from nothing."""
    url = sqlalchemy.URL.create(
        "postgresql",
        username=connection.user,
        host=connection.host,
        port=connection.port or 5432,
        database=connection.dbname,
        query={} if connection.sslmode is None else {"sslmode": connection.sslmode},
    )
    # As Studio's own connections give them: the password (or the empty one), the passfile, the authentication.
    given: dict[str, Any] = {"passfile": "", "password": "", "require_auth": "", "application_name": "", "options": ""}
    if connection.requirepeer is not None:
        given["requirepeer"] = connection.requirepeer
    try:
        pin_libpq_environment(url, given)
    except RegistryError as exc:
        raise WriteError(exc.code) from None


# --- running ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Archive:
    """A finished ``pg_dump`` archive: its final path, size and SHA-256, and what its table of contents showed."""

    path: Path
    size: int
    sha256: str
    extensions: int


def _stderr_class(text: bytes) -> str:
    lowered = text[:STDERR_LIMIT].lower()
    for name, marks in _STDERR_CLASSES:
        if any(mark in lowered for mark in marks):
            return name
    return "other"


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


class Runner:
    """Runs the verified tools with the rules of the module text. ``state`` is the folder of ``write-areas.toml``
    (the tools file and :data:`RUN_FOLDER` are there); None refuses every run (fail closed)."""

    def __init__(self, state: Path | None, *, run_timeout_s: float = RUN_TIMEOUT_S) -> None:
        self.state = state
        self.run_timeout_s = run_timeout_s

    # --- folders ---------------------------------------------------------------------------------------------------

    def tools(self) -> Tools:
        return load_tools(self.state)

    def remove_stale(self) -> int:
        """Remove what a crashed run left in :data:`RUN_FOLDER` (passfiles among it); the number removed
        (:func:`tabrion_studio.write.folders.remove_stale_runs`)."""
        return remove_stale_runs(self.state)

    @contextlib.contextmanager
    def private_folder(self, connection: Connection | None = None) -> Iterator[tuple[Path, Path | None]]:
        """One run's private folder (0700) with an empty ``sysconf`` folder and, when ``connection`` sends a
        password, the passfile (0600, one entry); removed when the block ends, whatever happened."""
        if self.state is None or not _POSIX:
            raise WriteError("studio.backup_tool_unavailable", [("tool.pg_dump", "tool.platform")])
        try:
            base = ensure_private_dir(self.state / RUN_FOLDER)
            folder = base / secrets.token_hex(8)
            folder.mkdir(mode=0o700)
            (folder / "sysconf").mkdir(mode=0o700)
        except (OSError, UnsafePathError):
            raise WriteError("studio.dbm_backup_failed") from None
        try:
            passfile: Path | None = None
            line = "" if connection is None else passfile_line(connection)
            if line:
                passfile = folder / "passfile"
                descriptor = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(line)
            yield folder, passfile
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # --- one process -----------------------------------------------------------------------------------------------

    def _run(
        self,
        tool: Tool,
        arguments: list[str],
        env: Mapping[str, str],
        cwd: Path,
        *,
        stdin: IO[bytes] | int,
        stdout: IO[bytes] | int,
        timeout: float,
    ) -> tuple[int, bytes | None]:
        argv = [str(tool.path), *arguments]
        try:
            process = subprocess.Popen(  # noqa: S603 - a verified absolute path and Studio's own arguments; no shell
                argv,
                shell=False,
                stdin=stdin,
                stdout=stdout,
                stderr=subprocess.PIPE,
                env=dict(env),
                cwd=cwd,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            raise _tool_error("studio.backup_tool_failed", tool.name, "not_started") from None
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            with contextlib.suppress(Exception):
                process.communicate(timeout=5)
            logger.warning("database tool stopped: timeout", extra={"event": "studio.backup_tool_failed"})
            raise _tool_error("studio.backup_tool_failed", tool.name, "timeout") from None
        except BaseException:
            _kill_group(process)
            raise
        finally:
            # Whatever the tool started is ended with it (review L-3): its group is killed when it is done.
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
        if process.returncode != 0:
            reason = _stderr_class(err or b"")
            logger.warning(
                "database tool failed",
                extra={"event": "studio.backup_tool_failed", "code": f"tool.{reason}"},
            )
            raise _tool_error("studio.backup_tool_failed", tool.name, reason)
        return process.returncode, out

    # --- pg_dump ---------------------------------------------------------------------------------------------------

    def dump(self, pg_dump: Tool, pg_restore: Tool, connection: Connection, target: Path) -> Archive:
        """``pg_dump -Fc`` of ``connection`` into the new file ``target`` (its folder exists, private): a temporary
        file first, then ``pg_restore --list`` of it, then the move to ``target`` without overwriting (review L-3).
        Raises :class:`WriteError`; nothing is left on failure."""
        _check_studio_environment(connection)
        folder = target.parent
        temporary = folder / f".{target.name}.{secrets.token_hex(8)}.partial"
        arguments = [
            "--format=custom",
            "--no-password",
            f"--lock-wait-timeout={LOCK_WAIT_TIMEOUT_MS}",
            f"--dbname={conninfo(connection)}",
        ]
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600)
        except OSError:
            raise WriteError("studio.dbm_backup_failed") from None
        published = False
        try:
            with os.fdopen(descriptor, "wb") as output, self.private_folder(connection) as (private, passfile):
                env = child_environment(private, passfile)
                self._run(
                    pg_dump,
                    arguments,
                    env,
                    private,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    timeout=self.run_timeout_s,
                )
                output.flush()
                os.fsync(output.fileno())
            with open_archive(temporary) as (archive, size, digest):
                extensions = self.table_of_contents(pg_restore, archive)
            _publish(temporary, target)
            published = True
        finally:
            if not published:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
        return Archive(path=target, size=size, sha256=digest, extensions=extensions)

    # --- pg_restore ------------------------------------------------------------------------------------------------

    def table_of_contents(self, pg_restore: Tool, archive: IO[bytes]) -> int:
        """``pg_restore --list`` of the open, checked ``archive`` (its ``stdin``): the table of contents must be
        readable (review L-3); the number of extensions it creates (review I-2). The list itself (object names)
        goes nowhere."""
        _rewind(archive)
        with self.private_folder() as (private, _passfile):
            _code, out = self._run(
                pg_restore,
                ["--list"],
                child_environment(private, None),
                private,
                stdin=archive,
                stdout=subprocess.PIPE,
                timeout=LIST_TIMEOUT_S,
            )
        _rewind(archive)
        return len(_TOC_EXTENSION.findall(out or b""))

    def restore(self, pg_restore: Tool, connection: Connection, archive: IO[bytes]) -> None:
        """``pg_restore`` of the open, checked ``archive`` (its ``stdin``) into ``connection``'s database, with
        ``--single-transaction --exit-on-error --no-owner --no-privileges`` (STU-ADR-0008 section 3). Never
        ``--clean``, ``--create``, ``--role`` or a script file. Raises :class:`WriteError`."""
        _check_studio_environment(connection)
        arguments = [
            "--no-password",
            "--single-transaction",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            f"--dbname={conninfo(connection)}",
        ]
        _rewind(archive)
        with self.private_folder(connection) as (private, passfile):
            self._run(
                pg_restore,
                arguments,
                child_environment(private, passfile),
                private,
                stdin=archive,
                stdout=subprocess.DEVNULL,
                timeout=self.run_timeout_s,
            )


@contextlib.contextmanager
def open_archive(path: Path) -> Iterator[tuple[IO[bytes], int, str]]:
    """``path`` opened without following a link, a regular file that starts with :data:`ARCHIVE_MAGIC` (a plain SQL
    file is refused): the open file, its size and SHA-256 (read from the open file, so a restore uses the bytes it
    checked). :class:`WriteError` ``studio.dbm_backup_invalid`` otherwise."""
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
    except OSError as exc:
        reason = "backup.link" if exc.errno == errno.ELOOP else "backup.missing"
        raise WriteError("studio.dbm_backup_invalid", [("backup.file", reason)]) from None
    # Unbuffered: every seek moves the descriptor itself, which the tool reads as its stdin.
    with os.fdopen(descriptor, "rb", buffering=0) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise WriteError("studio.dbm_backup_invalid", [("backup.file", "backup.not_a_file")])
        if handle.read(len(ARCHIVE_MAGIC)) != ARCHIVE_MAGIC:
            raise WriteError("studio.dbm_backup_invalid", [("backup.file", "backup.not_custom_format")])
        _rewind(handle)
        digest = _sha256(handle.fileno())
        _rewind(handle)
        yield handle, info.st_size, digest


def _rewind(handle: IO[bytes]) -> None:
    handle.seek(0)
    os.lseek(handle.fileno(), 0, os.SEEK_SET)


def _publish(temporary: Path, target: Path) -> None:
    """Move the finished file to ``target`` atomically, never over an existing name (a hard link, then unlink)."""
    try:
        os.link(temporary, target, follow_symlinks=False)
    except FileExistsError:
        raise WriteError("studio.dbm_backup_failed") from None
    except OSError:
        raise WriteError("studio.dbm_backup_failed") from None
    os.unlink(temporary)
    with contextlib.suppress(OSError):
        directory = os.open(target.parent, os.O_RDONLY | _CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
