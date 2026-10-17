"""session_prime_facts.py: the facts, the field replacement, the stamp and the post guard.
Fixtures are invented; `gh` is a fake on PATH and the git repo is a throwaway. Set SESSION_PRIME_REAL=1 to
also run against the live hub (read-only: facts mode and an identity replacement on the real body)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "session_prime_facts.py"
sys.path.insert(0, str(HERE.parent))
import session_prime_facts as sp  # noqa: E402

BODY = """**Purpose:** roadmap.

## Prime prompt

```
/prep
```

## Stop reading here

## Roadmap

<details>
<summary><b>Arc 1: Launch season</b>, through 10/31 (2 open, 1 done)</summary>

- [ ] **First open item that is long enough to be a title**
- [ ] Second open item
</details>

<details>
<summary><b>Arc 2: Later</b> (1 open, 0 done)</summary>

- [ ] Later thing
</details>

<details>
<summary><b>Next 3 sessions</b>: the old plan.</summary>

**Session +1 (hub):** old first.
1. old item

  <details><summary>nested</summary>
  inner
  </details>

</details>

### Next session should

Old next-session line.

---

## Why this exists

Sessions end.

<!-- roadmap-stamp v1 sha=0000000000000000000000000000000000000000 branch=main generated=2026-10-01T00:00:00Z issue=#7 -->
"""

NEW_ARC = """<details>
<summary><b>Arc 1: Launch season</b>, through 11/30 (1 open, 2 done)</summary>

- [ ] **Only open item now, written so it is long enough**
</details>"""
NEW_N3 = """<details>
<summary><b>Next 3 sessions</b>: the new plan.</summary>

**Session +1 (hub):** new first.
</details>"""

SECTION_BODY = """## Roadmap

### Active arc

**Old arc** text.

Remaining tasks:
- [ ] one

---

### Next 3 sessions queued

**Session +1:** old

---

### Next session should

Old line.

---

