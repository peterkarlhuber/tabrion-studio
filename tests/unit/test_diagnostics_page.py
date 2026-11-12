"""The diagnostics page names Studio's own findings in its en/de text (STU-014): the English titles must be the ones
the backend sends, and every ``STU-DX-*`` code the backend can send must have a title in both languages."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "src" / "tabrion_studio" / "services" / "diagnostics.py"
LOCALES = ROOT / "frontend" / "src" / "i18n" / "locales"


def _backend_titles() -> dict[str, str]:
    """``code -> title`` of every ``_finding(code, severity, title, ...)`` call in the diagnostics service."""
    titles: dict[str, str] = {}
    for node in ast.walk(ast.parse(SERVICE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_finding":
            code, _, title = node.args[:3]
            assert isinstance(code, ast.Constant) and isinstance(code.value, str)
            assert isinstance(title, ast.Constant) and isinstance(title.value, str)
            assert titles.setdefault(code.value, title.value) == title.value, code.value
    return titles


def _page_titles(language: str) -> dict[str, str]:
    doc = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    titles = doc["diagnostics"]["titles"]
    assert isinstance(titles, dict)
    return titles


def test_the_page_has_the_backend_titles_of_studio_findings() -> None:
    backend = _backend_titles()
    assert sorted(backend) == [f"STU-DX-{number:03d}" for number in range(1, 11)]
    assert _page_titles("en") == backend


def test_every_studio_finding_has_a_german_title() -> None:
    german = _page_titles("de")
    assert sorted(german) == sorted(_backend_titles())
    assert all(title.strip() for title in german.values())
