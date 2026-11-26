"""Issue 226: lane_start_parts.py says what a lane's fresh session loaded before its first answer and whether the lane's own four files are the
cause. The transcripts below are invented; they have the shape of a real Claude Code transcript (attachment entries, then the first assistant
message with its usage)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import lane_start_parts as lsp  # noqa: E402


def attach(kind, text="x" * 3000):
    return {"type": "attachment", "attachment": {"type": kind, "content": text}}


def answer(tokens, side=False):
    d = {"type": "assistant", "message": {"role": "assistant", "usage": {"input_tokens": 5, "cache_creation_input_tokens": tokens - 5, "cache_read_input_tokens": 0}}}
    if side:
        d["isSidechain"] = True
    return d


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lane = self.dir / "lane"
        self.lane.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def transcript(self, rows, raw_lines=()):
        p = self.dir / "t.jsonl"
        with open(p, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            for r in raw_lines:
                fh.write(r + "\n")
        return p

    def files(self, **sizes):
        for name, n in sizes.items():
            (self.lane / (name + ".md")).write_text("a" * n)

    def run_cli(self, t, lane=None):
        return subprocess.run([sys.executable, "-I", str(HERE / "lane_start_parts.py"), str(t), str(lane or self.lane)], capture_output=True, text=True)

    def test_no_first_answer_prints_nothing(self):
        t = self.transcript([attach("skill_listing")])
        r = self.run_cli(t)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_small_files_say_the_setup_is_the_cause(self):
        self.files(report=2000, status=3000, handoff=1500, prompt=1100)
        t = self.transcript([attach("skill_listing", "s" * 54000), attach("deferred_tools_delta", "d" * 52000), answer(60000)])
        out = self.run_cli(t).stdout
        self.assertIn("too small to be the cause", out)
        self.assertIn("the session's own setup", out)
        self.assertIn("skill listing", out)
        self.assertNotIn("archive or shrink", out)

    def test_big_files_are_named_as_the_bulk(self):
        self.files(report=1000, status=1000, handoff=1000, prompt=90000)
        t = self.transcript([attach("skill_listing", "s" * 4000), answer(20000)])
        out = self.run_cli(t).stdout
        self.assertIn("its own files are the bulk", out)
        self.assertIn("prompt.md the biggest", out)

    def test_sidechain_answer_is_ignored(self):
        self.files(report=500)
        t = self.transcript([attach("skill_listing", "s" * 5000), answer(900, side=True), answer(40000)])
        got = lsp.read_start(t)
        self.assertEqual(got[0], 40000)

    def test_bad_json_lines_are_skipped(self):
        self.files(report=500)
        t = self.transcript([attach("skill_listing", "s" * 5000)], raw_lines=["{not json", "[1,2]", ""])
        with open(t, "a") as fh:
            fh.write(json.dumps(answer(33000)) + "\n")
        self.assertEqual(lsp.read_start(t)[0], 33000)

    def test_missing_folder_still_describes_the_load(self):
        t = self.transcript([attach("skill_listing", "s" * 5000), answer(33000)])
        out = self.run_cli(t, self.dir / "nope").stdout
        self.assertIn("its own files could not be read", out)
        self.assertIn("skill listing", out)

    def test_missing_transcript_prints_nothing(self):
        r = self.run_cli(self.dir / "gone.jsonl")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_parts_under_the_floor_are_not_named(self):
        self.files(report=500)
        t = self.transcript([attach("agent_listing_delta", "a" * 100), answer(33000)])
        self.assertNotIn("agent listing", self.run_cli(t).stdout)

    def test_output_never_carries_message_text(self):
        self.files(report=500)
        t = self.transcript([attach("skill_listing", "SECRETWORDS" * 500), answer(33000)])
        self.assertNotIn("SECRETWORDS", self.run_cli(t).stdout)

    def test_wrong_argument_count_is_a_usage_error(self):
        r = subprocess.run([sys.executable, "-I", str(HERE / "lane_start_parts.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
