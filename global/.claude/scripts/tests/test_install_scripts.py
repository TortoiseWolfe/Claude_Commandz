"""install_scripts.py: the one command a person types to bring merged fleet scripts from origin/main into ~/.claude/scripts (issue 265).
Every repo, remote and "home" here is a throwaway folder; nothing touches the live ~/.claude. The script prints a per-file summary
(new, changed, same, DRIFT) before it changes anything, leaves the wall's own files alone, copies nothing for a file whose live copy
is not an older version from the repo's history, backs up what it replaces, and never reads the working tree."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "install_scripts.py"
sys.path.insert(0, str(HERE.parent))
import install_scripts as ins  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
TREE = "global/.claude/scripts"


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        root = Path(self._t.name)
        self.origin, self.dev, self.work, self.home = root / "origin.git", root / "dev", root / "work", root / "home"
        self.home.mkdir()
        self.sh("git", "-c", "init.defaultBranch=main", "init", "-q", "--bare", str(self.origin), cwd=root)
        self.sh("git", "clone", "-q", str(self.origin), str(self.dev), cwd=root)
        self.git(self.dev, "checkout", "-q", "-b", "main")
        self.first = True

    def sh(self, *cmd, cwd=None, input=None):
        return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV, input=input).stdout.strip()

    def git(self, repo, *args):
        return self.sh("git", "-C", str(repo), *args)

    def publish(self, files, modes=None):
        """Commit these {path under the scripts folder: text} to dev and push to origin main; return the commit."""
        for rel, text in files.items():
            p = self.dev / TREE / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            os.chmod(p, (modes or {}).get(rel, 0o644))
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-qm", "c")
        self.git(self.dev, "push", "-q", "origin", "main")
        if not self.work.exists():
            self.sh("git", "clone", "-q", str(self.origin), str(self.work))  # the installing checkout starts level with origin
        return self.git(self.dev, "rev-parse", "HEAD")

    def live(self, rel, text=None, mode=0o644):
        p = self.home / "scripts" / rel
        if text is None:
            return p.read_text() if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        os.chmod(p, mode)

    def run_ins(self, *args, input=None):
        p = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--repo", str(self.work), "--home", str(self.home), *args],
            capture_output=True, text=True, input=input if input is not None else "", env=GIT_ENV,
        )
        return p


class Summary(Base):
    def test_classifies_new_changed_same_and_drift(self):
        self.publish({"a.py": "a1", "b.py": "b1", "c.py": "c1", "d.py": "d1"})
        self.publish({"b.py": "b2", "c.py": "c2"})
        self.live("a.py", "a1")                  # equals the repo's latest -> same
        self.live("b.py", "b1")                  # an older repo version -> changed
        self.live("c.py", "c-local-edit")        # matches no version -> drift
        # d.py has no live copy -> new
        p = self.run_ins("--dry-run")
        self.assertIn("new      d.py", p.stdout)
        self.assertIn("changed  b.py", p.stdout)
        self.assertIn("DRIFT    c.py", p.stdout)
        self.assertNotIn("a.py", p.stdout.split("\n\n")[1])  # same files are counted, not listed
        self.assertIn("1 new, 1 changed, 1 same", p.stdout)
        self.assertIn("1 drifted", p.stdout)

    def test_list_same_names_the_same_files(self):
        self.publish({"a.py": "a1"})
        self.live("a.py", "a1")
        self.assertIn("same     a.py", self.run_ins("--dry-run", "--list-same").stdout)

    def test_dry_run_changes_nothing_and_says_so(self):
        self.publish({"a.py": "a1"})
        self.publish({"a.py": "a2"})
        self.live("a.py", "a1")
        p = self.run_ins("--dry-run")
        self.assertEqual(p.returncode, 0)
        self.assertIn("DRY RUN", p.stdout)
        self.assertEqual(self.live("a.py"), "a1")
        self.assertFalse((self.home / "state").exists())

    def test_the_summary_is_printed_before_the_prompt(self):
        self.publish({"a.py": "a1"})
        p = self.run_ins(input="n\n")
        self.assertLess(p.stdout.index("new      a.py"), p.stdout.index("Install? [y/N]"))

    def test_names_the_commit_it_reads(self):
        sha = self.publish({"a.py": "a1"})
        self.assertIn(f"origin/main {sha[:7]}", self.run_ins("--dry-run").stdout)


class Drift(Base):
    def test_a_drifted_file_is_never_copied_over_but_the_others_are(self):
        self.publish({"a.py": "a1", "b.py": "b1"})
        self.publish({"a.py": "a2", "b.py": "b2"})
        self.live("a.py", "a1")
        self.live("b.py", "hand-edited")
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.live("a.py"), "a2")
        self.assertEqual(self.live("b.py"), "hand-edited")
        self.assertIn("DRIFT    b.py", p.stdout)
        self.assertIn("drift 1", p.stdout.strip().splitlines()[-1])

    def test_a_live_file_equal_to_any_older_version_counts_as_an_ancestor(self):
        self.publish({"a.py": "v1"})
        self.publish({"a.py": "v2"})
        self.publish({"a.py": "v3"})
        self.live("a.py", "v1")
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.live("a.py"), "v3")

    def test_a_version_that_never_reached_origin_is_drift(self):
        self.publish({"a.py": "v1"})
        (self.work / TREE).mkdir(parents=True, exist_ok=True)
        (self.work / TREE / "a.py").write_text("local-only")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-qm", "local only, never pushed")
        self.publish({"a.py": "v2"})
        self.live("a.py", "local-only")
        p = self.run_ins("--yes")
        self.assertIn("DRIFT    a.py", p.stdout)
        self.assertEqual(self.live("a.py"), "local-only")

    def test_nothing_to_copy_but_drift_still_exits_1(self):
        self.publish({"a.py": "a1"})
        self.live("a.py", "other")
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing to copy", p.stdout)


class Wall(Base):
    FILES = {
        "no_ai_wall.py": "repo wall module",
        "no_ai_wall.sh": "repo wall helper",
        "no-ai-wall-guard.py": "repo wall hook",
        "settings.json": "{}",
        "settings.local.json": "{}",
        "tests/test_no_ai_wall_install.py": "wall test",
        "lane.sh": "lane v1",
    }

    def test_wall_and_settings_files_are_never_touched(self):
        self.publish(self.FILES)
        self.live("no_ai_wall.py", "live wall module")
        self.live("no-ai-wall-guard.py", "live wall hook")
        self.live("settings.json", "live settings")
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.live("no_ai_wall.py"), "live wall module")
        self.assertEqual(self.live("no-ai-wall-guard.py"), "live wall hook")
        self.assertEqual(self.live("settings.json"), "live settings")
        self.assertIsNone(self.live("no_ai_wall.sh"))          # not even created
        self.assertIsNone(self.live("settings.local.json"))
        self.assertIsNone(self.live("tests/test_no_ai_wall_install.py"))
        self.assertEqual(self.live("lane.sh"), "lane v1")      # an ordinary file still installs

    def test_wall_files_are_listed_as_skipped_and_not_called_drift(self):
        self.publish(self.FILES)
        self.live("no_ai_wall.py", "live wall module")
        out = self.run_ins("--dry-run").stdout
        self.assertIn("skipped  no_ai_wall.py", out)
        self.assertNotIn("DRIFT    no_ai_wall.py", out)

    def test_wall_files_are_not_backed_up_or_read(self):
        self.publish(self.FILES)
        self.live("no_ai_wall.py", "live wall module")
        self.run_ins("--yes")
        backed = [p.name for p in (self.home / "state").rglob("*") if p.is_file()]
        self.assertNotIn("no_ai_wall.py", backed)
        self.assertNotIn("settings.json", backed)

    def test_the_name_rule(self):
        for name in ("no_ai_wall.py", "no_ai_wall.sh", "no-ai-wall-guard.py", "settings.json", "settings.local.json",
                     "tests/test_no_ai_wall_install.py", "x/no-ai-paths.txt"):
            self.assertTrue(ins.is_wall_file(name), name)
        for name in ("lane.sh", "install_global.py", "tests/test_lane_recycle.sh", "install_scripts.py"):
            self.assertFalse(ins.is_wall_file(name), name)


class BackupAndUndo(Base):
    def test_a_replaced_file_is_backed_up_and_undo_restores_everything(self):
        self.publish({"a.py": "a1", "new.py": "n1"})
        self.publish({"a.py": "a2"})
        self.live("a.py", "a1", 0o755)
        p = self.run_ins("--yes")
        self.assertEqual(self.live("a.py"), "a2")
        self.assertEqual(self.live("new.py"), "n1")
        bk = next((self.home / "state" / "scripts-install-backups").iterdir())
        self.assertEqual((bk / "files" / "scripts" / "a.py").read_text(), "a1")
        self.assertIn(str(bk), p.stdout)
        r = subprocess.run(["bash", str(bk / "undo.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.live("a.py"), "a1")
        self.assertIsNone(self.live("new.py"))   # a file the install created is removed again
        self.assertEqual(stat.S_IMODE(os.stat(self.home / "scripts" / "a.py").st_mode), 0o755)

    def test_nothing_is_backed_up_for_a_dry_run_or_a_refusal(self):
        self.publish({"a.py": "a1"})
        self.run_ins("--dry-run")
        self.run_ins(input="n\n")
        self.assertFalse((self.home / "state").exists())

    def test_a_failure_part_way_puts_every_file_back(self):
        self.publish({"a.py": "a1", "b.py": "b1"})
        self.publish({"a.py": "a2", "b.py": "b2"})
        self.live("a.py", "a1")
        self.live("b.py", "b1")
        real = ins.put
        calls = []

        def flaky(repo, home, rel, mode, sha):
            calls.append(rel)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(repo, home, rel, mode, sha)

        ins.put = flaky
        try:
            import io
            from contextlib import redirect_stdout
            out = io.StringIO()
            with redirect_stdout(out):
                code = ins.main(["--repo", str(self.work), "--home", str(self.home), "--yes"])   # fetches: the clone was made before the second publish
        finally:
            ins.put = real
        self.assertEqual(code, 2)
        self.assertIn("INSTALL FAILED", out.getvalue())
        self.assertIn("after undoing itself", out.getvalue())
        self.assertEqual(self.live("a.py"), "a1")
        self.assertEqual(self.live("b.py"), "b1")


class Source(Base):
    def test_it_fetches_origin_so_a_new_merge_is_installed(self):
        self.publish({"a.py": "a1"})
        self.run_ins("--yes")
        self.publish({"a.py": "a2"})          # merged on origin after the work checkout last fetched
        self.assertEqual(self.run_ins("--yes").returncode, 0)
        self.assertEqual(self.live("a.py"), "a2")

    def test_no_fetch_reads_what_the_checkout_already_has(self):
        self.publish({"a.py": "a1"})
        self.publish({"a.py": "a2"})          # the work checkout has not fetched this
        self.run_ins("--yes", "--no-fetch")
        self.assertEqual(self.live("a.py"), "a1")

    def test_the_working_tree_is_never_read(self):
        self.publish({"a.py": "from origin"})
        (self.work / TREE).mkdir(parents=True, exist_ok=True)
        (self.work / TREE / "a.py").write_text("uncommitted edit in the working tree")
        self.run_ins("--yes")
        self.assertEqual(self.live("a.py"), "from origin")

    def test_a_failed_fetch_changes_nothing_and_says_so(self):
        self.publish({"a.py": "a1"})
        shutil.rmtree(self.origin)
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 2)
        self.assertIn("INSTALL FAILED", p.stdout)
        self.assertIsNone(self.live("a.py"))

    def test_a_folder_that_is_not_a_checkout_is_refused(self):
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--repo", str(self.home), "--home", str(self.home)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("INSTALL FAILED", p.stdout)

    def test_the_executable_bit_follows_the_repo(self):
        self.publish({"run.sh": "#!/bin/sh\n", "mod.py": "x"}, modes={"run.sh": 0o755})
        self.run_ins("--yes")
        self.assertTrue(os.access(self.home / "scripts" / "run.sh", os.X_OK))
        self.assertFalse(os.access(self.home / "scripts" / "mod.py", os.X_OK))

    def test_bytecode_caches_are_ignored(self):
        self.publish({"__pycache__/a.cpython-312.pyc": "junk", "a.py": "a1"})
        self.run_ins("--yes")
        self.assertIsNone(self.live("__pycache__/a.cpython-312.pyc"))

    def test_live_files_the_repo_does_not_have_are_left_alone(self):
        self.publish({"a.py": "a1"})
        self.live("only_here.py", "mine")
        self.run_ins("--yes")
        self.assertEqual(self.live("only_here.py"), "mine")

    def test_the_documented_one_line_command_works(self):
        """The command in the docstring and README: fetch, then run the copy of the script that sits on origin/main."""
        self.publish({"install_scripts.py": SCRIPT.read_text(), "a.py": "a1"})
        cmd = (f"git -C {self.work} fetch -q origin main && python3 -I <(git -C {self.work} show origin/main:{TREE}/install_scripts.py)"
               f" --repo {self.work} --home {self.home} --no-fetch --yes")
        p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("INSTALL OK", p.stdout)
        self.assertEqual(self.live("a.py"), "a1")


class Confirm(Base):
    def test_no_leaves_everything_alone(self):
        self.publish({"a.py": "a1"})
        p = self.run_ins(input="n\n")
        self.assertEqual(p.returncode, 3)
        self.assertIsNone(self.live("a.py"))

    def test_end_of_input_counts_as_no(self):
        self.publish({"a.py": "a1"})
        self.assertEqual(self.run_ins(input="").returncode, 3)
        self.assertIsNone(self.live("a.py"))

    def test_yes_installs(self):
        self.publish({"a.py": "a1"})
        p = self.run_ins(input="y\n")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.live("a.py"), "a1")
        self.assertIn("INSTALL OK", p.stdout.strip().splitlines()[-1])

    def test_the_last_line_counts_every_class(self):
        self.publish({"a.py": "a1", "b.py": "b1"})
        self.live("b.py", "b1")
        last = self.run_ins("--yes").stdout.strip().splitlines()[-1]
        self.assertRegex(last, r"^INSTALL OK [0-9a-f]{7} new 1 changed 0 same 1 drift 0 held 0$")


LANE_LIKE = '#!/usr/bin/env bash\nNO_AI_WALL_SH="$HOME/.claude/no-ai-wall/no_ai_wall.sh"\n[ -r "$NO_AI_WALL_SH" ] || { echo refusing; exit 1; }\n'


class WallHome(Base):
    """6 Nov: a lane.sh that reads ~/.claude/no-ai-wall/no_ai_wall.sh was installed on a machine with no such folder and every
    lane.sh call refused to run. A script that needs the wall's home folder is held back until the folder is complete."""

    def wall_home(self, skip=(), empty=()):
        d = self.home / "no-ai-wall"
        d.mkdir(parents=True, exist_ok=True)
        for f in ins.WALL_HOME_FILES:
            if f not in skip:
                (d / f).write_text("" if f in empty else "x\n")

    def test_a_script_that_reads_the_wall_is_held_when_the_folder_is_missing(self):
        self.publish({"lane.sh": LANE_LIKE, "plain.py": "p1"})
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIsNone(self.live("lane.sh"))                    # not copied
        self.assertEqual(self.live("plain.py"), "p1")              # an ordinary script still goes in
        self.assertRegex(p.stdout, r"HELD\s+lane\.sh")
        self.assertIn("global/.claude/no-ai-wall/install.sh", p.stdout)
        self.assertIn("settings.json", p.stdout)                   # says what the wall install changes
        self.assertRegex(p.stdout.strip().splitlines()[-1], r"^INSTALL OK [0-9a-f]{7} new 1 changed 0 same 0 drift 0 held 1$")

    def test_the_help_names_the_exact_commit_to_install_the_wall_from(self):
        sha = self.publish({"lane.sh": LANE_LIKE})
        p = self.run_ins("--dry-run")
        self.assertIn(f"show {sha}:global/.claude/no-ai-wall/install.sh) {sha}", p.stdout)

    def test_an_incomplete_folder_still_holds_it(self):
        self.publish({"lane.sh": LANE_LIKE})
        self.wall_home(skip=("no-ai-wall-guard.py",))
        self.assertRegex(self.run_ins("--yes").stdout, r"HELD\s+lane\.sh.*no-ai-wall-guard\.py")
        self.assertIsNone(self.live("lane.sh"))

    def test_an_empty_wall_file_counts_as_missing(self):
        self.publish({"lane.sh": LANE_LIKE})
        self.wall_home(empty=("no_ai_wall.sh",))
        self.assertRegex(self.run_ins("--yes").stdout, r"HELD\s+lane\.sh")

    def test_a_complete_folder_lets_it_install(self):
        self.publish({"lane.sh": LANE_LIKE})
        self.wall_home()
        p = self.run_ins("--yes")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.live("lane.sh"), LANE_LIKE)
        self.assertNotIn("HELD", p.stdout)

    def test_a_held_script_that_is_already_live_is_left_alone(self):
        self.publish({"lane.sh": "old\n"})
        self.live("lane.sh", "old\n")
        self.publish({"lane.sh": LANE_LIKE})
        self.run_ins("--yes")
        self.assertEqual(self.live("lane.sh"), "old\n")

    def test_the_repos_real_lane_sh_needs_the_wall_so_this_guard_covers_it(self):
        lane = HERE.parent / "lane.sh"
        self.assertTrue(lane.exists())
        self.assertTrue(ins.WALL_USE.search(lane.read_bytes()), "lane.sh no longer names the wall's home folder: update WALL_USE")

    def test_the_real_lane_sh_is_held_in_a_home_with_no_wall_folder(self):
        self.publish({"lane.sh": (HERE.parent / "lane.sh").read_text()})
        p = self.run_ins("--yes")
        self.assertRegex(p.stdout, r"HELD\s+lane\.sh")
        self.assertIsNone(self.live("lane.sh"))


class Docs(unittest.TestCase):
    def test_the_readme_gives_the_command(self):
        text = (HERE.parent.parent.parent.parent / "README.md").read_text()
        self.assertIn("install_scripts.py", text)
        self.assertIn("fetch -q origin main", text)

    def test_the_repo_instructions_name_the_installer(self):
        text = (HERE.parent.parent.parent.parent / "CLAUDE.md").read_text()
        self.assertIn("install_scripts.py", text)


if __name__ == "__main__":
    unittest.main()
