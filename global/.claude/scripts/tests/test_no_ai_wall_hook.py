"""The no-AI wall's PreToolUse hook, run as Claude Code runs it: JSON on stdin, deny = exit 2 with the reason on stderr. Fake folders only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks", "no-ai-wall-guard.py")


class HookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        for d in ("home", "wall/clientA", "work"):
            os.makedirs(os.path.join(self.tmp, d))
        open(os.path.join(self.tmp, "wall/clientA/secret.txt"), "w").write("x")
        self.pfile = os.path.join(self.tmp, "paths.txt")
        self.log = os.path.join(self.tmp, "state", "refusals.log")  # the folder does not exist yet: the hook makes it
        open(self.pfile, "w").write(os.path.join(self.tmp, "wall", "clientA") + "\n")
        self.env = dict(os.environ, HOME=os.path.join(self.tmp, "home"), NO_AI_PATHS_FILE=self.pfile,
                        NO_AI_REFUSAL_LOG=self.log)

    def run_hook(self, tool, cwd=None, **ti):
        payload = {"tool_name": tool, "tool_input": ti, "cwd": cwd or os.path.join(self.tmp, "work")}
        return subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True, env=self.env)

    def test_denies_a_listed_read_with_exit_2_and_logs_it(self):
        r = self.run_hook("Read", file_path=os.path.join(self.tmp, "wall/clientA/secret.txt"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no-AI list", r.stderr)
        self.assertEqual(r.stdout, "")
        line = open(self.log).read().strip().split("\t")
        self.assertEqual(len(line), 3)
        self.assertEqual(line[1:], ["Read", "entry 1"])  # the number, never the path
        self.assertNotIn("clientA", r.stderr + open(self.log).read())

    def test_allows_an_ordinary_call_and_logs_nothing(self):
        r = self.run_hook("Read", file_path=os.path.join(self.tmp, "work", "a.txt"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertFalse(os.path.exists(self.log))

    def test_bash_and_search_denials_are_logged_with_their_tool(self):
        self.assertEqual(self.run_hook("Bash", command="cat %s/wall/clientA/secret.txt" % self.tmp).returncode, 2)
        r = self.run_hook("Grep", pattern="x", path=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("entry 1", r.stderr)  # a search over a parent names the entry by number
        self.assertNotIn("clientA", r.stderr)
        tools = [l.split("\t")[1] for l in open(self.log).read().splitlines()]
        self.assertEqual(tools, ["Bash", "Grep"])

    def test_log_never_holds_file_content_or_the_command(self):
        marker = "TOPSECRETWORDS"
        self.run_hook("Bash", command="echo %s > %s/wall/clientA/x.txt" % (marker, self.tmp))
        self.assertNotIn(marker, open(self.log).read())

    def test_session_in_a_listed_folder_is_denied(self):
        r = self.run_hook("Bash", cwd=os.path.join(self.tmp, "wall/clientA"), command="echo hi")
        self.assertEqual(r.returncode, 2)

    def test_mcp_tool_in_a_listed_folder_and_with_a_listed_path(self):
        self.assertEqual(self.run_hook("mcp__x__ping", cwd=os.path.join(self.tmp, "wall/clientA"), q="hi").returncode, 2)
        self.assertEqual(self.run_hook("mcp__x__read", p=os.path.join(self.tmp, "wall/clientA/secret.txt")).returncode, 2)
        self.assertEqual(self.run_hook("mcp__x__ping", q="hi").returncode, 0)

    def test_the_list_and_the_hook_cannot_be_edited_from_a_session(self):
        r = self.run_hook("Edit", file_path=self.pfile)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(self.pfile, r.stderr)
        self.assertEqual(self.run_hook("Bash", command="echo x >> " + self.pfile).returncode, 2)

    def test_missing_list_means_the_wall_is_off(self):
        os.remove(self.pfile)
        r = self.run_hook("Read", file_path=os.path.join(self.tmp, "wall/clientA/secret.txt"))
        self.assertEqual(r.returncode, 0)

    def test_unreadable_list_fails_closed(self):
        open(self.pfile, "w").write("this is not a path\n")
        r = self.run_hook("Bash", command="echo hi")
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot be read", r.stderr)

    def test_garbage_stdin_does_not_crash_or_deny(self):
        r = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_fast_enough_for_every_tool_call(self):
        start = time.time()
        for _ in range(10):
            self.run_hook("Bash", command="ls -la; git status && echo done")
        self.assertLess((time.time() - start) / 10, 0.5)  # a Python start-up and two small files, no network


class HookIsolationTests(unittest.TestCase):
    """The hook must not run code planted in a folder the user can write: not a stdlib name, not the wall module."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.home = os.path.join(self.tmp, "home")
        self.hooks = os.path.join(self.home, ".claude", "hooks")
        self.scripts = os.path.join(self.home, ".claude", "scripts")
        for d in (self.hooks, self.scripts, os.path.join(self.tmp, "wall/clientA"), os.path.join(self.tmp, "work"), os.path.join(self.tmp, "evil")):
            os.makedirs(d)
        src = os.path.dirname(HOOK)
        shutil.copy(HOOK, os.path.join(self.hooks, "no-ai-wall-guard.py"))
        shutil.copy(os.path.join(src, "..", "scripts", "no_ai_wall.py"), os.path.join(self.scripts, "no_ai_wall.py"))
        self.hook = os.path.join(self.hooks, "no-ai-wall-guard.py")
        self.pfile = os.path.join(self.tmp, "paths.txt")
        open(self.pfile, "w").write(os.path.join(self.tmp, "wall", "clientA") + "\n")
        self.marker = os.path.join(self.tmp, "planted-code-ran")
        self.env = dict(os.environ, HOME=self.home, NO_AI_PATHS_FILE=self.pfile, NO_AI_REFUSAL_LOG=os.path.join(self.tmp, "r.log"))
        self.env.pop("PYTHONPATH", None)

    def plant(self, folder, name):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write("open(%r, 'w').write('ran')\nimport sys\nsys.exit(0)\n" % self.marker)

    def run_hook(self, tool="Read", env=None, **ti):
        payload = {"tool_name": tool, "tool_input": ti, "cwd": os.path.join(self.tmp, "work")}
        return subprocess.run([sys.executable, self.hook], input=json.dumps(payload), capture_output=True, text=True, env=env or self.env)

    def listed_read(self):
        return dict(file_path=os.path.join(self.tmp, "wall/clientA/x.txt"))

    def lone_hook(self):
        lone = os.path.join(self.tmp, "lone")
        os.makedirs(lone)
        shutil.copy(HOOK, os.path.join(lone, "no-ai-wall-guard.py"))
        self.hook = os.path.join(lone, "no-ai-wall-guard.py")

    def test_a_planted_bytecode_cache_of_the_module_is_never_read(self):
        import importlib.util
        import importlib._bootstrap_external as be
        real = os.path.join(self.scripts, "no_ai_wall.py")
        st = os.stat(real)
        evil = compile("open(%r, 'w').write('ran')\nimport sys\nsys.exit(0)\n" % self.marker, real, "exec")
        pyc = importlib.util.cache_from_source(real)
        os.makedirs(os.path.dirname(pyc), exist_ok=True)
        with open(pyc, "wb") as fh:  # a cache whose header matches the real source, so a normal import would trust it
            fh.write(be._code_to_timestamp_pyc(evil, int(st.st_mtime), st.st_size))
        r = self.run_hook(**self.listed_read())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(self.marker))

    def test_baseline_installed_copy_denies(self):
        self.assertEqual(self.run_hook(**self.listed_read()).returncode, 2)

    def test_a_stdlib_name_planted_beside_the_hook_does_not_run(self):
        for name in ("json.py", "re.py", "shlex.py", "glob.py", "datetime.py"):
            self.plant(self.hooks, name)
        r = self.run_hook(**self.listed_read())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(self.marker))

    def test_a_stdlib_name_planted_in_the_scripts_folder_does_not_run(self):
        for name in ("json.py", "re.py", "shlex.py", "glob.py", "datetime.py"):
            self.plant(self.scripts, name)
        r = self.run_hook(**self.listed_read())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(self.marker))

    def test_a_module_planted_on_pythonpath_does_not_run(self):
        for name in ("json.py", "re.py", "no_ai_wall.py"):
            self.plant(os.path.join(self.tmp, "evil"), name)
        env = dict(self.env, PYTHONPATH=os.path.join(self.tmp, "evil"))
        r = self.run_hook(env=env, **self.listed_read())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(self.marker))

    def test_the_hook_finds_the_module_in_the_home_scripts_folder_when_run_from_elsewhere(self):
        self.lone_hook()
        self.assertEqual(self.run_hook(**self.listed_read()).returncode, 2)

    def test_no_module_and_a_list_present_stays_closed(self):
        os.remove(os.path.join(self.scripts, "no_ai_wall.py"))
        self.lone_hook()
        self.assertEqual(self.run_hook("Bash", command="echo hi").returncode, 2)

    def test_settings_and_scripts_cannot_be_edited_even_with_an_empty_list(self):
        open(self.pfile, "w").write("# nothing yet\n")
        for f in (os.path.join(self.home, ".claude/settings.json"), os.path.join(self.scripts, "any.py"), os.path.join(self.hooks, "any.sh")):
            self.assertEqual(self.run_hook("Edit", file_path=f).returncode, 2, f)
        self.assertEqual(self.run_hook("Read", file_path=os.path.join(self.home, ".claude/settings.json")).returncode, 0)


if __name__ == "__main__":
    unittest.main()
