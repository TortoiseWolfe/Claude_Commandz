import json
import subprocess
import sys
import unittest
from pathlib import Path

from tmpdirs import fresh_dir

SCRIPT = Path(__file__).resolve().parent.parent / "graphify_hook_portable.py"
NAME = "settings" + ".json"
OLD = {"hooks": {"PreToolUse": [
    {"matcher": "Bash|Grep", "hooks": [{"type": "command", "command": "/home/someone/.local/bin/graphify hook-guard search"}]},
    {"matcher": "Read|Glob", "hooks": [{"type": "command", "command": "/home/someone/.local/bin/graphify hook-guard read"}]},
    {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/other-tool check"}]},
]}}


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class PortableHooks(unittest.TestCase):
    def repo(self, data):
        d = Path(fresh_dir(self))
        (d / ".claude").mkdir()
        (d / ".claude" / NAME).write_text(json.dumps(data))
        return d

    def cmds(self, d):
        data = json.loads((d / ".claude" / NAME).read_text())
        return [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]

    def test_rewrites_absolute_paths_and_leaves_others(self):
        d = self.repo(OLD)
        r = run(str(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        c = self.cmds(d)
        self.assertIn("hook-guard search", c[0])
        self.assertIn("command -v graphify", c[0])
        self.assertNotIn("/home/someone", c[0] + c[1])
        self.assertTrue(c[1].endswith("hook-guard read || exit 0'"))
        self.assertEqual(c[2], "/usr/bin/other-tool check")

    def test_second_run_changes_nothing(self):
        d = self.repo(OLD)
        run(str(d))
        before = (d / ".claude" / NAME).read_text()
        r = run(str(d))
        self.assertIn("already portable", r.stdout)
        self.assertEqual((d / ".claude" / NAME).read_text(), before)

    def test_check_mode_reports_without_writing(self):
        d = self.repo(OLD)
        before = (d / ".claude" / NAME).read_text()
        r = run("--check", str(d))
        self.assertEqual(r.returncode, 1)
        self.assertEqual((d / ".claude" / NAME).read_text(), before)

    def test_the_hook_runs_quietly_when_graphify_is_missing(self):
        d = self.repo(OLD)
        run(str(d))
        cmd = self.cmds(d)[0]
        home = fresh_dir(self)
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": home})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_missing_settings_is_skipped(self):
        r = run(fresh_dir(self))
        self.assertEqual(r.returncode, 0)
        self.assertIn("skip", r.stdout)


if __name__ == "__main__":
    unittest.main()
