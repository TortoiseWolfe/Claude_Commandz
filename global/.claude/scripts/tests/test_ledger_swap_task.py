"""The LedgerSwap task must keep the script's exit code (same reason as the UsageCalibrate task, issue 273): the action is wscript
running a one-line launcher, never conhost, and the launcher runs the daily swap through wsl and quits with its code."""

import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ledger_swap_task as t


class TaskXml(unittest.TestCase):
    def test_the_xml_parses_and_has_two_daily_triggers(self):
        root = ET.fromstring(t.xml_text().split("?>", 1)[1])
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        starts = [e.text for e in root.findall(".//t:CalendarTrigger/t:StartBoundary", ns)]
        self.assertEqual([s.split("T")[1] for s in starts], ["08:20:00", "16:20:00"])
        self.assertEqual(len(root.findall(".//t:ScheduleByDay", ns)), 2)

    def test_action_is_wscript_not_conhost_and_hidden(self):
        x = t.xml_text()
        self.assertIn("wscript.exe", x)
        self.assertNotIn("conhost", x)
        self.assertIn("//B", x)
        self.assertIn("ledger_swap.vbs", x)
        self.assertIn("\\AFA\\LedgerSwap", x)

    def test_a_missed_run_starts_when_the_machine_is_next_available(self):
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", t.xml_text())


class Install(unittest.TestCase):
    def test_install_refuses_to_point_the_task_at_a_checkout(self):
        # a checkout path is not under .claude/scripts, so install must stop before it touches Windows
        with mock.patch.object(t, "SCRIPT", Path("/work/repo/global/scripts/ledger_swap_daily.py")), \
             mock.patch.object(t.subprocess, "run", side_effect=AssertionError("must not run anything")):
            self.assertEqual(t.install(), 2)


class Launcher(unittest.TestCase):
    def test_launcher_runs_the_daily_swap_waits_and_quits_with_its_code(self):
        v = t.vbs_text()
        self.assertIn("wsl.exe -d Debian -- bash -lc", v)
        self.assertIn("ledger_swap_daily.py run", v)
        self.assertRegex(v, r"\.Run\(.*, 0, True\)")
        self.assertIn("WScript.Quit", v)


if __name__ == "__main__":
    unittest.main()
