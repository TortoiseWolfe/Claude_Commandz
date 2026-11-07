"""agy_deny.py writes the deny rules every agy lane starts with (6 Nov 2026: lane.sh started agy with no deny config, so a secrets file in the
checkout was readable and nothing stopped a push to main; opencode lanes had both walls since #316).

Nothing here runs agy (the tower's agy is not signed in, and a test must not spend quota). The checks are on the file it writes: the rules
that matter are present, and each command pattern, tried as agy anchors it (^(?:pattern)$), refuses what it should and lets ordinary work by."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402
import agy_deny  # noqa: E402

HOME = "/home/lane"


def command_patterns(settings):
    return [d[len("command(regex:"):-1] for d in settings["permissions"]["deny"] if d.startswith("command(regex:")]


def denied(settings, command):
    return any(re.fullmatch(p, command) for p in command_patterns(settings))


class AgyDeny(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(fresh_dir(self))
        self.repo = self.tmp / "repo"
        (self.repo / "sub").mkdir(parents=True)
        (self.repo / "node_modules" / "x").mkdir(parents=True)
        (self.repo / ".git").mkdir()
        for rel in (".env", ".env.local", "sub/.env", "prod.env", "keys/server.pem", "notes.txt", "src.py"):
            f = self.repo / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n")
        (self.repo / "node_modules" / "x" / ".env").write_text("x\n")
        (self.repo / ".git" / "config").write_text("x\n")
        self.settings = agy_deny.build(str(self.repo), HOME)
        self.rules = self.settings["permissions"]["deny"]

    def test_every_secret_file_in_the_checkout_is_denied_by_absolute_path(self):
        real = self.repo.resolve()
        for rel in (".env", ".env.local", "sub/.env", "prod.env", "keys/server.pem"):
            self.assertIn(f"read_file({real}/{rel})", self.rules, rel)

    def test_ordinary_files_and_vendored_folders_are_not_listed(self):
        text = "\n".join(self.rules)
        self.assertNotIn("notes.txt", text)
        self.assertNotIn("src.py", text)
        self.assertNotIn("node_modules", text)

    def test_root_names_home_secret_folders_and_git_settings_are_denied(self):
        for want in ("read_file(.env)", f"read_file({HOME}/.ssh/)", f"read_file({HOME}/.config/)", f"read_file({HOME}/.netrc)",
                     "write_file(.git/hooks/)", "write_file(.git/config)", f"write_file({HOME}/.gitconfig)"):
            self.assertIn(want, self.rules)

    def test_pushes_to_main_and_master_are_denied_in_every_spelling(self):
        for cmd in ("git push origin main", "git push -u origin master", "git push origin HEAD:main", "git push origin HEAD:refs/heads/main",
                    "git -C /x push origin main --no-verify", "git push --force origin work", "git push -f origin work", "git push origin +work",
                    "git push --mirror", "git push --all origin", "git push origin --delete work"):
            self.assertTrue(denied(self.settings, cmd), cmd)

    def test_work_that_only_looks_like_main_goes_through(self):
        for cmd in ("git push -u origin skip-census", "git push origin fix-main-menu", "git push origin maintenance", "git pull origin main",
                    "git fetch origin main", "git status", "npm test", "ls docs"):
            self.assertFalse(denied(self.settings, cmd), cmd)

    def test_env_files_keys_and_git_settings_are_denied_as_commands_too(self):
        for cmd in ("cat .env", "cat ../x/.env.local", "printenv", "env", "env | sort", "cat /proc/self/environ", "echo $OPENROUTER_API_KEY",
                    "python3 ~/.claude/scripts/install_scripts.py", "git config user.email a@b", "git -c core.hooksPath=/x commit -m x",
                    "GIT_CONFIG_GLOBAL=/x git commit -m x", "sed -i s/a/b/ ~/.gitconfig", "cp h .git/hooks/pre-commit", "cat id_rsa"):
            self.assertTrue(denied(self.settings, cmd), cmd)
        self.assertFalse(denied(self.settings, "environment"))

    def test_the_file_is_written_valid_json_and_a_missing_checkout_fails(self):
        dest = self.tmp / "agy-settings.json"
        run = subprocess.run([sys.executable, "-I", str(SCRIPTS / "agy_deny.py"), str(self.repo), str(dest)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(dest.read_text())["permissions"]["deny"][0].split("(")[0], "read_file")
        bad = subprocess.run([sys.executable, "-I", str(SCRIPTS / "agy_deny.py"), str(self.tmp / "nope"), str(self.tmp / "o.json")],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertFalse((self.tmp / "o.json").exists())


if __name__ == "__main__":
    unittest.main()
