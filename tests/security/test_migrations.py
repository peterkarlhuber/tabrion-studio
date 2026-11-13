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
import gc
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
import warnings

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


#: A child that writes the time to a file every 0.1 s for a minute: it is alive while the file changes.
HEARTBEAT = (
    "import os, pathlib, subprocess, sys, time\n"
    "beat = pathlib.Path(__file__).resolve().parents[2] / 'beat'\n"
    "code = 'import pathlib, sys, time\\nfor _ in range(600):\\n'\n"
    "code += '    pathlib.Path(sys.argv[1]).write_text(repr(time.time()))\\n    time.sleep(0.1)\\n'\n"
    "subprocess.Popen([sys.executable, '-c', code, str(beat)])\n"
    "time.sleep(60)\n"
)


def test_children_end_with_the_worker_on_every_platform(shop: Shop) -> None:
    """POSIX: the process group; Windows: the job object (TD-024). Checked by a heartbeat file, not by pid."""
    (shop.project / "migrations" / "versions" / "0002_suppliers.py").write_text(revision("0002", "0001", HEARTBEAT))
    trust(shop)
    shop.migrations.runner = lambda request, cwd: run_worker(request, cwd=cwd, timeout_s=4.0)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["problem"]["code"] == "studio.migrations_timeout"
    beat = shop.project / "beat"
    assert beat.exists()  # the child ran
    time.sleep(1.0)
    last = beat.read_text()
    time.sleep(1.0)
    assert beat.read_text() == last  # it stopped beating


