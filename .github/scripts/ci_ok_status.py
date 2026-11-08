#!/usr/bin/env python3
"""Post ci-ok's verdict as the commit status `ci-ok` on the pull request's head SHA (STU-018).

ci_ok.py (byte-identical to the canonical copy in nodestro-studio) evaluates the mandatory checks and writes its
verdicts with --json. A workflow_run job's own check run is attached to the default branch's commit, so it can never
be a required pull request check. This script turns the verdict into a commit status on `workflow_run.head_sha`,
which branch protection can require (same pattern as tabrion's ci-ok, G-T1 condition C-7):

  OK       -> success     every mandatory check ran on the head SHA and passed
  PENDING  -> pending     listed workflows are still running; a later completion re-evaluates
  NOT OK   -> failure     a mandatory check failed, was cancelled, is missing or an impostor
  no report or an evaluation error -> error

Workflow-change gate (review F-03): a pull request that adds, changes, renames or removes a file under
`.github/workflows/` or `.github/scripts/` gets `failure` ("NOT OK: workflow change not reviewed") unless it carries
the label `workflow-change-reviewed`. Only users with write access can set labels; the owner or an independent
review sets it after reading the change. Labels are read fresh from the API, so re-running ci-ok after labeling
re-evaluates. If no pull request is found for the head SHA, or the API cannot be read, the gate fails closed.

It fails closed: several targets on one head SHA (several pull requests) give success only when all are OK, and the
worst state wins. Posting is retried with backoff (review F-11); if it still fails, the script tries to post `error`
and then fails the job loudly. Standard library only; it never checks out or runs pull request code. Usage (in
ci-ok.yml):

  python3 -I default/.github/scripts/ci_ok_status.py --report report.json

Environment: GH_TOKEN (needs `statuses: write` and `pull-requests: read`), GITHUB_REPOSITORY, GITHUB_EVENT_PATH,
GITHUB_SERVER_URL, GITHUB_RUN_ID, GITHUB_API_URL (optional), GITHUB_STEP_SUMMARY (optional).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CONTEXT = "ci-ok"
STATES = {"OK": "success", "PENDING": "pending", "NOT OK": "failure"}
SEVERITY = ("error", "failure", "pending", "success")  # worst first
DESCRIPTION_MAX = 140  # GitHub rejects longer descriptions
REVIEWED_LABEL = "workflow-change-reviewed"
GUARDED_PREFIXES = (".github/workflows/", ".github/scripts/")
POST_ATTEMPTS = 4
BACKOFF_SECONDS = (2, 4, 8)
MAX_FILE_PAGES = 30  # GitHub lists at most 3000 files of a pull request (100 per page)


class ApiError(RuntimeError):
    """A GitHub API request failed."""


def worst(*states: str) -> str:
    return next((s for s in SEVERITY if s in states), "error")


def combined_state(report: list[dict] | None) -> tuple[str, str]:
    """(GitHub status state, description) for the verdicts of one ci_ok.py run."""
    if not report:
        return "error", "ci-ok produced no verdict for this head SHA"
    states = {STATES.get(str(entry.get("verdict", "")), "error") for entry in report}
    # The worst state wins: an error or a failure on any target is never hidden by an OK on another.
    state = next((s for s in ("error", "failure", "pending") if s in states), "success")
    verdicts = ", ".join(f"{entry.get('target', '?')}: {entry.get('verdict', '?')}" for entry in report)
    return state, verdicts[:DESCRIPTION_MAX]


def load_report(path: str) -> list[dict] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list) or not all(isinstance(entry, dict) for entry in data):
        return None
    return data


def load_run(event_path: str | None) -> dict:
    """The workflow_run object of the event, or {}."""
    if not event_path or not os.path.exists(event_path):
        return {}
    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)
    run = event.get("workflow_run") if isinstance(event, dict) else None
    return run if isinstance(run, dict) else {}


def _valid_sha(sha: object) -> bool:
    return isinstance(sha, str) and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def head_sha(event_path: str | None) -> str | None:
    """The PR head SHA of a pull_request workflow_run, else None (nothing to post)."""
    run = load_run(event_path)
    if run.get("event") != "pull_request":
        return None
    sha = run.get("head_sha")
    return sha if _valid_sha(sha) else None


class Http:
    """The few GitHub REST calls this script makes (replaced by a fake in the tests)."""

    def __init__(self, api: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.token = token

    def _open(self, path: str, body: dict | None = None) -> tuple[int, object]:
        request = urllib.request.Request(
            f"{self.api}{path}",
            data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ci-ok-status",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ApiError(f"{request.get_method()} {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ApiError(f"{request.get_method()} {path}: {exc}") from exc

    def get(self, path: str) -> object:
        return self._open(path)[1]

    def post(self, path: str, body: dict) -> int:
        return self._open(path, body)[0]


def guarded(files: list[dict]) -> list[str]:
    """The files of a pull request under .github/workflows/ or .github/scripts/ (also the old name of a rename)."""
    hits = []
    for entry in files:
        for key in ("filename", "previous_filename"):
            name = entry.get(key)
            if isinstance(name, str) and name.startswith(GUARDED_PREFIXES) and name not in hits:
                hits.append(name)
    return hits


def pull_numbers(http: Http, repo: str, sha: str, run: dict) -> list[int]:
    """The open pull requests whose head is ``sha``: from the event, else from the commit's pull request list."""
    numbers = [
        pr["number"]
        for pr in run.get("pull_requests") or []
        if isinstance(pr, dict) and isinstance(pr.get("number"), int) and (pr.get("head") or {}).get("sha") == sha
    ]
    if numbers:
        return sorted(set(numbers))
    # Pull requests from forks are missing from workflow_run.pull_requests.
    listed = http.get(f"/repos/{repo}/commits/{sha}/pulls?per_page=100")
    if not isinstance(listed, list):
        raise ApiError("unexpected answer when listing the pull requests of the head SHA")
    return sorted(
        {
            pr["number"]
            for pr in listed
            if isinstance(pr, dict)
            and isinstance(pr.get("number"), int)
            and pr.get("state") == "open"
            and (pr.get("head") or {}).get("sha") == sha
        }
    )


