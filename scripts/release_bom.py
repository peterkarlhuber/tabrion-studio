#!/usr/bin/env python3
"""Release SBOM and third-party license report of Tabrion Studio (STU-028, 07 G-R, 03 T-11).

    python -I scripts/collect_licenses.py > licenses-linux.json        # in the installed runtime environment
    python -I scripts/release_bom.py --licenses licenses-linux.json --out release/

The bill of materials comes from the lockfiles, not from one machine's environment, so it is the same on every
computer for one commit:

* Python: every pin of ``constraints/runtime-lock.txt`` (TD-031; name, version, environment marker, the SHA-256 of
  every file) and the Tabrion commit of ``constraints/tabrion-integration.txt``;
* frontend: every package of ``frontend/package-lock.json`` that is not ``dev`` (what can end up in the bundled
  frontend; build and test tools never ship).

The licenses of the Python packages are not in the lock. They come from one or more ``--licenses`` files written by
``scripts/collect_licenses.py`` in an environment that has only the wheel and the lock installed (what users get).
Without ``--licenses`` the script reads the distributions of the interpreter that runs it. Give one file per
platform (Linux, macOS, Windows) to cover the pins whose marker applies on one platform only.

Writes into ``--out``:

* ``tabrion-studio-<version>.cdx.json``: CycloneDX 1.5 JSON. Byte for byte the same for the same inputs: the serial
  number is derived from the content, and a timestamp is written only when ``SOURCE_DATE_EPOCH`` is set.
* ``third-party-licenses.md``: the license report G-R asks for (07 section 6): every shipped package with its
  license and the policy verdict, the psycopg LGPL-3.0 notice and the frontend licenses.

The policy is the one of ``scripts/check_licenses.py`` (STU-031). Exit code 1 when a package is disallowed (copyleft
outside the named exceptions, an unknown license, no license), when a lock pin without a marker has no license
record, or when the recorded version differs from the pin. A pin with a marker that no ``--licenses`` file covers is
reported as "not verified"; ``--strict`` (the release run) fails on it too. Exit code 2 for unreadable input.
Standard library only.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import quote
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_licenses as policy
import collect_licenses

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "constraints" / "runtime-lock.txt"
TABRION_PIN = ROOT / "constraints" / "tabrion-integration.txt"
NPM_LOCK = ROOT / "frontend" / "package-lock.json"
VERSION_FILE = ROOT / "src" / "tabrion_studio" / "_version.py"
REPOSITORY = "https://github.com/peterkarlhuber/tabrion-studio"

_PIN = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9.+!_-]+)"
    r"(?:\s*;\s*(?P<marker>.+?))?(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)"
)
_GIT_PIN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*@\s*git\+(?P<url>https://\S+?)@(?P<sha>[0-9a-f]{40})")

# Status words of the report.
ALLOWED = "allowed"
EXCEPTION = "allowed (exception)"
DISALLOWED = "DISALLOWED"
UNVERIFIED = "not verified"

PSYCOPG_NOTICE = """\
psycopg and psycopg-binary are licensed under the GNU Lesser General Public License v3.0 (LGPL-3.0-only).
Tabrion Studio does not include, embed or modify them: pip installs them as separate distributions into the
user's environment, Studio imports them dynamically, and the user can replace them with any compatible build. Their
license text is in each installed distribution's metadata (`.dist-info/licenses/`); the source code is at
<https://github.com/psycopg/psycopg> (tag `{version}`). psycopg-binary bundles libpq and other libraries under their
own licenses (PostgreSQL License, OpenSSL/Apache-2.0 and others; see the wheel's `psycopg_binary.libs` notices).
Any bundled distribution of Studio (for example a later desktop package) must keep psycopg replaceable and ship
these license texts (14 section 4.2 item 6, risk R-14)."""


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Component:
    ecosystem: str  # "python" or "npm"
    name: str
    version: str
    purl: str
    license: str = ""
    status: str = UNVERIFIED
    note: str = ""
    marker: str = ""
    vcs: str = ""  # "git+https://...@<sha>" for a Git pin; its version comes from the license record
    hashes: list[tuple[str, str]] = field(default_factory=list)  # (algorithm, hex)
    properties: list[tuple[str, str]] = field(default_factory=list)


def read_version(path: Path = VERSION_FILE) -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"{path}: no __version__")
    return match[1]


def read_lock(text: str) -> list[Component]:
    """The pins of ``runtime-lock.txt`` (``runtime_lock.py check`` validates the format; this only reads it)."""
    components = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"runtime lock line {number} is not 'name==version [; marker] --hash=sha256:...'")
        name = canonical(match["name"])
        hashes = sorted(h.removeprefix("--hash=sha256:") for h in match["hashes"].split())
        components.append(
            Component(
                "python",
                name,
                match["version"],
                f"pkg:pypi/{name}@{match['version']}",
                marker=(match["marker"] or "").strip(),
                hashes=[("SHA-256", h) for h in hashes],
            )
        )
    if not components:
        raise ValueError("the runtime lock pins nothing")
    return components


def read_git_pins(text: str) -> list[Component]:
    """``name @ git+https://...@<40 hex>`` lines of the Tabrion pin file."""
    components = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _GIT_PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"the git pin file has a line that is not 'name @ git+https://...@<sha>': {line!r}")
        name = canonical(match["name"])
        vcs = f"git+{match['url']}@{match['sha']}"
        components.append(
            Component("python", name, match["sha"], f"pkg:pypi/{name}?vcs_url={quote(vcs, safe='')}", vcs=vcs)
        )
    return components