def test_no_job_object_outside_windows(shop: Shop) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows makes a job object")
    assert migrations_module._windows_job(None)() is None  # type: ignore[arg-type]


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
@pytest.mark.parametrize("case", ["outside", "link", "too_many", "name"])
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
    elif case == "name":
        (versions / "0003 parts.py").write_text(revision("0003", "0002"))
    else:
        monkeypatch.setattr("tabrion_studio.config.code_trust.MAX_REVISION_FILES", 1)
    state = shop.code.load()
    assert state.state == "invalid" and state.issue is not None
    named = {"link": "migrations/versions/0003_link.py", "name": "migrations/versions/0003 parts.py"}
    assert state.issue_file == named.get(case)  # the file the issue is about, when there is one (I-1)
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
    done = subprocess.run(
        migrations_module.worker_command(),
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


def record_exchanges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Which exchange ``run_worker`` used ("posix" or "windows"): L-1 of stu-29-30-windows-delta-review.md, so that a
    "windows" case that silently ran the POSIX exchange (``_POSIX`` ignored) fails."""
    used: list[str] = []
    real_posix, real_threads = migrations_module._exchange_posix, migrations_module._exchange_threads

    def posix_exchange(*args: Any) -> tuple[bytes | None, threading.Thread | None]:
        used.append("posix")
        return real_posix(*args)

    def threads_exchange(*args: Any) -> tuple[bytes | None, threading.Thread | None]:
        used.append("windows")
        return real_threads(*args)

    monkeypatch.setattr(migrations_module, "_exchange_posix", posix_exchange)
    monkeypatch.setattr(migrations_module, "_exchange_threads", threads_exchange)
    return used


@pytest.mark.parametrize("worker", ["answers", "hangs", "too_big"])
def test_the_reader_thread_used_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: str) -> None:
    write = {
        "answers": "os.write(1, b'{\"size\": %d}\\n' % len(request))\n",
        "hangs": "time.sleep(60)\n",
        "too_big": "os.write(1, b'x' * 3_000_000)\ntime.sleep(60)\n",
    }[worker]
    boot = "import os, sys, time\nrequest = sys.stdin.buffer.read()\n" + write
    monkeypatch.setattr(migrations_module, "_BOOT", boot)
    monkeypatch.setattr(migrations_module, "_POSIX", False)  # the Windows exchange, also on POSIX
    used = record_exchanges(monkeypatch)
    request = {"padding": "x" * 300_000}
    started = time.monotonic()
    answer = run_worker(request, cwd=tmp_path, timeout_s=3.0)
    assert time.monotonic() - started < 8
    assert used == ["windows"]
    expected = {
        "answers": {"size": len(json.dumps(request))},
        "hangs": {"error": "studio.migrations_timeout"},
        "too_big": {"error": "studio.migrations_failed"},
    }
    assert answer == expected[worker]


@pytest.mark.parametrize("exchange", ["native", "windows"])
@pytest.mark.parametrize("worker", ["answers", "hangs", "too_big"])
def test_the_worker_pipes_are_closed_on_every_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exchange: str, worker: str
) -> None:
    """TD-024: both pipes are closed and the worker is reaped when ``run_worker`` returns, also on the Windows path
    (the reader thread's pipe). Nothing is left for a finalizer: no ``ResourceWarning`` when it is collected."""
    write = {
        "answers": "os.write(1, b'{}\\n')\n",
        "hangs": "time.sleep(60)\n",
        "too_big": "os.write(1, b'x' * 3_000_000)\ntime.sleep(60)\n",
    }[worker]
    monkeypatch.setattr(migrations_module, "_BOOT", "import os, sys, time\nsys.stdin.buffer.read()\n" + write)
    if exchange == "windows":
        monkeypatch.setattr(migrations_module, "_POSIX", False)
    used = record_exchanges(monkeypatch)
    started: list[subprocess.Popen[bytes]] = []

    class Recorded(subprocess.Popen[bytes]):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recorded)
    expected = {
        "answers": {},
        "hangs": {"error": "studio.migrations_timeout"},
        "too_big": {"error": "studio.migrations_failed"},
    }
    assert run_worker({}, cwd=tmp_path, timeout_s=2.0) == expected[worker]
    assert used == ["windows" if exchange == "windows" or os.name != "posix" else "posix"]
    [process] = started
    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed
    assert process.returncode is not None
    assert not [thread for thread in threading.enumerate() if thread.name == "tabrion-studio-migration-worker"]
    del process
    started.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        gc.collect()
    assert [str(warning.message) for warning in caught if issubclass(warning.category, ResourceWarning)] == []


@posix
@pytest.mark.parametrize("worker", ["hangs", "exits"])
def test_an_escaped_child_does_not_hold_the_windows_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: str
) -> None:
    """L-2 of stu-29-30-windows-delta-review.md: the Windows exchange without a job object (the no-job fallback of
    TD-024). The worker forks a child that leaves its session and keeps the answer pipe open, so the reader thread
    stays blocked; ``run_worker`` still returns within the timeout plus its bounded reap and join."""
    escape = (
        "import os, pathlib, sys, time\n"
        "sys.stdin.buffer.read()\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    pathlib.Path('escaped').write_text(str(os.getpid()))\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
    )
    monkeypatch.setattr(migrations_module, "_BOOT", escape + ("time.sleep(60)\n" if worker == "hangs" else ""))
    monkeypatch.setattr(migrations_module, "_POSIX", False)
    used = record_exchanges(monkeypatch)
    timeout_s = 2.0
    escaped = tmp_path / "escaped"
    started = time.monotonic()
    try:
        assert run_worker({}, cwd=tmp_path, timeout_s=timeout_s) == {"error": "studio.migrations_timeout"}
        took = time.monotonic() - started
        assert used == ["windows"]
        # The child held the pipe, so the reader was still blocked when run_worker gave up on it: the bound mattered.
        assert [thread for thread in threading.enumerate() if thread.name == "tabrion-studio-migration-worker"]
        assert took < timeout_s + 2 * migrations_module._REAP_S + 2
    finally:
        deadline = time.monotonic() + 5
        while not escaped.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if escaped.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(escaped.read_text()), signal.SIGKILL)
        # The child is gone, so the reader reads the pipe's end, closes it and ends.
        for thread in threading.enumerate():
            if thread.name == "tabrion-studio-migration-worker":
                thread.join(10)
    assert not [thread for thread in threading.enumerate() if thread.name == "tabrion-studio-migration-worker"]


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


# The project modules under the import roots (review finding F-7).


def test_what_the_trust_covers(shop: Shop) -> None:
    (shop.project / "shop_pkg" / "sub").mkdir(parents=True)
    (shop.project / "shop_pkg" / "__init__.py").write_text("")
    (shop.project / "shop_pkg" / "sub" / "rules.py").write_text("RULES = 1\n")
    doc = shop.code.load().to_dict(shop.project)
    assert doc["import_roots"] == ["."]
    assert doc["module_files"] == [
        "migrations/versions/0001_parts.py",
        "migrations/versions/0002_suppliers.py",
        "shop_models.py",
        "shop_pkg/__init__.py",
        "shop_pkg/sub/rules.py",
    ]
    (shop.project / "alembic.ini").write_text(INI.replace("prepend_sys_path = .", "prepend_sys_path = shop_pkg"))
    doc = shop.code.load().to_dict(shop.project)
    assert doc["import_roots"] == ["shop_pkg"] and doc["module_files"] == [
        "shop_pkg/__init__.py",
        "shop_pkg/sub/rules.py",
    ]


