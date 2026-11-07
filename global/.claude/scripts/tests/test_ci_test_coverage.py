"""Tests for .github/scripts/ci_test_coverage.py: CI must fail when a test file is never run."""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
TOOL = os.path.join(REPO, ".github", "scripts", "ci_test_coverage.py")

spec = importlib.util.spec_from_file_location("ci_test_coverage", TOOL)
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)

S = "global/.claude/scripts"
H = "global/.claude/hooks"
ISSUE = "https://github.com/TortoiseWolfe/Claude_Commandz/issues/324"


def test_runner_of_matches_what_the_workflow_steps_reach():
    assert cov.runner_of(S + "/tests/test_a.py") == "pytest-scripts"
    assert cov.runner_of(S + "/test_email_voice_check.py") == "pytest-scripts"
    assert cov.runner_of(H + "/tests/test_x.py") == "pytest-hooks"
    assert cov.runner_of(S + "/tests/test_a.sh") == "shell"
    assert cov.runner_of(S + "/tests/test_a.js") == "node"
    # the cases that used to be missed: a shell test outside tests/, a nested shell test the glob does not reach, a test elsewhere
    assert cov.runner_of(S + "/test_muse_poll_weekly.sh") is None
    assert cov.runner_of(S + "/tests/sub/test_a.sh") is None
    assert cov.runner_of("mods/afa-mods/src/lib.test.ts") is None


def test_test_file_names():
    for ok in ("test_a.py", "a_test.py", "test_a.sh", "test_a.cjs", "a.test.ts", "a.spec.js"):
        assert cov.is_test_file("x/" + ok), ok
    for no in ("conftest.py", "run_demo_video_smoke.sh", "tests.md", "latest_a.py", "contest.sh"):
        assert not cov.is_test_file("x/" + no), no


def test_a_shell_test_outside_the_glob_fails_the_check():
    # the 6 Nov state: test_muse_poll_weekly.sh sat beside the scripts, not in tests/
    problems = cov.check([S + "/tests/test_ok.sh", S + "/test_muse_poll_weekly.sh"], {})
    assert len(problems) == 1 and "test_muse_poll_weekly.sh" in problems[0] and "no CI step runs" in problems[0]


def test_a_js_test_with_no_node_step_would_have_failed_too():
    assert cov.check(["tools/x.test.js"], {})


def test_a_listed_file_passes_and_is_left_out_of_its_runners_list():
    files = [S + "/tests/test_a.sh", S + "/tests/test_uid.sh"]
    not_run = {S + "/tests/test_uid.sh": ("needs WSL", ISSUE)}
    assert cov.check(files, not_run) == []
    assert cov.listing(files, "shell", not_run) == ["tests/test_a.sh"]
    assert cov.listing(files, "shell", {}) == ["tests/test_a.sh", "tests/test_uid.sh"]


def test_a_listed_line_for_a_missing_or_non_test_file_fails():
    files = [S + "/tests/test_a.sh", S + "/notes.md"]
    assert cov.check(files, {S + "/tests/test_gone.sh": ("r", ISSUE)})
    assert cov.check(files, {S + "/notes.md": ("r", ISSUE)})


def test_the_list_file_needs_a_reason_and_an_issue_link(tmp_path):
    (tmp_path / ".github").mkdir()
    tsv = tmp_path / ".github" / "ci_not_run.tsv"
    tsv.write_text("# comment\n\nx/test_a.sh\tbecause\t%s\nx/test_b.sh\tno issue\tsoon\nx/test_c.sh\tonly two columns\n" % ISSUE)
    entries, problems = cov.read_not_run(str(tmp_path))
    assert list(entries) == ["x/test_a.sh", "x/test_b.sh"]
    assert len(problems) == 2
    assert any("issue link" in p for p in problems) and any("three" in p for p in problems)


def test_pytest_ran_flags_a_file_with_no_progress_line():
    files = [S + "/tests/test_a.py", S + "/tests/test_b.py", S + "/test_c.py", S + "/tests/test_skip.py"]
    out = "tests/test_a.py ....                                [ 40%]\ntest_c.py .s.\n"
    assert cov.pytest_missing(files, out, "scripts") == ["tests/test_b.py", "tests/test_skip.py"]
    assert cov.pytest_missing(files, out, "scripts", {S + "/tests/test_b.py": ("r", ISSUE)}) == ["tests/test_skip.py"]


def test_the_real_repo_has_no_unrun_test_file():
    r = subprocess.run([sys.executable, "-I", TOOL, "check"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_workflow_takes_its_lists_from_the_tool():
    wf = open(os.path.join(REPO, ".github", "workflows", "scripts-tests.yml"), encoding="utf-8").read()
    for needle in ("ci_test_coverage.py\" list shell", "ci_test_coverage.py\" list node", "ci_test_coverage.py check",
                   "pytest-ran \"$RUNNER_TEMP/pytest.out\" scripts", "pytest-ran \"$RUNNER_TEMP/hooks-pytest.out\" hooks"):
        assert needle in wf, needle
    assert "tests/test_*.sh" not in wf
