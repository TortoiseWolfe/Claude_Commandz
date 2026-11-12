"""big_output.py: over 8,000 characters goes to a file with a short summary, only when asked for; --full and machine callers get everything."""
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import big_output as bo  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

BIG = "".join("- [ ] 2026-10-%02d 08:21 | Job: item %d with some words in it to make it long\n" % (i % 28 + 1, i) for i in range(200))
SMALL = "one\ntwo\n"


class BigOutputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = fresh_dir(self)
        self.saved = dict(os.environ)
        os.environ.pop("AFA_BIG_OUT", None)
        os.environ.pop("AFA_LANE_FOLDER", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def run_emit(self, text, **kw):
        buf = io.StringIO()
        p = bo.emit(text, "t", stream=buf, **kw)
        return buf.getvalue(), p

    def test_big_length(self):
        self.assertGreater(len(BIG), 8000)

    def test_off_by_default_prints_everything(self):
        out, p = self.run_emit(BIG, folder=self.tmp)
        self.assertEqual(out, BIG)
        self.assertIsNone(p)

    def test_the_file_is_owner_only_and_a_new_folder_too(self):
        os.environ["AFA_BIG_OUT"] = "1"
        fresh = os.path.join(self.tmp, "made-here")
        _, p = self.run_emit(BIG, folder=fresh)
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(p.parent.stat().st_mode & 0o777), "0o700")

    def test_on_big_goes_to_file_with_short_summary(self):
        os.environ["AFA_BIG_OUT"] = "1"
        out, p = self.run_emit(BIG, folder=self.tmp, group_by=r"\| (\w+):")
        self.assertEqual(p.read_text(), BIG)
        self.assertEqual(p.parent, Path(self.tmp))
        summary = out.split("\nfull output")[0]
        self.assertLessEqual(len(summary), 500)
        self.assertIn(str(p), out)
        self.assertIn("Job 200", out)
        self.assertIn("--full", out)

    def test_small_output_is_untouched_even_when_on(self):
        os.environ["AFA_BIG_OUT"] = "1"
        out, p = self.run_emit(SMALL, folder=self.tmp)
        self.assertEqual(out, SMALL)
        self.assertIsNone(p)

    def test_full_flag_and_zero_win(self):
        os.environ["AFA_BIG_OUT"] = "1"
        self.assertEqual(self.run_emit(BIG, folder=self.tmp, full=True)[0], BIG)
        os.environ["AFA_BIG_OUT"] = "0"
        self.assertEqual(self.run_emit(BIG, folder=self.tmp)[0], BIG)

    def test_folder_is_the_lane_folder_when_known(self):
        os.environ["AFA_BIG_OUT"] = "1"
        os.environ["AFA_LANE_FOLDER"] = self.tmp
        _, p = self.run_emit(BIG)
        self.assertEqual(p.parent, Path(self.tmp))

    def test_filter_form(self):
        env = dict(os.environ, AFA_BIG_OUT="1")
        r = subprocess.run([sys.executable, "-I", str(HERE.parent / "big_output.py"), "--name", "board", "--folder", self.tmp],
                           input=BIG, capture_output=True, text=True, env=env)
        self.assertLess(len(r.stdout), 1000)
        r2 = subprocess.run([sys.executable, "-I", str(HERE.parent / "big_output.py"), "--full"], input=BIG,
                            capture_output=True, text=True, env=env)
        self.assertEqual(r2.stdout, BIG)

    # ---- latest mode and pruning ----
    def names(self):
        return sorted(os.listdir(self.tmp))

    def age(self, path, seconds):
        t = __import__("time").time() - seconds
        os.utime(path, (t, t), follow_symlinks=False)

    def test_latest_mode_replaces_one_fixed_file(self):
        os.environ["AFA_BIG_OUT"] = "1"
        _, p1 = self.run_emit(BIG, folder=self.tmp, latest=True)
        self.assertEqual(p1.name, "out-t-latest.txt")
        second = BIG + "extra line\n"
        _, p2 = self.run_emit(second, folder=self.tmp, latest=True)
        self.assertEqual(p2, p1)
        self.assertEqual(p2.read_text(), second)
        self.assertEqual(self.names(), ["out-t-latest.txt"])
        self.assertEqual(oct(p2.stat().st_mode & 0o777), "0o600")

    def test_latest_mode_leaves_no_temporary_file(self):
        os.environ["AFA_BIG_OUT"] = "1"
        self.run_emit(BIG, folder=self.tmp, latest=True)
        self.assertEqual([n for n in self.names() if n.endswith(".tmp")], [])

    def test_two_callers_at_once_do_not_share_a_temporary_file(self):
        # Two status calls can land in one folder in the same moment. With one shared temporary name the second caller
        # empties the first one's half-written file, and whoever swaps second finds its file already gone and crashes.
        os.environ["AFA_BIG_OUT"] = "1"
        seen = []
        real_replace, real_getpid = os.replace, os.getpid

        def spy(src, dst):
            seen.append(Path(src).name)
            return real_replace(src, dst)
        os.replace = spy
        try:
            for pid in (4101, 4102):
                os.getpid = lambda pid=pid: pid
                self.run_emit(BIG, folder=self.tmp, latest=True)
        finally:
            os.replace, os.getpid = real_replace, real_getpid
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        self.assertEqual(self.names(), ["out-t-latest.txt"])

    def test_latest_note_names_the_latest_path(self):
        os.environ["AFA_BIG_OUT"] = "1"
        out, p = self.run_emit(BIG, folder=self.tmp, latest=True)
        self.assertIn(str(Path(self.tmp) / "out-t-latest.txt"), out)

    def test_latest_cli_flag(self):
        env = dict(os.environ, AFA_BIG_OUT="1")
        for _ in range(2):
            subprocess.run([sys.executable, "-I", str(HERE.parent / "big_output.py"), "--name", "board", "--folder", self.tmp, "--latest"],
                           input=BIG, capture_output=True, text=True, env=env)
        self.assertEqual(self.names(), ["out-board-latest.txt"])

    def test_pruning_removes_only_old_tool_shaped_regular_files(self):
        os.environ["AFA_BIG_OUT"] = "1"
        d = Path(self.tmp)
        old = d / "out-other-101010.txt"
        young = d / "out-t-111111.txt"
        latest = d / "out-t-latest.txt"
        notes = d / "notes.txt"
        md = d / "out-report.md"
        for f in (old, young, latest, notes, md):
            f.write_text("x")
        for f in (old, latest, notes, md):
            self.age(f, 3 * 86400)
        self.age(young, 3600)
        adir = d / "out-dir-121212.txt"
        adir.mkdir()
        self.age(adir, 3 * 86400)
        outside = Path(fresh_dir(self))  # a second folder, removed with the test like the first
        target = outside / "target.txt"
        target.write_text("keep")
        self.age(target, 3 * 86400)
        link = d / "out-link-131313.txt"
        os.symlink(target, link)
        self.age(link, 3 * 86400)
        self.run_emit(BIG, folder=self.tmp, latest=True)
        self.assertFalse(old.exists())
        for f in (young, notes, md, adir):
            self.assertTrue(f.exists(), f)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(), "keep")
        self.assertEqual(latest.read_text(), BIG)  # replaced, not pruned

    def test_pruning_runs_for_time_named_mode_too(self):
        os.environ["AFA_BIG_OUT"] = "1"
        old = Path(self.tmp) / "out-x-000001.txt"
        old.write_text("x")
        self.age(old, 2 * 86400)
        _, p = self.run_emit(BIG, folder=self.tmp)
        self.assertFalse(old.exists())
        self.assertTrue(p.exists())

    def test_pruning_clears_a_dead_callers_temporary_file_after_a_day(self):
        os.environ["AFA_BIG_OUT"] = "1"
        d = Path(self.tmp)
        dead = d / "out-t-latest.txt.4242.tmp"
        fresh = d / "out-t-latest.txt.4243.tmp"
        other = d / "notes.txt.4242.tmp"
        for f in (dead, fresh, other):
            f.write_text("x")
        self.age(dead, 2 * 86400)
        self.age(other, 2 * 86400)
        self.age(fresh, 60)
        self.run_emit(BIG, folder=self.tmp, latest=True)
        self.assertFalse(dead.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())

    def test_under_the_limit_writes_and_prunes_nothing(self):
        os.environ["AFA_BIG_OUT"] = "1"
        old = Path(self.tmp) / "out-x-000001.txt"
        old.write_text("x")
        self.age(old, 2 * 86400)
        out, p = self.run_emit(SMALL, folder=self.tmp, latest=True)
        self.assertIsNone(p)
        self.assertEqual(out, SMALL)
        self.assertEqual(self.names(), ["out-x-000001.txt"])

    def test_a_failed_delete_does_not_stop_the_answer(self):
        os.environ["AFA_BIG_OUT"] = "1"
        old = Path(self.tmp) / "out-x-000001.txt"
        old.write_text("x")
        self.age(old, 2 * 86400)
        real = os.unlink

        def boom(*a, **k):
            raise PermissionError("no")
        os.unlink = boom
        try:
            out, p = self.run_emit(BIG, folder=self.tmp, latest=True)
        finally:
            os.unlink = real
        self.assertIn(str(p), out)
        self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
