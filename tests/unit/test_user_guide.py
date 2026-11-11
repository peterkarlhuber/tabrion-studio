"""The user guide (STU-017, first part) names every command-line option and is in the site navigation."""

from __future__ import annotations

from pathlib import Path

from tabrion_studio.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide"


def test_every_option_is_documented() -> None:
    text = (GUIDE / "starting.md").read_text(encoding="utf-8")
    options = [
        option
        for action in build_parser()._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    ]
    assert "--print-url" in options  # the parser was read
    missing = [option for option in options if f"`{option}" not in text]
    assert missing == []


def test_every_guide_page_is_in_the_navigation() -> None:
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    pages = sorted(path.name for path in GUIDE.glob("*.md"))
    assert len(pages) >= 6
    assert [page for page in pages if f"guide/{page}" not in nav] == []
