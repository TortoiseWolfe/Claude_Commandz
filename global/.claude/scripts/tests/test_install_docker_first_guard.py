"""install_docker_first_guard.py: the one command a person types to turn the Docker-first guard on or off on a machine.

Every "home" here is a throwaway folder made from the repo's own dotfiles settings.json; nothing touches the live ~/.claude, whose
settings the no-AI wall keeps from sessions. The installer must copy the hook first, merge exactly one PreToolUse Bash entry (no
duplicate on a second run, nothing else changed), prove it with a smoke test, and put both files back when the proof fails."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "install_docker_first_guard.py"
HOOK = HERE.parents[1] / "hooks" / "docker-first-guard.py"
DOTFILES = HERE.parents[2] / "dotfiles" / "settings.json"
sys.path.insert(0, str(HERE.parent))
import install_docker_first_guard as idg  # noqa: E402


def entry_count(settings):
    return len(idg.registered(settings))


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.home = self.root / "claude"
        (self.home / "hooks").mkdir(parents=True)
        self.settings = self.home / "settings.json"
        # the repo's real settings, minus our entry: what a machine has before the install
        d = json.loads(DOTFILES.read_text())
        idg.remove_entry(d)
        self.before_dict = d
        self.before = idg.dump(d, DOTFILES.read_bytes())
        self.settings.write_bytes(self.before)
        self.fake_home = self.root / "fakehome"  # the process HOME: a real one must never be touched
        self.fake_home.mkdir()

    def run_cli(self, *args, source=None):
        argv = [sys.executable, str(SCRIPT), "--home", str(self.home)]
        if source:
            argv += ["--source", str(source)]
        env = dict(os.environ, HOME=str(self.fake_home))
        r = subprocess.run(argv + list(args), capture_output=True, text=True, env=env, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def live(self):
        return json.loads(self.settings.read_text())

    def last(self, out):
        return out.strip().splitlines()[-1]


class InstallTests(Base):
    def test_fresh_install_copies_the_file_adds_one_entry_and_passes(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.last(out).startswith("PASS"), out)
        installed = self.home / "hooks" / "docker-first-guard.py"
        self.assertEqual(installed.read_bytes(), HOOK.read_bytes())
        self.assertTrue(installed.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(entry_count(self.live()), 1)

    def test_nothing_but_the_one_entry_changes(self):
        self.run_cli()
        after = self.live()
        groups = after["hooks"]["PreToolUse"]
        self.assertEqual(groups[-1], idg.ENTRY)
        after["hooks"]["PreToolUse"] = groups[:-1]
        self.assertEqual(after, self.before_dict)

    def test_result_equals_the_dotfiles_copy_in_this_repo(self):
        # the repo's settings.json mirror carries the same entry the installer adds, byte for byte
        self.run_cli()
        self.assertEqual(self.settings.read_bytes(), DOTFILES.read_bytes())

    def test_a_second_run_changes_nothing(self):
        self.run_cli()
        once = self.settings.read_bytes()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.settings.read_bytes(), once)
        self.assertEqual(entry_count(self.live()), 1)
        self.assertIn("already registered", out)
        self.assertTrue(self.last(out).startswith("PASS"))

    def test_a_rerun_after_a_hook_fix_replaces_the_old_file_and_adds_no_second_entry(self):
        # the heredoc fix ships as a new hook file: Jonathan re-runs the one line, the file is updated, the entry is not doubled
        self.run_cli()
        installed = self.home / "hooks" / "docker-first-guard.py"
        installed.write_bytes(b"#!/usr/bin/env python3\n# the version before the heredoc fix\nimport sys\nsys.exit(0)\n")
        settings_once = self.settings.read_bytes()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(installed.read_bytes(), HOOK.read_bytes())
        self.assertEqual(self.settings.read_bytes(), settings_once)
        self.assertEqual(entry_count(self.live()), 1)
        self.assertTrue(self.last(out).startswith("PASS"), out)

    def test_the_file_is_in_place_before_the_settings_entry_is_written(self):
        # a settings entry that points at a missing file makes every Bash call fail, so the order is part of the contract
        src = Path(idg.__file__).read_text()
        body = src[src.index("def install("):src.index("def uninstall(")]
        self.assertLess(body.index("write_atomic(hook, src"), body.index("write_atomic(settings_path, dump("))

    def test_backup_and_undo_put_both_files_back(self):
        old_hook = b"#!/usr/bin/env python3\n# an older version\n"
        (self.home / "hooks" / "docker-first-guard.py").write_bytes(old_hook)
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        folders = list((self.home / "state" / "docker-first-guard-backups").iterdir())
        self.assertEqual(len(folders), 1)
        self.assertEqual((folders[0] / "settings.json").read_bytes(), self.before)
        self.assertEqual((folders[0] / "docker-first-guard.py").read_bytes(), old_hook)
        subprocess.run(["sh", str(folders[0] / "undo.sh")], check=True)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertEqual((self.home / "hooks" / "docker-first-guard.py").read_bytes(), old_hook)

    def test_the_settings_own_indent_and_missing_final_newline_are_kept(self):
        four = json.dumps(self.before_dict, indent=4).encode()  # four spaces, no final newline
        self.settings.write_bytes(four)
        self.run_cli()
        raw = self.settings.read_bytes()
        self.assertFalse(raw.endswith(b"\n"))
        self.assertIn(b'\n    "hooks": {', raw)
        self.assertEqual(entry_count(self.live()), 1)

    def test_settings_with_no_hooks_section_gets_one(self):
        self.settings.write_text(json.dumps({"cleanupPeriodDays": 90}, indent=2) + "\n")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.live()["hooks"]["PreToolUse"], [idg.ENTRY])
        self.assertEqual(self.live()["cleanupPeriodDays"], 90)

    def test_dry_run_changes_nothing(self):
        code, out, _ = self.run_cli("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertFalse((self.home / "hooks" / "docker-first-guard.py").exists())
        self.assertFalse((self.home / "state").exists())

    def test_the_smoke_test_ignores_a_machines_exemption_file(self):
        afa = self.fake_home / ".config" / "afa"
        afa.mkdir(parents=True)
        (afa / "docker-first-exempt.txt").write_text("/\n")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual((afa / "docker-first-exempt.txt").read_text(), "/\n")  # read, never written
        self.assertEqual(sorted(p.name for p in afa.iterdir()), ["docker-first-exempt.txt"])

    def test_no_exemption_file_is_created(self):
        self.run_cli()
        self.assertFalse((self.fake_home / ".config").exists())


class RefusalTests(Base):
    def assert_untouched(self, code, out):
        self.assertEqual(code, 2, out)
        self.assertTrue(self.last(out).startswith("FAIL"), out)
        self.assertEqual(self.settings.read_bytes(), self.before_or_current)
        self.assertFalse((self.home / "hooks" / "docker-first-guard.py").exists())

    def test_missing_settings_stops_before_copying_anything(self):
        self.settings.unlink()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 2, out)
        self.assertTrue(self.last(out).startswith("FAIL"))
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.home / "hooks" / "docker-first-guard.py").exists())

    def test_invalid_json_is_left_alone(self):
        bad = b'{"hooks": {"PreToolUse": [ // a comment\n]}}'
        self.settings.write_bytes(bad)
        self.before_or_current = bad
        code, out, _ = self.run_cli()
        self.assert_untouched(code, out)
        self.assertIn("not valid JSON", out)

    def test_odd_shapes_are_left_alone(self):
        for odd in ('{"hooks": []}', '{"hooks": {"PreToolUse": {}}}', '{"hooks": {"PreToolUse": [3]}}', '[1]',
                    '{"hooks": {"PreToolUse": [{"hooks": [3]}]}}'):
            self.settings.write_text(odd)
            self.before_or_current = odd.encode()
            code, out, _ = self.run_cli()
            self.assert_untouched(code, out)

    def test_a_missing_source_stops(self):
        self.before_or_current = self.before
        code, out, _ = self.run_cli(source=self.root / "nope.py")
        self.assert_untouched(code, out)

    def test_a_source_with_scrub_placeholder_text_is_refused(self):
        src = self.root / "placeholder.py"
        src.write_text('SELF = "someone@example.com"\n')
        self.before_or_current = self.before
        code, out, _ = self.run_cli(source=src)
        self.assert_untouched(code, out)
        self.assertIn("placeholder", out)

    def test_the_real_hook_carries_no_placeholder_text(self):
        self.assertIsNone(idg.PLACEHOLDER_RE.search(HOOK.read_bytes()))


class RollbackTests(Base):
    def test_a_hook_that_allows_everything_fails_the_smoke_and_both_files_go_back(self):
        src = self.root / "lax.py"
        src.write_text("import sys\nsys.exit(0)\n")
        code, out, _ = self.run_cli(source=src)
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL smoke "npm ci": exit 0, wanted 2', out)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertFalse((self.home / "hooks" / "docker-first-guard.py").exists())

    def test_a_hook_that_refuses_everything_fails_the_smoke(self):
        src = self.root / "strict.py"
        src.write_text('import sys\nprint("no", file=sys.stderr)\nsys.exit(2)\n')
        code, out, _ = self.run_cli(source=src)
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL smoke "npm --version"', out)
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_a_refusal_that_does_not_quote_the_rule_fails(self):
        src = self.root / "mute.py"
        src.write_text('import sys, json\nc = json.load(sys.stdin)["tool_input"]["command"]\nsys.exit(2 if "ci" in c else 0)\n')
        code, out, _ = self.run_cli(source=src)
        self.assertEqual(code, 1, out)
        self.assertIn("does not quote the rule", out)

    def test_a_failed_reinstall_puts_the_previous_hook_back(self):
        old = HOOK.read_bytes()
        (self.home / "hooks" / "docker-first-guard.py").write_bytes(old + b"\n# older copy\n")
        src = self.root / "lax.py"
        src.write_text("import sys\nsys.exit(0)\n")
        code, _, _ = self.run_cli(source=src)
        self.assertEqual(code, 1)
        self.assertEqual((self.home / "hooks" / "docker-first-guard.py").read_bytes(), old + b"\n# older copy\n")
        self.assertEqual(self.settings.read_bytes(), self.before)


class UninstallTests(Base):
    def test_off_switch_removes_the_entry_and_the_file_and_nothing_else(self):
        self.run_cli()
        code, out, _ = self.run_cli("--uninstall")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.last(out).startswith("PASS"), out)
        self.assertEqual(self.live(), self.before_dict)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertFalse((self.home / "hooks" / "docker-first-guard.py").exists())

    def test_a_group_that_holds_other_hooks_keeps_them(self):
        d = json.loads(json.dumps(self.before_dict))
        d["hooks"]["PreToolUse"].append(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-guard"}, idg.ENTRY["hooks"][0]]})
        self.settings.write_bytes(idg.dump(d, self.before))
        self.run_cli("--uninstall")
        last = self.live()["hooks"]["PreToolUse"][-1]
        self.assertEqual(last["hooks"], [{"type": "command", "command": "other-guard"}])

    def test_uninstall_when_never_installed_is_a_clean_no_op(self):
        code, out, _ = self.run_cli("--uninstall")
        self.assertEqual(code, 0, out)
        self.assertIn("not registered", out)
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_install_again_after_uninstall_works(self):
        self.run_cli()
        self.run_cli("--uninstall")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(entry_count(self.live()), 1)


class ScopeTests(unittest.TestCase):
    def test_it_is_standalone_and_never_calls_a_repo_wide_sync(self):
        src = SCRIPT.read_text()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+install_", src, re.M))
        self.assertNotRegex(src, r"subprocess\.run\(\[[^\]]*install_(global|scripts)")

    def test_the_entry_matches_the_dotfiles_mirror(self):
        found = [g for g in json.loads(DOTFILES.read_text())["hooks"]["PreToolUse"] if idg.COMMAND in json.dumps(g)]
        self.assertEqual(found, [idg.ENTRY])

    def test_the_entry_is_the_documented_command(self):
        self.assertEqual(idg.COMMAND, "python3 -I ~/.claude/hooks/docker-first-guard.py")
        self.assertEqual(idg.LEGACY_COMMAND, "python3 ~/.claude/hooks/docker-first-guard.py")
        self.assertEqual(idg.RULE, "always Docker first! for all our projects")


class IsolatedFormTests(Base):
    def with_old_entry(self, extra=False):
        d = json.loads(json.dumps(self.before_dict))
        old = {"type": "command", "command": idg.LEGACY_COMMAND, "timeout": 5}
        d["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [old]})
        if extra:
            d["hooks"]["PreToolUse"].append(json.loads(json.dumps(idg.ENTRY)))
        self.settings.write_bytes(idg.dump(d, self.before))
        return d

    def commands(self):
        return [h["command"] for g in self.live()["hooks"]["PreToolUse"] for h in g["hooks"]]

    def test_a_fresh_install_writes_the_dash_I_form(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertIn("python3 -I ~/.claude/hooks/docker-first-guard.py", self.commands())
        self.assertNotIn(idg.LEGACY_COMMAND, self.commands())

    def test_an_old_plain_entry_is_upgraded_in_place_with_no_duplicate(self):
        self.with_old_entry()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.last(out)[:4], "PASS", out)
        self.assertEqual(self.commands().count(idg.COMMAND), 1)
        self.assertNotIn(idg.LEGACY_COMMAND, self.commands())
        self.assertEqual(self.live()["hooks"]["PreToolUse"][-1], idg.ENTRY)

    def test_a_plain_entry_beside_an_I_entry_is_dropped(self):
        self.with_old_entry(extra=True)
        self.run_cli()
        self.assertEqual(self.commands().count(idg.COMMAND), 1)
        self.assertNotIn(idg.LEGACY_COMMAND, self.commands())

    def test_the_upgrade_is_stable_on_a_second_run(self):
        self.with_old_entry()
        self.run_cli()
        first = self.settings.read_bytes()
        self.run_cli()
        self.assertEqual(self.settings.read_bytes(), first)

    def test_uninstall_removes_the_old_plain_form(self):
        self.with_old_entry()
        code, out, _ = self.run_cli("--uninstall")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.live(), self.before_dict)

    def test_uninstall_removes_the_dash_I_form(self):
        self.run_cli()
        self.run_cli("--uninstall")
        self.assertEqual(self.live(), self.before_dict)

    def test_uninstall_removes_both_forms_at_once(self):
        self.with_old_entry(extra=True)
        self.run_cli("--uninstall")
        self.assertEqual(self.live(), self.before_dict)


if __name__ == "__main__":
    unittest.main()
