#!/usr/bin/env python3
"""Install the built wheel into a fresh virtual environment and run it without Node.js (STU-018, STU-017).

    python scripts/smoke_wheel.py dist/tabrion_studio-*.whl

Steps: create a venv in a temporary folder, upgrade pip, install the runtime dependencies from the hashed lock
``constraints/runtime-lock.txt`` (``--lock``; ``pip --require-hashes --no-deps``, TD-031), then the wheel and the
Tabrion pin of ``constraints/tabrion-integration.txt`` (``--tabrion-pin``; Tabrion is not on PyPI) with ``--no-deps``,
so nothing outside the lock is installed, and ``pip check`` that the lock was complete; then
run the installed ``tabrion-studio`` with a PATH from which every folder containing ``node`` is removed:
``--version``, a start with ``--no-browser --print-url``, the health endpoint, the served ``index.html`` and its
script asset, the sign-in with the printed one-time link, a SQLite profile saved, connected (read-only) and read
(the catalog and the rows of a table: the clean-install test of STU-017), a second start (reuses the instance) and
``--stop``. Exit code 0 when everything passed. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

WINDOWS = sys.platform == "win32"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_TABRION_PIN = Path(__file__).resolve().parents[1] / "constraints" / "tabrion-integration.txt"
_LOCK = Path(__file__).resolve().parents[1] / "constraints" / "runtime-lock.txt"
_LINK = re.compile(r"do not share\): (http://127\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+/launch#[A-Za-z0-9_-]+)")


def _bin(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin") / (f"{name}.exe" if WINDOWS else name)


def node_free_path() -> str:
    """PATH without any folder that contains a node executable."""
    keep = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if folder and shutil.which("node", path=folder) is None:
            keep.append(folder)
    path = os.pathsep.join(keep)
    if shutil.which("node", path=path) is not None:
        sys.exit("smoke: could not remove node from PATH")
    return path


def _get(url: str) -> tuple[int, bytes]:
    with _OPENER.open(url, timeout=5) as response:
        return int(response.status), response.read()


def _api(url: str, cookie: str, csrf: str, body: object | None = None) -> Any:
    """One API request of a signed-in tab: the session cookie, the CSRF value and, for a POST, Origin and JSON."""
    origin = url.split("/api/", 1)[0]
    headers = {"Cookie": cookie, "X-Tabrion-CSRF": csrf, "Origin": origin}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with _OPENER.open(request, timeout=10) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        sys.exit(f"smoke: {request.method} {url.removeprefix(origin)} answered {exc.code}: {exc.read()[:300]!r}")


def sqlite_round_trip(link: str, database: Path) -> str:
    """Sign in with the one-time link, save a SQLite profile, connect it and read its catalog and rows."""
    base, _, token = link.partition("/launch#")
    request = urllib.request.Request(
        f"{base}/api/session",
        data=json.dumps({"token": token}).encode("utf-8"),
        headers={"Origin": base, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=10) as response:
            set_cookie = response.headers.get("Set-Cookie")
            doc = json.loads(response.read())
    except (urllib.error.URLError, ValueError) as exc:
        # The token is in the request body, never in this message.
        sys.exit(f"smoke: the sign-in with the one-time link failed: {exc}")
    if not set_cookie or not isinstance(doc, dict) or not isinstance(doc.get("csrf"), str):
        sys.exit("smoke: the sign-in answered without a session cookie or a CSRF value")
    cookie = set_cookie.split(";", 1)[0]
    csrf = doc["csrf"]
    profiles = _api(f"{base}/api/profiles", cookie, csrf)
    digest = profiles["user_file"]["digest"]
    profile = {"name": "smoke", "engine": "sqlite", "environment": "dev", "path": str(database)}
    created = _api(f"{base}/api/profiles", cookie, csrf, {"digest": digest, "profile": profile})
    connection = _api(f"{base}/api/connections", cookie, csrf, {"profile_id": created["id"]})
    connection_id = connection["connection_id"]
    if connection.get("read_only") is not True:
        sys.exit(f"smoke: the SQLite connection is not read-only: {connection}")
    catalog = json.dumps(_api(f"{base}/api/connections/{connection_id}/catalog", cookie, csrf))
    if '"parts"' not in catalog:
        sys.exit(f"smoke: the catalog does not list the table parts: {catalog[:300]}")
    rows = json.dumps(_api(f"{base}/api/connections/{connection_id}/tables/main/parts/rows", cookie, csrf))
    if "Aluminium" not in rows or "Brass" not in rows:
        sys.exit(f"smoke: the rows of parts were not read: {rows[:300]}")
    return "signed in, saved a SQLite profile, connected it read-only, read its catalog and the rows of parts"


def _redacted(log: Path) -> str:
    """The server's output with the one-time sign-in link hidden (every path that prints it)."""
    return _LINK.sub("do not share): <one-time sign-in link>", log.read_text(encoding="utf-8", errors="replace"))


