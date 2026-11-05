"""The no-AI wall's PreToolUse hook, run as Claude Code runs it: JSON on stdin, deny = exit 2 with the reason on stderr. Fake folders only."""
import json
import os
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
                        NO_AI_SENDERS_FILE=os.path.join(self.tmp, "senders.tsv"), NO_AI_REFUSAL_LOG=self.log)

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
        self.assertEqual(line[1:], ["Read", os.path.join(self.tmp, "wall", "clientA")])

    def test_allows_an_ordinary_call_and_logs_nothing(self):
        r = self.run_hook("Read", file_path=os.path.join(self.tmp, "work", "a.txt"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertFalse(os.path.exists(self.log))

    def test_bash_and_search_denials_are_logged_with_their_tool(self):
        self.assertEqual(self.run_hook("Bash", command="cat %s/wall/clientA/secret.txt" % self.tmp).returncode, 2)
        r = self.run_hook("Grep", pattern="x", path=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn(os.path.join(self.tmp, "wall", "clientA"), r.stderr)  # a search over a parent names the entry
        tools = [l.split("\t")[1] for l in open(self.log).read().splitlines()]
        self.assertEqual(tools, ["Bash", "Grep"])

    def test_log_never_holds_file_content_or_the_command(self):
        marker = "TOPSECRETWORDS"
        self.run_hook("Bash", command="echo %s > %s/wall/clientA/x.txt" % (marker, self.tmp))
        self.assertNotIn(marker, open(self.log).read())

    def test_session_in_a_listed_folder_is_denied(self):
        r = self.run_hook("Bash", cwd=os.path.join(self.tmp, "wall/clientA"), command="echo hi")
        self.assertEqual(r.returncode, 2)

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


if __name__ == "__main__":
    unittest.main()
