#!/usr/bin/env python3
"""Merge gate for one pull request: count its checks and skips, and print the verdict, the roll-up line and the merge command. No model.

The standing rule: a PR merges on green only when every check passed, none is pending, and nothing was skipped. Counting that by
eye from `gh pr checks` is how a pending check or a skipped job gets called green. This counts it the same way every time.
It never merges anything; a lane's safety check refuses `gh pr merge`, so the hub runs the command this prints.

  merge_gate.py PR [-R OWNER/REPO]     (default repo: the one the current folder belongs to, as gh decides)

Prints, in order:
  1. the verdict line:  GREEN 2/2 skips 0        or   PENDING 1/2 ...  /  RED ...  /  SKIPS ...  /  CONFLICT  /  NO CHECKS  /  MERGED  /  CLOSED
  2. the roll-up line:  <h:mm AM> | repo | title (#n) | merge sha | checks | skips      (sha says "not merged yet" until the PR is merged)
  3. only when GREEN and open: the merge command to put under "Hub merges" in report.md

Exit codes: 0 = GREEN (or already MERGED); 1 = anything else; 2 = could not read the PR.
Counting: a check passes when its conclusion is SUCCESS. SKIPPED and NEUTRAL count as skips and block (a skipped job means the suite
did not run). QUEUED, IN_PROGRESS and PENDING are pending. FAILURE, CANCELLED, TIMED_OUT, ACTION_REQUIRED, STARTUP_FAILURE, STALE
and ERROR are failures. Both kinds of status gh reports (check runs and commit statuses) are counted.
Test hooks: GH names the gh program; MERGE_GATE_NOW ("9:05 AM") pins the time.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

PASS = {"SUCCESS"}
SKIP = {"SKIPPED", "NEUTRAL"}
PENDING = {"", "QUEUED", "IN_PROGRESS", "PENDING", "EXPECTED", "WAITING", "REQUESTED"}


def clock():
    forced = os.environ.get("MERGE_GATE_NOW")
    if forced:
        return forced
    return datetime.datetime.now().strftime("%I:%M %p").lstrip("0")


def bucket(item):
    """'pass', 'skip', 'pending' or 'fail' for one statusCheckRollup entry (a check run or a commit status)."""
    if item.get("__typename") == "StatusContext" or "context" in item:
        state = (item.get("state") or "").upper()
        if state == "SUCCESS":
            return "pass"
        return "pending" if state in PENDING else "fail"
    status = (item.get("status") or "").upper()
    conclusion = (item.get("conclusion") or "").upper()
    if status != "COMPLETED":
        return "pending"
    if conclusion in PASS:
        return "pass"
    if conclusion in SKIP:
        return "skip"
    return "pending" if conclusion in PENDING else "fail"


def name_of(item):
    return item.get("name") or item.get("context") or "?"


def count(rollup):
    out = {"pass": [], "skip": [], "pending": [], "fail": []}
    for it in rollup or []:
        out[bucket(it)].append(name_of(it))
    return out


def verdict(pr):
    c = count(pr.get("statusCheckRollup"))
    total = sum(len(v) for v in c.values())
    npass = len(c["pass"])
    tail = "%d/%d skips %d" % (npass, total, len(c["skip"]))
    state = (pr.get("state") or "").upper()
    if state == "MERGED":
        return "MERGED", "MERGED %s" % tail, c, total
    if state == "CLOSED":
        return "CLOSED", "CLOSED without merging", c, total
    if (pr.get("mergeable") or "").upper() == "CONFLICTING":
        return "CONFLICT", "CONFLICT the branch does not merge cleanly", c, total
    if total == 0:
        return "NO CHECKS", "NO CHECKS reported yet", c, total
    if c["fail"]:
        return "RED", "RED %s; failing: %s" % (tail, ", ".join(c["fail"])), c, total
    if c["pending"]:
        return "PENDING", "PENDING %s; waiting on: %s" % (tail, ", ".join(c["pending"])), c, total
    if c["skip"]:
        return "SKIPS", "SKIPS %s; skipped: %s (a skipped job means the suite did not run)" % (tail, ", ".join(c["skip"])), c, total
    return "GREEN", "GREEN %s" % tail, c, total


def rollup_line(repo, pr, c, total):
    oid = ((pr.get("mergeCommit") or {}).get("oid") or "")
    sha = oid[:8] if oid else "not merged yet"
    title = " ".join((pr.get("title") or "").split())
    return "%s | %s | %s (#%s) | %s | %d of %d checks pass | skips %d" % (
        clock(), repo, title, pr.get("number"), sha, len(c["pass"]), total, len(c["skip"]))


def fetch(number, repo):
    gh = os.environ.get("GH", "gh")
    cmd = [gh, "pr", "view", str(number), "--json", "number,title,state,mergeable,mergeCommit,statusCheckRollup,url"]
    if repo:
        cmd += ["-R", repo]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("cannot run gh: %s" % e, file=sys.stderr)
        return None
    if r.returncode != 0:
        print("gh could not read the PR: %s" % (r.stderr.strip() or r.stdout.strip()), file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        print("gh gave something that is not JSON", file=sys.stderr)
        return None


def repo_of(pr, given):
    if given:
        return given
    parts = (pr.get("url") or "").split("/")
    return "/".join(parts[3:5]) if len(parts) >= 5 else "this repo"


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("pr", type=int)
    ap.add_argument("-R", "--repo", default="")
    a = ap.parse_args(argv)
    pr = fetch(a.pr, a.repo)
    if pr is None:
        return 2
    repo = repo_of(pr, a.repo)
    word, line, c, total = verdict(pr)
    print(line)
    print(rollup_line(repo, pr, c, total))
    if word == "GREEN":
        print("gh pr merge %d -R %s --squash --delete-branch" % (a.pr, repo))
    return 0 if word in ("GREEN", "MERGED") else 1


if __name__ == "__main__":
    sys.exit(main())
