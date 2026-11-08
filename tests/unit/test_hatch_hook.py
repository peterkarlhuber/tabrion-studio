from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hatch_build import CustomBuildHook  # noqa: E402

sys.path.remove(str(ROOT))


def make_hook(root: Path, target: str) -> Any:
    # Only root and target_name matter to the hook; the build configuration and metadata are not read.
    return CustomBuildHook(str(root), {}, None, None, str(root / "dist"), target)  # type: ignore[arg-type]


def test_wheel_without_frontend_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="build the frontend first"):
        make_hook(tmp_path, "wheel").initialize("standard", {})


def test_wheel_with_frontend_passes(tmp_path: Path) -> None:
    static = tmp_path / "src" / "tabrion_studio" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>")
    make_hook(tmp_path, "wheel").initialize("standard", {})


def test_editable_and_sdist_are_not_checked(tmp_path: Path) -> None:
    make_hook(tmp_path, "wheel").initialize("editable", {})
    make_hook(tmp_path, "sdist").initialize("standard", {})
