"""lane_merge.py: checks everything, then squash-merges one pull request, and refuses on anything doubtful.

Every pull request, log and diff here is invented; a fake gh program stands in for GitHub and records each call it gets."""

import datetime
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
from tmpdirs import fresh_dir  # noqa: E402
import lane_merge  # noqa: E402

TOOL = str(SCRIPTS / "lane_merge.py")
REPO = "example-org/reviewed-app"
HEAD = "0123456789abcdef0123456789abcdef01234567"
MERGE_OID = "eaa22110ffffffffffffffffffffffffffffffff"

FAKE_GH = '''#!%(py)s
import json, os, sys
sc = json.load(open(os.environ["FAKE_GH_SCENARIO"]))
calls = os.environ["FAKE_GH_CALLS"]
args = sys.argv[1:]
prior = [json.loads(l) for l in open(calls)] if os.path.exists(calls) else []
open(calls, "a").write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    views = sum(1 for c in prior if c[:2] == ["pr", "view"])
    print(json.dumps(sc["pr"] if views == 0 else sc.get("after", sc["pr"])))
elif args[:2] == ["pr", "diff"]:
    if "--name-only" in args:
        if sc.get("names_fail"):
            sys.stderr.write("no diff\\n"); sys.exit(1)
        print(sc["names"])
    else:
        if sc.get("diff_fail"):
            sys.stderr.write("no diff\\n"); sys.exit(1)
        print(sc["diff"])
elif args[:1] == ["api"]:
    job = args[1].split("/")[-2]
    text = sc["logs"].get(job)
    if text is None:
        sys.stderr.write("HTTP 410\\n"); sys.exit(1)
    sys.stdout.write(text)
elif args[:2] == ["pr", "merge"]:
    if sc.get("merge_fail"):
        sys.stderr.write(sc["merge_fail"] + "\\n"); sys.exit(1)
else:
    sys.stderr.write("unexpected gh call\\n"); sys.exit(9)
'''

PYTEST_OK = "2026-11-11T10:00:01.1234567Z \x1b[32m============ 521 passed in 3.2s ============\x1b[0m\n"
DIFF_OK = "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"


def job(name, n, conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": conclusion,
            "detailsUrl": "https://github.com/%s/actions/runs/9/job/%d" % (REPO, n)}


def pr_json(rollup=None, **kw):
    p = {"number": 51, "title": "Invented change", "state": "OPEN", "mergeable": "MERGEABLE", "isDraft": False,
         "headRefOid": HEAD, "baseRefName": "main", "mergeCommit": None,
         "url": "https://github.com/%s/pull/51" % REPO,
         "statusCheckRollup": rollup if rollup is not None else [job("pytest", 101), job("shell", 102)]}
    p.update(kw)
    return p


def scenario(**kw):
    s = {"pr": pr_json(), "after": pr_json(state="MERGED", mergeCommit={"oid": MERGE_OID}),
         "logs": {"101": PYTEST_OK, "102": PYTEST_OK}, "names": "src/app.py", "diff": DIFF_OK}
    s.update(kw)
    return s


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.gh = self.d / "gh"
        self.gh.write_text(FAKE_GH % {"py": sys.executable})
        self.gh.chmod(0o755)
        self.calls = self.d / "calls.jsonl"
        self.state = self.d / "state"
        self.listfile = self.d / "own-review.txt"
        self.listfile.write_text("# repos that keep their own review\nexample-org/other-*\n\n")

    def go(self, sc=None, *extra, env=None, repo=REPO, listed=True):
        sc = sc if sc is not None else scenario()
        if self.calls.exists():
            self.calls.unlink()
        (self.d / "scenario.json").write_text(json.dumps(sc))
        e = dict(os.environ, GH=str(self.gh), FAKE_GH_SCENARIO=str(self.d / "scenario.json"), FAKE_GH_CALLS=str(self.calls),
                 MERGE_GATE_NOW="8:05 PM", LANE_MERGE_STATE_DIR=str(self.state),
                 LANE_MERGE_OWN_REVIEW=str(self.listfile if listed else self.d / "missing.txt"))
        e.update(env or {})
        return subprocess.run([sys.executable, TOOL, "51", "-R", repo, *extra], capture_output=True, text=True, env=e)

    def calls_made(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def merges(self):
        return [c for c in self.calls_made() if c[:2] == ["pr", "merge"]]

    def rollup_file(self):
        return self.state / ("merge-rollup-%s.md" % datetime.date.today().isoformat())

    def refused(self, r, *words):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("REFUSED: "), r.stdout)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1, r.stdout)
        for w in words:
            self.assertIn(w, r.stdout)
        self.assertEqual(self.merges(), [], "gh pr merge must never be called on a refusal")
        self.assertFalse(self.rollup_file().exists())


