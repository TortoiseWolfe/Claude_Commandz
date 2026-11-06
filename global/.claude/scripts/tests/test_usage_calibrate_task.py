"""The UsageCalibrate task must keep the script's exit code (issue 273). `conhost.exe --headless` swallows the child's
exit code, so a Cloudflare check (exit 6) showed in Task Scheduler as Last Result 0. wscript running a one-line
launcher passes the code through, so the task's action is wscript and never conhost."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import usage_calibrate_task as t


class TaskAction(unittest.TestCase):
    def test_action_is_wscript_not_conhost(self):
        x = t.xml_text()
        self.assertIn("wscript.exe", x)
        self.assertNotIn("conhost", x)

    def test_action_runs_hidden_and_without_banner(self):
        x = t.xml_text()
        self.assertIn("//B", x)
        self.assertIn("//Nologo", x)

    def test_action_names_the_launcher_file(self):
        self.assertIn("usage_calibrate.vbs", t.xml_text())


class Launcher(unittest.TestCase):
    def test_launcher_waits_for_wsl_and_quits_with_its_code(self):
        v = t.vbs_text()
        self.assertIn("wsl.exe -d Debian -- bash -lc", v)
        self.assertIn("usage_calibrate.js", v)
        self.assertRegex(v, r"\.Run\(.*, 0, True\)")  # 0 = hidden window, True = wait for the exit code
        self.assertIn("WScript.Quit", v)


if __name__ == "__main__":
    unittest.main()
