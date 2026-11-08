from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"
