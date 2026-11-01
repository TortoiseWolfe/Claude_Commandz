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
from tmpdirs import fresh_dir  # noqa: E402

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


def vfs(inodes=10, space=10):
    """A fake os.statvfs result: `inodes` and `space` are the percent used."""
    return lambda path: mock.Mock(f_files=1000, f_ffree=1000 - inodes * 10, f_blocks=1000, f_bfree=1000 - space * 10, f_bavail=1000 - space * 10)


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
        self.sd = fresh_dir(self)
        self.tmp = fresh_dir(self, prefix="faketmp-")  # the fake /tmp: the tests never sweep or scan the real one
        self.statvfs = vfs()
        self.env = mock.patch.dict(os.environ, {"WSL_BRIDGE_STATE": self.sd})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.t = 1_000_000.0

    def go(self, box, *argv, probe=True, dt=0, churn="", statvfs=None):
        self.t += dt
        out = io.StringIO()
        probes = probe if isinstance(probe, list) else [probe] * 5
        with redirect_stdout(out):
            rc = wd.run(list(argv), read=box.read, root=box.root, probe=lambda: probes.pop(0), now=lambda: self.t, churn_of=lambda: churn,
                         statvfs=statvfs or self.statvfs, tmp_dir=self.tmp)
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
    def test_heal_that_leaves_the_blocks_missing_and_the_bridge_down_alerts_and_exits_2(self):
        box = Box(effects={"drop_caches": (PARTIAL, LOW_FREE)})
        rc, _ = self.go(box, probe=False)
        self.assertEqual(rc, 2)
        self.assertIn("NOT answering", self.alert())
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
        self.assertIn("NOT answering", self.alert())
        self.assertIn("probe=FAIL", self.heal_log())

    def test_probe_flag_probes_a_healthy_vm_and_alerts_on_a_dead_bridge(self):
        box = Box(buddy=HEALED, meminfo=HIGH_FREE)
        self.assertEqual(self.go(box, "--probe", probe=True)[0], 0)
        self.assertIsNone(self.alert())
        self.assertEqual(self.go(box, "--probe", probe=False)[0], 2)
        self.assertIn("NOT answering", self.alert())

    def test_a_healthy_vm_without_the_probe_flag_never_runs_the_probe(self):
        box = Box(buddy=HEALED, meminfo=HIGH_FREE)
        with mock.patch.object(wd, "probe_bridge", side_effect=AssertionError("probed")):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(wd.run([], read=box.read, root=box.root, now=lambda: self.t, statvfs=self.statvfs, tmp_dir=self.tmp), 0)

    # ---- rate limits
    def test_a_standing_outage_wakes_the_hub_once_per_half_hour(self):
        box = Box(effects={"drop_caches": (PARTIAL, LOW_FREE)})
        self.go(box, probe=False)
        self.assertIn("NOT answering", self.alert())
        os.utime(os.path.join(self.sd, "alert"), (self.t - 100, self.t - 100))
        mtime = os.stat(os.path.join(self.sd, "alert")).st_mtime
        self.go(box, probe=False, dt=60)
        self.assertEqual(os.stat(os.path.join(self.sd, "alert")).st_mtime, mtime)  # not rewritten: lane_watch would wake again
        self.go(box, probe=False, dt=wd.ALERT_GAP_S)
        self.assertGreater(os.stat(os.path.join(self.sd, "alert")).st_mtime, mtime)

    # ---- 19 Oct 9:27 PM: 7 blocks at one instant, 71 s after a heal, with the bridge still answering
    def test_low_blocks_inside_the_heal_gap_with_the_bridge_answering_do_not_heal_or_wake_the_hub(self):
        box = Box(effects={"drop_caches": (HEALED, HIGH_FREE)})
        self.go(box)
        box.files["/proc/buddyinfo"], box.files["/proc/meminfo"] = FAILING, LOW_FREE  # a scan refilled the cache in 71 s
        n = len(box.scripts)
        rc, out = self.go(box, probe=True, dt=71, churn="pid 160023 244MB/s (lane greg-drafts-check) bfs -S dfs / -iname x")
        self.assertEqual((rc, len(box.scripts), self.alert()), (0, n, None))
        self.assertIn("heal-skipped", out)
        self.assertIn("bridge answers", out)
        self.assertIn("churn=pid 160023 244MB/s (lane greg-drafts-check)", self.heal_log())

    def test_low_blocks_inside_the_heal_gap_with_the_bridge_down_heals_anyway(self):
        box = Box(effects={"drop_caches": (HEALED, HIGH_FREE)})
        self.go(box)
        box.files["/proc/buddyinfo"], box.files["/proc/meminfo"] = FAILING, LOW_FREE
        n = len(box.scripts)
        rc, out = self.go(box, probe=[False, True], dt=71)
        self.assertEqual(rc, 0)
        self.assertGreater(len(box.scripts), n)
        self.assertIn("DOWN: healing anyway", out)
        self.assertIsNone(self.alert())  # the forced heal brought the bridge back

    def test_the_alert_names_what_is_filling_the_cache(self):
        box = Box(effects={"drop_caches": (PARTIAL, LOW_FREE)})
        self.go(box, probe=False, churn="pid 160023 244MB/s (lane greg-drafts-check) bfs -S dfs / -iname x")
        self.assertIn("page-cache churn: pid 160023 244MB/s (lane greg-drafts-check)", self.alert())

    def test_the_same_skipped_state_is_logged_once(self):
        box = Box(effects={"drop_caches": (HEALED, HIGH_FREE)})
        self.go(box)
        box.files["/proc/buddyinfo"], box.files["/proc/meminfo"] = FAILING, LOW_FREE
        self.go(box, dt=71)
        n = len(self.heal_log().splitlines())
        self.go(box, dt=0)
        self.assertEqual(len(self.heal_log().splitlines()), n)

    # ---- who is filling the cache
    def test_busiest_reader_is_the_process_with_the_biggest_read_rate_over_the_floor(self):
        a = {1: 0, 2: 0, 3: 0}
        b = {1: 10 * 1048576, 2: 2000 * 1048576, 3: 5 * 1048576, 4: 9999 * 1048576}  # pid 4 is new: unknown rate
        self.assertEqual(wd.busiest_reader(a, b, 10), (2, 200.0))
        self.assertIsNone(wd.busiest_reader({1: 0}, {1: 100 * 1048576}, 10))  # 10 MB/s is not churn

    def test_describe_churn_names_the_pid_rate_lane_and_command(self):
        snaps = iter([{7: 0}, {7: 600 * 1048576}])
        with mock.patch.object(wd, "proc_cmd", return_value="bfs -S dfs / -iname *x*"), mock.patch.object(wd.time, "sleep"):
            got = wd.describe_churn(lambda: next(snaps), lambda pid: "greg-drafts-check", secs=2)
        self.assertEqual(got, "pid 7 300MB/s (lane greg-drafts-check) bfs -S dfs / -iname *x*")

    def test_describe_churn_redacts_known_secret_before_logging(self):
        value = "A1b2C3d4" + "E5f6G7h8" + "J9k0L1m2"
        with tempfile.TemporaryDirectory() as d:
            repos, secrets = os.path.join(d, "repos"), os.path.join(d, "secrets")
            os.makedirs(repos)
            os.makedirs(secrets)
            with open(os.path.join(secrets, "stream.key"), "w") as fh:
                fh.write(value + "\n")
            env = {"KNOWN_SECRETS_REPOS": repos, "KNOWN_SECRETS_DIR": secrets,
                   "KNOWN_SECRETS_IGNORE": os.path.join(d, "ignore")}
            snaps = iter([{7: 0}, {7: 600 * 1048576}])
            with mock.patch.dict(os.environ, env), mock.patch.object(wd, "proc_cmd", return_value="ffmpeg " + value), \
                    mock.patch.object(wd.time, "sleep"):
                got = wd.describe_churn(lambda: next(snaps), lambda pid: "", secs=2)
        self.assertNotIn(value, got)
        self.assertIn("<redacted:stream.key>", got)

    def test_describe_churn_is_empty_when_nothing_reads_much(self):
        snaps = iter([{7: 0}, {7: 1048576}])
        with mock.patch.object(wd.time, "sleep"):
            self.assertEqual(wd.describe_churn(lambda: next(snaps), lambda pid: "", secs=2), "")

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

    # ---- disk guard: early warning
    HEALTHY = dict(buddy=HEALED, meminfo=HIGH_FREE)

    def test_84_percent_of_inodes_does_not_alert(self):
        self.go(Box(**self.HEALTHY), statvfs=vfs(inodes=84, space=89))
        self.assertIsNone(self.alert())

    def test_86_percent_of_inodes_alerts_with_both_percentages_and_the_tmp_patterns(self):
        for i in range(5):
            os.mkdir(os.path.join(self.tmp, "tmpab%06d" % i))
        for i in range(2):
            os.mkdir(os.path.join(self.tmp, "cc-test-%d" % i))
        os.mkdir(os.path.join(self.tmp, "claude-1000"))
        rc, _ = self.go(Box(**self.HEALTHY), statvfs=vfs(inodes=86, space=40))
        a = self.alert()
        self.assertEqual(rc, 0)
        self.assertIn("inodes 86% used", a)
        self.assertIn("space 40% used", a)
        self.assertIn("tmpXXXXXXXX x5", a)
        self.assertIn("cc-test-* x2", a)
        self.assertIn("claude-* x1", a)
        self.assertIn("inodes 86%", open(os.path.join(self.sd, "disk.log")).read())

    def test_91_percent_of_space_alerts(self):
        self.go(Box(**self.HEALTHY), statvfs=vfs(inodes=10, space=91))
        self.assertIn("space 91% used", self.alert())

    def test_exactly_85_inodes_and_90_space_alert(self):
        self.go(Box(**self.HEALTHY), statvfs=vfs(inodes=85, space=10))
        self.assertIsNotNone(self.alert())
        os.remove(os.path.join(self.sd, "alert"))
        self.go(Box(**self.HEALTHY), statvfs=vfs(inodes=10, space=90))
        self.assertIsNotNone(self.alert())

    def test_a_standing_disk_alert_wakes_the_hub_once_per_half_hour(self):
        box = Box(**self.HEALTHY)
        full = vfs(inodes=90)
        self.go(box, statvfs=full)
        path = os.path.join(self.sd, "alert")
        os.utime(path, (self.t - 100, self.t - 100))
        mtime = os.stat(path).st_mtime
        self.go(box, statvfs=full, dt=60)
        self.assertEqual(os.stat(path).st_mtime, mtime)
        self.go(box, statvfs=full, dt=wd.ALERT_GAP_S)
        self.assertGreater(os.stat(path).st_mtime, mtime)

    def test_the_pattern_scan_is_capped(self):
        for i in range(30):
            os.mkdir(os.path.join(self.tmp, "tmp%08d" % i))
        top, seen, capped = wd.tmp_top_patterns(self.tmp, cap=10)
        self.assertEqual((seen, capped, sum(c for _, c in top)), (10, True, 10))
        self.assertIn("first 10 entries only", wd.disk_line(90, 10, (top, seen, capped)))

    def test_dry_run_prints_the_disk_line_and_does_not_write_an_alert_or_log(self):
        rc, out = self.go(Box(**self.HEALTHY), "--dry-run", statvfs=vfs(inodes=90, space=20))
        self.assertIn("dry-run: disk /: inodes 90% used, space 20% used", out)
        self.assertEqual((self.alert(), os.path.exists(os.path.join(self.sd, "disk.log"))), (None, False))

    # ---- disk guard: the narrow sweep
    def make_dir(self, name, age_h):
        p = os.path.join(self.tmp, name)
        os.mkdir(p)
        open(os.path.join(p, "f"), "w").close()
        t = self.t - age_h * 3600
        os.utime(p, (t, t))
        return p

    def test_only_old_cc_test_folders_are_swept(self):
        old = self.make_dir("cc-test-old", 25)
        new = self.make_dir("cc-test-new", 23)
        other_old = [self.make_dir(n, 100) for n in ("tmpabcd1234", "claude-1000", "cc-tests-nope", "xcc-test-old")]
        lock = os.path.join(self.tmp, "muse-poll.lock")
        open(lock, "w").close()
        os.utime(lock, (self.t - 400 * 3600,) * 2)
        link = os.path.join(self.tmp, "cc-test-link")
        os.symlink(new, link)
        os.utime(link, (self.t - 400 * 3600,) * 2, follow_symlinks=False)
        notold = os.path.join(self.tmp, "cc-test-file")
        open(notold, "w").close()
        os.utime(notold, (self.t - 400 * 3600,) * 2)
        _, out = self.go(Box(**self.HEALTHY))
        self.assertFalse(os.path.exists(old))
        for keep in [new, lock, link, notold] + other_old:
            self.assertTrue(os.path.lexists(keep), keep)
        self.assertTrue(os.path.exists(os.path.join(new, "f")))  # the symlink target was not followed
        self.assertIn("sweep: removed 1 cc-test-* folder(s)", out)
        self.assertIn("removed 1 cc-test-*", open(os.path.join(self.sd, "disk.log")).read())

    def test_nothing_to_sweep_logs_nothing(self):
        self.make_dir("cc-test-new", 1)
        _, out = self.go(Box(**self.HEALTHY))
        self.assertNotIn("sweep", out)
        self.assertFalse(os.path.exists(os.path.join(self.sd, "disk.log")))

    def test_the_sweep_stops_at_the_cap(self):
        paths = [self.make_dir("cc-test-%d" % i, 30) for i in range(7)]
        gone = wd.sweep_old_test_dirs(self.t, self.tmp, cap=3)
        self.assertEqual(len(gone), 3)
        self.assertEqual(sum(os.path.exists(p) for p in paths), 4)
        self.assertEqual(len(wd.sweep_old_test_dirs(self.t, self.tmp, cap=3)), 3)  # the next run takes the next three
        self.assertEqual(sum(os.path.exists(p) for p in paths), 1)

    def test_the_sweep_scan_is_capped(self):
        for i in range(10):
            self.make_dir("cc-test-%d" % i, 30)
        self.assertLessEqual(len(wd.sweep_old_test_dirs(self.t, self.tmp, dry_run=True, scan_cap=4)), 4)

    def test_dry_run_lists_the_sweep_and_deletes_nothing(self):
        old = self.make_dir("cc-test-old", 25)
        other = self.make_dir("tmpabcd1234", 100)
        _, out = self.go(Box(**self.HEALTHY), "--dry-run")
        self.assertIn("would remove 1 cc-test-*", out)
        self.assertIn("  cc-test-old", out)
        self.assertNotIn("tmpabcd1234", out)
        self.assertTrue(os.path.exists(old) and os.path.exists(other))
        self.assertFalse(os.path.exists(os.path.join(self.sd, "disk.log")))

    def test_pattern_names(self):
        self.assertEqual([wd.tmp_pattern(n) for n in ("tmpa1B2c3D4", "cc-test-xyz", "claude-1000", "muse-poll.lock", "x", "abcdef")],
                         ["tmpXXXXXXXX", "cc-test-*", "claude-*", "muse-*", "x*", "abc*"])


if __name__ == "__main__":
    unittest.main()
