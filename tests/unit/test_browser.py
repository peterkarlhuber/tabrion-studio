"""TD-002, review S-4: the command warns when the default browser is a snap or a Flatpak."""

from __future__ import annotations

import io
import os
from pathlib import Path
import stat
import sys

import pytest

from tabrion_studio import browser, cli


def _desktop(folder: Path, name: str, exec_line: str) -> Path:
    path = folder / "applications" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[Desktop Entry]\nName=Browser\n{exec_line}\nType=Application\n", encoding="utf-8")
    return path


def _env(tmp_path: Path, *dirs: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / "home-share"),
        "XDG_DATA_DIRS": os.pathsep.join(map(str, dirs)),
    }


@pytest.mark.parametrize(
    ("folder", "name", "exec_line", "expected"),
    [
        # Ubuntu's Firefox snap: the desktop file lives in snapd's folder.
        (
            "var/lib/snapd/desktop",
            "firefox_firefox.desktop",
            "Exec=env BAMF_DESKTOP_FILE_HINT=x /snap/bin/firefox %u",
            "snap",
        ),
        # A snap desktop file copied elsewhere is still recognised by its Exec line.
        ("usr/share", "chromium_chromium.desktop", "Exec=/snap/bin/chromium %U", "snap"),
        (
            "var/lib/flatpak/exports/share",
            "org.mozilla.firefox.desktop",
            "Exec=/usr/bin/flatpak run org.mozilla.firefox",
            "flatpak",
        ),
        (
            "usr/share",
            "org.chromium.Chromium.desktop",
            "Exec=/usr/bin/flatpak run --branch=stable org.chromium.Chromium",
            "flatpak",
        ),
        ("usr/share", "firefox.desktop", "Exec=firefox %u", None),
        ("usr/share", "google-chrome.desktop", "Exec=/usr/bin/google-chrome-stable %U", None),
    ],
)
def test_detects_snap_and_flatpak_browsers(
    tmp_path: Path, folder: str, name: str, exec_line: str, expected: str | None
) -> None:
    data = tmp_path / folder
    _desktop(data, name, exec_line)
    found = browser.sandboxed_default_browser(_env(tmp_path, data), query=lambda: name, platform="linux")
    assert found == (None if expected is None else (name, expected))


def test_the_first_desktop_file_on_the_data_path_wins(tmp_path: Path) -> None:
    home = tmp_path / "home-share"
    _desktop(home, "firefox.desktop", "Exec=/usr/lib/firefox/firefox %u")
    _desktop(tmp_path / "usr/share", "firefox.desktop", "Exec=/snap/bin/firefox %u")
    env = _env(tmp_path, tmp_path / "usr/share")
    assert browser.sandboxed_default_browser(env, query=lambda: "firefox.desktop", platform="linux") is None


@pytest.mark.parametrize(
    ("platform", "env_browser", "answer"),
    [
        ("darwin", None, "firefox_firefox.desktop"),
        ("win32", None, "firefox_firefox.desktop"),
        ("linux", "/usr/bin/true", "firefox_firefox.desktop"),  # $BROWSER comes first in webbrowser
        ("linux", None, None),  # no xdg-settings or no answer
        ("linux", None, "../../etc/passwd"),  # not a desktop file name
        ("linux", None, "missing.desktop"),
    ],
)
def test_nothing_is_detected_otherwise(
    tmp_path: Path, platform: str, env_browser: str | None, answer: str | None
) -> None:
    data = tmp_path / "var/lib/snapd/desktop"
    _desktop(data, "firefox_firefox.desktop", "Exec=/snap/bin/firefox %u")
    env = _env(tmp_path, data)
    if env_browser is not None:
        env["BROWSER"] = env_browser
    assert browser.sandboxed_default_browser(env, query=lambda: answer, platform=platform) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script as a fake xdg-settings")
def test_asks_xdg_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "xdg-settings"
    tool.write_text('#!/bin/sh\n[ "$1 $2" = "get default-web-browser" ] && echo firefox_firefox.desktop\n')
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    assert browser._xdg_default_browser() == "firefox_firefox.desktop"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert browser._xdg_default_browser() is None


def test_the_hint_names_the_ways_out(tmp_path: Path) -> None:
    hint = browser.sandbox_hint("firefox_firefox.desktop", "snap", tmp_path)
    assert "firefox_firefox.desktop" in hint and "a snap" in hint
    assert "--print-url" in hint and "--state-dir" in hint and str(tmp_path) in hint
    assert "a Flatpak" in browser.sandbox_hint("org.mozilla.firefox.desktop", "flatpak", tmp_path)


def test_the_command_prints_the_hint_before_opening_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "sandboxed_default_browser", lambda: ("firefox_firefox.desktop", "snap"))
    err = io.StringIO()
    cli._warn_about_a_sandboxed_browser(tmp_path, err)
    assert "firefox_firefox.desktop" in err.getvalue() and "--print-url" in err.getvalue()
    monkeypatch.setattr(cli, "sandboxed_default_browser", lambda: None)
    quiet = io.StringIO()
    cli._warn_about_a_sandboxed_browser(tmp_path, quiet)
    assert quiet.getvalue() == ""


def test_no_hint_with_no_browser(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cli, "_warn_about_a_sandboxed_browser", lambda *_: calls.append(1))
    assert cli.main([str(project), "--stop", "--no-browser", "--state-dir", str(tmp_path / "s")]) == cli.EXIT_OK
    assert calls == []
