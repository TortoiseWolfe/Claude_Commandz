"""The person-run install of the no-AI wall (global/.claude/no-ai-wall/install.sh), tried on a throwaway git repo and a throwaway HOME.

The repo has two commits that matter: the old layout (the wall's hook in hooks/, its module and helper in scripts/) and the new one (the real files from
this checkout, in their own folder). Nothing here touches the real ~/.claude, the real list or the network; the list entries are made up.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GLOBAL = os.path.normpath(os.path.join(HERE, "..", ".."))          # .../global/.claude
WALL_SRC = os.path.join(GLOBAL, "no-ai-wall")
INSTALL = os.path.join(WALL_SRC, "install.sh")
INSTALL_GLOBAL = os.path.join(GLOBAL, "scripts", "install_global.py")
WALL_FILES = ("no_ai_wall.py", "no_ai_wall.sh", "no-ai-wall-guard.py")

OLD_SETTINGS = {
    "model": "keep-me",
    "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python3 -I ~/.claude/hooks/no-ai-wall-guard.py", "timeout": 10}]}]},
}
OLD_LANE = "#!/usr/bin/env bash\n# old launcher\n. \"$SCRIPT_DIR/no_ai_wall.sh\"\n"
NEW_LANE = "#!/usr/bin/env bash\n# new launcher\n. \"$HOME/.claude/no-ai-wall/no_ai_wall.sh\"\n"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wall-install-test.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.home = os.path.join(self.tmp, "home")
        self.live = os.path.join(self.home, ".claude")
        self.list = os.path.join(self.home, ".config", "afa", "no-ai-paths.txt")
        os.makedirs(self.repo)
        self.git("init", "-q")
        # commit A: the old layout
        self.put_repo("hooks/no-ai-wall-guard.py", "# old hook\n")
        self.put_repo("scripts/no_ai_wall.py", "# old module\n")
        self.put_repo("scripts/no_ai_wall.sh", "# old helper\n")
        self.put_repo("scripts/lane.sh", OLD_LANE)
        self.put_repo("scripts/install_global.py", open(INSTALL_GLOBAL).read())
        self.A = self.commit("old layout")
        # commit B: the new layout, the real files from this checkout
        for f in WALL_FILES + ("smoke.py", "install.sh"):
            self.put_repo("no-ai-wall/" + f, open(os.path.join(WALL_SRC, f)).read())
        self.put_repo("scripts/lane.sh", NEW_LANE)
        for gone in ("hooks/no-ai-wall-guard.py", "scripts/no_ai_wall.py", "scripts/no_ai_wall.sh"):
            os.remove(os.path.join(self.repo, "global/.claude", gone))
        self.B = self.commit("new layout")
        # the live machine as it is before the install: commit A's files, the settings, a made-up list
        self.settings_text = json.dumps(OLD_SETTINGS, indent=2) + "\n"
        self.git("checkout", "-q", self.A)
        for rel in ("hooks/no-ai-wall-guard.py", "scripts/no_ai_wall.py", "scripts/no_ai_wall.sh", "scripts/lane.sh", "scripts/install_global.py"):
            self.put_live(rel, open(os.path.join(self.repo, "global/.claude", rel)).read())
        self.git("checkout", "-q", "-")
        self.put_live("settings.json", self.settings_text)
        self.put_live("state/install-global-commit", self.A + "\n")
        os.makedirs(os.path.dirname(self.list))
        with open(self.list, "w") as fh:
            fh.write("# made up\n%s/synthetic-client-alpha\n~/synthetic-client-beta/*\n" % self.home)

    # -- helpers
    def git(self, *args):
        r = run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args])
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def put_repo(self, rel, text):
        p = os.path.join(self.repo, "global/.claude", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def put_live(self, rel, text):
        p = os.path.join(self.live, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def snapshot(self):
        """Every file under the live folder (except the backups) as path -> (bytes, mode)."""
        out = {}
        for root, _, files in os.walk(self.live):
            for f in files:
                p = os.path.join(root, f)
                rel = os.path.relpath(p, self.live)
                if rel.startswith("state/no-ai-wall-backups"):
                    continue
                out[rel] = (open(p, "rb").read(), stat.S_IMODE(os.stat(p).st_mode))
        return out

    def install(self, sha=None, script=INSTALL):
        env = {k: v for k, v in os.environ.items() if k not in ("NO_AI_PATHS_FILE", "NO_AI_REFUSAL_LOG", "PYTHONPATH")}
        env.update(HOME=self.home, WALL_INSTALL_REPO=self.repo)
        return run(["bash", script, sha or self.B], env=env, cwd=self.tmp)

    def last(self, r):
        return r.stdout.strip().splitlines()[-1]

    def backups(self):
        d = os.path.join(self.live, "state", "no-ai-wall-backups")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    # -- the good run
    def test_installs_the_new_layout_and_ends_on_the_one_line(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(self.last(r), r"^WALL OK %s refused (\d+)/\1 ordinary (\d+)/\2$" % self.B[:7])
        m = re.match(r"WALL OK \S+ refused (\d+)/", self.last(r))
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)) if m else 0, 2 + 1 + 5)  # two list entries, the bash helper, five tamper probes
        wall = os.path.join(self.live, "no-ai-wall")
        self.assertEqual(sorted(os.listdir(wall)), sorted(WALL_FILES))  # the wall's three files and nothing else
        for f in WALL_FILES:
            self.assertEqual(open(os.path.join(wall, f)).read(), open(os.path.join(WALL_SRC, f)).read())
        for gone in ("hooks/no-ai-wall-guard.py", "scripts/no_ai_wall.py", "scripts/no_ai_wall.sh"):
            self.assertFalse(os.path.exists(os.path.join(self.live, gone)), gone)
        self.assertEqual(open(os.path.join(self.live, "scripts/lane.sh")).read(), NEW_LANE)  # the other global files came along
        self.assertEqual(open(os.path.join(self.live, "state/install-global-commit")).read().strip(), self.B)
        self.assertNotIn("install.sh", os.listdir(wall))  # the installer and its smoke test stay in the repo
        self.assertNotIn("smoke.py", os.listdir(wall))

    def test_settings_hook_entry_moves_and_nothing_else_in_the_file_changes(self):
        self.assertEqual(self.install().returncode, 0)
        new = open(os.path.join(self.live, "settings.json")).read()
        self.assertEqual(new, self.settings_text.replace("hooks/no-ai-wall-guard.py", "no-ai-wall/no-ai-wall-guard.py"))
        self.assertEqual(json.loads(new)["model"], "keep-me")

    def test_a_settings_file_with_no_wall_entry_gets_one(self):
        self.put_live("settings.json", json.dumps({"model": "keep-me", "hooks": {}}) + "\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout)
        cfg = json.load(open(os.path.join(self.live, "settings.json")))
        cmds = [h["command"] for g in cfg["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(cmds, ["python3 -I ~/.claude/no-ai-wall/no-ai-wall-guard.py"])
        self.assertEqual(cfg["model"], "keep-me")

    def test_backup_folder_and_undo_script_are_written_and_the_undo_restores_everything(self):
        before = self.snapshot()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout)
        names = self.backups()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^\d{8}-\d{6}-%s" % self.B[:7])
        bk = os.path.join(self.live, "state", "no-ai-wall-backups", names[0])
        self.assertTrue(os.path.isfile(os.path.join(bk, "undo.sh")))
        self.assertIn("undo.sh", r.stdout)  # it tells the person where the undo is
        self.assertNotEqual(self.snapshot(), before)
        u = run(["bash", os.path.join(bk, "undo.sh")])
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        self.assertEqual(self.snapshot(), before)  # byte for byte and mode for mode
        self.assertFalse(os.path.exists(os.path.join(self.live, "no-ai-wall")))

    def test_running_it_twice_is_fine(self):
        self.assertEqual(self.install().returncode, 0)
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(self.last(r), r"^WALL OK ")
        self.assertEqual(len(self.backups()), 2)

    def test_it_runs_from_the_commit_it_installs(self):
        """The one command is: bash <(git show <commit>:.../install.sh) <commit>."""
        script = os.path.join(self.tmp, "from-git.sh")
        with open(script, "w") as fh:
            fh.write(self.git("show", "%s:global/.claude/no-ai-wall/install.sh" % self.B) + "\n")
        r = self.install(script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(self.last(r), r"^WALL OK ")

    def test_files_come_from_the_commit_never_the_working_tree(self):
        with open(os.path.join(self.repo, "global/.claude/no-ai-wall/smoke.py"), "w") as fh:
            fh.write("raise SystemExit('a working-tree edit that was never committed')\n")
        with open(os.path.join(self.repo, "global/.claude/no-ai-wall/no_ai_wall.py"), "a") as fh:
            fh.write("\nUNCOMMITTED = True\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("UNCOMMITTED", open(os.path.join(self.live, "no-ai-wall", "no_ai_wall.py")).read())

    def test_the_script_never_uses_the_network(self):
        text = open(INSTALL).read()
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        self.assertIsNone(re.search(r"\b(git\s+(fetch|pull|clone|remote)|curl|wget|ssh|scp|nc)\b", code))
        self.assertIn("git -C \"$REPO\" archive", code)

    def test_no_list_means_a_stand_in_list_and_a_note(self):
        os.remove(self.list)
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(self.last(r), r"^WALL OK ")
        self.assertIn("no-ai-paths.txt", r.stdout)  # says the wall is off on this machine until a list exists

    def test_the_list_is_never_printed(self):
        r = self.install()
        self.assertNotIn("synthetic-client", r.stdout + r.stderr)

    # -- the failures
    def test_an_unknown_commit_changes_nothing(self):
        before = self.snapshot()
        r = self.install("0" * 40)
        self.assertEqual(r.returncode, 1)
        self.assertRegex(self.last(r), r"^WALL FAILED .*; nothing had been changed yet$")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.backups(), [])

    def test_a_commit_without_the_wall_changes_nothing(self):
        before = self.snapshot()
        r = self.install(self.A)
        self.assertRegex(self.last(r), r"^WALL FAILED .*; nothing had been changed yet$")
        self.assertEqual(self.snapshot(), before)

    def test_a_module_that_does_not_compile_changes_nothing(self):
        self.put_repo("no-ai-wall/no_ai_wall.py", "def broken(:\n")
        bad = self.commit("broken module")
        before = self.snapshot()
        r = self.install(bad)
        self.assertRegex(self.last(r), r"^WALL FAILED .*; nothing had been changed yet$")
        self.assertEqual(self.snapshot(), before)

    def test_a_hook_that_lets_everything_through_fails_the_smoke_test_and_the_install_undoes_itself(self):
        self.put_repo("no-ai-wall/no-ai-wall-guard.py", "import sys\nsys.exit(0)\n")
        bad = self.commit("a hook that allows everything")
        before = self.snapshot()
        r = self.install(bad)
        self.assertEqual(r.returncode, 1)
        self.assertRegex(self.last(r), r"^WALL FAILED .* after undoing itself$")
        self.assertEqual(self.snapshot(), before)

    def test_a_live_edit_of_lane_sh_is_not_overwritten_and_the_install_undoes_itself(self):
        self.put_live("scripts/lane.sh", OLD_LANE + "# someone's live edit\n")
        before = self.snapshot()
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertRegex(self.last(r), r"^WALL FAILED .*lane\.sh.* after undoing itself$")
        self.assertEqual(self.snapshot(), before)  # the edit is still there, and so is the old wall

    def test_a_stale_lane_sh_is_brought_up_to_date(self):
        """A live file that equals an earlier committed version (the machine missed an install) is stale, not a live edit: it is updated."""
        self.put_live("state/install-global-commit", self.B + "\n")  # the record is ahead of the live launcher
        self.put_live("scripts/lane.sh", OLD_LANE)                   # which still equals commit A's version
        self.put_repo("scripts/lane.sh", NEW_LANE + "# v2\n")
        newer = self.commit("launcher v2")
        r = self.install(newer)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(self.last(r), r"^WALL OK ")
        self.assertEqual(open(os.path.join(self.live, "scripts/lane.sh")).read(), NEW_LANE + "# v2\n")

    def test_a_launcher_that_still_reads_the_old_folder_fails_and_undoes_itself(self):
        self.put_repo("scripts/lane.sh", OLD_LANE + "# still the old path\n")
        bad = self.commit("launcher not moved")
        before = self.snapshot()
        r = self.install(bad)
        self.assertRegex(self.last(r), r"^WALL FAILED .* after undoing itself$")
        self.assertEqual(self.snapshot(), before)

    def test_no_argument_is_a_usage_failure(self):
        env = dict(os.environ, HOME=self.home, WALL_INSTALL_REPO=self.repo)
        r = run(["bash", INSTALL], env=env)
        self.assertEqual(r.returncode, 1)
        self.assertRegex(self.last(r), r"^WALL FAILED usage")


if __name__ == "__main__":
    unittest.main()