def test_a_changed_project_module_takes_the_trust_back(shop: Shop) -> None:
    trust(shop)
    module = shop.project / "shop_models.py"
    module.write_text('NAME = "changed"\n')
    assert shop.code.load().state == "changed"
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["code_trust"] == "changed" and found["revisions"] == NOT_TRUSTED and found["heads"] is None
    assert shop.ran() is None
    module.write_text('NAME = "shop"\n')
    assert shop.code.load().state == "trusted"


@pytest.mark.parametrize(
    "added", ["shop_extra.py", "shop_pkg/__init__.py", "shop_pkg/sub/rules.py", "migrations/helpers.py", "odd-name.py"]
)
def test_a_new_project_module_takes_the_trust_back(shop: Shop, added: str) -> None:
    trust(shop)
    path = shop.project / added
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("VALUE = 1\n")
    assert shop.code.load().state == "changed"
    assert shop.ran() is None


@pytest.mark.parametrize(
    "skipped",
    [
        "__pycache__/shop_models.cpython-313.pyc",
        "shop_models.pyc",
        ".venv/lib/helper.py",
        ".git/hooks/helper.py",
        "node_modules/helper.py",
        "env/site-packages/helper.py",
        "not-a-package/helper.py",
        "notes.txt",
    ],
)
def test_files_that_cannot_be_imported_from_the_roots_are_not_covered(shop: Shop, skipped: str) -> None:
    trust(shop)
    path = shop.project / skipped
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("VALUE = 1\n")
    assert shop.code.load().state == "trusted"


def test_a_virtual_environment_under_a_root_is_not_covered(shop: Shop) -> None:
    trust(shop)
    venv = shop.project / "venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "lib" / "helper.py").write_text("VALUE = 1\n")
    assert shop.code.load().state == "trusted"


@pytest.mark.parametrize(
    ("cap", "value", "issue"),
    [
        ("MAX_MODULE_FILES", 2, "migrations.too_many_modules"),
        ("MAX_SCANNED_ENTRIES", 3, "migrations.too_many_modules"),
        ("MAX_MODULE_BYTES", 10, "migrations.modules_too_large"),
        (None, None, "migrations.modules_too_large"),
    ],
)
def test_project_modules_over_the_caps_cannot_be_trusted(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, cap: str | None, value: int | None, issue: str
) -> None:
    trust(shop)
    if cap is None:
        (shop.project / "shop_big.py").write_text("#" * (256 * 1024 + 1))  # over the per-file cap
    else:
        monkeypatch.setattr(f"tabrion_studio.config.code_trust.{cap}", value)
    state = shop.code.load()
    assert (state.state, state.issue) == ("invalid", issue)  # fail closed: the earlier trust does not hold
    assert state.issue_file == ("shop_big.py" if cap is None else None)  # named when the issue is about one file
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["code_trust"] == "invalid" and found["heads"] is None
    with pytest.raises(Exception) as raised:
        shop.code.trust("a" * 64)
    assert getattr(raised.value, "code", None) == "studio.migrations_invalid"
    assert shop.ran() is None


@posix
@pytest.mark.parametrize("kind", ["file", "folder", "dangling"])
def test_a_link_out_of_the_project_cannot_be_trusted(shop: Shop, tmp_path: Path, kind: str) -> None:
    trust(shop)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shared.py").write_text("VALUE = 1\n")
    if kind == "file":
        (shop.project / "shared.py").symlink_to(outside / "shared.py")
    elif kind == "folder":
        (shop.project / "shared").symlink_to(outside, target_is_directory=True)
    else:
        (shop.project / "shared.py").symlink_to(outside / "gone.py")
    state = shop.code.load()
    assert (state.state, state.issue) == ("invalid", "migrations.link_outside")
    assert state.issue_file == ("shared" if kind == "folder" else "shared.py")
    assert state.to_dict(shop.project)["issue_file"] == state.issue_file
    assert alembic(shop.migrations.status(shop.connection_id))["heads"] is None
    assert shop.ran() is None


