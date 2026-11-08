from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

from tabrion_studio import paths

POSIX = sys.platform != "win32"


def test_default_state_dir_honors_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.STATE_DIR_ENV, str(tmp_path / "x"))
    assert paths.default_state_dir() == tmp_path / "x"
    monkeypatch.delenv(paths.STATE_DIR_ENV)
    assert "tabrion-studio" in str(paths.default_state_dir())


def test_private_dir_and_file(tmp_path: Path) -> None:
    folder = paths.ensure_private_dir(tmp_path / "a" / "state")
    target = folder / "info.json"
    paths.write_private_file(target, b"one")
    paths.write_private_file(target, b"two")  # replaces atomically
    assert paths.read_private_file(target) == b"two"
    assert [p.name for p in folder.iterdir()] == ["info.json"]  # no temporary file left
    if POSIX:
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_missing_file_reads_as_none(tmp_path: Path) -> None:
    assert paths.read_private_file(tmp_path / "missing") is None
    paths.remove_file(tmp_path / "missing")  # no error


def test_existing_dir_is_tightened(tmp_path: Path) -> None:
    folder = tmp_path / "loose"
    folder.mkdir()
    os.chmod(folder, 0o755)
    paths.ensure_private_dir(folder)
    if POSIX:
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def test_files_accessible_by_others_are_refused_on_posix(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"x")
    os.chmod(real, 0o644)
    if POSIX:
        with pytest.raises(paths.UnsafePathError, match="accessible by others"):
            paths.read_private_file(real)
    else:
        # Windows has no POSIX modes; the per-user profile ACL of the state directory protects the file.
        assert paths.read_private_file(real) == b"x"


def test_symlinks_are_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"x")
    os.chmod(real, 0o600)
    link = tmp_path / "link"
    linked_dir = tmp_path / "linked"
    link.symlink_to(real)  # GitHub's Windows runners allow symlinks (administrator)
    linked_dir.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(paths.UnsafePathError, match="symlink"):
        paths.read_private_file(link)
    with pytest.raises(paths.UnsafePathError, match="not a plain directory"):
        paths.ensure_private_dir(linked_dir)