def _step(message: str) -> None:
    print(f"smoke: {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the built wheel without Node.js")
    parser.add_argument("wheel", type=Path)
    parser.add_argument(
        "--tabrion-pin",
        # The old name, from when the file was passed with -c.
        "--constraint",
        dest="tabrion_pin",
        type=Path,
        default=_TABRION_PIN,
        help="the file with the full-SHA Tabrion pin, installed with -r and --no-deps "
        "(default: constraints/tabrion-integration.txt)",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=_LOCK,
        help="the hashed lock of the runtime dependencies (default: constraints/runtime-lock.txt)",
    )
    args = parser.parse_args(argv)
    wheel: Path = args.wheel.resolve()
    if not wheel.is_file():
        sys.exit(f"smoke: no wheel at {wheel}")

    with tempfile.TemporaryDirectory(prefix="tabrion-studio-smoke-") as tmp:
        root = Path(tmp)
        venv, project, state = root / "venv", root / "project", root / "state"
        project.mkdir()
        database = project / "parts.db"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT NOT NULL)")
            db.executemany("INSERT INTO parts (material) VALUES (?)", [("Aluminium",), ("Steel",), ("Brass",)])
        db.close()
        if not WINDOWS:
            database.chmod(0o600)
        _step(f"creating {venv}")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = _bin(venv, "python")
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip>=26.1"], check=True)
        _step(f"installing the runtime dependencies from {args.lock.name} (hashes required)")
        pip = [str(python), "-m", "pip", "install", "--quiet", "--no-deps"]
        subprocess.run(
            [*pip, "--require-hashes", "--only-binary", ":all:", "-r", str(args.lock.resolve())],
            check=True,
        )
        _step(f"installing {wheel.name} and the Tabrion pin of {args.tabrion_pin.name}")
        subprocess.run([*pip, str(wheel), "-r", str(args.tabrion_pin.resolve())], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)

        env = {**os.environ, "PATH": node_free_path(), "PYTHONUNBUFFERED": "1"}
        env.pop("NODE_OPTIONS", None)
        probe = subprocess.run(
            [str(python), "-c", "import shutil; print(shutil.which('node'))"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        if probe.stdout.strip() != "None":
            sys.exit(f"smoke: node is still reachable: {probe.stdout.strip()}")
        _step("node is not on PATH for the following steps")

        studio = str(_bin(venv, "tabrion-studio"))
        base = [studio, str(project), "--no-browser", "--state-dir", str(state)]
        version = subprocess.run([studio, "--version"], env=env, capture_output=True, text=True, check=True)
        _step(version.stdout.strip())

        log = root / "studio.log"
        with log.open("wb") as out:
            began = time.perf_counter()
            process = subprocess.Popen(
                [*base, "--print-url", "--idle-timeout", "0"],
                env={**env, "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"},
                stdout=out,
                stderr=subprocess.STDOUT,
            )
            try:
                url = _wait_ready(state, process, log)
                _step(f"ready at {url} after {time.perf_counter() - began:.2f} s")
                status, body = _get(f"{url}api/health")
                health = json.loads(body)
                if status != 200 or health.get("status") != "ok":
                    sys.exit(f"smoke: unhealthy: {status} {body!r}")
                status, body = _get(url)
                page = body.decode("utf-8")
                if status != 200 or "<title>Tabrion Studio</title>" not in page:
                    sys.exit(f"smoke: index.html not served: {status}")
                assets = re.findall(r'src="/(assets/[^"]+\.js)"', page)
                if not assets:
                    sys.exit("smoke: index.html references no script asset")
                status, _ = _get(url + assets[0])
                if status != 200:
                    sys.exit(f"smoke: asset {assets[0]} not served: {status}")
                _step(f"served index.html and {assets[0]}")
                link = _LINK.search(log.read_text(encoding="utf-8", errors="replace"))
                if link is None:
                    sys.exit("smoke: no one-time sign-in link in the output")
                _step(sqlite_round_trip(link.group(1), database))
                second = subprocess.run(base, env=env, capture_output=True, text=True, check=False)
                if second.returncode != 0 or "already running" not in second.stdout:
                    sys.exit(f"smoke: second start did not reuse the instance: {second.stdout}{second.stderr}")
                _step("second start reused the running instance")
                stop = subprocess.run([*base, "--stop"], env=env, capture_output=True, text=True, check=False)
                if stop.returncode != 0:
                    sys.exit(f"smoke: --stop failed: {stop.stdout}{stop.stderr}")
                code = process.wait(timeout=20)
                if code != 0:
                    sys.exit(f"smoke: exit code {code}")
            finally:
                if process.poll() is None:
                    process.kill()
        _step(_redacted(log).strip().replace("\n", " | "))
    _step("ok")
    return 0


def _wait_ready(state: Path, process: subprocess.Popen[bytes], log: Path) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit(f"smoke: tabrion-studio exited with {process.returncode}: {_redacted(log)}")
        infos = sorted(state.glob("studio-*.json")) if state.is_dir() else []
        if infos:
            try:
                info = json.loads(infos[0].read_text(encoding="utf-8"))
                # A-01: the instance's own random 127.a.b.c (older info files have no "host": 127.0.0.1).
                host = str(info.get("host", "127.0.0.1"))
                if re.fullmatch(r"127\.\d{1,3}\.\d{1,3}\.\d{1,3}", host) is None:
                    raise ValueError(host)
                url = f"http://{host}:{int(info['port'])}/"
                if _get(f"{url}api/health")[0] == 200:
                    return url
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(0.1)
    sys.exit(f"smoke: not ready within 30 s: {_redacted(log)}")


if __name__ == "__main__":
    sys.exit(main())
