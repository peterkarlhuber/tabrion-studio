"""DC-TS-1 (dependency confusion): the poisoned-index check of the CI job "install (poisoned index)".

``scripts/check_tabrion_source.py`` is the unchanged copy of nodestro-studio's canonical script (TABRION DC-NS-2); the
same SHA-256 is pinned in every consumer, as for ``check_tabrion_api.py`` (QA-21). Its own tests live with the
canonical copy; here the copy, the pin it reads and the CI job that runs it are checked. The job's zero-request
assertion is the proof that the pinned installs never ask an index for tabrion; ``check-installed``'s
``direct_url.json`` check only detects an accidental index install (a wheel can forge the file)."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tomllib
from types import ModuleType

from packaging.requirements import Requirement
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_tabrion_source.py"
PIN = ROOT / "constraints" / "tabrion-integration.txt"
#: SHA-256 of ``scripts/check_tabrion_source.py``: nodestro-studio #198 (``c036a29``) plus the per-package poison
#: version of NS-003 (tabrion 0.2.99, nodestro-session 0.1.99). Until nodestro-studio's canonical copy takes the same
#: change this copy is ahead of it (docs/TODO.md NS-003).
SCRIPT_SHA256 = "57de9fbf513f32a6a1e80b6cf4197f0c2463f9debba395ab602702e9302b683d"


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


def test_each_poison_version_falls_inside_studio_s_range() -> None:
    """An install by range would pick each poisoned release, so only the zero-request assertion tells it apart."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    ranges = {(req := Requirement(raw)).name: req.specifier for raw in project["dependencies"]}
    for name in ("tabrion", "nodestro-session"):
        version = source.poison_version(name)
        assert ranges[name].contains(version), (name, version, str(ranges[name]))
    assert source.poison_version("nodestro_session") == "0.1.99"
    assert source.poison_version("tabrion") == source.POISON_VERSION == "0.2.99"


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
