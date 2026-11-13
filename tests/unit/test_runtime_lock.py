"""TD-031 (security review F-6): the hashed runtime lock ``constraints/runtime-lock.txt`` stays in step with the
ranges of ``pyproject.toml`` and with ``build-constraints.txt`` (``scripts/runtime_lock.py check``)."""

from __future__ import annotations

import importlib.util
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
