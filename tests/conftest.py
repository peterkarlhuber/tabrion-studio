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


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A minimal built frontend: index.html and one script asset."""
    folder = tmp_path / "static"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><title>Tabrion Studio</title><div id=root></div>")
    (folder / "assets" / "app.js").write_text("console.log(1)")
    return folder
