#!/usr/bin/env python3
"""Install the built wheel into a fresh virtual environment and run it without Node.js (STU-018).

    python scripts/smoke_wheel.py dist/tabrion_studio-*.whl

Steps: create a venv in a temporary folder, ``pip install`` the wheel (its dependencies come from PyPI and, until
tabrion v0.1.0, the git pin of tabrion), then run the installed ``tabrion-studio`` with a PATH from which every
folder containing ``node`` is removed: ``--version``, a start with ``--no-browser``, the health endpoint, the
served ``index.html`` and its script asset, a second start (reuses the instance) and ``--stop``. Exit code 0 when
everything passed. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

WINDOWS = sys.platform == "win32"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def _step(message: str) -> None:
    print(f"smoke: {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the built wheel without Node.js")
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args(argv)
    wheel: Path = args.wheel.resolve()
    if not wheel.is_file():
        sys.exit(f"smoke: no wheel at {wheel}")

    with tempfile.TemporaryDirectory(prefix="tabrion-studio-smoke-") as tmp:
        root = Path(tmp)
        venv, project, state = root / "venv", root / "project", root / "state"
        project.mkdir()
        _step(f"creating {venv}")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = _bin(venv, "python")
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
        _step(f"installing {wheel.name}")
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", str(wheel)], check=True)
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
            process = subprocess.Popen([*base, "--idle-timeout", "0"], env=env, stdout=out, stderr=subprocess.STDOUT)
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
        _step(log.read_text(encoding="utf-8", errors="replace").strip().replace("\n", " | "))
    _step("ok")
    return 0


def _wait_ready(state: Path, process: subprocess.Popen[bytes], log: Path) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit(f"smoke: tabrion-studio exited with {process.returncode}: {log.read_text(errors='replace')}")
        infos = sorted(state.glob("studio-*.json")) if state.is_dir() else []
        if infos:
            try:
                info = json.loads(infos[0].read_text(encoding="utf-8"))
                url = f"http://127.0.0.1:{int(info['port'])}/"
                if _get(f"{url}api/health")[0] == 200:
                    return url
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(0.1)
    sys.exit(f"smoke: not ready within 30 s: {log.read_text(errors='replace')}")


if __name__ == "__main__":
    sys.exit(main())
