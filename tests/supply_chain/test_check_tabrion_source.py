"""DC-TS-1 (dependency confusion): the poisoned-index check of the CI job "install (poisoned index)".

``scripts/check_tabrion_source.py`` is the unchanged copy of nodestro-studio's canonical script (TABRION DC-NS-2); the
same SHA-256 is pinned in every consumer, as for ``check_tabrion_api.py`` (QA-21). Its own tests live with the
canonical copy; here the copy, the pin it reads and the CI job that runs it are checked."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_tabrion_source.py"
PIN = ROOT / "constraints" / "tabrion-integration.txt"
#: SHA-256 of ``scripts/check_tabrion_source.py`` (nodestro-studio, branch of DC-NS-2 at ``f0b4d70``).
SCRIPT_SHA256 = "f2516d5df948a554a3aa0261fd26611f00ec50cd62ffded710717aa2f980c40b"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_tabrion_source", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source = _load()


def test_the_script_is_the_shared_copy() -> None:
    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == SCRIPT_SHA256


def test_the_script_reads_the_tabrion_pin() -> None:
    lines = [m for raw in PIN.read_text(encoding="utf-8").splitlines() if (m := source.GIT_REQUIREMENT.match(raw))]
    assert len(lines) == 1
    assert lines[0]["name"] == "tabrion"
    assert lines[0]["url"] == "https://github.com/peterkarlhuber/tabrion"
    assert len(lines[0]["ref"]) == 40


def test_the_ci_job_runs_both_install_paths_under_the_poisoned_index() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["install-poisoned-index"]["steps"]
    runs = [str(step.get("run", "")) for step in steps if "check_tabrion_source.py" in str(step.get("run", ""))]
    assert len(runs) == 2
    for run in runs:
        assert "python -I scripts/check_tabrion_source.py --lock constraints/tabrion-integration.txt" in run
        assert "--no-control" not in run  # the positive control keeps the test from being vacuous
        assert '"pip>=26.1"' in run and "--no-cache-dir" in run
    assert '-e ".[dev]" -c constraints/tabrion-integration.txt' in runs[0]
    assert "scripts/runtime_lock.py check-installed" in runs[0]
    assert "--no-deps -e . -r constraints/tabrion-integration.txt" in runs[1]
