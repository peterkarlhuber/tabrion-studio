"""Fetch the uv binary of one platform from the pinned uv wheel on PyPI and check its SHA-256.

Usage: python fetch_uv.py <windows|macos|linux> <uvdl folder>
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.11.32"
# SHA-256 of the uv 0.11.32 wheels on PyPI (checked against PyPI's JSON when the kit was built).
WHEELS = {
    "macosx_10_12_x86_64": "c82954b1bd507e767b1d996c2865ea3eb7d464a479ec0e4d60f0b8e2e07dee45",
    "macosx_11_0_arm64": "2a56d1abf16337350e878b2c869b256b8448ecc4a722b98f8d41e1b3680a587b",
    "manylinux_2_17_x86_64": "3da76cd4e2697de30928b8a8524bd39183ac1e08cb7e72833807c022b7cba6c4",
    "win_amd64": "855632ee1d2a8491986efa54c562b806cd32477761889a1e13624b519d98a45e",
}


SYSTEMS = {
    "windows": {"win_amd64": "windows-x64"},
    "macos": {"macosx_11_0_arm64": "macos-arm64", "macosx_10_12_x86_64": "macos-x64"},
    "linux": {"manylinux_2_17_x86_64": "linux-x64"},
}


def main() -> None:
    for tag, folder in SYSTEMS[sys.argv[1]].items():
        fetch(tag, Path(sys.argv[2]) / folder)


def fetch(tag: str, target: Path) -> None:
    expected = WHEELS[tag]
    with urllib.request.urlopen(f"https://pypi.org/pypi/uv/{VERSION}/json", timeout=60) as response:
        urls = [u for u in json.load(response)["urls"] if tag in u["filename"]]
    if len(urls) != 1 or urls[0]["digests"]["sha256"] != expected:
        raise SystemExit(f"uv {VERSION} {tag}: PyPI does not list the expected file")
    with urllib.request.urlopen(urls[0]["url"], timeout=300) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("uv wheel: SHA-256 mismatch")
    name = "uv.exe" if tag.startswith("win") else "uv"
    member = f"uv-{VERSION}.data/scripts/{name}"
    target.mkdir(parents=True, exist_ok=True)
    out = target / name
    out.write_bytes(zipfile.ZipFile(io.BytesIO(data)).read(member))
    os.chmod(out, 0o755)
    print(f"{out}: {hashlib.sha256(out.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
