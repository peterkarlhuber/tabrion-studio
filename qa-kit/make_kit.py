"""Build the Tabrion Studio QA-S1 kit for one Studio commit, in one command.

    python make_kit.py <studio-sha> [--studio-repo PATH] [--tabrion-repo PATH] [--keep-work]

Steps (each one stops the build on a mismatch):
 1. A detached, clean worktree of tabrion-studio at <studio-sha> (work/studio-<sha7>).
 2. The tabrion pin is read from that commit's constraints/tabrion-integration.txt (full SHA required), and a clean
    worktree of tabrion at that SHA is made (work/tabrion-<sha7>); the SHA must be on tabrion's origin/master.
 3. Studio's frontend is built (npm ci + build, scripts/build_frontend.py) and both wheels are built with
    `python -m build --wheel` under each repo's hashed build-constraints.txt (PIP_REQUIRE_HASHES=1).
 4. Each wheel's Python files are compared byte for byte with the commit's src/ tree; Studio's static/ with
    frontend/dist; the wheel's tabrion requirement range is printed.
 5. runtime-lock.txt is copied from constraints/ of the commit (and compared after the build).
 6. uv is fetched per platform from the hash-pinned PyPI wheels (fetch_uv.py), the demo is built (build_demo.py),
    and build_kit.py assembles and zips the three kits. The zip SHA-256 values go to out/PRUEFSUMMEN.txt.
Without --keep-work the worktrees, node_modules and build folders are removed at the end.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
WHEELS = HERE / "wheels"
UVDL = HERE / "uvdl"
OUT = HERE / "out"
PIN = re.compile(r"^tabrion @ git\+https://github\.com/peterkarlhuber/tabrion@([0-9a-f]{40})\s*$", re.M)


def run(*cmd: str | Path, cwd: Path | None = None, env: dict[str, str] | None = None, capture: bool = False) -> str:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def worktree(repo: Path, sha: str, name: str) -> Path:
    target = WORK / name
    if not target.exists():
        run("git", "-C", repo, "worktree", "add", "--detach", target, sha)
    head = run("git", "-C", target, "rev-parse", "HEAD", capture=True)
    if head != sha:
        sys.exit(f"{target} is at {head}, not {sha}")
    dirty = run("git", "-C", target, "status", "--porcelain", "--untracked-files=normal", capture=True)
    if dirty:
        sys.exit(f"{target} is not clean:\n{dirty}")
    return target


def full_sha(repo: Path, ref: str) -> str:
    return run("git", "-C", repo, "rev-parse", "--verify", f"{ref}^{{commit}}", capture=True)


def build_python() -> Path:
    """A venv with pip>=26.1 (older pip ignores the hashes in a constraints file) and build 1.6.1, as in Studio's CI."""
    venv = WORK / "buildenv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        run(sys.executable, "-m", "venv", venv)
        run(python, "-m", "pip", "install", "--quiet", "pip>=26.1", "build==1.6.1")
    return python


