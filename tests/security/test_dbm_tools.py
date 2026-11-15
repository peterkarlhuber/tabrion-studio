"""STU-XDM-2: the verified ``pg_dump``/``pg_restore`` runner (``tabrion_studio.write.pgtools``; STU-ADR-0008 section 3;
TX-32..TX-34; GS4-04..06; reviews L-2 and L-3 of STU-XDM-1).

Fake tools (small Python programs) record the arguments, the environment and the passfile they get, so the tests see
exactly what a real tool would see. A real ``pg_dump`` and ``pg_restore`` run in ``test_dbm_restore_roundtrip.py``.

1. **Which tool (GS4-04):** only the owner's ``database-tools.toml``; refused: not configured (also with a tool on
   ``PATH``), a file that is not private, a relative path, a link (the file or a folder on the path), a file or
   folder others may write, a foreign owner, a wrong program, an older major than 16, the server or the archive.
2. **Process (GS4-05):** an argument list without a shell, ``--no-password``, ``stdin`` closed, one ``--dbname``
   connection string with ``require_auth`` and the profile's ``sslmode``; the environment built from nothing, so a
   planted ``PGHOST``, ``PGSSLMODE`` or ``PGPASSWORD`` changes nothing; a planted ``PGSERVICE`` refuses (as Studio's
   own connections, G2-01); a timeout kills the tool's whole process group (L-3).
3. **Secret (GS4-06):** the canary password only in the passfile (one escaped entry, 0600, in a 0700 folder), never
   in ``argv``, the environment, the log or a problem; the passfile gone after success, failure and timeout; a stale
   one removed at start.
4. **Output (L-3):** a backup becomes visible only after exit 0 and a readable table of contents; otherwise nothing
   is left. Never ``psql``, never ``--enable-row-security`` (L-2 c).

The runner refuses Windows (``tool.platform``); the fake tools are POSIX programs, so the rest of the module skips
there.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any
import uuid

import pytest

from tabrion_studio.write import pgtools
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.pgtools import Connection, Runner, Tools, conninfo, load_tools, passfile_line, verify_tool

pytestmark = pytest.mark.security

POSIX = sys.platform != "win32"
posix_only = pytest.mark.skipif(not POSIX, reason="the runner refuses Windows; the fake tools are POSIX programs")
CANARY_PASSWORD = f"pw-canary-{uuid.uuid4()}:with\\colon"

#: The fake tool. ``{record}``, ``{mode}`` and ``{version}`` are filled in; the environment is the tool's own (clean),
#: so everything it needs is in its text.
FAKE = r"""#!{python} -I
import json, os, stat, sys, time
name = os.path.basename(sys.argv[0])
mode = {mode!r}
record = {record!r}
if "--version" in sys.argv:
    if mode == "wrong":
        print("psql (PostgreSQL) {version}")
    else:
        print(name + " (PostgreSQL) {version}")
    sys.exit(0)
seen = {{"argv": sys.argv, "env": dict(os.environ), "stdin": None, "passfile": None}}
pf = os.environ.get("PGPASSFILE")
if pf and os.path.exists(pf):
    seen["passfile"] = open(pf).read()
    seen["passfile_mode"] = stat.S_IMODE(os.stat(pf).st_mode)
    seen["passfile_dir_mode"] = stat.S_IMODE(os.stat(os.path.dirname(pf)).st_mode)
seen["passfile_path"] = pf
data = sys.stdin.buffer.read()
seen["stdin"] = len(data)
seen["stdin_head"] = data[:5].decode("latin-1")
with open(record + "." + name + ("-list" if "--list" in sys.argv else ""), "w") as out:
    json.dump(seen, out)
if mode == "sleep":
    pid = os.fork()
    if pid == 0:
        with open(record + ".child", "w") as out:
            out.write(str(os.getpid()))
        time.sleep(60)
        os._exit(0)
    time.sleep(60)
if mode == "fail" or (mode == "badlist" and "--list" in sys.argv):
    sys.stderr.write("pg_dump: error: connection to server failed: FATAL password authentication failed\n")
    sys.exit(1)
if name == "pg_dump":
    sys.stdout.buffer.write(b"PGDMP" + b"\x00" * 64)
elif "--list" in sys.argv:
    sys.stdout.write(";\n; Archive created\n;\n215; 1262 16384 DATABASE - shop owner\n")
    sys.stdout.write("2; 3079 16385 EXTENSION - pgcrypto\n")
