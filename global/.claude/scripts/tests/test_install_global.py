import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import install_global as ig  # noqa: E402


class InstallGlobal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.repo)
        os.makedirs(self.home)
        self.git("init", "-q")

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True, env=env).stdout.decode().strip()

    def put(self, root, rel, text, mode=0o644):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, mode)

    def commit(self, **files):
        for rel, text in files.items():
            self.put(self.repo, "global/.claude/" + rel.replace("__", "/"), text)
        self.git("add", "-A")
        self.git("commit", "-qm", "c")
        return self.git("rev-parse", "HEAD")

    def live(self, rel):
        with open(os.path.join(self.home, rel)) as f:
            return f.read()

    def run_ig(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = ig.main(["--repo", self.repo, "--home", self.home, *args])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_copies_only_files_changed_between_the_commits(self):
        a = self.commit(scripts__x_py="x1", scripts__y_py="y1")
        self.put(self.home, "scripts/x_py", "x1")
        self.put(self.home, "scripts/y_py", "y1")
        self.commit(scripts__x_py="x2")
        # y was never touched after commit a; a live copy of it must stay exactly as it is, even though it differs from the repo
        self.put(self.home, "scripts/y_py", "y-local")
        code, out = self.run_ig("--from", a)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.live("scripts/x_py"), "x2")
        self.assertEqual(self.live("scripts/y_py"), "y-local")

    def test_refuses_a_live_edit_and_prints_its_name_and_does_not_advance_the_record(self):
        a = self.commit(scripts__x_py="x1", hooks__h_py="h1")
        self.put(self.home, "scripts/x_py", "x-live-edit")
        self.commit(scripts__x_py="x2", hooks__h_py="h2")
        self.put(self.home, "hooks/h_py", "h1")
        code, out = self.run_ig("--from", a)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED  global/.claude/scripts/x_py", out)
        self.assertEqual(self.live("scripts/x_py"), "x-live-edit")
        self.assertEqual(self.live("hooks/h_py"), "h2")
        self.assertFalse(os.path.exists(os.path.join(self.home, "state", ig.STATE_NAME)))

    def test_default_start_is_the_recorded_commit_and_success_records_head(self):
        a = self.commit(scripts__x_py="x1")
        self.put(self.home, "scripts/x_py", "x1")
        code, out = self.run_ig()
        self.assertEqual(code, 2)  # nothing recorded yet: it asks, it does not guess
        self.assertIn("no last install recorded", out)
        self.assertEqual(self.run_ig("--record", a)[0], 0)
        head = self.commit(scripts__x_py="x2", scripts__new_py="n")
        code, out = self.run_ig()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.live("scripts/x_py"), "x2")
        self.assertEqual(self.live("scripts/new_py"), "n")
        with open(os.path.join(self.home, "state", ig.STATE_NAME)) as f:
            self.assertEqual(f.read().strip(), head)

    def test_dry_run_changes_nothing_and_pycache_is_skipped(self):
        a = self.commit(scripts__x_py="x1")
        self.put(self.home, "scripts/x_py", "x1")
        self.put(self.repo, "global/.claude/scripts/__pycache__/x.cpython-312.pyc", "junk")
        self.put(self.repo, "global/.claude/scripts/stray.pyc", "junk")
        self.put(self.repo, "global/.claude/scripts/x_py", "x2")
        self.git("add", "-f", "-A")
        self.git("commit", "-qm", "junk")
        self.assertIn("pyc", self.git("ls-files"))  # the junk really is in the commit range
        code, out = self.run_ig("--from", a, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would copy  global/.claude/scripts/x_py", out)
        self.assertEqual(self.live("scripts/x_py"), "x1")
        self.assertFalse(os.path.exists(os.path.join(self.home, "scripts", "__pycache__")))
        code, out = self.run_ig("--from", a)
        self.assertNotIn("pyc", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "scripts", "stray.pyc")))

    def test_executable_bit_is_kept(self):
        a = self.commit(scripts__x_py="x1")
        self.put(self.repo, "global/.claude/scripts/run.sh", "#!/bin/sh\n", 0o755)
        self.git("add", "-A")
        self.git("commit", "-qm", "sh")
        self.assertEqual(self.run_ig("--from", a)[0], 0)
        self.assertTrue(os.access(os.path.join(self.home, "scripts", "run.sh"), os.X_OK))

    def test_status_tells_a_stale_file_from_a_live_edit(self):
        self.commit(scripts__stale_py="s1", scripts__edit_py="e1", scripts__ok_py="o1")
        self.commit(scripts__stale_py="s2", scripts__edit_py="e2")
        self.put(self.home, "scripts/stale_py", "s1")
        self.put(self.home, "scripts/edit_py", "e-live")
        self.put(self.home, "scripts/ok_py", "o1")
        code, out = self.run_ig("--status")
        self.assertEqual(code, 1)
        self.assertIn("stale      global/.claude/scripts/stale_py", out)
        self.assertIn("live edit  global/.claude/scripts/edit_py", out)
        self.assertNotIn("ok_py", out)

    def test_include_stale_reinstalls_a_stale_file_but_still_refuses_an_edit(self):
        self.commit(scripts__stale_py="s1", scripts__edit_py="e1")
        head = self.commit(scripts__stale_py="s2", scripts__edit_py="e2")
        self.put(self.home, "scripts/stale_py", "s1")
        self.put(self.home, "scripts/edit_py", "e-live")
        code, out = self.run_ig("--from", head, "--include-stale")
        self.assertEqual(code, 0, out)  # --from == --to: nothing changed between them, only the stale file is added
        self.assertEqual(self.live("scripts/stale_py"), "s2")
        self.assertEqual(self.live("scripts/edit_py"), "e-live")

    def gate_env(self, **env):
        old = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        os.environ.pop("INSTALL_GATE_OVERRIDE", None)
        os.environ.update(env)

    def test_a_placeholder_guard_is_refused_with_exit_4_and_copies_nothing(self):
        self.gate_env()
        self.commit(a__x="1")
        self.commit(**{"hooks__draft-recipient-guard.py": 'SELF = "owner@example.com"\n'})
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()) as err:
            ig.main(["--repo", self.repo, "--home", self.home, "--from", "HEAD~1"])
        self.assertEqual(cm.exception.code, 4)
        self.assertIn("identity-guard.json", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.home, "hooks", "draft-recipient-guard.py")))

    def test_a_guard_that_reads_the_private_file_is_not_refused(self):
        self.gate_env()
        self.commit(a__x="1")
        self.commit(**{"hooks__draft-recipient-guard.py": 'SELF = None  # identity-guard.json\n'})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ig.main(["--repo", self.repo, "--home", self.home, "--from", "HEAD~1"]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.home, "hooks", "draft-recipient-guard.py")))

    def test_the_override_switch_lets_a_person_install_anyway(self):
        self.gate_env(INSTALL_GATE_OVERRIDE="1")
        self.commit(a__x="1")
        self.commit(**{"hooks__draft-recipient-guard.py": 'SELF = "owner@example.com"\n'})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ig.main(["--repo", self.repo, "--home", self.home, "--from", "HEAD~1"]), 0)


if __name__ == "__main__":
    unittest.main()
