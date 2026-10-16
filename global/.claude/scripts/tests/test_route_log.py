"""route_log.py: records carry tokens only, appends survive a torn line, and the weekly window is the
subscription week (Wednesday 12:00 ET)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import route_log as rl  # noqa: E402


class RouteLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "sub/route-log.jsonl")

    def test_append_and_read_with_mode_600(self):
        rl.append(rl.make_record("p-1", "K-01", "public", [1, 2], channel="tray", ts=1000), self.path)
        rl.append(rl.make_record("p-2", "E-04", "client", escalated=True, claude_started=True, ts=1001), self.path)
        self.assertEqual([r["pack_id"] for r in rl.read(self.path)], ["p-1", "p-2"])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_free_text_is_refused(self):
        for bad in ("a b", "x" * 65, "", "line\nbreak", "p@ss", None):
            with self.subTest(v=bad):
                with self.assertRaises(ValueError):
                    rl.make_record(bad, "K-01", "public")
        with self.assertRaises(ValueError):
            rl.make_record("p", "K-01", "friendly")
        with self.assertRaises(ValueError):
            rl.make_record("p", "K-01", "public", [0])
        with self.assertRaises(ValueError):
            rl.append({"pack_id": "p", "text": "hello"}, self.path)

    def test_torn_line_is_skipped(self):
        rl.append(rl.make_record("p-1", "K-01", "own", ts=5), self.path)
        with open(self.path, "a") as f:
            f.write('{"pack_id": "broken\n')
        rl.append(rl.make_record("p-3", "K-01", "own", ts=6), self.path)
        self.assertEqual([r["pack_id"] for r in rl.read(self.path)], ["p-1", "p-3"])

    def test_window_is_wednesday_noon_et(self):
        et = ZoneInfo("America/New_York")
        tue = datetime(2026, 10, 13, 20, 0, tzinfo=et).timestamp()
        wed_before = datetime(2026, 10, 14, 11, 59, tzinfo=et).timestamp()
        wed_after = datetime(2026, 10, 14, 12, 0, tzinfo=et).timestamp()
        self.assertEqual(rl.window_start(tue), "2026-10-07")
        self.assertEqual(rl.window_start(wed_before), "2026-10-07")
        self.assertEqual(rl.window_start(wed_after), "2026-10-14")

    def test_report_counts(self):
        et = ZoneInfo("America/New_York")
        t = datetime(2026, 10, 15, 9, 0, tzinfo=et).timestamp()
        recs = [rl.make_record("a", "K-01", "public", ts=t), rl.make_record("b", "E-04", "client", escalated=True, claude_started=True, ts=t),
                rl.make_record("c", "K-02", "own", ts=t)]
        (row,) = rl.report(recs)
        self.assertEqual((row["items"], row["kept"], row["escalated"], row["claude_avoided"]), (3, 2, 1, 2))
        self.assertEqual(row["kept_share"], 0.667)

    def test_missing_log_reads_empty(self):
        self.assertEqual(rl.read(os.path.join(self.d, "none.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
