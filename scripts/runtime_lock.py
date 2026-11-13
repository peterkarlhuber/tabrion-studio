#!/usr/bin/env python3
"""TD-031 (security review F-6, TABRION QA-8): the hashed lock of Studio's Python runtime dependencies.

``pyproject.toml`` keeps the supported ranges (``fastapi>=0.115,<1``, ...). ``constraints/runtime-lock.txt`` pins the
whole runtime closure for Python 3.11 to 3.14 on Linux, macOS and Windows to exact versions with the SHA-256 hash of
every file, so ``pip install --require-hashes`` installs only reviewed files. tabrion is the one exception: it is not on
PyPI and is pinned by its full commit SHA in ``constraints/tabrion-integration.txt`` (pip cannot hash a Git checkout).

    python scripts/runtime_lock.py check            # the lock is well formed and inside the pyproject.toml ranges
    python scripts/runtime_lock.py check-installed  # this environment's runtime closure is exactly the lock
    python scripts/runtime_lock.py update           # regenerate the lock (needs uv on PATH and network access)

``check`` refuses a line that is not ``name==version [; marker] --hash=sha256:...`` (one line each, at least one
hash), a name pinned twice for the same marker, a tabrion line, a runtime dependency of ``pyproject.toml`` that has
no pin or a pin outside its range (extras included: ``psycopg[binary]`` needs ``psycopg-binary``), and a package pinned
to another version in ``build-constraints.txt`` (the second hashed pin set, the build backend).

``check-installed`` walks the requirements of the installed ``tabrion-studio`` (without extras) and of everything they
pull in, with the markers of this interpreter, and refuses a distribution that is not in the lock or installed at
another version, and a lock entry that applies here but is not part of the closure (a stale pin). CI runs it after
installing the development tools, so a tool that moved a runtime dependency fails the job.

``update`` writes the lock from the ranges of ``pyproject.toml`` and the tabrion pin with ``uv pip compile --universal
--generate-hashes``. It keeps every current pin that still fits and adds what is missing; ``update -- --upgrade``
moves every pin to the newest version inside the ranges, ``update -- --upgrade-package fastapi`` only that one.
Exit codes: 0 clean, 1 findings, 2 usage or tool errors.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib.metadata
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
LOCK = Path("constraints/runtime-lock.txt")
TABRION_PIN = Path("constraints/tabrion-integration.txt")
BUILD_CONSTRAINTS = Path("build-constraints.txt")
#: The oldest supported Python (``requires-python``); a universal lock covers it and every newer one.
LOWEST_PYTHON = "3.11"
#: Not pinned in the lock: Studio itself and tabrion (a full-SHA Git pin, TABRION_PIN).
UNLOCKED = frozenset({"tabrion-studio", "tabrion"})
#: Extras of pyproject.toml whose package must be pinned too (the lock cannot read extras from metadata offline).
EXTRA_PACKAGES = {("psycopg", "binary"): "psycopg-binary"}

_HASH = r"--hash=sha256:[0-9a-f]{64}"
_LINE = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9.+!_-]+)"
    r"(?:\s*;\s*(?P<marker>.+?))?"
    rf"(?P<hashes>(?:\s+{_HASH})+)"
)

HEADER = """\
# Hashed lock of Studio's Python runtime dependencies (TD-031, security review F-6, TABRION QA-8).
#
# Every runtime dependency of tabrion-studio and of tabrion, for Python 3.11 to 3.14 on Linux, macOS and Windows,
# pinned exactly with the SHA-256 of every file on PyPI. pyproject.toml keeps the supported ranges; this file is one
# tested set inside them. tabrion is not here: pip cannot hash a Git checkout, so constraints/tabrion-integration.txt
# pins it by its full commit SHA. Install (docs/guide/installation.md, the CI jobs do the same):
#
#   python -m pip install --require-hashes --no-deps --only-binary :all: -r constraints/runtime-lock.txt
#   python -m pip install --no-deps . -r constraints/tabrion-integration.txt
#   python -m pip check
#
# Do not edit by hand. To update: python scripts/runtime_lock.py update (needs uv), then
# python scripts/runtime_lock.py check; docs/development/ci.md, "Hashed runtime pins".
"""


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    marker: Marker | None
    line: int

    def applies(self, environment: dict[str, str] | None = None) -> bool:
        return self.marker is None or self.marker.evaluate(environment)


def read_lock(text: str) -> tuple[list[Pin], list[str]]:
    pins: list[Pin] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            problems.append(f"{LOCK}:{number}: not 'name==version [; marker] --hash=sha256:...' on one line")
            continue
        name = canonicalize_name(match["name"])
        marker_text = (match["marker"] or "").strip()
        if name in UNLOCKED:
            problems.append(f"{LOCK}:{number}: {name} does not belong in the lock")
            continue
        if (name, marker_text) in seen:
            problems.append(f"{LOCK}:{number}: {name} is pinned twice for the same marker")
        seen.add((name, marker_text))
        pins.append(Pin(name, match["version"], Marker(marker_text) if marker_text else None, number))
    if not pins and not problems:
        problems.append(f"{LOCK} pins nothing")
    return pins, problems


def runtime_requirements(pyproject: str) -> list[Requirement]:
    project = tomllib.loads(pyproject)["project"]
    return [Requirement(text) for text in project.get("dependencies", [])]


def build_pins(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in text.splitlines():
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", raw.strip())
        if match:
            found[canonicalize_name(match[1])] = match[2]
    return found


def check(root: Path = ROOT) -> list[str]:
    """Problems of the lock alone and against pyproject.toml and build-constraints.txt (empty list = in step)."""
    pins, problems = read_lock((root / LOCK).read_text(encoding="utf-8"))
    by_name: dict[str, list[Pin]] = {}
    for pin in pins:
        by_name.setdefault(pin.name, []).append(pin)
    for requirement in runtime_requirements((root / "pyproject.toml").read_text(encoding="utf-8")):
        name = canonicalize_name(requirement.name)
        if name in UNLOCKED:
            continue
        if name not in by_name:
            problems.append(f"{requirement} (pyproject.toml) has no pin in {LOCK}")
            continue
        for pin in by_name[name]:
            if not requirement.specifier.contains(Version(pin.version), prereleases=True):
                problems.append(f"{LOCK}:{pin.line}: {name}=={pin.version} is outside {requirement} (pyproject.toml)")
        for extra in sorted(requirement.extras):
            needed = EXTRA_PACKAGES.get((name, extra))
            if needed is not None and needed not in by_name:
                problems.append(f"{requirement} (pyproject.toml) needs {needed} in {LOCK}")
    build = build_pins((root / BUILD_CONSTRAINTS).read_text(encoding="utf-8"))
    for shared in sorted(set(build) & set(by_name)):
        for pin in by_name[shared]:
            if pin.version != build[shared]:
                problems.append(
                    f"{LOCK}:{pin.line}: {shared}=={pin.version}, but {BUILD_CONSTRAINTS} pins {build[shared]}"
                )
    return problems


#: An installed distribution's version and requirement strings, or None when it is not installed.
Lookup = Callable[[str], "tuple[str, list[str]] | None"]


def installed_distribution(name: str) -> tuple[str, list[str]] | None:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    return distribution.version, list(distribution.requires or [])


def _closure(root_name: str, lookup: Lookup) -> tuple[dict[str, str], set[str]]:
    """Installed name -> version for ``root_name`` and its runtime requirements, transitively, with extras; and the
    names that are required but not installed."""
    found: dict[str, str] = {}
    missing: set[str] = set()
    queue: list[tuple[str, frozenset[str]]] = [(root_name, frozenset())]
    done: set[tuple[str, frozenset[str]]] = set()
    while queue:
        name, extras = queue.pop()
        if (name, extras) in done:
            continue
        done.add((name, extras))
        distribution = lookup(name)
        if distribution is None:
            missing.add(name)
            continue
        version, requires = distribution
        found[canonicalize_name(name)] = version
        for text in requires:
            requirement = Requirement(text)
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in (extras or {""})
            ):
                continue
            queue.append((canonicalize_name(requirement.name), frozenset(requirement.extras)))
    return found, missing


def check_installed(root: Path = ROOT, lookup: Lookup = installed_distribution) -> list[str]:
    """Problems between this environment's runtime closure of tabrion-studio and the lock (empty list = equal)."""
    pins, problems = read_lock((root / LOCK).read_text(encoding="utf-8"))
    applicable = {pin.name: pin for pin in pins if pin.applies()}
    installed, missing = _closure("tabrion-studio", lookup)
    problems += [f"{name} is required but not installed" for name in sorted(missing)]
    for name, version in sorted(installed.items()):
        if name in UNLOCKED:
            continue
        pin = applicable.get(name)
        if pin is None:
            problems.append(f"{name}=={version} is a runtime dependency here but has no pin in {LOCK}")
        elif Version(version) != Version(pin.version):
            problems.append(f"{name} is installed at {version}, {LOCK}:{pin.line} pins {pin.version}")
    for name, pin in sorted(applicable.items()):
        # A required pin that is not installed is reported above, not as stale (review L-5).
        if name not in installed and name not in missing:
            problems.append(f"{LOCK}:{pin.line}: {name} applies here but nothing requires it (stale pin)")
    return problems


