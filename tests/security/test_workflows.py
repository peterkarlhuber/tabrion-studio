"""CI supply-chain rules (STU-018, TM-10): actions pinned by full SHA, images by digest, nothing publishes."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)", re.MULTILINE)
IMAGE = re.compile(r"^\s*(?:-\s*)?image:\s*(?P<ref>\S+)", re.MULTILINE)
# Release and publication need the owner's approval G-R for every version (00 section 13, E-PyPI).
PUBLISHING = re.compile(
    r"pypa/gh-action-pypi-publish|twine\s+upload|npm\s+publish|gh\s+release\s+(create|upload)|"
    r"softprops/action-gh-release|actions/create-release|id-token:\s*write",
)


def test_there_are_workflows() -> None:
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "ci-ok.yml", "nightly.yml"}


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_by_full_sha(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    for match in USES.finditer(text):
        ref = match.group("ref")
        if ref.startswith("./"):
            continue  # a reusable workflow of this repository
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{workflow.name}: {ref} is not pinned by SHA"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_images_are_pinned_by_digest(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    for match in IMAGE.finditer(text):
        ref = match.group("ref")
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref) or ref.startswith("${{"), f"{workflow.name}: {ref}"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_publishes(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    match = PUBLISHING.search(text)
    assert match is None, f"{workflow.name} publishes or can mint publishing credentials: {match.group(0)!r}"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_default_token_is_read_only(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", text, re.MULTILINE), workflow.name


def test_checkouts_do_not_persist_credentials() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        steps = text.split("uses: actions/checkout@")[1:]
        for step in steps:
            head = step.split("\n      - ", 1)[0]
            assert "persist-credentials: false" in head, f"{workflow.name}: checkout without persist-credentials"


def test_the_probe_catches_an_unpinned_action() -> None:
    probe = "      - uses: actions/checkout@v4\n"
    ref = USES.search(probe)
    assert ref is not None
    assert not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref.group("ref"))
