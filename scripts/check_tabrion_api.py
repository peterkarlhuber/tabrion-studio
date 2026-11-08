#!/usr/bin/env python3
"""Tabrion Studio uses only the public Tabrion API (AC-7, 07 section 7; first part of QA-21).

    python scripts/check_tabrion_api.py [PATH ...]        # default: src/tabrion_studio

Fails (exit code 1) for any of these in the scanned Python files:

* importing a private Tabrion module: ``import tabrion._sa``, ``from tabrion._sa.database import Database``;
* importing a private name: ``from tabrion.sqlite import _helper``;
* using a private attribute of an imported Tabrion module: ``tabrion._sa`` or ``tabrion.sqlite._helper``.

Dunder names (``tabrion.__version__``) are public. The full QA-21 check against ``__all__`` of Tabrion's API
contract and the export to ``tabrion/docs/api-consumers.md`` follow after the API freeze. Standard library only.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = [ROOT / "src" / "tabrion_studio"]


def _private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _is_tabrion(module: str) -> bool:
    return module == "tabrion" or module.startswith("tabrion.")


def check_source(source: str, filename: str = "<string>") -> list[str]:
    """Problems in one file (empty list = only public names)."""
    tree = ast.parse(source, filename=filename)
    problems: list[str] = []
    aliases: set[str] = set()  # local names bound to a tabrion module

    def where(node: ast.AST) -> str:
        return f"{filename}:{getattr(node, 'lineno', '?')}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_tabrion(alias.name):
                    continue
                if any(_private(part) for part in alias.name.split(".")):
                    problems.append(f"{where(node)}: private Tabrion module {alias.name}")
                aliases.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and _is_tabrion(node.module):
            if any(_private(part) for part in node.module.split(".")):
                problems.append(f"{where(node)}: private Tabrion module {node.module}")
            for alias in node.names:
                if _private(alias.name):
                    problems.append(f"{where(node)}: private Tabrion name {node.module}.{alias.name}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _private(node.attr):
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in aliases:
                problems.append(f"{where(node)}: private Tabrion attribute {ast.unparse(node)}")
    return problems


def check_paths(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            problems += check_source(file.read_text(encoding="utf-8"), file.as_posix())
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] or DEFAULT_PATHS
    problems = check_paths(paths)
    for problem in problems:
        print(problem)
    print(f"tabrion api check: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
