"""Detect a sandboxed default browser on Linux (TD-002, review S-4 of the STU-003 review).

Studio opens the browser with the ``file://`` path of a redirect file in the state directory (review F-02). A browser
packaged as a snap (the default Firefox on Ubuntu) or as a Flatpak cannot read hidden folders such as
``~/.local/state``, so it shows an error instead of Studio. This module only finds out whether the default browser is
such a package, so that the command can print a hint; it changes nothing about how the browser is opened.

The default browser comes from ``xdg-settings get default-web-browser`` (a desktop file name such as
``firefox_firefox.desktop``). Its desktop file is looked up in the XDG data directories: a file below
``…/snapd/desktop/applications`` or an ``Exec`` line that starts ``/snap/bin/…`` or ``snap run`` means a snap, a file
below ``…/flatpak/exports/share/applications`` or an ``Exec`` line with ``flatpak run`` means a Flatpak. Anything
unexpected (no ``xdg-settings``, a timeout, an unknown name) means "not detected": the hint is best effort.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Literal

Sandbox = Literal["snap", "flatpak"]

_DESKTOP_NAME = re.compile(r"[A-Za-z0-9._+-]{1,200}\.desktop")
_TIMEOUT_SECONDS = 2.0


def _xdg_default_browser() -> str | None:
    tool = shutil.which("xdg-settings")
    if tool is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 (fixed arguments, resolved tool path, no shell)
            [tool, "get", "default-web-browser"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _data_dirs(env: Mapping[str, str]) -> list[Path]:
    home = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME", "~")).expanduser() / ".local" / "share")
    dirs = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(home), *(Path(d) for d in dirs.split(":") if d)]


def _classify(desktop: Path) -> Sandbox | None:
    text = desktop.as_posix()
    if "/snapd/desktop/applications/" in text:
        return "snap"
    if "/flatpak/exports/share/applications/" in text:
        return "flatpak"
    try:
        content = desktop.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in content.splitlines():
        if not line.startswith("Exec="):
            continue
        command = line.removeprefix("Exec=").strip()
        if command.startswith(("/snap/bin/", "snap run ", "env BAMF_DESKTOP_FILE_HINT=/var/lib/snapd/")):
            return "snap"
        if "flatpak run " in command or command.startswith("/usr/bin/flatpak "):
            return "flatpak"
        return None
    return None


def sandboxed_default_browser(
    env: Mapping[str, str] | None = None,
    query: Callable[[], str | None] = _xdg_default_browser,
    platform: str = sys.platform,
) -> tuple[str, Sandbox] | None:
    """``(desktop file name, "snap" | "flatpak")`` if the default browser is sandboxed, else None.

    Only on Linux, and only when ``$BROWSER`` is not set (Python's ``webbrowser`` uses it before the desktop default).
    """
    env = os.environ if env is None else env
    if not platform.startswith("linux") or env.get("BROWSER"):
        return None
    name = query()
    if name is None or _DESKTOP_NAME.fullmatch(name) is None:
        return None
    for folder in _data_dirs(env):
        desktop = folder / "applications" / name
        if desktop.is_file():
            kind = _classify(desktop)
            return None if kind is None else (name, kind)
    return None


def sandbox_hint(name: str, kind: Sandbox, state_dir: Path) -> str:
    """The hint the command prints before it opens the browser."""
    package = "a snap" if kind == "snap" else "a Flatpak"
    return (
        f"tabrion-studio: your default browser ({name}) is {package} and may not be allowed to open the sign-in "
        f"file in {state_dir}. If the browser shows an error instead of Studio, run tabrion-studio --print-url and "
        "open the printed link by hand, or start Studio with --state-dir set to a folder the browser can read "
        "(not a hidden one)."
    )
