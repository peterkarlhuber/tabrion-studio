# Release checklist 1.0 (STU-028)

STU-028 ("Docs 1.0, clean install, release preparation") asks for a release checklist for 1.0, an SBOM and a
license report, a human security review before any public release (E-QA2) and publication only after G-R (09,
STU-028). This page is that checklist: every step of a 1.0 release, how it is verified, and its state.

!!! danger "Preparation only: nothing is released or published"
    G-R is **not** granted. This checklist prepares a release; it does not release anything. No tag, no GitHub
    Release, no upload to PyPI or npm and no public announcement happens because of it. The hard stops below are
    decisions of the owner and of a human reviewer, not of an agent or a CI job.

Status words: **Met** (done and checked), **Partly** (what is missing is named), **Open**, **Owner** (an owner
decision or action; agents do not do it). State as of 2026-11-13, master `d72a14f`.

## Hard stops

No step after a hard stop starts before the stop is cleared. Each is recorded with its date and the person who
cleared it, in this page and in `docs/TODO.md`.

| Stop | What clears it | Who | Source | State |
|---|---|---|---|---|
| **H-1 G-S1** | The owner's decision on the MVP security review: the independent review is accepted (re-review at `2a8a229`); the owner's decisions are recorded in [Security review preparation](../architecture/security-review-prep.md#owner-decisions-on-2026-11-13) | owner | 03 §8, 09 STU-016 | **Cleared 2026-11-13 by the owner**: the 13 recommendations accepted (each option A); **G-S1 passed** for master `9a5d4faa69f1c974999d258b36258fbc10b3323f`, on the condition that all 21 required CI jobs are green on that SHA, met on 2026-11-13 (run 43457105100). G-R is not granted; H-2 remains mandatory |
| **H-2 Human security review (E-QA2)** | A security review by a **human** reviewer of the release candidate (not an agent review; agent reviews count for internal use only), its findings fixed or accepted by the owner, the report filed. Mandatory before any public release of Studio; possible costs need the owner's approval | owner, human reviewer | 00 §13 E-QA2, 09 §1 and STU-028, 07 §5 G-R, 10 STU-ADR-0002 | **Open**: not started |
| **H-3 G-R** | The owner's approval for **this** publication (every publication, pre-releases included; there is no standing approval). G-R reads: the license and third-party license report (with the psycopg LGPL-3.0 notice and the frontend licenses), the trademark check, the SBOM, the release notes, AC-4 green, and for Studio the result of H-2 | owner | 07 §5, 12 §1 and §11, 10 TAB-ADR-0009 | **Owner**: not granted |

G-S2 (STU-026, the Data Editor) is no stop for 1.0: 1.0 ships **without** the Data Editor (03 §8), and STU-026 stays
locked.

### G-R preconditions from G-S1

With the G-S1 recommendations (2026-11-13) the owner made these items preconditions of G-R: H-3 is not cleared
while one of them is open. Each is tracked in `docs/TODO.md`; the decision numbers are those of
[Security review preparation](../architecture/security-review-prep.md#owner-decisions-on-2026-11-13).

| # | Precondition | Decision | Tracked in | State |
|---|---|---|---|---|
| GR-1 | Sandboxed browsers (Firefox snap, Flatpak) can open Studio: the redirect-file fix | 6 | TD-002 | **Open** (the hint is done, the fix is not) |
| GR-2 | The launch flow checked in Firefox and WebKit against the built wheel | 6 | TD-007 | **Open** |
| GR-3 | Windows: the state folder's permissions checked inside the user profile too | 7 | TD-003 | **Open** |
| GR-4 | A check on a real Windows machine of the refused device names (for example `CON.sql`) and of OneDrive-synced and other reparse-point folders (STU-020 decision D-4); so far CI and tests only | 7 (and 11) | TD-003 | **Open** |
| GR-5 | tabrion's log redaction also strips database details in the middle of a line, and Studio takes the new tabrion pin | 13 | TD-056 (owner: tabrion) | **Open** |

These are in addition to the human security review (H-2) and to every step below. The other decisions accept a
residual risk or name follow-up work that is not a G-R precondition (TD-031's nightly job, TD-033's usability rest,
TD-062).

### Install paths and their remaining risks

The secured install path is pip with `--require-hashes` from `constraints/runtime-lock.txt`
([Installation](../guide/installation.md#install-from-a-commit), D-1). The other documented paths are less secured,
and the release notes (E-4) name this:

- **`uv tool install`** takes the lock's exact versions but does **not** verify the lock's hashes (step 3a of the
  [clean install](#clean-install-2026-11-13-linux)).
- **pipx** installs with the Tabrion pin only and resolves every other dependency afresh from PyPI, without the
  lock's versions and without hashes.
- Also on the pip path: the `pip install --upgrade "pip>=26.1"` step is not hashed, and pip builds Studio and
  Tabrion with build backends fetched without hashes (build time only; TD-031).

## Steps

### A. Scope of 1.0

| # | Step | How it is verified | State |
|---|---|---|---|
| A-1 | Every 1.0 package done: STU-020 (favorites, history, scripts, format, completion), STU-021 (export), STU-022 (DDL, policies, row estimates), STU-023 (ER diagram), STU-024 (Levuvo migration formats), STU-025 (diagnosis report, backup hints), with their reviews accepted | the `TABRION STU-0xx` rows of `docs/TODO.md` say "Done" with the review; their acceptance tests (09) green on the release commit | **Partly**: all six are merged on `master`. STU-020: #71, #73, #82; review accepted (#73 accepted; #71 accepted with conditions, whose remaining parts are the check of F-2 and F-6 on a real Windows machine and the owner's decision D-4, a G-S1 item). STU-021: #33. STU-022: #38, #54. STU-023: #72; re-review accepted; its follow-ups are TD-052 (P3). STU-024: #30, #32. STU-025: #40, #47; the command-line report stays open (TD-036). The `TABRION STU-020` and `STU-023` rows of `docs/TODO.md` still say "In progress" and are corrected separately |
| A-2 | STU-054, the 1.0 UX acceptance: the 1.0 flows end to end without the Data Editor, de/en complete | its checklist (like [UX acceptance](../architecture/ux-acceptance.md) for the MVP) and its e2e suite | **Open**: not started |
| A-3 | Writes locked: every write attempt through the API is refused (03 §8 negative test) and the database refuses writes on the read-only path | `tests/security/test_read_only.py` (STU-T-010..012), `tests/security/test_queries.py::test_writes_and_several_statements_are_refused`, `::test_pg_writes_fail_and_one_statement_only`; STU-054's negative test | **Partly**: the read-only tests exist and run in CI; the 1.0 negative test over every route is STU-054's |
| A-4 | No Data Editor code is reachable (STU-026 locked behind G-S2) | review of the release diff; ADR 0003 | **Met** on `master` |

### B. Quality on the release commit

| # | Step | How it is verified | State |
|---|---|---|---|
| B-1 | Every required CI check green on the release commit (backend lint and tests on Python 3.11 to 3.14, security tests, T-2 path checks, PostgreSQL integration, frontend lint and unit, e2e in Chromium, Firefox and WebKit, build and install on three systems, license and supply chain, dependency audit, docs) and `ci-ok` | the commit status `ci-ok` of `.github/ci-manifest.json` ([CI](ci.md)) | Checked per commit; to be read for the release commit |
| B-2 | The nightly run green on the release commit (PostgreSQL 16/17/18, full matrix) | `nightly.yml` run of that commit | Open until a release commit exists |
| B-3 | Accessibility: no open WCAG 2.2 AA violation (axe in light/dark × en/de, the manual audit) | [Accessibility audit](../architecture/accessibility.md), STU-053, TD-022 | **Partly**: MVP pages audited; the 1.0 pages with STU-054 |
| B-4 | No P0 or P1 item of `docs/TODO.md` open against 1.0 without an owner's acceptance | read `docs/TODO.md` | **Open** |

### C. Build, artifacts and supply chain

| # | Step | How it is verified | State |
|---|---|---|---|
| C-1 | Version set: `src/tabrion_studio/_version.py` and `frontend/package.json` say `1.0.0`; `CHANGELOG.md` moves "Unreleased" to `1.0.0` with the date | a release pull request; `tabrion-studio --version` of the built wheel | **Open** (still `0.1.0.dev0`) |
| C-2 | Wheel and sdist built from the release commit with the hashed build backend (`PIP_CONSTRAINT=build-constraints.txt PIP_REQUIRE_HASHES=1`), `twine check --strict` passes | job `build and install`; [Building and testing](building.md#wheel) | Checked per commit in CI |
| C-3 | Reproducible: two builds of the release commit give the same wheel SHA-256 | `tests/packaging/test_artifacts.py::test_two_builds_give_the_same_wheel_hash` (CI); the hash recorded here | Checked per commit in CI |
| C-4 | Dependency audit: `pip-audit` without a finding on the locked versions, `npm audit --omit=dev --audit-level=high` clean | job `dependency audit` ([CI](ci.md#dependency-audit)) | Checked per commit in CI; TD-030 (moderate, dev tools only) stays open |
| C-5 | License policy: no shipped package outside the allowlist; copyleft and unknown licenses fail; psycopg's LGPL-3.0 is the only named exception | `scripts/check_licenses.py` in job `license and supply chain` (with its negative probes); `scripts/release_bom.py` (below) | **Met** on `master`: see [the run below](#sbom-and-license-report) |
| C-6 | Release SBOM (CycloneDX) and third-party license report, from the lockfiles, with the license records of all three systems and of Python 3.11 | `python -I scripts/release_bom.py --strict --licenses ... --out release/` exits 0 ([below](#sbom-and-license-report)); the CI SBOMs of job `license and supply chain` as a cross-check | **Partly**: generated on Linux (Python 3.13 and 3.11); the macOS and Windows license records are open, so `--strict` still lists the two Windows-only pins |
| C-7 | The runtime lock is current: no advisory against a pin, and refreshed about monthly ([Hashed runtime pins](ci.md#hashed-runtime-pins)) | advisories: job `dependency audit` (pip-audit on the locked versions); the lock is in step with `pyproject.toml`: `python scripts/runtime_lock.py check`; its age: the date of the last lock update, `git log -1 --format=%cs -- constraints/runtime-lock.txt`, read by hand (no check enforces an age) | Advisories and consistency checked per commit in CI; the age by hand at release time |

### D. Clean install

| # | Step | How it is verified | State |
|---|---|---|---|
| D-1 | The wheel of the release commit installs into a fresh environment from the hashed lock and the Tabrion pin, runs without Node.js, signs in, connects a SQLite profile read-only and reads it, stops | `scripts/smoke_wheel.py` (job `build and install` on Linux, macOS and Windows) | **Met** for `master` `d72a14f` on Linux, by hand ([below](#clean-install-2026-11-13-linux)); CI runs it on all three systems |
| D-2 | Clean install with **pipx** and **uv** on Linux, macOS and Windows (03 §8 for 1.0) | the commands of [Installation](../guide/installation.md#pipx-or-uv) in a fresh home folder: `--version`, a start, the health endpoint, `--stop`. Neither tool checks the lock's hashes (uv takes the lock's versions, pipx resolves afresh); the hash-checked install is pip with `--require-hashes` (D-1) | **Partly**: Linux passed for `uv tool install` and for pipx with pip as its installer and the Tabrion pin only ([below](#clean-install-2026-11-13-linux)); macOS and Windows open (TD-023) |
| D-3 | The guide's commands by hand on Windows (PowerShell) and macOS | the steps of [Installation](../guide/installation.md) | **Open** (TD-023) |
| D-4 | Browsers: the 1.0 flows in current Chrome, Edge, Firefox and Safari | e2e in Chromium, Firefox and WebKit (CI); by hand in Edge and Safari | **Partly**: CI covers the engines; Edge and Safari by hand open (TD-047) |

### E. Documentation

| # | Step | How it is verified | State |
|---|---|---|---|
| E-1 | The user guide covers every 1.0 feature (03 §10: complete docs) | [Documentation checklist](docs-checklist.md) D-1..D-24, all **Met** | **Partly**: see that page (the tag-dependent install wording, the diagnostics/CLI report of TD-036) |
| E-2 | The site builds without a warning, every link and anchor resolves | `mkdocs build --strict` (job `docs`) | Checked per commit in CI |
| E-3 | README status names 1.0 and what is in it; the install section names the tag instead of a commit | read `README.md`; `tests/unit/test_user_guide.py` | **Open** until the tag |
| E-4 | Release notes: the `1.0.0` section of `CHANGELOG.md`, and the residual risks and open items in short | read `CHANGELOG.md` | **Open** |
| E-5 | `SECURITY.md` names the supported version and the private reporting route | `tests/security/test_conventions.py` | **Partly**: the route is checked; the supported-version table changes with 1.0 |

### F. Legal

| # | Step | How it is verified | State |
|---|---|---|---|
| F-1 | License: `LICENSE` (MIT) and `TRADEMARKS.md` in the wheel and the sdist | `tests/packaging/test_artifacts.py` (job `build and install`) | Checked per commit in CI |
| F-2 | Third-party license report with the psycopg LGPL-3.0 notice and the frontend licenses | `third-party-licenses.md` from `scripts/release_bom.py` | **Met** for `master` on Linux (C-6 completes it) |
| F-3 | Trademark check of the name "Tabrion Studio" before the first public release (07 §5 G-R) | the owner's check, recorded here | **Owner** |

### G. Tag and publication

| # | Step | How it is verified | State |
|---|---|---|---|
| G-1 | H-1 cleared (G-S1) | recorded above | **Done** (2026-11-13, master `9a5d4fa`) |
| G-2 | H-2 cleared (human security review) | the review report and its findings | **Open** |
| G-3 | Annotated tag `v1.0.0` on the master merge commit of the release pull request, never moved (10 TAB-ADR-0009). A tag is allowed without G-R, but only by the owner's decision after G-S1 | `git tag -v` / the tag's commit equals the release commit | **Owner**: not done |
| G-4 | **H-3 G-R** for this publication | the owner's approval, recorded here; GR-1..GR-5 ([G-R preconditions](#g-r-preconditions-from-g-s1)) met | **Owner**: not granted |
| G-5 | Only after G-R: a GitHub Release with the wheel, the sdist, the SBOM and the license report (SHA-256 of each in the notes) | the release page; the hashes equal C-2 and C-6 | **Blocked by G-R**; there is no release workflow yet (`tests/security/test_workflows.py` refuses publish steps) |
| G-6 | Only after G-R and a new owner decision: PyPI. Distribution is by Git SHA pin for now (owner decision 2026-11-08); PyPI refuses a direct Git dependency, so Studio on PyPI also needs Tabrion on PyPI (TAB-043, its own G-R) and Trusted Publishing from a reviewed release workflow | the decision and TAB-043 | **Blocked by G-R** and the owner decision |
| G-7 | Only after G-R: any announcement | the owner | **Blocked by G-R** |

## SBOM and license report

`scripts/release_bom.py` writes the release SBOM and the license report from the lockfiles, so both are the same on
every computer for one commit (03 T-11, 07 TM-10: CycloneDX per release; 07 G-R: license and third-party license
report). Standard library only.

```bash
# in the fresh runtime environment of each system and Python version (wheel + lock, as in the guide):
<venv>/bin/python -I scripts/collect_licenses.py > licenses-<system>-py<version>.json
# then, on any computer, with every record file:
python -I scripts/release_bom.py --strict \
  --licenses licenses-linux-py3.13.json --licenses licenses-linux-py3.11.json \
  --licenses licenses-macos-py3.13.json --licenses licenses-windows-py3.13.json \
  --out release/
```

- **Contents:** every pin of `constraints/runtime-lock.txt` (version, marker, the SHA-256 of every file), Tabrion
  from `constraints/tabrion-integration.txt` (purl with the Git commit), every frontend package that is not `dev` in
  `frontend/package-lock.json` (with its npm integrity hashes; a package at several nested paths once, with every
  path). Build and test tools are checked but not listed. Packages marked `devOptional` (such as `typescript` and
  the React type packages) count as shipped, conservatively, as in STU-031.
- **Policy:** the one of `scripts/check_licenses.py`. These fail (exit code 1): copyleft outside the psycopg
  exception, an unknown license, no license; license records of a name at a version that no pin of that name has
  (with or without a marker: a record exists, so the package was installed there); a pin without a marker that no
  record covers; and a Tabrion record that is not of the pinned commit (`collect_licenses.py` records the commit of
  PEP 610 `direct_url.json`, and it must equal the SHA of `constraints/tabrion-integration.txt`; a URL with user
  information is never recorded, so such a record fails too). Every record of a name, from every `--licenses` file,
  is judged: one matching record never hides a mismatching or disallowed one, and the order of the files does not
  change the result. A pin whose marker
  applies only elsewhere is "not verified"; `--strict` fails on it too, so the release run needs the records of
  every system. Unreadable or malformed input, and an output folder that cannot be written, exit with code 2.
- **Reproducible:** the same inputs give the same bytes; the serial number is derived from the content, and a
  timestamp is written only with `SOURCE_DATE_EPOCH`.
- **Tested** offline by `tests/supply_chain/test_release_bom.py` (every pin and shipped package listed, copyleft,
  unknown, missing and stale records fail, a Tabrion record of another commit fails, every record counts in either
  file order, `--strict`, npm duplicates,
  multi-hash integrity and workspace names, reproducibility, exit codes).

Run on 2026-11-13 for `master` `d72a14f`, with the license records of fresh runtime environments on Ubuntu 24.04
(Python 3.13.18 and 3.11.18; wheel, lock and Tabrion pin, `pip check` clean):

| Run | Result |
|---|---|
| without `--strict` | exit 0, "release license check: ok"; 103 components (38 Python, 65 frontend); Python: MIT, BSD-3-Clause, Apache-2.0, MIT-0, PSF-2.0, `Apache-2.0 OR BSD-3-Clause` and psycopg/psycopg-binary as the LGPL-3.0 exception; frontend: MIT, ISC, BSD-3-Clause, Apache-2.0, CC0-1.0, Python-2.0 |
| `--strict` | exit 1: `pywin32-ctypes` and `tzdata` (Windows only) have no record yet; the Python 3.11 pins (`backports-tarfile`, `importlib-metadata`, `lazy-importer`, `zipp`) are covered by the 3.11 record |
| twice, same inputs | identical bytes |

Open: the macOS and Windows records (a CI step that collects them is a `.github/` change and needs its workflow
review; until then by hand on those systems), and attaching the files to a release (G-5, after G-R).

Note (TD-061, review R-1 of the re-check on `d833d6f`): a license record of a package that the lock does not pin is
ignored by `release_bom.py`; it gives no failure and does not appear in the report, even with `--strict`. The CI
job's `check_licenses.py` run still judges every installed package, so the policy as a whole holds; only the
release report does not list such a package. Until TD-061 fixes it, read the records of each release run for names
outside the lock.

## Clean install, 2026-11-13 (Linux)

On Ubuntu 24.04 with Python 3.13.18, Node.js 22.23.4, uv 0.11.32 and pipx 1.18.16, from `master` `d72a14f`:

| Step | Command | Result |
|---|---|---|
| 1 | `python scripts/build_frontend.py`, then `python -m build --wheel` with `build==1.6.1` and the hashed `build-constraints.txt` | `tabrion_studio-0.1.0.dev0-py3-none-any.whl`, SHA-256 `353353285f5f40bcf4ea41122b4505baa516cb52f999889c8fe2f2c8a9580606` |
| 2 | `python scripts/smoke_wheel.py dist/tabrion_studio-0.1.0.dev0-py3-none-any.whl` | **passed** in 25 s: fresh venv, lock with hashes, wheel and Tabrion pin with `--no-deps`, `pip check` clean; without Node.js on `PATH`: `--version`, ready in 0.71 s, `index.html` and its script served, signed in with the one-time link, a SQLite profile saved, connected read-only, catalog and rows read, second start reused the instance, `--stop` |
| 3 | `uv tool install <wheel> -c constraints/runtime-lock.txt -c constraints/tabrion-integration.txt` (fresh tool and bin folders) | installed every package at the version of the lock, Tabrion 0.1.0 from the pinned commit; `tabrion-studio --version` prints `0.1.0.dev0`; in a fresh home folder with `PATH=/usr/bin:/bin`: ready in 1.84 s, `/api/health` ok, `--stop` exit 0 |
| 3a | the same with a copy of the lock in which both hashes of `click` were changed | **installed anyway**: `uv tool install` does not check the lock's hashes. `uv pip install <wheel>` with the same constraints refuses it ("hash mismatch"). So the uv tool path is not hash-checked |
| 4 | `pipx install <wheel> --pip-args "-c constraints/tabrion-integration.txt -c constraints/runtime-lock.txt"` (fresh `PIPX_HOME`, uv **not** on `PATH`, so pipx uses pip) | **fails**: pipx passes the arguments to the upgrade of its shared pip, and pip, in hash-checking mode because of the lock's hashes, refuses it. (With uv on `PATH`, pipx 1.18 uses uv and the command installs; the first run of this PR did that and was wrongly counted as a pipx pass.) The guide therefore no longer gives the lock to pipx |
| 4a | `pipx install <wheel> --pip-args "-c constraints/tabrion-integration.txt"` (fresh `PIPX_HOME`, uv not on `PATH`) | installed with pip in 19 s, Tabrion 0.1.0 from the pinned commit, the other dependencies resolved from PyPI without hashes (on that day the same versions as the lock); `pip check` clean; started in a fresh home folder (ready in 1.08 s), `/api/health` ok, `--stop` exit 0 |
| 5 | fresh runtime environments (Python 3.13 and 3.11) as in the guide, `collect_licenses.py`, `release_bom.py` | see [SBOM and license report](#sbom-and-license-report) |

Result: **passed on Linux** for the guide's commands (steps 2, 3 and 4a). Step 4 showed that the first version of
the guide's pipx command failed with pip as pipx's installer; the guide is corrected. Neither tool path checks the
lock's hashes (step 3a), so the guide keeps pip with `--require-hashes` as the main path and says so. Not run:
macOS and Windows by hand (CI runs step 2 there), the PowerShell forms, Python 3.12 and 3.14 by hand (CI tests
them).
