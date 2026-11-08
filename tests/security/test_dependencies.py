"""RC-8 (05 section 4.5) and the ADR 0009 amendment (tabrion PR #20: no PyPI publication; tabrion comes from Git).

* ``pyproject.toml`` (dependencies and extras): no direct reference (git or URL) and no pre-release specifier;
  tabrion only as a final range ``>=0.N,<0.(N+1)``.
* A requirements or constraints file (``constraints/*.txt``, ``requirements*.txt``) may pin tabrion to a **full
  commit SHA** of its repository, ``tabrion @ git+https://github.com/peterkarlhuber/tabrion@<40 hex digits>``, and
  nothing else as a direct reference (no branch, tag or short SHA: a tag can be moved).
"""

from __future__ import annotations

from pathlib import Path
import re
import tomllib

import pytest

pytestmark = pytest.mark.security

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
PRE_RELEASE = re.compile(r"\d(a|b|rc|\.dev)\d", re.IGNORECASE)


def requirements(project: dict[str, object]) -> list[str]:
    deps = list(project.get("dependencies", []))  # type: ignore[call-overload]
    for extra in dict(project.get("optional-dependencies", {})).values():  # type: ignore[call-overload]
        deps += list(extra)
    return [str(d) for d in deps]


def problems(data: dict[str, object]) -> list[str]:
    found = []
    project = dict(data.get("project", {}))  # type: ignore[call-overload]
    for req in requirements(project):
        if " @ " in req or "git+" in req or "://" in req:
            found.append(f"direct reference: {req}")
        if PRE_RELEASE.search(req):
            found.append(f"pre-release specifier: {req}")
        name = re.split(r"[\s\[<>=!~;@]", req, maxsplit=1)[0].lower()
        if name == "tabrion" and not re.search(r">=\s*0\.\d+\s*,\s*<\s*0\.\d+", req):
            found.append(f"tabrion needs a final-only range >=0.N,<0.(N+1): {req}")
    metadata = dict(dict(data.get("tool", {})).get("hatch", {})).get("metadata", {})  # type: ignore[call-overload]
    if dict(metadata).get("allow-direct-references"):
        found.append("tool.hatch.metadata.allow-direct-references is set")
    return found


def test_pyproject_follows_rc8() -> None:
    assert problems(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize(
    "toml",
    [
        'project.dependencies = ["tabrion @ git+https://github.com/peterkarlhuber/tabrion.git@' + "a" * 40 + '"]',
        'project.dependencies = ["tabrion>=0.1.0a1,<0.2"]',
        'project.dependencies = ["tabrion"]',
        'project.optional-dependencies.dev = ["x @ https://example.com/x.whl"]',
        "tool.hatch.metadata.allow-direct-references = true",
    ],
)
def test_probes_are_caught(toml: str) -> None:
    assert problems(tomllib.loads(toml))


ROOT = PYPROJECT.parent
_SHA_PIN = re.compile(r"tabrion @ git\+https://github\.com/peterkarlhuber/tabrion@[0-9a-f]{40}")


def pin_problems(text: str) -> list[str]:
    """Direct references in a requirements or constraints file that are not a full-SHA tabrion pin."""
    found = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if (" @ " in line or "git+" in line or "://" in line) and _SHA_PIN.fullmatch(line) is None:
            found.append(f"not a full-SHA tabrion pin: {line}")
    return found


def pin_files() -> list[Path]:
    return sorted([*ROOT.glob("constraints/*.txt"), *ROOT.glob("requirements*.txt")])


def test_pin_files_pin_only_full_commits() -> None:
    for path in pin_files():
        assert pin_problems(path.read_text(encoding="utf-8")) == [], path


@pytest.mark.parametrize(
    "line",
    [
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@v0.1.0",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@546d254",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@master",
        "tabrion @ git+https://github.com/someone-else/tabrion@" + "a" * 40,
        "tabrion @ git+http://github.com/peterkarlhuber/tabrion@" + "a" * 40,
        "other @ https://example.com/other.whl",
        "tabrion[postgres] @ git+https://github.com/peterkarlhuber/tabrion@" + "a" * 40,
    ],
)
def test_pin_probes_are_caught(line: str) -> None:
    assert pin_problems(line)


def test_a_full_sha_pin_passes() -> None:
    assert pin_problems("# comment\ntabrion @ git+https://github.com/peterkarlhuber/tabrion@" + "a" * 40 + "\n") == []


def test_final_range_passes() -> None:
    assert problems(tomllib.loads('project.dependencies = ["tabrion[postgres,sqlalchemy,migrations]>=0.1,<0.2"]')) == []


CONSTRAINTS = PYPROJECT.parent / "constraints" / "tabrion-integration.txt"


def test_the_integration_branch_pins_one_full_commit() -> None:
    """STU-009 integration branch (RC-8): the pre-release range in pyproject.toml, the commit in the constraints
    file. test_pyproject_follows_rc8 fails on this branch on purpose (it refuses a pre-release range): the branch
    must not merge before tabrion v0.1.0. Then the final range goes into pyproject.toml and the constraints file
    pins the v0.1.0 commit by its full SHA, which the RC-8 tests allow."""
    lines = [line for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert lines == ["tabrion @ git+https://github.com/peterkarlhuber/tabrion@546d254a27df9f6b539d57c307ad3022d0ee3a4a"]
    deps = requirements(dict(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]))
    assert [d for d in deps if d.startswith("tabrion")] == ["tabrion[postgres,sqlalchemy]>=0.1.0a1,<0.2"]
