"""RC-8 (05 section 4.5) and the ADR 0009 amendment (tabrion PR #20: no PyPI publication; tabrion comes from Git).

* ``pyproject.toml`` (dependencies and extras): no direct reference (git or URL) and no pre-release specifier;
  tabrion only as a final range ``>=0.N,<0.(N+1)``.
* A requirements or constraints file (``requirements*``/``constraints*`` ``.txt`` or ``.in``, or a file in a
  ``requirements``/``constraints`` folder, at any depth) may pin tabrion to a **full commit SHA** of its repository,
  ``tabrion @ git+https://github.com/peterkarlhuber/tabrion@<40 hex digits>``. Every other line is a plain
  requirement from the index; pip options, paths, wheel files, other URLs and fragments are refused (no branch, tag
  or short SHA: a tag can be moved; R-L2 of the STU-009 re-review).
"""

from __future__ import annotations

import os
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
#: ``name[extras] <op> version, ...`` with an optional marker and ``--hash`` options: a requirement from the index.
_VERSION = r"(?:===|==|!=|~=|>=|<=|>|<)\s*[A-Za-z0-9.*+!_-]+"
_NAMED = re.compile(
    # A wheel or an sdist file name is a local file to pip, not a project name.
    r"(?![^\s\[;<>=!~]*\.(?:whl|zip|tar|tgz|gz|bz2|xz)\b)"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:\[[A-Za-z0-9._-]+(?:\s*,\s*[A-Za-z0-9._-]+)*\])?"
    rf"(?:\s*{_VERSION}(?:\s*,\s*{_VERSION})*)?"
    # A marker; a "-" after white space would start a pip option (R2-I1 of re-review 2: --config-settings, -e, ...).
    r"(?:\s*;\s*(?:[A-Za-z0-9_.<>=!~\'\" ()]|(?<!\s)-)+)?"
    r"(?:\s+--hash=sha256:[0-9a-f]{64})*"
)
#: pip reads ``#`` as a comment only at the start of a line or after white space (R-L2: ``...@<sha>#subdirectory=x``).
_COMMENT = re.compile(r"(?:^|\s)#.*")
#: Directories that hold no file of the repository's own.
_SKIPPED = frozenset({".git", ".venv", "venv", "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache", "site"})


def pin_problems(text: str) -> list[str]:
    """Lines of a requirements or constraints file that are neither a plain requirement nor the full-SHA tabrion pin.

    Two forms only (R-L2 of the STU-009 re-review): ``name[extras] <op> version`` (with an optional marker and
    ``--hash=``), or exactly ``tabrion @ git+https://github.com/peterkarlhuber/tabrion@<40 hex>``. So every pip option
    (``-e``, ``-r``, ``-c``, ``-f``/``--find-links``, ``--index-url``, ``--extra-index-url``, ``--trusted-host``, ...),
    every path or wheel file, every other URL and a fragment after the SHA are refused; a line continuation as well.
    """
    found = []
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        if _SHA_PIN.fullmatch(line) is None and _NAMED.fullmatch(line) is None:
            found.append(f"neither a requirement nor a full-SHA tabrion pin: {line}")
    return found


