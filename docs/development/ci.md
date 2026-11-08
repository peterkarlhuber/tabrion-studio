# CI and supply chain (STU-018, STU-031)

## Workflows

| Workflow | Trigger | Content |
|---|---|---|
| `ci.yml` (`CI`) | pull request, push to master, nightly | backend lint, backend tests (Python 3.11 to 3.14 on Linux, 3.13 on macOS and Windows), security tests, frontend lint/typecheck and unit tests, build and install on Linux, macOS and Windows (wheel with the frontend, fresh venv, run without Node), license and supply chain, dependency audit, docs |
| `ci-ok.yml` | `workflow_run` of CI | evaluates the mandatory checks of `.github/ci-manifest.json` on the PR head SHA (`ci_ok.py`, byte-identical to the canonical copy in nodestro-studio) and posts the verdict as the commit status `ci-ok` on that SHA (`ci_ok_status.py`), so that branch protection can require it |
| `nightly.yml` (`Nightly`) | cron 03:30 UTC, manual | the whole CI on master plus a test-ID inventory; a red run opens or updates the issue "Nightly failed" |

There is no release workflow. Publishing (PyPI, GitHub release) needs the owner's approval G-R for every version;
`tests/security/test_workflows.py` fails if a workflow gains a publish step or `id-token: write`.

Rules: every action is pinned by its full commit SHA, the default token is read-only, checkouts do not persist
credentials (all enforced by `tests/security/test_workflows.py`). `ci-ok.yml` runs from the default branch, so a pull
request cannot change the gate that judges it; it starts working once it is on master.

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

`pip-audit` over the development environment and `npm audit --omit=dev --audit-level=high`. Tabrion is installed
from git until its first release and is reported as "not on PyPI, not audited".
