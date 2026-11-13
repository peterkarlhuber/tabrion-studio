# Documentation checklist (STU-017)

STU-017 ("MVP docs and tag 0.1.0") asks for user documentation of installation, start, the security model and
troubleshooting (03 section 10), accepted by a documentation checklist and a clean-install test (09, STU-017). This
page is that checklist: every requirement, the page that meets it and how it is checked. The tag `v0.1.0` is a
separate step, decided after G-S1 (STU-016); there is no publication without G-R.

Status words: **Met** (the page says it and the check passed), **Partly** (what is missing is named), **Open**.

## Requirements and pages

| # | Requirement | Source | Page | Check | Status |
|---|---|---|---|---|---|
| D-1 | Installation: Python 3.11 or later; Linux (glibc), macOS 13 or later, Windows 10 or later; current Chrome, Edge, Firefox, Safari; Node.js only to build | 03 §4.6 | [Installation](../guide/installation.md#requirements) | read against `pyproject.toml` (`requires-python`) and `frontend/package.json` (`engines`) | Met as documentation; CI tests only the current Ubuntu, macOS and Windows runners, not the oldest versions named |
| D-2 | Installation from a pinned version before G-R; no PyPI | 03 §4.6, owner decision 2026-11-08 | [Installation](../guide/installation.md#install-from-a-commit) | clean-install test below | Met for a commit SHA. 03 §4.6 names `uv tool install git+…@vX.Y.Z`; that cannot work, because the built frontend is not in Git, so the guide builds locally. Naming the tag instead of a commit waits for the tag (TD-023) |
| D-3 | The Tabrion pin and the pip version needed | constraints file, TD-023 | [Installation](../guide/installation.md#install-from-a-commit), README | `tests/unit/test_user_guide.py::test_every_install_command_carries_the_tabrion_pin`; clean-install test | Met |
| D-4 | Install from a locally built wheel; PowerShell commands for Windows | STU-017 brief | [Installation](../guide/installation.md#or-build-a-wheel-first) | clean-install test below (bash); `test_every_install_command_carries_the_tabrion_pin` checks the PowerShell blocks too | Met on Linux; the PowerShell commands are not yet run by hand |
| D-5 | Update and uninstall, and the files Studio leaves behind | 03 §4.6 | [Installation](../guide/installation.md#update), [Uninstall](../guide/installation.md#uninstall) | read against `paths.py` | Met |
| D-6 | Start: the command, every option, the sign-in link, one Studio per folder, stop, idle timeout, logs | 03 §4.1, 08 §5 "First start" | [Starting and stopping](../guide/starting.md) | `tests/unit/test_user_guide.py::test_every_option_has_a_row_in_the_options_table`; clean-install test | Met |
| D-7 | Security model: loopback only, sign-in per tab, read-only, PROD marked and confirmed (D-02), Studio's own files, untrusted projects (D-01), no secrets in files or logs, nothing leaves the computer | 03 §7, ADR 0002 | [How Studio protects your data](../guide/security.md) | read against the architecture pages it links | Met |
| D-8 | Security model: the residual risks (malware as the user, PostgreSQL functions with side effects, warnings are UX) and the open reviews | 03 §7.3 | [How Studio protects your data](../guide/security.md#what-studio-does-not-protect-against) | read against [Security review preparation](../architecture/security-review-prep.md#residual-risks-doc-03-section-73) | Met |
| D-9 | How to report a vulnerability | 03 §7 | [How Studio protects your data](../guide/security.md), `SECURITY.md` | `tests/security/test_conventions.py` (private route) | Met |
| D-10 | Troubleshooting: installing, the browser, the views' problems, the terminal (also the `--stop` failures, I-5 of `stu-27-review.md`), logs | 03 §10, TD-023 | [Troubleshooting](../guide/troubleshooting.md) | messages read against `cli.py`, `hatch_build.py`, npm's behaviour without `engine-strict` (only the warning `EBADENGINE`) and `frontend/src/i18n/locales/en.json` | Met |
| D-11 | Connections: profiles, the assistant, test and connect, trust dialog, passwords | TD-023 (STU-006..STU-009) | [Connections](../guide/connections.md) | `e2e/backend.spec.ts`, clean-install test | Met |
| D-12 | Explorer with DDL, policies and row counts | TD-023 (STU-010, STU-022) | [Explorer](../guide/explorer.md) | `e2e/explorer*.spec.ts` (STU-050 check of the pages) | Met |
| D-13 | Data viewer | TD-023 (STU-013) | [Data viewer](../guide/data-viewer.md) | `e2e/viewer*.spec.ts`, clean-install test | Met |
| D-14 | SQL editor with export | TD-023 (STU-012, STU-021) | [SQL editor](../guide/sql-editor.md) | `e2e/editor*.spec.ts`, `e2e/export.spec.ts` | Met |
| D-15 | Diagnostics with backup hints and the report | TD-023 (STU-014, STU-025) | [Diagnostics](../guide/diagnostics.md) | `e2e/diagnostics.spec.ts` | Met |
| D-16 | Migration status and code trust | TD-023 (STU-015, STU-024) | [Migration status](../guide/migrations.md) | `e2e/migrations.spec.ts` | Met |
| D-17 | Settings and keyboard | STU-008, STU-052 | [Settings and keyboard](../guide/settings.md) | read against `services/connections.py` (`studio_profile`) and `services/queries.py` (`_max_rows`) | Met; the SQL logging setting is saved but not used yet, and the page says so (TD-013) |
| D-18 | The guide's start page lists every page and says what works | TD-023 | [User guide](../guide/index.md) | `tests/unit/test_user_guide.py::test_every_guide_page_is_in_the_navigation` | Met |
| D-19 | The README's status, install command and usage match `master` | review L-2 of `stu-27-review.md` | `README.md` | `tests/unit/test_user_guide.py::test_every_install_command_carries_the_tabrion_pin` | Met |
| D-20 | The site builds without a warning, every link and anchor resolves | QA | the whole site | `mkdocs build --strict` (CI job `docs`) | Met |
| D-21 | US English throughout | repository rule | the whole site | review | Met |
| D-22 | Clean-install test | 09 STU-017 | this page, [Building and testing](building.md#wheel) | below | Met on Linux (manual and automated); macOS and Windows through the automated test in CI, which uses its own commands, not the guide's. For 1.0, 03 §8 also asks for a clean install with pipx and uv on all three systems; neither is run yet (TD-023) |
| D-23 | Tag `v0.1.0`, no publication | 09 STU-017, G-R | none yet | not done in this pull request | Open: a separate step after G-S1 |

Out of scope here: the features of later packages that are not on `master` (STU-020 history and favorites, STU-023
ER diagram, STU-026 data editor) get their pages with their own pull requests.

## Clean-install test

### Automated

`scripts/smoke_wheel.py` installs the built wheel into a fresh virtual environment with the Tabrion pin, starts
`tabrion-studio` without Node.js on `PATH`, signs in with the printed one-time link, saves a SQLite profile, connects
it read-only, reads its catalog and the rows of a table, and checks the second start and `--stop`. CI runs it in the
`packaging` job on `ubuntu-latest`, `macos-latest` and `windows-latest` for every pull request
([Building and testing](building.md#wheel)).

### Manual run, 2026-11-13 (Linux)

The steps of the [installation guide](../guide/installation.md), in a fresh `HOME` (no earlier Studio folders, no
keyring), on Ubuntu 24.04.5 with Python 3.13.18, Node.js 22.23.4 and npm 10.9.9:

| Step | Command (as in the guide) | Result |
|---|---|---|
| 1 | `git clone https://github.com/peterkarlhuber/tabrion-studio.git`, `git checkout 7093beab5182be2d8e734d4bf20ad7500d272638` (`master`) | done |
| 2 | `python scripts/build_frontend.py` | built in 16 s, 11 files copied into the package |
| 3 | `python -m build --wheel` with `build==1.6.1` and the hashed `build-constraints.txt` | `tabrion_studio-0.1.0.dev0-py3-none-any.whl`, SHA-256 `5ef54b7c238e161a2f5f1069776cbf9128aba786885b1468c6088717a3f6300b` |
| 4 | `python -m venv ~/.venvs/tabrion-studio`, `pip install --upgrade "pip>=26.1"` (pip 26.3), `pip install dist/tabrion_studio-*.whl -c constraints/tabrion-integration.txt` | installed in 23 s with `tabrion 0.1.0` from the pinned commit; `pip check`: no broken requirements |
| 4a | the same install without `-c constraints/tabrion-integration.txt` | fails with `No matching distribution found for tabrion`, as the guide says |
| 4b | `pip install . -c constraints/tabrion-integration.txt` in another fresh venv (the guide's main path) | installed; `--version` prints `tabrion-studio 0.1.0.dev0` |
| 5 | `~/.venvs/tabrion-studio/bin/tabrion-studio --version` | `tabrion-studio 0.1.0.dev0` |
| 6 | `tabrion-studio --no-browser --print-url` in a project folder, with `PATH=/usr/bin:/bin` (no Node.js) | ready in 0.44 s; prints the address, the project, the log path and the one-time link |
| 7 | open the link in Chromium (headless shell 1243) | the start page "Welcome", status bar "Local server 0.1.0.dev0", no console error |
| 8 | **Connections**, **New connection**: a SQLite file with a table `parts` (3 rows), no password, name `clean-install`, `DEV`, **Save** with the test after saving | "Connection works: 0.3 ms · SQLite 3.53.1 · foreign keys on · journal delete · encoding UTF-8"; `profiles.toml` 0600 in a 0700 folder |
| 9 | **Connect** | header "clean-install connected", `DEV`, "Read-only"; window title `[DEV] clean-install – Tabrion Studio` |
| 10 | **Explorer**, **Open**, table `parts`, **Show data** | "Catalog read: 1 object."; "Rows 1–3", Aluminium, Steel, Brass |
| 11 | `tabrion-studio --stop` in the project folder | "Tabrion Studio for … stopped.", exit code 0 |

Result: **passed**. The test found one documentation bug, fixed in the STU-017 docs pull request: the README's
install command had no Tabrion pin and failed. Not run by hand: macOS and Windows (the automated test covers both),
Firefox, Safari and Edge (TD-047), and the pip 24.0 failure the guide describes (a Python 3.13 virtual environment
already brings pip 26.2).