def is_pin_file(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() not in (".txt", ".in"):
        return False
    return name.startswith(("requirements", "constraints")) or any(
        part.lower() in ("requirements", "constraints") for part in path.parent.parts
    )


def pin_files(root: Path = ROOT) -> list[Path]:
    """Every requirements and constraints file of the repository, at any depth (R-L2)."""
    found = []
    for top, subdirectories, files in os.walk(root):  # Path.walk needs Python 3.12
        subdirectories[:] = [name for name in subdirectories if name not in _SKIPPED]
        directory = Path(top)
        found += [directory / name for name in files if is_pin_file((directory / name).relative_to(root))]
    return sorted(found)


def test_pin_files_pin_only_full_commits() -> None:
    files = pin_files()
    assert ROOT / "constraints" / "tabrion-integration.txt" in files
    for path in files:
        assert pin_problems(path.read_text(encoding="utf-8")) == [], path


def test_pin_files_are_found_at_any_depth(tmp_path: Path) -> None:
    for name in ("requirements.txt", "docs/requirements-docs.txt", "a/b/constraints/x.txt", "ci/constraints-ci.in"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x==1\n", encoding="utf-8")
    (tmp_path / "node_modules" / "requirements.txt").parent.mkdir()
    (tmp_path / "node_modules" / "requirements.txt").write_text("x==1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    found = {path.relative_to(tmp_path).as_posix() for path in pin_files(tmp_path)}
    assert found == {"requirements.txt", "docs/requirements-docs.txt", "a/b/constraints/x.txt", "ci/constraints-ci.in"}


SHA = "a" * 40


@pytest.mark.parametrize(
    "line",
    [
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@v0.1.0",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@546d254",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@master",
        "tabrion @ git+https://github.com/someone-else/tabrion@" + SHA,
        "tabrion @ git+http://github.com/peterkarlhuber/tabrion@" + SHA,
        "other @ https://example.com/other.whl",
        "tabrion[postgres] @ git+https://github.com/peterkarlhuber/tabrion@" + SHA,
        # R-L2 of the STU-009 re-review: pip options, local references and a fragment.
        "-e ../tabrion",
        "--editable=../tabrion",
        "./local/tabrion.whl",
        "tabrion-0.1.0-py3-none-any.whl",
        "tabrion-0.1.0.tar.gz",
        "../tabrion",
        "-f ./wheels",
        "--find-links=vendor",
        "--index-url=file:/x",
        "-i https://example.com/simple",
        "--extra-index-url example.com/simple",
        "--trusted-host example.com",
        "--no-index",
        "-r ../other.txt",
        "-c elsewhere.txt",
        "--requirement=other.txt",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@" + SHA + "#subdirectory=x",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@" + SHA + "#egg=tabrion",
        "tabrion @ file:///home/user/tabrion",
        "tabrion@git+https://github.com/peterkarlhuber/tabrion@" + SHA,
        "tabrion==0.1 \\",
        "tabrion==0.1 --config-settings=x=y",
        "tabrion; python_version < '3.14' and os_name == 'posix' or extra == '../x'",
        # R2-I1 of re-review 2: pip options after a marker.
        'foo==1 ; python_version > "3" --config-settings x=y',
        "foo==1; python_version > '3' --global-option=x",
        "foo==1; python_version > '3' -e x",
        "foo==1; python_version > '3' --install-option x",
    ],
)
def test_pin_probes_are_caught(line: str) -> None:
    assert pin_problems(line)


@pytest.mark.parametrize(
    "text",
    [
        "# comment\ntabrion @ git+https://github.com/peterkarlhuber/tabrion@" + SHA + "\n",
        "tabrion @ git+https://github.com/peterkarlhuber/tabrion@" + SHA + "  # the v0.1.0 commit\n",
        "psycopg[binary]>=3.2,<4\n",
        "zope.interface==7.2\n",
        "sqlalchemy==2.0.43 --hash=sha256:" + "0" * 64 + "\n",
        "tomli>=2; python_version < '3.11'\n",
        "pywin32>=306; sys_platform == 'win32' and platform_machine != 'arm-64'\n",
        "\n   \n# only comments\n",
    ],
)
def test_allowed_lines_pass(text: str) -> None:
    assert pin_problems(text) == []


def test_final_range_passes() -> None:
    assert problems(tomllib.loads('project.dependencies = ["tabrion[postgres,sqlalchemy,migrations]>=0.1,<0.2"]')) == []


CONSTRAINTS = PYPROJECT.parent / "constraints" / "tabrion-integration.txt"


def test_the_constraints_file_pins_tabrion_v0_1_0() -> None:
    """RC-8 (ADR 0009 amendment, no PyPI publication): pyproject.toml carries the final range with no direct
    reference, and the constraints file pins tabrion v0.1.0 (the merge commit of tabrion PR #56) by its full SHA."""
    lines = [line for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert lines == ["tabrion @ git+https://github.com/peterkarlhuber/tabrion@d5821faa8742dea012abda3b663549c038d76543"]
    deps = requirements(dict(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]))
    assert [d for d in deps if d.startswith("tabrion")] == ["tabrion[migrations,postgres,sqlalchemy]>=0.1,<0.2"]
