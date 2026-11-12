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

    def run_hook(self, stdin=None, *, cwd=None, lanes="a,b", running="0", lane_env=False, hub_pid="", **payload):
        env = {key: value for key, value in os.environ.items() if key != "AFA_LANE"}
        env["LANE_WATCH_GUARD_HUB"] = str(self.hub)
        env["LANE_WATCH_GUARD_LANES"] = lanes
        env["LANE_WATCH_GUARD_RUNNING"] = running
        # Empty means "this hook found no Claude Code process above it", so the tests read the same under any runner.
        # hub_pid=None leaves the hook to look for itself.
        env.pop("LANE_WATCH_GUARD_HUB_PID", None)
        if hub_pid is not None:
            env["LANE_WATCH_GUARD_HUB_PID"] = str(hub_pid)
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

    def write_pid_file(self, pid, start=None):
        pid_file = self.state / "lanes" / ".lane_watch.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        content = str(pid) + (f" {start}" if start is not None else "")
        pid_file.write_text(content + "\n", encoding="utf-8")

    def test_pid_file_from_another_session_allows_stop(self):
        """A watcher started before /clear writes its pid; the new session must still find it."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            stat = Path(f"/proc/{proc.pid}/stat").read_text(encoding="utf-8")
            start = stat.rsplit(") ", 1)[-1].split()[19]
            self.write_pid_file(proc.pid, start)
            self.assertEqual(self.run_hook(running="0", session_id="new-session"), "")
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_pid_file_with_dead_pid_still_blocks(self):
        self.write_pid_file(999999)
        data = json.loads(self.run_hook(running="0", session_id="hub-1"))
        self.assertEqual(data["decision"], "block")

    def test_recycled_pid_is_not_a_watcher(self):
        self.write_pid_file(os.getpid(), start="0")
        data = json.loads(self.run_hook(running="0", session_id="hub-1"))
        self.assertEqual(data["decision"], "block")

    # A watcher wakes the hub only as a child of the hub's own process. The stand-ins below are sleeping processes:
    # "child" is started by this test process, and "stranger" is a second one that is nobody's parent.

    def sleeper(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        return proc

    def start_of(self, pid):
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[-1].split()[19]

    def test_a_watcher_that_is_a_child_of_this_session_allows_the_stop(self):
        child = self.sleeper()
        self.write_pid_file(child.pid, self.start_of(child.pid))
        self.assertEqual(self.run_hook(hub_pid=os.getpid()), "")

    def test_a_watcher_two_levels_below_this_session_allows_the_stop(self):
        """The real chain has a wrapper between the session and the watcher."""
        code = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(p.pid, flush=True)\n"
            "try:\n    time.sleep(30)\nfinally:\n    p.terminate()\n"
        )
        wrapper = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.addCleanup(wrapper.wait, 10)
        self.addCleanup(wrapper.terminate)
        grandchild = int(wrapper.stdout.readline())
        self.addCleanup(wrapper.stdout.close)
        self.addCleanup(lambda: subprocess.run(["kill", str(grandchild)], capture_output=True))
        self.write_pid_file(grandchild, self.start_of(grandchild))
        self.assertEqual(self.run_hook(hub_pid=os.getpid()), "")

    def test_a_live_watcher_that_is_not_a_child_of_this_session_blocks_and_is_named(self):
        watcher = self.sleeper()
        stranger = self.sleeper()
        self.write_pid_file(watcher.pid, self.start_of(watcher.pid))
        data = json.loads(self.run_hook(hub_pid=stranger.pid))
        self.assertEqual(data["decision"], "block")
        self.assertIn("started detached", data["reason"])
        self.assertIn(f"kill {watcher.pid}", data["reason"])
        self.assertIn("run_in_background", data["reason"])
        self.assertNotIn("no lane_watch.sh is running", data["reason"])

    def test_a_detached_watcher_is_not_saved_by_the_process_listing_answer(self):
        """The pid file's watcher cannot wake this session; a listing that says "none" must not turn that into quiet."""
        watcher = self.sleeper()
        stranger = self.sleeper()
        self.write_pid_file(watcher.pid, self.start_of(watcher.pid))
        data = json.loads(self.run_hook(running="0", hub_pid=stranger.pid))
        self.assertEqual(data["decision"], "block")

    def test_another_watcher_that_can_wake_this_session_still_allows_the_stop(self):
        """The pid file names a detached watcher, and the listing finds a proper one beside it."""
        watcher = self.sleeper()
        stranger = self.sleeper()
        self.write_pid_file(watcher.pid, self.start_of(watcher.pid))
        self.assertEqual(self.run_hook(running="1", hub_pid=stranger.pid), "")

    def test_no_session_process_found_keeps_the_older_answer(self):
        """Under a runner with no Claude Code process above the hook, a live watcher counts as before."""
        watcher = self.sleeper()
        self.write_pid_file(watcher.pid, self.start_of(watcher.pid))
        self.assertEqual(self.run_hook(hub_pid=""), "")

    def test_a_session_process_that_has_gone_blocks(self):
        """A watcher left over from a session whose process ended has nobody to wake."""
        watcher = self.sleeper()
        self.write_pid_file(watcher.pid, self.start_of(watcher.pid))
        data = json.loads(self.run_hook(hub_pid=999999))
        self.assertEqual(data["decision"], "block")
        self.assertIn("started detached", data["reason"])

    def test_a_detached_watcher_does_not_block_once_the_session_has_handed_off(self):
        watcher = self.sleeper()
        stranger = self.sleeper()
        self.write_pid_file(watcher.pid, self.start_of(watcher.pid))
        self.handoff(5)
        self.marker("hub-1")
        self.assertEqual(self.run_hook(hub_pid=stranger.pid, session_id="hub-1"), "")

    def test_with_no_watcher_at_all_the_older_message_stands(self):
        data = json.loads(self.run_hook(hub_pid=os.getpid()))
        self.assertIn("no lane_watch.sh is running", data["reason"])
        self.assertNotIn("started detached", data["reason"])

    def test_the_hook_finds_its_own_session_by_process_name(self):
        """Run through a stand-in named like the session's process: a watcher beside the hook counts, a stranger's does not."""
        self.assertEqual(self.in_a_stand_in_session("claude", watcher_inside=True), "")
        stranger = self.sleeper()
        self.write_pid_file(stranger.pid, self.start_of(stranger.pid))
        reason = json.loads(self.in_a_stand_in_session("claude", watcher_inside=False))["reason"]
        self.assertIn(f"kill {stranger.pid}", reason)

    def test_the_other_name_the_session_process_can_carry_is_found_too(self):
        self.assertEqual(self.in_a_stand_in_session("node", watcher_inside=True), "")
        stranger = self.sleeper()
        self.write_pid_file(stranger.pid, self.start_of(stranger.pid))
        reason = json.loads(self.in_a_stand_in_session("node", watcher_inside=False))["reason"]
        self.assertIn(f"kill {stranger.pid}", reason)

    def in_a_stand_in_session(self, name, *, watcher_inside):
        """Run the hook under a process that carries the given name, as the session's own process does.

        The stand-in renames itself by writing /proc/self/comm (the short name the hook reads), then starts the hook. With
        watcher_inside it also starts a sleeping child of its own and writes that pid to the pid file. The stand-in is the
        nearest such name above the hook, so the answer is the same whether or not a real session sits above the test runner.
        """
        pid_file = self.state / "lanes" / ".lane_watch.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        code = (
            "import subprocess, sys\n"
            "open('/proc/self/comm', 'w').write(sys.argv[4])\n"
            "mine = None\n"
            "if sys.argv[5] == '1':\n"
            "    mine = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "    open(sys.argv[3], 'w').write(str(mine.pid) + '\\n')\n"
            "try:\n"
            "    r = subprocess.run([sys.executable, sys.argv[1]], input=sys.argv[2], capture_output=True, text=True, timeout=30)\n"
            "    sys.stdout.write(r.stdout)\n"
            "finally:\n"
            "    if mine is not None:\n"
            "        mine.terminate(); mine.wait()\n"
        )
        env = {key: value for key, value in os.environ.items() if key not in ("AFA_LANE", "LANE_WATCH_GUARD_HUB_PID")}
        env.update({
            "LANE_WATCH_GUARD_HUB": str(self.hub), "LANE_WATCH_GUARD_LANES": "a,b",
            "LANE_WATCH_GUARD_RUNNING": "0", "AFA_STATE_DIR": str(self.state),
        })
        result = subprocess.run(
            [sys.executable, "-c", code, str(HOOK), json.dumps({"cwd": str(self.hub)}), str(pid_file), name,
             "1" if watcher_inside else "0"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


if __name__ == "__main__":
    unittest.main()