@posix
def test_a_link_inside_the_project_is_not_followed(shop: Shop) -> None:
    (shop.project / "alias.py").symlink_to(shop.project / "shop_models.py")
    (shop.project / "loop").symlink_to(shop.project, target_is_directory=True)
    state = shop.code.load()
    assert state.state == "untrusted" and state.scan is not None
    assert "alias.py" not in state.scan.module_files and not any(
        name.startswith("loop/") for name in state.scan.module_files
    )


def test_a_record_of_the_earlier_digest_scheme_needs_trust_again(shop: Shop) -> None:
    trust(shop)
    record = json.loads(shop.code.record.read_text())
    assert record["version"] == 2
    record["version"] = 1  # trusted before the modules were covered: the digest alone must not keep the trust
    shop.code.record.write_text(json.dumps(record))
    assert shop.code.load().state == "changed"
    assert alembic(shop.migrations.status(shop.connection_id))["heads"] is None
    record["version"] = True
    shop.code.record.write_text(json.dumps(record))
    assert shop.code.load().state == "untrusted"
    trust(shop)
    assert shop.code.load().state == "trusted"


def test_the_worker_runs_the_hashed_modules_and_no_other(shop: Shop) -> None:
    ran_instead = shop.project / "module-ran"
    body = (
        "import importlib, json, pathlib\n"
        "here = pathlib.Path(__file__).resolve().parents[2]\n"
        f"here.joinpath('shop_models.py').write_text({probe(ran_instead) + 'NAME = 1'!r})\n"
        f"here.joinpath('late_module.py').write_text({probe(ran_instead)!r})\n"
        "importlib.invalidate_caches()\n"
        "import shop_models\n"
        "try:\n    import late_module\n    late = True\nexcept ImportError:\n    late = False\n"
        "here.joinpath('ran.json').write_text(json.dumps({'module': shop_models.NAME, 'late': late}))\n"
    )
    (shop.project / "migrations" / "versions" / "0001_parts.py").write_text(revision("0001", None, body))
    trust(shop)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["heads"] == ["0002"] and found["problem"] is None
    assert shop.ran() == {"module": "shop", "late": False}  # the hashed bytes; a module added later is not loaded
    assert not ran_instead.exists()


def test_import_roots_and_module_names_on_windows() -> None:
    """``prepend_sys_path`` with drives, and the names and keys of modules with Windows paths."""
    import ntpath

    from tabrion_studio.config.code_trust import _relative, module_key, sys_path_entries

    assert sys_path_entries(r"C:\proj\src;C:/proj/lib, .", windows=True) == [r"C:\proj\src", "C:/proj/lib", "."]
    assert sys_path_entries("src:lib vendor,tools", windows=False) == ["src", "lib", "vendor", "tools"]
    assert _relative(r"C:\Proj\src\pkg\models.py", r"C:\Proj", ntpath) == "src/pkg/models.py"
    assert module_key(r"C:\Proj\SRC\Models.py", ntpath) == module_key(r"c:/proj/src/models.py", ntpath)


# Review C-1 of F-7: project folders that reach the worker through Studio's own environment (an editable install's
# ``.pth`` file or meta path finder) are held to the hashed loader as well. Each test runs the worker with the
# interpreter of a throwaway virtual environment inside the project (``.venv``, skipped by the scan), whose site
# folder holds the ``.pth`` files; the environment is removed with the test's temporary folder.

#: Imports ``name`` from the trusted revision file and records the module's ``NAME`` or the refusal.
ENVIRONMENT_IMPORTS = """
import importlib, json, pathlib
here = pathlib.Path(__file__).resolve().parents[2]
found = {}
for name in NAMES:
    try:
        found[name] = getattr(importlib.import_module(name), "NAME", "imported")
    except ImportError as exc:
        found[name] = "refused: " + str(exc)
here.joinpath("ran.json").write_text(json.dumps(found))
"""
REFUSED = "refused: not a trusted project module"
#: A meta path finder as an editable install puts into the environment: it maps package names to project folders.
EDITABLE_FINDER = """
import importlib.util, sys

MAPPING = {mapping!r}


class EditableFinder:
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        folder = MAPPING.get(fullname)
        if folder is None:
            return None
        return importlib.util.spec_from_file_location(
            fullname, folder + "/__init__.py", submodule_search_locations=[folder]
        )


def install():
    {install}
"""