def npm_components(lock: dict[str, Any]) -> tuple[list[Component], int]:
    """The shipped (not ``dev``) packages of an npm lockfile, judged by the STU-031 policy, and the dev count."""
    verdicts = {(f.name, f.version, f.note): f for f in policy.check_npm(lock)}
    components = []
    dev = 0
    for path, meta in sorted(lock["packages"].items()):
        if not path or not isinstance(meta, dict) or meta.get("link"):
            continue
        if meta.get("dev"):
            dev += 1
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        version = str(meta.get("version", ""))
        finding = verdicts[(name, version, "shipped")]
        component = Component(
            "npm",
            name,
            version,
            f"pkg:npm/{quote(name, safe='/')}@{version}",
            license=finding.license if finding.license != "(none)" else "",
            status=ALLOWED if finding.allowed else DISALLOWED,
            note="" if finding.allowed else "license outside the shipped allowlist",
        )
        integrity = str(meta.get("integrity") or "")
        algorithm, _, digest = integrity.partition("-")
        if algorithm in {"sha512", "sha384", "sha256"} and digest:
            hex_digest = base64.b64decode(digest).hex()
            component.hashes.append(
                ({"sha512": "SHA-512", "sha384": "SHA-384", "sha256": "SHA-256"}[algorithm], hex_digest)
            )
        if meta.get("resolved"):
            component.properties.append(("tabrion-studio:resolved", str(meta["resolved"])))
        if path.count("node_modules/") > 1:
            component.properties.append(("tabrion-studio:npm-path", path))
        components.append(component)
    return components, dev


