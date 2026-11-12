# Repository conventions (TABRION QA-22)

Conventions audit of this repository (planning document 09 QA-22; planning document 05: compare the new repositories
with orkexa and axentra). The joint report for both new repositories is in tabrion,
`docs/program/reports/qa-22-conventions.md` (tabrion PR #42). Audited on 2026-11-11 at master `061db9d`.

| Item | Result | Evidence |
|---|---|---|
| Actions pinned by full commit SHA (version as a comment), images by digest | **Met** | [`tests/security/test_workflows.py`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/tests/security/test_workflows.py), CI job `security tests` |
| `ci-manifest.json` and the `ci-ok` gate | **Met** | [manifest](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/ci-manifest.json), [`ci-ok.yml`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/workflows/ci-ok.yml), [`test_ci_ok.py`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/scripts/test_ci_ok.py); `tests/security/test_conventions.py::test_ci_manifest_names_existing_workflows` |
| Dependabot | **Met** | [`dependabot.yml`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/.github/dependabot.yml): pip, npm (`/frontend`) and github-actions, weekly, cooldown, no auto-merge; `test_dependabot_covers_pip_npm_and_actions_weekly_with_a_cooldown` |
| Branch protection | **Optional by owner decision** | Owner decision of 2026-11-08 (tabrion deviation D-67, tabrion TODO TB-003; this TODO OA-001): optional hardening; the merge rule "`ci-ok` green on the head SHA" is in [CONTRIBUTING.md](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/CONTRIBUTING.md). The agent cannot read repository settings (tabrion deviation D-84) |
| English | **Met** | A scan of every tracked text file finds German only in the UI locale [`de.json`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/frontend/src/i18n/locales/de.json), its tests and the bilingual `<noscript>` line of `frontend/index.html`: product content, not repository text |
| License file | **Met** | [`LICENSE`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/LICENSE) (MIT), `license = "MIT"` and `license-files` in [`pyproject.toml`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/pyproject.toml); `test_license_is_mit_in_the_file_and_the_metadata` |
| `SECURITY.md` | **Met** | [`SECURITY.md`](https://github.com/peterkarlhuber/tabrion-studio/blob/061db9d/SECURITY.md): supported versions, private reporting route; `test_security_policy_names_a_private_reporting_route` |

Compared with orkexa, the more complete of the two older repositories, Studio lacked `CODE_OF_CONDUCT.md` and
`SUPPORT.md`; this change adds both (the Contributor Covenant 2.1 text of tabrion and a Studio support page), and
`test_community_files_are_present` keeps them. Studio already has `CODEOWNERS` for `/.github/` and a
`.gitattributes`; it has no `.editorconfig` (orkexa has one; not part of the checklist).

`tests/security/test_conventions.py` (marker `security`) keeps the file-based items true.
