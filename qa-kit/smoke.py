"""Smoke test of an unpacked QA kit: install, start (browser launch captured), sign in, trust the demo project,
open "Shop (DEV)" and check the vector data view (STU-055), stop, uninstall.

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

import html
import json
import urllib.error
import urllib.parse
import zipfile

WINDOWS = os.name == "nt"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
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
# Studio is on a loopback address: never send these requests through an HTTP(S)_PROXY of the environment.
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run(label: str, answer: str = "\n", timeout: int = 900) -> str:
    script = KIT / f"{label}{EXT}"
    cmd = ["cmd", "/c", str(script)] if WINDOWS else ["/bin/bash", str(script)]
    proc = subprocess.run(cmd, input=answer + "\n\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=KIT)
    print(f"--- {label}: exit {proc.returncode}\n{proc.stdout}{proc.stderr}", flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"{label} failed")
    return proc.stdout


def api(base: str, method: str, path: str, session: dict[str, str], body: object = None) -> tuple[int, object]:
    """One request to Studio as the signed-in page sends it."""
    origin = base.rstrip("/")
    headers = {"Origin": origin, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", **session}
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(origin + path, data=data, method=method, headers=headers)
    try:
        with LOCAL.open(request, timeout=60) as response:
            raw, status, cookie = response.read(), response.status, response.headers.get("Set-Cookie")
    except urllib.error.HTTPError as exc:
        raw, status, cookie = exc.read(), exc.code, None
    if cookie:
        session["Cookie"] = cookie.split(";", 1)[0]
    return status, (json.loads(raw) if raw else None)


def check_vectors(base: str, launch_url: str) -> None:
    """Sign in with the captured launch token, trust the demo project, open "Shop (DEV)" and read the vector view
    (STU-055): the demo's Orkexa-layout table must show up as checklist S1-35/S1-36 expect."""
    session: dict[str, str] = {}
    status, body = api(base, "POST", "/api/session", session, {"token": launch_url.split("#", 1)[1]})
    assert status == 200 and "Cookie" in session, (status, body)
    session["X-Tabrion-CSRF"] = body["csrf"]  # type: ignore[index]
    status, project = api(base, "GET", "/api/project", session)
    assert status == 200, (status, project)
    status, body = api(base, "POST", "/api/project/trust", session, {"digest": project["digest"]})  # type: ignore[index]
    assert status == 204, (status, body)
    status, profiles = api(base, "GET", "/api/profiles", session)
    dev = next(p for p in profiles["profiles"] if p["name"] == "Shop (DEV)")  # type: ignore[index]
    status, opened = api(base, "POST", "/api/connections", session,
                         {"profile_id": dev["id"], "confirmed_environment": "dev"})
    assert status == 201, (status, opened)
    cid = urllib.parse.quote(opened["connection_id"], safe="")  # type: ignore[index]
    status, report = api(base, "GET", f"/api/connections/{cid}/vectors", session)
    assert status == 200, (status, report)
    columns = [(c["table"], c["column"], c["type"], c["layout"], c["dimensions"], c["search"], c["rows"])
               for c in report["columns"]]  # type: ignore[index]
    identities = [(i["space"], i["model"], i["provider"], i["dimensions"], i["state"], i["complete"])
                  for i in report["identities"]]  # type: ignore[index]
    hints = sorted(h["code"] for h in report["hints"])  # type: ignore[index]
    print("vectors:", report["extension"]["state"], columns, identities, hints)  # type: ignore[index]
    assert report["extension"]["state"] == "not_determinable"  # type: ignore[index]
    assert columns == [("index_vectors", "embedding", "BLOB", "orkexa", 8, "exact", {"value": 120, "kind": "exact"})]
    assert identities == [("produkte", "shop-embed-mini", "beispiel", 8, "active", True)]
    assert hints == ["exact_search", "sqlite_vec_undeterminable"], hints


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
        found = re.search(r"running at (http://127\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+/)", "".join(lines))  # A-01
        if found:
            base = found.group(1)
            break
        time.sleep(0.5)
    print("".join(lines), flush=True)
    if not base:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(server.pid)] if WINDOWS else ["kill", str(server.pid)])
        raise SystemExit("Studio did not start")
    with LOCAL.open(base, timeout=10) as response:
        assert response.status == 200, response.status
    print("server answers on", base)
    for _ in range(60):  # the browser launch: captured where BROWSER is honored, else a real browser opened it
        if CAPTURE.exists() and CAPTURE.read_text().strip():
            launch = CAPTURE.read_text().strip()
            print("launch page:", re.sub(r"[0-9a-f]{16,}", "…", launch))
            page = Path(urllib.request.url2pathname(launch.removeprefix("file://").removeprefix("/" if WINDOWS else "")))
            text = page.read_text(encoding="utf-8")
            assert base in text, "no sign-in link to the printed address"
            found = re.search(r'url=(http://[^"]+/launch#[^"]+)"', text)
            assert found, "no launch URL in the launch page"
            check_vectors(base, html.unescape(found.group(1)))
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
