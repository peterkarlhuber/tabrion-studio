"""The user guide (STU-017, first part) has a table row for every command-line option and is in the site navigation."""

from __future__ import annotations

from pathlib import Path
import re

from tabrion_studio.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide"


def _options() -> list[str]:
    return [
        option
        for action in build_parser()._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    ]


def _nav_pages() -> set[str]:
    """The pages of the ``nav:`` section of mkdocs.yml, as ``- page.md`` or ``- Title: page.md`` lines.

    Commented-out lines do not count, and neither does a path that is named only outside the navigation.
    """
    lines = (ROOT / "mkdocs.yml").read_text(encoding="utf-8").splitlines()
    nav = lines[lines.index("nav:") + 1 :]
    entry = re.compile(r"^\s+- (?:[^:#]+: )?([\w/.-]+\.md)$")
    return {match.group(1) for line in nav if (match := entry.match(line))}


def test_every_option_has_a_row_in_the_options_table() -> None:
    text = (GUIDE / "starting.md").read_text(encoding="utf-8")
    options = _options()
    assert "--print-url" in options  # the parser was read
    rows = set(re.findall(r"^\| `(--[a-z-]+)[ `]", text, re.MULTILINE))
    assert [option for option in options if option not in rows] == []


def test_every_guide_page_is_in_the_navigation() -> None:
    nav = _nav_pages()
    assert "guide/index.md" in nav  # the navigation was read
    pages = sorted(path.name for path in GUIDE.glob("*.md"))
    assert len(pages) >= 6
    assert [page for page in pages if f"guide/{page}" not in nav] == []