class CleanMerge(Base):
    def test_a_clean_merge_pins_the_head_and_writes_the_rollup_line(self):
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.merges(), [["pr", "merge", "51", "-R", REPO, "--squash", "--delete-branch",
                                          "--match-head-commit", HEAD]])
        want = ("- 8:05 PM example-org/reviewed-app PR 51 (Invented change) squash-merged eaa22110 on green "
                "(2 checks: pytest, shell; 0 skipped, read from 2 job logs; lane_merge.py).")
        self.assertEqual(self.rollup_file().read_text(), want + "\n")
        self.assertEqual(r.stdout.splitlines(), [want, "not readable: none", "MERGED"])

    def test_the_rollup_file_is_appended_not_replaced(self):
        self.state.mkdir()
        self.rollup_file().write_text("- earlier line\n")
        self.go()
        lines = self.rollup_file().read_text().splitlines()
        self.assertEqual(lines[0], "- earlier line")
        self.assertEqual(len(lines), 2)

    def test_check_mode_merges_nothing_and_writes_nothing(self):
        r = self.go(None, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("WOULD MERGE example-org/reviewed-app PR 51"), r.stdout)
        self.assertIn("0 skipped, read from 2 job logs", r.stdout)
        self.assertEqual(self.merges(), [])
        self.assertFalse(self.state.exists())

    def test_check_mode_still_refuses(self):
        r = self.go(scenario(pr=pr_json(isDraft=True)), "--check")
        self.refused(r, "draft")

    def test_checks_that_are_not_actions_jobs_are_listed_as_not_readable(self):
        status = {"__typename": "StatusContext", "context": "ci/old", "state": "SUCCESS"}
        r = self.go(scenario(pr=pr_json([job("pytest", 101), status])), "--check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("not readable: ci/old", r.stdout)

    def test_only_unreadable_checks_means_no_proof_and_a_refusal(self):
        status = {"__typename": "StatusContext", "context": "ci/old", "state": "SUCCESS"}
        r = self.go(scenario(pr=pr_json([status])))
        self.refused(r, "could not find a test summary")


class OwnReviewList(Base):
    def test_a_missing_list_refuses_fail_closed(self):
        r = self.go(listed=False)
        self.refused(r, "own-review list not found at", "missing.txt", "the hub merges this one")

    def test_a_repo_on_the_list_is_refused_in_any_letter_case(self):
        r = self.go(None, repo="Example-Org/Other-App")
        self.refused(r, "keeps its own review process")

    def test_comments_and_blank_lines_in_the_list_are_ignored(self):
        self.listfile.write_text("# example-org/*\n\n   \n")
        self.assertEqual(self.go(None, "--check").returncode, 0)

    def test_the_list_is_checked_before_gh_is_asked_anything(self):
        self.go(None, repo="example-org/other-app")
        self.assertEqual(self.calls_made(), [])


class PullRequestState(Base):
    def test_a_draft_is_refused(self):
        self.refused(self.go(scenario(pr=pr_json(isDraft=True))), "draft")

    def test_a_conflict_is_refused(self):
        self.refused(self.go(scenario(pr=pr_json(mergeable="CONFLICTING"))), "conflicts")

    def test_a_closed_pull_request_is_refused(self):
        self.refused(self.go(scenario(pr=pr_json(state="CLOSED"))), "CLOSED")

    def test_a_pending_check_is_refused(self):
        pending = {"__typename": "CheckRun", "name": "shell", "status": "IN_PROGRESS", "conclusion": "",
                   "detailsUrl": "https://github.com/x/y/actions/runs/9/job/102"}
        self.refused(self.go(scenario(pr=pr_json([job("pytest", 101), pending]))), "not green", "PENDING")

    def test_a_failed_check_is_refused(self):
        self.refused(self.go(scenario(pr=pr_json([job("pytest", 101), job("shell", 102, "FAILURE")]))), "RED")

    def test_a_skipped_job_is_refused(self):
        self.refused(self.go(scenario(pr=pr_json([job("pytest", 101), job("shell", 102, "SKIPPED")]))), "SKIPS")

    def test_no_checks_is_refused(self):
        self.refused(self.go(scenario(pr=pr_json([]))), "NO CHECKS")


class LogSkips(Base):
    def with_log(self, text):
        return self.go(scenario(logs={"101": text, "102": PYTEST_OK}))

    def test_pytest_skips_are_refused(self):
        r = self.with_log("=== 3360 passed, 2 skipped in 12.3s ===\n")
        self.refused(r, "pytest", "2 skipped")

    def test_pytest_deselected_is_refused(self):
        self.refused(self.with_log("=== 12 passed, 1 deselected ===\n"), "deselected")

    def test_pytest_xfailed_is_refused(self):
        self.refused(self.with_log("=== 12 passed, 1 xfailed in 1.0s ===\n"), "xfailed")

    def test_vitest_skips_are_refused(self):
        self.refused(self.with_log(" Tests  10 passed | 2 skipped (12)\n"), "skipped")

    def test_vitest_test_files_skips_are_refused(self):
        self.refused(self.with_log(" Test Files  3 passed | 1 skipped (4)\n"), "skipped")

    def test_jest_skips_are_refused(self):
        self.refused(self.with_log("Tests:  2 skipped, 10 passed, 12 total\n"), "skipped")

    def test_jest_todo_is_refused(self):
        self.refused(self.with_log("Tests:  1 todo, 10 passed, 11 total\n"), "todo")

    def test_playwright_skips_and_flaky_are_refused(self):
        self.refused(self.with_log("  10 passed (30s)\n  3 skipped\n"), "3 skipped")
        self.calls.unlink()
        self.refused(self.with_log("  10 passed (30s)\n  1 flaky\n"), "flaky")

    def test_node_test_skips_are_refused(self):
        self.refused(self.with_log("# tests 5\n# pass 3\n# skipped 2\n"), "skipped 2")
        self.calls.unlink()
        self.refused(self.with_log("ℹ tests 5\nℹ todo 1\n"), "todo 1")

    def test_phpunit_skipped_is_refused(self):
        self.refused(self.with_log("Tests: 5, Assertions: 9, Skipped: 3.\n"), "Skipped: 3")

    def test_phpunit_ok_but_incomplete_is_refused(self):
        self.refused(self.with_log("OK, but incomplete, skipped, or risky tests!\n"), "incomplete")

    def test_tap_skip_is_refused(self):
        self.refused(self.with_log("1..4\nok 1 - a\nok 4 # SKIP no network\n"), "SKIP")

    def test_go_skip_is_refused(self):
        self.refused(self.with_log("--- SKIP: TestThing (0.00s)\nok  \texample/pkg\t0.01s\n"), "SKIP")

    def test_zero_counts_count_as_a_suite_that_ran(self):
        for text in ("=== 521 passed in 3s ===\n", "=== 5 passed, 0 skipped, 0 deselected in 1s ===\n",
                     "# tests 5\n# pass 5\n# skipped 0\n# todo 0\n", " Tests  10 passed (10)\n"):
            self.assertEqual(self.go(scenario(logs={"101": text, "102": text}), "--check").returncode, 0, text)

    def test_a_line_with_timestamp_and_colour_codes_is_still_read(self):
        text = "2026-11-11T10:00:01.1234567Z \x1b[1m\x1b[33m=== 5 passed, 2 skipped in 1.0s ===\x1b[0m\n"
        self.refused(self.with_log(text), "2 skipped")

    def test_no_summary_in_any_log_is_refused(self):
        r = self.go(scenario(logs={"101": "building\nall done\n", "102": "more noise\n"}))
        self.refused(r, "could not find a test summary", "zero skips is not proven")

    def test_one_log_with_a_summary_is_enough_for_the_other_to_be_quiet(self):
        r = self.go(scenario(logs={"101": PYTEST_OK, "102": "lint ok\n"}), "--check")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_log_that_cannot_be_fetched_is_refused(self):
        r = self.go(scenario(logs={"101": PYTEST_OK}))
        self.refused(r, "could not fetch the CI log")

    def test_logs_are_fetched_with_the_api_path_for_the_job(self):
        self.go(None, "--check")
        apis = [c for c in self.calls_made() if c[0] == "api"]
        self.assertEqual(apis, [["api", "repos/%s/actions/jobs/101/logs" % REPO], ["api", "repos/%s/actions/jobs/102/logs" % REPO]])


class Diff(Base):
    def named(self, path, diff=DIFF_OK):
        return self.go(scenario(names=path, diff=diff))

    def test_a_migration_path_is_refused(self):
        self.refused(self.named("db/migrations/0001_init.py"), "database migration or a deploy step", "db/migrations/0001_init.py")

    def test_other_database_paths_are_refused(self):
        for path in ("supabase/schema/x.sql", "prisma/schema.prisma", "alembic/env.py", "db/migrate/1.rb",
                     "web/modules/x/x.install", "web/modules/x/x.post_update.php", "web/modules/x/x.deploy.php"):
            self.setUp()
            self.refused(self.named(path), "the hub decides this one")

    def test_a_deploy_workflow_path_is_refused(self):
        self.refused(self.named(".github/workflows/deploy-prod.yml"), "deploy, release or publish workflow")

    def test_a_plain_ci_workflow_is_allowed(self):
        self.assertEqual(self.go(scenario(names=".github/workflows/ci.yml"), "--check").returncode, 0)

    def test_deploy_files_and_folders_are_refused(self):
        for path in ("deploy.sh", "Procfile", "fly.toml", "vercel.json", "netlify.toml", "eas.json", "ops/deploy/run.sh",
                     "docker-compose.yml", "compose.prod.yml", "Dockerfile", "app/Dockerfile.dev"):
            self.setUp()
            self.refused(self.named(path), "the hub decides this one")

    def test_compose_and_dockerfiles_under_tests_are_allowed(self):
        for path in ("tests/docker-compose.yml", "tests/fixtures/Dockerfile"):
            self.assertEqual(self.go(scenario(names=path), "--check").returncode, 0, path)

    def test_an_added_drupal_update_line_is_refused(self):
        patch = DIFF_OK + "+function mymodule_update_9001() {\n"
        self.refused(self.named("src/app.py", patch), "src/app.py", "_update_")

    def test_other_added_deploy_lines_are_refused(self):
        for added in ("+function mymodule_post_update_fix() {", "+function mymodule_deploy_things() {", "+ * Implements hook_update_N().",
                      "+create table users (id int);", "+DROP TABLE users;", "+DROP COLUMN name"):
            self.setUp()
            self.refused(self.named("src/app.py", DIFF_OK + added + "\n"), "the hub decides this one")

    def test_an_added_alter_table_line_is_refused(self):
        self.refused(self.named("src/app.py", DIFF_OK + "+ALTER TABLE users ADD x int;\n"), "ALTER TABLE")

    def test_a_removed_alter_table_line_is_not_a_refusal(self):
        patch = DIFF_OK + "-ALTER TABLE users ADD x int;\n"
        self.assertEqual(self.go(scenario(diff=patch), "--check").returncode, 0)

    def test_a_diff_that_cannot_be_read_or_is_empty_is_refused(self):
        self.refused(self.go(scenario(names_fail=True)), "could not read the list of changed files")
        self.setUp()
        self.refused(self.go(scenario(diff_fail=True)), "could not read the patch")
        self.setUp()
        self.refused(self.go(scenario(diff="")), "could not read the patch")


class Merge(Base):
    def test_gh_refusing_the_merge_prints_its_message_and_does_not_retry(self):
        r = self.go(scenario(merge_fail="At least 1 approving review is required"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "REFUSED: gh would not merge: At least 1 approving review is required")
        self.assertEqual(len(self.merges()), 1)
        self.assertFalse(self.rollup_file().exists())

    def test_a_merge_that_is_not_confirmed_writes_no_line(self):
        r = self.go(scenario(after=pr_json(state="OPEN")))
        self.assertEqual(r.returncode, 1)
        self.assertIn("MERGE NOT CONFIRMED", r.stdout)
        self.assertFalse(self.rollup_file().exists())

    def test_merged_without_a_merge_commit_is_not_confirmed(self):
        r = self.go(scenario(after=pr_json(state="MERGED", mergeCommit=None)))
        self.assertIn("MERGE NOT CONFIRMED", r.stdout)
        self.assertEqual(r.returncode, 1)


class NoWayAround(Base):
    def test_unknown_flags_exit_non_zero_and_merge_nothing(self):
        for flag in ("--admin", "--force", "--merge", "--rebase", "--skip-checks"):
            r = self.go(None, flag)
            self.assertNotEqual(r.returncode, 0, flag)
            self.assertEqual(self.merges(), [], flag)

    def test_the_repo_flag_is_required(self):
        r = subprocess.run([sys.executable, TOOL, "51"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_no_environment_variable_turns_a_check_off(self):
        env = {"LANE_MERGE_SKIP_CHECKS": "1", "LANE_MERGE_FORCE": "1", "LANE_MERGE_ADMIN": "1", "MERGE_GATE_SKIP": "1", "FORCE": "1"}
        r = self.go(scenario(pr=pr_json([job("pytest", 101), job("shell", 102, "SKIPPED")])), env=env)
        self.refused(r, "SKIPS")

    def test_a_bad_repo_name_is_refused_before_anything_runs(self):
        r = self.go(None, repo="not-a-repo")
        self.refused(r, "OWNER/REPO")
        self.assertEqual(self.calls_made(), [])


class ScanUnits(unittest.TestCase):
    def test_scan_log_counts_a_summary_with_nothing_skipped(self):
        seen, skips = lane_merge.scan_log("=== 521 passed in 3s ===\n")
        self.assertEqual((seen, skips), (1, []))

    def test_path_rule_is_case_insensitive(self):
        self.assertTrue(lane_merge.path_rule("DB/Migrations/X.py"))
        self.assertEqual(lane_merge.path_rule("src/app.py"), "")


if __name__ == "__main__":
    unittest.main()
