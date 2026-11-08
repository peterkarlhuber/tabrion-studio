"""STU-006 review T-2: paths from a project file are judged as text before any file-system access.

Pure path logic is tested with ``PureWindowsPath`` and ``PurePosixPath``, so the Windows forms (UNC, device and
drive-relative paths) are covered on every platform; the walk of real links runs where symlinks exist.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import sys

import pytest

from tabrion_studio.config.locations import (
    contained,
    lexical_target,
    lexically_within,
    network_or_device,
    safe_realpath,
)

POSIX = sys.platform != "win32"
posix_only = pytest.mark.skipif(not POSIX, reason="symlinks")


@pytest.mark.parametrize(
    "text",
    [
        "//attacker.example/share/x.db",
        "\\\\attacker.example\\share\\x.db",
        "\\\\attacker.example@80\\share\\x.db",  # WebDAV
        "/\\attacker.example/share",
        "\\/attacker.example/share",
        "\\\\?\\C:\\data\\x.db",
        "\\\\?\\UNC\\attacker.example\\share\\x",
        "\\\\.\\pipe\\x",
        "\\\\.\\PhysicalDrive0",
        "\\??\\UNC\\attacker.example\\share",  # the NT namespace prefix (re-review R-3)
        "/??/C:/x",
        "C:foo.db",
        "c:",
        "file://attacker.example/s/x",
        "FILE://attacker.example/s/x",
        "file:\\\\attacker.example\\s\\x",
        "///etc/passwd",
        "/net/attacker.example/share/x.db",
        "/NET/attacker.example",
        "/net",
        "/Network/Servers/attacker.example/x",
        "/./net/attacker.example",
        "/srv/../net/attacker.example",
    ],
)
def test_network_device_and_drive_relative_forms_are_refused(text: str) -> None:
    assert network_or_device(text)
    assert lexical_target(text, PureWindowsPath("C:\\work\\project")) is None
    assert lexical_target(text, PurePosixPath("/work/project")) is None


@pytest.mark.parametrize(
    "text",
    ["data/parts.db", "data\\parts.db", "C:\\data\\x.db", "c:/data/x.db", "/etc/x", "file:data.db", "network/x.db"],
)
def test_ordinary_paths_are_not_refused(text: str) -> None:
    assert not network_or_device(text)


@pytest.mark.parametrize(
    ("text", "expected", "inside"),
    [
        ("data\\parts.db", "C:\\work\\project\\data\\parts.db", True),
        ("data/parts.db", "C:\\work\\project\\data\\parts.db", True),
        ("DATA\\Parts.DB", "C:\\work\\project\\DATA\\Parts.DB", True),  # Windows compares case-insensitively
        ("..\\other\\x.db", "C:\\work\\other\\x.db", False),
        ("data\\..\\..\\project2\\x.db", "C:\\work\\project2\\x.db", False),
        ("\\x.db", "C:\\x.db", False),  # root-relative: the drive of the project
        ("D:\\work\\project\\x.db", "D:\\work\\project\\x.db", False),
        ("C:\\WORK\\PROJECT\\x.db", "C:\\WORK\\PROJECT\\x.db", True),
    ],
)
def test_windows_containment_as_text(text: str, expected: str, inside: bool) -> None:
    base = PureWindowsPath("C:\\work\\project")
    target = lexical_target(text, base)
    assert target == PureWindowsPath(expected)
    assert lexically_within(target, [base]) is inside


@pytest.mark.parametrize(
    ("text", "expected", "inside"),
    [
        ("data/parts.db", "/work/project/data/parts.db", True),
        ("./data/../parts.db", "/work/project/parts.db", True),
        ("../other/x.db", "/work/other/x.db", False),
        ("/work/project-evil/x.db", "/work/project-evil/x.db", False),  # a common prefix is not containment
        ("/work/project/x.db", "/work/project/x.db", True),
        ("DATA/x.db", "/work/project/DATA/x.db", True),
        ("/WORK/PROJECT/x.db", "/WORK/PROJECT/x.db", False),  # POSIX compares case-sensitively
        ("data\\x.db", "/work/project/data\\x.db", True),  # a backslash is part of a POSIX name
    ],
)
def test_posix_containment_as_text(text: str, expected: str, inside: bool) -> None:
    base = PurePosixPath("/work/project")
    target = lexical_target(text, base)
    assert target == PurePosixPath(expected)
    assert lexically_within(target, [base]) is inside


def test_roots_of_another_flavour_never_match() -> None:
    assert not lexically_within(PurePosixPath("/work/x"), [PureWindowsPath("C:\\work")])
    assert not lexically_within(PureWindowsPath("C:\\work\\x"), [PurePosixPath("/work")])


def _no_file_system(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every resolve, realpath, lstat, stat and readlink: none may happen for a refused value."""
    calls: list[str] = []
    real_lstat, real_stat, real_readlink = os.lstat, os.stat, os.readlink

    def record(name: str, original: object) -> object:
        def wrapper(path: object, *args: object, **kwargs: object) -> object:
            calls.append(f"{name}({path})")
            return original(path, *args, **kwargs)  # type: ignore[operator]

        return wrapper

    monkeypatch.setattr(Path, "resolve", record("resolve", Path.resolve))
    monkeypatch.setattr(os.path, "realpath", record("realpath", os.path.realpath))
    monkeypatch.setattr(os, "lstat", record("lstat", real_lstat))
    monkeypatch.setattr(os, "stat", record("stat", real_stat))
    monkeypatch.setattr(os, "readlink", record("readlink", real_readlink))
    return calls


