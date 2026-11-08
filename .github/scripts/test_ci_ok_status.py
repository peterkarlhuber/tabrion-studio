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


class Main(unittest.TestCase):
    def run_main(self, event: str | None, verdicts: list[str] | None) -> tuple[int, list[tuple]]:
        calls: list[tuple] = []

        def poster(repo: str, sha: str, token: str, body: dict, api: str) -> int:
            calls.append((repo, sha, token, body, api))
            return 201

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_RUN_ID": "42",
                "GH_TOKEN": "t0ken",
                "GITHUB_EVENT_PATH": _event(tmp, event) if event else "",
            }
            code = cs.main(["--report", _report(tmp, verdicts)], env=env, poster=poster)
        return code, calls

    def test_posts_success_on_the_head_sha(self) -> None:
        code, calls = self.run_main("pull_request", ["OK"])
        self.assertEqual(code, 0)
        repo, sha, token, body, api = calls[0]
        self.assertEqual((repo, sha, token, api), ("owner/repo", SHA, "t0ken", "https://api.github.com"))
        self.assertEqual((body["state"], body["context"]), ("success", "ci-ok"))
        self.assertTrue(body["target_url"].endswith("/owner/repo/actions/runs/42"))

    def test_missing_report_is_an_error_status(self) -> None:
        _, calls = self.run_main("pull_request", None)
        self.assertEqual(calls[0][3]["state"], "error")

    def test_not_a_pull_request_posts_nothing(self) -> None:
        self.assertEqual(self.run_main("push", ["OK"]), (0, []))
        self.assertEqual(self.run_main(None, ["OK"]), (0, []))

    def test_malformed_sha_posts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cs.head_sha(_event(tmp, sha="../../evil")))


if __name__ == "__main__":
    unittest.main()
