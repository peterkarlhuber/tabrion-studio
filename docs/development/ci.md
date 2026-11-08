# CI and supply chain (STU-018, STU-031)

## Workflows

| Workflow | Trigger | Content |
|---|---|---|
| `ci.yml` (`CI`) | pull request, push to master, nightly | backend lint, backend tests (Python 3.11 to 3.14 on Linux, 3.13 on macOS and Windows), security tests, frontend lint/typecheck and unit tests, e2e (Playwright in Chromium: axe with color contrast in light/dark and en/de, contrast, dialogs, status, and the built wheel in a real server under the CSP), build and install on Linux, macOS and Windows (wheel with the frontend, fresh venv, run without Node), license and supply chain, dependency audit, docs |
| `ci-ok.yml` | `workflow_run` of CI | evaluates the mandatory checks of `.github/ci-manifest.json` on the PR head SHA (`ci_ok.py`, byte-identical to the canonical copy in nodestro-studio) and posts the verdict as the commit status `ci-ok` on that SHA (`ci_ok_status.py`), so that branch protection can require it |
| `nightly.yml` (`Nightly`) | cron 03:30 UTC, manual | the whole CI on master plus a test-ID inventory; a red run opens or updates the issue "Nightly failed" |

There is no release workflow. Publishing (PyPI, GitHub release) needs the owner's approval G-R for every version;
`tests/security/test_workflows.py` fails if a workflow gains a publish step or `id-token: write`.

Rules: every action is pinned by its full commit SHA, the default token is read-only, checkouts do not persist
credentials (all enforced by `tests/security/test_workflows.py`). `ci-ok.yml` runs from the default branch, so a pull
request cannot change the gate that judges it; it starts working once it is on master.

## Workflow-change gate (review F-03, R-2, R-3)

`ci-ok.yml` judges the jobs that the pull request's own `ci.yml` runs. A pull request that changes `ci.yml` so that
every listed job passes would therefore turn the gate green. So `ci_ok_status.py` reports **NOT OK** (status
`failure`, description "NOT OK: PR #N changes .github/; needs label workflow-change-reviewed") when the pull request
adds, changes, renames or removes **any file under `.github/`**, unless it carries the label
**`workflow-change-reviewed`**, set after its latest push. The job summary lists the files.

- **What is guarded (R-3):** all of `.github/` (workflows, scripts, local actions under `.github/actions/`, the
  manifest, `CODEOWNERS`, `dependabot.yml`, any new file), except a short allowlist of files no workflow runs or
  reads: `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE/` and `.github/pull_request_template.md`. Code
  outside `.github/` that CI executes (tests, `conftest.py`, `scripts/`, `hatch_build.py`, `pyproject.toml`, the
  frontend's npm scripts) is not guarded by the gate; it stays a matter for the normal code review.
- **Who sets the label:** the owner, or an independent review agent that has read the change. Only users with write
  access (and triage) can set labels; fork contributors cannot. The author of the change does not set it on their own
  pull request.
- **The label counts only after the latest push (R-2):** the newest `labeled` event of the label (issue events API)
  must be newer than every `pull_request` workflow run for the head SHA (GitHub creates one for every push, reopen
  and synchronize; a re-run keeps its time), than the `workflow_run` being judged and than any force push of the
  branch. The commit's own dates are never used, because the author can set them. A push after the review therefore
  gives NOT OK ("label ... predates the latest push"): review the new changes, remove the label, set it again and
  re-run ci-ok. A label set in the few seconds between a push and the start of its CI run, or before the pull request
  was reopened, counts as too early as well (fail closed); set it again.
- **After labeling:** adding a label does not start CI, so re-run the latest `ci-ok` run of the pull request
  (Actions, `ci-ok`, "Re-run all jobs"). Labels and label events are read fresh from the API on every run.
- **Fail closed:** no open pull request for the head SHA, more files or events than the API lists, a missing or
  malformed timestamp, or an API error give NOT OK or `error`. The label never hides a failed check: the worst state
  wins.
- **Posting (review F-11):** the status POST is retried with backoff (2, 4, 8 s). If it still fails, the script tries
  to post `error` and fails the job, so a stale `success` never stays unnoticed.
- The repository needs the label once: the owner creates `workflow-change-reviewed` (docs/TODO.md, OA-003).

**Residual risk (status forging, R-1):** a same-repository branch runs with a token that can write commit statuses. A
workflow in **any** branch, not only in the pull request being merged (a second pull request, or a branch pushed with
an `on: push` workflow), could post `ci-ok = success` on this pull request's head SHA after ci-ok has posted its
verdict. Both statuses come from the `github-actions` app, so branch protection cannot tell them apart, and the pull
request being merged need not change any workflow file. Therefore, **before every merge**, the merging party checks
that the latest `ci-ok` status on the head SHA links (`target_url`, "Details") to a run of the **ci-ok** workflow for
that head SHA, and that the run ID at the end of the status description (`[ci-ok run <id>]`) is that run. Before
merging a pull request that changes `.github/`, the owner or reviewer also reads the diff of `.github/`. Fork pull
requests get a read-only token and cannot post statuses.

**CODEOWNERS (recommendation):** `.github/CODEOWNERS` names the owner for `/.github/`. Requiring code-owner review
in the ruleset would close the forging gap, but it would block every agent pull request in this solo-owner
repository, so it is not required. The owner may turn it on when a second maintainer exists.

## License policy

`scripts/collect_licenses.py` lists the licenses of a fresh environment that has only the wheel and its runtime
dependencies; `scripts/check_licenses.py` judges them and the npm lockfile:

- shipped code (Python runtime closure, npm packages without `dev`): MIT, MIT-0, ISC, 0BSD, BSD-2-Clause,
  BSD-3-Clause, Apache-2.0, PSF-2.0, Python-2.0, Unlicense, CC0-1.0;
- named exception: psycopg and psycopg-binary (LGPL-3.0-only, dynamically loaded and replaceable; license notice
  required for G-R);
- dev-only npm tools may also use MPL-2.0, BlueOak-1.0.0 and CC-BY-4.0;
- anything else, or no license at all, fails the job.

The job also runs the negative probes in `tests/supply_chain/probes` (a GPL-3.0 package in each ecosystem) and
requires them to fail.

## SBOM

The same job writes CycloneDX SBOMs and uploads them as the artifact `sbom`:
`tabrion-studio-python.cdx.json` (`cyclonedx-py environment` over the runtime environment) and
`tabrion-studio-frontend.cdx.json` (`npm sbom`; shipped packages have scope `required`, build tools `optional`).

## Dependency audit

`pip-audit` over the development environment and `npm audit --omit=dev --audit-level=high`.
