#!/usr/bin/env python3
"""Lane merge: the one narrow merge a lane may run. Checks everything, squash-merges one pull request, writes the roll-up line. No model.

  lane_merge.py PR -R OWNER/REPO            check everything, then squash-merge, then write the roll-up line
  lane_merge.py PR -R OWNER/REPO --check    check everything and print the verdict; merge nothing, write nothing

-R is required. There is no flag or environment variable that skips a check, widens the merge or picks another merge method.
It refuses (one line starting "REFUSED: ", exit 1), in this order, when:
  1. the repo is on the own-review list, or is not on the list of repos we merge in (a missing list refuses too, and so does
  a line there that does not name its owner in full), 2. the pull request is not open, is a draft or conflicts,
  3. merge_gate.py does not say GREEN, 4. the CI (continuous integration, the automatic checks) logs show any skipped,
  deselected, expected-fail, rerun or todo test, or show no test summary at all, or a log cannot be fetched, or a job whose
  log shows no test summary is not one of the known non-test jobs (gitleaks, email-guard, shell),
  5. the diff touches a database migration or a deploy step, or anything under .github/ (a change to the CI itself can make
  its own checks pass); old names count too, so a renamed or deleted file is seen, 6. the branch moved while it was checked,
  7. gh itself will not merge (branch protection that needs a review ends here).
The merge is pinned to the commit that was checked (--match-head-commit), so what was checked is what merges.
The two lists are private files, one pattern per line (OWNER/REPO or OWNER/*, any letter case, # starts a comment):
merge-repos.txt names the repos we merge in, and everything else is refused; own-review-repos.txt names repos that keep
their own review, and it wins when a repo is on both.
What it cannot see: a known non-test job passes on its green badge alone, and so does a check that is not an Actions job
(no log to read), as long as one other job shows a summary. Each is named after "no test summary in:" or "not readable:".
After the merge it re-reads the pull request, and only then appends one line to <state dir>/merge-rollup-YYYY-MM-DD.md.
Exit codes: 0 = merged (or, with --check, would merge); 1 = refused or not confirmed.
Test hooks only: GH (the gh program), MERGE_GATE_NOW (pins the time), LANE_MERGE_STATE_DIR (default ~/.claude/state),
LANE_MERGE_OWN_REVIEW (default ~/.config/afa/own-review-repos.txt), LANE_MERGE_REPOS (default ~/.config/afa/merge-repos.txt).
"""
import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_gate  # noqa: E402

FIELDS = "number,title,state,mergeable,mergeCommit,statusCheckRollup,url,isDraft,headRefOid,baseRefName"
REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class Refused(Exception):
    pass


def refuse(why) -> NoReturn:
    raise Refused(why)