def _one_line(compiled: str) -> Iterable[str]:
    for raw in compiled.replace("\\\n", " ").splitlines():
        line = " ".join(raw.split())
        if line and not line.startswith("#") and canonicalize_name(re.split(r"[\s=@;\[]", line)[0]) not in UNLOCKED:
            yield line


def update(root: Path = ROOT, extra_args: list[str] | None = None) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("update needs uv on PATH (https://docs.astral.sh/uv/)", file=sys.stderr)
        return 2
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    tabrion = next(
        line.strip()
        for line in (root / TABRION_PIN).read_text(encoding="utf-8").splitlines()
        if line.startswith("tabrion @ ")
    )
    requirements = [str(r) for r in runtime_requirements(pyproject)]
    lines = [tabrion.replace("tabrion @", f"tabrion[{_extras('tabrion', requirements)}] @", 1)]
    lines += [r for r in requirements if canonicalize_name(Requirement(r).name) not in UNLOCKED]
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "runtime.in"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = Path(scratch) / "runtime.txt"
        command = [uv, "pip", "compile", str(source), "--universal", "--python-version", LOWEST_PYTHON]
        command += ["--generate-hashes", "--no-header", "--no-annotate", "--quiet", "-o", str(output)]
        if (root / LOCK).exists():
            # uv keeps the pins of an existing output file that still fit, unless --upgrade(-package) says otherwise.
            shutil.copyfile(root / LOCK, output)
        result = subprocess.run([*command, *(extra_args or [])], check=False)
        if result.returncode != 0:
            return 2
        body = "\n".join(_one_line(output.read_text(encoding="utf-8")))
    (root / LOCK).write_text(HEADER + body + "\n", encoding="utf-8")
    print(f"wrote {LOCK}; now run: python scripts/runtime_lock.py check")
    return 0


def _extras(name: str, requirements: list[str]) -> str:
    for text in requirements:
        requirement = Requirement(text)
        if canonicalize_name(requirement.name) == name:
            return ",".join(sorted(requirement.extras))
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=["check", "check-installed", "update"])
    parser.add_argument("uv_args", nargs="*", help="update only: more arguments for uv pip compile")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.command == "update":
        return update(args.root, args.uv_args)
    problems = check(args.root) if args.command == "check" else check_installed(args.root)
    for problem in problems:
        print(f"::error::{problem}" if "GITHUB_ACTIONS" in os.environ else problem)
    if not problems:
        print(f"{LOCK}: ok ({args.command})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
