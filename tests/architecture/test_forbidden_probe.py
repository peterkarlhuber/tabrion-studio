"""QA-16 (AC-1): the import-linter contracts follow the edge list K-33, and a forbidden import turns them red.

The template is tabrion's ``tests/architecture/test_import_contracts.py``:

* ``lint-imports`` on the repository passes (positive control), and so does an unmodified copy.
* Mutation probe (07 section 7): a copy of the package with one forbidden import fails ``lint-imports`` and names the
  broken contract.
* The edge Tabrion Studio -> tabrion is allowed (K-33): a copy with ``import tabrion`` stays green.
* The K-33 contract names every known Levuvo platform, and an AST scan catches any ``orkexa*``, ``axentra*``,
  ``nodestro*`` or ``levuvo*`` package, prefixes import-linter cannot express.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PYPROJECT = ROOT / "pyproject.toml"
LEVUVO_PREFIXES = ("orkexa", "axentra", "nodestro", "levuvo")

NO_LEVUVO = "Tabrion Studio depends on no Levuvo platform (K-33)"
NO_CLI = "The process model and the app never import the CLI"
PROBLEMS = "Problem documents and logging (STU-004) import no other Studio layer"
CONFIG = "Configuration and trust (STU-006) never import the API, the app, the server, the CLI or security"
SECURITY = "The security package never imports the app, the server, the process model or the CLI"


def _lint_imports(cwd: Path, src: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(src)
    # The console script's entry point, called through the running interpreter: ``python -m importlinter.cli`` does
    # nothing (the module has no ``__main__`` block), and the script's location differs between POSIX and Windows.
    entry = "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"
    return subprocess.run(
        [sys.executable, "-c", entry, "--config", str(cwd / "pyproject.toml"), "--no-cache"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _copy(tmp_path: Path) -> Path:
    shutil.copytree(
        SRC / "tabrion_studio", tmp_path / "src" / "tabrion_studio", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy(PYPROJECT, tmp_path / "pyproject.toml")
    return tmp_path


def _inject(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8")
    anchor = "from __future__ import annotations\n"
    assert anchor in text, path
    path.write_text(text.replace(anchor, anchor + "\n" + line + "\n", 1), encoding="utf-8")


def test_contracts_are_kept() -> None:
    result = _lint_imports(ROOT, SRC)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def test_unmodified_copy_is_kept(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    result = _lint_imports(root, root / "src")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout, "lint-imports produced no report"


@pytest.mark.parametrize(
    ("module", "line", "contract"),
    [
        ("cli.py", "import orkexa", NO_LEVUVO),
        ("server.py", "import axentra", NO_LEVUVO),
        ("api/config.py", "import nodestro_runtime", NO_LEVUVO),
        ("config/project.py", "import axentra_studio", NO_LEVUVO),
        ("security/launch.py", "import levuvo_nextcloud_mcp", NO_LEVUVO),
        ("app.py", "import nodestro_client", NO_LEVUVO),
        ("app.py", "import tabrion_studio.cli", NO_CLI),
        ("instance.py", "import tabrion_studio.cli", NO_CLI),
        ("problems.py", "import tabrion_studio.server", PROBLEMS),
        ("logs.py", "import tabrion_studio.security", PROBLEMS),
        ("config/writer.py", "import tabrion_studio.api", CONFIG),
        ("config/files.py", "import tabrion_studio.request_log", CONFIG),
        ("security/sessions.py", "import tabrion_studio.app", SECURITY),
    ],
)
def test_mutation_probe_turns_red(tmp_path: Path, module: str, line: str, contract: str) -> None:
    root = _copy(tmp_path)
    _inject(root / "src" / "tabrion_studio" / module, line)
    result = _lint_imports(root, root / "src")
    assert result.returncode != 0, f"{line!r} in {module} went unnoticed:\n{result.stdout}"
    # lint-imports wraps a long contract name over two lines, so compare the report with its whitespace folded.
    assert f"{contract} BROKEN" in " ".join(result.stdout.split()), result.stdout


def test_the_edge_to_tabrion_is_allowed(tmp_path: Path) -> None:
    """K-33: tabrion-studio -> tabrion is a required edge, so no contract may forbid it."""
    root = _copy(tmp_path)
    _inject(root / "src" / "tabrion_studio" / "cli.py", "import tabrion")
    result = _lint_imports(root, root / "src")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def _contracts() -> list[dict[str, object]]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    contracts: list[dict[str, object]] = data["tool"]["importlinter"]["contracts"]
    return contracts


def test_levuvo_contract_names_every_known_platform() -> None:
    (contract,) = [c for c in _contracts() if c["name"] == NO_LEVUVO]
    forbidden = set(contract["forbidden_modules"])  # type: ignore[call-overload]
    assert contract["source_modules"] == ["tabrion_studio"]
    assert {
        "orkexa",
        "axentra",
        "axentra_studio",
        "nodestro",
        "nodestro_runtime",
        "nodestro_api",
        "nodestro_studio",
        "nodestro_examples",
        "nodestro_integrations",
        "nodestro_sdk",
        "nodestro_client",
        "levuvo_nextcloud_mcp",
    } <= forbidden
    assert "tabrion" not in forbidden


def test_no_contract_ignores_imports() -> None:
    """An ``ignore_imports`` entry would hide a real edge from every contract."""
    assert not [c["name"] for c in _contracts() if c.get("ignore_imports")]


def _imported_top_levels(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.partition(".")[0])
    return names


def test_no_levuvo_import_by_prefix() -> None:
    """Covers any future Levuvo package, also in ``TYPE_CHECKING`` blocks."""
    offenders = []
    for path in sorted((SRC / "tabrion_studio").rglob("*.py")):
        for name in _imported_top_levels(path):
            if name.startswith(LEVUVO_PREFIXES):
                offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert not offenders


def test_prefix_scan_detects_a_probe(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("from nodestro_server import x\nimport axentra_future.y\n", encoding="utf-8")
    names = _imported_top_levels(probe)
    assert names == {"nodestro_server", "axentra_future"}
    assert all(name.startswith(LEVUVO_PREFIXES) for name in names)
