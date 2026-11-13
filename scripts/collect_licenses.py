#!/usr/bin/env python3
"""Write the license of every installed distribution as JSON (STU-031).

Run it with the interpreter of the environment to inspect, in isolated mode, so that nothing from the current folder
is imported:

    <venv>/bin/python -I scripts/collect_licenses.py > licenses.json

Standard library only. For each distribution it reports the SPDX expression from ``License-Expression`` (core
metadata 2.4) or, failing that, the trove classifiers and the ``License`` field, exactly as declared;
scripts/check_licenses.py normalizes and judges them. A distribution installed from a version control system also
gets ``vcs_url`` and ``vcs_commit`` from its PEP 610 ``direct_url.json``, so scripts/release_bom.py can check that
the record belongs to the pinned commit (STU-028). Local file URLs are never written.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import sys

# Packaging tools of the virtual environment itself; they are not dependencies of Tabrion Studio.
TOOLING = {"pip", "setuptools", "wheel"}


def _first(meta: md.PackageMetadata, field: str) -> str:
    return str((meta.get_all(field) or [""])[0]).strip()


def _vcs(dist: md.Distribution) -> dict[str, str]:
    """``vcs_url`` and ``vcs_commit`` of a distribution installed from Git (PEP 610), else nothing."""
    try:
        raw = dist.read_text("direct_url.json")
        data = json.loads(raw) if raw else None
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    info = data.get("vcs_info")
    url = data.get("url")
    if not isinstance(info, dict) or not isinstance(url, str) or not url.startswith("https://"):
        return {}
    commit = info.get("commit_id")
    return {"vcs_url": url, "vcs_commit": commit} if isinstance(commit, str) and commit else {}


def collect() -> list[dict[str, object]]:
    seen: dict[str, dict[str, object]] = {}
    for dist in md.distributions():
        meta = dist.metadata
        name = (meta["Name"] or "").strip()
        if not name or name.lower() in TOOLING:
            continue
        key = name.lower().replace("_", "-")
        license_field = _first(meta, "License")
        seen[key] = {
            "name": name,
            "version": meta["Version"],
            "license_expression": _first(meta, "License-Expression"),
            "classifiers": sorted(c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")),
            # A full license text in the License field is not useful here; keep only short declarations.
            "license": license_field if len(license_field) <= 120 and "\n" not in license_field else "",
            **_vcs(dist),
        }
    return [seen[k] for k in sorted(seen)]


def main() -> int:
    json.dump(collect(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
