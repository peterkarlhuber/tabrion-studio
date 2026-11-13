#!/usr/bin/env python3
"""License policy check for Tabrion Studio (STU-031, 03 section 3.2, STU-ADR-0004).

    python scripts/check_licenses.py --python licenses.json --npm frontend/package-lock.json

``--python`` takes the output of scripts/collect_licenses.py for an environment that has only the wheel and its
runtime dependencies installed (what users install). ``--npm`` takes the frontend lockfile (lockfileVersion 2 or
3, which records each package's license); packages marked ``dev`` are build and test tools that never ship.

Policy:

* Shipped code (Python runtime closure, npm packages without ``dev``): MIT, MIT-0, ISC, BSD (0BSD, 2- and
  3-clause), Apache-2.0, PSF-2.0 / Python-2.0, Unlicense, CC0-1.0.
* Exceptions by name: psycopg and psycopg-binary (LGPL-3.0-only), loaded dynamically from site-packages and
  replaceable by the user, so the LGPL is met by shipping the license text and a source reference (14 section 4.2
  item 6; G-R lists the psycopg LGPL notice).
* Dev-only npm packages may also use MPL-2.0 (axe-core, LightningCSS), BlueOak-1.0.0 and CC-BY-4.0 (the
  caniuse-lite data used by the build); none of them is bundled into the shipped frontend.
* Anything else, including GPL/AGPL/LGPL outside the exceptions, SSPL, a commercial license or a missing license,
  fails the check with exit code 1.

An SPDX ``OR`` expression passes if any alternative passes, an ``AND`` expression only if all parts pass;
parentheses group, and ``AND`` binds tighter than ``OR`` (review L-6).
Standard library only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

ALLOWED_SHIPPED = frozenset(
    {
        "MIT",
        "MIT-0",
        "ISC",
        "0BSD",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "PSF-2.0",
        "Python-2.0",
        "Unlicense",
        "CC0-1.0",
    }
)
ALLOWED_DEV_ONLY = frozenset({"MPL-2.0", "BlueOak-1.0.0", "CC-BY-4.0"})
# Python distributions that may carry a license outside the allowlist, with the reason.
PYTHON_EXCEPTIONS = {
    "psycopg": ("LGPL-3.0-only", "dynamically loaded, replaceable; license text and source reference (14 §4.2)"),
    "psycopg-binary": ("LGPL-3.0-only", "binary build of psycopg; same terms (14 §4.2)"),
}

# Trove classifiers and free-text License fields that older packages use instead of an SPDX expression.
_ALIASES = {
    "mit license": "MIT",
    "mit": "MIT",
    "mit-0": "MIT-0",
    "bsd license": "BSD-3-Clause",
    "bsd": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "apache software license": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "python software foundation license": "PSF-2.0",
    "isc license (iscl)": "ISC",
    "isc license": "ISC",
    "isc": "ISC",
    "the unlicense (unlicense)": "Unlicense",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.0-or-later",
    "gnu general public license v2 (gplv2)": "GPL-2.0-only",
    "gnu general public license v3 (gplv3)": "GPL-3.0-only",
    "gnu affero general public license v3": "AGPL-3.0-only",
}


@dataclass(frozen=True)
class Finding:
    ecosystem: str
    name: str
    version: str
    license: str
    allowed: bool
    note: str = ""


def normalize(raw: str) -> str:
    """One license name as SPDX where it is known, else the stripped input."""
    text = raw.strip()
    if text.startswith("License :: "):
        text = text.rsplit(" :: ", 1)[-1]
    return _ALIASES.get(text.lower(), text)


def expression_allowed(expression: str, allowed: frozenset[str]) -> bool:
    """Judge an SPDX expression: OR needs one allowed alternative, AND needs every part allowed.

    Parentheses group as in SPDX, and AND binds tighter than OR, so ``(MIT OR GPL-3.0-only) AND LGPL-2.1-only``
    fails (review L-6 of ``pr83-stu028-review.md``). ``WITH`` adds an exception, which only adds permissions, so
    ``Apache-2.0 WITH LLVM-exception`` is judged as ``Apache-2.0``. A license name may be several words (the free-text
    ``License`` field of older packages); the operators are the upper-case words ``OR``, ``AND`` and ``WITH``. An
    expression that does not parse (an unbalanced parenthesis, an operator without an operand) is not allowed.
    """
    tokens = _tokens(expression)
    if not tokens:
        return False
    try:
        value, end = _parse_or(tokens, 0, allowed)
    except _SyntaxError:
        return False
    return value and end == len(tokens)


_OPERATORS = frozenset({"OR", "AND", "WITH", "(", ")"})


class _SyntaxError(ValueError):
    """An SPDX expression that does not parse; it is judged as not allowed."""


def _tokens(expression: str) -> list[str]:
    """Parentheses, the operators, and each license name (the text of consecutive other words, as written).

    A parenthesis right after a word of a name belongs to the name, as in the trove classifier name
    ``GNU General Public License (GPL)``: in SPDX a group opens only at the start or after an operator.
    """
    tokens: list[str] = []
    start = end = -1  # the span of the license name being read
    depth = 0  # open parentheses inside that name
    for match in re.finditer(r"\(|\)|[^\s()]+", expression):
        word = match.group()
        if start >= 0:  # inside a name
            if depth or word == "(" or word not in _OPERATORS:
                depth += {"(": 1, ")": -1}.get(word, 0)
                end = match.end()
                continue
            tokens.append(expression[start:end])
            start = -1
        if word in _OPERATORS:
            tokens.append(word)
        else:
            start, end = match.start(), match.end()
    if start >= 0:
        if depth:  # a parenthesis left open inside a name (workflow review L-1 of PR #90)
            return []
        tokens.append(expression[start:end])
    return tokens


def _parse_or(tokens: list[str], i: int, allowed: frozenset[str]) -> tuple[bool, int]:
    value, i = _parse_and(tokens, i, allowed)
    while i < len(tokens) and tokens[i] == "OR":
        right, i = _parse_and(tokens, i + 1, allowed)
        value = value or right
    return value, i


def _parse_and(tokens: list[str], i: int, allowed: frozenset[str]) -> tuple[bool, int]:
    value, i = _parse_with(tokens, i, allowed)
    while i < len(tokens) and tokens[i] == "AND":
        right, i = _parse_with(tokens, i + 1, allowed)
        value = value and right
    return value, i


def _parse_with(tokens: list[str], i: int, allowed: frozenset[str]) -> tuple[bool, int]:
    if i >= len(tokens):
        raise _SyntaxError("an operand is missing")
    word = tokens[i]
    if word == "(":
        value, i = _parse_or(tokens, i + 1, allowed)
        if i >= len(tokens) or tokens[i] != ")":
            raise _SyntaxError("a parenthesis is not closed")
        i += 1
    elif word in _OPERATORS:
        raise _SyntaxError(f"{word} where a license is expected")
    else:
        value, i = normalize(word) in allowed, i + 1
    if i < len(tokens) and tokens[i] == "WITH":
        # The exception (one identifier) only adds permissions; the license before it is judged.
        if i + 1 >= len(tokens) or tokens[i + 1] in _OPERATORS:
            raise _SyntaxError("WITH without an exception")
        i += 2
    return value, i


def python_license(entry: dict[str, object]) -> str:
    """The declared license of one collected distribution as an SPDX-like expression ("" if none)."""
    expression = str(entry.get("license_expression") or "").strip()
    if expression:
        return expression
    raw = entry.get("classifiers")
    listed = raw if isinstance(raw, list) else []
    classifiers = [normalize(str(c)) for c in listed if str(c) != "License :: OSI Approved"]
    if classifiers:
        return " OR ".join(sorted(set(classifiers)))
    return normalize(str(entry.get("license") or ""))


def check_python(entries: list[dict[str, object]]) -> list[Finding]:
    findings = []
    for entry in entries:
        name = str(entry["name"])
        version = str(entry.get("version", ""))
        declared = python_license(entry)
        key = name.lower().replace("_", "-").replace(".", "-")
        if key in PYTHON_EXCEPTIONS:
            expected, reason = PYTHON_EXCEPTIONS[key]
            ok = normalize(declared) == expected or expression_allowed(declared, ALLOWED_SHIPPED)
            findings.append(
                Finding("python", name, version, declared or "(none)", ok, f"exception: {reason}" if ok else "")
            )
            continue
        findings.append(
            Finding("python", name, version, declared or "(none)", expression_allowed(declared, ALLOWED_SHIPPED))
        )
    return findings


def npm_license(meta: dict[str, object]) -> str:
    """The declared license of one npm lockfile entry ("" if none); the legacy ``{"type": ...}`` form too."""
    declared = meta.get("license")
    if isinstance(declared, dict):  # legacy {"type": "MIT"}
        declared = declared.get("type")
    return str(declared or "")


def npm_allowed(declared: str, dev: bool) -> bool:
    """The policy for one npm package: the shipped allowlist, plus the dev-only licenses for ``dev`` packages."""
    return expression_allowed(declared, ALLOWED_SHIPPED | ALLOWED_DEV_ONLY if dev else ALLOWED_SHIPPED)


def check_npm(lock: dict[str, object]) -> list[Finding]:
    packages = lock.get("packages")
    if not isinstance(packages, dict) or int(str(lock.get("lockfileVersion", 0))) < 2:
        raise ValueError("the npm lockfile needs lockfileVersion 2 or 3 (with a packages section)")
    findings = []
    for path, meta in sorted(packages.items()):
        if not path or not isinstance(meta, dict) or meta.get("link"):
            continue  # the root project and workspace links
        name = path.rsplit("node_modules/", 1)[-1]
        declared = npm_license(meta)
        # Conservative: only "dev" packages count as tools; "devOptional" ones may be installed in production.
        dev = bool(meta.get("dev"))
        findings.append(
            Finding(
                "npm",
                name,
                str(meta.get("version", "")),
                declared or "(none)",
                npm_allowed(declared, dev),
                "dev" if dev else "shipped",
            )
        )
    return findings


def report(findings: list[Finding]) -> str:
    bad = [f for f in findings if not f.allowed]
    lines = []
    for ecosystem in ("python", "npm"):
        mine = [f for f in findings if f.ecosystem == ecosystem]
        if not mine:
            continue
        counts: dict[str, int] = {}
        for f in mine:
            counts[f.license] = counts.get(f.license, 0) + 1
        summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append(f"{ecosystem}: {len(mine)} package(s): {summary}")
        lines += [f"  exception {f.name} {f.version}: {f.license} ({f.note})" for f in mine if "exception" in f.note]
    for f in bad:
        lines.append(f"DISALLOWED {f.ecosystem} {f.name} {f.version}: {f.license} {f.note}".rstrip())
    lines.append("license check: " + (f"FAILED ({len(bad)} disallowed)" if bad else "ok"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="License policy check (STU-031)")
    parser.add_argument("--python", type=Path, help="JSON from scripts/collect_licenses.py")
    parser.add_argument("--npm", type=Path, help="frontend/package-lock.json")
    args = parser.parse_args(argv)
    if args.python is None and args.npm is None:
        parser.error("give --python and/or --npm")
    findings: list[Finding] = []
    if args.python is not None:
        findings += check_python(json.loads(args.python.read_text(encoding="utf-8")))
    if args.npm is not None:
        findings += check_npm(json.loads(args.npm.read_text(encoding="utf-8")))
    if not findings:
        print("license check: FAILED (nothing to check)")
        return 1
    print(report(findings))
    return 1 if any(not f.allowed for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
