"""handoff_check.py: the size limit, the headings, and a rotation that deletes nothing.

Every hand-off here is invented text in a throwaway folder."""

import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

CHECK = str(SCRIPTS / "handoff_check.py")
GOOD = "# Hand-off\n\n## Done\nbuilt the invented thing\n\n## Next steps\nopen the PR\n\n## Re-arm\nnothing\n"


class HandoffCheck(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.f = self.d / "handoff.md"

    def run_check(self, *args):
        return subprocess.run([sys.executable, CHECK, str(self.f), *args], capture_output=True, text=True)

    def test_a_short_hand_off_with_the_headings_is_ok(self):
        self.f.write_text(GOOD)
        r = self.run_check()
        self.assertEqual((r.returncode, r.stdout), (0, "ok\n"))

    def test_heading_level_and_case_do_not_matter(self):
        self.f.write_text("### DONE\nx\n# next step\ny\n## Re-Arm\nz\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_over_the_limit_exits_1_and_says_the_size(self):
        self.f.write_text(GOOD + "x" * 4100)
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("over the 4096-byte limit", r.stdout)

    def test_exactly_at_the_limit_is_ok(self):
        pad = 4096 - len(GOOD.encode())
        self.f.write_text(GOOD + "x" * pad)
        self.assertEqual(self.f.stat().st_size, 4096)
        self.assertEqual(self.run_check().returncode, 0)

    def test_the_limit_counts_bytes_not_characters(self):
        self.f.write_text(GOOD + "é" * 2100)  # 2,190 characters (under the limit) but 4,290 bytes (over it)
        self.assertEqual(self.run_check().returncode, 1)

    def test_a_missing_heading_exits_2_and_names_it(self):
        self.f.write_text("## Done\nx\n## Next\ny\n")
        r = self.run_check()
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing heading: Re-arm", r.stdout)

    def test_big_and_missing_headings_exit_3(self):
        self.f.write_text("just text " * 600)
        self.assertEqual(self.run_check().returncode, 3)

    def test_unreadable_file_exits_4_with_the_reason_on_stderr(self):
        r = self.run_check()
        self.assertEqual(r.returncode, 4)
        self.assertIn("cannot read", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_rotate_keeps_every_byte_and_leaves_a_skeleton_that_passes(self):
        text = GOOD + "history " * 700
        self.f.write_text(text)
        r = self.run_check("--rotate")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.d / "handoff-prev1.md").read_text(), text)
        self.assertIn("handoff-prev1.md", self.f.read_text())
        self.assertEqual(self.run_check().returncode, 0)

    def test_rotate_uses_the_next_free_number(self):
        (self.d / "handoff-prev1.md").write_text("old one")
        (self.d / "handoff-prev2.md").write_text("old two")
        self.f.write_text(GOOD + "x" * 5000)
        self.run_check("--rotate")
        self.assertEqual((self.d / "handoff-prev1.md").read_text(), "old one")
        self.assertEqual((self.d / "handoff-prev2.md").read_text(), "old two")
        self.assertTrue((self.d / "handoff-prev3.md").read_text().startswith("# Hand-off"))

    def test_rotate_changes_nothing_when_the_size_is_fine(self):
        self.f.write_text(GOOD)
        r = self.run_check("--rotate")
        self.assertEqual((r.returncode, r.stdout), (0, "ok\n"))
        self.assertEqual(self.f.read_text(), GOOD)
        self.assertFalse((self.d / "handoff-prev1.md").exists())

    def test_max_flag_changes_the_limit(self):
        self.f.write_text(GOOD)
        self.assertEqual(self.run_check("--max", "50").returncode, 1)


if __name__ == "__main__":
    unittest.main()
