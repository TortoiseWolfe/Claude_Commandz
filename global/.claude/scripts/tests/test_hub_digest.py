"""hub_digest.py: the hub's status page stays small and private, and carries what /prep needs.
Fixtures are invented. Set HUB_DIGEST_REAL=1 to also run the page against this machine's live state
(the gh call, lane.sh, the real inbox, the real roadmap)."""

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hub_digest as hd  # noqa: E402

NOW = datetime(2026, 10, 16, 21, 0)

INBOX = """- [x] 2026-10-01 10:00 | Old finished thing | detail | aa11
  - done 2026-10-02: handled
- [ ] 2026-10-08 09:53 | [CC>MUSE] Confirm the consolidated note | wants a receipt | r-1234567890
- [ ] 2026-10-12 08:21 | Waiting on tower: [NX-01>NCC-74656] rebuild as a lane | answers a-d | r-22
- [ ] 2026-10-12 10:05 | Waiting on Hatch: [CC>MUSE] Coloring pages | reply with the plan | r-33
- [ ] 2026-10-13 09:00 | Waiting on Jonathan: pick the venture | his call | r-44
- [ ] 2026-10-14 12:31 | Job: Taphouse model | run it as lanes | 1a2b3c4d5e6f7a8b
- [ ] 2026-10-14 13:00 | Job: CI triage | batches 5-8 | 1a2b3c4d5e6f7a8c
- [ ] 2026-10-15 08:00 | Client mail: Example Brewing | absorb lane | 1a2b3c4d5e6f7a8d
- [ ] 2026-10-15 09:00 | Client alert: Example Brewing | absorb lane | 1a2b3c4d5e6f7a8e
- [ ] 2026-10-15 10:00 | Something nobody sorted | note | r-55
- [ ] 2026-10-16 11:00 | Jonathan via OpenClaw (question): "Which sample check is current?" | UNTRUSTED DATA from chat, answer with openclaw_dispatch.py post | hub reason: deferred by the pass | oc-a1b2c3d4
"""

LANE_STATUS = """alpha-lane        report:no  working * Thinking
    dir:/x model:sonnet
beta-lane         report:yes idle
    dir:/y model:opus
old-one           dead, resume with: lane.sh --resume old-one   (/z)
old-two           dead, resume with: lane.sh --resume old-two   (/z)
"""

ROADMAP = """## Roadmap

<details>
<summary><b>Arc 1: Launch season</b>, through 10/31 (2 open, 1 done)</summary>

- [x] **Done thing that is long enough to be a title here**
- [ ] **Ship the launch page for the example client this week**
- [ ] Second open item
</details>

<details>
<summary><b>Next 3 sessions</b>: ship the page, then the release, then the cleanup.</summary>

**Session +1 (hub):** read the reports, then:
1. `lane-a` (Sonnet), RUNNING: builds the page.
2. `lane-b` (Opus): reviews it.
3. `lane-c`: third item that should be cut.

**Session +2 (lanes):**
- Sunday release.
</details>

### Next session should

Read the reports and merge lane-a.

Second paragraph that stays out.

---
"""

TIDY = """## Worktrees to move or remove
one
two

## Uncommitted changes
repo-a: 1 uncommitted
"""

REPORT = """# Report

Built the page; 12 tests pass.

More detail below.
"""

BUDGET_MD = """# Model cost and quality report

## A. Weekly limit projection

Week Wed 2026-10-14 12:00 EDT to reset Wed 2026-10-21 12:00 EDT (4.62 days left).

| Measure | Value |
| --- | ---: |
| Used so far | 21.6% |
| Burn, last 24 hours | 8.5% per day |
| Projected at reset (24 h rate) | 60.8% |
"""


