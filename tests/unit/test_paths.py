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


def test_existing_dir_readable_by_others_is_tightened(tmp_path: Path) -> None:
    # Nothing secret is in it yet, and nobody else could have written to it: removing the read bits is safe.
    folder = tmp_path / "loose"
    folder.mkdir()
    os.chmod(folder, 0o755)
    paths.ensure_private_dir(folder)
    if POSIX:
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757, 0o720, 0o702])
def test_existing_dir_writable_by_others_is_refused(tmp_path: Path, mode: int) -> None:
    # Review F-10: another user may already have placed files in it; Studio refuses instead of tightening it.
    folder = tmp_path / "shared"
    folder.mkdir()
    os.chmod(folder, mode)
    if POSIX:
        with pytest.raises(paths.UnsafePathError, match="written by other users"):
            paths.ensure_private_dir(folder)
        assert stat.S_IMODE(os.stat(folder).st_mode) == mode  # left as it was
    else:
        # Windows ignores POSIX modes (only the read-only flag); its ACL checks are tested below.
        os.chmod(folder, 0o777)
        paths.ensure_private_dir(folder)


def test_dir_of_another_user_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "theirs"
    folder.mkdir(mode=0o700)
    if POSIX:
        monkeypatch.setattr(os, "getuid", lambda: os.stat(folder).st_uid + 1)
        with pytest.raises(paths.UnsafePathError, match="belongs to another user"):
            paths.ensure_private_dir(folder)
    else:
        assert paths.ensure_private_dir(folder) == folder


def test_lock_file_of_another_user_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabrion_studio import instance

    lock_path = tmp_path / "studio-x.lock"
    lock_path.write_bytes(b"")
    with lock_path.open("r+b") as fh:
        instance._check_lock_file(fh, lock_path)  # our own file
        if POSIX:
            monkeypatch.setattr(os, "getuid", lambda: os.fstat(fh.fileno()).st_uid + 1)
            with pytest.raises(paths.UnsafePathError, match="lock file belongs to another user"):
                instance._check_lock_file(fh, lock_path)


def test_is_within() -> None:
    root = Path(os.path.abspath("profile-root"))
    assert paths.is_within(root, [root])
    assert paths.is_within(root / "a" / "b", [root])
    assert not paths.is_within(Path(str(root) + "-other"), [root])
    assert not paths.is_within(root.parent, [root])
    assert not paths.is_within(root / "a", [])
    assert paths.is_within(root / "a", [Path("/nowhere"), root])


def test_reparse_points_are_detected(tmp_path: Path) -> None:
    class Info:
        st_file_attributes = 0x400

    assert paths._is_reparse_point(Info())  # type: ignore[arg-type]
    assert not paths._is_reparse_point(os.lstat(tmp_path))


def test_profile_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "home" / "AppData" / "Local"))
    roots = paths.profile_roots()
    assert Path(os.path.abspath(tmp_path / "home")) in roots
    assert paths.is_within(tmp_path / "home" / "AppData" / "Local" / "tabrion-studio", roots)
    monkeypatch.delenv("USERPROFILE")
    monkeypatch.delenv("LOCALAPPDATA")
    assert paths.profile_roots() == []


def _run(*args: str) -> None:
    import subprocess

    subprocess.run(list(args), check=True, capture_output=True, text=True)


def test_windows_state_dir_outside_the_profile_needs_a_private_acl(tmp_path: Path) -> None:
    # Review F-07. POSIX has no profile rule (modes and owner are checked instead); there a folder outside the home
    # directory with mode 0700 is fine.
    if POSIX:
        assert paths.ensure_private_dir(tmp_path / "anywhere") == tmp_path / "anywhere"
        return
    import secrets
    import shutil

    drive = Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\")
    outside = drive / f"tabrion-studio-test-{secrets.token_hex(4)}"
    try:
        # Created in the drive root, it inherits that ACL: Authenticated Users may create files and folders.
        with pytest.raises(paths.UnsafePathError, match="outside your user profile and not private"):
            paths.ensure_private_dir(outside)
        # Owner-only: no inheritance, full control for the current user only.
        _run("icacls", str(outside), "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:(OI)(CI)F")
        assert paths.ensure_private_dir(outside) == outside
        _run("icacls", str(outside), "/grant", "*S-1-5-11:(OI)(CI)M")  # Authenticated Users: modify
        with pytest.raises(paths.UnsafePathError, match="grants access to S-1-5-11"):
            paths.ensure_private_dir(outside)
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_windows_junction_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    if POSIX:
        link.symlink_to(target, target_is_directory=True)  # the POSIX counterpart of a junction
        with pytest.raises(paths.UnsafePathError, match="not a plain directory"):
            paths.ensure_private_dir(link)
        return
    _run("cmd", "/c", "mklink", "/J", str(link), str(target))
    with pytest.raises(paths.UnsafePathError, match=r"not a plain directory|junction"):
        paths.ensure_private_dir(link)
    with pytest.raises(paths.UnsafePathError, match="junction or link"):
        paths.ensure_private_dir(link / "state")


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