def build_wheel(src: Path) -> Path:
    dist = src / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    env = dict(os.environ, PIP_CONSTRAINT=str(src / "build-constraints.txt"), PIP_REQUIRE_HASHES="1")
    env.pop("PIP_INDEX_URL", None)
    env.pop("PIP_EXTRA_INDEX_URL", None)
    run(build_python(), "-m", "build", "--wheel", "--outdir", dist, src, env=env)
    wheels = list(dist.glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected one wheel in {dist}, found {wheels}")
    return wheels[0]


def compare_sources(wheel: Path, tree: Path, package: str) -> int:
    """Every .py file of the package in the wheel equals the file in the commit's src/, and vice versa."""
    with zipfile.ZipFile(wheel) as zf:
        in_wheel = {n: zf.read(n) for n in zf.namelist() if n.startswith(f"{package}/") and n.endswith(".py")}
    in_tree = {p.relative_to(tree / "src").as_posix(): p.read_bytes()
               for p in (tree / "src" / package).rglob("*.py")}
    if in_wheel != in_tree:
        diff = sorted(set(in_wheel) ^ set(in_tree)) or [n for n in in_wheel if in_wheel[n] != in_tree.get(n)]
        sys.exit(f"{wheel.name}: Python sources differ from {tree} ({diff[:5]})")
    return len(in_wheel)


def compare_static(wheel: Path, tree: Path) -> int:
    dist = tree / "frontend" / "dist"
    with zipfile.ZipFile(wheel) as zf:
        in_wheel = {n.removeprefix("tabrion_studio/static/"): zf.read(n) for n in zf.namelist()
                    if n.startswith("tabrion_studio/static/") and not n.endswith("/")}
    in_dist = {p.relative_to(dist).as_posix(): p.read_bytes() for p in dist.rglob("*") if p.is_file()}
    if not in_wheel or in_wheel != in_dist:
        sys.exit(f"{wheel.name}: static/ differs from frontend/dist")
    return len(in_wheel)


def requires(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        message = email.parser.Parser().parsestr(zf.read(meta).decode())
    return message.get_all("Requires-Dist") or []


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("studio_sha")
    parser.add_argument("--studio-repo", type=Path, default=HERE.parent / "wt" / "studio-td027")
    parser.add_argument("--tabrion-repo", type=Path, default=Path.home() / "tabrion")
    parser.add_argument("--studio-tree", type=Path, help="an existing clean worktree at <studio-sha> to use (kept)")
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args()

    run("git", "-C", args.studio_repo, "fetch", "origin", "master")
    run("git", "-C", args.tabrion_repo, "fetch", "origin", "master")
    studio_sha = full_sha(args.studio_repo, args.studio_sha)
    on_master = subprocess.run(["git", "-C", str(args.studio_repo), "merge-base", "--is-ancestor", studio_sha,
                                "origin/master"]).returncode == 0
    WORK.mkdir(exist_ok=True)
    if args.studio_tree:
        studio = args.studio_tree.resolve()
        if run("git", "-C", studio, "rev-parse", "HEAD", capture=True) != studio_sha:
            sys.exit(f"{studio} is not at {studio_sha}")
        if run("git", "-C", studio, "status", "--porcelain", capture=True):
            sys.exit(f"{studio} is not clean")
    else:
        studio = worktree(args.studio_repo, studio_sha, f"studio-{studio_sha[:7]}")

    pins = PIN.findall((studio / "constraints" / "tabrion-integration.txt").read_text(encoding="utf-8"))
    if len(pins) != 1:
        sys.exit("constraints/tabrion-integration.txt: expected exactly one full-SHA tabrion pin")
    tabrion_sha = pins[0]
    if full_sha(args.tabrion_repo, tabrion_sha) != tabrion_sha:
        sys.exit(f"tabrion {tabrion_sha} not found")
    if subprocess.run(["git", "-C", str(args.tabrion_repo), "merge-base", "--is-ancestor", tabrion_sha,
                       "origin/master"]).returncode != 0:
        sys.exit(f"tabrion {tabrion_sha} is not on tabrion's origin/master")
    tabrion = worktree(args.tabrion_repo, tabrion_sha, f"tabrion-{tabrion_sha[:7]}")

    run(sys.executable, "scripts/build_frontend.py", cwd=studio)
    studio_wheel = build_wheel(studio)
    tabrion_wheel = build_wheel(tabrion)
    print(f"studio wheel: {compare_sources(studio_wheel, studio, 'tabrion_studio')} .py files and "
          f"{compare_static(studio_wheel, studio)} static files identical to {studio_sha[:7]}")
    print(f"tabrion wheel: {compare_sources(tabrion_wheel, tabrion, 'tabrion')} .py files identical to "
          f"{tabrion_sha[:7]}")
    ranges = [r for r in requires(studio_wheel) if r.startswith("tabrion")]
    print("studio requires:", ranges)

    if WHEELS.exists():
        shutil.rmtree(WHEELS)
    WHEELS.mkdir()
    for wheel in (studio_wheel, tabrion_wheel):
        shutil.copy2(wheel, WHEELS / wheel.name)
    lock = HERE / "runtime-lock.txt"
    shutil.copy2(studio / "constraints" / "runtime-lock.txt", lock)

    if UVDL.exists():
        shutil.rmtree(UVDL)
    for system in ("windows", "macos", "linux"):
        run(sys.executable, HERE / "fetch_uv.py", system, UVDL)
    run(sys.executable, HERE / "build_demo.py", HERE / "beispielprojekt")
    uv_version = re.search(r'^VERSION = "([^"]+)"', (HERE / "fetch_uv.py").read_text(), re.M).group(1)
    info = (f"Studio commit {studio_sha} ({'on master' if on_master else 'NOT on master'}), "
            f"Tabrion commit {tabrion_sha} (Studio's pin), uv {uv_version} from PyPI")
    output = run(sys.executable, HERE / "build_kit.py", lock, info, capture=True)
    print(output)
    if sha256(lock) != sha256(studio / "constraints" / "runtime-lock.txt"):
        sys.exit("runtime-lock.txt changed during the build")
    sums = [f"{sha256(a)}  {a.name}" for a in sorted(OUT.glob("TabrionStudio-QA-*.zip"))]  # sha256sum -c format
    (OUT / "PRUEFSUMMEN.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print("\n".join(sums))
    for wheel in sorted(WHEELS.glob("*.whl")):
        print(f"wheel {sha256(wheel)}  {wheel.name}")

    if not args.keep_work:
        made = [(tabrion, args.tabrion_repo)] + ([] if args.studio_tree else [(studio, args.studio_repo)])
        for tree, repo in made:
            run("git", "-C", repo, "worktree", "remove", "--force", tree)
        if args.studio_tree:  # keep the tree, drop what the build added to it
            for extra in ("frontend/node_modules", "frontend/dist", "src/tabrion_studio/static", "dist", "build"):
                shutil.rmtree(studio / extra, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
