"""Assemble the Tabrion Studio QA-S1 test kits (one zip per operating system).

Inputs (all under this folder): wheels/ (Studio and Tabrion wheels), uvdl/<platform>/uv[.exe] (uv binaries from the
PyPI wheels, SHA-256 checked when downloaded), src/{posix,windows}/ (the scripts), beispielprojekt/ (the example
project), docs/ (Anleitung.md, QA-S1-Checkliste.md), and the runtime lock of the Studio commit.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = Path(sys.argv[1])  # constraints/runtime-lock.txt of the Studio commit
INFO = sys.argv[2]  # one line: the Studio and Tabrion commits
OUT = HERE / "out"
NAME = "TabrionStudio-QA"

SCRIPTS = {
    "install": "1 - Installieren",
    "start": "2 - Studio starten",
    "stop": "3 - Studio beenden",
    "uninstall": "4 - Deinstallieren",
}
SYSTEMS = {
    "windows": (["windows-x64"], "windows", ".cmd"),
    "macos": (["macos-arm64", "macos-x64"], "posix", ".command"),
    "linux": (["linux-x64"], "posix", ".sh"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(system: str) -> Path:
    platforms, flavor, ext = SYSTEMS[system]
    root = OUT / system / NAME
    if root.parent.exists():
        shutil.rmtree(root.parent)
    kit = root / "_kit"
    (kit / "wheels").mkdir(parents=True)
    src = HERE / "src" / flavor
    for key, label in SCRIPTS.items():
        text = (src / f"{key}{'.cmd' if flavor == 'windows' else '.sh'}").read_text(encoding="utf-8")
        if flavor == "windows":
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        (root / f"{label}{ext}").write_bytes(text.encode())
    common = (src / ("common.cmd" if flavor == "windows" else "common.sh")).read_text(encoding="utf-8")
    if flavor == "windows":
        common = common.replace("\r\n", "\n").replace("\n", "\r\n")
    (kit / ("common.cmd" if flavor == "windows" else "common.sh")).write_bytes(common.encode())
    for platform in platforms:
        target = kit / "bin" / platform
        target.mkdir(parents=True)
        for binary in (HERE / "uvdl" / platform).iterdir():
            shutil.copy2(binary, target / binary.name)
    for wheel in sorted((HERE / "wheels").glob("*.whl")):
        shutil.copy2(wheel, kit / "wheels" / wheel.name)
    shutil.copy2(LOCK, kit / "runtime-lock.txt")
    shutil.copytree(HERE / "beispielprojekt", root / "Beispielprojekt")
    for doc in ("Anleitung.md", "QA-S1-Checkliste.md"):
        if (HERE / "docs" / doc).exists():
            shutil.copy2(HERE / "docs" / doc, root / doc)
    lines = [
        "Tabrion Studio, local test version for QA-S1 only. Not a release; nothing is published.",
        INFO,
        "Files (SHA-256):",
    ]
    for path in sorted(p for p in kit.rglob("*") if p.is_file()):
        lines.append(f"  {sha256(path)}  {path.relative_to(root).as_posix()}")
    (kit / "KIT-INFO.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def pack(system: str, root: Path) -> Path:
    archive = OUT / f"{NAME}-{system}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(root.rglob("*")):
            arcname = path.relative_to(root.parent).as_posix()
            info = zipfile.ZipInfo(arcname + ("/" if path.is_dir() else ""), date_time=(2026, 11, 13, 0, 0, 0))
            info.create_system = 3  # Unix, so that the mode bits below are honored by unzip tools
            executable = path.is_file() and (
                path.suffix in {".command", ".sh"} or (path.name == "uv" and system != "windows")
            )
            mode = 0o40755 if path.is_dir() else (0o100755 if executable else 0o100644)
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, b"" if path.is_dir() else path.read_bytes())
    return archive


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for system in sys.argv[3:] or list(SYSTEMS):
        archive = pack(system, stage(system))
        print(f"{archive.name}  {archive.stat().st_size // 1024} KiB  sha256 {sha256(archive)}")


if __name__ == "__main__":
    main()
