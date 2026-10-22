"""internship_status.py: the Jr Dev tracker counts a ticket only the way TechJoy does.
Fixtures are invented (made-up issue numbers, no ledger rows from this machine); nothing here reaches the network:
the gh layer is a function the tests replace."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import internship_status as ist  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

NOW = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
HEADER = "program\trepo\tissue\tpr\tlevel\tlevel_source\tpicked_up\tin_review\tmerged\tloom\treflection\tnotes\n"


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket(issue, level, days, *, start=START, merged=True, files=12, lines=80, repo="Org/app", pr=None, reviewers=()):
    """A ledger row (as read) plus what gh would add, in one tuple."""
    row = {"program": "t", "repo": repo, "issue": str(issue), "pr": str(pr or issue + 1000), "level": level,
           "picked_up": iso(start), "in_review": iso(start + timedelta(days=days)),
           "merged": iso(start + timedelta(days=days + 1)) if merged else ""}
    live = {"changed_files": files, "additions": lines, "deletions": 0, "reviewers": list(reviewers), "base_ref": "main"}
    return row, live


def build(*tickets, now=NOW):
    return [ist.build_ticket(r, live, now) for r, live in tickets]


def others(first=40):
    """Two more in-week tickets (levels low, a week apart each) so only (a), (b) or (d) can fail in a test."""
    return [ticket(first, "LEVEL I", 3, start=START - timedelta(days=9)), ticket(first + 1, "LEVEL I", 3, start=START - timedelta(days=17))]


class OneTicket(unittest.TestCase):
    def test_an_l3_in_review_in_6_days_counts(self):
        c = ist.checklist(build(ticket(10, "LEVEL III", 6)), NOW)
        self.assertTrue(c["a_l3_in_a_week"])
        self.assertEqual(c["unit"], ["app #10"])

    def test_7_days_is_still_in_a_week_and_8_is_not(self):
        self.assertTrue(build(ticket(10, "LEVEL III", 7.0))[0]["in_week"])
        t = build(ticket(10, "LEVEL III", 8))[0]
        self.assertFalse(t["in_week"])
        self.assertFalse(ist.checklist([t], NOW)["a_l3_in_a_week"])

    def test_the_ledger_level_wins_over_the_github_label(self):
        row, live = ticket(10, "LEVEL III", 6)
        live["labels"] = ["LEVEL I"]
        self.assertEqual(ist.build_ticket(row, live, NOW)["level"], 3)
        row["level"] = ""
        self.assertEqual(ist.build_ticket(row, live, NOW)["level"], 1)

    def test_gh_fills_the_gaps_the_ledger_leaves(self):
        row = {"repo": "Org/app", "issue": "10", "pr": "99", "level": "", "picked_up": "", "in_review": "", "merged": ""}
        live = {"labels": ["LEVEL II"], "assigned_at": iso(START), "pr_created": iso(START + timedelta(days=2)),
                "merged_at": iso(START + timedelta(days=3)), "base_ref": "main", "changed_files": 3, "additions": 5, "deletions": 4}
        t = ist.build_ticket(row, live, NOW)
        self.assertEqual((t["level"], t["in_week"], t["merged"], t["files"], t["lines"]), (2, True, True, 3, 9))

    def test_a_pr_merged_into_another_branch_is_not_merged_into_main(self):
        row, live = ticket(10, "LEVEL III", 6)
        live["base_ref"] = "feature/x"
        self.assertFalse(ist.build_ticket(row, live, NOW)["merged"])

    def test_an_unmerged_ticket_waits_from_its_review_date(self):
        t = build(ticket(10, "LEVEL III", 6, start=NOW - timedelta(days=9), merged=False))[0]
        self.assertAlmostEqual(t["wait_days"], 3.0, places=2)
        self.assertIsNone(build(ticket(10, "LEVEL III", 6))[0]["wait_days"])

    def test_level_names_read_every_way_the_ledger_and_github_write_them(self):
        got = [ist.parse_level(x) for x in ("LEVEL 0", "LEVEL I", "LEVEL II", "LEVEL III", "LEVEL IV", "LEVEL V", "L3", "2", "", "bug")]
        self.assertEqual(got, [0, 1, 2, 3, 4, 5, 3, 2, None, None])


class Pairs(unittest.TestCase):
    def test_two_l2s_picked_up_together_count_as_one_l3(self):
        c = ist.checklist(build(ticket(10, "LEVEL II", 5, files=6, lines=40), ticket(11, "LEVEL II", 6, start=START + timedelta(hours=3), files=6, lines=40)), NOW)
        self.assertTrue(c["a_l3_in_a_week"])
        self.assertEqual(c["unit"], ["app #10", "app #11"])
        self.assertEqual((c["d_files"], c["d_lines"]), (12, 80))  # the pair's work is added up

    def test_two_l2s_picked_up_3_days_apart_do_not_pair(self):
        c = ist.checklist(build(ticket(10, "LEVEL II", 5), ticket(11, "LEVEL II", 5, start=START + timedelta(days=3))), NOW)
        self.assertFalse(c["a_l3_in_a_week"])

    def test_a_pair_needs_both_in_a_week(self):
        c = ist.checklist(build(ticket(10, "LEVEL II", 5), ticket(11, "LEVEL II", 9, start=START + timedelta(hours=2))), NOW)
        self.assertFalse(c["a_l3_in_a_week"])

    def test_a_pair_in_two_repos_does_not_pair(self):
        c = ist.checklist(build(ticket(10, "LEVEL II", 5), ticket(11, "LEVEL II", 5, repo="Org/other")), NOW)
        self.assertFalse(c["a_l3_in_a_week"])


class Checklist(unittest.TestCase):
    def test_everything_met(self):
        c = ist.checklist(build(ticket(10, "LEVEL III", 6), *others()), NOW)
        self.assertTrue(all([c["a_l3_in_a_week"], c["b_merged"], c["c_ok"], c["d_ok"]]))
        self.assertEqual(c["c_other_in_week"], 2)

    def test_an_l3_in_a_week_but_unmerged_fails_b_only(self):
        c = ist.checklist(build(ticket(10, "LEVEL III", 6, merged=False), *others()), NOW)
        self.assertEqual((c["a_l3_in_a_week"], c["b_merged"], c["c_ok"], c["d_ok"]), (True, False, True, True))

    def test_thin_work_fails_d(self):
        self.assertFalse(ist.checklist(build(ticket(10, "LEVEL III", 6, files=9), *others()), NOW)["d_ok"])
        self.assertFalse(ist.checklist(build(ticket(10, "LEVEL III", 6, lines=49), *others()), NOW)["d_ok"])

    def test_only_in_week_tickets_count_as_others(self):
        late = ticket(41, "LEVEL I", 9, start=START - timedelta(days=9))
        c = ist.checklist(build(ticket(10, "LEVEL III", 6), others()[0], late), NOW)
        self.assertEqual(c["c_other_in_week"], 1)
        self.assertFalse(c["c_ok"])

    def test_the_merged_unit_is_chosen_over_an_unmerged_one(self):
        c = ist.checklist(build(ticket(10, "LEVEL III", 6, merged=False, files=30), ticket(11, "LEVEL III", 5, merged=True)), NOW)
        self.assertEqual(c["unit"], ["app #11"])
        self.assertTrue(c["b_merged"])

    def test_weeks_of_consistency(self):
        # reviews 1, 8, 15 and 22 days before now: four different weeks
        ts = [ticket(50 + k, "LEVEL I", 2, start=NOW - timedelta(days=7 * k + 3)) for k in range(4)]
        c = ist.checklist(build(*ts), NOW)
        self.assertEqual((c["e_weeks"], c["e_ok"]), (4, True))
        c = ist.checklist(build(*ts[:2]), NOW)
        self.assertEqual((c["e_weeks"], c["e_ok"]), (2, False))


class Output(unittest.TestCase):
    def ledger(self, *tickets):
        d = Path(fresh_dir(self))
        p = d / "tickets.tsv"
        p.write_text(HEADER + "".join("\t".join([r["program"], r["repo"], r["issue"], r["pr"], r["level"], "", r["picked_up"],
                                                 r["in_review"], r["merged"], "", "", "private note"]) + "\n" for r, _ in tickets))
        return str(p), {(r["repo"], r["issue"]): live for r, live in tickets}

    def run_main(self, args, tickets, fetch=None):
        path, lives = self.ledger(*tickets)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ist.main(["--ledger", path] + args, fetch=fetch or (lambda r: lives[(r["repo"], r["issue"])]), now=NOW)
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_gh_failure_prints_the_fallback_and_exits_zero(self):
        def broken(row):
            raise ist.GhError("gh exited 1")
        for args in ([], ["--digest"], ["--json"]):
            self.assertEqual(self.run_main(args, [ticket(10, "LEVEL III", 6)], broken), "internship: gh unavailable\n")

    def test_the_digest_is_at_most_4_lines_and_leaves_out_the_notes(self):
        out = self.run_main(["--digest"], [ticket(10, "LEVEL III", 6), *others(), ticket(60, "LEVEL I", 2, start=NOW - timedelta(days=3), merged=False, pr=77, reviewers=["ki"])])
        self.assertLessEqual(len(out.splitlines()), 4)
        self.assertIn("[x] L3 in a week (app #10)", out)
        self.assertIn("Longest review wait: app #60 PR #77", out)
        self.assertIn("held by ki", out)
        self.assertNotIn("private note", out)

    def test_the_clock_of_an_open_ticket_runs_from_pickup(self):
        row, live = ticket(70, "LEVEL II", 0)
        row.update(pr="", in_review="", merged="", picked_up=iso(NOW - timedelta(days=2.5)))
        out = self.run_main(["--digest"], [(row, live)])
        self.assertIn("Clock: app #70 picked up 2.5 days ago, 4.5 days left", out)

    def test_an_unstarted_pick_shows_as_not_started(self):
        row = {"program": "t", "repo": "Org/app", "issue": "80", "pr": "", "level": "LEVEL II", "picked_up": "", "in_review": "", "merged": ""}
        self.assertIn("Clock: not running; next pick app #80, not started", self.run_main(["--digest"], [(row, {})]))

    def test_json_and_default_report_both_work(self):
        t = [ticket(10, "LEVEL III", 6), *others()]
        self.assertIn('"a_l3_in_a_week": true', self.run_main(["--json"], t))
        out = self.run_main([], t)
        self.assertIn("Tickets:", out)
        self.assertIn("app #10 PR 1010 level 3", out)


class GhLayer(unittest.TestCase):
    def test_fetch_live_reads_labels_assignment_and_pr(self):
        def api(path):
            if path.endswith("/issues/10"):
                return {"labels": [{"name": "LEVEL II"}]}
            if path.endswith("/timeline"):
                return [{"event": "assigned", "assignee": {"login": "someone"}, "created_at": "x"},
                        {"event": "assigned", "assignee": {"login": ist.USER}, "created_at": "2026-10-01T12:00:00Z"}]
            if path.endswith("/reviews"):
                return [{"user": {"login": "ki"}, "state": "CHANGES_REQUESTED"}, {"user": {"login": "ki"}, "state": "APPROVED"}]
            return {"created_at": "c", "merged_at": None, "base": {"ref": "main"}, "requested_reviewers": [{"login": "ki"}],
                    "changed_files": 4, "additions": 3, "deletions": 2}
        live = ist.fetch_live({"repo": "Org/app", "issue": "10", "pr": "99"}, api)
        self.assertEqual((live["labels"], live["assigned_at"], live["reviewers"], live["changed_files"], live["review"]),
                         (["LEVEL II"], "2026-10-01T12:00:00Z", ["ki"], 4, "approved"))

    def test_paginated_output_is_joined_and_a_single_object_stays_one(self):
        self.assertEqual(ist.decode_pages('[{"a": 1}]\n[{"a": 2}]\n'), [{"a": 1}, {"a": 2}])
        self.assertEqual(ist.decode_pages('{"a": 1}'), {"a": 1})
        self.assertEqual(ist.decode_pages("[]"), [])

    def test_a_missing_gh_is_a_gh_error(self):
        import unittest.mock as mock
        with mock.patch.object(ist.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(ist.GhError):
                ist.gh_api("repos/x/y")


if __name__ == "__main__":
    unittest.main()
