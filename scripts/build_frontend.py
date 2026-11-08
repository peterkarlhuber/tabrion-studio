#!/usr/bin/env python3
"""Build the frontend and copy it into the Python package (05 section 3.4, STU-018).

    python scripts/build_frontend.py            # npm ci + npm run build, then copy frontend/dist
    python scripts/build_frontend.py --no-install   # skip npm ci (node_modules is already current)

The result lands in src/tabrion_studio/static (git-ignored), which the wheel includes. The installed package needs
no Node.js at run time.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
STATIC = ROOT / "src" / "tabrion_studio" / "static"


def _npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("build_frontend: npm is not on PATH (Node.js 22 or later is needed to build the frontend)")
    return npm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--no-install", action="store_true", help="skip `npm ci`")
    args = parser.parse_args(argv)
    npm = _npm()
    if not args.no_install:
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)
    if not (DIST / "index.html").is_file():
        sys.exit(f"build_frontend: {DIST / 'index.html'} was not produced")
    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(DIST, STATIC)
    files = sorted(p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.is_file())
    print(f"build_frontend: copied {len(files)} file(s) to {STATIC.relative_to(ROOT).as_posix()}: {', '.join(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
