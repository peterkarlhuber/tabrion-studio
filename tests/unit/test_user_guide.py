"""The user guide (STU-017) has a table row for every command-line option, is in the site navigation, and every
documented install command carries the Tabrion pin (the clean-install test found a README command without it) or is
the hashed install of the runtime lock (TD-031, security review F-6)."""

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


INSTALL_DOCS = ("README.md", "docs/guide/installation.md")
#: The Tabrion pin, as a constraint (``-c``) or, with ``--no-deps``, as a requirement (``-r``).
PINS = tuple(
    f"{option} constraints{separator}tabrion-integration.txt" for option in ("-c", "-r") for separator in ("/", "\\")
)
LOCKS = ("-r constraints/runtime-lock.txt", "-r constraints\\runtime-lock.txt")
#: The fence languages the install docs use; a new one must be added here, so that its block is checked too.
FENCE_LANGUAGES = {"", "bash", "powershell"}


def _fences(text: str) -> list[tuple[str, str]]:
    """Every fenced code block as (language, body), whatever its language."""
    return re.findall(r"^```([\w-]*)\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


def _install_commands(text: str) -> list[str]:
    """The ``pip install`` lines of every fenced code block that install Studio (not pip itself or the build tool)."""
    lines = [line.strip() for _, body in _fences(text) for line in body.splitlines()]
    return [
        line
        for line in lines
        if re.search(r"pip install\b", line) and not re.search(r"pip install (--upgrade \"pip|\"build==)", line)
    ]


def test_the_install_docs_use_only_known_fence_languages() -> None:
    languages = {language for name in INSTALL_DOCS for language, _ in _fences((ROOT / name).read_text("utf-8"))}
    assert "powershell" in languages  # the Windows blocks were read
    assert languages <= FENCE_LANGUAGES


def test_every_install_command_carries_the_tabrion_pin() -> None:
    commands = [command for name in INSTALL_DOCS for command in _install_commands((ROOT / name).read_text("utf-8"))]
    studio = [command for command in commands if not any(lock in command for lock in LOCKS)]
    # README, the guide's commit install (bash and PowerShell), the wheel install (both) and the update were read.
    assert len(studio) >= 6
    assert [command for command in studio if not any(pin in command for pin in PINS)] == []
    assert (ROOT / "constraints" / "tabrion-integration.txt").is_file()


def test_every_studio_install_without_dependencies_follows_the_hashed_lock() -> None:
    """TD-031: the dependencies come from the lock with hashes required; Studio itself then with ``--no-deps``."""
    for name in INSTALL_DOCS:
        commands = _install_commands((ROOT / name).read_text("utf-8"))
        locks = [i for i, command in enumerate(commands) if any(lock in command for lock in LOCKS)]
        assert locks, name
        for i in locks:
            assert "--require-hashes --no-deps --only-binary :all:" in commands[i], commands[i]
            # The next install is Studio (or its wheel) with the Tabrion pin, without resolving dependencies.
            assert "--no-deps" in commands[i + 1] and any(pin in commands[i + 1] for pin in PINS), commands[i + 1]
    assert (ROOT / "constraints" / "runtime-lock.txt").is_file()


def test_the_docs_checklist_is_in_the_navigation() -> None:
    assert "development/docs-checklist.md" in _nav_pages()


def test_every_diagnose_option_has_a_row_in_the_diagnostics_guide() -> None:
    """TD-036: ``tabrion-studio diagnose`` is documented with every option, next to the report it writes."""
    from tabrion_studio.diagnose_cli import build_parser as diagnose_parser

    text = (GUIDE / "diagnostics.md").read_text(encoding="utf-8")
    options = [
        option
        for action in diagnose_parser()._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    ]
    assert "--confirm-prod" in options  # the parser was read
    rows = {
        option
        for row in re.findall(r"^\| (`--.*?) \|", text, re.MULTILINE)
        for option in re.findall(r"`(--[a-z-]+)`", row)
    }
    assert [option for option in options if option not in rows] == []