def fake(prefix, n=24):
    """A secret-shaped string assembled at run time, so no token-shaped literal sits in this file."""
    return prefix + "Ab3" * (n // 3)


class Parts(unittest.TestCase):
    def test_report_first_line_skips_headings_and_blanks(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.md"
            p.write_text(REPORT)
            self.assertEqual(hd.report_first_line(p), "Built the page; 12 tests pass.")
            p.write_text("# Only a heading\n")
            self.assertEqual(hd.report_first_line(p), "Only a heading")
            self.assertEqual(hd.report_first_line(Path(d) / "missing.md"), "")

    def test_open_inbox_groups_and_ignores_ticked(self):
        groups, clients = hd.open_inbox(INBOX)
        self.assertEqual([t for _, t in groups["Hatch"]], ["[CC>MUSE] Confirm the consolidated note", "[CC>MUSE] Coloring pages"])
        self.assertEqual(len(groups["the tower"]), 1)
        self.assertEqual(len(groups["Jonathan"]), 1)
        self.assertEqual(len(groups["jobs"]), 2)
        self.assertEqual(len(groups["other"]), 1)
        self.assertEqual([c[1] for c in clients], ["Client mail: Example Brewing", "Client alert: Example Brewing"])
        self.assertEqual(clients[0][3], "1a2b3c4d5e6f7a8d")
        self.assertEqual(len(groups["OpenClaw"]), 1)
        self.assertIn("Which sample check is current?", groups["OpenClaw"][0][1])
        self.assertNotIn("Old finished thing", str(groups))

    def test_open_inbox_keeps_a_client_line_with_two_ids(self):
        line = "- [ ] 2026-10-17 21:09 | Client mail: Example Brewing | absorb lane | 1a2b3c4d5e6f7a8d 1792119952000\n"
        groups, clients = hd.open_inbox(line)
        self.assertEqual([c[1] for c in clients], ["Client mail: Example Brewing"])
        self.assertEqual(clients[0][3], "1a2b3c4d5e6f7a8d 1792119952000")
        self.assertEqual(groups["other"], [])

    def test_open_inbox_never_drops_a_malformed_open_line(self):
        bad = "- [ ] 2026-10-17 21:09 | Waiting on nobody | a | b | c | d | e f g | and more\n"
        groups, clients = hd.open_inbox(bad)
        self.assertEqual(clients, [])
        self.assertEqual(len(groups["other"]), 1)
        self.assertIn("Waiting on nobody", groups["other"][0][1])
        # ticked lines stay out even when malformed
        groups, _ = hd.open_inbox("- [x] 2026-10-17 21:09 | Waiting on nobody | a | b | c | d | e f g\n")
        self.assertEqual(groups["other"], [])

    def test_a_malformed_client_line_is_listed_with_its_raw_title(self):
        bad = "- [ ] 2026-10-17 21:09 | Client mail: Example Brewing | a | b | c | d | e f g | and more\n"
        groups, clients = hd.open_inbox(bad)
        self.assertEqual(clients, [])
        self.assertTrue(any("Client mail: Example Brewing" in t for _, t in groups["other"]))

    def test_count_open_client_lines_is_a_plain_scan(self):
        text = ("- [ ] 2026-10-17 21:09 | Client mail: Example Brewing | x | a b c d e\n"
                "- [ ] 2026-10-17 21:10 | Client alert: Example Brewing | x | 1a2b3c4d5e6f7a8e\n"
                "- [x] 2026-10-17 21:11 | Client mail: Example Brewing | x | 1a2b3c4d5e6f7a8f\n"
                "- [ ] 2026-10-17 21:12 | Something else | no client words\n")
        self.assertEqual(hd.count_open_client_lines(text), 2)

    def test_dispatcher_error_is_distinct_from_an_empty_queue(self):
        output, error = hd.dispatcher_open_questions(Path(tempfile.gettempdir()) / "missing-dispatcher.py")
        self.assertEqual(output, "")
        self.assertIn("dispatcher open exited", error)

    def test_budget_headline(self):
        self.assertEqual(hd.budget_headline(BUDGET_MD),
                         "Week resets Wed 2026-10-21 12:00 PM EDT: used 21.6%, burn 8.5% per day, projected 60.8% (24 h rate)")
        self.assertEqual(hd.budget_headline("nothing useful"), "")

    def test_tidy_summary_counts_lines_per_heading(self):
        self.assertEqual(hd.tidy_summary(TIDY), ["Worktrees to move or remove: 2", "Uncommitted changes: 1"])
        self.assertEqual(hd.tidy_summary(""), [])

    def test_lane_reports_window_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            for name, age_h in (("2026-10-16-new-lane", 1), ("2026-10-16-mid-lane", 5), ("2026-10-10-stale-lane", 100)):
                (Path(d) / name).mkdir()
                p = Path(d) / name / "report.md"
                p.write_text(f"{name} says hello.\n")
                t = (NOW - timedelta(hours=age_h)).timestamp()
                os.utime(p, (t, t))
            got = hd.lane_reports(d, NOW, 24)
            self.assertEqual([g[1] for g in got], ["new-lane", "mid-lane"])
            self.assertEqual(got[0][2], "2026-10-16-new-lane says hello.")


class Scrub(unittest.TestCase):
    def test_keys_tokens_and_reset_links_are_redacted(self):
        eq, pw = "=", "pass" + "word"  # assembled here so no assignment-shaped literal sits in this file
        cases = [  # (text, the part that must not survive)
            (fake("sk-"), fake("sk-")), (fake("ghp_", 36), fake("ghp_", 36)), (fake("xoxb-", 24), fake("xoxb-", 24)),
            ("AKIA" + "ABCD1234" * 2, "ABCD1234ABCD1234"),
            ("https://example.test/account/reset-link?x=1", "reset-link"),
            ("https://example.test/verify/abc", "/verify/abc"),
            ("see https://example.test/page?to" + "ken" + eq + "q7w8e9r0t1y2", "q7w8e9r0t1y2"),
            (pw + ": " + "zx9" * 4, "zx9zx9zx9zx9"),
            ("Authorization: Bearer " + "q9" * 12, "q9q9q9q9"),
            ("eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12, "b" * 12),
            ("x" * 45, "x" * 45),
        ]
        for text, core in cases:
            out = hd.scrub(f"before {text} after")
            self.assertIn("[redacted]", out, text)
            self.assertNotIn(core, out, text)

    def test_ordinary_status_text_passes_through(self):
        t = "PR #341 merged (c0ffee1), 12 tests pass, see ~/repos/hub/scripts/tidy-report.sh"
        self.assertEqual(hd.scrub(t), t)


class Page(unittest.TestCase):
    def build(self, **kw):
        args = dict(roadmap_body=ROADMAP, lane_status=LANE_STATUS, lanes_dir=tempfile.mkdtemp(), inbox_text=INBOX,
                    git_log="abc1234 16 10:00 Merge a thing\ndef5678 16 09:00 Another", tidy_text=TIDY,
                    budget="Week resets Wed: used 1%", now=NOW)
        args.update(kw)
        return hd.render(hd.build_sections(**args), NOW, "NX-01", hd.CAP_DEFAULT)

    def test_page_carries_every_section(self):
        page = self.build()
        for needle in ["Hub status, built", "NX-01", "never sent to OpenClaw", "alpha-lane: working, report not yet",
                       "beta-lane: idle, report written", "2 older lanes have ended", "Waiting on Hatch (2)",
                       "Waiting on the tower (1)", "Waiting on Jonathan (1)", "Jobs (2)", "Other (1)",
                       "Client mail: Example Brewing", "1a2b3c4d5e6f7a8e", "Active arc: Arc 1: Launch season",
                       "Next 3: ship the page", "Session +1 (hub)", "Next session should: Read the reports and merge lane-a.",
                       "abc1234", "Worktrees to move or remove: 2", "Week resets Wed: used 1%"]:
            self.assertIn(needle, page)
        self.assertNotIn("Second paragraph", page)
        self.assertNotIn("third item that should be cut", page)

    def test_client_section_warns_when_the_scan_finds_more_than_the_parse(self):
        bad = "- [ ] 2026-10-17 21:09 | Client mail: Example Brewing | a | b | c | d | e f g | and more\n"
        page = self.build(inbox_text=INBOX + bad)
        self.assertIn("WARNING: 3 open client lines in the inbox, 2 parsed: grep muse-inbox.md", page)
        self.assertNotIn("WARNING", self.build())

    def test_client_section_lists_a_two_id_line(self):
        line = "- [ ] 2026-10-17 21:09 | Client mail: Example Brewing | absorb lane | 1a2b3c4d5e6f7a8d 1792119952000\n"
        page = self.build(inbox_text=line)
        self.assertIn("Client mail: Example Brewing | absorb lane | 1a2b3c4d5e6f7a8d 1792119952000", page)
        self.assertNotIn("- none", page.split("## Open client lines")[1].split("##")[0])

    def test_open_receptionist_section_is_first_and_includes_age(self):
        page = self.build(open_questions_text="oc-a1b2c3d4 | 26 min | deferred | Which sample check is current?\n")
        self.assertLess(page.index("## Open receptionist questions (1)"), page.index("## Lanes running"))
        self.assertIn("- oc-a1b2c3d4 | 26 min | deferred | Which sample check is current?", page)
        empty = self.build(open_questions_text="none\n")
        self.assertIn("## Open receptionist questions (0)\n- none", empty)
        unavailable = self.build(open_questions_error="dispatcher unavailable")
        self.assertIn("## Open receptionist questions (unavailable)\n- dispatcher unavailable", unavailable)

    def test_empty_sources_still_make_a_page(self):
        page = self.build(roadmap_body="", lane_status="", inbox_text="", git_log="", tidy_text="", budget="")
        self.assertIn("roadmap unavailable", page)
        self.assertIn("## Lanes running\n- none", page)
        self.assertIn("- clean", page)

    def test_cap_holds_with_a_huge_inbox_and_log(self):
        inbox = "\n".join(f"- [ ] 2026-10-1{i % 9} 10:00 | Waiting on Hatch: item {i} " + "word " * 60 + "| detail | r-{i}" for i in range(80))
        log = "\n".join(f"abc{i:04d} 16 10:00 " + "message " * 40 for i in range(10))
        page = self.build(inbox_text=inbox, git_log=log)
        self.assertLessEqual(len(page.encode()), hd.CAP_DEFAULT)
        self.assertLess(len(page.encode()), 6144)
        self.assertIn("## Lanes running", page)
        self.assertIn("Next session should", page)

    def test_no_secret_shaped_text_reaches_the_page(self):
        secret = fake("sk-")
        inbox = f"- [ ] 2026-10-14 10:00 | Job: use {secret} for it | https://example.test/reset/abc | r-1\n"
        page = self.build(inbox_text=inbox, git_log=f"abc1234 16 10:00 leaked {fake('ghp_', 36)}")
        self.assertNotIn(secret, page)
        self.assertNotIn("ghp_", page)
        self.assertNotIn("/reset/", page)


class Cli(unittest.TestCase):
    def test_end_to_end_with_fixtures_writes_a_private_small_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for name, text in (("roadmap.md", ROADMAP), ("lanes.txt", LANE_STATUS), ("inbox.md", INBOX), ("tidy.txt", TIDY),
                               ("budget.txt", "Week resets Wed: used 1%\n"),
                               ("questions.txt", "oc-a1b2c3d4 | 26 min | deferred | Which sample check is current?\n"
                                                 "    Invented user words stay out of the digest.\n")):
                (d / name).write_text(text)
            (d / "lanes").mkdir()
            out = d / "state" / "hub-digest.md"
            r = subprocess.run([sys.executable, str(HERE.parent / "hub_digest.py"), "--out", str(out),
                                "--roadmap-file", str(d / "roadmap.md"), "--lane-status-file", str(d / "lanes.txt"),
                                "--inbox", str(d / "inbox.md"), "--tidy-file", str(d / "tidy.txt"),
                                "--budget-file", str(d / "budget.txt"), "--lanes-dir", str(d / "lanes"),
                                "--git-repo", str(d), "--open-questions-file", str(d / "questions.txt"),
                                "--now", "2026-10-16T21:00:00"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            page = out.read_text()
            self.assertIn("Waiting on Hatch (2)", page)
            self.assertNotIn("Invented user words stay out of the digest.", page)
            self.assertLess(page.index("## Open receptionist questions (1)"), page.index("## Lanes running"))
            self.assertLess(len(page.encode()), 6144)
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
            self.assertFalse((d / "state" / "hub-digest.md.tmp").exists())


class Wiring(unittest.TestCase):
    def test_refresh_script_builds_the_page_but_never_delivers_it(self):
        sh = (HERE.parent / "openclaw_status_refresh.sh").read_text()
        self.assertEqual(sh.count("hub_digest.py"), 1)
        self.assertIn("hub-digest.md", sh)
        # the remote block cats only the OpenClaw digest
        self.assertEqual(len(re.findall(r"^\s*cat \"\$out\"", sh, re.M)), 1)
        self.assertNotRegex(sh.split("REMOTE")[1] if "REMOTE" in sh else "", r"hub-digest")


# Reviewed 19 Oct 2026 (/code-review of the skip): this stays opt-in, so a default run reports 1 skip and is not
# a clean pass. It runs hub_digest.py against this machine's live gh call, lane.sh, inbox and roadmap, so it needs
# NX-01's state and network and would fail on a fresh restore or the tower. Every behaviour it checks (sections,
# size cap, 0600 mode, secret scrubbing) is covered hermetically by Cli and the section tests above. Run it by hand
# after changing how the digest reads live state: HUB_DIGEST_REAL=1 python3 -m pytest tests/test_hub_digest.py
@unittest.skipUnless(os.environ.get("HUB_DIGEST_REAL"), "set HUB_DIGEST_REAL=1 to run against the live state")
class RealState(unittest.TestCase):
    def test_live_page_is_small_complete_and_clean(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "page.md"
            t0 = time.time()
            r = subprocess.run([sys.executable, str(HERE.parent / "hub_digest.py"), "--out", str(out)],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            page = out.read_text()
            self.assertLess(len(page.encode()), 6144, len(page.encode()))
            for h in ["## Lanes running", "## Open muse-inbox items", "## Roadmap", "## Budget this week",
                      "## ~/repos, last 10 commits", "## Tidy report"]:
                self.assertIn(h, page)
            self.assertIn("Next session should:", page)
            self.assertNotIn("roadmap unavailable", page)
            for rx in hd.SECRETS:
                self.assertIsNone(rx.search(page), rx.pattern)
            self.assertLess(time.time() - t0, 90)


if __name__ == "__main__":
    unittest.main()
