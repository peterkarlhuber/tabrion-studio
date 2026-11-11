"""STU-015: migration status (Alembic, SQLite ``user_version``) and the code trust it needs (threat T-16).

* Untrusted (the default), changed or invalid code: ``current`` from the version table, the revisions
  ``unknown (project not trusted)``, and no project code runs (a revision file that writes a canary when imported).
* Trusted: the revisions are read in a separate process (another pid), whose environment holds none of Studio's
  variables, which gets the connection's target on standard input, is killed after its timeout (with its children)
  and refuses to run when the files changed after the trust.
* The code-trust routes sit behind the session and the CSRF check; a stale digest is refused.
* The worker's guards (review of #29): what runs is the bytes that were hashed (no bytecode of the project is read),
  the database driver is loaded before the project's folders are on ``sys.path``, the worker is isolated (``-I``),
  its standard streams are closed, its answer is capped while it is read, nothing of the project's code runs after
  the answer, a child that left the process group does not hold Studio, and one worker runs at a time.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import py_compile
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
import sqlalchemy

from tabrion_studio.config.code_trust import CodeTrust
from tabrion_studio.config.schema import Profile, SecretRef
from tabrion_studio.services import migrations as migrations_module
from tabrion_studio.services.connections import ConnectionRegistry
from tabrion_studio.services.migrations import NOT_TRUSTED, Migrations, run_worker, worker_environment
from tests.security.test_read_only import Source
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

PG_URL = os.environ.get("TABRION_STUDIO_TEST_POSTGRES")
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and process groups")

INI = "[alembic]\nscript_location = %(here)s/migrations\nprepend_sys_path = .\n"
#: Writes the canary when Alembic imports the file: the pid, the environment's names, and whether the module the
#: project keeps next to its migrations (prepend_sys_path) imports.
CANARY = """
import json, os, pathlib
try:
    import shop_models
    found = shop_models.NAME
except ImportError:
    found = None
try:
    import shop_compiled
    compiled = shop_compiled.NAME
except ImportError:
    compiled = None
