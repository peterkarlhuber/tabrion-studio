"""STU-031: the license policy check, including the negative probes CI runs (tests/supply_chain/probes)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PROBES = Path(__file__).resolve().parent / "probes"
sys.path.insert(0, str(SCRIPTS))
import check_licenses as cl  # noqa: E402

sys.path.remove(str(SCRIPTS))

pytestmark = pytest.mark.security


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "check_licenses.py"), *args], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    ("expression", "ok"),
    [
        ("MIT", True),
        ("Apache-2.0 OR BSD-3-Clause", True),
        ("(MIT OR GPL-3.0-only)", True),
        ("MIT AND ISC", True),
        ("MIT AND GPL-3.0-only", False),
        ("Apache-2.0 WITH LLVM-exception", True),
        ("GPL-3.0-only", False),
        ("AGPL-3.0-or-later", False),
        ("LGPL-3.0-only", False),
        ("SSPL-1.0", False),
        ("MPL-2.0", False),
        ("UNLICENSED", False),
        ("", False),
        # Review L-6: parentheses group, and AND binds tighter than OR (SPDX).
        ("(MIT OR GPL-3.0-only) AND LGPL-2.1-only", False),
        ("(MIT OR GPL-3.0-only) AND Apache-2.0", True),
        ("MIT OR (GPL-3.0-only AND LGPL-2.1-only)", True),
        ("GPL-3.0-only OR MIT AND LGPL-2.1-only", False),
        ("((MIT)) AND (ISC OR GPL-2.0-only)", True),
        ("GPL-2.0-only WITH Classpath-exception-2.0", False),
        ("Apache-2.0 WITH LLVM-exception AND MIT", True),
        # Malformed expressions are not allowed.
        ("(MIT", False),
        ("MIT OR Foo (x", False),
        ("MIT OR GPL-3.0-only (x", False),
        ("MIT)", False),
        ("MIT AND", False),
        ("OR MIT", False),
        ("()", False),
        ("MIT WITH", False),
        # Trove classifier names with parentheses stay one name (normalized by the aliases).
        ("ISC License (ISCL)", True),
        ("BSD-3-Clause OR GNU General Public License (GPL)", True),
        ("GNU General Public License (GPL)", False),
    ],
)
def test_shipped_expressions(expression: str, ok: bool) -> None:
    assert cl.expression_allowed(expression, cl.ALLOWED_SHIPPED) is ok


def test_dev_only_licenses() -> None:
    allowed = cl.ALLOWED_SHIPPED | cl.ALLOWED_DEV_ONLY
    assert cl.expression_allowed("MPL-2.0", allowed)
    assert cl.expression_allowed("CC-BY-4.0", allowed)
    assert not cl.expression_allowed("GPL-2.0-only", allowed)


def test_python_classifiers_and_fields() -> None:
    entries: list[dict[str, object]] = [
        {"name": "a", "version": "1", "classifiers": ["License :: OSI Approved :: MIT License"]},
        {"name": "b", "version": "1", "license": "BSD"},
        {"name": "c", "version": "1", "license_expression": "Apache-2.0"},
        {"name": "d", "version": "1"},
        {
            "name": "e",
            "version": "1",
            "classifiers": ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
        },
    ]
    verdicts = {f.name: f.allowed for f in cl.check_python(entries)}
    assert verdicts == {"a": True, "b": True, "c": True, "d": False, "e": False}


def test_psycopg_lgpl_is_an_explicit_exception_only() -> None:
    entries: list[dict[str, object]] = [
        {"name": "psycopg", "version": "3.3", "license_expression": "LGPL-3.0-only"},
        {"name": "psycopg-binary", "version": "3.3", "license_expression": "LGPL-3.0-only"},
        {"name": "psycopg", "version": "4.0", "license_expression": "GPL-3.0-only"},
        {"name": "other-lgpl", "version": "1", "license_expression": "LGPL-3.0-only"},
    ]
    assert [f.allowed for f in cl.check_python(entries)] == [True, True, False, False]


def test_npm_dev_and_shipped() -> None:
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "x"},
            "node_modules/react": {"version": "19", "license": "MIT"},
            "node_modules/axe-core": {"version": "4", "license": "MPL-2.0", "dev": True},
            "node_modules/shipped-mpl": {"version": "1", "license": "MPL-2.0"},
            "node_modules/legacy": {"version": "1", "license": {"type": "ISC"}, "dev": True},
            "node_modules/a/node_modules/none": {"version": "1"},
        },
    }
    verdicts = {f.name: f.allowed for f in cl.check_npm(lock)}
    assert verdicts == {"react": True, "axe-core": True, "shipped-mpl": False, "legacy": True, "none": False}


def test_old_lockfile_is_rejected() -> None:
    with pytest.raises(ValueError, match="lockfileVersion"):
        cl.check_npm({"lockfileVersion": 1, "dependencies": {}})


def test_negative_probes_fail() -> None:
    # The same probes run as a CI step; a disallowed license must break the build.
    python = run("--python", str(PROBES / "python-gpl.json"))
    assert python.returncode == 1, python.stdout
    assert "DISALLOWED python copyleft-probe 1.0.0: GPL-3.0-only" in python.stdout
    npm = run("--npm", str(PROBES / "npm-gpl-lock.json"))
    assert npm.returncode == 1, npm.stdout
    assert "DISALLOWED npm copyleft-probe 1.0.0: GPL-3.0-only shipped" in npm.stdout


def test_the_frontend_lockfile_passes() -> None:
    result = run("--npm", str(ROOT / "frontend" / "package-lock.json"))
    assert result.returncode == 0, result.stdout
    assert "license check: ok" in result.stdout


def test_collector_reports_this_environment(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "collect_licenses.py")], capture_output=True, text=True, check=True
    ).stdout
    entries = json.loads(out)
    names = {e["name"].lower() for e in entries}
    assert {"tabrion-studio", "fastapi", "uvicorn"} <= names
    assert "pip" not in names


def test_nothing_to_check_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    assert run("--python", str(empty)).returncode == 1
