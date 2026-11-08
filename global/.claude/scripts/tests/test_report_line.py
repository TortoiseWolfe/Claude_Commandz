"""report_line.py: line 1 is stamped from the clock, only line 1 changes, unknown states are refused, and the result
still reads the way lane_watch.sh reads it.

Every report here is invented text in a throwaway folder."""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
from tmpdirs import fresh_dir  # noqa: E402
import report_line  # noqa: E402

TOOL = str(SCRIPTS / "report_line.py")
BODY = "\n## Hub merges\nline a\nline b\n"


class ReportLine(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.f = self.d / "report.md"

    def run_tool(self, *args, now: "str | None" = "9:05 AM"):
        env = dict(os.environ)
        if now:
            env["REPORT_LINE_NOW"] = now
        else:
            env.pop("REPORT_LINE_NOW", None)
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env)

    def test_set_rewrites_only_line_1_and_keeps_the_rest_exactly(self):
        self.f.write_text("WORKING 8:00 AM | old" + BODY)
        r = self.run_tool("set", str(self.f), "--state", "DONE", "--text", "built the invented thing")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.f.read_text(), "DONE 9:05 AM | built the invented thing" + BODY)
        self.assertEqual(r.stdout, "DONE 9:05 AM | built the invented thing\n")

    def test_the_stamp_comes_from_the_clock_in_12_hour_form_with_no_leading_zero(self):
        self.f.write_text("WORKING 8:00 AM" + BODY)
        r = self.run_tool("set", str(self.f), "--state", "WORKING", "--text", "x", now=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.f.read_text().split("\n")[0], r"^WORKING (1[0-2]|[1-9]):[0-5][0-9] (AM|PM) \| x$")
        n = self.run_tool("now", now=None)
        self.assertRegex(n.stdout, r"^(1[0-2]|[1-9]):[0-5][0-9] (AM|PM)\n$")

    def test_an_unknown_state_is_refused_and_the_file_is_untouched(self):
        self.f.write_text("WORKING 8:00 AM" + BODY)
        r = self.run_tool("set", str(self.f), "--state", "ALMOST", "--text", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown state", r.stderr)
        self.assertEqual(self.f.read_text(), "WORKING 8:00 AM" + BODY)

    def test_every_known_state_is_accepted_including_two_and_three_word_ones(self):
        for s in report_line.STATES:
            r = self.run_tool("set", str(self.f), "--state", s, "--text", "https://example.test/x")
            self.assertEqual(r.returncode, 0, (s, r.stderr))
            self.assertEqual(report_line.split_state(self.f.read_text().split("\n")[0])[0], s)

    def test_step_for_jonathan_needs_an_https_link(self):
        r = self.run_tool("set", str(self.f), "--state", "STEP-FOR-JONATHAN", "--text", "open the page")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.f.exists())
        r = self.run_tool("set", str(self.f), "--state", "STEP-FOR-JONATHAN", "--text", "open http://example.test/p")
        self.assertEqual(r.returncode, 2)
        r = self.run_tool("set", str(self.f), "--state", "STEP-FOR-JONATHAN", "--text", "open https://example.test/p in the fleet tab")
        self.assertEqual(r.returncode, 0, r.stderr)
        # The stamp is forced to 9:05 AM, but check reads the file's real change time. Move the change time to 9:05 AM
        # local so the test does not depend on the time of day it runs (it failed on a UTC runner before 9:03 AM UTC).
        at = report_line.datetime.datetime.now().replace(hour=9, minute=5, second=0, microsecond=0).timestamp()
        os.utime(self.f, (at, at))
        self.assertEqual(self.run_tool("check", str(self.f)).stdout, "ok\n")

    def test_multi_line_text_is_refused(self):
        r = self.run_tool("set", str(self.f), "--state", "DONE", "--text", "a\nb")
        self.assertEqual(r.returncode, 2)

    def test_a_missing_file_is_created_with_just_line_1(self):
        r = self.run_tool("set", str(self.f), "--state", "QUEUE EMPTY", "--text", "nothing queued")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.f.read_text(), "QUEUE EMPTY 9:05 AM | nothing queued\n")

    def test_a_file_with_only_one_line_and_no_newline_is_rewritten(self):
        self.f.write_text("WORKING 8:00 AM | old")
        self.run_tool("set", str(self.f), "--state", "BLOCKED", "--text", "waiting on the hub")
        self.assertEqual(self.f.read_text(), "BLOCKED 9:05 AM | waiting on the hub\n")

    def test_a_long_top_section_warns_but_still_writes(self):
        self.f.write_text("WORKING 8:00 AM\n" + "word " * 200 + "\n## Detail\nx\n")
        r = self.run_tool("set", str(self.f), "--state", "WORKING", "--text", "again")
        self.assertEqual(r.returncode, 0)
        self.assertRegex(r.stderr, r"top section is \d+ words")
        self.assertEqual(self.run_tool("check", str(self.f)).returncode, 1)

    def test_words_below_the_first_heading_do_not_count(self):
        self.f.write_text("WORKING 9:00 AM | short\n## Detail\n" + "word " * 400 + "\n")
        self.assertLess(report_line.words(report_line.top_section(self.f.read_text())), 150)

    def test_check_names_each_problem(self):
        self.f.write_text("hello there" + BODY)
        r = self.run_tool("check", str(self.f))
        self.assertEqual(r.returncode, 1)
        self.assertIn("known state word", r.stdout)
        self.assertIn("no h:mm", r.stdout)

    def test_check_flags_a_time_ahead_of_the_file_change(self):
        self.f.write_text("WORKING 11:59 PM | late stamp" + BODY)
        os.utime(self.f, (0, 0))  # changed at the epoch: midnight UTC, so any evening stamp is ahead of it in local time unless the zone is far east
        mtime_min = report_line.datetime.datetime.fromtimestamp(0)
        ahead = 23 * 60 + 59 - (mtime_min.hour * 60 + mtime_min.minute) > report_line.STAMP_SLACK_MIN
        r = self.run_tool("check", str(self.f))
        self.assertEqual(r.returncode, 1 if ahead else 0, r.stdout)

    def test_a_deadline_time_is_not_the_stamp(self):
        line = "WORKING 11:17 AM deadline 7:00 PM"
        self.assertEqual(report_line.status_time(line), 11 * 60 + 17)
        self.assertEqual(report_line.status_time("WORKING due 7:00 PM"), None)

    def test_noon_and_midnight(self):
        self.assertEqual(report_line.status_time("DONE 12:05 PM"), 12 * 60 + 5)
        self.assertEqual(report_line.status_time("DONE 12:05 AM"), 5)

    def test_a_state_word_must_end_at_a_word_boundary(self):
        self.assertEqual(report_line.split_state("DONEZO 9:00 AM")[0], None)
        self.assertEqual(report_line.split_state("QUEUE EMPTY 9:00 AM")[0], "QUEUE EMPTY")
        self.assertEqual(report_line.split_state("EMPTY 9:00 AM")[0], "EMPTY")

    def test_the_line_set_reads_the_way_the_watchers_read_it(self):
        """The same two patterns lane_watch.sh uses: the first H:MM AM|PM on line 1, and a state prefix idle_remind_check reads."""
        self.run_tool("set", str(self.f), "--state", "BLOCKED", "--text", "hub: approve the thing")
        first = self.f.read_text().split("\n")[0]
        self.assertEqual(re.findall(r"(1[0-2]|0?[1-9]):[0-5][0-9] ?[AaPp][Mm]", first)[:1], ["9"])
        self.assertTrue(first.startswith("BLOCKED"))
        self.run_tool("set", str(self.f), "--state", "QUEUE EMPTY", "--text", "done")
        self.assertTrue(self.f.read_text().startswith("QUEUE EMPTY"))
        self.run_tool("set", str(self.f), "--state", "WORKING", "--text", "building")
        self.assertTrue(self.f.read_text().startswith("WORKING"))


if __name__ == "__main__":
    unittest.main()
