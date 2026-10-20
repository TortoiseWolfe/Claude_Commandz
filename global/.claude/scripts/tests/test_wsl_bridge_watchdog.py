"""wsl_bridge_watchdog.py: the 19 Oct 2026 buddyinfo (no free block at order 7 or above) triggers a heal, the healed state does not,
a heal that fails or a probe that fails wakes the hub (alert file), and nothing runs twice inside five minutes."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wsl_bridge_watchdog as wd  # noqa: E402

# /proc/buddyinfo copied from ~/.claude/state/wsl-bridge/2026-10-19-evidence.txt, 8:17 PM, 33 h up: cmd.exe timed out at 10 s.
FAILING = """\
Node 0, zone    DMA32  10488   5205   3188   2747    575     50      2      0      0      0      0
Node 0, zone   Normal  18761  14525   7734   4575   1675    322     67      0      0      0      0
"""
# The same VM after the hub's drop_caches=1 + compact_memory (/proc/buddyinfo at 8:50 PM): cmd.exe answers in 0.03 s.
HEALED = """\
Node 0, zone    DMA32  12889  15542  11341  11843   5760   2583   1296    692    298    113     15
Node 0, zone   Normal  73910 221297 232480 157752  80585  34169  10293   1716    197     41      0
"""
# Compacted but cache still holds the memory: 100 order-7 blocks short of enough.
PARTIAL = """\
Node 0, zone   Normal  26444   9396   6932   3991   1547    317     60     60      0      0      0
"""
LOW_FREE = "MemFree:          917504 kB\nCached:         33554432 kB\n"
HIGH_FREE = "MemFree:        27262976 kB\nCached:          7000000 kB\n"
SYSCTL_OK = {wd.sysctl_path(k): "%d\n" % v for k, v in wd.DESIRED.items()}


class Box:
    """A fake /proc and a fake root: running a script changes what the next read returns."""

    def __init__(self, buddy=FAILING, meminfo=LOW_FREE, sysctl=None, effects=None, root_ok=True):
        self.files = {"/proc/buddyinfo": buddy, "/proc/meminfo": meminfo, **(SYSCTL_OK if sysctl is None else sysctl)}
        self.effects = effects or {}  # substring of the script -> (buddy, meminfo) it leaves behind
        self.root_ok = root_ok
        self.scripts = []

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def root(self, script):
        self.scripts.append(script)
        if not self.root_ok:
            return False, "none"
        for key, (buddy, meminfo) in self.effects.items():
            if key in script:
                self.files["/proc/buddyinfo"], self.files["/proc/meminfo"] = buddy, meminfo
        for k, v in wd.DESIRED.items():
            if "echo %d > %s" % (v, wd.sysctl_path(k)) in script:
                self.files[wd.sysctl_path(k)] = "%d\n" % v
        return True, "docker"


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.sd = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"WSL_BRIDGE_STATE": self.sd})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.t = 1_000_000.0

    def go(self, box, *argv, probe=True, dt=0):
        self.t += dt
        out = io.StringIO()
        with redirect_stdout(out):
            rc = wd.run(list(argv), read=box.read, root=box.root, probe=lambda: probe, now=lambda: self.t)
        return rc, out.getvalue()

    def alert(self):
        p = os.path.join(self.sd, "alert")
        return open(p).read() if os.path.exists(p) else None

    def heal_log(self):
        p = os.path.join(self.sd, "heal.log")
        return open(p).read() if os.path.exists(p) else ""

    # ---- the measurement
    def test_the_19_oct_buddyinfo_has_no_big_block_and_the_healed_one_has_plenty(self):
        self.assertEqual(wd.big_blocks(FAILING), 0)
        self.assertEqual(wd.big_blocks(HEALED), (692 + 298 * 2 + 113 * 4 + 15 * 8) + (1716 + 197 * 2 + 41 * 4))  # an order-8 block counts 2, order-9 4, order-10 8
        self.assertGreaterEqual(wd.big_blocks(HEALED), wd.MIN_BLOCKS)
        self.assertLess(wd.big_blocks(PARTIAL), wd.MIN_BLOCKS)

    def test_parse_reads_every_zone_and_ignores_other_lines(self):
        z = wd.parse_buddyinfo(FAILING + "garbage line\n")
        self.assertEqual(sorted(z), ["0/DMA32", "0/Normal"])
        self.assertEqual(z["0/Normal"][:3], [18761, 14525, 7734])

    # ---- the trigger
    def test_failing_fixture_triggers_a_heal_that_drops_cache_then_compacts(self):
        box = Box(effects={"drop_caches": (HEALED, HIGH_FREE)})
        rc, out = self.go(box)
        self.assertEqual(rc, 0)
        heal_scripts = [s for s in box.scripts if "compact_memory" in s]
        self.assertEqual(len(heal_scripts), 1)  # 900 MB free: compacting alone cannot work, so it goes straight to drop + compact
        self.assertIn("drop_caches", heal_scripts[0])
        self.assertLess(heal_scripts[0].index("drop_caches"), heal_scripts[0].index("compact_memory"))
        self.assertIn("blocks 0->", self.heal_log())
        self.assertIn("steps=drop_caches+compact route=docker", self.heal_log())
        self.assertIn("probe=ok", self.heal_log())
        self.assertIsNone(self.alert())

    def test_healed_state_does_not_heal_log_or_alert(self):
        box = Box(buddy=HEALED, meminfo=HIGH_FREE)
        rc, out = self.go(box)
        self.assertEqual((rc, out, box.scripts, self.heal_log(), self.alert()), (0, "", [], "", None))

    def test_compact_alone_is_tried_first_when_memory_is_free(self):
        box = Box(meminfo=HIGH_FREE, effects={"compact_memory": (HEALED, HIGH_FREE)})
        rc, _ = self.go(box)
        self.assertEqual(rc, 0)
        self.assertEqual(len(box.scripts), 1)
        self.assertNotIn("drop_caches", box.scripts[0])
        self.assertIn("steps=compact ", self.heal_log())

    def test_compact_that_does_not_help_falls_through_to_dropping_cache(self):
        box = Box(meminfo=HIGH_FREE, effects={"echo 1 > /proc/sys/vm/compact_memory": (PARTIAL, HIGH_FREE), "drop_caches": (HEALED, HIGH_FREE)})
        rc, _ = self.go(box)
        self.assertEqual(rc, 0)
        self.assertEqual(len(box.scripts), 2)
        self.assertIn("steps=compact,drop_caches+compact", self.heal_log())

    # ---- failure wakes the hub
    def test_heal_that_leaves_the_blocks_missing_alerts_and_exits_2(self):
        box = Box(effects={"drop_caches": (PARTIAL, LOW_FREE)})
        rc, _ = self.go(box)
        self.assertEqual(rc, 2)
        self.assertIn("wsl bridge: free order-7+ blocks", self.alert())
        self.assertRegex(self.alert(), r"^\d{1,2}:\d{2} [AP]M ")  # Jonathan reads times as 12-hour

    def test_no_root_route_alerts_and_exits_2(self):
        rc, _ = self.go(Box(root_ok=False))
        self.assertEqual(rc, 2)
        self.assertIsNotNone(self.alert())
        self.assertIn("drop_caches+compact-failed", self.heal_log())

    def test_probe_that_still_fails_after_a_heal_alerts(self):
        box = Box(effects={"drop_caches": (HEALED, HIGH_FREE)})
        rc, _ = self.go(box, probe=False)
        self.assertEqual(rc, 2)
        self.assertIn("probe FAILED", self.alert())
        self.assertIn("probe=FAIL", self.heal_log())

    def test_probe_flag_probes_a_healthy_vm_and_alerts_on_a_dead_bridge(self):
        box = Box(buddy=HEALED, meminfo=HIGH_FREE)
        self.assertEqual(self.go(box, "--probe", probe=True)[0], 0)
        self.assertIsNone(self.alert())
        self.assertEqual(self.go(box, "--probe", probe=False)[0], 2)
        self.assertIn("probe FAILED", self.alert())

    def test_a_healthy_vm_without_the_probe_flag_never_runs_the_probe(self):
        box = Box(buddy=HEALED, meminfo=HIGH_FREE)
        with mock.patch.object(wd, "probe_bridge", side_effect=AssertionError("probed")):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(wd.run([], read=box.read, root=box.root, now=lambda: self.t), 0)

    # ---- rate limits
    def test_no_second_heal_inside_five_minutes_and_a_standing_failure_wakes_the_hub_once_per_half_hour(self):
        box = Box(effects={"drop_caches": (PARTIAL, LOW_FREE)})
        self.go(box)
        first_alert = self.alert()
        n = len(box.scripts)
        rc, _ = self.go(box, dt=60)
        self.assertEqual(rc, 2)
        self.assertEqual(len(box.scripts), n)  # skipped
        self.assertIn("heal-skipped", self.heal_log())
        os.utime(os.path.join(self.sd, "alert"), (self.t - 100, self.t - 100))
        mtime = os.stat(os.path.join(self.sd, "alert")).st_mtime
        self.go(box, dt=60)
        self.assertEqual(os.stat(os.path.join(self.sd, "alert")).st_mtime, mtime)  # not rewritten: lane_watch would wake again
        self.go(box, dt=wd.ALERT_GAP_S)
        self.assertGreater(os.stat(os.path.join(self.sd, "alert")).st_mtime, mtime)
        self.assertEqual(first_alert.split(" ", 2)[2][:20], self.alert().split(" ", 2)[2][:20])

    def test_heal_runs_again_after_five_minutes(self):
        box = Box(effects={"drop_caches": (PARTIAL, LOW_FREE)})
        self.go(box)
        n = len(box.scripts)
        self.go(box, dt=wd.HEAL_GAP_S + 1)
        self.assertGreater(len(box.scripts), n)

    # ---- the sysctl reserve
    def test_drifted_sysctl_is_reapplied_and_correct_sysctl_is_left_alone(self):
        drifted = dict(SYSCTL_OK, **{wd.sysctl_path("vm.min_free_kbytes"): "45056\n", wd.sysctl_path("vm.compaction_proactiveness"): "0\n"})
        box = Box(buddy=HEALED, meminfo=HIGH_FREE, sysctl=drifted)
        rc, _ = self.go(box)
        self.assertEqual(rc, 0)
        self.assertEqual(len(box.scripts), 1)
        self.assertIn("echo 1048576 > /proc/sys/vm/min_free_kbytes", box.scripts[0])
        self.assertIn("sysctl-reapplied(docker)", self.heal_log())
        self.go(box, dt=10)
        self.assertEqual(len(box.scripts), 1)  # now matches: nothing more

    def test_a_missing_sysctl_file_counts_as_drift(self):
        self.assertEqual(wd.sysctl_drift(Box(sysctl={}).read), {k: None for k in wd.DESIRED})

    def test_sysctl_conf_carries_every_desired_value(self):
        conf = wd.sysctl_conf()
        for k, v in wd.DESIRED.items():
            self.assertIn("%s = %d" % (k, v), conf)

    # ---- dry run
    def test_dry_run_changes_nothing(self):
        box = Box()
        rc, out = self.go(box, "--dry-run")
        self.assertEqual((rc, box.scripts, self.heal_log(), self.alert()), (0, [], "", None))
        self.assertIn("would-heal", out)
        self.assertFalse(os.path.exists(os.path.join(self.sd, "last-heal")))


if __name__ == "__main__":
    unittest.main()
