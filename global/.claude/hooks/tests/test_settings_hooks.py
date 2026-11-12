"""The settings file kept in this repo must register the guard hooks the live machine runs.

global/dotfiles/settings.json is a backup of ~/.claude/settings.json. A person copies it
into place (the scripts installer never touches settings files), so a hook that is missing
here is a hook a rebuilt machine silently lacks. These tests read only the repo copy.
"""
import json
import os
import subprocess
import tempfile
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

    def test_every_stop_hook_has_a_time_limit(self):
        # A Stop hook runs at the end of every turn and three of them re-read the whole transcript. With no limit of its
        # own, one that hangs holds every turn's end for as long as Claude Code's default allows.
        missing = []
        for group in json.loads(SETTINGS.read_text())["hooks"]["Stop"]:
            for hook in group.get("hooks", []):
                limit = hook.get("timeout")
                if not (isinstance(limit, int) and 1 <= limit <= 30):
                    missing.append(hook.get("command", ""))
        self.assertEqual(missing, [], "Stop hooks with no time limit of 30 seconds or less")

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
        # The live value must be one bare absolute path: no spaces, no quotes, no $HOME and no ~. Claude Code runs
        # the prefix through a shell for Bash calls and hooks, but to start a stdio connector (MCP server) it uses
        # the whole prefix text as the path of one program, so a value like bash -c '... "$HOME/..."' starts no
        # connector (ENOENT, seen live on Claude Code 2.1.322). This template holds __HOME__ in place of the home
        # folder, so it carries no username; the restore steps in README.md fill it in.
        prefix = json.loads(SETTINGS.read_text())["env"].get("CLAUDE_CODE_SHELL_PREFIX", "")
        self.assertEqual(prefix, "__HOME__/.claude/scripts/shell_scrub_prefix.sh")
        for bad in (" ", "$", "~", "'", '"', "/home/", "/Users/"):
            self.assertNotIn(bad, prefix)

    def test_restore_steps_fill_in_the_home_folder(self):
        # Copied as it is, the template's prefix names no real file and every Bash call would exit 127,
        # so each restore block in README.md must fill __HOME__ in right after it copies the file.
        readme = (HOOKS_DIR.parent.parent.parent / "README.md").read_text()
        copies = readme.count("cp global/dotfiles/settings.json ~/.claude/settings.json")
        fills = readme.count('sed -i "s|__HOME__|$HOME|g" ~/.claude/settings.json')
        self.assertGreaterEqual(copies, 2)
        self.assertEqual(copies, fills)
        scripts = HOOKS_DIR.parent / "scripts"
        for name in ("shell_scrub_prefix.sh", "shell_scrub_wrapper.py", "secret_scrubber.py"):
            self.assertTrue((scripts / name).exists(), f"no copy of {name}")

    def test_the_four_scripts_are_in_this_repo(self):
        # Other hooks in the file (the private inbox watcher, for one) stay out of this
        # public repo on purpose, so only the four named here are checked.
        for name in ("lane-watch-guard.py", "context-warn.py", "coop-scrubber.py", "output-scrubber.py"):
            self.assertTrue((HOOKS_DIR / name).exists(), f"no copy of {name}")

    def test_outbound_guard_refuses_when_its_file_is_missing(self):
        # bash on a missing file exits 127, and Claude Code treats any hook exit but 2 as "carry on",
        # so a deleted block-outbound.sh would let every mail and shell call through with no guard.
        # The entry therefore checks for the file first and exits 2 (refuse) when it is gone.
        guard = "~/.claude/hooks/block-outbound.sh"
        found = [c for m, c in commands("PreToolUse") if m == "mcp__claude_ai_Gmail__.*|Bash"]
        self.assertEqual(len(found), 1)
        command = found[0]
        self.assertTrue(command.startswith(f"test -f {guard} || {{"), command)
        self.assertTrue(command.endswith(f"bash {guard}"), command)
        missing_branch = command[command.index("{"):command.rindex("}") + 1]
        self.assertIn("exit 2", missing_branch)
        self.assertIn(">&2", missing_branch)

    def test_outbound_guard_entry_run_for_real(self):
        # Run the entry's own text in an empty home folder, then with a stand-in guard file there.
        command = [c for m, c in commands("PreToolUse") if m == "mcp__claude_ai_Gmail__.*|Bash"][0]
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
            gone = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, input="{}")
            self.assertEqual(gone.returncode, 2)
            self.assertIn("BLOCKED", gone.stderr)
            hooks = Path(home) / ".claude" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "block-outbound.sh").write_text("cat >/dev/null; echo stand-in-ran; exit 0\n")
            there = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, input="{}")
            self.assertEqual(there.returncode, 0)
            self.assertEqual(there.stdout.strip(), "stand-in-ran")
            (hooks / "block-outbound.sh").write_text("cat >/dev/null; echo refused >&2; exit 2\n")
            refused = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, input="{}")
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stderr.strip(), "refused")

    def test_identity_leak_guard_covers_every_connector(self):
        # A connector (MCP tool) can send text to an outside service as easily as Bash or a web call can.
        self.assertIn(
            ("Bash|WebFetch|WebSearch|mcp__.*", "python3 ~/.claude/hooks/identity-leak-guard.py"),
            commands("PreToolUse"),
        )

    def test_own_review_merge_guard_runs_before_every_shell_call(self):
        # lane_merge.py refuses a repo that keeps its own review; this hook refuses the plain gh, curl and
        # git commands a session could type instead, so it must see every Bash call in every session.
        self.assertIn(("Bash", "python3 ~/.claude/hooks/own-review-merge-guard.py"), commands("PreToolUse"))
        self.assertTrue((HOOKS_DIR / "own-review-merge-guard.py").exists())

    def test_list_file_guard_covers_bash_and_the_edit_tools(self):
        found = [m for m, c in commands("PreToolUse") if c == "python3 ~/.claude/hooks/list-file-guard.py"]
        self.assertEqual(len(found), 1)
        matched = found[0].split("|")
        for tool in ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"):
            self.assertIn(tool, matched)
        self.assertTrue((HOOKS_DIR / "list-file-guard.py").exists(), "no copy of list-file-guard.py")

    def test_afa_mods_marketplace_and_plugin(self):
        data = json.loads(SETTINGS.read_text())
        self.assertIs(data["enabledPlugins"].get("afa-mods@afa-mods"), True)
        source = data["extraKnownMarketplaces"]["afa-mods"]["source"]
        self.assertEqual(source["source"], "directory")
        self.assertTrue(source["path"].endswith("/repos/Claude_Commandz"))

    def test_no_ai_wall_guard_runs_before_every_tool(self):
        # The wall's entry has no matcher on purpose: a matcher would leave some tool unguarded. On 12 Nov 2026 the
        # live settings file was overwritten and this entry was missing from the copy here, so a restore from this
        # repo alone would have brought the machine back with no wall.
        self.assertIn((None, "python3 -I ~/.claude/no-ai-wall/no-ai-wall-guard.py"), commands("PreToolUse"))
        self.assertTrue((HOOKS_DIR.parent / "no-ai-wall" / "no-ai-wall-guard.py").exists())

    def test_nextcloud_guard_runs_before_every_shell_call(self):
        # Like the private inbox watcher, this guard's own file stays out of this public repo, so only its entry
        # is checked. With the entry in place and the file missing every Bash call is refused, so a restore copies
        # the file from the private backup first.
        self.assertIn(("Bash", "python3 ~/.claude/hooks/nextcloud-guard.py"), commands("PreToolUse"))

    def test_vercel_plugin_and_marketplace(self):
        data = json.loads(SETTINGS.read_text())
        self.assertIs(data["enabledPlugins"].get("vercel-plugin@vercel"), True)
        self.assertEqual(data["extraKnownMarketplaces"]["vercel"]["source"], {"source": "github", "repo": "vercel/vercel-plugin"})
        self.assertEqual(data["env"].get("VERCEL_PLUGIN_TELEMETRY"), "off")

    def test_push_notices_and_model_switch_are_on(self):
        data = json.loads(SETTINGS.read_text())
        self.assertIs(data["agentPushNotifEnabled"], True)
        self.assertIs(data["switchModelsOnFlag"], True)

    def test_idle_compaction_is_off(self):
        self.assertIs(json.loads(SETTINGS.read_text())["idleCompaction"], False)


if __name__ == "__main__":
    unittest.main()
