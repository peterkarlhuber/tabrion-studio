"""Smoke test of an unpacked QA kit: install, start (browser launch captured), sign in, stop, uninstall.

Usage: python smoke.py <kit zip> [<parent folder>]
The zip is unpacked below the user's home folder into a folder with a space, umlauts and an apostrophe.
The scripts are run as a user would, with their prompts answered on stdin. BROWSER points to a tiny capture
script, so Studio's launch page is recorded instead of opening a browser.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import zipfile

WINDOWS = os.name == "nt"
PARENT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "Tëst Ördner l'été"
PARENT.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(sys.argv[1]) as archive:
    for member in archive.infolist():
        target = Path(archive.extract(member, PARENT))
        mode = (member.external_attr >> 16) & 0o777
        if mode and not WINDOWS:
            target.chmod(mode)
KIT = PARENT / "TabrionStudio-QA"
os.environ["QA_KIT_NO_TERMINAL"] = "1"
EXT = ".cmd" if WINDOWS else (".command" if sys.platform == "darwin" else ".sh")
CAPTURE = KIT.parent / "launch.txt"
CAPTURE_PY = KIT.parent / "capture.py"


def run(label: str, answer: str = "\n", timeout: int = 900) -> str:
    script = KIT / f"{label}{EXT}"
    cmd = ["cmd", "/c", str(script)] if WINDOWS else ["/bin/bash", str(script)]
    proc = subprocess.run(cmd, input=answer + "\n\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=KIT)
    print(f"--- {label}: exit {proc.returncode}\n{proc.stdout}{proc.stderr}", flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"{label} failed")
    return proc.stdout


def main() -> None:
    CAPTURE_PY.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path({str(CAPTURE)!r}).write_text(sys.argv[1])\n"
    )
    env_browser = f'"{Path(sys.executable).as_posix()}" "{CAPTURE_PY.as_posix()}" %s'

    out = run("1 - Installieren")
    assert "tabrion-studio" in out, "the version line is missing"

    script = KIT / f"2 - Studio starten{EXT}"
    cmd = ["cmd", "/c", str(script)] if WINDOWS else ["/bin/bash", str(script)]
    env = dict(os.environ, BROWSER=env_browser)
    server = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", cwd=KIT, env=env)
    for _ in range(120):
        if CAPTURE.exists() and CAPTURE.read_text().strip():
            break
        time.sleep(0.5)
    else:
        server.kill()
        print(server.stdout.read() if server.stdout else "")
        raise SystemExit("Studio did not open its launch page")
    launch = CAPTURE.read_text().strip()
    print("launch page:", re.sub(r"[0-9a-f]{16,}", "…", launch))
    page = Path(urllib.request.url2pathname(launch.removeprefix("file://").removeprefix("/" if WINDOWS else "")))
    html = page.read_text(encoding="utf-8")
    match = re.search(r"http://127\.0\.0\.1:\d+/[^\"'<>\s]*", html)
    assert match, "no sign-in link in the launch page"
    base = re.match(r"http://127\.0\.0\.1:\d+/", match.group()).group()
    with urllib.request.urlopen(base, timeout=10) as response:
        assert response.status == 200, response.status
    print("server answers on", base)

    run("3 - Studio beenden")
    try:
        server.communicate(input="\n\n", timeout=60)
    except subprocess.TimeoutExpired:
        server.kill()
        raise SystemExit("Studio did not stop")
    print("start script ended with", server.returncode)

    run("4 - Deinstallieren", answer="j")
    leftovers = [p.name for p in (KIT / "_kit").iterdir() if p.name in {"env", "python", "cache", "studio-daten"}]
    if WINDOWS and (Path(os.environ["LOCALAPPDATA"]) / "TabrionStudio-QA").exists():
        leftovers.append("LOCALAPPDATA/TabrionStudio-QA")
    assert not leftovers, f"uninstall left {leftovers}"
    print("SMOKE OK")


if __name__ == "__main__":
    main()
