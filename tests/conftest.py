from __future__ import annotations

from pathlib import Path

import pytest

import tabrion_studio.config.secrets as studio_secrets


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read or write the developer's or the CI runner's OS keyring (STU-007)."""
    monkeypatch.setattr(studio_secrets, "select_backend", lambda: None)


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
