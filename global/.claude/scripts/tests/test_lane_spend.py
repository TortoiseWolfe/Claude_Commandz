"""lane_spend.py: what each lane cost today, from the usage ledger. Fixtures are invented; nothing reads this machine's ledger."""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import lane_spend as ls  # noqa: E402
import usage_ledger as ul  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

DAY = "2026-10-21"
NOON = int(time.mktime((2026, 10, 21, 12, 0, 0, 0, 0, -1)))
YESTERDAY = NOON - 86400


def build_home(test):
    """A home with a ledger, a lane index and transcripts: lane-a (one session), lane-b (a session, then a /clear'd one)."""
    h = Path(fresh_dir(test))
    lanes = h / ".claude/state/lanes"
    lanes.mkdir(parents=True)
    rows = [
        ("2026-10-21", "lane-a", "/r/one", "fable", "subscription", "-", "-", "sid-a", "1", "-", "-", ""),
        ("2026-10-21", "lane-b", "/r/one", "sonnet", "subscription", "-", "-", "sid-b1", "2", "-", "-", ""),
        ("2026-10-21", "short", "/r/one"),   # a malformed row is skipped
    ]
    (lanes / "index.tsv").write_text("\n".join("\t".join(r) for r in rows) + "\n")
    proj = h / ".claude/projects" / ul.slugify("/r/one")
    proj.mkdir(parents=True)
    (proj / "sid-b2.jsonl").write_text(json.dumps({"type": "user", "message": {"content":
        "Hub: your context was cleared. Read %s/prompt.md, then handoff.md. Your hand-off folder is %s" % (
            str(lanes / "2026-10-21-lane-b"), str(lanes / "2026-10-21-lane-b"))}}) + "\n")
    (proj / "sid-hub.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "just the hub talking"}}) + "\n")
    env = ul.Env(str(h))
    conn = ul.connect(env)

    def tok(mid, ts, sid, model, o):
        conn.execute("INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (mid, ts, sid, None, "r", model, 0, 0, o, 0, 0, 0))
    tok("m1", NOON, "sid-a", "claude-fable-5-1", 1_000_000)       # $50
    tok("m2", NOON, "sid-b1", "claude-sonnet-5-5", 1_000_000)     # $10
    tok("m3", NOON + 60, "sid-b2", "claude-sonnet-5-5", 500_000)  # $5, after the /clear
    tok("m4", NOON, "sid-hub", "claude-opus-5-5", 1_000_000)      # $20, not a lane
    tok("m5", YESTERDAY, "sid-a", "claude-fable-5-1", 1_000_000)  # yesterday: not today
    conn.commit()
    conn.close()
    return str(h)


class LaneSpend(unittest.TestCase):
    def setUp(self):
        self.h = build_home(self)

    def spend(self, **kw):
        return ls.lane_spend(DAY, ingest=False, h=self.h, **kw)

    def test_each_lane_is_priced_from_its_tokens_today_most_expensive_first(self):
        lanes, other = self.spend()
        self.assertEqual([(n, round(u, 2)) for n, u, _f in lanes], [("lane-a", 50.0), ("lane-b", 15.0)])
        self.assertEqual(lanes[0][2], {"fable": 50.0})

    def test_a_session_after_a_clear_is_matched_to_its_lane_by_the_handoff_folder(self):
        lanes, _other = self.spend()
        self.assertAlmostEqual(dict((n, u) for n, u, _f in lanes)["lane-b"], 15.0)   # sid-b1 $10 + sid-b2 $5

    def test_spend_outside_any_lane_is_counted_apart_and_yesterday_is_left_out(self):
        lanes, other = self.spend()
        self.assertAlmostEqual(other, 20.0)
        self.assertAlmostEqual(sum(u for _n, u, _f in lanes) + other, 85.0)

    def test_a_transcript_that_quotes_no_lane_folder_belongs_to_no_lane(self):
        self.assertIsNone(ls.lane_of_transcript(os.path.join(self.h, ".claude/projects", ul.slugify("/r/one"), "sid-hub.jsonl")))
        self.assertIsNone(ls.lane_of_transcript("/nonexistent/x.jsonl"))

    def test_no_ledger_is_no_spend_not_a_crash(self):
        lanes, other = ls.lane_spend(DAY, ingest=False, h=fresh_dir(self))
        self.assertEqual((lanes, other), ([], 0.0))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        h = build_home(self)
        return subprocess.run([sys.executable, str(HERE.parent / "lane_spend.py"), "--day", DAY, "--no-ingest", *args],
                              capture_output=True, text=True, env=dict(os.environ, LANE_SPEND_HOME=h))

    def test_text_output_is_tab_separated_with_a_total_line(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["50.00\tlane-a\tfable", "15.00\tlane-b\tsonnet", "total\t85.00\t20.00"])

    def test_over_keeps_only_lanes_at_or_past_the_line_but_the_total_stays_whole(self):
        r = self.run_cli("--over", "25")
        self.assertEqual(r.stdout.splitlines(), ["50.00\tlane-a\tfable", "total\t85.00\t20.00"])

    def test_json_and_top(self):
        d = json.loads(self.run_cli("--json", "--top", "1").stdout)
        self.assertEqual([l["name"] for l in d["lanes"]], ["lane-a"])
        self.assertEqual((d["other"], d["total"]), (20.0, 85.0))

    def test_a_bad_day_is_refused(self):
        r = subprocess.run([sys.executable, str(HERE.parent / "lane_spend.py"), "--day", "yesterday"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
