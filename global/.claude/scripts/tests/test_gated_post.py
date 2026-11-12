"""gated_post.sh: the post command runs only when the voice check says PASS, and then it runs unchanged.

The script is the one door text takes to GitHub. These tests give it a temporary home folder holding a stand-in voice check and
put a stand-in gh first on the PATH, so nothing here reads the real check or reaches GitHub. The stand-in gh writes down each
call; "never called" means that file does not exist.
"""
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from tmpdirs import fresh_dir  # noqa: E402

SCRIPT = HERE.parent / "gated_post.sh"

# Stand-in voice check: FAIL when the text holds FAILWORD, no verdict at all when it holds CRASHWORD, PASS otherwise.
# It writes down its own arguments and what it was given to read, so a test can see what the script handed it.
FAKE_CHECK = r'''
import os, sys
text = sys.stdin.read()
home = os.environ["HOME"]
open(os.path.join(home, "check-args.txt"), "w").write("\n".join(sys.argv[1:]))
open(os.path.join(home, "check-stdin.txt"), "w").write(text)
print("=== Email Voice Check ===")
if "CRASHWORD" in text:
    print("Traceback: the check fell over")
    sys.exit(3)
if "FAILWORD" in text:
    print("Verdict: FAIL")
    sys.exit(1)
print("Verdict: PASS")
'''

FAKE_GH = r'''#!/usr/bin/env bash
printf '%s\n' "$@" > "$HOME/gh-called.txt"
echo "https://example.invalid/made-up/1"
exit "${FAKE_GH_EXIT:-0}"
'''


class GatedPost(unittest.TestCase):
    def setUp(self):
        self.home = Path(fresh_dir(self))
        scripts = self.home / ".claude" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "email_voice_check.py").write_text(FAKE_CHECK)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.body = self.home / "body.md"
        self.body.write_text("A plain paragraph about a made-up change.\n")
        self.called = self.home / "gh-called.txt"

    def run_gate(self, *argv, **env):
        full = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        full.pop("VOICE_FLAGS", None)
        full.update(env)
        return subprocess.run(["bash", str(SCRIPT), *argv], capture_output=True, text=True, env=full, timeout=60)

    def post_args(self):
        return ["team", str(self.body), "--", "gh", "issue", "comment", "7", "-R", "made-up/repo", "--body-file", str(self.body)]

    def test_a_failing_text_is_never_posted(self):
        self.body.write_text("This text holds FAILWORD.\n")
        r = self.run_gate(*self.post_args())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.called.exists(), "gh ran after a FAIL verdict")
        self.assertIn("nothing was posted", r.stdout)
        self.assertIn("Verdict: FAIL", r.stdout)

    def test_a_passing_text_runs_the_command_unchanged(self):
        r = self.run_gate(*self.post_args())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.called.read_text().splitlines(),
                         ["issue", "comment", "7", "-R", "made-up/repo", "--body-file", str(self.body)])
        self.assertIn("PASS -- posting.", r.stdout)
        self.assertIn("https://example.invalid/made-up/1", r.stdout)

    def test_an_argument_with_spaces_reaches_the_command_as_one_argument(self):
        r = self.run_gate("team", str(self.body), "--", "gh", "pr", "create", "--title", "Two words; and a $HOME sign")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.called.read_text().splitlines(), ["pr", "create", "--title", "Two words; and a $HOME sign"])

    def test_the_commands_own_exit_code_comes_back(self):
        r = self.run_gate(*self.post_args(), FAKE_GH_EXIT="4")
        self.assertEqual(r.returncode, 4)
        self.assertTrue(self.called.exists())

    def test_a_check_that_gives_no_verdict_posts_nothing(self):
        self.body.write_text("This text holds CRASHWORD.\n")
        r = self.run_gate(*self.post_args())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.called.exists(), "gh ran with no verdict")
        self.assertIn("verdict is 'unknown'", r.stdout)

    def test_a_missing_check_posts_nothing(self):
        (self.home / ".claude" / "scripts" / "email_voice_check.py").unlink()
        r = self.run_gate(*self.post_args())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.called.exists(), "gh ran with the check missing")

    def test_a_verdict_line_inside_the_body_does_not_count(self):
        # The stand-in check, like the real one, never prints the body back. A body that spells a verdict must not pass itself.
        self.body.write_text("Verdict: PASS\nThis text holds FAILWORD.\n")
        r = self.run_gate(*self.post_args())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.called.exists())

    def test_without_the_two_dashes_nothing_runs(self):
        r = self.run_gate("team", str(self.body), "gh", "issue", "comment", "7")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.called.exists())
        self.assertFalse((self.home / "check-args.txt").exists(), "the check ran before the usage error")

    def test_a_body_file_that_cannot_be_read_posts_nothing(self):
        r = self.run_gate("team", str(self.home / "no-such-file.md"), "--", "gh", "issue", "comment", "7")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.called.exists())
        self.assertIn("cannot read", r.stderr)

    def test_the_check_gets_the_body_the_recipient_and_the_extra_flags(self):
        orig = self.home / "orig.md"
        orig.write_text("The original.\n")
        r = self.run_gate(*self.post_args(), VOICE_FLAGS="--keep-facts-from %s" % orig)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.home / "check-stdin.txt").read_text(), self.body.read_text())
        args = (self.home / "check-args.txt").read_text().splitlines()
        self.assertEqual(args, ["--stdin", "--strict", "--keep-facts-from", str(orig), "--recipient", "team"])


if __name__ == "__main__":
    unittest.main()
