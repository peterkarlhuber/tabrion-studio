"""TABRION QA-8 (Studio review L-2): the comparison of ``scripts/check_frontend_reproducible.py`` (no npm needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_frontend_reproducible", ROOT / "scripts" / "check_frontend_reproducible.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def test_identical_trees_have_no_differences(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name / "assets").mkdir(parents=True)
        (tmp_path / name / "index.html").write_text("<html></html>", encoding="utf-8")
        (tmp_path / name / "assets" / "app-1234.js").write_bytes(b"console.log(1)")
    first, second = check.listing(tmp_path / "a"), check.listing(tmp_path / "b")
    assert list(first) == ["assets/app-1234.js", "index.html"]
    assert check.differences(first, second) == []


def test_changed_missing_and_extra_files_are_reported(tmp_path: Path) -> None:
    first = {"index.html": "1", "assets/a.js": "2", "assets/gone.css": "3"}
    second = {"index.html": "1", "assets/a.js": "X", "assets/new.css": "4"}
    assert check.differences(first, second) == ["assets/a.js", "assets/gone.css", "assets/new.css"]