<!-- roadmap-stamp v1 sha=abc branch=main generated=2026-10-01T00:00:00Z issue=#7 -->
"""

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys, shutil
d = os.environ["FAKE_GH_DIR"]
a = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
def out(x): sys.stdout.write(x if isinstance(x, str) else json.dumps(x))
def arg(flag):
    return a[a.index(flag) + 1] if flag in a else None
cmd = " ".join(a[:2])
if cmd == "repo view" and "isPrivate" in a:
    out({"isPrivate": os.environ.get("FAKE_PRIVATE", "1") == "1"})
elif cmd == "repo view":
    out(open(os.path.join(d, "repo.txt")).read())
elif cmd == "issue list" and arg("--label"):
    out([{"number": 7, "title": "session-prime"}] if not os.path.exists(os.path.join(d, "none")) else [])
elif cmd == "issue list":
    out(open(os.path.join(d, "issues.json")).read())
elif cmd == "pr list":
    out(open(os.path.join(d, "prs.json")).read())
elif cmd == "issue view":
    out(open(os.path.join(d, "issue.json")).read())
elif cmd == "issue edit":
    shutil.copy(arg("--body-file"), os.path.join(d, "edited-body.md"))
elif cmd == "issue comment":
    shutil.copy(arg("--body-file"), os.path.join(d, "comment.md"))
else:
    sys.stderr.write("fake gh: unhandled " + cmd + "\n"); sys.exit(9)
'''

FAKE_HOOK = r'''#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_DIR/hook.log"
echo "drift 0 commits (exact, fake)"
'''


def git(cwd, *args, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test", **(env or {}))
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=e).stdout


class Editing(unittest.TestCase):
    def test_next_should_replaced_heading_kept_rest_untouched(self):
        out = sp.replace_field(BODY, "next-should", "Brand new line.\n")
        self.assertIn("### Next session should\n\nBrand new line.\n\n---", out)
        self.assertNotIn("Old next-session line", out)
        self.assertIn("## Why this exists", out)

    def test_arc_details_block_matched_by_number(self):
        out = sp.replace_field(BODY, "arc", NEW_ARC)
        self.assertIn("through 11/30", out)
        self.assertNotIn("First open item", out)
        self.assertIn("Arc 2: Later", out)  # the other arc is untouched

    def test_next3_details_block_with_nested_details(self):
        out = sp.replace_field(BODY, "next3", NEW_N3)
        self.assertIn("the new plan", out)
        self.assertNotIn("old first", out)
        self.assertNotIn("inner", out)
        self.assertEqual(out.count("<details"), out.count("</details>"))
        self.assertIn("### Next session should", out)

    def test_section_shapes(self):
        out = sp.replace_field(SECTION_BODY, "arc", "### Active arc\n\n**New arc** text.\n")
        self.assertIn("**New arc**", out)
        self.assertIn("### Next 3 sessions queued", out)
        out = sp.replace_field(out, "next3", "### Next 3 sessions queued\n\n**Session +1:** new\n")
        self.assertIn("**Session +1:** new", out)
        self.assertNotIn("old", out.split("Next session should")[0])
        out = sp.replace_field(out, "next-should", "New line.")
        self.assertIn("New line.", out)

    def test_not_found_and_malformed_are_errors_not_guesses(self):
        with self.assertRaises(sp.Fail):
            sp.replace_field(BODY, "arc", NEW_ARC.replace("Arc 1", "Arc 9"))
        with self.assertRaises(sp.Fail):
            sp.replace_field(BODY, "next3", "<details>\n<summary><b>Something else</b></summary>\n</details>")
        with self.assertRaises(sp.Fail):
            sp.replace_field(BODY, "arc", NEW_ARC.replace("</details>", ""))
        with self.assertRaises(sp.Fail):
            sp.replace_field("no sections here\n", "next-should", "x")
        with self.assertRaises(sp.Fail):
            sp.replace_field(SECTION_BODY, "arc", "just prose")

    def test_stamp_replaced_once_or_added(self):
        out = sp.refresh_stamp(BODY, "f" * 40, "main", "2026-10-16T12:00:00Z", 7)
        self.assertEqual(out.count("roadmap-stamp"), 1)
        self.assertIn("sha=" + "f" * 40 + " branch=main generated=2026-10-16T12:00:00Z issue=#7 -->", out)
        bare = sp.refresh_stamp("body only\n", "a" * 40, "main", "g", 7)
        self.assertTrue(bare.rstrip().endswith("issue=#7 -->"))
        self.assertEqual(bare.count("roadmap-stamp"), 1)


class Facts(unittest.TestCase):
    SINCE = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)

    def test_window_changes(self):
        rows = [
            {"number": 1, "title": "the roadmap", "createdAt": "2026-09-01T00:00:00Z", "closedAt": None},
            {"number": 2, "title": "opened now", "createdAt": "2026-10-16T21:00:00Z", "closedAt": None},
            {"number": 3, "title": "closed now", "createdAt": "2026-10-01T00:00:00Z", "closedAt": "2026-10-16T22:00:00Z"},
            {"number": 4, "title": "closed long ago", "createdAt": "2026-10-01T00:00:00Z", "closedAt": "2026-10-02T00:00:00Z"},
            {"number": 5, "title": "merged now", "createdAt": "2026-10-10T00:00:00Z", "closedAt": "2026-10-16T23:00:00Z", "mergedAt": "2026-10-16T23:00:00Z"},
        ]
        opened, closed = sp.window_changes(rows, self.SINCE, skip=1)
        self.assertEqual(opened, [(2, "opened now")])
        self.assertEqual(closed, [(3, "closed now", False), (5, "merged now", True)])

    def test_ticked_since_reads_done_lines(self):
        inbox = (
            "- [x] 2026-10-10 10:00 | Old item | d | aa\n  - done 2026-10-11: before the window\n"
            "- [x] 2026-10-12 10:00 | Date only item | d | bb\n  - done 2026-10-16: today\n"
            "- [x] 2026-10-12 11:00 | Timed item | d | cc\n  - done 2026-10-16 23:30: late tonight\n"
            "- [ ] 2026-10-13 10:00 | Still open | d | dd\n"
        )
        since = datetime(2026, 10, 16, 12, 0).astimezone()
        got = [t for t, _ in sp.ticked_since(inbox, since)]
        self.assertEqual(got, ["Date only item", "Timed item"])

    def test_comment_facts_drops_empty_sections(self):
        f = dict(repo="o/r", branch="main", commits=[("a" * 40, "aaaaaaa", "Did a thing")], unpushed=0, issues_opened=[],
                 issues_closed=[(3, "Closed one", False)], prs_opened=[], prs_closed=[(5, "Merged one", True)], lanes=[], inbox=[])
        text = sp.comment_facts(f)
        self.assertIn("**Shipped (1 commit, all on `origin/main`):**", text)
        self.assertIn("[`aaaaaaa`](https://github.com/o/r/commit/" + "a" * 40 + ") — Did a thing", text)
        self.assertIn("**Closed:** #3 (Closed one), PR #5 (Merged one, merged)", text)
        for absent in ("**Opened:**", "Lanes closed", "Inbox items"):
            self.assertNotIn(absent, text)

    def test_unpushed_commits_are_said(self):
        f = dict(repo="o/r", branch="main", commits=[("a" * 40, "aaaaaaa", "x")], unpushed=1, issues_opened=[], issues_closed=[],
                 prs_opened=[], prs_closed=[], lanes=[], inbox=[])
        self.assertIn("1 not yet pushed to `origin/main`", sp.comment_facts(f))

    def test_render_comment_shape(self):
        f = dict(repo="o/r", branch="main", commits=[], unpushed=0, issues_opened=[], issues_closed=[], prs_opened=[], prs_closed=[],
                 lanes=[], inbox=[])
        now = datetime(2026, 10, 16, 17, 45).astimezone()
        c = sp.render_comment(f, now, "NX-01", "~/repos", "abc123", "**Watch:**\n- Sunday release", "Do the thing.\n\nSecond para.")
        lines = c.splitlines()
        self.assertRegex(lines[0], r"^## Session ended 2026-10-16 5:45 PM \S+ \(NX-01, ~/repos, session abc123\)$")
        self.assertIn("_Nothing shipped, opened or closed since the last comment._", c)
        self.assertIn("**Watch:**\n- Sunday release", c)
        self.assertEqual(lines[-1], "**Next session should:** Do the thing.")


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.d = t / "fake"
        self.d.mkdir()
        gh = t / "bin" / "gh"
        gh.parent.mkdir()
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.hook = t / "drift.sh"
        self.hook.write_text(FAKE_HOOK)
        self.state = t / "state"
        self.lanes = t / "lanes"
        self.lanes.mkdir()
        (t / "inbox.md").write_text("- [x] 2026-10-16 09:00 | Ticked tonight | d | aa\n  - done 2026-10-16 23:00: ok\n")
        self.inbox = t / "inbox.md"
        # git: a bare origin and a clone with one old and two new commits
        bare, self.repo = t / "origin.git", t / "work"
        git(t, "init", "-q", "--bare", "-b", "main", str(bare))
        git(t, "clone", "-q", str(bare), str(self.repo))
        git(self.repo, "checkout", "-q", "-b", "main")
        for msg, when in (("old commit", "2026-10-01T10:00:00Z"), ("new one", "2026-10-16T21:00:00Z"), ("new two", "2026-10-16T22:00:00Z")):
            (self.repo / "f.txt").write_text(msg)
            git(self.repo, "add", "f.txt")
            git(self.repo, "commit", "-q", "-m", msg, "--date", when, env={"GIT_COMMITTER_DATE": when})
        git(self.repo, "push", "-q", "origin", "main")
        (self.d / "repo.txt").write_text("o/r\n")
        self.write_issue("2026-10-16T20:30:00Z")
        (self.d / "issues.json").write_text(json.dumps([
            {"number": 7, "title": "session-prime", "createdAt": "2026-09-01T00:00:00Z", "closedAt": None},
            {"number": 8, "title": "a new issue", "createdAt": "2026-10-16T21:30:00Z", "closedAt": None}]))
        (self.d / "prs.json").write_text(json.dumps([
            {"number": 9, "title": "a merged pr", "createdAt": "2026-10-15T00:00:00Z", "closedAt": "2026-10-16T22:10:00Z", "mergedAt": "2026-10-16T22:10:00Z"}]))
        (self.lanes / "2026-10-16-lane-x").mkdir()
        (self.lanes / "2026-10-16-lane-x" / "report.md").write_text("# R\n\nLane x finished.\n")
        self.env = dict(os.environ, PATH=f"{gh.parent}:{os.environ['PATH']}", FAKE_GH_DIR=str(self.d))

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, updated, body=BODY):
        (self.d / "issue.json").write_text(json.dumps({
            "body": body, "updatedAt": updated, "createdAt": "2026-09-01T00:00:00Z",
            "comments": [{"createdAt": "2026-10-16T20:30:00Z", "body": "## Session ended earlier"}]}))

    def run_cli(self, *extra, repo="o/r", machine="NX-01"):
        cmd = [sys.executable, str(SCRIPT), "--repo", repo, "--dir", str(self.repo), "--machine", machine,
               "--state-dir", str(self.state), "--drift-hook", str(self.hook), "--lanes-dir", str(self.lanes),
               "--inbox", str(self.inbox), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, env=self.env)

    def calls(self):
        p = self.d / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def fieldfiles(self):
        t = Path(self.tmp.name)
        (t / "arc.md").write_text(NEW_ARC)
        (t / "n3.md").write_text(NEW_N3)
        (t / "ns.md").write_text("Start on the new thing.\n")
        (t / "note.md").write_text("**Watch:**\n- Sunday release\n")
        return ["--arc", str(t / "arc.md"), "--next3", str(t / "n3.md"), "--next-should", str(t / "ns.md"), "--note", str(t / "note.md")]

    def test_facts_print_and_save_read_at(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in ("o/r#7", "READ_AT 2026-10-16T20:30:00Z", "stamp now: sha=", "drift: drift 0 commits", "**Shipped (2 commits, all on `origin/main`):**",
                       "new one", "new two", "**Closed:** PR #9 (a merged pr, merged)", "**Opened:** #8 (a new issue)"):
            self.assertIn(needle, r.stdout)
        self.assertNotIn("old commit", r.stdout)
        self.assertNotIn("Lanes closed", r.stdout)  # not the private hub: no lane or inbox facts
        self.assertEqual(json.loads((self.state / "o__r.json").read_text())["read_at"], "2026-10-16T20:30:00Z")

    def test_public_or_other_repos_never_carry_lane_or_inbox_facts(self):
        r = self.run_cli(repo="o/other")
        self.assertNotIn("Lane x finished", r.stdout)
        self.assertNotIn("Ticked tonight", r.stdout)

    def test_private_hub_carries_lanes_and_inbox(self):
        r = self.run_cli("--dry-run", repo=sp.HUB_REPO)
        self.assertIn("**Lanes closed (1, first line of each report):**\n- lane-x: Lane x finished.", r.stdout)
        self.assertIn("**Inbox items ticked (1):**\n- Ticked tonight", r.stdout)
        self.env["FAKE_PRIVATE"] = "0"
        r = self.run_cli("--dry-run", repo=sp.HUB_REPO)
        self.assertNotIn("Lane x finished", r.stdout)

    def test_dry_run_facts_saves_nothing(self):
        r = self.run_cli("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.state.exists())

    def test_post_dry_run_prints_the_comment_and_never_writes(self):
        self.run_cli()
        r = self.run_cli("--post", "--dry-run", "--session", "s1", *self.fieldfiles())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY RUN", r.stdout)
        self.assertIn("body: replace arc", r.stdout)
        self.assertIn("updatedAt guard: ok", r.stdout)
        self.assertRegex(r.stdout, r"## Session ended \d{4}-\d\d-\d\d \d{1,2}:\d\d [AP]M \S+ \(NX-01, .*, session s1\)")
        self.assertIn("**Next session should:** Start on the new thing.", r.stdout)
        self.assertFalse(any(c.startswith(("issue edit", "issue comment")) for c in self.calls()))
        self.assertNotIn("seed-cache", (self.d / "hook.log").read_text())  # only the facts run's check

    def test_post_writes_body_comment_and_seeds_the_drift_cache(self):
        self.run_cli()
        r = self.run_cli("--post", *self.fieldfiles())
        self.assertEqual(r.returncode, 0, r.stderr)
        body = (self.d / "edited-body.md").read_text()
        self.assertIn("through 11/30", body)
        self.assertIn("the new plan", body)
        self.assertIn("Start on the new thing.", body)
        self.assertEqual(body.count("roadmap-stamp"), 1)
        self.assertNotIn("sha=0000000000000000000000000000000000000000", body)
        comment = (self.d / "comment.md").read_text()
        self.assertIn("**Shipped (2 commits", comment)
        self.assertIn("**Watch:**", comment)
        hook = (self.d / "hook.log").read_text().splitlines()
        self.assertTrue(hook[-2].startswith("seed-cache o/r 7 "))
        self.assertEqual(hook[-1], "check")
        self.assertFalse((self.state / "o__r.json").exists())  # one facts run, one post

    def test_post_refuses_when_the_issue_changed_since_the_facts(self):
        self.run_cli()
        self.write_issue("2026-10-16T23:59:00Z")
        r = self.run_cli("--post", *self.fieldfiles())
        self.assertEqual(r.returncode, 6)
        self.assertIn("changed since the facts were read", r.stderr)
        self.assertFalse(any(c.startswith(("issue edit", "issue comment")) for c in self.calls()))

    def test_post_without_a_facts_run_refuses(self):
        r = self.run_cli("--post", *self.fieldfiles())
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("run the facts step first", r.stderr)
        self.assertFalse(any(c.startswith("issue edit") for c in self.calls()))

    def test_bad_field_posts_nothing(self):
        self.run_cli()
        t = Path(self.tmp.name)
        (t / "bad.md").write_text(NEW_ARC.replace("Arc 1", "Arc 9"))
        r = self.run_cli("--post", "--arc", str(t / "bad.md"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(any(c.startswith(("issue edit", "issue comment")) for c in self.calls()))

    def test_tower_cannot_rewrite_the_hub_priorities_but_can_post_a_note(self):
        self.run_cli(repo=sp.HUB_REPO, machine="NCC-74656")
        r = self.run_cli("--post", *self.fieldfiles(), repo=sp.HUB_REPO, machine="NCC-74656")
        self.assertEqual(r.returncode, 2)
        self.assertIn("belong to NX-01", r.stderr)
        t = Path(self.tmp.name)
        r = self.run_cli("--post", "--note", str(t / "note.md") if (t / "note.md").exists() else "/dev/null",
                         repo=sp.HUB_REPO, machine="NCC-74656")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.d / "comment.md").exists())

    def test_unknown_machine_refuses_to_post(self):
        self.run_cli()
        r = self.run_cli("--post", machine="UNKNOWN")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(any(c.startswith("issue edit") for c in self.calls()))

    def test_no_or_many_rolling_issues_stop(self):
        (self.d / "none").write_text("")
        r = self.run_cli()
        self.assertEqual(r.returncode, 4)


@unittest.skipUnless(os.environ.get("SESSION_PRIME_REAL"), "set SESSION_PRIME_REAL=1 to run against the live hub")
class RealState(unittest.TestCase):
    def test_facts_mode_on_the_live_hub(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--dir", str(Path.home() / "repos")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("session-prime facts for TortoiseWolfe/workspace#1", r.stdout)
        self.assertIn("drift:", r.stdout)
        self.assertIn("--- facts for the comment ---", r.stdout)

    def test_identity_replacement_on_the_live_body(self):
        body = subprocess.run(["gh", "issue", "view", "1", "-R", sp.HUB_REPO, "--json", "body", "-q", ".body"],
                              capture_output=True, text=True, timeout=60).stdout
        self.assertGreater(len(body), 10000)
        lines = body.split("\n")
        s, e = sp._details_span(lines, r"<summary><b>Arc 1\b")
        self.assertEqual(sp.replace_field(body, "arc", "\n".join(lines[s:e])), body)
        s, e = sp._details_span(lines, r"<summary><b>Next 3 sessions")
        self.assertEqual(sp.replace_field(body, "next3", "\n".join(lines[s:e])), body)
        s, e = sp._section_span(lines, r"###\s+Next session should\b")
        text = "\n".join(lines[s + 1:e]).strip("\n")
        self.assertEqual(sp.replace_field(body, "next-should", text), body)


if __name__ == "__main__":
    unittest.main()
