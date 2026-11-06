import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


HOOK = Path(__file__).resolve().parents[1] / "lane-watch-guard.py"


class LaneWatchGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.hub = self.root / "hub"
        self.hub.mkdir()
        self.other = self.root / "other"
        self.other.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_hook(self, stdin=None, *, cwd=None, lanes="a,b", running="0", lane_env=False, **payload):
        env = {key: value for key, value in os.environ.items() if key != "AFA_LANE"}
        env["LANE_WATCH_GUARD_HUB"] = str(self.hub)
        env["LANE_WATCH_GUARD_LANES"] = lanes
        env["LANE_WATCH_GUARD_RUNNING"] = running
        env["AFA_STATE_DIR"] = str(self.state)
        if lane_env:
            env["AFA_LANE"] = "1"
        if stdin is None:
            stdin = json.dumps({"cwd": str(cwd or self.hub), **payload})
        result = subprocess.run(
            [sys.executable, str(HOOK)], input=stdin, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def test_open_lanes_and_no_watcher_blocks(self):
        out = self.run_hook()
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("2 lane(s) open (a, b)", data["reason"])
        self.assertIn("lane_watch.sh", data["reason"])

    def test_watcher_running_is_quiet(self):
        self.assertEqual(self.run_hook(running="1"), "")

    def test_no_lanes_is_quiet(self):
        self.assertEqual(self.run_hook(lanes=""), "")

    def test_other_folder_is_quiet(self):
        self.assertEqual(self.run_hook(cwd=self.other), "")

    def test_lane_session_is_quiet(self):
        self.assertEqual(self.run_hook(lane_env=True), "")

    def test_stop_hook_active_is_quiet(self):
        self.assertEqual(self.run_hook(stop_hook_active=True), "")

    def test_malformed_stdin_is_quiet(self):
        self.assertEqual(self.run_hook("not json"), "")

    def test_lane_names_are_capped_at_six(self):
        data = json.loads(self.run_hook(lanes="a,b,c,d,e,f,g,h"))
        self.assertIn("8 lane(s) open (a, b, c, d, e, f)", data["reason"])

    def handoff(self, age_minutes):
        path = self.state / "hub-handoff-2026-11-04-1500.md"
        path.write_text("hand-off", encoding="utf-8")
        old = time.time() - age_minutes * 60
        os.utime(path, (old, old))

    def marker(self, session="hub-1"):
        (self.state / f"hub-handed-off.{session}").write_text("", encoding="utf-8")

    def test_marker_without_handoff_file_still_blocks(self):
        self.marker()
        self.assertEqual(json.loads(self.run_hook(session_id="hub-1"))["decision"], "block")

    def test_handoff_file_without_marker_still_blocks(self):
        self.handoff(1)
        self.assertEqual(json.loads(self.run_hook(session_id="hub-1"))["decision"], "block")

    def test_marker_with_31_minute_old_handoff_still_blocks(self):
        self.marker()
        self.handoff(31)
        self.assertEqual(json.loads(self.run_hook(session_id="hub-1"))["decision"], "block")

    def test_marker_with_fresh_handoff_allows_stop(self):
        self.marker()
        self.handoff(5)
        self.assertEqual(self.run_hook(session_id="hub-1"), "")

    def test_marker_for_another_session_does_not_count(self):
        self.marker("other-session")
        self.handoff(5)
        self.assertEqual(json.loads(self.run_hook(session_id="hub-1"))["decision"], "block")

    def test_missing_session_id_does_not_match_a_marker_named_none(self):
        self.marker("None")
        self.handoff(5)
        self.assertEqual(json.loads(self.run_hook())["decision"], "block")

    def test_block_names_this_sessions_marker_when_handoff_is_fresh_but_marker_missing(self):
        self.marker("other-session")
        self.handoff(5)
        reason = json.loads(self.run_hook(session_id="hub-1"))["reason"]
        self.assertIn(f"touch {self.state}/hub-handed-off.hub-1", reason)
        self.assertIn("lane_watch.sh", reason)

    def test_block_without_a_fresh_handoff_does_not_offer_the_marker(self):
        self.handoff(45)
        self.assertNotIn("hub-handed-off", json.loads(self.run_hook(session_id="hub-1"))["reason"])

    def test_the_named_marker_then_allows_the_stop(self):
        self.handoff(5)
        self.marker("hub-1")
        self.assertEqual(self.run_hook(session_id="hub-1"), "")


if __name__ == "__main__":
    unittest.main()
