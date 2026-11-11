from __future__ import annotations

from collections.abc import Iterator
import gc
import importlib
import importlib.util
from pathlib import Path

import pytest

# The packaging tests run where tabrion_studio is not importable (they only inspect the built wheel and sdist).
_STUDIO_IMPORTABLE = importlib.util.find_spec("tabrion_studio") is not None


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read or write the developer's or the CI runner's OS keyring (STU-007)."""
    if not _STUDIO_IMPORTABLE:
        return
    studio_secrets = importlib.import_module("tabrion_studio.config.secrets")
    monkeypatch.setattr(studio_secrets, "select_backend", lambda: None)


@pytest.fixture(autouse=True)
def collect_garbage_after_each_test() -> Iterator[None]:
    """A connection that a test leaks is finalized (and its ResourceWarning raised) in that test's teardown, not
    during some later test's setup (the ResourceWarning flake of stu-013-review.md)."""
    yield
    gc.collect()


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
