import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_hook(self, stdin=None, *, cwd=None, lanes="a,b", running="0", lane_env=False, **payload):
        env = {key: value for key, value in os.environ.items() if key != "AFA_LANE"}
        env["LANE_WATCH_GUARD_HUB"] = str(self.hub)
        env["LANE_WATCH_GUARD_LANES"] = lanes
        env["LANE_WATCH_GUARD_RUNNING"] = running
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


if __name__ == "__main__":
    unittest.main()
