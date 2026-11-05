"""The no-AI wall's shared reader and its tool-call decisions. Every path here is a fake folder under a temp dir."""
import io
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS)
import no_ai_wall as w  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.home = os.path.join(self.tmp, "home")
        self.wall = os.path.join(self.tmp, "wall")  # the fake client area
        for d in ("home", "wall/clientA/docs", "wall/clientB", "wall/open", "work"):
            os.makedirs(os.path.join(self.tmp, d))
        for f in ("wall/clientA/docs/secret.txt", "wall/clientB/notes.md", "wall/open/ok.txt", "work/a.txt"):
            with open(os.path.join(self.tmp, f), "w") as fh:
                fh.write("x")
        self._env = {k: os.environ.get(k) for k in ("HOME", "NO_AI_PATHS_FILE", "NO_AI_SENDERS_FILE", "NO_AI_REFUSAL_LOG")}
        os.environ["HOME"] = self.home
        self.pfile = os.path.join(self.tmp, "paths.txt")
        self.sfile = os.path.join(self.tmp, "senders.tsv")
        self.log = os.path.join(self.tmp, "refusals.log")
        os.environ["NO_AI_PATHS_FILE"] = self.pfile
        os.environ["NO_AI_SENDERS_FILE"] = self.sfile
        os.environ["NO_AI_REFUSAL_LOG"] = self.log
        w._warned.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def paths(self, *lines):
        with open(self.pfile, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        return w.load_paths()

    def p(self, rel):
        return os.path.join(self.tmp, rel)


class ReaderTests(Fixture):
    def test_missing_list_is_off(self):
        pl = w.load_paths()
        self.assertEqual(pl.status, "missing")
        self.assertFalse(pl.on)
        self.assertIsNone(pl.match(self.p("wall/clientA")))
        self.assertIsNone(w.evaluate({"tool_name": "Read", "tool_input": {"file_path": self.p("wall/clientA/docs/secret.txt")}}, pl))

    def test_warns_once_per_run(self):
        buf = io.StringIO()
        w.warn_missing("paths", "/x/no-ai-paths.txt", buf)
        w.warn_missing("paths", "/x/no-ai-paths.txt", buf)
        self.assertEqual(buf.getvalue().count("\n"), 1)

    def test_literal_entry_covers_everything_under_it(self):
        pl = self.paths(self.p("wall/clientA"))
        self.assertTrue(pl.match(self.p("wall/clientA")))
        self.assertTrue(pl.match(self.p("wall/clientA/docs/secret.txt")))
        self.assertTrue(pl.match(self.p("wall/clientA/not-there-yet/file")))
        self.assertIsNone(pl.match(self.p("wall/clientB/notes.md")))
        self.assertIsNone(pl.match(self.p("wall/clientAB/x")))  # a sibling that shares a prefix is not covered

    def test_star_stays_in_one_segment(self):
        pl = self.paths(self.p("wall/*/docs"))
        self.assertTrue(pl.match(self.p("wall/clientA/docs/secret.txt")))
        self.assertIsNone(pl.match(self.p("wall/clientA/other/docs")))
        self.assertIsNone(pl.match(self.p("wall/clientA/x/docs")))
        pl = self.paths(self.p("wall/client*"))
        self.assertTrue(pl.match(self.p("wall/clientB/notes.md")))
        self.assertIsNone(pl.match(self.p("wall/open/ok.txt")))

    def test_double_star_crosses_segments(self):
        pl = self.paths(self.p("wall/**/secret.txt"))
        self.assertTrue(pl.match(self.p("wall/clientA/docs/secret.txt")))

    def test_tilde_expands_to_home(self):
        os.makedirs(os.path.join(self.home, "repos", "Acme"))
        pl = self.paths("~/repos/Acme", "# a comment", "")
        self.assertTrue(pl.match(os.path.join(self.home, "repos", "Acme", "x")))
        self.assertTrue(pl.match("~/repos/Acme/y"))
        self.assertIsNone(pl.match(os.path.join(self.home, "repos", "Other")))

    def test_symlink_is_resolved_before_matching(self):
        pl = self.paths(self.p("wall/clientA"))
        link = self.p("work/innocent")
        os.symlink(self.p("wall/clientA"), link)
        self.assertTrue(pl.match(link + "/docs/secret.txt"))

    def test_entry_reached_through_a_symlinked_parent_still_matches(self):
        os.symlink(self.p("wall"), self.p("wallink"))
        pl = self.paths(self.p("wallink/clientA"))
        self.assertTrue(pl.match(self.p("wall/clientA/docs/secret.txt")))

    def test_dot_dot_is_collapsed(self):
        pl = self.paths(self.p("wall/clientA"))
        self.assertTrue(pl.match(self.p("work/../wall/clientA/docs")))

    def test_relative_path_uses_cwd(self):
        pl = self.paths(self.p("wall/clientA"))
        self.assertTrue(pl.match("docs/secret.txt", self.p("wall/clientA")))
        self.assertIsNone(pl.match("a.txt", self.p("work")))

    def test_unparseable_list_fails_closed(self):
        for body in ("relative/path\n", self.p("wall/a") + "\nnot-a-path\n"):
            with open(self.pfile, "w") as fh:
                fh.write(body)
            pl = w.load_paths()
            self.assertEqual(pl.status, "bad")
            self.assertEqual(pl.match(self.p("work/a.txt")), w.UNREADABLE)  # everything counts as listed
        with open(self.pfile, "wb") as fh:
            fh.write(b"\xff\xfe\x00bad")
        self.assertEqual(w.load_paths().status, "bad")

    def test_a_folder_in_place_of_the_file_fails_closed(self):
        os.makedirs(self.pfile)
        self.assertEqual(w.load_paths().status, "bad")

    def test_contains_finds_a_listed_folder_below_a_root(self):
        pl = self.paths(self.p("wall/clientA"), self.p("wall/*/docs"))
        self.assertTrue(pl.contains(self.tmp))
        self.assertTrue(pl.contains(self.p("wall")))
        self.assertIsNone(pl.contains(self.p("work")))
        self.assertTrue(pl.contains(self.p("wall/open")))  # wall/*/docs would match wall/open/docs
        self.assertTrue(pl.contains(self.p("wall/clientB")))  # wall/clientB/docs matches the glob too
        only = self.paths(self.p("wall/clientA"))
        self.assertIsNone(only.contains(self.p("wall/open")))
        self.assertIsNone(only.contains(self.p("wall/clientB")))

    def test_contains_sees_through_a_symlinked_root(self):
        pl = self.paths(self.p("wall/clientA"))
        os.symlink(self.p("wall"), self.p("work/w"))
        self.assertTrue(pl.contains(self.p("work/w")))

    def test_text_naming_a_listed_path(self):
        pl = self.paths(self.p("wall/clientA"))
        self.assertTrue(w.text_names_listed_path("please read %s/docs/secret.txt now" % self.p("wall/clientA"), pl))
        self.assertTrue(w.text_names_listed_path("the folder (%s)." % self.p("wall/clientA"), pl))
        self.assertIsNone(w.text_names_listed_path("work in %s only" % self.p("work"), pl))


class SenderTests(Fixture):
    def senders(self, *rows):
        with open(self.sfile, "w") as fh:
            fh.write("client\trepo_dir\tkind\tgmail_from\n" + "\n".join(rows) + "\n")
        return w.load_senders()

    def test_missing_senders_list_is_off(self):
        sl = w.load_senders()
        self.assertEqual(sl.status, "missing")
        self.assertIsNone(sl.match("a@b.test"))

    def test_exact_address_and_domain_match(self):
        sl = self.senders("Acme Co\t~/repos/acme\tmail\tboss@acme.test", "Beta Inc\t~/repos/beta\tmail\tbeta.test")
        self.assertEqual(sl.match("boss@acme.test"), "Acme Co")
        self.assertEqual(sl.match("Boss <BOSS@ACME.TEST>"), "Acme Co")
        self.assertIsNone(sl.match("other@acme.test"))
        self.assertEqual(sl.match("x@beta.test"), "Beta Inc")
        self.assertEqual(sl.match("x@mail.beta.test"), "Beta Inc")
        self.assertIsNone(sl.match("x@notbeta.test"))

    def test_bad_row_fails_closed(self):
        sl = self.senders("Acme Co\t~/repos/acme\tmail")
        self.assertEqual(sl.status, "bad")
        self.assertEqual(sl.match("anyone@anywhere.test"), w.UNREADABLE)
        self.assertTrue(sl.mentions("nothing in particular"))

    def test_mentions_names_and_addresses(self):
        sl = self.senders("Acme Co\t-\tmail\tboss@acme.test")
        self.assertTrue(sl.mentions("Client mail: acme co | absorb lane"))
        self.assertTrue(sl.mentions("from boss@acme.test yesterday"))
        self.assertFalse(sl.mentions("Client mail: Someone Else"))
        self.assertEqual(sl.clients, ["Acme Co"])


class DecisionTests(Fixture):
    def call(self, tool, cwd=None, **ti):
        return w.evaluate({"tool_name": tool, "tool_input": ti, "cwd": cwd or self.p("work")}, w.load_paths())

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))

    def test_file_tools_deny_a_listed_path(self):
        s = self.p("wall/clientA/docs/secret.txt")
        for tool, key in (("Read", "file_path"), ("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
            r = self.call(tool, **{key: s})
            self.assertIsNotNone(r, tool)
            self.assertEqual(r[0], tool)
            self.assertEqual(r[1], self.p("wall/clientA"))
        self.assertIsNone(self.call("Read", file_path=self.p("wall/open/ok.txt")))
        self.assertIsNone(self.call("Write", file_path=self.p("work/new.txt")))

    def test_session_in_a_listed_folder_is_denied_for_every_tool(self):
        cwd = self.p("wall/clientA/docs")
        for tool, ti in (("Read", {"file_path": self.p("work/a.txt")}), ("Bash", {"command": "echo hi"}), ("Grep", {"pattern": "x", "path": self.p("work")})):
            r = self.call(tool, cwd=cwd, **ti)
            self.assertIsNotNone(r, tool)
            self.assertIn("working folder", r[2])

    def test_grep_and_glob_deny_a_root_inside_or_above_the_wall(self):
        r = self.call("Grep", pattern="x", path=self.p("wall/clientA"))
        self.assertIsNotNone(r)
        r = self.call("Grep", pattern="x", path=self.tmp)  # a parent that contains a listed folder
        self.assertIsNotNone(r)
        self.assertIn(self.p("wall/clientA"), r[2])  # names the entry so the caller narrows the root
        r = self.call("Glob", pattern="**/*.md", path=self.p("wall"))
        self.assertIsNotNone(r)
        self.assertIn("Narrow", r[2])
        self.assertIsNone(self.call("Grep", pattern="x", path=self.p("work")))
        self.assertIsNone(self.call("Grep", pattern="x", path=self.p("wall/open")))
        self.assertIsNone(self.call("Glob", pattern="*.txt", path=self.p("work")))

    def test_grep_with_no_path_searches_the_session_folder(self):
        self.assertIsNotNone(self.call("Grep", cwd=self.tmp, pattern="x"))
        self.assertIsNone(self.call("Grep", cwd=self.p("work"), pattern="x"))

    def test_glob_pattern_that_is_itself_an_absolute_path(self):
        self.assertIsNotNone(self.call("Glob", pattern=self.p("wall/clientB") + "/*.md"))
        self.assertIsNotNone(self.call("Glob", pattern=self.tmp + "/**/*.md"))
        self.assertIsNone(self.call("Glob", pattern=self.p("work") + "/*.txt"))

    def test_bash_path_arguments(self):
        s = self.p("wall/clientA/docs/secret.txt")
        for cmd in ("cat " + s, "cat '%s'" % s, "ls; cat " + s, "echo hi && head -n1 " + s,
                    "cd %s && cat docs/secret.txt" % self.p("wall/clientA"),
                    "cd %s && ls" % self.p("wall/clientA"),
                    "git -C %s log" % self.p("wall/clientA"),
                    "tool --input=%s" % s, "python3 -c \"print(open('%s').read())\"" % s,
                    "cat %s/wall/client*/notes.md" % self.tmp,
                    "cat ../wall/clientB/notes.md"):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[0], "Bash")
        for cmd in ("ls " + self.p("work"), "cat " + self.p("wall/open/ok.txt"), "cat a.txt", "echo hello", "git status", "ls " + self.p("wall")):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

    def test_bash_relative_word_resolved_against_a_cd(self):
        r = self.call("Bash", cwd=self.p("wall"), command="cat clientA/docs/secret.txt")
        self.assertIsNotNone(r)
        self.assertIsNotNone(self.call("Bash", cwd=self.tmp, command="cd wall && cat clientB/notes.md"))

    def test_bash_symlink_into_the_wall(self):
        os.symlink(self.p("wall/clientA"), self.p("work/innocent"))
        self.assertIsNotNone(self.call("Bash", command="cat innocent/docs/secret.txt"))

    def test_recursive_search_over_a_parent_is_a_root_denial(self):
        for cmd in ("grep -r token " + self.tmp, "rg token " + self.tmp, "find %s -name '*.md'" % self.p("wall"), "du -sh " + self.tmp,
                    "grep -rn x ../wall", "tar cf out.tar " + self.p("wall")):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertIn("Narrow", r[2], cmd)
        r = self.call("Bash", cwd=self.tmp, command="rg token")  # no folder named: it walks the session folder
        self.assertIsNotNone(r)
        self.assertIsNone(self.call("Bash", cwd=self.p("work"), command="rg token"))
        self.assertIsNone(self.call("Bash", command="grep -r token " + self.p("work")))

    def test_plain_listing_of_a_parent_is_allowed(self):
        self.assertIsNone(self.call("Bash", command="ls " + self.tmp))
        self.assertIsNone(self.call("Bash", command="cd " + self.tmp))

    def test_other_tools_are_not_judged(self):
        self.assertIsNone(self.call("WebFetch", url="https://example.test"))
        self.assertIsNone(self.call("Agent", prompt="read " + self.p("wall/clientA")))

    def test_unreadable_list_denies_every_file_tool_except_repairing_the_list(self):
        with open(self.pfile, "w") as fh:
            fh.write("garbage\n")
        for tool, ti in (("Read", {"file_path": self.p("work/a.txt")}), ("Bash", {"command": "echo hi"}), ("Grep", {"pattern": "x"})):
            r = self.call(tool, **ti)
            self.assertIsNotNone(r, tool)
            self.assertEqual(r[1], w.UNREADABLE)
        self.assertIsNone(self.call("Edit", file_path=self.pfile))
        self.assertIsNotNone(self.call("Edit", file_path=self.p("work/a.txt")))

    def test_refusal_log_holds_time_tool_entry_and_nothing_else(self):
        w.log_refusal("Read", self.p("wall/clientA"))
        w.log_refusal("Bash", self.p("wall/clientB"))
        lines = open(self.log).read().splitlines()
        self.assertEqual(len(lines), 2)
        t, tool, entry = lines[0].split("\t")
        self.assertRegex(t, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual((tool, entry), ("Read", self.p("wall/clientA")))


class CliTests(Fixture):
    def run_cli(self, *args):
        env = dict(os.environ)
        return subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "no_ai_wall.py"), *args], capture_output=True, text=True, env=env)

    def test_check_path_exit_codes(self):
        self.paths(self.p("wall/clientA"))
        self.assertEqual(self.run_cli("check-path", self.p("work")).returncode, 0)
        r = self.run_cli("check-path", self.p("work"), self.p("wall/clientA/docs"))
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("clientA\n", r.stderr.replace(self.p("wall/clientA/docs"), ""))  # it names the entry by number, never by its text
        self.assertIn("entry 1", r.stderr)

    def test_check_path_missing_list_warns_once_and_passes(self):
        r = self.run_cli("check-path", self.p("wall/clientA"), self.p("work"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.count("wall is off"), 1)

    def test_check_text(self):
        self.paths(self.p("wall/clientA"))
        prompt = self.p("work/prompt.md")
        open(prompt, "w").write("fix the thing in %s\n" % self.p("work"))
        self.assertEqual(self.run_cli("check-text", prompt).returncode, 0)
        open(prompt, "w").write("fix the thing in %s\n" % self.p("wall/clientA/docs"))
        self.assertEqual(self.run_cli("check-text", prompt).returncode, 3)

    def test_check_sender(self):
        open(self.sfile, "w").write("client\trepo_dir\tkind\tgmail_from\nAcme Co\t-\tmail\tboss@acme.test\n")
        self.assertEqual(self.run_cli("check-sender", "x@y.test").returncode, 0)
        r = self.run_cli("check-sender", "boss@acme.test")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "Acme Co"))

    def test_status_prints_counts_only(self):
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        r = self.run_cli("status")
        self.assertIn("paths: ok, 2 entries", r.stdout)
        self.assertIn("senders: missing", r.stdout)
        self.assertNotIn("wall", r.stdout.replace("wall is", ""))


if __name__ == "__main__":
    unittest.main()
