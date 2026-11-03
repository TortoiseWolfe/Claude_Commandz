"""big_output.py: over 8,000 characters goes to a file with a short summary, only when asked for; --full and machine callers get everything."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import big_output as bo  # noqa: E402

BIG = "".join("- [ ] 2026-10-%02d 08:21 | Job: item %d with some words in it to make it long\n" % (i % 28 + 1, i) for i in range(200))
SMALL = "one\ntwo\n"


class BigOutputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
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


if __name__ == "__main__":
    unittest.main()
