#!/usr/bin/env python3
"""Build the frontend twice and compare the outputs file by file (TABRION QA-8, review L-2 of the Studio PR).

    python scripts/check_frontend_reproducible.py          # needs frontend/node_modules (npm ci) and Node.js 22

The wheel test (`tests/packaging/test_artifacts.py`) packages one frontend build twice, so it shows that hatchling is
deterministic, not that Vite is. This check runs `npm run build` twice from the same sources and lockfile and compares
a sorted SHA-256 listing of `frontend/dist`. It prints the listing, so the listings of two machines (for example a
Linux and a Windows checkout) can be compared as well. It leaves `src/tabrion_studio/static` alone. Exit code 0 when
both builds match, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"


def listing(directory: Path) -> dict[str, str]:
    """``{relative POSIX path: SHA-256}`` of every file below ``directory``."""
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def differences(first: dict[str, str], second: dict[str, str]) -> list[str]:
    """The paths that are missing on one side or have different content, sorted."""
    return sorted(name for name in first.keys() | second.keys() if first.get(name) != second.get(name))


def _build() -> dict[str, str]:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("check_frontend_reproducible: npm is not on PATH (Node.js 22 or later is needed)")
    if DIST.exists():
        shutil.rmtree(DIST)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True, stdout=subprocess.DEVNULL)
    return listing(DIST)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None).parse_args(argv)
    first = _build()
    second = _build()
    for name, digest in second.items():
        print(f"{digest}  {name}")
    changed = differences(first, second)
    if changed:
        print(f"check_frontend_reproducible: the two builds differ in {', '.join(changed)}", file=sys.stderr)
        return 1
    print(f"check_frontend_reproducible: {len(second)} file(s), both builds identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
