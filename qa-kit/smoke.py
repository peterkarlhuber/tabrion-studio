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
import threading
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
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.extend(server.stdout), daemon=True)  # type: ignore[arg-type]
    reader.start()
    base = ""
    for _ in range(180):
        found = re.search(r"running at (http://127\.0\.0\.1:\d+/)", "".join(lines))
        if found:
            base = found.group(1)
            break
        time.sleep(0.5)
    print("".join(lines), flush=True)
    if not base:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(server.pid)] if WINDOWS else ["kill", str(server.pid)])
        raise SystemExit("Studio did not start")
    with urllib.request.urlopen(base, timeout=10) as response:
        assert response.status == 200, response.status
    print("server answers on", base)
    for _ in range(60):  # the browser launch: captured where BROWSER is honored, else a real browser opened it
        if CAPTURE.exists() and CAPTURE.read_text().strip():
            launch = CAPTURE.read_text().strip()
            print("launch page:", re.sub(r"[0-9a-f]{16,}", "…", launch))
            page = Path(urllib.request.url2pathname(launch.removeprefix("file://").removeprefix("/" if WINDOWS else "")))
            assert re.search(r"http://127\.0\.0\.1:\d+/", page.read_text(encoding="utf-8")), "no sign-in link"
            break
        time.sleep(0.5)
    else:
        print("BROWSER was not used; the system's default browser opened the launch page instead")

    run("3 - Studio beenden")
    assert server.stdin is not None
    server.stdin.write("\n\n")
    server.stdin.close()
    try:
        server.wait(timeout=60)
    except subprocess.TimeoutExpired:
        raise SystemExit("Studio did not stop")
    reader.join(timeout=10)
    print("start script ended with", server.returncode, "".join(lines[-3:]))

    run("4 - Deinstallieren", answer="j")
    leftovers = [p.name for p in (KIT / "_kit").iterdir() if p.name in {"env", "python", "cache", "studio-daten"}]
    if WINDOWS and (Path(os.environ["LOCALAPPDATA"]) / "TabrionStudio-QA").exists():
        leftovers.append("LOCALAPPDATA/TabrionStudio-QA")
    assert not leftovers, f"uninstall left {leftovers}"
    print("SMOKE OK")


if __name__ == "__main__":
    main()
