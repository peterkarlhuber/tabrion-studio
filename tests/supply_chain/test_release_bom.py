"""STU-028: the release SBOM (CycloneDX) and license report of ``scripts/release_bom.py``, offline.

The Python license records are written by the test (``scripts/collect_licenses.py`` reads an installed environment,
which a unit test cannot pin), so every case runs on the real lockfiles without network access.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
LOCK = ROOT / "constraints" / "runtime-lock.txt"
PIN = ROOT / "constraints" / "tabrion-integration.txt"
NPM_LOCK = ROOT / "frontend" / "package-lock.json"
sys.path.insert(0, str(SCRIPTS))
import release_bom as rb  # noqa: E402

sys.path.remove(str(SCRIPTS))

pytestmark = pytest.mark.security

Record = dict[str, object]


def lock_records(*, with_markers: bool = True) -> list[Record]:
    """One license record per lock pin (MIT; psycopg LGPL-3.0 as declared upstream) and one for tabrion."""
    records: list[Record] = []
    for component in rb.read_lock(LOCK.read_text(encoding="utf-8")):
        if component.marker and not with_markers:
            continue
        lgpl = component.name in {"psycopg", "psycopg-binary"}
        records.append(
            {
                "name": component.name,
                "version": component.version,
                "license_expression": "LGPL-3.0-only" if lgpl else "MIT",
            }
        )
    records.append({"name": "tabrion", "version": "0.1.0", "license_expression": "MIT"})
    return records


def build(
    records: list[Record], *, strict: bool = False, npm: dict[str, object] | None = None
) -> tuple[dict[str, object], str, list[str]]:
    npm_lock = npm if npm is not None else json.loads(NPM_LOCK.read_text(encoding="utf-8"))
    return rb.build(
        [records], LOCK.read_text(encoding="utf-8"), PIN.read_text(encoding="utf-8"), npm_lock, "1.0.0", strict
    )


def components(bom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    listed = bom["components"]
    assert isinstance(listed, list)
    return {str(c["purl"]): c for c in listed}


def prop(component: dict[str, Any], name: str) -> list[str]:
    listed = component.get("properties", [])
    assert isinstance(listed, list)
    return [str(p["value"]) for p in listed if p["name"] == name]


def test_the_sbom_lists_every_pin_and_every_shipped_frontend_package() -> None:
    bom, report, failures = build(lock_records())
    assert failures == []
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.5"
    metadata = bom["metadata"]
    assert isinstance(metadata, dict) and metadata["component"]["name"] == "tabrion-studio"
    assert "timestamp" not in metadata or os.environ.get("SOURCE_DATE_EPOCH")
    by_purl = components(bom)

    pins = rb.read_lock(LOCK.read_text(encoding="utf-8"))
    for pin in pins:
        entry = by_purl[pin.purl]
        assert prop(entry, "tabrion-studio:lock-sha256") == [h for _, h in pin.hashes]
    assert by_purl["pkg:pypi/fastapi@" + next(p.version for p in pins if p.name == "fastapi")]["licenses"] == [
        {"expression": "MIT"}
    ]

    lock = json.loads(NPM_LOCK.read_text(encoding="utf-8"))
    shipped = {
        path.rsplit("node_modules/", 1)[-1]
        for path, meta in lock["packages"].items()
        if path and not meta.get("dev") and not meta.get("link")
    }
    dev = {path.rsplit("node_modules/", 1)[-1] for path, meta in lock["packages"].items() if meta.get("dev")}
    npm_names = {str(c["name"]) for c in by_purl.values() if str(c["purl"]).startswith("pkg:npm/")}
    assert npm_names == shipped
    assert {"react", "react-dom", "@xyflow/react"} <= npm_names
    assert not (dev - shipped) & npm_names, "a build or test tool is listed as shipped"
    react = next(c for c in by_purl.values() if c["name"] == "react")
    assert react["hashes"][0]["alg"] == "SHA-512" and len(react["hashes"][0]["content"]) == 128
    assert any(purl.startswith("pkg:npm/%40xyflow/react@") for purl in by_purl)

    tabrion = next(c for c in by_purl.values() if c["name"] == "tabrion")
    assert tabrion["version"] == "0.1.0"
    assert prop(tabrion, "tabrion-studio:vcs") == [
        "git+https://github.com/peterkarlhuber/tabrion@d5821faa8742dea012abda3b663549c038d76543"
    ]
    assert str(tabrion["purl"]).startswith("pkg:pypi/tabrion@0.1.0?vcs_url=git%2Bhttps")

    assert "Policy: **passed**" in report
    assert "## psycopg (LGPL-3.0)" in report and "<https://github.com/psycopg/psycopg>" in report
    assert "| psycopg-binary |" in report and "allowed (exception)" in report
    assert "| react |" in report


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda r: r.update(license_expression="GPL-3.0-only"), "GPL-3.0-only: copyleft or unknown"),
        (lambda r: r.update(license_expression="AGPL-3.0-or-later"), "AGPL-3.0-or-later: copyleft"),
        (lambda r: r.update(license_expression="LGPL-3.0-only"), "LGPL-3.0-only: copyleft"),
        (
            lambda r: r.update(license_expression="LicenseRef-Proprietary"),
            "LicenseRef-Proprietary: copyleft or unknown",
        ),
        (lambda r: r.update(license_expression=""), "(none): no license"),
        (lambda r: r.update(version="0.0.1"), "the license records are for version 0.0.1"),
    ],
)
def test_copyleft_unknown_and_stale_python_licenses_fail(change: Callable[[Record], None], expected: str) -> None:
    records = lock_records()
    fastapi = next(r for r in records if r["name"] == "fastapi")
    change(fastapi)
    _, report, failures = build(records)
    assert len(failures) == 1 and failures[0].startswith("python fastapi ") and expected in failures[0], failures
    assert "Policy: **FAILED** (1 finding(s))" in report


def test_a_pin_without_a_record_fails_and_a_marker_pin_needs_strict() -> None:
    _, report, failures = build(lock_records(with_markers=False))
    assert failures == []  # the marker pins are "not verified", which only --strict refuses
    assert "not verified" in report and "a release run uses `--strict`" in report
    _, _, strict = build(lock_records(with_markers=False), strict=True)
    marked = {c.name for c in rb.read_lock(LOCK.read_text(encoding="utf-8")) if c.marker}
    assert {f.split()[1] for f in strict} == marked

    without_tabrion = [r for r in lock_records() if r["name"] != "tabrion"]
    _, _, failures = build(without_tabrion)
    assert len(failures) == 1 and "tabrion" in failures[0] and "no license record" in failures[0]


def test_frontend_copyleft_and_missing_licenses_fail() -> None:
    lock = json.loads(NPM_LOCK.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/copyleft-probe"] = {"version": "1.0.0", "license": "GPL-3.0-only"}
    lock["packages"]["node_modules/no-license"] = {"version": "2.0.0"}
    lock["packages"]["node_modules/dev-tool"] = {"version": "3.0.0", "license": "SSPL-1.0", "dev": True}
    bom, _, failures = build(lock_records(), npm=lock)
    assert sorted(failures) == [
        "npm (dev) dev-tool 3.0.0: SSPL-1.0: outside the dev allowlist",
        "npm copyleft-probe 1.0.0: GPL-3.0-only: license outside the shipped allowlist",
        "npm no-license 2.0.0: (none): license outside the shipped allowlist",
    ]
    names = {c["name"] for c in components(bom).values()}
    assert {"copyleft-probe", "no-license"} <= names and "dev-tool" not in names


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "release_bom.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_the_command_is_reproducible_and_reports_its_verdict(tmp_path: Path) -> None:
    records = tmp_path / "licenses.json"
    records.write_text(json.dumps(lock_records()), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
    first = run("--licenses", str(records), "--version", "1.0.0", "--out", str(tmp_path / "a"), env=env)
    second = run("--licenses", str(records), "--version", "1.0.0", "--out", str(tmp_path / "b"), env=env)
    assert first.returncode == 0 and second.returncode == 0, first.stdout + first.stderr
    assert "release license check: ok" in first.stdout
    for name in ("tabrion-studio-1.0.0.cdx.json", "third-party-licenses.md"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()

    dated = run(
        "--licenses",
        str(records),
        "--version",
        "1.0.0",
        "--out",
        str(tmp_path / "c"),
        env={**env, "SOURCE_DATE_EPOCH": "1800000000"},
    )
    assert dated.returncode == 0
    bom = json.loads((tmp_path / "c" / "tabrion-studio-1.0.0.cdx.json").read_text(encoding="utf-8"))
    assert bom["metadata"]["timestamp"] == "2027-01-15T08:00:00Z"
    assert (
        bom["serialNumber"]
        != json.loads((tmp_path / "a" / "tabrion-studio-1.0.0.cdx.json").read_text())["serialNumber"]
    )

    bad = json.loads(records.read_text(encoding="utf-8"))
    next(r for r in bad if r["name"] == "uvicorn")["license_expression"] = "GPL-2.0-only"
    records.write_text(json.dumps(bad), encoding="utf-8")
    failed = run("--licenses", str(records), "--out", str(tmp_path / "d"), env=env)
    assert failed.returncode == 1
    assert "DISALLOWED python uvicorn" in failed.stdout and "release license check: FAILED (1)" in failed.stdout

    missing = run("--licenses", str(tmp_path / "nope.json"), "--out", str(tmp_path / "e"), env=env)
    assert missing.returncode == 2 and "release_bom:" in missing.stderr


def test_a_malformed_lock_is_refused() -> None:
    with pytest.raises(ValueError, match="line 1"):
        rb.read_lock("fastapi>=0.1\n")
    with pytest.raises(ValueError, match="git pin"):
        rb.read_git_pins("tabrion @ git+https://github.com/peterkarlhuber/tabrion@master\n")
