"""merge_gate.py: counts checks and skips the same way every time, and never merges.

The pull requests here are invented JSON; a fake gh program stands in for GitHub."""

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
from tmpdirs import fresh_dir  # noqa: E402
import merge_gate  # noqa: E402

TOOL = str(SCRIPTS / "merge_gate.py")


def run(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def pr(rollup, state="OPEN", mergeable="MERGEABLE", oid=None, number=7, title="Invented change"):
    return {
        "number": number, "title": title, "state": state, "mergeable": mergeable,
        "mergeCommit": {"oid": oid} if oid else None, "statusCheckRollup": rollup,
        "url": "https://github.com/example-org/example-repo/pull/%d" % number,
    }


class Counting(unittest.TestCase):
    def word(self, p):
        return merge_gate.verdict(p)[0]

    def test_all_passing_is_green(self):
        self.assertEqual(self.word(pr([run("pytest"), run("shell")])), "GREEN")

    def test_a_pending_check_is_not_green(self):
        self.assertEqual(self.word(pr([run("pytest"), run("shell", "", "IN_PROGRESS")])), "PENDING")
        self.assertEqual(self.word(pr([run("pytest"), run("shell", "", "QUEUED")])), "PENDING")

    def test_a_skipped_check_blocks_and_is_counted(self):
        p = pr([run("pytest"), run("shell", "SKIPPED")])
        word, line, c, total = merge_gate.verdict(p)
        self.assertEqual(word, "SKIPS")
        self.assertIn("skips 1", line)
        self.assertEqual(c["skip"], ["shell"])

    def test_neutral_counts_as_a_skip(self):
        self.assertEqual(self.word(pr([run("a"), run("b", "NEUTRAL")])), "SKIPS")

    def test_every_failure_kind_is_red_and_red_wins_over_pending_and_skips(self):
        for conc in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"):
            self.assertEqual(self.word(pr([run("a", conc), run("b", "", "IN_PROGRESS"), run("c", "SKIPPED")])), "RED", conc)

    def test_commit_statuses_count_too(self):
        ok = {"__typename": "StatusContext", "context": "ci/old", "state": "SUCCESS"}
        wait = {"__typename": "StatusContext", "context": "ci/old", "state": "PENDING"}
        bad = {"__typename": "StatusContext", "context": "ci/old", "state": "ERROR"}
        self.assertEqual(self.word(pr([run("a"), ok])), "GREEN")
        self.assertEqual(self.word(pr([run("a"), wait])), "PENDING")
        self.assertEqual(self.word(pr([run("a"), bad])), "RED")

    def test_no_checks_is_never_green(self):
        self.assertEqual(self.word(pr([])), "NO CHECKS")
        self.assertEqual(self.word(pr(None)), "NO CHECKS")

    def test_a_conflict_blocks_even_when_checks_pass(self):
        self.assertEqual(self.word(pr([run("a")], mergeable="CONFLICTING")), "CONFLICT")

    def test_merged_and_closed_are_reported_as_such(self):
        self.assertEqual(self.word(pr([run("a")], state="MERGED", oid="0123456789abcdef")), "MERGED")
        self.assertEqual(self.word(pr([run("a")], state="CLOSED")), "CLOSED")

    def test_the_rollup_line_has_time_repo_title_number_sha_checks_skips(self):
        os.environ["MERGE_GATE_NOW"] = "9:05 AM"
        self.addCleanup(os.environ.pop, "MERGE_GATE_NOW", None)
        p = pr([run("pytest"), run("shell")], state="MERGED", oid="0123456789abcdef", number=12, title="Add  the\nthing")
        _, _, c, total = merge_gate.verdict(p)
        self.assertEqual(merge_gate.rollup_line("example-org/example-repo", p, c, total),
                         "9:05 AM | example-org/example-repo | Add the thing (#12) | 01234567 | 2 of 2 checks pass | skips 0")

    def test_an_unmerged_pr_says_so_instead_of_inventing_a_sha(self):
        p = pr([run("a")])
        _, _, c, total = merge_gate.verdict(p)
        self.assertIn("not merged yet", merge_gate.rollup_line("o/r", p, c, total))


class WithFakeGh(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.gh = self.d / "gh"
        self.gh.write_text('#!/bin/sh\n[ -n "$FAKE_GH_FAIL" ] && { echo "boom" >&2; exit 1; }\ncat "$FAKE_GH_JSON"\n'
                           'echo "$@" > "$FAKE_GH_ARGS"\n')
        self.gh.chmod(self.gh.stat().st_mode | stat.S_IXUSR)
        self.json = self.d / "pr.json"
        self.args = self.d / "args.txt"

    def go(self, p, *extra, fail=False):
        self.json.write_text(json.dumps(p))
        env = dict(os.environ, GH=str(self.gh), FAKE_GH_JSON=str(self.json), FAKE_GH_ARGS=str(self.args),
                   MERGE_GATE_NOW="9:05 AM")
        if fail:
            env["FAKE_GH_FAIL"] = "1"
        return subprocess.run([sys.executable, TOOL, "7", *extra], capture_output=True, text=True, env=env)

    def test_green_prints_verdict_rollup_and_the_hub_merge_command_and_exits_0(self):
        r = self.go(pr([run("pytest"), run("shell")]))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "GREEN 2/2 skips 0")
        self.assertTrue(lines[1].startswith("9:05 AM | example-org/example-repo | Invented change (#7) | not merged yet"))
        self.assertEqual(lines[2], "gh pr merge 7 -R example-org/example-repo --squash --delete-branch")

    def test_it_only_reads_the_pr_and_never_runs_a_merge(self):
        self.go(pr([run("pytest")]))
        said = self.args.read_text()
        self.assertTrue(said.startswith("pr view 7"))
        self.assertNotIn("merge", said.split("--json")[0])

    def test_not_green_exits_1_and_prints_no_merge_command(self):
        r = self.go(pr([run("pytest"), run("shell", "", "IN_PROGRESS")]))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("gh pr merge", r.stdout)
        self.assertIn("waiting on: shell", r.stdout)

    def test_the_repo_flag_is_passed_to_gh_and_used_in_the_command(self):
        r = self.go(pr([run("a")]), "-R", "other-org/other-repo")
        self.assertIn("-R other-org/other-repo", self.args.read_text())
        self.assertIn("gh pr merge 7 -R other-org/other-repo --squash --delete-branch", r.stdout)

    def test_a_gh_failure_exits_2(self):
        r = self.go(pr([run("a")]), fail=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("boom", r.stderr)

    def test_an_already_merged_pr_exits_0_with_its_sha_and_no_command(self):
        r = self.go(pr([run("a")], state="MERGED", oid="abcdef0123456789"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("abcdef01", r.stdout)
        self.assertNotIn("gh pr merge", r.stdout)


if __name__ == "__main__":
    unittest.main()
