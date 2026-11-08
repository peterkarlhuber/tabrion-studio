"""Tests for ci_ok_status.py (run: python3 -m unittest discover -s .github/scripts -p "test_ci_ok*.py")."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_ok_status as cs

SHA = "a" * 40


def _event(directory: str, event: str = "pull_request", sha: str = SHA) -> str:
    path = os.path.join(directory, "event.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"workflow_run": {"event": event, "head_sha": sha}}, fh)
    return path


def _report(directory: str, verdicts: list[str] | None) -> str:
    path = os.path.join(directory, "report.json")
    if verdicts is not None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([{"target": f"PR #{i}", "verdict": v} for i, v in enumerate(verdicts, 1)], fh)
    return path


class CombinedState(unittest.TestCase):
    def test_states(self) -> None:
        self.assertEqual(cs.combined_state([{"verdict": "OK"}])[0], "success")
        self.assertEqual(cs.combined_state([{"verdict": "PENDING"}])[0], "pending")
        self.assertEqual(cs.combined_state([{"verdict": "NOT OK"}])[0], "failure")
        self.assertEqual(cs.combined_state([{"verdict": "weird"}])[0], "error")
        self.assertEqual(cs.combined_state(None)[0], "error")
        self.assertEqual(cs.combined_state([])[0], "error")

    def test_worst_state_wins(self) -> None:
        self.assertEqual(cs.combined_state([{"verdict": "OK"}, {"verdict": "NOT OK"}])[0], "failure")
        self.assertEqual(cs.combined_state([{"verdict": "OK"}, {"verdict": "PENDING"}])[0], "pending")
        self.assertEqual(cs.combined_state([{"verdict": "NOT OK"}, {"verdict": "x"}])[0], "error")

    def test_description_is_bounded(self) -> None:
        report = [{"target": "PR #1 " + "x" * 300, "verdict": "OK"}]
        self.assertLessEqual(len(cs.combined_state(report)[1]), cs.DESCRIPTION_MAX)


PUSHED = "2026-01-01T00:00:00Z"
LABELED = "2026-01-02T00:00:00Z"


def labeled(at: str = LABELED, name: str = cs.REVIEWED_LABEL, login: str = "owner") -> dict:
    return {"event": "labeled", "label": {"name": name}, "created_at": at, "actor": {"login": login}}


class FakeHttp:
    """A fake GitHub API: GET answers from the fields below, POST records the body and answers from ``post_codes``."""

    def __init__(
        self,
        files=None,
        labels=(),
        commit_pulls=None,
        post_codes=None,
        fail_get=False,
        events=None,
        runs=None,
    ) -> None:
        self.files = files if files is not None else [{"filename": "src/app.py"}]
        self.labels = list(labels)
        self.commit_pulls = commit_pulls
        self.post_codes = list(post_codes or [201])
        self.fail_get = fail_get
        self.events = events if events is not None else [labeled()]
        self.runs = runs if runs is not None else [{"created_at": PUSHED}]
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

    def get(self, path: str) -> object:
        self.gets.append(path)
        if self.fail_get:
            raise cs.ApiError("boom")
        if "/commits/" in path:
            return self.commit_pulls or []
        if "/actions/runs?" in path:
            return {"total_count": len(self.runs), "workflow_runs": self.runs}
        page = int(path.rsplit("page=", 1)[1]) if "page=" in path else 1
        if "/files?" in path:
            return self.files[(page - 1) * 100 : page * 100]
        if "/events?" in path:
            return self.events[(page - 1) * 100 : page * 100]
        return {"number": 7, "labels": [{"name": n} for n in self.labels]}

    def post(self, path: str, body: dict) -> int:
        self.posts.append((path, body))
        code = self.post_codes.pop(0) if self.post_codes else 201
        if code == 0:
            raise cs.ApiError("connection reset")
        return code


def _write_event(directory: str, event: str, pulls: list | None) -> str:
    path = os.path.join(directory, "event.json")
    run: dict = {"event": event, "head_sha": SHA}
    if pulls is not None:
        run["pull_requests"] = pulls
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"workflow_run": run}, fh)
    return path


PR7 = [{"number": 7, "head": {"sha": SHA}}]


class Main(unittest.TestCase):
    def run_main(
        self, event: str | None, verdicts: list[str] | None, http: FakeHttp | None = None, pulls: list | None = PR7
    ) -> tuple[int, FakeHttp, list[float]]:
        http = http or FakeHttp()
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_RUN_ID": "42",
                "GH_TOKEN": "t0ken",
                "GITHUB_EVENT_PATH": _write_event(tmp, event, pulls) if event else "",
                "GITHUB_STEP_SUMMARY": os.path.join(tmp, "summary.md"),
            }
            code = cs.main(["--report", _report(tmp, verdicts)], env=env, http=http, sleep=sleeps.append)
        return code, http, sleeps

    def last_body(self, http: FakeHttp) -> dict:
        return http.posts[-1][1]

    def test_posts_success_on_the_head_sha(self) -> None:
        code, http, _ = self.run_main("pull_request", ["OK"])
        self.assertEqual(code, 0)
        path, body = http.posts[0]
        self.assertEqual(path, f"/repos/owner/repo/statuses/{SHA}")
        self.assertEqual((body["state"], body["context"]), ("success", "ci-ok"))
        self.assertTrue(body["target_url"].endswith("/owner/repo/actions/runs/42"))
        # Review R-1: the description names the ci-ok run too, so the merging party can check it.
        self.assertTrue(body["description"].endswith(" [ci-ok run 42]"))

    def test_run_id_survives_a_long_description(self) -> None:
        text = cs.with_run_id("x" * 300, "123456789012")
        self.assertEqual(len(text), cs.DESCRIPTION_MAX)
        self.assertTrue(text.endswith(" [ci-ok run 123456789012]"))
        self.assertEqual(cs.with_run_id("abc", ""), "abc")

    def test_missing_report_is_an_error_status(self) -> None:
        _, http, _ = self.run_main("pull_request", None)
        self.assertEqual(self.last_body(http)["state"], "error")

    def test_not_a_pull_request_posts_nothing(self) -> None:
        for event in ("push", None):
            code, http, _ = self.run_main(event, ["OK"])
            self.assertEqual((code, http.posts, http.gets), (0, [], []))

    def test_malformed_sha_posts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cs.head_sha(_event(tmp, sha="../../evil")))


class WorkflowGate(unittest.TestCase):
    """Review F-03, R-3: a change anywhere under .github/ (but the templates) needs the label."""

    run_main = Main.run_main
    last_body = Main.last_body

    def test_change_under_github_without_label_is_not_ok(self) -> None:
        for name in (
            ".github/workflows/ci.yml",
            ".github/scripts/ci_ok.py",
            ".github/actions/setup/action.yml",  # R-3: local composite actions that ci.yml would run
            ".github/ci-manifest.json",
            ".github/CODEOWNERS",
            ".github/dependabot.yml",
            ".github/new-file.sh",
        ):
            http = FakeHttp(files=[{"filename": "README.md"}, {"filename": name}])
            code, http, _ = self.run_main("pull_request", ["OK"], http)
            body = self.last_body(http)
            self.assertEqual(code, 0)
            self.assertEqual(body["state"], "failure", name)
            self.assertIn("NOT OK", body["description"])
            self.assertIn(cs.REVIEWED_LABEL, body["description"])
            self.assertLessEqual(len(body["description"]), cs.DESCRIPTION_MAX)

    def test_templates_need_no_label(self) -> None:
        files = [
            {"filename": ".github/ISSUE_TEMPLATE/bug_report.yml"},
            {"filename": ".github/pull_request_template.md"},
            {"filename": ".github/PULL_REQUEST_TEMPLATE/other.md"},
            {"filename": "src/x.py"},
            {"filename": "github/workflows/not-dot-github.yml"},
        ]
        _, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(files=files))
        self.assertEqual(self.last_body(http)["state"], "success")
        self.assertFalse(any(g.endswith("/pulls/7") for g in http.gets))  # labels only read when needed

    def test_is_guarded(self) -> None:
        self.assertTrue(cs.is_guarded(".github/ISSUE_TEMPLATE.md"))  # not the folder
        self.assertTrue(cs.is_guarded(".github/pull_request_template.md.sh"))
        self.assertFalse(cs.is_guarded(".githubx/workflows/a.yml"))
        self.assertFalse(cs.is_guarded("docs/.github/workflows/a.yml"))

    def test_renamed_away_from_a_guarded_folder_counts(self) -> None:
        files = [{"filename": "docs/old.yml", "previous_filename": ".github/workflows/ci-ok.yml"}]
        _, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(files=files))
        self.assertEqual(self.last_body(http)["state"], "failure")

    def test_files_on_a_later_page_are_seen(self) -> None:
        files = [{"filename": f"src/f{i}.py"} for i in range(150)] + [{"filename": ".github/scripts/x.py"}]
        _, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(files=files))
        self.assertEqual(self.last_body(http)["state"], "failure")
        self.assertTrue(any("page=2" in g for g in http.gets))

    def test_label_allows_the_change(self) -> None:
        http = FakeHttp(files=[{"filename": ".github/workflows/ci.yml"}], labels=[cs.REVIEWED_LABEL])
        _, http, _ = self.run_main("pull_request", ["OK"], http)
        self.assertEqual(self.last_body(http)["state"], "success")

    def test_label_never_hides_a_failed_check(self) -> None:
        http = FakeHttp(files=[{"filename": ".github/workflows/ci.yml"}], labels=[cs.REVIEWED_LABEL])
        _, http, _ = self.run_main("pull_request", ["NOT OK"], http)
        self.assertEqual(self.last_body(http)["state"], "failure")

    def test_fork_pull_requests_are_found_via_the_commit(self) -> None:
        pulls = [
            {"number": 9, "state": "open", "head": {"sha": SHA}},
            {"number": 3, "state": "closed", "head": {"sha": SHA}},
        ]
        http = FakeHttp(files=[{"filename": ".github/workflows/ci.yml"}], commit_pulls=pulls)
        _, http, _ = self.run_main("pull_request", ["OK"], http, pulls=[])
        self.assertEqual(self.last_body(http)["state"], "failure")
        self.assertIn("PR #9", self.last_body(http)["description"])

    def test_no_pull_request_fails_closed(self) -> None:
        _, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(commit_pulls=[]), pulls=[])
        self.assertEqual(self.last_body(http)["state"], "failure")

    def test_api_error_fails_closed(self) -> None:
        _, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(fail_get=True))
        self.assertEqual(self.last_body(http)["state"], "error")


class LabelAfterPush(unittest.TestCase):
    """Review R-2: the label counts only if it was set after the latest push to the pull request."""

    run_main = Main.run_main
    last_body = Main.last_body

    def state(self, **kwargs: object) -> tuple[str, str]:
        http = FakeHttp(files=[{"filename": ".github/workflows/ci.yml"}], labels=[cs.REVIEWED_LABEL], **kwargs)
        _, http, _ = self.run_main("pull_request", ["OK"], http)
        body = self.last_body(http)
        return body["state"], body["description"]

    def test_label_after_the_push_counts(self) -> None:
        self.assertEqual(self.state()[0], "success")

    def test_label_before_the_latest_push_is_not_ok(self) -> None:
        # The author pushed another workflow change after the review: a new CI run was created after the label.
        state, description = self.state(runs=[{"created_at": PUSHED}, {"created_at": "2026-01-03T00:00:00Z"}])
        self.assertEqual(state, "failure")
        self.assertIn("predates the latest push", description)

    def test_label_at_the_push_time_is_not_ok(self) -> None:
        self.assertEqual(self.state(runs=[{"created_at": LABELED}])[0], "failure")

    def test_force_push_after_the_label_is_not_ok(self) -> None:
        events = [labeled(), {"event": "head_ref_force_pushed", "created_at": "2026-01-03T00:00:00Z"}]
        self.assertEqual(self.state(events=events)[0], "failure")

    def test_workflow_run_of_the_event_counts_as_a_push(self) -> None:
        http = FakeHttp(files=[{"filename": ".github/workflows/ci.yml"}], labels=[cs.REVIEWED_LABEL])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "event.json")
            run = {"event": "pull_request", "head_sha": SHA, "pull_requests": PR7, "created_at": "2026-02-01T00:00:00Z"}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"workflow_run": run}, fh)
            env = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "1", "GITHUB_EVENT_PATH": path}
            cs.main(["--report", _report(tmp, ["OK"])], env=env, http=http, sleep=lambda s: None)
        self.assertEqual(http.posts[-1][1]["state"], "failure")

    def test_relabeling_after_the_push_counts(self) -> None:
        events = [labeled("2026-01-02T00:00:00Z"), {"event": "unlabeled"}, labeled("2026-01-04T00:00:00Z")]
        self.assertEqual(self.state(events=events, runs=[{"created_at": "2026-01-03T00:00:00Z"}])[0], "success")

    def test_other_labels_do_not_count(self) -> None:
        events = [labeled(name="something-else"), labeled("2025-12-01T00:00:00Z")]
        self.assertEqual(self.state(events=events)[0], "failure")

    def test_label_without_an_event_is_not_ok(self) -> None:
        self.assertEqual(self.state(events=[])[0], "failure")

    def test_label_events_on_a_later_page_are_seen(self) -> None:
        events = [{"event": "commented", "created_at": PUSHED}] * 150 + [labeled()]
        self.assertEqual(self.state(events=events)[0], "success")

    def test_unknown_push_time_fails_closed(self) -> None:
        self.assertEqual(self.state(runs=[])[0], "error")

    def test_malformed_times_fail_closed(self) -> None:
        for bad in ("", "yesterday", "2026-01-02T00:00:00", None, 5):
            self.assertEqual(self.state(events=[labeled(at=bad)])[0], "error", bad)  # type: ignore[arg-type]
            self.assertEqual(self.state(runs=[{"created_at": bad}])[0], "error", bad)

    def test_parse_time(self) -> None:
        self.assertLess(cs.parse_time("2026-01-01T00:00:00Z"), cs.parse_time("2026-01-01T00:00:01+00:00"))
        self.assertEqual(cs.parse_time("2026-01-01T01:00:00+01:00"), cs.parse_time("2026-01-01T00:00:00Z"))


class Posting(unittest.TestCase):
    """Review F-11: retry with backoff, then fail loudly."""

    run_main = Main.run_main

    def test_transient_failures_are_retried(self) -> None:
        code, http, sleeps = self.run_main("pull_request", ["OK"], FakeHttp(post_codes=[0, 502, 201]))
        self.assertEqual(code, 0)
        self.assertEqual(len(http.posts), 3)
        self.assertEqual(sleeps, [2, 4])
        self.assertEqual(http.posts[-1][1]["state"], "success")

    def test_persistent_failure_fails_the_job_and_tries_error(self) -> None:
        codes = [0] * cs.POST_ATTEMPTS + [201]
        code, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(post_codes=codes))
        self.assertEqual(code, 1)
        self.assertEqual(len(http.posts), cs.POST_ATTEMPTS + 1)
        self.assertEqual(http.posts[-1][1]["state"], "error")

    def test_failure_of_the_error_post_also_fails_the_job(self) -> None:
        code, http, _ = self.run_main("pull_request", ["OK"], FakeHttp(post_codes=[500] * (2 * cs.POST_ATTEMPTS)))
        self.assertEqual(code, 1)
        self.assertEqual(len(http.posts), 2 * cs.POST_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
