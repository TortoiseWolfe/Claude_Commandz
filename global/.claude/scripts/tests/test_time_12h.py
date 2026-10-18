"""18 Oct: Jonathan asked the receptionist why it still used military time. It copied the 24-hour stamps out of the status
text it is handed. Everything that reaches his chat or his eyes prints 12-hour with AM/PM; machine fields (ISO stamps in
JSON, logs, file names, sort keys) stay as they are."""

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
import hub_digest as hd  # noqa: E402
import openclaw_status_digest as dg  # noqa: E402

# A clock written the 24-hour way: two-digit hour 00-23, minutes, and no AM/PM after it.
MILITARY = re.compile(r"(?<![\w:.])(?:[01]\d|2[0-3]):[0-5]\d(?!\d|:\d|\.\d|\s?[AaPp]\.?[Mm]\b)")
# Lines that format a time for a machine to read back: they carry a full date, or a compact one with no colon.
MACHINE_FORMAT = re.compile(r"%Y-%m-%d|%Y%m%d|%m%d-%H%M|%Y-%m-%dT|%F")
HUMAN_FILES = ["openclaw_dispatch.py", "openclaw_status_digest.py", "hub_digest.py"]


class Clock12(unittest.TestCase):
    def test_a_24_hour_clock_in_prose_becomes_12_hour(self):
        for src, want in {
            "from 09:10 every call failed": "from 9:10 AM every call failed",
            "the 13:09 status": "the 1:09 PM status",
            "Support-followup (13:10)": "Support-followup (1:10 PM)",
            "midnight 00:05 and noon 12:00 EDT": "midnight 12:05 AM and noon 12:00 PM EDT",
            "2026-10-16 21:00 sharp": "2026-10-16 9:00 PM sharp",
            "12:00-13:30 window": "12:00 PM-1:30 PM window",
            "23:59 and 10:45": "11:59 PM and 10:45 AM",
        }.items():
            with self.subTest(src=src):
                self.assertEqual(dg.clock12(src), want)

    def test_things_that_are_not_a_24_hour_clock_are_left_alone(self):
        for src in ["9:53 PM", "11:17 AM", "9:53", "2026-10-16T21:00:00Z", "at 21:00:15 exactly", "port 8080:80", "ab12:34",
                    "ratio 24:00", "v1.2:30", "--:--", "12:60", "1234:56", "12:34.5", "10:45 a.m."]:
            with self.subTest(src=src):
                self.assertEqual(dg.clock12(src), src)

    def test_it_is_idempotent(self):
        once = dg.clock12("at 13:09 and 09:10")
        self.assertEqual(dg.clock12(once), once)


class SourcesPrintTwelveHour(unittest.TestCase):
    def test_no_human_facing_strftime_uses_a_24_hour_clock(self):
        bad = []
        for f in HUMAN_FILES:
            for n, line in enumerate((SCRIPTS / f).read_text().splitlines(), 1):
                if re.search(r"%H", line) and not MACHINE_FORMAT.search(line):
                    bad.append("%s:%d: %s" % (f, n, line.strip()))
        self.assertEqual(bad, [])

    def test_hub_digest_stamp_and_git_dates(self):
        now = datetime(2026, 10, 18, 13, 30)
        self.assertRegex(hd._stamp(now), r"^Sun 18 Oct 1:30 PM ")
        self.assertNotIn("%H", hd.GIT_DATE)
        d = Path(tempfile.mkdtemp())
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-10-18T21:53:00", GIT_COMMITTER_DATE="2026-10-18T21:53:00",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test")
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
        out = subprocess.run(["git", "-C", str(d), "log", "-1", "--format=%h %ad %s", "--date=" + hd.GIT_DATE],
                             capture_output=True, text=True, check=True).stdout
        self.assertRegex(out, r" [A-Z][a-z]{2} 9:53 PM x\n$")

    def test_hub_digest_report_lines_and_prose(self):
        now = datetime(2026, 10, 18, 22, 0)
        lanes = Path(tempfile.mkdtemp())
        p = lanes / "2026-10-18-some-lane"
        p.mkdir()
        (p / "report.md").write_text("Cause: from 09:10 every call failed.\n")
        t = datetime(2026, 10, 18, 21, 53).timestamp()
        os.utime(p / "report.md", (t, t))
        page = hd.render(hd.build_sections(
            roadmap_body="", lane_status="", lanes_dir=lanes, inbox_text="", git_log="abc1234 Sun 9:53 PM did a thing",
            tidy_text="", budget="Week resets Wed 2026-10-21 12:00 EDT: used 1%", now=now), now, "NX-01", hd.CAP_DEFAULT)
        self.assertIn("Sun 9:53 PM some-lane: Cause: from 9:10 AM every call failed.", page)
        self.assertIn("built Sun 18 Oct 10:00 PM", page.split("\n", 1)[0])
        self.assertEqual(MILITARY.findall(page), [])
        self.assertIn("12:00 PM EDT", hd.budget_headline("Week a to reset Wed 2026-10-21 12:00 EDT (4 days left).\n| Used so far | 1% |\n"))

    def test_status_digest_stamp_and_prose(self):
        self.assertRegex(dg._stamp(datetime(2026, 10, 18, 13, 15)), r"^Sun 18 Oct 2026 1:15 PM ")

    def test_dispatch_clock_of_and_facts_line(self):
        import openclaw_dispatch as dp
        ts = datetime(2026, 10, 18, 13, 9).timestamp()
        self.assertEqual(dp._clock_of(ts), "1:09 PM")
        self.assertEqual(dp._clock_of(0), "--:--")  # no time known: not a clock at all


if __name__ == "__main__":
    unittest.main()
