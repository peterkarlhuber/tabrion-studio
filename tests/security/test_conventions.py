"""TABRION QA-22: the repository conventions shared with tabrion and orkexa stay in place (conventions audit,
``docs/development/conventions.md``). Actions pins and image digests are ``test_workflows.py``; this file covers
the rest of the checklist that a file can show."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

import pytest

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_license_is_mit_in_the_file_and_the_metadata() -> None:
    assert _text(ROOT / "LICENSE").startswith("MIT License\n")
    project = tomllib.loads(_text(ROOT / "pyproject.toml"))["project"]
    assert project["license"] == "MIT"
    assert "LICENSE" in project["license-files"]


def test_security_policy_names_a_private_reporting_route() -> None:
    policy = _text(ROOT / "SECURITY.md")
    assert "## Reporting a vulnerability" in policy
    assert "private vulnerability reporting" in policy
    assert "do not report vulnerabilities in public issues" in policy


def test_community_files_are_present() -> None:
    for name in ("README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SUPPORT.md", "CHANGELOG.md"):
        assert (ROOT / name).is_file(), name
    assert (GITHUB / "pull_request_template.md").is_file()


def _dependabot_updates() -> dict[str, str]:
    """``{ecosystem: the text of its block}`` of ``.github/dependabot.yml`` (no YAML parser in the dev extra)."""
    text = _text(GITHUB / "dependabot.yml")
    blocks = re.split(r"^  - (?=package-ecosystem:)", text, flags=re.MULTILINE)[1:]
    found: dict[str, str] = {}
    for block in blocks:
        match = re.match(r"package-ecosystem: (\S+)", block)
        assert match is not None
        found[match[1]] = block
    return found


def test_dependabot_covers_pip_npm_and_actions_weekly_with_a_cooldown() -> None:
    updates = _dependabot_updates()
    assert set(updates) == {"pip", "npm", "github-actions"}
    assert "directory: /frontend" in updates["npm"]
    for ecosystem, block in updates.items():
        assert "interval: weekly" in block, ecosystem
        assert "cooldown:" in block, ecosystem
    # No auto-merge: no workflow merges a Dependabot pull request on its own.
    for workflow in (GITHUB / "workflows").glob("*.yml"):
        text = _text(workflow)
        assert "--auto" not in text and "enableautomerge" not in text.replace("_", "").replace("-", "").lower()


def test_ci_manifest_names_existing_workflows() -> None:
    """The ``ci-ok`` gate judges by ``.github/ci-manifest.json``; every workflow it requires must exist and run on
    pull requests (``ci_ok.py`` checks the job names at run time)."""
    manifest = json.loads(_text(GITHUB / "ci-manifest.json"))
    assert manifest["pull_request"]
    for workflow in manifest["pull_request"]:
        path = GITHUB / "workflows" / workflow
        assert path.is_file(), workflow
        assert re.search(r"^  pull_request:", _text(path), flags=re.MULTILINE), workflow
    for workflow in manifest.get("nightly", []):
        assert (GITHUB / "workflows" / workflow).is_file(), workflow
    assert (GITHUB / "workflows" / "ci-ok.yml").is_file()
