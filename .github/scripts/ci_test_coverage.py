#!/usr/bin/env python3
"""Fail CI when a test file in this repo is never run.

A test file that no CI step runs is a test that always "passes" (issue: test_muse_poll_weekly.sh sat outside the tests/ glob and
test_usage_calibrate.js had no node step, so neither ran). This script is the one place that says which runner runs which file,
and the workflow takes its shell and node lists from it, so the lists cannot drift apart from the rule.

  ci_test_coverage.py check                 every tracked test file is run by a runner below or is listed, with a reason and an issue link,
                                            in .github/ci_not_run.tsv (a listed file is left out of its runner's list); a listed line for a
                                            file that no longer exists fails too
  ci_test_coverage.py list shell|node       the files the shell / node step must run, one per line, relative to global/.claude/scripts
  ci_test_coverage.py pytest-ran FILE scripts|hooks
                                            FILE is the output of `pytest --collect-only -q` (one "path::test" line per test); every file that
                                            suite must run needs at least one line in it, so a file that collected no tests (or was ignored) fails

A "test file" is any tracked file named test_*.py|sh|js|cjs|mjs, *_test.py|sh|js|cjs|mjs or *.test.* / *.spec.* in js/ts.
"""
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = "global/.claude/scripts"
HOOKS = "global/.claude/hooks"
NOT_RUN = ".github/ci_not_run.tsv"

TEST_NAME = re.compile(r"^(test_.+\.(py|sh|js|cjs|mjs)|.+_test\.(py|sh|js|cjs|mjs)|.+\.(test|spec)\.(js|cjs|mjs|ts))$")

# runner name -> regex over the repo-relative path (what the workflow's step actually reaches)
RUNNERS = {
    # pytest tests test_*.py   (working directory global/.claude/scripts; pytest collects test_*.py and *_test.py)
    "pytest-scripts": re.compile(r"^%s/(tests/.*[^/]*(test_[^/]*|_test)\.py|test_[^/]*\.py)$" % re.escape(SCRIPTS)),
    # pytest tests            (working directory global/.claude/hooks)
    "pytest-hooks": re.compile(r"^%s/tests/.*[^/]*(test_[^/]*|_test)\.py$" % re.escape(HOOKS)),
    # for t in tests/test_*.sh
    "shell": re.compile(r"^%s/tests/test_[^/]*\.sh$" % re.escape(SCRIPTS)),
    # node --test on tests/test_*.js
    "node": re.compile(r"^%s/tests/test_[^/]*\.js$" % re.escape(SCRIPTS)),
}


def tracked(root=ROOT):
    out = subprocess.run(["git", "-C", root, "ls-files"], check=True, capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if p]


def is_test_file(path):
    return bool(TEST_NAME.match(os.path.basename(path)))


def runner_of(path):
    for name, rx in RUNNERS.items():
        if rx.match(path):
            return name
    return None


def read_not_run(root=ROOT):
    """{path: (reason, issue)} from ci_not_run.tsv; lines starting with # and blank lines are comments."""
    entries, problems = {}, []
    p = os.path.join(root, NOT_RUN)
    if not os.path.exists(p):
        return entries, problems
    for n, line in enumerate(open(p, encoding="utf-8"), 1):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 3 or not all(c.strip() for c in cols):
            problems.append("%s:%d needs three tab-separated columns: path, exact reason, issue link" % (NOT_RUN, n))
            continue
        path, reason, issue = (c.strip() for c in cols)
        if not re.match(r"^https://github\.com/[^/]+/[^/]+/issues/\d+$", issue):
            problems.append("%s:%d the third column must be a GitHub issue link, got %r" % (NOT_RUN, n, issue))
        entries[path] = (reason, issue)
    return entries, problems


def check(files, not_run, parse_problems=()):
    """Problems (a list of strings); empty means every test file is run or openly excluded."""
    problems = list(parse_problems)
    fileset = set(files)
    for f in sorted(files):
        if is_test_file(f) and f not in not_run and not runner_of(f):
            problems.append("%s is a test file no CI step runs: move it under %s/tests/ so a runner reaches it, "
                            "or list it in %s with the exact reason and an issue link" % (f, SCRIPTS, NOT_RUN))
    for f in sorted(not_run):
        if f not in fileset:
            problems.append("%s is listed in %s but is not a tracked file: remove the line" % (f, NOT_RUN))
        elif not is_test_file(f):
            problems.append("%s is listed in %s but is not a test file: remove the line" % (f, NOT_RUN))
    return problems


def listing(files, runner, not_run):
    """What a runner's step runs: every file it reaches, except the ones openly listed as not run."""
    rel = SCRIPTS + "/"
    return sorted(f[len(rel):] for f in files if f.startswith(rel) and runner_of(f) == runner and f not in not_run)


def pytest_missing(files, output, suite, not_run=()):
    """Files the pytest suite must run that have no collected test ("path::name" line) in `pytest --collect-only -q` output."""
    runner = "pytest-" + suite
    base = (SCRIPTS if suite == "scripts" else HOOKS) + "/"
    want = sorted(f[len(base):] for f in files if runner_of(f) == runner and f not in not_run)
    seen = set()
    for line in output.splitlines():
        m = re.match(r"^(\S+\.py)::", line)
        if m:
            seen.add(m.group(1))
    return [w for w in want if w not in seen]


def main(argv):
    files = tracked()
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        not_run, parse_problems = read_not_run()
        problems = check(files, not_run, parse_problems)
        for p in problems:
            print("::error::" + p)
        n = sum(1 for f in files if is_test_file(f))
        if problems:
            return 1
        print("every one of the %d test files is run by a CI step or listed in %s (%d listed)" % (n, NOT_RUN, len(not_run)))
        for f, (reason, issue) in sorted(not_run.items()):
            print("not run in CI: %s (%s) %s" % (f, reason, issue))
        return 0
    if cmd == "list" and len(argv) == 3 and argv[2] in ("shell", "node"):
        for f in listing(files, argv[2], read_not_run()[0]):
            print(f)
        return 0
    if cmd == "pytest-ran" and len(argv) == 4 and argv[3] in ("scripts", "hooks"):
        missing = pytest_missing(files, open(argv[2], encoding="utf-8", errors="replace").read(), argv[3], read_not_run()[0])
        for m in missing:
            print("::error::pytest ran no test from %s" % m)
        return 1 if missing else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
