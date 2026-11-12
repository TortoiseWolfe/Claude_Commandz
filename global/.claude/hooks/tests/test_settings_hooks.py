"""The settings file kept in this repo must register the guard hooks the live machine runs.

global/dotfiles/settings.json is a backup of ~/.claude/settings.json. A person copies it
into place (the scripts installer never touches settings files), so a hook that is missing
here is a hook a rebuilt machine silently lacks. These tests read only the repo copy.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SETTINGS = ROOT / "global" / "dotfiles" / "settings.json"
HOOKS_DIR = ROOT / "global" / ".claude" / "hooks"


def commands(event):
    out = []
    for group in json.loads(SETTINGS.read_text())["hooks"].get(event, []):
        for hook in group.get("hooks", []):
            out.append((group.get("matcher"), hook.get("command", "")))
    return out


class SettingsHookTests(unittest.TestCase):
    def test_file_is_valid_json(self):
        json.loads(SETTINGS.read_text())

    def test_lane_watch_guard_runs_on_stop(self):
        self.assertIn(
            "python3 ~/.claude/hooks/lane-watch-guard.py",
            [c for _, c in commands("Stop")],
        )

    def test_context_warn_runs_on_prompt_and_after_tools(self):
        want = "python3 ~/.claude/hooks/context-warn.py"
        self.assertIn(want, [c for _, c in commands("UserPromptSubmit")])
        self.assertIn(want, [c for _, c in commands("PostToolUse")])

    def test_output_scrubber_runs_after_every_tool(self):
        # output-scrubber.py masks secrets and then runs the co-op scrub itself (it loads coop-scrubber.py),
        # so it is registered in place of coop-scrubber.py, never beside it: two hooks that both rewrite
        # a tool's output would race.
        self.assertIn(
            ("*", "python3 ~/.claude/hooks/output-scrubber.py"),
            commands("PostToolUse"),
        )
        self.assertNotIn("python3 ~/.claude/hooks/coop-scrubber.py", [c for _, c in commands("PostToolUse")])

    def test_output_scrubber_runs_after_a_failed_tool_call(self):
        # A failed call's output cannot be rewritten by a hook; there the hook only detects and warns.
        self.assertIn(
            ("*", "python3 ~/.claude/hooks/output-scrubber.py"),
            commands("PostToolUseFailure"),
        )

    def test_shell_prefix_points_at_the_scrub_wrapper(self):
        # The wrapper is what masks a Bash call that exits non-zero, so the prefix must name a file in this repo.
        prefix = json.loads(SETTINGS.read_text())["env"].get("CLAUDE_CODE_SHELL_PREFIX", "")
        self.assertTrue(prefix.endswith("/.claude/scripts/shell_scrub_prefix.sh"), prefix)
        scripts = HOOKS_DIR.parent / "scripts"
        for name in ("shell_scrub_prefix.sh", "shell_scrub_wrapper.py", "secret_scrubber.py"):
            self.assertTrue((scripts / name).exists(), f"no copy of {name}")

    def test_the_four_scripts_are_in_this_repo(self):
        # Other hooks in the file (the private inbox watcher, for one) stay out of this
        # public repo on purpose, so only the four named here are checked.
        for name in ("lane-watch-guard.py", "context-warn.py", "coop-scrubber.py", "output-scrubber.py"):
            self.assertTrue((HOOKS_DIR / name).exists(), f"no copy of {name}")

    def test_afa_mods_marketplace_and_plugin(self):
        data = json.loads(SETTINGS.read_text())
        self.assertIs(data["enabledPlugins"].get("afa-mods@afa-mods"), True)
        source = data["extraKnownMarketplaces"]["afa-mods"]["source"]
        self.assertEqual(source["source"], "directory")
        self.assertTrue(source["path"].endswith("/repos/Claude_Commandz"))

    def test_idle_compaction_is_off(self):
        self.assertIs(json.loads(SETTINGS.read_text())["idleCompaction"], False)


if __name__ == "__main__":
    unittest.main()
