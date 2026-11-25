"""Tests for script_drift.py: a throwaway git repo stands in for Claude_Commandz and a temporary folder for ~/.claude.
The comparison reads the ref, not the working files, so a checkout on a side branch cannot hide drift."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import script_drift as sd  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Drift(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo, self.live = root / "repo", root / "live"
        (self.repo / "global/.claude/scripts/tests").mkdir(parents=True)
        (self.repo / "global/.claude/hooks").mkdir(parents=True)
        (self.live / "scripts").mkdir(parents=True)
        (self.live / "hooks").mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        self.put(self.repo / "global/.claude/scripts/same.py", "a")
        self.put(self.repo / "global/.claude/scripts/changed.py", "repo version")
        self.put(self.repo / "global/.claude/scripts/not_installed.sh", "x")
        self.put(self.repo / "global/.claude/scripts/tests/test_same.py", "tests are not compared")
        self.put(self.repo / "global/.claude/hooks/guard.py", "g")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.put(self.live / "scripts/same.py", "a")
        self.put(self.live / "scripts/changed.py", "installed version with unsaved lines")
        self.put(self.live / "scripts/private_only.py", "p")
        self.put(self.live / "scripts/TOOLS.md", "generated")
        self.put(self.live / "scripts/notes.txt", "not a script")
        self.put(self.live / "hooks/guard.py", "g")

    @staticmethod
    def put(path, text):
        path.write_text(text)

    def test_the_three_kinds_are_told_apart(self):
        r = sd.compare(self.repo, "main", self.live)
        self.assertEqual(r["differs"], ["scripts/changed.py"])
        self.assertEqual(r["only_live"], ["scripts/private_only.py"])
        self.assertEqual(r["only_repo"], ["scripts/not_installed.sh"])
        self.assertEqual(r["checked"], 3)   # same.py, changed.py, hooks/guard.py

    def test_a_checkout_on_a_side_branch_does_not_hide_drift(self):
        git(self.repo, "checkout", "-q", "-b", "side")
        self.put(self.repo / "global/.claude/scripts/changed.py", "installed version with unsaved lines")
        git(self.repo, "commit", "-qam", "side work")
        r = sd.compare(self.repo, "main", self.live)   # main still has the older text
        self.assertEqual(r["differs"], ["scripts/changed.py"])

    def test_matching_files_report_no_drift(self):
        self.put(self.live / "scripts/changed.py", "repo version")
        r = sd.compare(self.repo, "main", self.live)
        self.assertEqual(r["differs"], [])
        self.assertIn("0 of 3 installed files differ from main", sd.summary_line(r))

    def test_summary_names_up_to_five_and_counts_the_rest(self):
        r = {"ref": "main", "checked": 9, "differs": [f"scripts/f{i}.py" for i in range(7)], "only_live": [], "only_repo": []}
        line = sd.summary_line(r)
        self.assertIn("7 of 9 installed files differ from main", line)
        self.assertIn("and 2 more", line)
        self.assertNotIn("f5.py", line)

    def test_a_ref_git_cannot_read_exits_2_and_writes_nothing(self):
        out = Path(self.tmp.name) / "drift.json"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = sd.main(["--repo", str(self.repo), "--ref", "no-such-ref", "--live", str(self.live), "--write", str(out)])
        self.assertEqual(rc, 2)
        self.assertFalse(out.exists())

    def test_write_saves_json_with_a_time_and_nothing_in_the_live_folder_changes(self):
        out = Path(self.tmp.name) / "drift.json"
        before = {p.name: p.read_bytes() for p in (self.live / "scripts").iterdir()}
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sd.main(["--repo", str(self.repo), "--ref", "main", "--live", str(self.live), "--write", str(out)])
        data = json.loads(out.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(data["differs"], ["scripts/changed.py"])
        self.assertGreater(data["at"], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.live / "scripts").iterdir()})


if __name__ == "__main__":
    unittest.main()