"""


class Fakes:
    def __init__(self, base: Path) -> None:
        # The real path: tmp_path may lie below a link (macOS /var), and the runner refuses any link on the path.
        self.base = Path(os.path.realpath(base))
        self.bin = self.base / "bin"
        self.bin.mkdir(mode=0o755)
        self.bin.chmod(0o755)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        self.record = self.base / "record"

    def tool(self, name: str, mode: str = "ok", version: str = "17.2", folder: Path | None = None) -> Path:
        path = (folder or self.bin) / name
        text = FAKE.format(python=sys.executable, mode=mode, record=str(self.record), version=version)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def configure(self, pg_dump: Path | str | None, pg_restore: Path | str | None, mode: int = 0o600) -> None:
        lines = ["version = 1", "[postgresql]"]
        if pg_dump is not None:
            lines.append(f"pg_dump = {json.dumps(str(pg_dump))}")
        if pg_restore is not None:
            lines.append(f"pg_restore = {json.dumps(str(pg_restore))}")
        file = self.state / "database-tools.toml"
        file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        file.chmod(mode)

    def seen(self, name: str) -> dict[str, Any]:
        found: dict[str, Any] = json.loads(Path(f"{self.record}.{name}").read_text())
        return found

    def scratch(self) -> Path:
        folder = self.base / f"scratch-{uuid.uuid4().hex[:8]}"
        folder.mkdir(mode=0o700)
        return folder


@pytest.fixture
def fakes(tmp_path: Path) -> Iterator[Fakes]:
    if not POSIX:
        pytest.skip("the runner refuses Windows; the fake tools are POSIX programs")
    yield Fakes(tmp_path)


def _reason(exc: pytest.ExceptionInfo[WriteError]) -> tuple[str, list[tuple[str, str]] | None]:
    return exc.value.code, exc.value.issues


def _connection(**extra: Any) -> Connection:
    fields: dict[str, Any] = {
        "host": "db.example.test",
        "port": 5433,
        "dbname": "shop",
        "user": "admin_role",
        "sslmode": "verify-full",
        "password": CANARY_PASSWORD,
    }
    fields.update(extra)
    return Connection(**fields)


# --- 1. which tool (GS4-04) ----------------------------------------------------------------------------------------


@posix_only
def test_a_configured_private_tool_is_verified(fakes: Fakes) -> None:
    dump = fakes.tool("pg_dump")
    fakes.configure(dump, fakes.tool("pg_restore"))
    tool = verify_tool("pg_dump", load_tools(fakes.state), fakes.scratch(), at_least=17)
    assert (tool.name, tool.path, tool.major, tool.version) == ("pg_dump", dump, 17, "17.2")
    assert len(tool.sha256) == 64
    assert tool.metadata() == {"name": "pg_dump", "file": "pg_dump", "version": "17.2", "sha256": tool.sha256}


@posix_only
def test_a_tool_on_path_only_is_not_used(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
    fakes.tool("pg_dump")
    monkeypatch.setenv("PATH", f"{fakes.bin}{os.pathsep}{os.environ.get('PATH', '')}")
    with pytest.raises(WriteError) as found:
        verify_tool("pg_dump", load_tools(fakes.state), fakes.scratch())
    assert _reason(found) == ("studio.backup_tool_unavailable", [("tool.pg_dump", "tool.not_configured")])


@posix_only
def test_a_tools_file_that_is_not_private_is_not_read(fakes: Fakes) -> None:
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"), mode=0o644)
    assert load_tools(fakes.state) == Tools()
    fakes.configure(fakes.tool("pg_dump"), None)
    (fakes.state / "database-tools.toml").write_text('version = 1\n[postgresql]\npsql = "/x"\n')
    assert load_tools(fakes.state) == Tools()


@posix_only
@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("relative", "relative"),
        ("link", "link"),
        ("linked_folder", "link"),
        ("group_writable", "writable"),
        ("world_writable", "writable"),
        ("writable_folder", "folder"),
        ("missing", "missing"),
        ("not_executable", "not_a_program"),
        ("wrong_program", "wrong_program"),
        ("other_tool", "wrong_program"),
        ("old", "too_old"),
    ],
)
def test_an_unsafe_tool_is_refused(fakes: Fakes, setup: str, reason: str) -> None:
    dump = fakes.tool("pg_dump")
    configured: Path | str = dump
    if setup == "relative":
        configured = "bin/pg_dump"
    elif setup == "link":
        configured = fakes.base / "pg_dump"
        configured.symlink_to(dump)
    elif setup == "linked_folder":
        (fakes.base / "linked").symlink_to(fakes.bin)
        configured = fakes.base / "linked" / "pg_dump"
    elif setup == "group_writable":
        dump.chmod(0o775)
    elif setup == "world_writable":
        dump.chmod(0o757)
    elif setup == "writable_folder":
        fakes.bin.chmod(0o777)
    elif setup == "missing":
        configured = fakes.bin / "nothing"
    elif setup == "not_executable":
        dump.chmod(0o644)
    elif setup == "wrong_program":
        configured = fakes.tool("pg_dump", mode="wrong")
    elif setup == "other_tool":
        configured = fakes.tool("pg_restore")
    elif setup == "old":
        configured = fakes.tool("pg_dump", version="15.8")
    fakes.configure(configured, None)
    try:
        with pytest.raises(WriteError) as found:
            verify_tool("pg_dump", load_tools(fakes.state), fakes.scratch())
    finally:
        fakes.bin.chmod(0o755)
    assert _reason(found) == ("studio.backup_tool_unavailable", [("tool.pg_dump", f"tool.{reason}")])


@posix_only
def test_a_tool_of_a_foreign_owner_is_refused(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
    dump = fakes.tool("pg_dump")
    fakes.configure(dump, None)
    # The tools file is read first: it must stay the user's own, as it is (a changed uid would refuse the file itself).
    tools = load_tools(fakes.state)
    scratch = fakes.scratch()
    if os.geteuid() == 0:
        os.chown(dump, 65534, -1)  # nobody: neither the user (root here) nor root
    else:
        monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 4242)  # the tool is no longer "ours"
    with pytest.raises(WriteError) as found:
        verify_tool("pg_dump", tools, scratch)
    assert _reason(found) == ("studio.backup_tool_unavailable", [("tool.pg_dump", "tool.owner")])


@posix_only
def test_the_client_must_be_at_least_the_server_and_the_archive(fakes: Fakes) -> None:
    fakes.configure(fakes.tool("pg_dump", version="17.2"), fakes.tool("pg_restore", version="16.4"))
    tools = load_tools(fakes.state)
    assert verify_tool("pg_dump", tools, fakes.scratch(), at_least=17).major == 17
    with pytest.raises(WriteError) as server:
        verify_tool("pg_dump", tools, fakes.scratch(), at_least=18)
    with pytest.raises(WriteError) as archive:
        verify_tool("pg_restore", tools, fakes.scratch(), at_least=17)  # an archive of pg_dump 17
    for found in (server, archive):
        assert found.value.issues is not None and found.value.issues[0][1] == "tool.too_old"


def test_windows_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pgtools, "_POSIX", False)
    with pytest.raises(WriteError) as found:
        verify_tool("pg_dump", Tools(pg_dump=tmp_path / "pg_dump"), tmp_path)
    assert _reason(found) == ("studio.backup_tool_unavailable", [("tool.pg_dump", "tool.platform")])


# --- 2. and 3. the process and the secret (GS4-05, GS4-06) ---------------------------------------------------------


def _dump(fakes: Fakes, connection: Connection, mode: str = "ok", timeout: float = 30.0) -> Path:
    fakes.configure(fakes.tool("pg_dump", mode=mode), fakes.tool("pg_restore", mode="ok"))
    tools = load_tools(fakes.state)
    pg_dump = verify_tool("pg_dump", tools, fakes.scratch())
    pg_restore = verify_tool("pg_restore", tools, fakes.scratch())
    folder = fakes.base / "backups"
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / "20261115T000000Z-0badc0de.dump"
    Runner(fakes.state, run_timeout_s=timeout).dump(pg_dump, pg_restore, connection, target)
    return target


@posix_only
def test_the_tool_gets_an_argument_list_a_clean_environment_and_the_passfile(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    # Planted in Studio's own environment: none of them may reach the tool or redirect it (TX-34).
    monkeypatch.setenv("PGHOST", "192.0.2.66")
    monkeypatch.setenv("PGSSLMODE", "disable")
    monkeypatch.setenv("PGPASSWORD", CANARY_PASSWORD)
    monkeypatch.setenv("PGUSER", "intruder")
    target = _dump(fakes, _connection())
    assert target.read_bytes().startswith(b"PGDMP")
    seen = fakes.seen("pg_dump")
    argv = seen["argv"]
    assert argv[1:4] == ["--format=custom", "--no-password", "--lock-wait-timeout=60000"]
    assert len(argv) == 5 and argv[4].startswith("--dbname=")
    assert argv[4] == "--dbname=" + conninfo(_connection())
    assert (
        "host='db.example.test' port='5433' dbname='shop' user='admin_role' sslmode='verify-full'"
        " require_auth='scram-sha-256'"
    ) in argv[4]
    assert "192.0.2.66" not in argv[4] and "intruder" not in argv[4]
    assert seen["stdin"] == 0  # stdin closed (DEVNULL)
    env = seen["env"]
    # macOS's CoreFoundation adds __CF_USER_TEXT_ENCODING to the environment of the started (Python) fake tool
    # itself; Studio does not pass it, and it is no libpq variable.
    os_added = {"__CF_USER_TEXT_ENCODING"} if sys.platform == "darwin" else set()
    assert set(env) - os_added == {"LC_ALL", "PGPASSFILE", "PGSYSCONFDIR", "PGSERVICEFILE"}
    assert env["LC_ALL"] == "C" and not os.listdir(env["PGSYSCONFDIR"]) if os.path.exists(env["PGSYSCONFDIR"]) else True
    # The canary only in the passfile, as one exact, escaped entry; 0600 in a 0700 folder.
    assert seen["passfile"] == passfile_line(_connection())
    assert seen["passfile"].count("\n") == 1 and "pw-canary-" in seen["passfile"]
    assert seen["passfile_mode"] == 0o600 and seen["passfile_dir_mode"] == 0o700
    everything = json.dumps(argv) + json.dumps(env) + caplog.text + target.read_bytes().decode("latin-1")
    assert CANARY_PASSWORD not in everything and "pw-canary-" not in everything
    # Gone after the run, and nothing left in the run folder.
    assert not os.path.exists(seen["passfile_path"])
    assert os.listdir(fakes.state / "tool-runs") == []
    # pg_restore --list read the finished file on its stdin, also with a clean environment and no passfile.
    listed = fakes.seen("pg_restore-list")
    assert listed["argv"][1:] == ["--list"] and listed["stdin_head"] == "PGDMP" and listed["passfile"] is None


@posix_only
def test_without_a_password_no_passfile_is_written(fakes: Fakes) -> None:
    _dump(fakes, _connection(password=None, host="/var/run/postgresql", sslmode=None, requirepeer="postgres"))
    seen = fakes.seen("pg_dump")
    assert seen["passfile"] is None
    assert "require_auth='!password,!md5'" in seen["argv"][4] and "requirepeer='postgres'" in seen["argv"][4]


@posix_only
def test_a_planted_service_refuses_the_tool_as_it_refuses_studios_connections(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_file = fakes.base / "pg_service.conf"
    service_file.write_text("[evil]\nhost=192.0.2.66\n")
    monkeypatch.setenv("PGSERVICE", "evil")
    with pytest.raises(WriteError) as found:
        _dump(fakes, _connection())
    assert found.value.code == "studio.connection_environment"
    assert not Path(f"{fakes.record}.pg_dump").exists()  # never started
    monkeypatch.delenv("PGSERVICE")
    monkeypatch.setenv("PGSERVICEFILE", str(service_file))
    with pytest.raises(WriteError):
        _dump(fakes, _connection())
    assert not Path(f"{fakes.record}.pg_dump").exists()


@posix_only
def test_a_failing_tool_leaves_nothing_and_answers_a_class_only(fakes: Fakes, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(WriteError) as found:
        _dump(fakes, _connection(), mode="fail")
    assert _reason(found) == ("studio.backup_tool_failed", [("tool.pg_dump", "tool.auth")])
    seen = fakes.seen("pg_dump")
    assert not os.path.exists(seen["passfile_path"])
    assert list((fakes.base / "backups").iterdir()) == []
    assert "password authentication failed" not in caplog.text  # stderr is never logged
    assert CANARY_PASSWORD not in caplog.text


@posix_only
def test_an_unreadable_table_of_contents_leaves_no_backup(fakes: Fakes) -> None:
    """L-3: exit 0 of pg_dump is not enough; pg_restore --list must read the archive before it is published."""
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore", mode="badlist"))
    tools = load_tools(fakes.state)
    folder = fakes.base / "backups"
    folder.mkdir(mode=0o700)
    target = folder / "20261115T000000Z-0badc0de.dump"
    with pytest.raises(WriteError) as found:
        Runner(fakes.state).dump(
            verify_tool("pg_dump", tools, fakes.scratch()),
            verify_tool("pg_restore", tools, fakes.scratch()),
            _connection(),
            target,
        )
    assert found.value.code == "studio.backup_tool_failed"
    assert list(folder.iterdir()) == []


@posix_only
def test_a_timeout_kills_the_tools_process_group_and_removes_the_passfile(fakes: Fakes) -> None:
    started = time.monotonic()
    with pytest.raises(WriteError) as found:
        _dump(fakes, _connection(), mode="sleep", timeout=2.0)
    assert time.monotonic() - started < 30
    assert _reason(found) == ("studio.backup_tool_failed", [("tool.pg_dump", "tool.timeout")])
    seen = fakes.seen("pg_dump")
    assert not os.path.exists(seen["passfile_path"])
    child = int(Path(f"{fakes.record}.child").read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("a process the tool started outlived the timeout")
    assert list((fakes.base / "backups").iterdir()) == []


@posix_only
def test_an_existing_backup_name_is_never_overwritten(fakes: Fakes) -> None:
    folder = fakes.base / "backups"
    folder.mkdir(mode=0o700)
    target = folder / "20261115T000000Z-0badc0de.dump"
    target.write_bytes(b"older backup")
    with pytest.raises(WriteError) as found:
        _dump(fakes, _connection())
    assert found.value.code == "studio.dbm_backup_failed"
    assert target.read_bytes() == b"older backup"
    assert sorted(p.name for p in folder.iterdir()) == [target.name]


@posix_only
def test_a_stale_passfile_is_removed_at_start(fakes: Fakes) -> None:
    stale = fakes.state / "tool-runs" / "0123456789abcdef"
    stale.mkdir(parents=True, mode=0o700)
    (fakes.state / "tool-runs").chmod(0o700)
    (stale / "passfile").write_text(f"h:5432:d:u:{CANARY_PASSWORD}\n")
    assert Runner(fakes.state).remove_stale() == 1
    assert not stale.exists()


@posix_only
def test_a_restore_streams_the_checked_archive_with_the_fixed_options(fakes: Fakes) -> None:
    fakes.configure(fakes.tool("pg_dump"), fakes.tool("pg_restore"))
    pg_restore = verify_tool("pg_restore", load_tools(fakes.state), fakes.scratch())
    archive = fakes.base / "a.dump"
    archive.write_bytes(b"PGDMP" + b"\x01" * 100)
    archive.chmod(0o600)
    with pgtools.open_archive(archive) as (handle, size, digest):
        assert size == 105 and len(digest) == 64
        assert Runner(fakes.state).table_of_contents(pg_restore, handle) == 1  # one EXTENSION line (I-2)
        Runner(fakes.state).restore(pg_restore, _connection(dbname="copy"), handle)
    seen = fakes.seen("pg_restore")
    assert seen["argv"][1:6] == [
        "--no-password",
        "--single-transaction",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
    ]
    assert len(seen["argv"]) == 7 and seen["argv"][6].startswith("--dbname=") and "dbname='copy'" in seen["argv"][6]
    assert seen["stdin"] == 105 and seen["stdin_head"] == "PGDMP"
    assert not any(a.startswith(("--clean", "--create", "--role", "-f", "--file")) for a in seen["argv"])
    assert not os.path.exists(seen["passfile_path"])


@posix_only
def test_a_plain_sql_file_is_never_restored(fakes: Fakes) -> None:
    plain = fakes.base / "plain.sql"
    plain.write_text("-- PostgreSQL database dump\nCREATE ROLE x;\n")
    with pytest.raises(WriteError) as found, pgtools.open_archive(plain):
        pass
    assert _reason(found) == ("studio.dbm_backup_invalid", [("backup.file", "backup.not_custom_format")])


def test_the_connection_string_quotes_every_value() -> None:
    text = conninfo(_connection(dbname="it's a \\ name", user="o'neil"))
    assert "dbname='it\\'s a \\\\ name'" in text and "user='o\\'neil'" in text
    assert CANARY_PASSWORD not in text and "password" not in text.replace("require_auth", "")
    assert repr(_connection()).count("pw-canary") == 0
    line = passfile_line(_connection(dbname="a:b", password="p\\q:r"))
    assert line == "db.example.test:5433:a\\:b:admin_role:p\\\\q\\:r\n"
    with pytest.raises(WriteError):
        passfile_line(_connection(password="two\nlines"))


def test_never_psql_and_never_row_security_or_a_script() -> None:
    """TX-38, L-2 c: the runner starts only pg_dump and pg_restore, never with --enable-row-security, never writes a
    plain script (no --format=plain) and never passes the password through the environment; no string of its code
    (docstrings left out: they name what is never done) holds such an option."""
    tree = ast.parse(Path(pgtools.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    texts = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]
    assert pgtools.TOOL_NAMES == ("pg_dump", "pg_restore")
    for forbidden in ("psql", "enable-row-security", "--format=plain", "PGPASSWORD", "--clean", "--create", "--role"):
        assert not any(forbidden in text for text in texts), forbidden
    assert "shell=True" not in Path(pgtools.__file__).read_text(encoding="utf-8")
