"""RC-8 (05 section 4.5): master carries no git/URL pin and no pre-release specifier; tabrion only as a final range."""

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


def test_final_range_passes() -> None:
    assert problems(tomllib.loads('project.dependencies = ["tabrion[postgres,sqlalchemy,migrations]>=0.1,<0.2"]')) == []


CONSTRAINTS = PYPROJECT.parent / "constraints" / "tabrion-integration.txt"


def test_the_integration_branch_pins_one_full_commit() -> None:
    """STU-009 integration branch (RC-8): the pre-release range in pyproject.toml, the commit in the constraints
    file. test_pyproject_follows_rc8 fails on this branch on purpose: it must not merge before tabrion v0.1.0."""
    lines = [line for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert lines == ["tabrion @ git+https://github.com/peterkarlhuber/tabrion@546d254a27df9f6b539d57c307ad3022d0ee3a4a"]
    deps = requirements(dict(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]))
    assert [d for d in deps if d.startswith("tabrion")] == ["tabrion[postgres,sqlalchemy]>=0.1.0a1,<0.2"]
