#!/usr/bin/env python3
"""Lane merge: the one narrow merge a lane may run. Checks everything, squash-merges one pull request, writes the roll-up line. No model.

  lane_merge.py PR -R OWNER/REPO            check everything, then squash-merge, then write the roll-up line
  lane_merge.py PR -R OWNER/REPO --check    check everything and print the verdict; merge nothing, write nothing

-R is required. There is no flag or environment variable that skips a check, widens the merge or picks another merge method.
It refuses (one line starting "REFUSED: ", exit 1), in this order, when:
  1. the repo is on the own-review list (a missing list refuses too), 2. the pull request is not open, is a draft or conflicts,
  3. merge_gate.py does not say GREEN, 4. the CI logs show any skipped, deselected, expected-fail or todo test, or show no test
  summary at all, or a log cannot be fetched, 5. the diff touches a database migration or a deploy step,
  6. gh itself will not merge (branch protection that needs a review ends here).
The merge is pinned to the commit that was checked (--match-head-commit), so what was checked is what merges.
After the merge it re-reads the pull request, and only then appends one line to <state dir>/merge-rollup-YYYY-MM-DD.md.
Exit codes: 0 = merged (or, with --check, would merge); 1 = refused or not confirmed.
Test hooks only: GH (the gh program), MERGE_GATE_NOW (pins the time), LANE_MERGE_STATE_DIR (default ~/.claude/state),
LANE_MERGE_OWN_REVIEW (default ~/.config/afa/own-review-repos.txt).
"""
import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_gate  # noqa: E402

FIELDS = "number,title,state,mergeable,mergeCommit,statusCheckRollup,url,isDraft,headRefOid,baseRefName"
REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class Refused(Exception):
    pass


def refuse(why):
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


# ---- 1. own-review list ------------------------------------------------------------------------------------------------

def own_review_path():
    return os.path.expanduser(os.environ.get("LANE_MERGE_OWN_REVIEW") or "~/.config/afa/own-review-repos.txt")


def check_own_review(repo):
    path = own_review_path()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        refuse("own-review list not found at %s; the hub merges this one" % path)
    for line in lines:
        pat = line.strip()
        if not pat or pat.startswith("#"):
            continue
        if fnmatch.fnmatchcase(repo.lower(), pat.lower()):
            refuse("this repo keeps its own review process (it matches %r on the own-review list); the hub merges this one" % pat)


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

PYTEST_BARE = re.compile(r"^[=\s]*(\d+ [a-z]+(?:, \d+ [a-z]+)*)\s+in\s+[\d.:]+s", re.I)
PYTEST_BAR = re.compile(r"^=+\s*(\d+ [a-z]+(?:, \d+ [a-z]+)*)\s*=*\s*$", re.I)
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
            pairs = COUNT_WORD.findall(m.group(1))
            if any(w.lower() in ("passed", "failed", "skipped", "deselected", "xfailed", "xpassed", "error", "errors") for _, w in pairs):
                saw = True
                found += sum(int(n) for n, w in pairs if w.lower() in ("skipped", "deselected", "xfailed", "xpassed"))
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


def check_logs(repo, pr):
    """Read every Actions job log. Returns (job log count, [names of checks that cannot be read])."""
    jobs = 0
    unreadable = []
    ran = False
    for item in pr.get("statusCheckRollup") or []:
        name = merge_gate.name_of(item)
        m = JOB_URL.search(item.get("detailsUrl") or "") if item.get("__typename") != "StatusContext" else None
        if not m:
            unreadable.append(name)
            continue
        rc, out, err = gh(["api", "repos/%s/actions/jobs/%s/logs" % (repo, m.group(1))], 180)
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
    if not ran:
        refuse("could not find a test summary in the CI logs, so zero skips is not proven; the hub checks this one")
    return jobs, unreadable


# ---- 5. the diff ---------------------------------------------------------------------------------------------------------

DEPLOY_WORDS = ("deploy", "release", "publish")
DEPLOY_FILES = {"deploy.sh", "deploy.yml", "procfile", "fly.toml", "vercel.json", "netlify.toml", "eas.json"}
TEST_DIRS = {"tests", "test", "__tests__"}
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


def check_diff(number, repo):
    rc, names, err = gh(["pr", "diff", str(number), "-R", repo, "--name-only"], 120)
    if rc != 0 or not names.strip():
        refuse("could not read the list of changed files (%s); the hub checks this one" % ((err.strip() or "empty list")[:200]))
    rc, patch, err = gh(["pr", "diff", str(number), "-R", repo], 180)
    if rc != 0 or not patch.strip():
        refuse("could not read the patch (%s); the hub checks this one" % ((err.strip() or "empty patch")[:200]))
    where = "this change touches a database migration or a deploy step; the hub decides this one"
    for path in names.splitlines():
        rule = path_rule(path)
        if rule:
            refuse("%s (file %s, rule: %s)" % (where, path.strip(), rule))
    hit = patch_rule(patch)
    if hit:
        refuse("%s (file %s, rule: added line with %s: %r)" % (where, hit[0], hit[1], hit[2]))


# ---- the whole run -------------------------------------------------------------------------------------------------------

def facts(repo, pr, c, jobs, unreadable):
    title = " ".join((pr.get("title") or "").split())
    names = ", ".join(c["pass"])
    text = "%s PR %s (%s)" % (repo, pr.get("number"), title)
    detail = "%d checks: %s; 0 skipped, read from %d job logs" % (len(c["pass"]), names, jobs)
    return text, detail, ("; not readable: %s" % ", ".join(unreadable)) if unreadable else "; not readable: none"


def run(number, repo, check_only):
    if not REPO_RX.match(repo):
        refuse("-R must look like OWNER/REPO")
    check_own_review(repo)
    pr, why = read_pr(number, repo)
    if pr is None:
        refuse("could not read the pull request (%s)" % why[:200])
    check_state(pr)
    word, line, c, total = merge_gate.verdict(pr)
    if word != "GREEN":
        refuse("the checks are not green (%s); wait or fix, then try again" % line)
    jobs, unreadable = check_logs(repo, pr)
    check_diff(number, repo)
    head = pr["headRefOid"]
    text, detail, nr = facts(repo, pr, c, jobs, unreadable)
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
