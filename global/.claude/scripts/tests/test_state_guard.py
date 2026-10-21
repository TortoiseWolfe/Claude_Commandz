#!/usr/bin/env python3
"""The guard that keeps every test run off the live ~/.claude/state (19 Oct 2026, 5:48 PM: a run left two fake posts in his chat
outbox and 168 fake vsock errors in the live inject log)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_guard  # noqa: E402
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402


class Guard(unittest.TestCase):
    def refused(self, fn):
        before = len(state_guard.tried)
        with self.assertRaises(PermissionError):
            fn()
        self.assertEqual(len(state_guard.tried), before + 1)
        del state_guard.tried[before:]  # this probe was meant to be refused; the run must not fail for it

    def test_a_write_to_the_live_state_is_refused(self):
        probe = os.path.join(state_guard.LIVE, "state-guard-probe-%d" % os.getpid())
        try:
            self.refused(lambda: open(probe, "a").close())
            self.refused(lambda: Path(probe).write_text("x"))
            src = os.path.join(fresh_dir(self), "src")
            open(src, "w").close()
            self.refused(lambda: os.replace(src, probe))
            self.refused(lambda: os.remove(probe))
        finally:
            if os.path.exists(probe):  # only if the guard failed; never left behind
                self.fail("the guard let a test create %s" % probe)

    def test_reading_and_temp_writes_are_allowed(self):
        with tempfile.NamedTemporaryFile("w") as f:
            f.write("ok")
        self.assertFalse(state_guard._live(tempfile.gettempdir()))
        self.assertTrue(state_guard._live(os.path.join(state_guard.LIVE, "openclaw-chat-queue.json")))

    def test_the_guard_is_on_for_the_openclaw_suites(self):
        self.assertTrue(getattr(sys, "_state_guard_on", False))
        src = (Path(td.__file__)).read_text()
        self.assertLess(src.index("import state_guard"), src.index("import openclaw_dispatch"))


class Redirected(td.Base):
    def test_every_state_path_points_at_the_test_folder(self):
        live = Path(state_guard.LIVE)
        for p in (dp.STATE, pk.STATE, dp.PASS_FLAG, dp.RESULTS_LOG, dp.LANE_INDEX):
            self.assertTrue(str(p).startswith(str(self.tmp)), p)
            self.assertFalse(str(p).startswith(str(live)), p)


if __name__ == "__main__":
    unittest.main()
