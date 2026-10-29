"""hub_digest.py: the hub's status page stays small and private, and carries what /prep needs.
Fixtures are invented; nothing here reads this machine's live state (hub_digest.py checks that itself on every live build)."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hub_digest as hd  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

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

    def test_open_inbox_groups_the_towers_peer_lines(self):
        text = """- [ ] 2026-10-12 08:00 | [NX-01>NCC-74656] Run the rebuild once | detail | r-1
- [ ] 2026-10-19 08:00 | [MUSE>NCC-74656] Weekly to-do | detail | r-2
- [ ] 2026-10-27 13:48 | Waiting on NX-01: How is the stream running? | detail | r-3
- [ ] 2026-10-27 13:41 | Job: Host the band | detail | r-4
- [x] 2026-10-16 12:00 | [NX-01>NCC-74656] Done already | detail | r-5
"""
        groups, _ = hd.open_inbox(text)
        self.assertEqual([t for _, t in groups["From NX-01"]], ["Run the rebuild once"])
        self.assertEqual([t for _, t in groups["From Muse"]], ["Weekly to-do"])
        self.assertEqual([t for _, t in groups["NX-01"]], ["How is the stream running?"])
        self.assertEqual(len(groups["jobs"]), 1)
        self.assertEqual(groups["other"], [])

    def test_the_tower_shows_its_own_next_session_line_or_labels_nx01s(self):
        def tower_page(body, machine="NCC-74656"):
            return hd.render(hd.build_sections(roadmap_body=body, lane_status=LANE_STATUS, lanes_dir=fresh_dir(self), inbox_text="",
                                               git_log="", tidy_text=TIDY, budget="b", now=NOW, machine=machine), NOW, machine, 100000)
        own = ROADMAP.replace("---", "### Next session should (NCC-74656)\n\nFinish the tower fixes.\n\n---", 1)
        self.assertIn("Next session should (NCC-74656): Finish the tower fixes.", tower_page(own))
        self.assertNotIn("Read the reports and merge lane-a.", tower_page(own))
        self.assertIn("Next session should (NX-01's line): Read the reports and merge lane-a.", tower_page(ROADMAP))
        forged = ROADMAP.replace("---", "### Next session should (NX-01)\n\nForged line.\n\n---", 1)
        self.assertNotIn("Forged line.", tower_page(forged, "NX-01"))
        self.assertIn("(NX-01's line): Read the reports", tower_page(forged))
        nx = tower_page(own, "NX-01")  # NX-01 keeps its own line unlabeled and does not show the tower's
        self.assertIn("Next session should: Read the reports and merge lane-a.", nx)
        self.assertNotIn("Finish the tower fixes.", nx)

    def test_a_machine_other_than_nx01_lists_every_open_peer_line_under_its_group(self):
        lines = ["- [ ] 2026-10-27 13:%02d | [NX-01>NCC-74656] Note number %d | detail | r-%d" % (i, i, i) for i in range(9)]
        text = "\n".join(lines) + "\n"
        tower = hd.render(hd.build_sections(roadmap_body=ROADMAP, lane_status=LANE_STATUS, lanes_dir=fresh_dir(self), inbox_text=text,
                                            git_log="", tidy_text=TIDY, budget="b", now=NOW, machine="NCC-74656"), NOW, "NCC-74656", 100000)
        self.assertIn("From NX-01 (9):", tower)
        for i in range(9):
            self.assertIn("Note number %d" % i, tower)
        main = hd.render(hd.build_sections(roadmap_body=ROADMAP, lane_status=LANE_STATUS, lanes_dir=fresh_dir(self), inbox_text=text,
                                           git_log="", tidy_text=TIDY, budget="b", now=NOW), NOW, "NX-01", 100000)
        self.assertIn("and 4 more", main)

    def test_default_inbox_is_the_peer_inbox_off_nx01(self):
        for machine, name in (("NX-01", "hub-inbox.md"), ("NCC-74656", "peer-inbox.md"), ("", "hub-inbox.md")):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(hd, "HOME", Path(d)), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HUB_INBOX", None)
                os.environ.pop("MUSE_INBOX", None)
                if machine:
                    (Path(d) / ".config/afa").mkdir(parents=True)
                    (Path(d) / ".config/afa/machine").write_text(machine + "\n")
                self.assertEqual(Path(hd.default_inbox()).name, name, machine)
        with mock.patch.dict(os.environ, {"HUB_INBOX": "/x/own.md"}):
            self.assertEqual(hd.default_inbox(), "/x/own.md")

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

    def test_lane_reports_uses_handoff_when_newer(self):
        d = Path(fresh_dir(self))
        name = "2026-10-27-handoff-lane"
        (d / name).mkdir()
        p_rep = d / name / "report.md"
        p_hand = d / name / "handoff.md"

        p_rep.write_text("# Report\nStale report line\n")
        p_hand.write_text("# Handoff\nDone: task A\nNext: task B\n")

        # Make handoff newer
        t_rep = (NOW - timedelta(minutes=10)).timestamp()
        t_hand = (NOW - timedelta(minutes=5)).timestamp()
        os.utime(p_rep, (t_rep, t_rep))
        os.utime(p_hand, (t_hand, t_hand))

        got = hd.lane_reports(str(d), NOW, 24)
        self.assertEqual(len(got), 1)
        # The handoff summary comes before the report line
        self.assertEqual(got[0][2], "(Done: task A | Next: task B) Stale report line")

    def test_lane_reports_handoff_only_no_report(self):
        d = Path(fresh_dir(self))
        name = "2026-10-27-handoff-only"
        (d / name).mkdir()
        p_hand = d / name / "handoff.md"

        p_hand.write_text("# Handoff\nDone: only handoff task\nNext: follow up\n")
        t_hand = (NOW - timedelta(minutes=5)).timestamp()
        os.utime(p_hand, (t_hand, t_hand))

        got = hd.lane_reports(str(d), NOW, 24)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][2], "(Done: only handoff task | Next: follow up)")


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
        args = dict(roadmap_body=ROADMAP, lane_status=LANE_STATUS, lanes_dir=fresh_dir(self), inbox_text=INBOX,
                    git_log="abc1234 16 10:00 Merge a thing\ndef5678 16 09:00 Another", tidy_text=TIDY,
                    budget="Week resets Wed: used 1%", now=NOW)
        args.update(kw)
        return hd.render(hd.build_sections(**args), NOW, "NX-01", hd.CAP_DEFAULT)

    def test_budget_section_names_the_phase_and_phase_3_holds_no_reserve(self):
        et = ZoneInfo("America/New_York")
        def budget_lines(now):
            secs = dict(hd.build_sections(**{**dict(roadmap_body=ROADMAP, lane_status=LANE_STATUS, lanes_dir=fresh_dir(self), inbox_text=INBOX,
                                                    git_log="", tidy_text=TIDY, budget="Week resets Wed: used 1%"), "now": datetime.fromtimestamp(now.timestamp())}))  # the build uses naive local time
            return secs["Budget this week"]
        self.assertEqual(budget_lines(datetime(2026, 10, 28, 9, 0, tzinfo=et))[1],
                         "Phase 3: no reserve; start anything held for after the reset now")  # Wed 9 AM, before the noon reset
        self.assertEqual(budget_lines(datetime(2026, 10, 27, 20, 0, tzinfo=et))[1], "Phase 3: no reserve; start anything held for after the reset now")
        self.assertEqual(budget_lines(datetime(2026, 10, 26, 12, 0, tzinfo=et))[1], "Phase 2 of 3")
        self.assertEqual(budget_lines(datetime(2026, 10, 24, 12, 0, tzinfo=et))[1], "Phase 1 of 3")
        self.assertNotIn("no reserve", " ".join(budget_lines(datetime(2026, 10, 28, 13, 0, tzinfo=et))))  # after the noon reset: phase 1 again

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
        self.assertIn("WARNING: 3 open client lines in the inbox, 2 parsed: grep hub-inbox.md", page)
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

    def test_spend_section_shows_the_top_three_lanes_by_cost_today(self):
        spend = {"lanes": [{"name": "acme-mail", "usd": 62.48, "families": {"fable": 60.0, "opus": 2.48}},
                           {"name": "b-lane", "usd": 30.1, "families": {"opus": 30.1}},
                           {"name": "c-lane", "usd": 20.0, "families": {"sonnet": 20.0}},
                           {"name": "d-lane", "usd": 5.0, "families": {"sonnet": 5.0}}], "other": 1.0, "total": 118.0}
        page = self.build(spend=spend)
        for needle in ["## Spend today (API-equivalent; the hub, then top 3 lanes", "- acme-mail: $62 (fable+opus)", "- b-lane: $30 (opus)",
                       "- c-lane: $20 (sonnet)", "all lanes and the hub together: $118"]:
            self.assertIn(needle, page)
        self.assertNotIn("d-lane", page)
        self.assertNotIn("- Hub (", page)   # a spend answer with no hub in it adds no hub line

    def test_spend_section_puts_the_hubs_own_spend_above_the_lanes_with_its_share_and_context(self):
        spend = {"lanes": [{"name": "acme-mail", "usd": 62.48, "families": {"opus": 62.48}}], "other": 1.0, "total": 400.0,
                 "hub": {"usd": 23.6, "families": {"opus": 23.6},
                         "week": {"since": 1793203200, "usd": 163.8, "total": 432.75, "share": 0.3786, "sessions": 13,
                                  "median_context": 168754, "first_context": 60976}}}
        lines = hd.spend_section(spend)[1]
        self.assertEqual(lines[0], "- Hub (started in ~/repos, not a lane): today $24; since the weekly reset $164 = 38% of all use; "
                                   "13 sessions typed in, median context per turn 169K (first turn 61K)")
        self.assertEqual(lines[1:], ["- acme-mail: $62 (opus)", "- all lanes and the hub together: $400"])
        self.assertIn("a lane past $25 wakes the hub", hd.spend_section(spend)[0])

    def test_hub_spend_line_degrades_to_what_the_json_carries(self):
        self.assertEqual(hd.hub_spend_line({"usd": 4.0, "week": None}), "- Hub (started in ~/repos, not a lane): today $4")
        quiet = {"usd": 0, "week": {"usd": 5.0, "share": 0.0116, "sessions": 0, "median_context": None, "first_context": None}}
        self.assertEqual(hd.hub_spend_line(quiet), "- Hub (started in ~/repos, not a lane): today $0; since the weekly reset $5 = 1% of all use")
        no_first = {"usd": 1, "week": {"usd": 2, "share": 0.5, "sessions": 1, "median_context": 90000, "first_context": None}}
        self.assertTrue(hd.hub_spend_line(no_first).endswith("1 session typed in, median context per turn 90K"))
        self.assertIsNone(hd.hub_spend_line(None))
        self.assertIsNone(hd.hub_spend_line({"lanes": []}))

    def test_spend_section_says_so_when_it_could_not_be_measured(self):
        self.assertIn("- not measured", self.build())
        self.assertIn("- unavailable: lane_spend.py gave no readable answer", self.build(spend="lane_spend.py gave no readable answer"))
        self.assertIn("- all lanes and the hub together: $0", self.build(spend={"lanes": [], "total": 0}))

    def test_cap_holds_with_a_huge_inbox_and_log(self):
        inbox = "\n".join(f"- [ ] 2026-10-1{i % 9} 10:00 | Waiting on Hatch: item {i} " + "word " * 60 + "| detail | r-{i}" for i in range(80))
        log = "\n".join(f"abc{i:04d} 16 10:00 " + "message " * 40 for i in range(10))
        page = self.build(inbox_text=inbox, git_log=log)
        self.assertLessEqual(len(page.encode()), hd.CAP_DEFAULT)
        self.assertLess(len(page.encode()), hd.PAGE_HARD_CAP)
        self.assertIn("## Lanes running", page)
        self.assertIn("Next session should", page)

    def test_a_busy_page_keeps_the_next_session_line(self):
        # 19 Oct 6:05 PM: seven open questions and seventeen lanes filled the page, and the cap cut the roadmap from its end, which
        # was the "Next session should" line the prime prompt tells the hub to start from.
        qs = "".join("oc-%08x | %d min | deferred | %s\n" % (i, i, "a long question about the porch columns " * 4) for i in range(30))
        lanes = "\n".join("lane-%02d: idle, report written" % i for i in range(60))
        page = self.build(open_questions_text=qs, lane_status=lanes)
        self.assertLessEqual(len(page.encode()), hd.CAP_DEFAULT)
        self.assertIn("Next session should: Read the reports and merge lane-a.", page)

    def test_jr_dev_section_follows_the_client_lines_and_the_page_stays_small(self):
        lines = ["Jr Dev: [x] L3 in a week (app #1), [ ] merged", "Clock: not running; next pick app #2, not started",
                 "Longest review wait: app #3 PR #4, 3.0 days, held by nobody named"]
        page = self.build(internship=lines)
        self.assertIn("## Jr Dev", page)
        self.assertIn("- Clock: not running; next pick app #2, not started", page)
        self.assertLess(page.index("## Open client lines"), page.index("## Jr Dev"))
        self.assertLess(page.index("## Jr Dev"), page.index("## Waiting on") if "## Waiting on" in page else len(page))
        self.assertLessEqual(len(page.encode()), hd.CAP_DEFAULT)
        self.assertNotIn("## Jr Dev", self.build())
        self.assertNotIn("## Jr Dev", self.build(internship=[]))

    def test_jr_dev_lines_come_from_the_script_and_a_missing_script_adds_nothing(self):
        d = Path(fresh_dir(self))
        (d / "fake.py").write_text("print('Jr Dev: ok')\nprint()\nprint('Clock: x')\n")
        self.assertEqual(hd.internship_lines(d / "fake.py"), ["Jr Dev: ok", "Clock: x"])
        self.assertEqual(hd.internship_lines(d / "missing.py"), [])
        (d / "slow.py").write_text("import time; time.sleep(5)\n")
        self.assertEqual(hd.internship_lines(d / "slow.py", timeout=1), [])

    def test_hours_section_shows_the_weekly_line_and_a_stale_or_missing_file_adds_nothing(self):
        d = Path(fresh_dir(self))
        f = d / "hours-week.txt"
        f.write_text("Hours, week of Mon 19 Oct: Acme 9.4 h (typed 9.0 h, call 0.4 h)\n")
        page = self.build(hours=hd.hours_lines(f))
        self.assertIn("## Hours", page)
        self.assertIn("- Hours, week of Mon 19 Oct: Acme 9.4 h", page)
        self.assertNotIn("## Hours", self.build())
        self.assertNotIn("## Hours", self.build(hours=hd.hours_lines(d / "missing.txt")))
        self.assertEqual(hd.hours_lines(f, now=f.stat().st_mtime + 10 * 86400), [])
        self.assertEqual(hd.hours_lines(f, now=f.stat().st_mtime + 2 * 86400), ["Hours, week of Mon 19 Oct: Acme 9.4 h (typed 9.0 h, call 0.4 h)"])
        self.assertLessEqual(len(page.encode()), hd.CAP_DEFAULT)

    def test_no_secret_shaped_text_reaches_the_page(self):
        secret = fake("sk-")
        inbox = f"- [ ] 2026-10-14 10:00 | Job: use {secret} for it | https://example.test/reset/abc | r-1\n"
        page = self.build(inbox_text=inbox, git_log=f"abc1234 16 10:00 leaked {fake('ghp_', 36)}")
        self.assertNotIn(secret, page)
        self.assertNotIn("ghp_", page)
        self.assertNotIn("/reset/", page)


class WeekStart(unittest.TestCase):
    """The hub's share is counted from the weekly reset, which the page reads where its Budget section does: the quality report's line."""
    EST = ZoneInfo("America/New_York")
    BUDGET = "Week resets Wed 2026-11-04 12:00 PM EST: used 16.1%, burn 18.7% per day, projected 133.2% (24 h rate)"

    def test_the_reset_is_read_from_the_budget_line(self):
        self.assertEqual(hd.weekly_reset_ts(self.BUDGET), datetime(2026, 11, 4, 12, 0, tzinfo=self.EST).timestamp())
        self.assertEqual(hd.weekly_reset_ts("Week resets Thu 2026-11-05 1:30 AM EST: used 1%"),
                         datetime(2026, 11, 5, 1, 30, tzinfo=self.EST).timestamp())
        self.assertEqual(hd.weekly_reset_ts("Week resets Wed 2026-10-28 12:00 AM EDT: used 1%"),
                         datetime(2026, 10, 28, 0, 0, tzinfo=self.EST).timestamp())

    def test_no_reset_in_the_line_is_none(self):
        for line in ("budget report unavailable", "This week: used 4%", "", None):
            self.assertIsNone(hd.weekly_reset_ts(line))

    def test_the_week_began_seven_days_before_that_reset(self):
        now = datetime(2026, 10, 29, 8, 0, tzinfo=self.EST).timestamp()
        self.assertEqual(hd.week_start_ts(self.BUDGET, now), datetime(2026, 10, 28, 12, 0, tzinfo=self.EST).timestamp())

    def test_with_no_budget_line_the_dispatchers_wednesday_noon_rule_gives_the_same_start(self):
        for now in (datetime(2026, 10, 29, 8, 0, tzinfo=self.EST), datetime(2026, 10, 28, 11, 59, tzinfo=self.EST),
                    datetime(2026, 10, 28, 12, 1, tzinfo=self.EST)):
            start = hd.week_start_ts("budget report unavailable", now.timestamp())
            self.assertEqual(datetime.fromtimestamp(start, self.EST).strftime("%a %H:%M"), "Wed 12:00", now)
            self.assertLessEqual(start, now.timestamp())
            self.assertGreater(start + 7 * 86400, now.timestamp())

    def test_a_reset_already_past_is_ignored_for_the_rule(self):
        # a stale cached budget line from last week must not move the start back a week
        now = datetime(2026, 11, 5, 9, 0, tzinfo=self.EST).timestamp()
        self.assertEqual(hd.week_start_ts(self.BUDGET, now), datetime(2026, 11, 4, 12, 0, tzinfo=self.EST).timestamp())

    def test_lane_spend_is_asked_for_the_week_only_when_the_start_is_known(self):
        now = datetime(2026, 10, 29, 8, 0, tzinfo=self.EST).timestamp()
        cmd = hd.lane_spend_command(self.BUDGET, now)
        self.assertEqual(cmd[-2:], ["--week-start", str(int(datetime(2026, 10, 28, 12, 0, tzinfo=self.EST).timestamp()))])
        self.assertEqual(Path(cmd[1]).name, "lane_spend.py")
        self.assertIn("--json", cmd)
        with mock.patch.object(hd, "week_start_ts", return_value=None):
            self.assertEqual(hd.lane_spend_command(self.BUDGET, now)[-1], "--json")


class PublishBudget(unittest.TestCase):
    def test_value_is_one_line_with_a_stamp_the_figure_and_the_phase(self):
        v = hd.budget_published_value("Week resets Wed: used 1.5%", "Phase 1 of 3", datetime(2026, 10, 28, 14, 5))
        self.assertEqual(v.count("\n"), 0)
        self.assertRegex(v, r"^updated Wed 2026-10-28 2:05 PM \S+ \| Week resets Wed: used 1\.5% \| Phase 1 of 3$")

    def test_no_figure_means_nothing_to_publish(self):
        self.assertEqual(hd.budget_published_value("", "Phase 1 of 3", NOW), "")
        self.assertEqual(hd.budget_published_value("budget report unavailable", "Phase 1 of 3", NOW), "")
        self.assertFalse(hd.publish_budget("", run=lambda *a, **k: self.fail("must not call gh")))

    def test_publish_sets_the_variable_with_the_value_on_stdin_never_in_the_command(self):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.assertTrue(hd.publish_budget("updated x | used 1%", repo="o/r", run=run))
        cmd, kw = calls[0]
        self.assertEqual(cmd, ["gh", "variable", "set", "FLEET_BUDGET", "-R", "o/r"])
        self.assertEqual(kw["input"], "updated x | used 1%")
        self.assertNotIn("used 1%", " ".join(cmd))

    def test_a_failed_publish_is_reported_and_returns_false(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "HTTP 403")
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(hd.publish_budget("v", run=run))
            self.assertFalse(hd.publish_budget("v", run=lambda *a, **k: (_ for _ in ()).throw(OSError("no gh"))))
        self.assertIn("could not publish FLEET_BUDGET", err.getvalue())
        self.assertIn("HTTP 403", err.getvalue())

    def test_a_fixture_build_never_publishes(self):
        # --publish-budget with --roadmap-file must not call gh: put a gh that fails the test first on PATH.
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bin").mkdir()
            marker = d / "gh-called"
            gh = d / "bin" / "gh"
            gh.write_text(f"#!/bin/sh\ntouch {marker}\n")
            gh.chmod(0o755)
            for name, text in (("roadmap.md", ROADMAP), ("lanes.txt", LANE_STATUS), ("inbox.md", INBOX), ("tidy.txt", TIDY),
                               ("budget.txt", "Week resets Wed: used 1%\n"), ("questions.txt", ""), ("spend.json", '{"lanes": [], "other": 0, "total": 0}')):
                (d / name).write_text(text)
            (d / "lanes").mkdir()
            r = subprocess.run([sys.executable, str(HERE.parent / "hub_digest.py"), "--publish-budget", "--out", str(d / "o.md"),
                                "--roadmap-file", str(d / "roadmap.md"), "--lane-status-file", str(d / "lanes.txt"),
                                "--inbox", str(d / "inbox.md"), "--tidy-file", str(d / "tidy.txt"),
                                "--budget-file", str(d / "budget.txt"), "--lanes-dir", str(d / "lanes"),
                                "--git-repo", str(d), "--open-questions-file", str(d / "questions.txt"),
                                "--spend-file", str(d / "spend.json"), "--now", "2026-10-16T21:00:00"],
                               capture_output=True, text=True, env={**os.environ, "PATH": f"{d / 'bin'}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(marker.exists())


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
            (d / "spend.json").write_text('{"lanes": [{"name": "mail-x", "usd": 41.2, "families": {"fable": 41.2}}], "other": 0, "total": 41.2}')
            out = d / "state" / "hub-digest.md"
            r = subprocess.run([sys.executable, str(HERE.parent / "hub_digest.py"), "--out", str(out),
                                "--roadmap-file", str(d / "roadmap.md"), "--lane-status-file", str(d / "lanes.txt"),
                                "--inbox", str(d / "inbox.md"), "--tidy-file", str(d / "tidy.txt"),
                                "--budget-file", str(d / "budget.txt"), "--lanes-dir", str(d / "lanes"),
                                "--git-repo", str(d), "--open-questions-file", str(d / "questions.txt"),
                                "--spend-file", str(d / "spend.json"), "--now", "2026-10-16T21:00:00"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            page = out.read_text()
            self.assertIn("Waiting on Hatch (2)", page)
            self.assertNotIn("Invented user words stay out of the digest.", page)
            self.assertIn("- mail-x: $41 (fable)", page)
            self.assertLess(page.index("## Open receptionist questions (1)"), page.index("## Lanes running"))
            self.assertLess(len(page.encode()), hd.PAGE_HARD_CAP)
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
            self.assertFalse((d / "state" / "hub-digest.md.tmp").exists())


class MailIndexLines(unittest.TestCase):
    """The "Incoming mail" section: counts by class, and every unhandled person, job and client-alert row (made-up data only)."""

    def write(self, rows):
        d = fresh_dir(self)
        path = Path(d) / "mail-index.tsv"
        head = "id\tthread\tdate\tclass\twho\taddress\tsubject\thandled\n"
        lines = []
        for i, (cls, hours_ago, handled) in enumerate(rows):
            when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append("\t".join([f"{i + 1:016x}", f"t{i}", when, cls, "who", f"a{i}@example.com", f"Subject {i}", handled]))
        path.write_text(head + "\n".join(lines) + "\n")
        return path

    def test_counts_by_class_in_order_and_old_rows_not_counted(self):
        path = self.write([("person", 1, ""), ("bounce", 2, ""), ("bulk", 3, ""), ("bulk", 4, ""), ("client", 5, ""), ("bulk", 100, "")])
        lines = hd.mail_index_lines(path)
        self.assertEqual(lines[0], "Last 2 days: 1 bounce, 1 client, 1 person, 2 bulk")

    def test_unhandled_person_job_and_client_alert_rows_are_listed_clients_and_bounces_are_not(self):
        path = self.write([("person", 1, ""), ("job", 2, ""), ("client-alert", 3, ""), ("client", 4, ""), ("bounce", 5, ""), ("bulk", 6, "")])
        text = "\n".join(hd.mail_index_lines(path))
        for cls in ("person", "job", "client-alert"):
            self.assertIn(" %s " % cls, text)
        self.assertNotIn("Subject 3", text.replace("Subject 2", ""))  # the client row (index 3) is not listed
        self.assertEqual(len([l for l in hd.mail_index_lines(path) if l.startswith("- ")]), 3)

    def test_handled_rows_are_hidden_and_an_empty_list_says_so(self):
        path = self.write([("person", 1, "2026-10-29T12:00:00Z"), ("job", 2, "2026-10-29T12:00:00Z")])
        lines = hd.mail_index_lines(path)
        self.assertEqual(lines[1], "- no person, job or client-alert mail waiting")

    def test_old_unhandled_rows_are_still_listed_newest_first(self):
        path = self.write([("job", 200, ""), ("person", 100, "")])
        rows = [l for l in hd.mail_index_lines(path) if l.startswith("- ")]
        self.assertIn("person", rows[0])
        self.assertIn("job", rows[1])

    def test_more_than_the_shown_limit_ends_with_and_n_more(self):
        n = hd.MAIL_ROWS_SHOWN + 3
        path = self.write([("person", i + 1, "") for i in range(n)])
        lines = hd.mail_index_lines(path)
        self.assertEqual(len([l for l in lines if l.startswith("- ") and "more unhandled" not in l]), hd.MAIL_ROWS_SHOWN)
        self.assertEqual(lines[-1], "- and 3 more unhandled (mail_index.py list)")

    def test_missing_file_gives_none(self):
        self.assertIsNone(hd.mail_index_lines(Path(fresh_dir(self)) / "nope.tsv"))

    def test_a_bounce_inbox_line_reads_as_an_open_client_line(self):
        line = "- [ ] 2026-10-29 12:54 | Client alert: BOUNCE gone@example.com | original 1a16ab0165dd16db | 2026-10-28 | 1a18275baa407e7e\n"
        _, clients = hd.open_inbox(line)
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0][1], "Client alert: BOUNCE gone@example.com")
        self.assertEqual(hd.count_open_client_lines(line), 1)


class Wiring(unittest.TestCase):
    def test_refresh_script_builds_the_page_but_never_delivers_it(self):
        sh = (HERE.parent / "openclaw_status_refresh.sh").read_text()
        self.assertEqual(sh.count("hub_digest.py"), 1)
        self.assertIn("hub-digest.md", sh)
        # the remote block cats only the OpenClaw digest
        self.assertEqual(len(re.findall(r"^\s*cat \"\$out\"", sh, re.M)), 1)
        self.assertNotRegex(sh.split("REMOTE")[1] if "REMOTE" in sh else "", r"hub-digest")


class Decode(unittest.TestCase):
    def test_output_that_is_not_utf8_does_not_crash_the_digest(self):
        # 19 Oct: lane.sh --status carried wsl.exe's UTF-16 error text while the bridge was down, and the digest died on it.
        out = hd._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'lane \\xff\\xfe ok')"])
        self.assertTrue(out.startswith("lane ") and out.endswith(" ok"), out)


class LiveState(unittest.TestCase):
    """The old env-gated RealState test ran the digest against the real roadmap and skipped everywhere else. Its asserts now run twice:
    on invented text of the real shape here (every machine, every run), and on the live text inside hub_digest.py itself, which fails
    loudly and files an alert with the hub when the live roadmap stops parsing."""

    def page(self, body=ROADMAP):
        return hd.render(hd.build_sections(roadmap_body=body, lane_status=LANE_STATUS, lanes_dir=fresh_dir(self), inbox_text=INBOX,
                                           git_log="abc1234 16 10:00 Merge a thing", tidy_text=TIDY,
                                           budget="Week resets Wed: used 1%", now=NOW), NOW, "NX-01", hd.CAP_DEFAULT)

    def test_a_page_from_roadmap_shaped_text_is_small_complete_and_clean(self):
        page = self.page()
        self.assertLess(len(page.encode()), hd.PAGE_HARD_CAP)
        for h in hd.PAGE_HEADINGS:
            self.assertIn(h, page)
        self.assertIn("Next session should:", page)
        self.assertNotIn("roadmap unavailable", page)
        for rx in hd.SECRETS:
            self.assertIsNone(rx.search(page), rx.pattern)
        self.assertEqual(hd.live_problems(ROADMAP, page), [])

    def test_live_problems_names_each_way_the_roadmap_can_stop_parsing(self):
        cases = {
            "": "empty",
            ROADMAP.replace("### Next session should", "### Next up"): "Next session should",
            ROADMAP.replace("<summary><b>Arc 1", "<summary><b>Phase 1"): "Arc summary",
            ROADMAP.replace("<summary><b>Next 3 sessions</b>", "<summary><b>Coming sessions</b>"): "Next 3 sessions",
        }
        for body, word in cases.items():
            probs = hd.live_problems(body, self.page(body))
            self.assertTrue(any(word in x for x in probs), (word, probs))

    def test_live_problems_catches_an_oversized_page_and_a_missing_section(self):
        page = self.page()
        self.assertTrue(any("over the" in x for x in hd.live_problems(ROADMAP, page + "x" * 7000)))
        self.assertTrue(any("## Tidy report" in x for x in hd.live_problems(ROADMAP, page.replace("## Tidy report", "## Tidy"))))


class LiveAlert(unittest.TestCase):
    def run_digest(self, d, roadmap, *extra):
        (d / "roadmap.md").write_text(roadmap)
        for name, text in (("lanes.txt", LANE_STATUS), ("inbox.md", INBOX), ("tidy.txt", TIDY), ("budget.txt", "Week resets Wed: used 1%\n")):
            (d / name).write_text(text)
        (d / "lanes").mkdir(exist_ok=True)
        return subprocess.run([sys.executable, str(HERE.parent / "hub_digest.py"), "--out", str(d / "out" / "hub-digest.md"),
                               "--roadmap-file", str(d / "roadmap.md"), "--lane-status-file", str(d / "lanes.txt"),
                               "--inbox", str(d / "inbox.md"), "--tidy-file", str(d / "tidy.txt"), "--budget-file", str(d / "budget.txt"),
                               "--lanes-dir", str(d / "lanes"), "--git-repo", str(d), "--open-questions-file", str(d / "nq.txt"),
                               "--alert-state", str(d / "state" / "openclaw-dispatch.json"), "--now", "2026-10-16T21:00:00", *extra],
                              capture_output=True, text=True)

    def test_a_broken_roadmap_fails_loudly_keeps_the_previous_page_and_wakes_the_hub_once(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "nq.txt").write_text("none\n")
            good = self.run_digest(d, ROADMAP, "--strict")
            self.assertEqual(good.returncode, 0, good.stderr)
            before = (d / "out" / "hub-digest.md").read_text()
            self.assertFalse((d / "state" / "hub-inbox.md").exists())

            broken = ROADMAP.replace("### Next session should", "### Next up")
            bad = self.run_digest(d, broken, "--strict")
            self.assertEqual(bad.returncode, 3, bad.stderr)
            self.assertIn("the live state does not parse", bad.stderr)
            self.assertIn("Next session should", bad.stderr)
            self.assertEqual((d / "out" / "hub-digest.md").read_text(), before)  # the previous page stays
            inbox = (d / "state" / "hub-inbox.md").read_text()
            self.assertEqual(inbox.count("- [ ] "), 1)
            self.assertIn("ALERT from hub_digest.py", inbox)
            self.assertTrue((d / "state" / "openclaw-ask.wake").exists())  # what lane_watch.sh wakes the hub on
            self.assertIn("oc-", (d / "state" / "openclaw-dispatch.json").read_text())

            again = self.run_digest(d, broken, "--strict")  # the next 15-minute run
            self.assertEqual(again.returncode, 3)
            self.assertEqual((d / "state" / "hub-inbox.md").read_text().count("- [ ] "), 1)  # one alert a day, not one a run

    def test_a_hermetic_build_is_not_checked_unless_asked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "nq.txt").write_text("none\n")
            r = self.run_digest(d, "no roadmap shape here at all")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((d / "state").exists())

    def test_the_refresh_script_keeps_the_digests_stderr(self):
        line = next(x for x in (HERE.parent / "openclaw_status_refresh.sh").read_text().splitlines() if "timeout" in x and "hub_digest.py" in x)
        self.assertNotIn("2>/dev/null", line)


if __name__ == "__main__":
    unittest.main()