def pull_files(http: Http, repo: str, number: int) -> list[dict]:
    files: list[dict] = []
    for page in range(1, MAX_FILE_PAGES + 1):
        batch = http.get(f"/repos/{repo}/pulls/{number}/files?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ApiError(f"unexpected answer when listing the files of PR #{number}")
        files += [entry for entry in batch if isinstance(entry, dict)]
        if len(batch) < 100:
            return files
    raise ApiError(f"PR #{number} changes more files than the API lists; it cannot be checked")


def pull_labels(http: Http, repo: str, number: int) -> set[str]:
    pr = http.get(f"/repos/{repo}/pulls/{number}")
    if not isinstance(pr, dict):
        raise ApiError(f"unexpected answer when reading PR #{number}")
    return {label.get("name") for label in pr.get("labels") or [] if isinstance(label, dict)}


def workflow_gate(http: Http, repo: str, sha: str, run: dict) -> tuple[str, str, list[str]]:
    """(state, description, summary lines) of the workflow-change gate. Fails closed."""
    try:
        numbers = pull_numbers(http, repo, sha, run)
        if not numbers:
            return "failure", "NOT OK: no open pull request found for this head SHA", []
        lines, state, description = [], "success", ""
        for number in numbers:
            hits = guarded(pull_files(http, repo, number))
            if not hits:
                continue
            if REVIEWED_LABEL in pull_labels(http, repo, number):
                lines.append(f"PR #{number} changes {len(hits)} workflow file(s); label `{REVIEWED_LABEL}` is set.")
                continue
            state = "failure"
            description = (
                description
                or f"NOT OK: PR #{number} changes .github/workflows or scripts; needs label {REVIEWED_LABEL}"
            )
            lines.append(
                f"PR #{number} changes {', '.join(f'`{h}`' for h in hits[:10])}"
                f"{' and more' if len(hits) > 10 else ''}. A workflow change needs a review: the owner or an"
                f" independent reviewer sets the label `{REVIEWED_LABEL}` and re-runs ci-ok."
            )
        return state, description, lines
    except ApiError as exc:
        return "error", "ci-ok could not check the pull request for workflow changes", [f"API error: {exc}"]


def post_status(http: Http, repo: str, sha: str, body: dict, sleep=time.sleep) -> int:
    """POST the status, retrying with backoff. Raises ApiError after the last attempt."""
    last: Exception | None = None
    for attempt in range(POST_ATTEMPTS):
        if attempt:
            sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        try:
            status = http.post(f"/repos/{repo}/statuses/{sha}", body)
        except ApiError as exc:
            last = exc
            print(f"::warning title=ci-ok status::attempt {attempt + 1} failed: {exc}")
            continue
        if 200 <= status < 300:
            return status
        last = ApiError(f"HTTP {status}")
        print(f"::warning title=ci-ok status::attempt {attempt + 1} answered HTTP {status}")
    raise ApiError(f"posting the status failed {POST_ATTEMPTS} times: {last}")


def _summary(env: dict, lines: list[str]) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if not path or not lines:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n### Workflow-change gate\n\n" + "".join(f"- {line}\n" for line in lines))


def main(argv: list[str] | None = None, env: dict | None = None, http: Http | None = None, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--report", required=True, help="the --json file of ci_ok.py")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    sha = head_sha(env.get("GITHUB_EVENT_PATH"))
    if sha is None:
        print("::notice title=ci-ok status::not a pull_request workflow_run: no status posted")
        return 0
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repo = env.get("GITHUB_REPOSITORY", "")
    if http is None:
        http = Http(env.get("GITHUB_API_URL") or "https://api.github.com", env.get("GH_TOKEN", ""))

    state, description = combined_state(load_report(args.report))
    gate_state, gate_description, lines = workflow_gate(http, repo, sha, load_run(env.get("GITHUB_EVENT_PATH")))
    for line in lines:
        print(f"workflow-change gate: {line}")
    _summary(env, lines)
    if worst(state, gate_state) != state:
        state, description = gate_state, gate_description
    body = {
        "state": state,
        "context": CONTEXT,
        "description": description[:DESCRIPTION_MAX],
        "target_url": f"{server}/{repo}/actions/runs/{env.get('GITHUB_RUN_ID', '')}",
    }
    try:
        status = post_status(http, repo, sha, body, sleep)
    except ApiError as exc:
        print(f"::error title=ci-ok status::could not post {state!r} on {sha[:12]}: {exc}")
        if state != "error":
            # Leave no stale success behind: try once more to mark the SHA as errored.
            fallback = {**body, "state": "error", "description": "ci-ok could not post its verdict; re-run ci-ok"}
            try:
                post_status(http, repo, sha, fallback, sleep)
            except ApiError as again:
                print(f"::error title=ci-ok status::posting 'error' failed too: {again}")
        return 1
    print(f"ci-ok status {state!r} posted on {sha[:12]} (HTTP {status}): {body['description']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