def gh(args, timeout=120):
    """(exit code, stdout, stderr). A gh that cannot run gives exit code 127."""
    try:
        r = subprocess.run([os.environ.get("GH", "gh")] + args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", "cannot run gh: %s" % e
    return r.returncode, r.stdout, r.stderr


def read_pr(number, repo):
    rc, out, err = gh(["pr", "view", str(number), "--json", FIELDS, "-R", repo], 60)
    if rc != 0:
        return None, (err.strip() or out.strip() or "gh failed")
    try:
        return json.loads(out), ""
    except ValueError:
        return None, "gh gave something that is not JSON"


# ---- 1. the two repo lists ---------------------------------------------------------------------------------------------

def own_review_path():
    return os.path.expanduser(os.environ.get("LANE_MERGE_OWN_REVIEW") or "~/.config/afa/own-review-repos.txt")


def merge_repos_path():
    return os.path.expanduser(os.environ.get("LANE_MERGE_REPOS") or "~/.config/afa/merge-repos.txt")


def read_patterns(path, what):
    """The patterns in one list file: one per line, blank lines and # comments left out. A file that cannot be read refuses."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        refuse("%s not found at %s; the hub merges this one" % (what, path))
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def check_repo(repo):
    """Refuse unless the repo is named on the list of repos we merge in, and is not on the own-review list. Both lists must exist."""
    low = repo.lower()
    for pat in read_patterns(own_review_path(), "own-review list"):
        if fnmatch.fnmatchcase(low, pat.lower()):
            refuse("this repo keeps its own review process (it matches %r on the own-review list); the hub merges this one" % pat)
    path = merge_repos_path()
    allowed = False
    for pat in read_patterns(path, "list of repos we merge in"):
        owner = pat.split("/")[0]
        # The owner is always written out. A line such as */* would let every repo through, which is what this list is here to stop.
        if pat.count("/") != 1 or not owner or any(ch in owner for ch in "*?["):
            refuse("the list of repos we merge in at %s holds %r, which does not name its owner in full (OWNER/REPO or OWNER/*); "
                   "the hub merges this one" % (path, pat))
        if fnmatch.fnmatchcase(low, pat.lower()):
            allowed = True
    if not allowed:
        refuse("this repo is not on the list of repos we merge in (%s); the hub merges this one" % path)


# ---- 2. pull request state ---------------------------------------------------------------------------------------------

def check_state(pr):
    state = (pr.get("state") or "").upper()
    if state != "OPEN":
        refuse("the pull request is %s, not OPEN; nothing to merge" % (state or "in an unknown state"))
    if pr.get("isDraft"):
        refuse("the pull request is a draft; mark it ready for review first, or ask the hub")
    if (pr.get("mergeable") or "").upper() == "CONFLICTING":
        refuse("the branch conflicts with its base; rebase it, then try again")
    if not pr.get("headRefOid"):
        refuse("gh did not report the head commit, so the merge cannot be pinned; the hub checks this one")


# ---- 4. skips read from the CI logs ------------------------------------------------------------------------------------

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
STAMP = re.compile(r"^\s*\d{4}-\d\d-\d\dT[\d:.]+Z\s?")
COUNT_WORD = re.compile(r"(\d+)\s+([a-z]+)", re.I)
JOB_URL = re.compile(r"/job/(\d+)\s*$")

# A pytest count is a number and one or more words: "3501 passed", and also "227 subtests passed".
PYTEST_COUNT = r"\d+ [a-z]+(?: [a-z]+)*"
PYTEST_BARE = re.compile(r"^[=\s]*(%s(?:, %s)*)\s+in\s+[\d.:]+s" % (PYTEST_COUNT, PYTEST_COUNT), re.I)
PYTEST_BAR = re.compile(r"^=+\s*(%s(?:, %s)*)\s*=*\s*$" % (PYTEST_COUNT, PYTEST_COUNT), re.I)
PYTEST_RESULT = {"passed", "failed", "skipped", "deselected", "xfailed", "xpassed", "error", "errors", "rerun"}
PYTEST_UNCLEAN = {"skipped", "deselected", "xfailed", "xpassed", "rerun"}
JEST = re.compile(r"^\s*Tests?:\s+.*\btotal\b", re.I)
VITEST = re.compile(r"^\s*Tests?(?:\s+Files)?\s{2,}.*\(\d+\)\s*$|^\s*Tests?(?:\s+Files)?\s+\d+ \w+.*\(\d+\)\s*$")
PLAYWRIGHT_UNCLEAN = re.compile(r"^\s*(\d+) (skipped|flaky|did not run|interrupted)\s*$")
PLAYWRIGHT_PASS = re.compile(r"^\s*\d+ passed \(.*\)\s*$")
NODE = re.compile(r"^\s*(?:#|ℹ)\s*(tests|pass|fail|cancelled|skipped|todo)\s+(\d+)\s*$")
PHP_COUNT = re.compile(r"\b(Skipped|Incomplete|Risky): (\d+)")
PHP_SUMMARY = re.compile(r"^\s*(OK \(\d+ tests?|Tests: \d+, Assertions: \d+|OK, but incomplete, skipped, or risky tests!)")
PHP_BUT = re.compile(r"OK, but incomplete, skipped, or risky tests!")
TAP_SKIP = re.compile(r"^\s*ok \d+\b.*#\s*skip", re.I)
TAP_PLAN = re.compile(r"^\s*1\.\.\d+\s*$")
GO_SKIP = re.compile(r"^\s*--- SKIP:")
GO_SUMMARY = re.compile(r"^\s*(ok|FAIL)\s+\S+\s+[\d.]+s|^\s*--- PASS:")

# Jobs that are known not to print a test summary (a secret scan, a mail check, a shell lint). Any other job whose log shows
# no summary refuses, because a test job that ran nothing looks the same as a lint job. Compared in lower case, whole name.
QUIET_JOBS = frozenset({"gitleaks", "email-guard", "shell"})


def clean_line(raw):
    return STAMP.sub("", ANSI.sub("", raw)).rstrip("\r")


def scan_log(text):
    """(summaries seen, [(skip count, line)] for every line with a skip-like count above zero)."""
    summaries = 0
    skips = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line.strip():
            continue
        found = 0
        saw = False
        m = PYTEST_BARE.match(line) or PYTEST_BAR.match(line)
        if m:
            counts = [(int(part.split()[0]), set(part.lower().split()[1:])) for part in m.group(1).split(", ")]
            if any(words & PYTEST_RESULT for _, words in counts):
                saw = True
                found += sum(n for n, words in counts if words & PYTEST_UNCLEAN)
        if JEST.match(line) or VITEST.match(line):
            saw = True
            found += sum(int(n) for n, w in COUNT_WORD.findall(line) if w.lower() in ("skipped", "todo"))
        m = PLAYWRIGHT_UNCLEAN.match(line)
        if m:
            saw = True
            found += int(m.group(1))
        if PLAYWRIGHT_PASS.match(line):
            saw = True
        m = NODE.match(line)
        if m:
            saw = True
            if m.group(1) in ("skipped", "todo", "cancelled"):
                found += int(m.group(2))
        if PHP_SUMMARY.match(line) or PHP_COUNT.search(line):
            saw = True
            found += sum(int(n) for _, n in PHP_COUNT.findall(line))
            if PHP_BUT.search(line):
                found += 1
        if TAP_SKIP.match(line):
            saw = True
            found += 1
        if TAP_PLAN.match(line):
            saw = True
        if GO_SKIP.match(line):
            saw = True
            found += 1
        if GO_SUMMARY.match(line):
            saw = True
        if saw:
            summaries += 1
        if found > 0:
            skips.append((found, line.strip()))
    return summaries, skips


def job_log(repo, job_id):
    """One Actions job's log. A job log carries colour codes, and gh refuses to print those unless it is told it may."""
    path = "repos/%s/actions/jobs/%s/logs" % (repo, job_id)
    rc, out, err = gh(["api", "--allow-escape-sequences", path], 180)
    if rc != 0 and "unknown flag" in err:
        rc, out, err = gh(["api", path], 180)  # an older gh has no such flag and prints the log as it is
    return rc, out, err


def check_logs(repo, pr):
    """Read every Actions job log. Returns (job log count, [checks that cannot be read], [jobs whose log shows no test summary])."""
    jobs = 0
    unreadable = []
    quiet = []
    ran = False
    for item in pr.get("statusCheckRollup") or []:
        name = merge_gate.name_of(item)
        m = JOB_URL.search(item.get("detailsUrl") or "") if item.get("__typename") != "StatusContext" else None
        if not m:
            unreadable.append(name)
            continue
        rc, out, err = job_log(repo, m.group(1))
        if rc != 0:
            refuse("could not fetch the CI log for job %r (%s); zero skips is not proven; the hub checks this one"
                   % (name, (err.strip() or "gh failed")[:200]))
        jobs += 1
        seen, skips = scan_log(out)
        if skips:
            n, line = skips[0]
            refuse("job %r shows %d skipped or unclean test(s): %r; fix the skip, or the hub decides this one" % (name, n, line[:200]))
        if seen:
            ran = True
        else:
            quiet.append(name)
    if not ran:
        refuse("could not find a test summary in the CI logs, so zero skips is not proven; the hub checks this one")
    unknown = [name for name in quiet if name.strip().lower() not in QUIET_JOBS]
    if unknown:
        refuse("job %r is green but its log shows no test summary, and it is not a known non-test job (%s); it may be a test "
               "job that ran nothing, so zero skips is not proven; the hub checks this one"
               % (unknown[0], ", ".join(sorted(QUIET_JOBS))))
    return jobs, unreadable, quiet


# ---- 5. the diff ---------------------------------------------------------------------------------------------------------

DEPLOY_WORDS = ("deploy", "release", "publish")
DEPLOY_FILES = {"deploy.sh", "deploy.yml", "procfile", "fly.toml", "vercel.json", "netlify.toml", "eas.json"}
TEST_DIRS = {"tests", "test", "__tests__"}
CI_RULE = "a file under .github/, where the workflows, actions and scripts the checks run on live"
ADDED_RULES = [
    (re.compile(r"_update_\d{4,}", re.I), "a Drupal update function (_update_ then 4 or more digits)"),
    (re.compile(r"_post_update_", re.I), "a Drupal post-update function"),
    (re.compile(r"function\s+\w*_deploy_\w+", re.I), "a Drupal deploy function"),
    (re.compile(r"hook_update_N", re.I), "hook_update_N"),
    (re.compile(r"CREATE\s+TABLE", re.I), "CREATE TABLE"),
    (re.compile(r"ALTER\s+TABLE", re.I), "ALTER TABLE"),
    (re.compile(r"DROP\s+TABLE", re.I), "DROP TABLE"),
    (re.compile(r"DROP\s+COLUMN", re.I), "DROP COLUMN"),
]


def path_rule(path):
    """The rule a changed path trips, or ''."""
    p = path.strip().lower()
    parts = p.split("/")
    dirs, base = parts[:-1], parts[-1]
    in_tests = any(d in TEST_DIRS for d in dirs)
    if any(d in ("migrations", "migration") for d in dirs):
        return "a folder named migrations"
    if base.endswith(".sql"):
        return "a .sql file"
    if p.endswith("prisma/schema.prisma"):
        return "the Prisma schema"
    if "alembic" in dirs:
        return "an alembic folder"
    if "db/migrate/" in "/" + p:
        return "a db/migrate folder"
    if base.endswith((".install", ".post_update.php", ".deploy.php")):
        return "a Drupal install, post-update or deploy file"
    if p.startswith(".github/workflows/") and any(w in base for w in DEPLOY_WORDS):
        return "a deploy, release or publish workflow"
    if parts[0] == ".github" and dirs:
        return CI_RULE
    if base in DEPLOY_FILES:
        return "a deploy file (%s)" % base
    if any(d in ("deploy", ".deploy") for d in dirs):
        return "a deploy folder"
    if not in_tests and re.match(r"^(docker-)?compose.*\.ya?ml$", base):
        return "a compose file"
    if not in_tests and base.startswith("dockerfile"):
        return "a Dockerfile"
    return ""


def patch_rule(patch):
    """(file, rule, line) for the first added patch line that trips a rule, or None."""
    current = "?"
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current = line.rsplit(" b/", 1)[-1]
        elif line.startswith("+") and not line.startswith("+++"):
            for rx, name in ADDED_RULES:
                if rx.search(line):
                    return current, name, line[:120]
    return None


def unquote(name):
    """git puts a path with unusual characters in double quotes, with backslash escapes; give back the plain path."""
    name = name.strip()
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        inner = name[1:-1]
        try:
            return inner.encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")
        except (UnicodeError, ValueError):
            return inner
    return name


def side(name):
    """The path from one side of a file header ("a/x", "b/x" or /dev/null), or ''."""
    name = unquote(name.split("\t")[0])
    if name == "/dev/null":
        return ""
    return name[2:] if name[:2] in ("a/", "b/") else name


def patch_paths(patch):
    """Every path named in the patch's file headers, old names and new, in order and once each.

    The list of changed files gives only the new names, so a migration that is renamed away or deleted would pass unseen.
    Only the header of each file is read (from its "diff --git" line to its first "@@"), never the changed lines themselves."""
    found = []
    in_header = False
    for line in patch.splitlines():
        got = []
        if line.startswith("diff --git "):
            in_header = True
            rest = line[len("diff --git "):]
            half = (len(rest) - 1) // 2
            if len(rest) % 2 == 1 and rest[half] == " " and side(rest[:half]) == side(rest[half + 1:]):
                got.append(side(rest[:half]))
        elif line.startswith("@@"):
            in_header = False
        elif in_header:
            for lead in ("rename from ", "rename to ", "copy from ", "copy to "):
                if line.startswith(lead):
                    got.append(unquote(line[len(lead):]))
            if line.startswith(("--- ", "+++ ")):
                got.append(side(line[4:]))
            m = re.match(r"^Binary files (.+) and (.+) differ$", line)
            if m:
                got += [side(m.group(1)), side(m.group(2))]
        for path in got:
            if path and path not in found:
                found.append(path)
    return found


def check_diff(number, repo):
    rc, names, err = gh(["pr", "diff", str(number), "-R", repo, "--name-only"], 120)
    if rc != 0 or not names.strip():
        refuse("could not read the list of changed files (%s); the hub checks this one" % ((err.strip() or "empty list")[:200]))
    rc, patch, err = gh(["pr", "diff", str(number), "-R", repo], 180)
    if rc != 0 or not patch.strip():
        refuse("could not read the patch (%s); the hub checks this one" % ((err.strip() or "empty patch")[:200]))
    where = "this change touches a database migration or a deploy step; the hub decides this one"
    paths = [p.strip() for p in names.splitlines() if p.strip()]
    for path in paths + [p for p in patch_paths(patch) if p not in paths]:
        rule = path_rule(path)
        if rule == CI_RULE:
            refuse("this change edits the CI itself; the hub merges this one (file %s, rule: %s)" % (path, rule))
        if rule:
            refuse("%s (file %s, rule: %s)" % (where, path, rule))
    hit = patch_rule(patch)
    if hit:
        refuse("%s (file %s, rule: added line with %s: %r)" % (where, hit[0], hit[1], hit[2]))


# ---- the whole run -------------------------------------------------------------------------------------------------------

def facts(repo, pr, c, jobs, unreadable, quiet):
    title = " ".join((pr.get("title") or "").split())
    names = ", ".join(c["pass"])
    text = "%s PR %s (%s)" % (repo, pr.get("number"), title)
    detail = "%d checks: %s; 0 skipped, read from %d job logs" % (len(c["pass"]), names, jobs)
    unseen = "; not readable: %s; no test summary in: %s" % (", ".join(unreadable) or "none", ", ".join(quiet) or "none")
    return text, detail, unseen


def run(number, repo, check_only):
    if not REPO_RX.match(repo):
        refuse("-R must look like OWNER/REPO")
    check_repo(repo)
    pr, why = read_pr(number, repo)
    if pr is None:
        refuse("could not read the pull request (%s)" % why[:200])
    check_state(pr)
    word, line, c, _ = merge_gate.verdict(pr)
    if word != "GREEN":
        refuse("the checks are not green (%s); wait or fix, then try again" % line)
    jobs, unreadable, quiet = check_logs(repo, pr)
    check_diff(number, repo)
    head = pr["headRefOid"]
    # The diff was read after the checks. Read the pull request once more: if its head moved in between, the diff that was
    # read may belong to a commit whose checks were never looked at.
    again, why = read_pr(number, repo)
    if again is None:
        refuse("could not read the pull request a second time (%s); the hub checks this one" % why[:200])
    if again.get("headRefOid") != head:
        refuse("the branch moved while it was being checked (head was %s, now %s); wait for the new checks, then try again"
               % (head[:8], (again.get("headRefOid") or "unknown")[:8]))
    text, detail, nr = facts(repo, pr, c, jobs, unreadable, quiet)
    if check_only:
        print("WOULD MERGE %s head %s on green (%s%s)" % (text, head[:8], detail, nr))
        return 0
    rc, out, err = gh(["pr", "merge", str(number), "-R", repo, "--squash", "--delete-branch", "--match-head-commit", head], 180)
    if rc != 0:
        refuse("gh would not merge: %s" % (err.strip() or out.strip() or "no message"))
    after, why = read_pr(number, repo)
    oid = ((after or {}).get("mergeCommit") or {}).get("oid") or ""
    if not after or (after.get("state") or "").upper() != "MERGED" or not oid:
        print("MERGE NOT CONFIRMED: gh said the merge went through but the pull request does not show MERGED with a merge commit; "
              "look at it by hand. The roll-up line was not written.")
        return 1
    note = "- %s %s squash-merged %s on green (%s; lane_merge.py)." % (
        merge_gate.clock(), text, oid[:8], detail)
    state_dir = os.path.expanduser(os.environ.get("LANE_MERGE_STATE_DIR") or "~/.claude/state")
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "merge-rollup-%s.md" % datetime.date.today().isoformat())
    with open(path, "a", encoding="utf-8") as f:
        f.write(note + "\n")
    print(note)
    print(nr.lstrip("; "))
    print("MERGED")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("pr", type=int)
    ap.add_argument("-R", "--repo", required=True)
    ap.add_argument("--check", action="store_true", help="check everything and print the verdict; merge nothing, write nothing")
    a = ap.parse_args(argv)
    try:
        return run(a.pr, a.repo, a.check)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
