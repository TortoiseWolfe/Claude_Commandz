"""lane.sh --status and hub_digest.py read a hand-off's Done and Next lines through lane_handoff_summary.py."""

import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402
import hub_digest  # noqa: E402
import lane_handoff_summary as lhs  # noqa: E402


class HandoffSummary(unittest.TestCase):
    def write(self, text):
        d = fresh_dir(self)
        p = Path(d) / "handoff.md"
        p.write_text(text)
        return p

    def test_headings(self):
        p = self.write("# handoff\n## Done\n- merged it\n## Next\n- open the PR\n")
        self.assertEqual(lhs.handoff_summary(p), "Done: merged it | Next: open the PR")

    def test_inline_and_missing(self):
        self.assertEqual(lhs.handoff_summary(self.write("Done: a\nNext: b\n")), "Done: a | Next: b")
        self.assertEqual(lhs.handoff_summary("/nonexistent/handoff.md"), "")

    def test_hub_digest_uses_the_same_function(self):
        self.assertIs(hub_digest.handoff_summary, lhs.handoff_summary)

    def test_cli_prints_the_line(self):
        p = self.write("Done: a\nNext: b\n")
        out = subprocess.run([sys.executable, "-I", str(HERE / "lane_handoff_summary.py"), str(p)], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "Done: a | Next: b")


if __name__ == "__main__":
    unittest.main()