def judge_python(components: list[Component], records: list[dict[str, object]]) -> None:
    """Set license, status and note of each Python component from the collected license records."""
    by_name: dict[str, list[dict[str, object]]] = {}
    for record in records:
        by_name.setdefault(canonical(str(record.get("name", ""))), []).append(record)
    for component in components:
        found = by_name.get(component.name, [])
        if not found:
            component.license = ""
            if component.marker:
                component.status = UNVERIFIED
                component.note = "no license record: the marker does not apply where the licenses were collected"
            else:
                component.status = DISALLOWED
                component.note = "no license record (not installed where the licenses were collected)"
            continue
        versions = sorted({str(r.get("version", "")) for r in found})
        if component.vcs:
            # A Git pin: the record names the package version, the purl and a property keep the commit.
            if len(versions) != 1:
                component.status = DISALLOWED
                component.note = f"license records of several versions: {', '.join(versions)}"
                continue
            component.version = versions[0]
            component.purl = component.purl.replace("?", f"@{component.version}?", 1)
        else:
            # The lock may pin one name at two versions under different markers: use the record of this version.
            found = [r for r in found if str(r.get("version", "")) == component.version]
            if not found:
                component.status = UNVERIFIED if component.marker else DISALLOWED
                component.note = (
                    f"the license records are for version {', '.join(versions)}, the lock pins {component.version}"
                )
                continue
        findings = policy.check_python([found[0]])
        finding = findings[0]
        component.license = "" if finding.license == "(none)" else finding.license
        if not finding.allowed:
            component.status = DISALLOWED
            component.note = "copyleft or unknown license outside the policy" if component.license else "no license"
        elif finding.note.startswith("exception"):
            component.status = EXCEPTION
            component.note = finding.note.removeprefix("exception: ")
        else:
            component.status = ALLOWED


def _cdx_component(component: Component) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "library",
        "bom-ref": component.purl,
        "name": component.name,
        "version": component.version,
        "scope": "required",
        "purl": component.purl,
    }
    if component.license:
        entry["licenses"] = [{"expression": component.license}]
    if component.hashes and component.ecosystem == "npm":
        entry["hashes"] = [{"alg": alg, "content": digest} for alg, digest in component.hashes]
    properties = [("tabrion-studio:license-policy", component.status)]
    if component.marker:
        properties.append(("tabrion-studio:marker", component.marker))
    if component.ecosystem == "python":
        # One pin covers several files (one wheel per platform); CycloneDX allows one hash per algorithm, so the
        # file hashes of the lock are listed as properties.
        properties += [("tabrion-studio:lock-sha256", digest) for _, digest in component.hashes]
    if component.vcs:
        properties.append(("tabrion-studio:vcs", component.vcs))
    properties += component.properties
    entry["properties"] = [{"name": n, "value": v} for n, v in properties]
    return entry


def sbom(version: str, components: list[Component]) -> dict[str, Any]:
    root_ref = f"pkg:pypi/tabrion-studio@{version}"
    body: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "tools": {"components": [{"type": "application", "name": "tabrion-studio scripts/release_bom.py"}]},
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "tabrion-studio",
                "version": version,
                "purl": root_ref,
                "licenses": [{"expression": "MIT"}],
                "externalReferences": [{"type": "vcs", "url": REPOSITORY}],
            },
        },
        "components": [_cdx_component(c) for c in sorted(components, key=lambda c: (c.ecosystem, c.name, c.purl))],
    }
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        body["metadata"]["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    body["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'tabrion-studio-sbom:' + digest)}"
    return {k: body[k] for k in ("bomFormat", "specVersion", "serialNumber", "version", "metadata", "components")}


def _table(components: list[Component]) -> list[str]:
    lines = ["| Package | Version | License | Policy | Note |", "|---|---|---|---|---|"]
    for c in sorted(components, key=lambda c: (c.name, c.version)):
        note = c.note + (f" (marker `{c.marker}`)" if c.marker else "")
        lines.append(f"| {c.name} | {c.version} | {c.license or '(none)'} | {c.status} | {note.strip()} |")
    return lines


