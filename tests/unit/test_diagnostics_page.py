"""The diagnostics page names Studio's own findings in its en/de text (STU-014): the English titles must be the ones
the backend sends, and every ``STU-DX-*`` code the backend can send must have a title in both languages. The same holds
for the backup hints ``STU-BK-*`` that the page shows since TD-036 (STU-025). Since TD-032 the same holds for their
details and remedies: the page's English templates are the server's (:mod:`.diagnosis_texts`), and German has every
one of them."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from tabrion_studio.services import backup_hints, diagnosis_texts
from tabrion_studio.services import diagnostics as diagnostics_module

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "src" / "tabrion_studio" / "services" / "diagnostics.py"
BACKUP = ROOT / "src" / "tabrion_studio" / "services" / "backup_hints.py"
LOCALES = ROOT / "frontend" / "src" / "i18n" / "locales"


def _titles(source: Path, function: str) -> dict[str, str]:
    """``code -> title`` of every ``function(code, severity, title, ...)`` call in ``source``."""
    titles: dict[str, str] = {}
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == function:
            code, _, title = node.args[:3]
            assert isinstance(code, ast.Constant) and isinstance(code.value, str)
            assert isinstance(title, ast.Constant) and isinstance(title.value, str)
            assert titles.setdefault(code.value, title.value) == title.value, code.value
    return titles


def _backend_titles() -> dict[str, str]:
    """The titles of the diagnostics service's findings and of the backup hints."""
    return {**_titles(SERVICE, "_finding"), **_titles(BACKUP, "_hint")}


def _page_titles(language: str) -> dict[str, str]:
    doc = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    titles = doc["diagnostics"]["titles"]
    assert isinstance(titles, dict)
    return titles


def test_the_page_has_the_backend_titles_of_studio_findings() -> None:
    backend = _backend_titles()
    assert sorted(code for code in backend if code.startswith("STU-DX-")) == [
        f"STU-DX-{number:03d}" for number in range(1, 11)
    ]
    assert sorted(code for code in backend if code.startswith("STU-BK-")) == [
        f"STU-BK-{number:03d}" for number in (1, 2, 3, 4, 5, 10, 11, 12, 13, 14)
    ]
    assert _page_titles("en") == backend


def test_every_studio_finding_has_a_german_title() -> None:
    german = _page_titles("de")
    assert sorted(german) == sorted(_backend_titles())
    assert all(title.strip() for title in german.values())


# TD-032: Studio's details and remedies in en/de. The page words each finding's ``params`` from its i18n files; the
# server words the same ``params`` in English for the API, the report and the command line.
CASES = ROOT / "frontend" / "src" / "views" / "diagnostics" / "studioTexts.cases.json"


def _page_texts(language: str) -> dict[str, dict[str, str]]:
    doc = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    texts = doc["diagnostics"]["texts"]
    assert isinstance(texts, dict)
    return texts


def test_the_pages_english_texts_are_the_servers() -> None:
    assert _page_texts("en") == diagnosis_texts.TEXTS
    assert sorted(diagnosis_texts.TEXTS) == sorted(_backend_titles())


def test_every_text_has_german_with_the_same_keys() -> None:
    german = _page_texts("de")
    assert {code: sorted(keys) for code, keys in german.items()} == {
        code: sorted(keys) for code, keys in diagnosis_texts.TEXTS.items()
    }


def test_the_shared_cases_are_the_servers_english_and_use_every_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """``studioTexts.cases.json`` is checked here against the server and in Vitest against the page's English, so
    both word the same ``params`` alike. Every template of every code is used by some case."""
    used: set[tuple[str, str]] = set()
    real = diagnosis_texts._text

    def recording(code: str, key: str, values: Any = None, *, count: int | None = None) -> str:
        used.add((code, key if count is None else f"{key}_{'one' if count == 1 else 'other'}"))
        return real(code, key, values, count=count)

    monkeypatch.setattr(diagnosis_texts, "_text", recording)
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    for case in cases:
        assert diagnosis_texts.studio_text(case["code"], case["params"]) == (case["detail"], case["remedy"]), case
    assert used == {(code, key) for code, texts in diagnosis_texts.TEXTS.items() for key in texts}


def test_studio_findings_carry_their_params_and_their_english_text() -> None:
    """Every ``_finding``/``_hint`` result has ``params``, and its ``detail`` and ``remedy`` are made from them."""
    finding = diagnostics_module._finding("STU-DX-005", "warning", "Rows break foreign keys", {"rows": 2})
    assert finding["params"] == {"rows": 2}
    assert (finding["detail"], finding["remedy"]) == diagnosis_texts.studio_text("STU-DX-005", {"rows": 2})
    hint = backup_hints._hint("STU-BK-001", "warning", "No backup found")
    assert hint["params"] == {} and hint["detail"].startswith("Studio found no SQLite file")
