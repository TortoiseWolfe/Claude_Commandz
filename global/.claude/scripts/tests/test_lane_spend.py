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
    proj = h / ".claude/projects"
    dir_a = proj / ul.slugify("/r/one")
    dir_a.mkdir(parents=True)
    (dir_a / "sid-b2.jsonl").write_text(json.dumps({"type": "user", "message": {"content":
        "Hub: your context was cleared. Read %s/prompt.md, then handoff.md. Your hand-off folder is %s" % (
            str(lanes / "2026-10-21-lane-b"), str(lanes / "2026-10-21-lane-b"))}}) + "\n")
    (dir_a / "sid-hub-mimic.jsonl").write_text(json.dumps({"type": "user", "message": {"content":
        "Your hand-off folder is %s" % str(lanes / "2026-10-21-lane-a")}}) + "\n")
    (dir_a / "sid-other.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "just some other talk"}}) + "\n")

    # Hub sessions: in the ~/repos project folder
    hub_dir = proj / ul.slugify(str(h / "repos"))
    hub_dir.mkdir(parents=True)
    (hub_dir / "sid-hub.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "just the hub talking"}}) + "\n")
    (hub_dir / "sid-hub-quoted.jsonl").write_text(json.dumps({"type": "user", "message": {"content":
        "rule: use %s" % str(lanes / "2026-10-21-lane-a")}}) + "\n")
    (hub_dir / "sid-lane-in-hub.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "lane session started in hub"}}) + "\n")

    # Session map
    sessions = lanes / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "lane-b").write_text("sid-lane-in-hub\t1729512000\t-\n")

    env = ul.Env(str(h))
    conn = ul.connect(env)

    def tok(mid, ts, sid, model, o):
        conn.execute("INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (mid, ts, sid, None, "r", model, 0, 0, o, 0, 0, 0))
    tok("m1", NOON, "sid-a", "claude-fable-5-1", 1_000_000)       # $50
    tok("m2", NOON, "sid-b1", "claude-sonnet-5-5", 1_000_000)     # $10
    tok("m3", NOON + 60, "sid-b2", "claude-sonnet-5-5", 500_000)  # $5, after the /clear
    tok("m4", NOON, "sid-hub", "claude-opus-5-5", 1_000_000)      # $20, hub
    tok("m5", NOON, "sid-hub-quoted", "claude-opus-5-5", 500_000) # $10, hub (quotes lane-a but in hub project)
    tok("m6", NOON, "sid-lane-in-hub", "claude-sonnet-5-5", 100_000) # $1, lane-b via sessions map
    tok("m7", NOON, "sid-other", "claude-opus-5-5", 100_000)      # $2, other
    tok("m8", YESTERDAY, "sid-a", "claude-fable-5-1", 1_000_000)  # yesterday: not today
    conn.commit()
    conn.close()
    return str(h)


class LaneSpend(unittest.TestCase):
    def setUp(self):
        self.h = build_home(self)

    def spend(self, **kw):
        return ls.lane_spend(DAY, ingest=False, h=self.h, **kw)

    def test_each_lane_is_priced_from_its_tokens_today_most_expensive_first(self):
        lanes, other, hub = self.spend()
        # lane-a: sid-a $50
        # lane-b: sid-b1 $10, sid-b2 $5, sid-lane-in-hub $1 = $16
        self.assertEqual([(n, round(u, 2)) for n, u, _f in lanes], [("lane-a", 50.0), ("lane-b", 16.0)])
        self.assertEqual(lanes[0][2], {"fable": 50.0})

    def test_a_session_after_a_clear_is_matched_to_its_lane_by_the_handoff_folder(self):
        lanes, _other, _hub = self.spend()
        self.assertAlmostEqual(dict((n, u) for n, u, _f in lanes)["lane-b"], 16.0)

    def test_spend_outside_any_lane_is_counted_apart_and_yesterday_is_left_out(self):
        lanes, other, hub = self.spend()
        self.assertAlmostEqual(other, 2.0)  # sid-other $2
        self.assertAlmostEqual(sum(hub.values()), 30.0) # sid-hub $20 + sid-hub-quoted $10
        self.assertAlmostEqual(sum(u for _n, u, _f in lanes) + other + sum(hub.values()), 98.0)

    def test_hub_sessions_are_counted_as_hub_not_lane_even_if_quoting_lane_folder(self):
        _lanes, _other, hub = self.spend()
        self.assertAlmostEqual(sum(hub.values()), 30.0)

    def test_a_transcript_that_quotes_no_lane_folder_belongs_to_no_lane(self):
        proj_dir = os.path.join(self.h, ".claude/projects", ul.slugify("/r/one"))
        self.assertIsNone(ls.lane_of_transcript(os.path.join(proj_dir, "sid-other.jsonl")))
        self.assertIsNone(ls.lane_of_transcript("/nonexistent/x.jsonl"))

    def test_no_ledger_is_no_spend_not_a_crash(self):
        lanes, other, hub = ls.lane_spend(DAY, ingest=False, h=fresh_dir(self))
        self.assertEqual((lanes, other, hub), ([], 0.0, {}))


WEEK = NOON - 2 * 86400   # the weekly reset in the week fixture: two days before NOON


def build_week_home(test):
    """A home for the hub's week: one session the hub typed into, a scripted run, a lane that started in ~/repos (same ledger repo,
    'repos (hub)'), a hub session that began before the reset, and spend elsewhere. Every figure is chosen to be easy to add up."""
    h = Path(fresh_dir(test))
    lanes = h / ".claude/state/lanes"
    (lanes / "sessions").mkdir(parents=True)
    (lanes / "index.tsv").write_text("\t".join(("2026-10-19", "lane-a", "/r/one", "fable", "subscription", "-", "-", "sid-lane-a", "1",
                                                  "-", "-", "")) + "\n")
    (lanes / "sessions" / "lane-b").write_text("sid-lane-in-hub\t1729512000\t-\n")
    proj = h / ".claude/projects"
    hub_dir = proj / ul.slugify(str(h / "repos"))
    hub_dir.mkdir(parents=True)
    for sid in ("sid-hub", "sid-scripted", "sid-lane-in-hub", "sid-early", "sid-before"):
        (hub_dir / (sid + ".jsonl")).write_text("{}\n")
    (proj / ul.slugify("/r/one")).mkdir(parents=True)

    env = ul.Env(str(h))
    conn = ul.connect(env)
    n = [0]

    def tok(ts, sid, model, repo="repos (hub)", side=0, i=0, o=0, w5=0, w1=0, r=0):
        n[0] += 1
        conn.execute("INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ("m%d" % n[0], ts, sid, None, repo, model, side, i, o, w5, w1, r))

    def human(ts, sid, kind="human"):
        n[0] += 1
        conn.execute("INSERT INTO activity VALUES (?,?,?,?,?,?)", ("a%d" % n[0], sid, ts, "repos (hub)", kind, 0))

    opus, sonnet, haiku, fable = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"
    # sid-hub: three main-chain turns whose context is 60K, 100K and 140K (cache writes and reads count), a subagent turn that does not
    # claude-opus-5-5 prices (usage_ledger PRICE_OVERRIDES): $4 in, $20 out, $0.20 read, cache writes 1.25x (5m) and 2x (1h) of input
    tok(WEEK + 100, "sid-hub", opus, i=10_000, w5=20_000, r=30_000, o=1_000_000)   # context 60K; $0.04 + 0.10 + 0.006 + 20
    tok(WEEK + 200, "sid-hub", opus, i=1_000, r=99_000)                            # context 100K; $0.0238
    tok(WEEK + 300, "sid-hub", opus, w1=40_000, r=100_000)                         # context 140K; $0.34
    tok(WEEK + 400, "sid-hub", opus, side=1, i=1_000_000)                          # a subagent: $4, but not a turn of the hub's own
    human(WEEK + 50, "sid-hub")
    tok(WEEK + 500, "sid-scripted", haiku, o=1_000_000)                            # $5, nobody typed: the ledger saw only injected prompts
    human(WEEK + 480, "sid-scripted", kind="machine")
    tok(WEEK + 600, "sid-lane-in-hub", sonnet, o=1_000_000)                        # $10, lane-b: not the hub, though typed into
    human(WEEK + 550, "sid-lane-in-hub")
    tok(WEEK - 3600, "sid-early", opus, i=300_000)                                 # before the reset: out of the week, and its first turn
    tok(WEEK + 60, "sid-early", opus, i=200_000)                                   # context 200K, not a first turn; $0.80
    human(WEEK + 30, "sid-early")
    tok(WEEK - 100, "sid-before", opus, o=1_000_000)                               # wholly before the reset
    tok(WEEK + 700, "sid-lane-a", fable, repo="r", o=1_000_000)                    # $50, a lane elsewhere
    tok(WEEK + 800, "sid-other", sonnet, repo="r", o=1_000_000)                    # $10, no lane and no hub
    conn.commit()
    conn.close()
    return str(h)


class HubWeek(unittest.TestCase):
    def setUp(self):
        self.h = build_week_home(self)

    def week(self):
        return ls.hub_week(WEEK, until=NOON + 86400, h=self.h)

    def test_the_hub_counts_its_own_sessions_and_leaves_out_the_lane_that_started_in_the_same_folder(self):
        wk = self.week()
        # sid-hub $24.5098 + sid-scripted $5 + sid-early $0.80 = $30.3098; the lane in ~/repos (sid-lane-in-hub, $10) is not the hub
        self.assertEqual(wk["usd"], 30.31)
        self.assertEqual(wk["total"], 100.31)   # the hub $30.3098 + lane-b $10 + lane-a $50 + no lane or hub $10
        self.assertEqual(wk["share"], 0.3022)

    def test_sessions_are_the_ones_a_person_typed_in_and_the_scripted_run_and_the_lane_are_not(self):
        self.assertEqual(self.week()["sessions"], 2)   # sid-hub, sid-early

    def test_median_context_is_over_main_chain_turns_only(self):
        # contexts 60K, 100K, 140K (sid-hub) and 200K (sid-early) -> 120K; the subagent's 1M-token turn and the pre-reset turn are out
        self.assertEqual(self.week()["median_context"], 120_000)

    def test_first_turn_context_skips_a_session_that_began_before_the_reset(self):
        self.assertEqual(self.week()["first_context"], 60_000)   # only sid-hub began inside the week

    def test_the_week_starts_at_the_reset_and_nothing_before_it_counts(self):
        later = ls.hub_week(WEEK + 450, until=NOON + 86400, h=self.h)   # after sid-hub's turns and its prompt
        self.assertEqual((later["sessions"], later["median_context"], later["first_context"]), (0, None, None))
        self.assertEqual(later["usd"], 5.0)   # only sid-scripted's $5 is left

    def test_no_ledger_is_no_week_not_a_crash(self):
        self.assertIsNone(ls.hub_week(WEEK, h=fresh_dir(self)))

    def test_json_carries_the_week_under_hub_only_when_asked(self):
        env = dict(os.environ, LANE_SPEND_HOME=self.h)
        cmd = [sys.executable, str(HERE.parent / "lane_spend.py"), "--day", DAY, "--no-ingest", "--json"]
        plain = json.loads(subprocess.run(cmd, capture_output=True, text=True, env=env).stdout)
        self.assertNotIn("week", plain["hub"])
        asked = json.loads(subprocess.run(cmd + ["--week-start", str(WEEK)], capture_output=True, text=True, env=env).stdout)
        self.assertEqual((asked["hub"]["week"]["sessions"], asked["hub"]["week"]["median_context"]), (2, 120_000))
        self.assertEqual(asked["hub"]["week"]["since"], WEEK)


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        h = build_home(self)
        return subprocess.run([sys.executable, str(HERE.parent / "lane_spend.py"), "--day", DAY, "--no-ingest", *args],
                              capture_output=True, text=True, env=dict(os.environ, LANE_SPEND_HOME=h))

    def test_text_output_is_tab_separated_with_a_total_line(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["50.00\tlane-a\tfable", "16.00\tlane-b\tsonnet", "hub\t30.00\topus", "total\t98.00\t2.00"])

    def test_over_keeps_only_lanes_at_or_past_the_line_but_the_total_stays_whole_and_hub_is_never_a_lane(self):
        r = self.run_cli("--over", "25")
        self.assertEqual(r.stdout.splitlines(), ["50.00\tlane-a\tfable", "hub\t30.00\topus", "total\t98.00\t2.00"])

    def test_json_and_top(self):
        d = json.loads(self.run_cli("--json", "--top", "1").stdout)
        self.assertEqual([l["name"] for l in d["lanes"]], ["lane-a"])
        self.assertEqual((d["other"], d["total"]), (2.0, 98.0))
        self.assertEqual(d["hub"]["usd"], 30.0)

    def test_a_bad_day_is_refused(self):
        r = subprocess.run([sys.executable, str(HERE.parent / "lane_spend.py"), "--day", "yesterday"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