def _summary(components: list[Component]) -> str:
    counts: dict[str, int] = {}
    for c in components:
        counts[c.license or "(none)"] = counts.get(c.license or "(none)", 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def report(version: str, python: list[Component], npm: list[Component], dev_count: int, failures: list[str]) -> str:
    psycopg = next((c for c in python if c.name == "psycopg"), None)
    lines = [
        f"# Third-party licenses of Tabrion Studio {version}",
        "",
        "Written by `scripts/release_bom.py` from `constraints/runtime-lock.txt`,",
        "`constraints/tabrion-integration.txt` and `frontend/package-lock.json`, with the Python license records of",
        "`scripts/collect_licenses.py`. Policy: `scripts/check_licenses.py` (STU-031). Tabrion Studio itself is MIT;",
        "its names and logos are not (TRADEMARKS.md).",
        "",
        "## Result",
        "",
        f"- Python runtime packages: {len(python)} ({_summary(python)})",
        f"- Frontend packages that can ship in the bundle: {len(npm)} ({_summary(npm)})",
        f"- Frontend build and test tools (never shipped, checked by the same policy, not listed): {dev_count}",
        "- Policy: " + ("**passed**" if not failures else f"**FAILED** ({len(failures)} finding(s))"),
    ]
    lines += [f"  - {f}" for f in failures]
    unverified = [c for c in python if c.status == UNVERIFIED]
    if unverified:
        lines += [
            "",
            f"{len(unverified)} pin(s) apply only on platforms or Python versions whose license records were not",
            "given; collect them there and pass every file with `--licenses` (a release run uses `--strict`).",
        ]
    lines += ["", "## Python runtime packages", "", *_table(python), ""]
    lines += ["## Frontend packages", "", *_table(npm), ""]
    lines += ["## psycopg (LGPL-3.0)", "", PSYCOPG_NOTICE.format(version=psycopg.version if psycopg else "3.x"), ""]
    return "\n".join(lines)


def build(
    licenses: list[list[dict[str, object]]],
    lock_text: str,
    pin_text: str,
    npm_lock: dict[str, Any],
    version: str,
    strict: bool,
) -> tuple[dict[str, Any], str, list[str]]:
    """The SBOM, the report and the list of policy failures (empty = passed)."""
    records = [record for batch in licenses for record in batch]
    python = read_lock(lock_text) + read_git_pins(pin_text)
    judge_python(python, records)
    npm, dev_count = npm_components(npm_lock)
    dev_failures = [f for f in policy.check_npm(npm_lock) if f.note == "dev" and not f.allowed]
    failures = [
        f"{c.ecosystem} {c.name} {c.version}: {c.license or '(none)'}: {c.note or 'disallowed'}"
        for c in python + npm
        if c.status == DISALLOWED or (strict and c.status == UNVERIFIED)
    ]
    failures += [f"npm (dev) {f.name} {f.version}: {f.license}: outside the dev allowlist" for f in dev_failures]
    return sbom(version, python + npm), report(version, python, npm, dev_count, failures), failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release SBOM (CycloneDX) and license report (STU-028)")
    parser.add_argument("--licenses", type=Path, action="append", default=[], help="collect_licenses.py output")
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--tabrion-pin", type=Path, default=TABRION_PIN)
    parser.add_argument("--npm-lock", type=Path, default=NPM_LOCK)
    parser.add_argument("--version", default=None, help="Studio version (default: src/tabrion_studio/_version.py)")
    parser.add_argument("--strict", action="store_true", help="fail on pins no license record covers")
    parser.add_argument("--out", type=Path, required=True, help="folder for the SBOM and the report")
    args = parser.parse_args(argv)
    try:
        batches: list[list[dict[str, object]]] = [
            json.loads(path.read_text(encoding="utf-8")) for path in args.licenses
        ] or [collect_licenses.collect()]
        version = args.version or read_version()
        bom, text, failures = build(
            batches,
            args.lock.read_text(encoding="utf-8"),
            args.tabrion_pin.read_text(encoding="utf-8"),
            json.loads(args.npm_lock.read_text(encoding="utf-8")),
            version,
            args.strict,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"release_bom: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    sbom_path = args.out / f"tabrion-studio-{version}.cdx.json"
    sbom_path.write_text(json.dumps(bom, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (args.out / "third-party-licenses.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {sbom_path} ({len(bom['components'])} components) and {args.out / 'third-party-licenses.md'}")
    for failure in failures:
        print(f"DISALLOWED {failure}")
    print("release license check: " + (f"FAILED ({len(failures)})" if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
