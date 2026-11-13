from __future__ import annotations

from collections.abc import Iterator
import gc
import importlib
import importlib.util
import logging
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


#: The loggers whose state a test may change through Uvicorn or ``configure_logging``: those of
#: ``tabrion_studio.logs.OWNED_LOGGERS`` (repeated here: the packaging tests cannot import Studio).
_GUARDED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi", "asyncio")


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    """Every test leaves the logging state as it found it (TD-055).

    A ``uvicorn.Config`` built with its default ``log_config`` runs ``logging.config.dictConfig``: the ``uvicorn``
    logger gets its own stderr handler and ``propagate=False`` for the rest of the process, so that a later test's
    Uvicorn records never reach the handlers its ``configure_logging`` put on the root logger. The root logger's
    handlers are left alone (pytest's own capture handlers come and go there); its level is restored.
    """
    loggers = [logging.getLogger(name) for name in _GUARDED_LOGGERS]
    saved = [(lg, list(lg.handlers), list(lg.filters), lg.level, lg.propagate, lg.disabled) for lg in loggers]
    root_level = logging.root.level
    disabled = logging.root.manager.disable
    yield
    logging.disable(disabled)
    logging.root.setLevel(root_level)
    for logger, handlers, filters, level, propagate, was_disabled in saved:
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = was_disabled


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
