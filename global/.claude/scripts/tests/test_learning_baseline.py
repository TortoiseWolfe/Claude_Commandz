import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import learning_baseline as lb  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

ET = ZoneInfo("America/New_York")


def ts(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=ET).timestamp())


class T(unittest.TestCase):
    def setUp(self):
        self.d = fresh_dir(self)
        self.db = os.path.join(self.d, "l.db")
        self.out = os.path.join(self.d, "learning", "b.jsonl")
        con = sqlite3.connect(self.db)
        con.execute("""CREATE TABLE tokens(message_id TEXT PRIMARY KEY, ts INTEGER, session TEXT, agent_id TEXT,
          repo TEXT, model TEXT, sidechain INTEGER, input INTEGER, output INTEGER, cache_5m INTEGER,
          cache_1h INTEGER, cache_read INTEGER) WITHOUT ROWID""")
        rows = [  # week of Wed 7 Oct 2026 12:00 to Wed 14 Oct 12:00
            ("a", ts("2026-10-07T12:00:00"), 0, 100, 0, 0, 0),          # first instant is in
            ("b", ts("2026-10-08T09:00:00"), 0, 50_000, 0, 0, 100_000),  # 150K
            ("c", ts("2026-10-09T09:00:00"), 0, 10, 100_000, 100_000, 100_000),  # over 200K
            ("d", ts("2026-10-09T10:00:00"), 1, 900_000, 0, 0, 0),      # sidechain, ignored
            ("e", ts("2026-10-14T12:00:00"), 0, 999_999, 0, 0, 0),      # next week
            ("f", ts("2026-10-07T11:59:59"), 0, 999_999, 0, 0, 0),      # last week
        ]
        for i, (m, t, sc, inp, c5, c1, cr) in enumerate(rows):
            con.execute("INSERT INTO tokens VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (m, t, "s", None, "r", "x", sc, inp, 0, c5, c1, cr))
        con.commit()
        con.close()
        self.now = ts("2026-10-15T09:00:00")

    def test_last_finished_week(self):
        s, e = lb.last_finished_week(self.now)
        self.assertEqual(s.strftime("%Y-%m-%d"), "2026-10-07")
        self.assertEqual(e.strftime("%Y-%m-%d"), "2026-10-14")

    def test_week_boundary_is_wednesday_noon(self):
        s, _ = lb.week_bounds(ts("2026-10-14T11:59:59"))
        self.assertEqual(s.strftime("%Y-%m-%d"), "2026-10-07")
        s, _ = lb.week_bounds(ts("2026-10-14T12:00:00"))
        self.assertEqual(s.strftime("%Y-%m-%d"), "2026-10-14")

    def test_m1_counts_main_thread_only_inside_the_window(self):
        s, e = lb.last_finished_week(self.now)
        m = lb.m1(self.db, s, e)
        self.assertEqual(m["turns"], 3)
        self.assertEqual(m["median"], 150_000)
        self.assertEqual(m["over_200k"], 1)

    def test_observe_writes_once(self):
        r = lb.observe(now_ts=self.now, db=self.db, out=self.out, report="")
        self.assertEqual(r["week"], "2026-10-07")
        self.assertEqual(r["m1"]["turns"], 3)
        self.assertIsNone(r["m2"])
        self.assertIsNone(lb.observe(now_ts=self.now, db=self.db, out=self.out, report=""))
        self.assertEqual(len(open(self.out).read().splitlines()), 1)
        self.assertEqual(os.stat(self.out).st_mode & 0o777, 0o600)

    def test_missing_db_gives_null_not_a_crash(self):
        r = lb.observe(now_ts=self.now, db=os.path.join(self.d, "none.db"), out=self.out, report="")
        self.assertIsNone(r["m1"])

    def test_hub_report_fields(self):
        fake = os.path.join(self.d, "fake_report.py")
        with open(fake, "w") as f:
            f.write("import json\nprint(json.dumps({'context_load':{'report_window':{'compact':{'count':43,'auto':39,'manual':4}}},"
                    "'director':{'by_tier_reviewer':{'haiku|opus':[14,13],'sonnet|opus':[19,18]}}}))\n")
        r = lb.observe(now_ts=self.now, db=self.db, out=self.out, report=fake)
        self.assertEqual(r["m2"]["count"], 43)
        self.assertEqual(r["m3"]["reviewed"], 33)
        self.assertEqual(r["m3"]["first_pass_ok"], 31)

    def test_failing_hub_report_gives_null(self):
        bad = os.path.join(self.d, "bad.py")
        with open(bad, "w") as f:
            f.write("import sys\nsys.exit(3)\n")
        r = lb.observe(now_ts=self.now, db=self.db, out=self.out, report=bad)
        self.assertIsNone(r["m2"])
        self.assertIsNone(r["m3"])

    def test_record_holds_no_text(self):
        r = lb.observe(now_ts=self.now, db=self.db, out=self.out, report="")
        self.assertEqual(set(r), {"week", "end", "m1", "m2", "m3", "written"})
        json.dumps(r)


if __name__ == "__main__":
    unittest.main()
