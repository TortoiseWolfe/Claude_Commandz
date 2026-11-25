"""Tests for server_lines.py: the carriage return (CR) a server tool puts at each line end is removed, ro refuses a command
that is not a read-only wrapper, write needs a reason. Fake wrappers in a temporary folder; nothing live is touched."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server_lines as sl  # noqa: E402


def fake(folder, name, body):
    path = os.path.join(folder, name)
    with open(path, "w") as fh:
        fh.write("#!/usr/bin/env bash\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = sl.main(argv)
    return rc, out.getvalue(), err.getvalue()


class CleanLines(unittest.TestCase):
    def test_crlf_and_lone_cr_become_plain_lines(self):
        self.assertEqual(sl.clean_lines("a:\r\nb:\r\n"), ["a:", "b:"])
        self.assertEqual(sl.clean_lines("a\rb"), ["a", "b"])

    def test_the_line_end_pattern_that_failed_in_the_incident_now_matches(self):
        raw = "    - apps/social/lib.php:\r\n    - apps/social/x.php:\r\n"
        import re
        self.assertEqual([ln for ln in raw.split("\n") if re.search(r":$", ln)], [])  # the old way: a line-feed split leaves the CR on each line
        self.assertEqual([ln for ln in sl.clean_lines(raw) if re.search(r":$", ln)], ["    - apps/social/lib.php:", "    - apps/social/x.php:"])

    def test_empty_answer_is_no_lines(self):
        self.assertEqual(sl.clean_lines(""), [])

    def test_blank_line_inside_is_kept(self):
        self.assertEqual(sl.clean_lines("a\r\n\r\nb\r\n"), ["a", "", "b"])


class Names(unittest.TestCase):
    def test_read_only_names(self):
        for ok in ("occ-ro.sh", "/x/y/occ-ro", "check-ro.py"):
            self.assertTrue(sl.is_read_only_name(ok), ok)
        for bad in ("occ.sh", "ro.sh", "occ-rw.sh", "occ-ro.sh.bak", "echo"):
            self.assertFalse(sl.is_read_only_name(bad), bad)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ro = fake(self.tmp.name, "occ-ro.sh", r"printf 'one:\r\ntwo:\r\nthree\r\n'; echo warn >&2")
        self.plain = fake(self.tmp.name, "occ.sh", "echo changed")

    def test_ro_returns_clean_lines_with_stderr_merged(self):
        rc, out, _ = call(["ro", "--", self.ro])
        self.assertEqual(rc, 0)
        self.assertNotIn("\r", out)
        self.assertEqual(sorted(out.splitlines()), ["one:", "three", "two:", "warn"])

    def test_ro_grep_with_a_dollar_pattern_matches_after_cr_removal(self):
        rc, out, _ = call(["ro", "--grep", r":$", "--", self.ro])
        self.assertEqual(out.splitlines(), ["one:", "two:"])

    def test_ro_json(self):
        rc, out, _ = call(["ro", "--json", "--", self.ro])
        data = json.loads(out)
        self.assertEqual(data["rc"], 0)
        self.assertIn("one:", data["lines"])

    def test_ro_refuses_a_command_that_is_not_a_read_only_wrapper(self):
        rc, out, err = call(["ro", "--", self.plain])
        self.assertEqual(rc, sl.REFUSED)
        self.assertEqual(out, "")
        self.assertIn("write command", err)

    def test_write_needs_a_reason_and_announces_itself(self):
        with self.assertRaises(SystemExit):
            call(["write", "--", self.plain])
        rc, out, err = call(["write", "--reason", "test", "--", self.plain])
        self.assertEqual((rc, out.strip()), (0, "changed"))
        self.assertIn("WRITE (test)", err)

    def test_the_command_exit_code_is_passed_through(self):
        bad = fake(self.tmp.name, "fail-ro.sh", "echo no; exit 7")
        rc, out, _ = call(["ro", "--", bad])
        self.assertEqual((rc, out.strip()), (7, "no"))

    def test_missing_command_is_127_not_a_traceback(self):
        rc, out, _ = call(["ro", "--", os.path.join(self.tmp.name, "gone-ro.sh")])
        self.assertEqual(rc, 127)

    def test_timeout_is_124(self):
        slow = fake(self.tmp.name, "slow-ro.sh", "sleep 5")
        rc, _, _ = call(["ro", "--timeout", "0.3", "--", slow])
        self.assertEqual(rc, 124)

    def test_arguments_are_not_expanded_by_a_shell(self):
        echo = fake(self.tmp.name, "echo-ro.sh", 'printf "%s\\n" "$@"')
        rc, out, _ = call(["ro", "--", echo, "$HOME", "a;b", "*"])
        self.assertEqual(out.splitlines(), ["$HOME", "a;b", "*"])


if __name__ == "__main__":
    unittest.main()
