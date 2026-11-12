"""Tests for .github/scripts/ci_test_coverage.py: CI must fail when a test file is never run."""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

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


def test_the_list_file_needs_a_reason_and_an_issue_link():
    with tempfile.TemporaryDirectory() as tmp:  # not pytest's tmp_path: test_no_tmp_leak fails on what it leaves behind
        os.mkdir(os.path.join(tmp, ".github"))
        with open(os.path.join(tmp, ".github", "ci_not_run.tsv"), "w") as fh:
            fh.write("# comment\n\nx/test_a.sh\tbecause\t%s\nx/test_b.sh\tno issue\tsoon\nx/test_c.sh\tonly two columns\n" % ISSUE)
        entries, problems = cov.read_not_run(tmp)
    assert list(entries) == ["x/test_a.sh", "x/test_b.sh"]
    assert len(problems) == 2
    assert any("issue link" in p for p in problems) and any("three" in p for p in problems)


def test_pytest_ran_flags_a_file_with_no_collected_test():
    files = [S + "/tests/test_a.py", S + "/tests/test_b.py", S + "/test_c.py", S + "/tests/test_skip.py"]
    out = "tests/test_a.py::test_one\ntests/test_a.py::TestX::test_two\ntest_c.py::test_three\n\n3 tests collected in 0.01s\n"
    assert cov.pytest_missing(files, out, "scripts") == ["tests/test_b.py", "tests/test_skip.py"]
    assert cov.pytest_missing(files, out, "scripts", {S + "/tests/test_b.py": ("r", ISSUE)}) == ["tests/test_skip.py"]


def test_the_real_repo_has_no_unrun_test_file():
    r = subprocess.run([sys.executable, "-I", TOOL, "check"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_shell_test_that_does_not_source_the_closed_room_guard_fails_the_check():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, S, "tests"))
        guarded = S + "/tests/test_guarded.sh"
        bare = S + "/tests/test_bare.sh"
        room = S + "/tests/test_closed_room.sh"
        commented = S + "/tests/test_commented.sh"
        for rel, body in (
            (guarded, 'set -euo pipefail\n. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"\n'),
            (bare, "set -euo pipefail\necho hi\n"),
            (commented, '# . "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"\n'),
            (room, "set -euo pipefail\n"),
        ):
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(body)
        files = [guarded, bare, commented, room]
        problems = cov.unguarded(files, {}, root)
        assert len(problems) == 2, problems
        assert bare in problems[0] and commented in problems[1]
        # the one exception passes without the guard, and fails if it has it; a file listed as not run is not asked for the guard
        with open(os.path.join(root, room), "w", encoding="utf-8") as fh:
            fh.write('. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"\n')
        assert any(room in p for p in cov.unguarded([room], {}, root))
        assert cov.unguarded([bare], {bare: ("r", ISSUE)}, root) == []


def test_every_real_shell_test_sources_the_guard_except_the_room_test():
    files = cov.tracked()
    assert cov.unguarded(files, cov.read_not_run()[0]) == []
    assert S + "/tests/test_closed_room.sh" in files


def test_the_workflow_takes_its_lists_from_the_tool():
    wf = open(os.path.join(REPO, ".github", "workflows", "scripts-tests.yml"), encoding="utf-8").read()
    for needle in ("ci_test_coverage.py\" list shell", "ci_test_coverage.py\" list node", "ci_test_coverage.py check",
                   "pytest-ran \"$RUNNER_TEMP/collected.out\" scripts", "pytest-ran \"$RUNNER_TEMP/hooks-collected.out\" hooks"):
        assert needle in wf, needle
    assert "tests/test_*.sh" not in wf


# The shell tests run as N slices side by side. A slice nobody runs is a set of tests that always "passes".

def test_the_parts_together_are_the_whole_list_and_share_no_file():
    items = ["tests/test_%02d.sh" % i for i in range(11)]
    for n in (1, 2, 3, 4, 5, 11, 12):
        parts = [cov.part_of(items, "%d/%d" % (i, n)) for i in range(n)]
        flat = [f for p in parts for f in p]
        assert sorted(flat) == items, n
        assert len(flat) == len(set(flat)), n


def test_a_bad_part_is_refused_rather_than_read_as_an_empty_list():
    items = ["a", "b", "c"]
    for bad in ("3/3", "4/3", "0/0", "-1/3", "1", "1/", "/3", "a/b", "", "1/3/5", " 1/3"):
        assert cov.part_of(items, bad) is None, bad
    for argv in (["list", "shell", "--part", "3/3"], ["list", "shell", "--part", "x"], ["list", "shell", "--part"],
                 ["list", "shell", "--slice", "0/3"]):
        r = subprocess.run([sys.executable, "-I", TOOL] + argv, cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 2, argv
        assert r.stdout == "", argv


def test_the_real_parts_add_up_to_the_real_list():
    def run(*args):
        r = subprocess.run([sys.executable, "-I", TOOL, "list", "shell"] + list(args), cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout.split()
    whole = run()
    assert whole
    assert sorted(f for i in range(3) for f in run("--part", "%d/3" % i)) == whole


def test_the_workflow_runs_every_part_it_cuts():
    """The slice count sits in two places in the workflow: the matrix list and the "/N" handed to the tool. If only one changes, a slice never runs."""
    wf = open(os.path.join(REPO, ".github", "workflows", "scripts-tests.yml"), encoding="utf-8").read()
    matrix = re.findall(r"^\s+part: \[([0-9, ]+)\]\s*$", wf, re.M)
    cut = re.findall(r'list shell --part "\$PART/(\d+)"', wf)
    assert len(matrix) == 1 and len(cut) == 1, (matrix, cut)
    parts = [int(x) for x in matrix[0].split(",")]
    assert sorted(parts) == list(range(int(cut[0]))), (parts, cut)
    assert "PART: ${{ matrix.part }}" in wf
    # No step may list the shell tests whole or cut them another way beside the matrix.
    assert len(re.findall(r"list shell", wf)) == 1
    # The required check is the job named "shell": it must wait for the parts, run even when one failed, and fail unless all passed.
    shell_job = wf.split("\n  shell:\n", 1)[1]
    assert "needs: shell-part" in shell_job
    assert re.search(r"^    if: always\(\)$", shell_job, re.M)
    assert "PARTS_RESULT: ${{ needs.shell-part.result }}" in shell_job
    assert 'if [ "$PARTS_RESULT" != "success" ]; then' in shell_job
    assert "fail-fast: false" in wf
