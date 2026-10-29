"""hub_lean.py: the hub's lean project settings, built from lane.sh's lists. Everything runs against a throwaway
AFA_REPOS folder; the live ~/repos is never touched."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
SCRIPT = SCRIPTS / "hub_lean.py"
LANE = (SCRIPTS / "lane.sh").read_text()

# What the hub used on 16-19 Oct (26 hub sessions): these must never be switched off.
HUB_NEEDS = ["claude.ai Gmail", "claude.ai Google Calendar", "claude.ai Google Drive"]
# Kept on in lanes too (lane.sh: "Kept on: afa-mods, security-guidance, code-review").
KEPT_PLUGINS = ["afa-mods", "security-guidance", "code-review"]


class HubLean(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repos = Path(self.tmp.name)
        self.file = self.repos / ".claude" / "settings.json"
        self.lean = Path(self.tmp.name) / "lean"
        self.lean_file = self.lean / ".claude" / "settings.json"

    def run_it(self, *args):
        env = dict(os.environ, AFA_REPOS=str(self.repos), AFA_LEAN_DIR=str(self.lean))
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def written(self):
        return json.loads(self.file.read_text())

    def test_install_writes_the_three_managed_keys(self):
        r = self.run_it("--install")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.written()
        self.assertEqual(sorted(d), ["deniedMcpServers", "enabledPlugins", "permissions"])
        self.assertEqual(d["permissions"]["deny"], ["Artifact", "ArtifactComments", "ArtifactData"])
        self.assertTrue(d["enabledPlugins"])
        self.assertFalse(any(d["enabledPlugins"].values()), "every listed plugin is switched off")

    def test_plugins_and_tools_come_from_lane_sh(self):
        self.run_it("--install")
        d = self.written()
        lane_plugins = re.search(r'^LANE_PLUGINS_OFF="([^"]*)"', LANE, re.M).group(1).split()
        lane_tools = re.search(r'^LANE_TOOLS_OFF="([^"]*)"', LANE, re.M).group(1).split()
        self.assertEqual(sorted(d["enabledPlugins"]), sorted(lane_plugins))
        self.assertEqual(d["permissions"]["deny"], lane_tools)

    def test_plugins_the_lanes_keep_stay_on(self):
        self.run_it("--install")
        for name in self.written()["enabledPlugins"]:
            for kept in KEPT_PLUGINS:
                self.assertFalse(name.startswith(kept + "@"), "%s must stay enabled" % name)

    def test_connectors_the_hub_uses_are_not_denied(self):
        self.run_it("--install")
        denied = [s["serverName"] for s in self.written()["deniedMcpServers"]]
        for need in HUB_NEEDS:
            self.assertNotIn(need, denied)
        for gone in ("claude.ai Resend", "claude.ai Calendly", "claude.ai Clay", "claude.ai PayPal", "claude.ai Vibe Prospecting"):
            self.assertIn(gone, denied)
        self.assertEqual(len(denied), len(set(denied)))

    def test_install_is_quiet_and_idempotent(self):
        first = self.run_it("--install")
        self.assertIn("wrote", first.stdout)
        before = self.file.stat().st_mtime_ns
        second = self.run_it("--install")
        self.assertEqual((second.returncode, second.stdout), (0, ""))
        self.assertEqual(self.file.stat().st_mtime_ns, before, "a current file is not rewritten")

    def test_check_reports_missing_and_stale(self):
        self.assertEqual(self.run_it("--check").returncode, 1)
        self.assertFalse(self.file.exists(), "--check never writes")
        self.run_it("--install")
        self.assertEqual(self.run_it("--check").returncode, 0)
        d = self.written()
        d["enabledPlugins"]["superpowers@claude-plugins-official"] = True
        self.file.write_text(json.dumps(d))
        self.assertEqual(self.run_it("--check").returncode, 1)
        self.assertEqual(self.run_it("--install").returncode, 0)
        self.assertEqual(self.run_it("--check").returncode, 0)

    def test_other_keys_are_kept(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm:*)"]}, "enabledPlugins": {"mine@x": True}}))
        self.run_it("--install")
        d = self.written()
        self.assertEqual(d["model"], "opus")
        self.assertEqual(d["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertEqual(d["permissions"]["deny"], ["Bash(rm:*)", "Artifact", "ArtifactComments", "ArtifactData"])
        self.assertTrue(d["enabledPlugins"]["mine@x"])
        self.assertIn("superpowers@claude-plugins-official", d["enabledPlugins"])

    def test_broken_json_is_left_alone(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{not json")
        r = self.run_it("--install")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.file.read_text(), "{not json")

    def test_print_shows_settings_without_writing(self):
        r = self.run_it("--print")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["permissions"]["deny"][0], "Artifact")
        self.assertFalse(self.file.exists())

    # --lean-dir: the folder the unattended headless calls (muse_poll.sh) run in, outside ~/repos so no workspace CLAUDE.md loads.

    def test_lean_dir_prints_the_folder_and_writes_its_settings(self):
        r = self.run_it("--lean-dir")
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, str(self.lean), ""))
        d = json.loads(self.lean_file.read_text())
        self.assertEqual(sorted(d), ["autoMemoryEnabled", "deniedMcpServers", "enabledPlugins", "permissions"])
        self.assertIs(d["autoMemoryEnabled"], False)
        self.assertFalse(any(d["enabledPlugins"].values()))
        self.assertEqual(d["permissions"]["deny"], ["Artifact", "ArtifactComments", "ArtifactData"])
        denied = [s["serverName"] for s in d["deniedMcpServers"]]
        for need in HUB_NEEDS:
            self.assertNotIn(need, denied, "the poller needs Gmail; Calendar and Drive stay as in the hub")
        self.assertFalse(self.file.exists(), "the hub's own settings file is not touched")

    def test_lean_dir_default_is_outside_the_workspace(self):
        env = {k: v for k, v in os.environ.items() if k != "AFA_LEAN_DIR"}
        with tempfile.TemporaryDirectory() as home:
            env["HOME"] = home
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--lean-dir"], capture_output=True, text=True, env=env)
            self.assertEqual(r.stdout.strip(), str(Path(home) / ".claude" / "state" / "lean"))
            self.assertNotIn(str(Path(home) / "repos"), r.stdout, "a folder under ~/repos would load the workspace CLAUDE.md")

    def test_lean_dir_is_quiet_and_idempotent(self):
        self.run_it("--lean-dir")
        before = self.lean_file.stat().st_mtime_ns
        r = self.run_it("--lean-dir")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.lean_file.stat().st_mtime_ns, before, "a current file is not rewritten")

    def test_lean_dir_repairs_a_stale_file_and_keeps_other_keys(self):
        self.lean_file.parent.mkdir(parents=True)
        self.lean_file.write_text(json.dumps({"model": "haiku", "autoMemoryEnabled": True, "enabledPlugins": {"superpowers@claude-plugins-official": True}}))
        self.run_it("--lean-dir")
        d = json.loads(self.lean_file.read_text())
        self.assertEqual(d["model"], "haiku")
        self.assertIs(d["autoMemoryEnabled"], False)
        self.assertIs(d["enabledPlugins"]["superpowers@claude-plugins-official"], False)

    def test_lean_dir_with_broken_settings_warns_but_still_prints(self):
        self.lean_file.parent.mkdir(parents=True)
        self.lean_file.write_text("{not json")
        r = self.run_it("--lean-dir")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(self.lean)))
        self.assertIn("not updated", r.stderr)
        self.assertEqual(self.lean_file.read_text(), "{not json")

    def test_lean_dir_that_cannot_be_made_prints_nothing_and_fails(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("a file, not a folder")
        self.lean = blocker / "lean"
        r = self.run_it("--lean-dir")
        self.assertEqual((r.returncode, r.stdout), (1, ""))


if __name__ == "__main__":
    unittest.main()