def environment(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A virtual environment in ``<project>/.venv`` that sees the test's packages; the worker runs with its
    interpreter. Returns its site folder."""
    import site
    import sysconfig
    import venv

    folder = shop.project / ".venv"
    venv.create(folder, with_pip=False, symlinks=os.name != "nt")
    purelib = Path(sysconfig.get_path("purelib", "venv", vars={"base": str(folder), "platbase": str(folder)}))
    purelib.mkdir(parents=True, exist_ok=True)
    # Each site folder of the test's environment with its own .pth files (an editable dependency included).
    lines = [f"import site; site.addsitedir({folder!r})" for folder in site.getsitepackages()]
    (purelib / "_studio_test_packages.pth").write_text("\n".join(lines) + "\n")
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setattr(sys, "executable", str(python))  # the worker runs with this environment's interpreter
    return purelib


def imports(shop: Shop, *names: str) -> None:
    body = f"NAMES = {list(names)!r}\n" + ENVIRONMENT_IMPORTS
    (shop.project / "migrations" / "versions" / "0001_parts.py").write_text(revision("0001", None, body))


def shoplib(shop: Shop) -> Path:
    package = shop.project / "src" / "shoplib"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text('NAME = "hashed"\n')
    return package


def read_status(shop: Shop) -> dict[str, Any] | None:
    shop.canary.unlink(missing_ok=True)
    found = alembic(shop.migrations.status(shop.connection_id))
    assert found["code_trust"] == "trusted" and found["heads"] == ["0002"] and found["problem"] is None
    return shop.ran()


def absent(name: str) -> str:
    return f"refused: No module named {name!r}"


@pytest.mark.parametrize("prepend", [".", None])
def test_a_project_folder_on_the_environment_path_loads_only_hashed_modules(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, prepend: str | None
) -> None:
    """Extension modules (one next to a hashed module) and bytecode without a source, in a project folder that a
    ``.pth`` file of Studio's environment puts on ``sys.path``: never run. Under an import root (``prepend_sys_path =
    .``) the folder gets the finder for hashed sources, so the hashed module is what loads; outside the import roots
    the import guard refuses every module there, since none was hashed."""
    import importlib.machinery

    if prepend is None:
        (shop.project / "alembic.ini").write_text("[alembic]\nscript_location = %(here)s/migrations\n")
    purelib = environment(shop, monkeypatch)
    package = shoplib(shop)
    (purelib / "__editable__.shoplib.pth").write_text(str(shop.project / "src") + "\n")
    names = ("shoplib.models", "shoplib.optional", "flat", "flatopt")
    imports(shop, *names)
    trust(shop)
    scanned = shop.code.load().scan
    first = read_status(shop)
    if prepend is None:
        assert first == dict.fromkeys(names[:2], REFUSED) | {"flat": absent("flat"), "flatopt": absent("flatopt")}
    else:
        assert first == {"shoplib.models": "hashed", **{name: absent(name) for name in names[1:]}}
    ran_instead = shop.project / "unhashed-ran"
    suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    for module in (package / f"models{suffix}", shop.project / "src" / f"flat{suffix}"):
        module.write_bytes(b"\x7fELF not a module")
    for module in (package / "optional.pyc", shop.project / "src" / "flatopt.pyc"):
        bytecode(probe(ran_instead) + 'NAME = "bytecode"\n', module, shop.project / "scratch-pyc")
    state = shop.code.load()
    assert state.state == "trusted" and state.scan == scanned  # none of them is a module the digest covers
    if prepend is None:
        assert read_status(shop) == dict.fromkeys(names, REFUSED)
    else:
        assert read_status(shop) == first  # the hashed module, and only sources are looked for
    assert not ran_instead.exists()


def test_an_unhashed_package_on_the_environment_path_is_refused(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    """A package in a folder the scan skips (not an identifier), put on ``sys.path`` by a second ``.pth`` file, and
    modules there that the environment imports when it starts (``sitecustomize``, an import line of a ``.pth``
    file)."""
    purelib = environment(shop, monkeypatch)
    shoplib(shop)
    (purelib / "__editable__.shoplib.pth").write_text(str(shop.project / "src") + "\n")
    library = shop.project / "packages" / "my-lib" / "src" / "mylib"
    library.mkdir(parents=True)
    (purelib / "__editable__.mylib.pth").write_text(str(library.parent) + "\n")
    imports(shop, "shoplib.models", "mylib")
    trust(shop)
    ran_instead = shop.project / "unhashed-ran"
    (library / "__init__.py").write_text(probe(ran_instead) + 'NAME = "unhashed"\n')
    # Imported when the environment starts: a sitecustomize, and a module a .pth file of the environment imports.
    (library.parent / "sitecustomize.py").write_text(probe(ran_instead))
    (library.parent / "mylib_startup.py").write_text(probe(ran_instead))
    (purelib / "zz_mylib_startup.pth").write_text("import mylib_startup\n")
    assert shop.code.load().state == "trusted"  # the scan skips packages/my-lib
    assert read_status(shop) == {"shoplib.models": "hashed", "mylib": REFUSED}
    assert not ran_instead.exists()


@pytest.mark.parametrize("place", ["append", "front"])
def test_an_editable_meta_path_finder_loads_only_hashed_modules(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, place: str
) -> None:
    """A meta path finder of an editable install that maps packages to project folders, added after the others
    (setuptools, editables) or in front of them (meson-python, scikit-build-core; review C-2): the hashed package is
    loaded from its hashed bytes, a package the digest does not cover is refused."""
    purelib = environment(shop, monkeypatch)
    package = shoplib(shop)
    library = shop.project / "packages" / "my-lib" / "src" / "mylib"
    library.mkdir(parents=True)
    mapping = {"shoplib": str(package), "mylib": str(library)}
    install = "sys.meta_path.append(EditableFinder)" if place == "append" else "sys.meta_path.insert(0, EditableFinder)"
    (purelib / "__editable___studio_test_finder.py").write_text(
        EDITABLE_FINDER.format(mapping=mapping, install=install)
    )
    (purelib / "__editable___studio_test.pth").write_text(
        "import __editable___studio_test_finder; __editable___studio_test_finder.install()\n"
    )
    imports(shop, "shoplib.models", "mylib")
    trust(shop)
    ran_instead = shop.project / "unhashed-ran"
    (library / "__init__.py").write_text(probe(ran_instead) + 'NAME = "unhashed"\n')
    assert shop.code.load().state == "trusted"
    assert read_status(shop) == {"shoplib.models": "hashed", "mylib": REFUSED}
    (package / "models.py").write_text(probe(ran_instead) + 'NAME = "changed"\n')
    assert shop.code.load().state == "changed"  # a hashed module changed: nothing runs until it is trusted again
    assert alembic(shop.migrations.status(shop.connection_id))["heads"] is None
    assert not ran_instead.exists()


def test_studio_from_a_source_checkout_inside_the_project_refuses_to_run(shop: Shop) -> None:
    """Fail closed: when the folder that holds ``tabrion_studio`` lies in the project folder (not in an environment),
    the worker cannot tell Studio's modules from the project's and does not run."""
    import shutil

    import tabrion_studio

    checkout = shop.project / "studio-checkout" / "src"  # not an identifier: the scan skips it
    shutil.copytree(
        Path(tabrion_studio.__file__).resolve().parent,
        checkout / "tabrion_studio",
        ignore=shutil.ignore_patterns("__pycache__", "static"),
    )
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
    command = migrations_module.worker_command()
    done = subprocess.run(
        [*command[:-1], str(checkout)],
        input=json.dumps(request).encode(),
        capture_output=True,
        cwd=shop.project,
        env=worker_environment(),
        timeout=30,
        check=False,
    )
    assert json.loads(done.stdout) == {"error": "studio.migrations_failed"}
    assert shop.ran() is None


def test_a_hashed_namespace_package_loads(shop: Shop) -> None:
    """A package without ``__init__.py`` under an import root (review N-1): its hashed module loads."""
    (shop.project / "nsp").mkdir()
    (shop.project / "nsp" / "mod.py").write_text('NAME = "hashed"\n')
    imports(shop, "nsp.mod", "nsp.late")
    trust(shop)
    assert read_status(shop) == {"nsp.mod": "hashed", "nsp.late": absent("nsp.late")}
