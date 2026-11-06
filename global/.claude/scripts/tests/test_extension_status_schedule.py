"""/extension-status scheduling guidance (issue 3).
The command file is the product: the footer must give a recommended cron expression with a reason for the cadence, and
--install-schedule must be documented as an interactive step that asks before it registers anything. These tests read the
command file and hold those promises in place."""

import re
import unittest
from pathlib import Path

COMMAND = Path(__file__).resolve().parent.parent.parent / "commands" / "extension-status.md"
CRON = re.compile(r"(?<![\d*/,-])(?:[\d*/,-]+\s+){4}[\d*/,A-Za-z-]+(?=\s|`|$)")


def section(text, heading):
    m = re.search(rf"^#+ {re.escape(heading)}\s*$", text, re.M)
    assert m, f"no heading {heading!r}"
    level = len(m.group(0)) - len(m.group(0).lstrip("#"))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


class ScheduleDocs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = COMMAND.read_text()

    def test_has_a_schedule_section(self):
        section(self.text, "Scheduling")

    def test_recommends_a_weekday_morning_cron_for_triage(self):
        s = section(self.text, "Scheduling")
        self.assertIn("0 9 * * 1-5", s)
        self.assertIn("--triage", s)
        self.assertTrue(CRON.search(s))

    def test_explains_why_this_cadence(self):
        s = section(self.text, "Scheduling")
        self.assertRegex(s, r"(?i)why this cadence")
        self.assertRegex(s, r"(?i)snapshot|what changed")

    def test_install_schedule_flag_is_in_usage_and_arguments(self):
        self.assertEqual(len(re.findall(r"--install-schedule", self.text.split("## Instructions")[0])), 2)

    def test_install_schedule_asks_before_writing(self):
        s = section(self.text, "--install-schedule")
        self.assertRegex(s, r"(?i)confirm")
        self.assertRegex(s, r"/schedule")
        self.assertRegex(s, r"(?i)never .*without (?:an? )?(?:explicit )?(?:yes|confirmation)|only after")

    def test_install_schedule_does_not_pick_a_cadence_for_the_user(self):
        s = section(self.text, "--install-schedule")
        self.assertRegex(s, r"(?i)user picks|you pick|ask which")

    def test_a_second_schedule_is_a_second_cron_not_a_feature(self):
        s = section(self.text, "Scheduling")
        self.assertRegex(s, r"(?i)second cron|two separate")

    def test_the_old_related_line_points_at_the_section(self):
        related = section(self.text, "Related")
        self.assertIn("Scheduling", related)

    def test_the_roadmap_marks_it_done(self):
        road = section(self.text, "Roadmap")
        self.assertRegex(road, r"~~`/schedule` integration helper~~")


if __name__ == "__main__":
    unittest.main()