pathlib.Path(__file__).resolve().parents[2].joinpath("ran.json").write_text(
    json.dumps(
        {"pid": os.getpid(), "env": sorted(os.environ), "module": found, "cwd": os.getcwd(), "compiled": compiled}
    )
)
"""


def revision(name: str, down: str | None, body: str = "") -> str:
    return (
        f'"""{name}"""\n{body}\nrevision = "{name}"\ndown_revision = {down!r}\nbranch_labels = None\n'
        "depends_on = None\n\n\ndef upgrade():\n    pass\n\n\ndef downgrade():\n    pass\n"
    )


@dataclass
class Shop:
    project: Path
    database: Path
    code: CodeTrust
    registry: ConnectionRegistry
    migrations: Migrations
    connection_id: str

    @property
    def canary(self) -> Path:
        return self.project / "ran.json"

    def ran(self) -> dict[str, Any] | None:
        return json.loads(self.canary.read_text()) if self.canary.exists() else None


def make_project(root: Path, *, current: str | None = "0001") -> tuple[Path, Path]:
    project = root / "shop"
    versions = project / "migrations" / "versions"
    versions.mkdir(parents=True)
    (project / "alembic.ini").write_text(INI)
    (project / "shop_models.py").write_text('NAME = "shop"\n')
    (versions / "0001_parts.py").write_text(revision("0001", None, CANARY))
    (versions / "0002_suppliers.py").write_text(revision("0002", "0001"))
    database = project / "shop.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
    if current is not None:
        connection.execute("INSERT INTO alembic_version VALUES (?)", (current,))
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()
    database.chmod(0o600)
    return project, database


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[Shop]:
    project, database = make_project(tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    source = Source(
        profiles={"project:shop": Profile("shop", "sqlite", "dev", path="shop.db")},
        files={"project:shop": database},
    )
    registry = ConnectionRegistry(source)
    code = CodeTrust(project, state, "k1")
    migrations = Migrations(registry, code)
    try:
        yield Shop(project, database, code, registry, migrations, registry.open("project:shop").id)
    finally:
        registry.close_all()


def trust(shop: Shop) -> None:
    state = shop.code.load()
    assert state.scan is not None
    shop.code.trust(state.scan.digest)


def probe(path: Path) -> str:
    """Python source that writes ``path`` when it runs."""
    return f"import pathlib\npathlib.Path({str(path)!r}).write_text('ran')\n"


def bytecode(source: str, target: Path, scratch: Path) -> None:
    """Writes ``target`` as bytecode of ``source`` that the interpreter never checks against a source file."""
    scratch.mkdir(exist_ok=True)
    origin = scratch / f"{target.name}.py"
    origin.write_text(source)
    target.parent.mkdir(exist_ok=True)
    py_compile.compile(
        str(origin), cfile=str(target), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH
    )


def alembic(doc: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in doc["formats"] if item["format"] == "alembic")


def test_untrusted_code_never_runs(shop: Shop) -> None:
    assert shop.code.load().state == "untrusted"  # off by default
    doc = shop.migrations.status(shop.connection_id)
    assert doc["formats"][0] == {"format": "sqlite_user_version", "current": 3}
    found = alembic(doc)
    assert found["code_trust"] == "untrusted" and found["revisions"] == NOT_TRUSTED == "unknown (project not trusted)"
    assert found["current"] == ["0001"]  # the version table, read with SQL
    assert found["heads"] is None and found["pending"] is None and found["is_current"] is None
    assert shop.ran() is None


def test_trusted_code_runs_in_a_separate_process_with_only_the_target(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUDIO_CANARY_SECRET", "canary-value-1234")
    monkeypatch.setenv("PGPASSWORD", "not-for-the-worker")
    state = shop.code.load()
    assert state.scan is not None
    shop.code.trust(state.scan.digest)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["code_trust"] == "trusted" and found["revisions"] == "read" and found["problem"] is None
    assert found["current"] == ["0001"] and found["heads"] == ["0002"] and found["pending"] == ["0002"]
    assert found["is_current"] is False and found["unknown"] == []
    ran = shop.ran()
    assert ran is not None and ran["pid"] != os.getpid()
    # macOS's CoreFoundation sets __CF_USER_TEXT_ENCODING in every process (the user id and the text encoding).
    assert set(ran["env"]) - {"__CF_USER_TEXT_ENCODING"} <= set(migrations_module._WORKER_VARIABLES)
    assert "STUDIO_CANARY_SECRET" not in ran["env"] and "PGPASSWORD" not in ran["env"]
    assert ran["module"] == "shop"  # prepend_sys_path = . (inside the project)
    assert Path(ran["cwd"]).resolve() == shop.project.resolve()


def test_a_changed_revision_file_takes_the_trust_back(shop: Shop) -> None:
    state = shop.code.load()
    assert state.scan is not None
    shop.code.trust(state.scan.digest)
    versions = shop.project / "migrations" / "versions"
    (versions / "0003_extra.py").write_text(revision("0003", "0002", CANARY))
    assert shop.code.load().state == "changed"
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["code_trust"] == "changed" and found["revisions"] == NOT_TRUSTED and found["heads"] is None
    assert shop.ran() is None
    (versions / "0003_extra.py").unlink()
    (shop.project / "alembic.ini").write_text(INI + "# edited\n")
    assert shop.code.load().state == "changed"


def test_the_worker_refuses_files_that_changed_after_the_check(shop: Shop) -> None:
    state = shop.code.load()
    assert state.scan is not None
    request = {
        "target": shop.registry.worker_target(shop.connection_id),
        "project": str(shop.project),
        "script_location": str(state.scan.script_location),
        "sys_path": [],
        "digest": "0" * 64,  # not the files on disk
    }
    assert run_worker(request, cwd=shop.project) == {"error": "studio.migrations_changed"}
    assert shop.ran() is None


@posix
def test_a_slow_revision_file_is_killed_with_its_children(shop: Shop) -> None:
    slow = (
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(__file__).resolve().parents[2].joinpath('pids').write_text(f'{os.getpid()} {child.pid}')\n"
        "time.sleep(60)\n"
    )
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", slow))
    state = shop.code.load()
    assert state.scan is not None
    shop.code.trust(state.scan.digest)
    shop.migrations.runner = lambda request, cwd: run_worker(request, cwd=cwd, timeout_s=3.0)
    started = time.monotonic()
    found = alembic(shop.migrations.status(shop.connection_id))
    assert time.monotonic() - started < 10
    assert found["problem"]["code"] == "studio.migrations_timeout" and found["heads"] is None
    pids = [int(pid) for pid in (shop.project / "pids").read_text().split()]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
        time.sleep(0.1)
    assert not any(_alive(pid) for pid in pids)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")  # a zombie of the killed group runs no code any more
    return not stat.exists() or stat.read_text().rpartition(")")[2].split()[0] != "Z"


def test_a_revision_file_that_fails_is_a_problem_not_a_crash(shop: Shop) -> None:
    broken = 'raise RuntimeError("boom")\n'
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", broken))
    state = shop.code.load()
    assert state.scan is not None
    shop.code.trust(state.scan.digest)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["problem"]["code"] in ("studio.migrations_revisions", "studio.migrations_failed")
    assert found["revisions"] == "unknown (could not be read)"
    assert "boom" not in json.dumps(found)


@posix
@pytest.mark.parametrize("case", ["outside", "link", "too_many"])
def test_invalid_alembic_files_cannot_be_trusted(
    shop: Shop, tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = shop.project / "migrations" / "versions"
    if case == "outside":
        elsewhere = tmp_path / "elsewhere" / "versions"
        elsewhere.mkdir(parents=True)
        (shop.project / "alembic.ini").write_text("[alembic]\nscript_location = ../elsewhere\n")
    elif case == "link":
        target = tmp_path / "outside.py"
        target.write_text(revision("0003", "0002"))
        (versions / "0003_link.py").symlink_to(target)
    else:
        monkeypatch.setattr("tabrion_studio.config.code_trust.MAX_REVISION_FILES", 1)
    state = shop.code.load()
    assert state.state == "invalid" and state.issue is not None
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["code_trust"] == "invalid" and found["revisions"] == "unknown (migrations folder not valid)"
    with pytest.raises(Exception) as raised:
        shop.code.trust("a" * 64)
    assert getattr(raised.value, "code", None) == "studio.migrations_invalid"
    assert shop.ran() is None


def test_a_project_without_alembic_has_only_user_version(shop: Shop) -> None:
    (shop.project / "alembic.ini").unlink()
    assert shop.code.load().state == "none"
    assert shop.migrations.status(shop.connection_id)["formats"] == [{"format": "sqlite_user_version", "current": 3}]


def test_no_version_table_is_current_none(tmp_path: Path) -> None:
    project, database = make_project(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE alembic_version")
    connection.commit()
    connection.close()
    source = Source(
        profiles={"project:shop": Profile("shop", "sqlite", "dev", path="shop.db")}, files={"project:shop": database}
    )
    registry = ConnectionRegistry(source)
    try:
        migrations = Migrations(registry, CodeTrust(project, tmp_path, "k2"))
        assert alembic(migrations.status(registry.open("project:shop").id))["current"] is None
    finally:
        registry.close_all()


def test_the_trust_record_is_bound_to_the_project(shop: Shop) -> None:
    state = shop.code.load()
    assert state.scan is not None
    shop.code.trust(state.scan.digest)
    record = json.loads(shop.code.record.read_text())
    record["project"] = "/somewhere/else"
    shop.code.record.write_text(json.dumps(record))
    assert shop.code.load().state == "untrusted"
    shop.code.record.write_text("not json")
    assert shop.code.load().state == "untrusted"


def test_worker_environment_is_an_allowlist() -> None:
    environ = {"PATH": "/bin", "HOME": "/home/x", "AWS_SECRET_ACCESS_KEY": "s", "PGPASSWORD": "p", "LANG": "C"}
    assert worker_environment(environ) == {"PATH": "/bin", "HOME": "/home/x", "LANG": "C"}


def test_the_code_trust_routes(shop: Shop, tmp_path: Path) -> None:
    studio = make_studio(tmp_path / "static", connections=shop.registry, migrations=shop.migrations)
    headers = {**studio.sign_in(), "Origin": ORIGIN}
    shown = studio.client.get("/api/project/code-trust", headers=headers).json()
    assert shown["state"] == "untrusted" and shown["format"] == "alembic" and "separate process" in shown["notice"]
    assert shown["revision_files"] == ["0001_parts.py", "0002_suppliers.py"]
    assert shown["script_location"] == "migrations"
    stale = studio.client.post("/api/project/code-trust", json={"digest": "b" * 64}, headers=headers)
    assert (stale.status_code, stale.json()["code"]) == (409, "studio.trust_stale")
    assert studio.client.post("/api/project/code-trust", json={"digest": shown["digest"]}).status_code in (401, 403)
    assert (
        studio.client.post("/api/project/code-trust", json={"digest": shown["digest"]}, headers=headers).status_code
        == 204
    )
    assert studio.client.get("/api/project/code-trust", headers=headers).json()["state"] == "trusted"
    status = studio.client.get(f"/api/connections/{shop.connection_id}/migrations", headers=headers)
    assert status.status_code == 200 and alembic(status.json())["heads"] == ["0002"]
    assert studio.client.request("DELETE", "/api/project/code-trust", json={}, headers=headers).status_code == 204
    assert studio.client.get("/api/project/code-trust", headers=headers).json()["state"] == "untrusted"
    gone = studio.client.get("/api/connections/c_nope/migrations", headers=headers)
    assert gone.json()["code"] == "studio.connection_not_found"


# The worker's guards (review of #29).


def test_only_the_hashed_sources_run_never_bytecode(shop: Shop, tmp_path: Path) -> None:
    trust(shop)
    tag = sys.implementation.cache_tag
    ran_instead = shop.project / "bytecode-ran"
    versions = shop.project / "migrations" / "versions"
    scratch = tmp_path / "scratch"
    # Bytecode the digest does not cover: next to a revision file, next to a module on prepend_sys_path, and a module
    # that exists only as bytecode. Adding it changes nothing of the trust.
    bytecode(
        probe(ran_instead) + revision("0002", "0001"), versions / "__pycache__" / f"0002_suppliers.{tag}.pyc", scratch
    )
    bytecode(
        probe(ran_instead) + 'NAME = "bytecode"\n', shop.project / "__pycache__" / f"shop_models.{tag}.pyc", scratch
    )
    bytecode(probe(ran_instead) + 'NAME = "bytecode"\n', shop.project / "shop_compiled.pyc", scratch)
    assert shop.code.load().state == "trusted"
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["revisions"] == "read" and found["heads"] == ["0002"] and found["problem"] is None
    ran = shop.ran()
    assert ran is not None and ran["module"] == "shop" and ran["compiled"] is None
    assert not ran_instead.exists()


def test_only_the_hashed_bytes_run(shop: Shop) -> None:
    """The worker runs the bytes it hashed; a revision file that is not among them is not loaded."""
    from tabrion_studio.services.migration_worker import _hashed_loader

    versions = shop.project / "migrations" / "versions"
    load = _hashed_loader(versions, {"0002_suppliers.py": revision("0002", "0001", "VALUE = 2\n").encode()})
    (versions / "0002_suppliers.py").write_text(revision("0002", "0001", "VALUE = 3\n"))
    assert load(versions, "0002_suppliers.py").VALUE == 2
    with pytest.raises(ImportError):
        load(versions, "0001_parts.py")
    with pytest.raises(ImportError):
        load(shop.project, "0002_suppliers.py")


def test_a_revision_file_changed_during_the_run_is_run_as_hashed(shop: Shop) -> None:
    ran_instead = shop.project / "changed-ran"
    later = shop.project / "migrations" / "versions" / "0002_suppliers.py"
    changed = revision("0002", "0001", probe(ran_instead))
    # Alembic loads the files in name order: the first one changes the second one on disk after the check.
    first = f"import pathlib\npathlib.Path({str(later)!r}).write_text({changed!r})\n"
    (shop.project / "migrations" / "versions" / "0001_parts.py").write_text(revision("0001", None, first))
    trust(shop)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["heads"] == ["0002"] and found["problem"] is None
    assert later.read_text() == changed and not ran_instead.exists()


def test_the_worker_is_isolated_from_the_project_folder(shop: Shop) -> None:
    ran_instead = shop.project / "shadow-ran"
    for name in ("json", "sitecustomize", "usercustomize", "selectors"):
        (shop.project / f"{name}.py").write_text(probe(ran_instead))  # the worker's cwd is the project
    trust(shop)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["heads"] == ["0002"] and found["problem"] is None
    assert not ran_instead.exists()


def test_what_the_project_code_prints_is_not_the_answer(shop: Shop) -> None:
    noisy = (
        "import os, sys\n"
        'os.write(1, b\'{"heads": ["9999"], "current": [], "pending": [], "unknown": []}\\n\')\n'
        'os.write(2, b"noise\\n")\n'
        'print("noise")\n'
    )
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", noisy))
    trust(shop)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["heads"] == ["0002"] and found["pending"] == ["0002"] and found["problem"] is None


def test_nothing_of_the_project_code_runs_after_the_answer(shop: Shop) -> None:
    marker = shop.project / "at-exit"
    hooks = f"import atexit, pathlib\natexit.register(pathlib.Path({str(marker)!r}).write_text, 'ran')\n"
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", hooks))
    trust(shop)
    state = shop.code.load()
    assert state.scan is not None
    request = {
        "target": shop.registry.worker_target(shop.connection_id),
        "project": str(shop.project),
        "script_location": str(state.scan.script_location),
        "sys_path": [str(path) for path in state.scan.sys_path],
        "digest": state.scan.digest,
    }
    # The worker on its own, waited for to its end (run_worker kills it once the answer is in).
    package_root = str(Path(migrations_module.__file__).resolve().parents[2])
    done = subprocess.run(
        [sys.executable, "-I", "-B", "-c", migrations_module._BOOT, package_root],
        input=json.dumps(request).encode(),
        capture_output=True,
        cwd=shop.project,
        env=worker_environment(),
        timeout=30,
        check=False,
    )
    assert json.loads(done.stdout)["heads"] == ["0002"]
    assert not marker.exists()


@pytest.mark.parametrize("ending", ["line", "no_line_end"])
def test_the_answer_is_capped_while_it_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str) -> None:
    assert migrations_module.MAX_ANSWER_BYTES < 20_000 * 100
    big = 'json.dumps({"heads": ["a" * 100] * 20_000})'  # about 2 MB of valid JSON, written by the boot
    tail = "\\n" if ending == "line" else ""
    boot = (
        f"import json, os, sys, time\nsys.stdin.buffer.read()\nos.write(1, {big}.encode() + b'{tail}')\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(migrations_module, "_BOOT", boot)
    started = time.monotonic()
    assert run_worker({}, cwd=tmp_path, timeout_s=20.0) == {"error": "studio.migrations_failed"}
    assert time.monotonic() - started < 10


def test_a_small_answer_from_the_boot_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = "import os, sys\nrequest = sys.stdin.buffer.read()\nos.write(1, b'{\"size\": %d}\\n' % len(request))\n"
    monkeypatch.setattr(migrations_module, "_BOOT", boot)
    request = {"padding": "x" * 300_000}  # more than a pipe holds: written while the answer is awaited
    assert run_worker(request, cwd=tmp_path) == {"size": len(json.dumps(request))}


ESCAPE = (
    "import os, pathlib, time\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    pathlib.Path(__file__).resolve().parents[2].joinpath('escaped').write_text(str(os.getpid()))\n"
    "    time.sleep(60)\n"
    "    os._exit(0)\n"
)


@posix
@pytest.mark.parametrize("worker", ["answers", "hangs"])
def test_a_child_that_left_the_process_group_does_not_hold_studio(shop: Shop, worker: str) -> None:
    body = ESCAPE + ("time.sleep(60)\n" if worker == "hangs" else "")
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", body))
    trust(shop)
    shop.migrations.runner = lambda request, cwd: run_worker(request, cwd=cwd, timeout_s=3.0)
    started = time.monotonic()
    try:
        found = alembic(shop.migrations.status(shop.connection_id))
        assert time.monotonic() - started < 8
        if worker == "answers":
            assert found["heads"] == ["0002"] and found["problem"] is None
        else:
            assert found["problem"]["code"] == "studio.migrations_timeout"
    finally:
        escaped = shop.project / "escaped"
        deadline = time.monotonic() + 5
        while not escaped.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if escaped.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(escaped.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("worker", ["answers", "hangs", "too_big"])
def test_the_reader_thread_used_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: str) -> None:
    write = {
        "answers": "os.write(1, b'{\"size\": %d}\\n' % len(request))\n",
        "hangs": "time.sleep(60)\n",
        "too_big": "os.write(1, b'x' * 3_000_000)\ntime.sleep(60)\n",
    }[worker]
    boot = "import os, sys, time\nrequest = sys.stdin.buffer.read()\n" + write
    monkeypatch.setattr(migrations_module, "_BOOT", boot)
    monkeypatch.setattr(migrations_module, "_exchange_posix", migrations_module._exchange_threads)
    request = {"padding": "x" * 300_000}
    started = time.monotonic()
    answer = run_worker(request, cwd=tmp_path, timeout_s=3.0)
    assert time.monotonic() - started < 8
    expected = {
        "answers": {"size": len(json.dumps(request))},
        "hangs": {"error": "studio.migrations_timeout"},
        "too_big": {"error": "studio.migrations_failed"},
    }
    assert answer == expected[worker]


@posix
def test_children_left_in_the_process_group_end_with_the_answer(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    left = (
        "import os, pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(__file__).resolve().parents[2].joinpath('pids').write_text(str(child.pid))\n"
    )
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", left))
    trust(shop)
    poll = subprocess.Popen.poll

    def poll_after_the_end(process: subprocess.Popen[bytes]) -> int | None:
        process.wait(10)  # the worker has ended by now; its children are still there
        return poll(process)

    monkeypatch.setattr(subprocess.Popen, "poll", poll_after_the_end)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["heads"] == ["0002"] and found["problem"] is None
    pid = int((shop.project / "pids").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    assert not _alive(pid)


def test_one_worker_at_a_time(shop: Shop) -> None:
    trust(shop)
    started, release = threading.Event(), threading.Event()

    def runner(request: dict[str, Any], cwd: Path) -> dict[str, Any]:
        started.set()
        release.wait(10)
        return {"current": ["0001"], "heads": ["0002"], "pending": ["0002"], "is_current": False, "unknown": []}

    shop.migrations.runner = runner
    first: list[dict[str, Any]] = []
    thread = threading.Thread(target=lambda: first.append(shop.migrations.status(shop.connection_id)))
    thread.start()
    try:
        assert started.wait(10)
        busy = alembic(shop.migrations.status(shop.connection_id))
        assert busy["problem"]["code"] == "studio.migrations_busy" and busy["heads"] is None
    finally:
        release.set()
        thread.join(10)
    assert alembic(first[0])["heads"] == ["0002"]
    assert alembic(shop.migrations.status(shop.connection_id))["heads"] == ["0002"]  # the lock was given back


# PostgreSQL, against the server.


@pytest.mark.postgres
@pytest.mark.skipif(PG_URL is None, reason="TABRION_STUDIO_TEST_POSTGRES is not set")
def test_pg_trusted_status_through_the_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert PG_URL is not None
    url = sqlalchemy.make_url(PG_URL)
    project, _ = make_project(tmp_path)
    # Modules the PostgreSQL driver imports when it is first used: the worker loads them before the project's folders
    # are on sys.path, so these files of the same name never run (review M-1).
    shadowed = project / "shadow-ran"
    for name in ("quopri", "psycopg_binary"):
        (project / f"{name}.py").write_text(probe(shadowed))
    admin = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"))
    with admin.begin() as connection:
        if connection.exec_driver_sql("SELECT to_regclass('alembic_version')").scalar() is not None:
            pytest.skip("the test database already has an alembic_version table")
        connection.exec_driver_sql("CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO public.alembic_version VALUES ('0002')")
    profile = Profile(
        "pg", "postgresql", "dev", host=url.host, port=url.port, database=url.database, user=url.username,
        password=SecretRef("keyring", ""), sslmode="prefer",
    )  # fmt: skip
    source = Source(profiles={"user:pg": profile}, passwords={"user:pg": url.password or ""})
    registry = ConnectionRegistry(source)
    monkeypatch.setenv("PGPASSWORD", "not-for-the-worker")
    try:
        code = CodeTrust(project, tmp_path, "k3")
        connection_id = registry.open("user:pg").id
        untrusted = alembic(Migrations(registry, code).status(connection_id))
        assert untrusted["current"] == ["0002"] and untrusted["revisions"] == NOT_TRUSTED
        state = code.load()
        assert state.scan is not None
        code.trust(state.scan.digest)
        found = alembic(Migrations(registry, code).status(connection_id))
        assert found["is_current"] is True and found["heads"] == ["0002"] and found["pending"] == []
        assert "PGPASSWORD" not in json.loads((project / "ran.json").read_text())["env"]
        assert not shadowed.exists()
    finally:
        registry.close_all()
        with admin.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS public.alembic_version")
        admin.dispose()
