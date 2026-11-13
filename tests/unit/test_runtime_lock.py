"""TD-031 (security review F-6): the hashed runtime lock ``constraints/runtime-lock.txt`` stays in step with the
ranges of ``pyproject.toml`` and with ``build-constraints.txt`` (``scripts/runtime_lock.py check``)."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
HASH = "--hash=sha256:" + "0" * 64


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runtime_lock", ROOT / "scripts" / "runtime_lock.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lock = _load()


def test_the_repository_lock_is_in_step() -> None:
    assert lock.check(ROOT) == []


def test_every_runtime_dependency_is_pinned_with_hashes() -> None:
    pins, problems = lock.read_lock((ROOT / lock.LOCK).read_text(encoding="utf-8"))
    assert problems == []
    names = {pin.name for pin in pins}
    assert {"fastapi", "uvicorn", "pydantic", "keyring", "platformdirs", "psycopg", "psycopg-binary"} <= names
    # tabrion's own runtime dependencies (its extras postgres, sqlalchemy and migrations) are locked too.
    assert {"sqlalchemy", "alembic"} <= names
    assert "tabrion" not in names


@pytest.fixture
def root(tmp_path: Path) -> Path:
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(ROOT / "build-constraints.txt", tmp_path / "build-constraints.txt")
    (tmp_path / "constraints").mkdir()
    shutil.copy(ROOT / lock.LOCK, tmp_path / lock.LOCK)
    return tmp_path


def _edit(root: Path, old: str, new: str) -> None:
    path = root / lock.LOCK
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _line(root: Path, name: str) -> str:
    lines: list[str] = (root / lock.LOCK).read_text(encoding="utf-8").splitlines()
    return next(line for line in lines if line.startswith(name))


def test_a_pin_without_a_hash_is_refused(root: Path) -> None:
    line = _line(root, "fastapi==")
    _edit(root, line, line.split(" --hash")[0])
    assert any("not 'name==version" in p for p in lock.check(root))


def test_a_range_instead_of_a_pin_is_refused(root: Path) -> None:
    _edit(root, "fastapi==", "fastapi>=")
    assert any("not 'name==version" in p for p in lock.check(root))


def test_a_pin_outside_the_pyproject_range_is_refused(root: Path) -> None:
    line = _line(root, "fastapi==")
    _edit(root, line, f"fastapi==0.100.0 {HASH}")
    assert any("outside fastapi" in p for p in lock.check(root))


def test_a_missing_runtime_dependency_is_refused(root: Path) -> None:
    _edit(root, _line(root, "platformdirs==") + "\n", "")
    assert any("platformdirs" in p and "has no pin" in p for p in lock.check(root))


def test_the_psycopg_binary_extra_needs_its_package(root: Path) -> None:
    _edit(root, _line(root, "psycopg-binary==") + "\n", "")
    assert any("needs psycopg-binary" in p for p in lock.check(root))


def test_tabrion_does_not_belong_in_the_lock(root: Path) -> None:
    _edit(root, "alembic==", f"tabrion==0.1.0 {HASH}\nalembic==")
    assert any("tabrion does not belong" in p for p in lock.check(root))


def test_a_name_pinned_twice_is_refused(root: Path) -> None:
    line = _line(root, "fastapi==")
    _edit(root, line, f"{line}\n{line}")
    assert any("pinned twice" in p for p in lock.check(root))


def test_the_build_constraints_must_agree(root: Path) -> None:
    (root / "build-constraints.txt").write_text(f"fastapi==0.115.0 {HASH}\n", encoding="utf-8")
    assert any("build-constraints.txt pins 0.115.0" in p for p in lock.check(root))


def test_markers_are_kept() -> None:
    pins, _ = lock.read_lock(f"pywin32-ctypes==0.2.3 ; sys_platform == 'win32' {HASH}\n")
    (pin,) = pins
    assert pin.applies({"sys_platform": "win32"}) is True
    assert pin.applies({"sys_platform": "linux"}) is False


# check-installed (review L-4): a fake environment instead of importlib.metadata.

FAKE = {
    "tabrion-studio": ("0.1.0", ["alpha>=1", "tabrion[extra]>=0.1,<0.2", "dev-tool>=1; extra == 'dev'"]),
    "tabrion": ("0.1.0", ["beta>=2; extra == 'extra'", "gamma; extra == 'other'"]),
    "alpha": ("1.0", []),
    "beta": ("2.0", []),
}


COMMIT = "52353daee6cd6e37a7efc45b74061a48beb2b280"
REPO = "https://github.com/peterkarlhuber/tabrion"
PIN = f"# a comment\ntabrion @ git+{REPO}@{COMMIT}\n"


def _fake_root(tmp_path: Path, pins: str, tabrion_pin: str = PIN) -> Path:
    (tmp_path / "constraints").mkdir()
    (tmp_path / lock.LOCK).write_text(pins, encoding="utf-8")
    (tmp_path / lock.TABRION_PIN).write_text(tabrion_pin, encoding="utf-8")
    return tmp_path


def _lookup(environment: dict[str, tuple[str, list[str]]]) -> Callable[[str], tuple[str, list[str]] | None]:
    return environment.get


def _direct_url(data: object | None = None) -> Callable[[str], str | None]:
    """A fake ``direct_url.json`` of tabrion; by default the Git commit the fake pin names."""
    if data is None:
        data = {"url": REPO, "vcs_info": {"vcs": "git", "commit_id": COMMIT, "requested_revision": COMMIT}}
    text = data if isinstance(data, str) else json.dumps(data)
    return lambda name: text if name == "tabrion" else None


GIT = _direct_url()


def test_check_installed_accepts_the_exact_closure(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, f"alpha==1.0 {HASH}\nbeta==2.0 {HASH}\n")
    # The dev extra of tabrion-studio and tabrion's other extra are not followed.
    assert lock.check_installed(root, _lookup(FAKE), GIT) == []


def test_check_installed_refuses_another_version(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, f"alpha==1.0 {HASH}\nbeta==2.0 {HASH}\n")
    problems = lock.check_installed(root, _lookup({**FAKE, "beta": ("2.1", [])}), GIT)
    assert problems == [f"beta is installed at 2.1, {lock.LOCK}:2 pins 2.0"]


def test_check_installed_reports_a_missing_package_once(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, f"alpha==1.0 {HASH}\nbeta==2.0 {HASH}\n")
    environment = {name: value for name, value in FAKE.items() if name != "alpha"}
    assert lock.check_installed(root, _lookup(environment), GIT) == ["alpha is required but not installed"]


def test_check_installed_refuses_an_unpinned_dependency(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, f"alpha==1.0 {HASH}\n")
    problems = lock.check_installed(root, _lookup(FAKE), GIT)
    assert problems == [f"beta==2.0 is a runtime dependency here but has no pin in {lock.LOCK}"]


def test_check_installed_refuses_a_stale_pin(tmp_path: Path) -> None:
    root = _fake_root(tmp_path, f"alpha==1.0 {HASH}\nbeta==2.0 {HASH}\nsix==1.17.0 {HASH}\n")
    problems = lock.check_installed(root, _lookup(FAKE), GIT)
    assert problems == [f"{lock.LOCK}:3: six applies here but nothing requires it (stale pin)"]


def test_check_installed_ignores_a_pin_whose_marker_does_not_apply(tmp_path: Path) -> None:
    pins = f"alpha==1.0 {HASH}\nbeta==2.0 {HASH}\nsix==1.17.0 ; sys_platform == 'no-such-platform' {HASH}\n"
    assert lock.check_installed(_fake_root(tmp_path, pins), _lookup(FAKE), GIT) == []


# DC-TS-1: the installed tabrion must come from the commit of constraints/tabrion-integration.txt.

CLEAN = f"alpha==1.0 {HASH}\nbeta==2.0 {HASH}\n"


def test_check_installed_accepts_tabrion_from_the_pinned_commit(tmp_path: Path) -> None:
    assert lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), GIT) == []
    # pip writes the URL as given; a trailing ".git" or "/" names the same repository.
    dotgit = _direct_url({"url": REPO + ".git", "vcs_info": {"vcs": "git", "commit_id": COMMIT}})
    assert lock.check_installed(tmp_path, _lookup(FAKE), dotgit) == []


def test_check_installed_refuses_tabrion_without_direct_url(tmp_path: Path) -> None:
    """A tabrion from a package index has no direct_url.json (the dependency-confusion case)."""
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), lambda name: None)
    assert len(problems) == 1 and problems[0].startswith("tabrion has no direct_url.json"), problems


def test_check_installed_refuses_tabrion_from_a_directory(tmp_path: Path) -> None:
    source = _direct_url({"url": "file:///src/tabrion", "dir_info": {"editable": True}})
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), source)
    assert problems == [
        f"tabrion was installed from 'file:///src/tabrion' (dir_info), not from the Git commit {COMMIT} of "
        f"{lock.TABRION_PIN}"
    ]


def test_check_installed_refuses_tabrion_from_an_archive(tmp_path: Path) -> None:
    source = _direct_url({"url": "https://example.invalid/tabrion-0.1.99.tar.gz", "archive_info": {}})
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), source)
    assert len(problems) == 1 and "(archive_info)" in problems[0], problems


def test_check_installed_refuses_another_commit(tmp_path: Path) -> None:
    other = "0" * 40
    source = _direct_url({"url": REPO, "vcs_info": {"vcs": "git", "commit_id": other}})
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), source)
    assert problems == [f"tabrion is installed at commit {other!r}, {lock.TABRION_PIN} pins {COMMIT}"]


def test_check_installed_refuses_another_repository(tmp_path: Path) -> None:
    source = _direct_url({"url": "https://github.com/someone/tabrion", "vcs_info": {"vcs": "git", "commit_id": COMMIT}})
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), source)
    assert problems == [
        f"tabrion was installed from 'https://github.com/someone/tabrion', {lock.TABRION_PIN} pins {REPO}"
    ]


@pytest.mark.parametrize("text", ["not json", "[]"])
def test_check_installed_refuses_a_broken_direct_url(tmp_path: Path, text: str) -> None:
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN), _lookup(FAKE), _direct_url(text))
    assert len(problems) == 1 and problems[0].startswith("tabrion's direct_url.json is not"), problems


@pytest.mark.parametrize(
    "pin",
    [
        "",
        f"tabrion @ git+{REPO}@master\n",
        f"tabrion @ git+{REPO}@{COMMIT[:12]}\n",
        f"tabrion @ git+{REPO}@{COMMIT}\ntabrion @ git+{REPO}@{'1' * 40}\n",
    ],
)
def test_check_installed_needs_one_full_commit_pin(tmp_path: Path, pin: str) -> None:
    problems = lock.check_installed(_fake_root(tmp_path, CLEAN, pin), _lookup(FAKE), GIT)
    assert problems == [f"{lock.TABRION_PIN} has no single 'tabrion @ git+https://...@<full commit>' line"]


def test_the_repository_pin_is_read() -> None:
    pin = lock.tabrion_pin(ROOT)
    assert pin is not None and pin[0] == REPO and len(pin[1]) == 40