@pytest.mark.parametrize("text", ["//attacker.example/share/x.db", "\\\\attacker.example\\s", "C:x", "../../x.db"])
def test_contained_touches_no_file_for_a_refused_or_outside_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls = _no_file_system(monkeypatch)
    assert not contained(text, project, [project], [project])
    assert calls == []


@posix_only
def test_safe_realpath_follows_ordinary_links(tmp_path: Path) -> None:
    (tmp_path / "real" / "sub").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "real" / "rel").symlink_to("sub")
    assert safe_realpath(tmp_path / "link" / "rel" / "x.db") == (tmp_path / "real" / "sub" / "x.db").resolve()
    assert safe_realpath(tmp_path / "link" / "missing" / ".." / "y") == (tmp_path / "real" / "y").resolve()
    assert safe_realpath(PurePath("relative")) is None  # type: ignore[arg-type]


@posix_only
@pytest.mark.parametrize("target", ["//attacker.example/share", "/net/attacker.example/share", "C:x"])
def test_safe_realpath_refuses_a_link_to_a_network_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    (tmp_path / "data").symlink_to(target)
    calls = _no_file_system(monkeypatch)
    assert safe_realpath(tmp_path / "data" / "x.db") is None
    # The link itself was read, but nothing at or below its target was touched.
    assert not any(target in call or "attacker" in call for call in calls)


@posix_only
def test_safe_realpath_stops_on_a_link_loop(tmp_path: Path) -> None:
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    assert safe_realpath(tmp_path / "a" / "x") is None


@posix_only
def test_contained_with_links(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "out").symlink_to(outside)
    (project / "in").symlink_to(project / "data")
    roots = [project]
    assert contained("data/x.db", project, roots, roots)
    assert contained("in/x.db", project, roots, roots)
    assert not contained("out/x.db", project, roots, roots)
    assert not contained(str(outside / "x.db"), project, roots, roots)


@posix_only
def test_a_relative_link_that_climbs_to_net_is_refused_before_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review R-1: on macOS, lstat("/net/<host>") triggers the automount; the walk refuses /net before it."""
    depth = len(tmp_path.parts)
    (tmp_path / "l").symlink_to("../" * depth + "net/attacker.example/share")
    (tmp_path / "m").symlink_to("l")  # the same over two hops
    calls = _no_file_system(monkeypatch)
    assert safe_realpath(tmp_path / "l" / "x.db") is None
    assert safe_realpath(tmp_path / "m" / "x.db") is None
    assert not any("/net" in call for call in calls), calls
