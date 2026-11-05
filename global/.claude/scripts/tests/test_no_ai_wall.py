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
        self._env = {k: os.environ.get(k) for k in ("HOME", "NO_AI_PATHS_FILE", "NO_AI_REFUSAL_LOG")}
        os.environ["HOME"] = self.home
        self.pfile = os.path.join(self.tmp, "paths.txt")
        self.log = os.path.join(self.tmp, "refusals.log")
        os.environ["NO_AI_PATHS_FILE"] = self.pfile
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
            self.assertEqual(r[1], "entry 1")
            self.assertNotIn(self.p("wall"), r[2])
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
        self.assertIn("entry 1", r[2])  # names the entry by number so the caller narrows the root
        self.assertNotIn(self.p("wall"), r[2])
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

    def test_tools_with_no_path_in_them_are_not_judged(self):
        self.assertIsNone(self.call("WebFetch", url="https://example.test"))
        self.assertIsNone(self.call("Agent", prompt="read " + self.p("work/a.txt")))

    def test_unreadable_list_denies_every_tool_and_offers_no_way_to_edit_it(self):
        with open(self.pfile, "w") as fh:
            fh.write("garbage\n")
        for tool, ti in (("Read", {"file_path": self.p("work/a.txt")}), ("Bash", {"command": "echo hi"}), ("Grep", {"pattern": "x"}),
                         ("mcp__x__ping", {"q": "hi"}), ("Edit", {"file_path": self.pfile})):
            r = self.call(tool, **ti)
            self.assertIsNotNone(r, tool)
            self.assertEqual(r[1], w.UNREADABLE)
            self.assertNotIn("Edit " + self.pfile, r[2])

    def test_refusal_log_holds_time_tool_entry_and_nothing_else(self):
        w.log_refusal("Read", "entry 1")
        w.log_refusal("Bash", "entry 2")
        lines = open(self.log).read().splitlines()
        self.assertEqual(len(lines), 2)
        t, tool, entry = lines[0].split("\t")
        self.assertRegex(t, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual((tool, entry), ("Read", "entry 1"))


class SecurityFindingTests(Fixture):
    """The five findings of the 4 Nov security review of the first wall, one group each. Synthetic paths only."""

    def call(self, tool, cwd=None, **ti):
        return w.evaluate({"tool_name": tool, "tool_input": ti, "cwd": cwd or self.p("work")}, w.load_paths())

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))

    # 1. relative Glob and Grep patterns and ".." are joined with the root and normalized
    def test_glob_relative_pattern_that_climbs_into_the_wall(self):
        self.assertIsNotNone(self.call("Glob", pattern="../wall/clientA/**/*.txt"))
        self.assertIsNotNone(self.call("Glob", pattern="../wall/clientB/*"))
        self.assertIsNotNone(self.call("Glob", pattern="../../" + os.path.basename(self.tmp) + "/wall/clientA/docs/*"))

    def test_glob_relative_pattern_joins_with_the_search_root_not_the_session_folder(self):
        # the session sits in work/, but the search root is wall/: "clientA/docs/*" starts inside the wall
        self.assertIsNotNone(self.call("Glob", pattern="clientA/docs/*", path=self.p("wall/open")) or self.call("Glob", pattern="../clientA/docs/*", path=self.p("wall/open")))
        self.assertIsNotNone(self.call("Glob", pattern="../clientA/docs/*", path=self.p("wall/open")))
        self.assertIsNone(self.call("Glob", pattern="../open/*", path=self.p("wall/open")))

    def test_glob_dot_dot_after_a_wildcard_cannot_be_checked_so_it_is_refused(self):
        r = self.call("Glob", pattern="*/../../wall/clientA/*")
        self.assertIsNotNone(r)
        self.assertIn("climbs", r[2])

    def test_glob_brace_alternatives_start_at_the_brace(self):
        self.assertIsNotNone(self.call("Glob", pattern=self.p("wall") + "/{clientA,clientB}/*"))

    def test_grep_glob_filter_that_climbs_into_the_wall(self):
        self.assertIsNotNone(self.call("Grep", pattern="x", path=self.p("work"), glob="../wall/clientA/**"))
        self.assertIsNone(self.call("Grep", pattern="x", path=self.p("work"), glob="*.py"))

    def test_plain_relative_patterns_stay_allowed(self):
        self.assertIsNone(self.call("Glob", pattern="*.txt"))
        self.assertIsNone(self.call("Glob", pattern="src/**/*.py", path=self.p("work")))
        self.assertIsNone(self.call("Glob", pattern="../open/*.txt"))

    def test_glob_home_pattern(self):
        self.paths("~/private")
        self.assertIsNotNone(self.call("Glob", pattern="~/private/*"))
        self.assertIsNone(self.call("Glob", pattern="~/other/*"))

    # 2. the working-folder check runs before the tool-name filter; MCP and unknown tools are judged
    def test_listed_working_folder_denies_mcp_and_unknown_tools(self):
        cwd = self.p("wall/clientA")
        for tool in ("mcp__claude_ai_Gmail__search_threads", "mcp__fs__read", "SomeNewTool", "WebFetch", "Agent", ""):
            r = self.call(tool, cwd=cwd, q="hello")
            self.assertIsNotNone(r, tool)
            self.assertIn("working folder", r[2])

    def test_unknown_tool_naming_a_listed_path_in_any_field_is_denied(self):
        r = self.call("mcp__fs__read_file", args={"files": [{"p": self.p("wall/clientA/docs/secret.txt")}]})
        self.assertIsNotNone(r)
        self.assertEqual(r[1], "entry 1")
        self.assertIsNotNone(self.call("mcp__x__ask", prompt="summarise " + self.p("wall/clientB") + " please"))
        self.assertIsNotNone(self.call("Agent", prompt="read " + self.p("wall/clientA")))

    def test_unknown_tool_with_no_listed_path_is_allowed(self):
        self.assertIsNone(self.call("mcp__fs__read_file", path=self.p("work/a.txt")))
        self.assertIsNone(self.call("mcp__x__ping"))
        self.assertIsNone(self.call("mcp__x__ping", nested={"a": [1, 2, {"b": "plain words"}]}))

    # 3. the list, the hook and the module cannot be changed from a session
    def test_edit_tools_refuse_the_list_the_hook_and_the_module(self):
        h = self.home
        for f in (self.pfile, os.path.join(h, ".claude/hooks/no-ai-wall-guard.py"), os.path.join(h, ".claude/scripts/no_ai_wall.py"), os.path.join(h, ".claude/scripts/no_ai_wall.sh")):
            for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
                r = self.call(tool, **{key: f})
                self.assertIsNotNone(r, (tool, f))
                self.assertEqual(r[1], "protected file")

    def test_protected_file_reached_through_a_symlink_or_dots(self):
        os.makedirs(os.path.join(self.home, ".claude/scripts"))
        os.symlink(self.pfile, self.p("work/shortcut.txt"))
        self.assertIsNotNone(self.call("Edit", file_path="shortcut.txt"))
        self.assertIsNotNone(self.call("Edit", file_path=self.p("work/../paths.txt")))

    def test_bash_that_writes_a_protected_file_is_denied(self):
        h = self.home
        for cmd in ("echo x >> " + self.pfile, "sed -i 's/a/b/' " + self.pfile, "rm " + self.pfile, "mv %s /tmp/x" % self.pfile,
                    "cp /tmp/x " + os.path.join(h, ".claude/scripts/no_ai_wall.py"), "tee " + os.path.join(h, ".claude/hooks/no-ai-wall-guard.py"),
                    "python3 -c \"open('%s','w')\"" % self.pfile, "cat %s > /dev/null" % self.pfile, "truncate -s0 " + self.pfile):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)

    def test_bash_may_check_the_list_exists_or_view_it_through_redact_view(self):
        rv = os.path.join(self.home, ".claude/scripts/redact_view.py")
        self.assertIsNone(self.call("Bash", command="ls -l " + self.pfile))
        self.assertIsNone(self.call("Bash", command="wc -l " + self.pfile))
        self.assertIsNone(self.call("Bash", command="python3 %s %s" % (rv, self.pfile)))
        self.assertIsNone(self.call("Bash", command="python3 -I %s status" % os.path.join(self.home, ".claude/scripts/no_ai_wall.py")))
        self.assertIsNotNone(self.call("Bash", command="python3 /tmp/other.py " + self.pfile))
        self.assertIsNotNone(self.call("Bash", command="cat " + self.pfile))  # the contents stay out of the transcript

    def test_read_tool_refuses_the_list_but_may_read_the_hook_and_module(self):
        self.assertIsNotNone(self.call("Read", file_path=self.pfile))
        self.assertIsNone(self.call("Read", file_path=os.path.join(self.home, ".claude/scripts/no_ai_wall.py")))

    def test_unknown_tool_naming_a_protected_file_is_denied(self):
        self.assertIsNotNone(self.call("mcp__fs__write_file", path=self.pfile, content="x"))

    # 4. /mnt/ paths ignore case
    def test_mnt_entries_match_without_regard_to_case(self):
        self.paths("/mnt/c/Users/Someone/Private")
        for p in ("/mnt/c/users/someone/private/a.txt", "/mnt/C/USERS/SOMEONE/PRIVATE", "/mnt/c/Users/Someone/Private/deep/er.txt"):
            self.assertEqual(self.call("Read", file_path=p)[1], "entry 1", p)
        self.assertIsNone(self.call("Read", file_path="/mnt/c/Users/Someone/Public/a.txt"))

    def test_mnt_glob_entries_match_without_regard_to_case(self):
        self.paths("/mnt/c/Users/*/Recordings/*.mkv")
        self.assertIsNotNone(self.call("Read", file_path="/mnt/c/USERS/Anyone/recordings/Call.MKV"))

    def test_mnt_entries_are_also_case_insensitive_for_a_search_root_above_them(self):
        self.paths("/mnt/c/Users/Someone/Private")
        self.assertIsNotNone(self.call("Grep", pattern="x", path="/mnt/c/USERS"))
        self.assertIsNotNone(self.call("Bash", command="grep -r x /mnt/C/users"))

    def test_entries_outside_mnt_stay_case_sensitive(self):
        self.paths(self.p("wall/clientA"))
        self.assertIsNone(self.call("Read", file_path=self.p("wall/CLIENTA/x.txt")))

    def test_entry_reached_through_a_symlink_into_mnt_is_case_insensitive(self):
        # the variant produced by realpath starts with /mnt/, so the same rule applies to it
        e = w.Entry("/mnt/c/Users/Someone/Private")
        self.assertTrue(e.covers("/mnt/c/USERS/someone/private/x"))
        self.assertTrue(e.inside("/mnt/C/users"))

    # 5. deny messages name the entry by number, never by its path
    def test_every_deny_message_and_log_entry_names_a_number_not_a_path(self):
        wall = self.p("wall")
        cases = [("Read", {"file_path": self.p("wall/clientB/notes.md")}), ("Grep", {"pattern": "x", "path": self.tmp}),
                 ("Glob", {"pattern": "../wall/clientB/*"}), ("Bash", {"command": "cat %s/wall/clientB/notes.md" % self.tmp}),
                 ("Bash", {"command": "grep -r x " + self.tmp}), ("mcp__x__y", {"p": self.p("wall/clientB")})]
        for tool, ti in cases:
            r = self.call(tool, **ti)
            self.assertIsNotNone(r, (tool, ti))
            self.assertNotIn(wall, r[1] + r[2], (tool, ti))
            self.assertNotIn("clientB", r[1] + r[2], (tool, ti))
            self.assertRegex(r[1] + r[2], r"entry \d", (tool, ti))
        r = self.call("Read", file_path=self.p("wall/clientB/notes.md"))
        self.assertEqual(r[1], "entry 2")  # the second line of the list

    def test_check_path_cli_names_the_entry_by_number(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "no_ai_wall.py"), "check-path", self.p("wall/clientB/x")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        self.assertIn("entry 2", r.stderr)


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

    def test_status_prints_counts_only(self):
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        r = self.run_cli("status")
        self.assertIn("paths: ok, 2 entries", r.stdout)
        self.assertNotIn("wall", r.stdout.replace("wall is", ""))


class ReviewFixTests(Fixture):
    """The three findings of the automated security review of PR 230 (fake folders only)."""

    def call(self, tool, cwd=None, **ti):
        return w.evaluate({"tool_name": tool, "tool_input": ti, "cwd": cwd or self.p("work")}, w.load_paths())

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))

    def move_list(self):
        """The list lives in its own folder, away from every listed folder, as it does under ~/.config/afa."""
        cfg = self.p("home/.config/afa")
        os.makedirs(cfg)
        self.pfile = os.path.join(cfg, "no-ai-paths.txt")
        os.environ["NO_AI_PATHS_FILE"] = self.pfile
        self.paths(self.p("wall/clientA"))
        return cfg

    # finding 2: the list itself is not readable through Grep or Glob
    def test_grep_and_glob_refuse_the_list_and_every_folder_above_it(self):
        cfg = self.move_list()
        for root in (self.pfile, cfg, self.p("home/.config"), self.p("home"), self.tmp, "../home/.config", "../home/.config/afa/../afa"):
            for tool, extra in (("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*"})):
                r = self.call(tool, path=root, **extra)
                self.assertIsNotNone(r, (tool, root))
                self.assertNotIn("no-ai-paths", r[2])
        self.assertIsNone(self.call("Grep", pattern="x", path=self.p("work")))
        self.assertIsNone(self.call("Grep", pattern="x", path=self.p("home/.cache")))
        self.assertIsNone(self.call("Glob", pattern="*", path=self.p("home/.config/other")))

    def test_grep_with_no_path_refuses_when_the_session_folder_holds_the_list(self):
        cfg = self.move_list()
        self.assertIsNotNone(self.call("Grep", cwd=cfg, pattern="x"))
        self.assertIsNotNone(self.call("Grep", cwd=self.p("home"), pattern="x"))

    def test_grep_and_glob_refuse_the_list_through_a_symlink_and_a_pattern(self):
        cfg = self.move_list()
        os.symlink(cfg, self.p("work/link"))
        self.assertIsNotNone(self.call("Grep", pattern="x", path="link"))
        self.assertIsNotNone(self.call("Glob", pattern=cfg + "/*"))
        self.assertIsNotNone(self.call("Glob", pattern="../home/.config/afa/no-ai-*.txt"))
        self.assertIsNotNone(self.call("Grep", pattern="x", path=self.p("work"), glob="../home/.config/afa/*"))
        self.assertIsNone(self.call("Glob", pattern="*.txt", path=self.p("work")))

    # finding 1: protected files, with the list full or empty
    def protected_cases(self):
        h = self.home
        return [os.path.join(h, ".claude/hooks/other-hook.py"), os.path.join(h, ".claude/hooks/x.sh"),
                os.path.join(h, ".claude/scripts/gmail_api.py"), os.path.join(h, ".claude/scripts/lane.sh"),
                os.path.join(h, ".claude/scripts/tests/test_x.py"), os.path.join(h, ".claude/settings.json"),
                os.path.join(h, ".claude/settings.local.json"), self.p("work/.claude/settings.json"),
                self.p("work/.claude/settings.local.json")]

    def test_session_may_not_write_hook_script_or_settings_files(self):
        for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
            for f in self.protected_cases():
                r = self.call(tool, **{key: f})
                self.assertIsNotNone(r, (tool, f))
                self.assertEqual(r[1], "protected file", (tool, f))

    def test_protected_check_runs_when_the_list_has_no_entries(self):
        self.paths("# nothing listed yet")
        self.assertEqual(w.load_paths().entries, [])
        for f in self.protected_cases():
            r = self.call("Write", file_path=f)
            self.assertIsNotNone(r, f)
            self.assertEqual(r[1], "protected file", f)
        self.assertIsNone(self.call("Write", file_path=self.p("work/a.py")))
        self.assertIsNone(self.call("Read", file_path=self.p("home/.claude/settings.json")))

    def test_a_missing_list_leaves_everything_open(self):
        os.remove(self.pfile)
        for f in self.protected_cases():
            self.assertIsNone(self.call("Write", file_path=f), f)

    def test_files_that_are_not_protected_stay_writable(self):
        h = self.home
        for f in (os.path.join(h, ".claude/hooks/notes.md"), os.path.join(h, ".claude/scripts/data.json"), os.path.join(h, ".claude/commands/x.md"),
                  self.p("work/settings.json"), self.p("work/script.py"), self.p("work/.claude/commands/x.md"), os.path.join(h, ".claude/other.json")):
            self.assertIsNone(self.call("Write", file_path=f), f)

    def test_protected_script_reached_through_a_symlinked_folder(self):
        real = self.p("work/real-scripts")
        os.makedirs(real)
        os.makedirs(os.path.join(self.home, ".claude"))
        os.symlink(real, os.path.join(self.home, ".claude/scripts"))
        self.assertIsNotNone(self.call("Write", file_path=os.path.join(real, "x.py")))
        self.assertIsNotNone(self.call("Write", file_path=os.path.join(self.home, ".claude/scripts/x.py")))

    def test_bash_writes_to_hook_script_or_settings_files_are_denied(self):
        h = self.home
        s, py, sh = os.path.join(h, ".claude/settings.json"), os.path.join(h, ".claude/scripts/x.py"), os.path.join(h, ".claude/hooks/y.sh")
        proj = self.p("work/.claude/settings.local.json")
        for cmd in ("echo '{}' > " + s, "echo x >> " + py, "cat /tmp/a >| " + sh, "sed -i 's/a/b/' " + py, "tee " + sh + " < /tmp/a", "cp /tmp/a " + py,
                    "cp -f /tmp/a /tmp/b " + os.path.dirname(py) + "/", "mv /tmp/a " + s, "mv " + py + " /tmp/gone", "rm " + sh, "truncate -s0 " + proj,
                    "jq . /tmp/a > /tmp/b && mv /tmp/b " + s, "chmod +x " + py, "cd %s/.claude/scripts && echo x > x.py" % h, "python3 -c \"open('%s','w')\"" % s,
                    "install -m 755 /tmp/a " + sh, "ln -sf /tmp/evil " + py, "cp -t %s /tmp/a" % os.path.dirname(py), "perl -pi -e 's/a/b/' " + s):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)

    def test_bash_may_not_move_or_delete_the_folders_that_hold_the_guarded_files(self):
        h = self.home
        for cmd in ("mv %s/.claude/hooks /tmp/gone" % h, "rm -rf %s/.claude/scripts" % h, "rm -r %s/.claude" % h, "mv %s/.claude /tmp/x" % h,
                    "chmod 000 %s/.claude/hooks" % h, "rm -rf " + h, "cd %s/.claude && rm -rf hooks" % h, "mv ../home/.claude/scripts /tmp/y"):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)
        for cmd in ("rm -rf %s/.claude/projects/x" % h, "mv %s/.claude/commands /tmp/c" % h, "rm -rf %s/.cache" % h, "rm /tmp/a"):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

    def test_cp_target_directory_forms_are_destinations(self):
        h = self.home
        d = os.path.join(h, ".claude/scripts")
        for cmd in ("cp --target-directory=%s /tmp/a" % d, "cp -t%s /tmp/a" % d, "cp --target-directory %s /tmp/a" % d, "install -t %s /tmp/a" % d):
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        self.assertIsNone(self.call("Bash", command="cp -t /tmp/dest %s/.claude/scripts/x.py" % h))

    def test_guarded_files_on_a_windows_drive_ignore_case(self):
        os.environ["HOME"] = "/mnt/c/Users/Someone"
        for f in ("/mnt/C/USERS/SOMEONE/.CLAUDE/Settings.JSON", "/mnt/c/users/someone/.claude/SCRIPTS/X.PY", "/mnt/c/Users/Someone/.Claude/hooks/y.SH"):
            r = self.call("Write", file_path=f)
            self.assertIsNotNone(r, f)
            self.assertEqual(r[1], "protected file", f)

    def test_bash_runs_and_reads_scripts_and_settings_freely(self):
        h = self.home
        s, py, sh = os.path.join(h, ".claude/settings.json"), os.path.join(h, ".claude/scripts/x.py"), os.path.join(h, ".claude/hooks/y.sh")
        for cmd in ("python3 " + py + " draft-create --to a", "bash " + sh, "cat " + s, "cp " + py + " /tmp/backup/", "cp -a %s %s /tmp/backup/" % (py, sh),
                    "diff " + py + " /tmp/x.py", "echo hi > /tmp/out.txt", "ls " + os.path.dirname(py), "grep -n def " + py, "cd %s/.claude/scripts && python3 x.py" % h):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

    def test_bash_protected_check_runs_when_the_list_has_no_entries(self):
        self.paths("# nothing listed yet")
        self.assertIsNotNone(self.call("Bash", command="echo x > " + os.path.join(self.home, ".claude/settings.json")))
        self.assertIsNone(self.call("Bash", command="echo hi"))

    # finding 3: relative paths in MCP and unknown tools
    def test_mcp_relative_path_into_the_wall_is_denied(self):
        for cwd, val in ((self.p("wall"), "clientA/docs/secret.txt"), (self.p("wall"), "./clientA"), (self.p("work"), "../wall/clientA/docs"),
                         (self.p("work"), "../wall/clientB/../clientA"), (self.p("wall/open"), "../clientB/notes.md")):
            r = self.call("mcp__fs__read", cwd=cwd, path=val)
            self.assertIsNotNone(r, (cwd, val))
            self.assertEqual(r[1], "entry 1" if "clientA" in val else "entry 2", val)

    def test_mcp_relative_path_nested_or_in_a_sentence_is_denied(self):
        self.assertIsNotNone(self.call("mcp__x__ask", args={"files": [{"p": "../wall/clientA/docs/secret.txt"}]}))
        self.assertIsNotNone(self.call("mcp__x__ask", prompt="please summarise ../wall/clientB/notes.md for me"))
        self.assertIsNotNone(self.call("mcp__x__ask", prompt="open './../wall/clientA' now"))
        self.assertIsNotNone(self.call("Agent", cwd=self.p("wall"), prompt="read clientA/docs/secret.txt"))

    def test_mcp_relative_path_through_a_symlink_is_denied(self):
        os.symlink(self.p("wall/clientA"), self.p("work/shortcut"))
        self.assertIsNotNone(self.call("mcp__fs__read", path="./shortcut/docs/secret.txt"))

    def test_mcp_relative_path_to_a_protected_file_is_denied(self):
        scripts = os.path.join(self.home, ".claude/scripts")
        os.makedirs(scripts)
        r = self.call("mcp__fs__write", cwd=scripts, path="./no_ai_wall.py", content="x")
        self.assertEqual(r[1], "protected file")
        r = self.call("mcp__fs__write", cwd=self.p("work"), path="../home/.claude/settings.json", content="x")
        self.assertEqual(r[1], "protected file")

    # follow-ups found in the review of the fixes
    def test_a_search_that_reaches_the_list_says_so_and_not_that_a_file_was_changed(self):
        cfg = self.move_list()
        r = self.call("Grep", pattern="x", path=cfg)
        self.assertIn("search", r[2])
        self.assertNotIn("changed", r[2])
        self.assertNotIn("no-ai-paths", r[2])

    def test_bash_recursive_content_search_over_the_lists_folder_is_denied(self):
        cfg = self.move_list()
        for cmd in ("grep -r x " + cfg, "grep -rn x %s/../" % cfg, "rg x " + self.p("home"), "tar cf /tmp/a.tar " + self.p("home/.config"),
                    "find %s -type f -exec cat {} +" % self.p("home/.config"), "git grep x -- " + cfg, "cp -r %s /tmp/copy" % self.p("home/.config"),
                    "cd %s && rg x" % cfg, "cd %s && grep -r x ." % cfg):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)
            self.assertNotIn("no-ai-paths", r[2])

    def test_bash_names_only_listing_and_other_searches_stay_allowed(self):
        cfg = self.move_list()
        for cmd in ("find %s -name '*.json'" % self.p("home/.config"), "tree " + self.p("home"), "du -sh " + self.p("home"), "ls -R " + self.p("home"),
                    "grep -r x " + self.p("work"), "rg x " + self.p("home/.cache"), "grep x %s/other.txt" % self.p("work")):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

    def test_inline_interpreter_check_matches_the_interpreter_by_name_only(self):
        sc = os.path.join(self.home, ".claude/scripts/x.sh")
        for cmd in ("shellcheck -e SC1090 " + sc, "shasum -c " + sc, "bunzip2 -c " + sc, "nodemon -e js " + sc):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

    def test_a_huge_string_in_an_mcp_call_is_still_judged_quickly(self):
        import time
        text = ("see ./a/b and c/d.txt " * 40000) + " ../wall/clientA/docs"
        t0 = time.time()
        self.call("mcp__x__send", body=text)
        self.assertLess(time.time() - t0, 2.0)
        self.assertIsNotNone(self.call("mcp__x__send", body="line one\n../wall/clientA/docs\nline three"))

    def test_mcp_relative_paths_outside_the_wall_stay_allowed(self):
        for val in ("../work/a.txt", "./a.txt", "application/json", "src/index.ts", "../wall/open/ok.txt", ".", "..", "https://example.com/a/b", "just words"):
            self.assertIsNone(self.call("mcp__fs__read", path=val), val)
        self.assertIsNone(self.call("mcp__x__ask", prompt="compare work/a.txt with ../wall/open/ok.txt"))


class BypassGapTests(Fixture):
    """Four gaps the automated security review found after the tier 1 merge (issue 228, PR 230), and issue 233. Fake folders only."""

    def call(self, tool, cwd=None, **ti):
        return w.evaluate({"tool_name": tool, "tool_input": ti, "cwd": cwd or self.p("work")}, w.load_paths())

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"))
        h = self.home
        for d in (".claude/hooks", ".claude/scripts", ".claude/commands", ".claude/projects", "empty"):
            os.makedirs(os.path.join(h, d))
        for f in (".claude/hooks/x.py", ".claude/hooks/no-ai-wall-guard.py", ".claude/scripts/x.py", ".claude/scripts/no_ai_wall.py",
                  ".claude/settings.json", ".claude/commands/c.md", ".claude/projects/p.txt"):
            with open(os.path.join(h, f), "w") as fh:
                fh.write("x")
        self.d = lambda rel: os.path.join(h, ".claude", rel)

    def denied(self, cmd, what="protected file"):
        r = self.call("Bash", command=cmd)
        self.assertIsNotNone(r, cmd)
        self.assertEqual(r[1], what, cmd)

    def allowed(self, cmd):
        self.assertIsNone(self.call("Bash", command=cmd), cmd)

    # HIGH: wildcards in an operand
    def test_wildcard_operands_that_reach_the_guarded_places_are_writes(self):
        h = self.home
        for cmd in ("rm -rf %s/.claude/h*" % h, "rm -rf %s/.cl*" % h, "rm -rf %s/.claude/hooks/*" % h, "chmod 000 %s/.c*/scripts" % h,
                    "mv %s/.claude/s* /tmp/x" % h, "rm %s/.claude/hooks/no-ai-*" % h, "rm -rf %s/.claude/{hooks,junk}" % h, "rm -rf %s/.claude/ho?ks" % h,
                    "rm -rf %s/.claude/[h]ooks" % h, "cd %s/.claude && rm -rf h*" % h, "rm -rf /*", "rm %s/.claude/scripts/*.py" % h,
                    "sed -i s/a/b/ %s/.claude/hooks/*.py" % h, "rm -rf %s/.claude/**" % h):
            self.denied(cmd)

    def test_wildcard_operands_elsewhere_stay_allowed(self):
        h = self.home
        for cmd in ("rm /tmp/a*", "rm -rf %s/.claude/commands/*" % h, "rm %s/.claude/projects/*" % h, "rm -rf %s/.claude/{commands,projects}" % h,
                    "chmod 644 %s/.claude/commands/*.md" % h, "rm -rf %s/work/*.txt" % self.tmp):
            self.allowed(cmd)

    # HIGH: copy-target options in every form getopt accepts
    def test_copy_target_option_forms_are_destinations(self):
        s, hk = self.d("scripts"), self.d("hooks")
        for cmd in ("cp -at %s /tmp/a" % s, "cp -vt %s /tmp/a" % s, "cp -avt %s /tmp/a" % hk, "cp --target %s /tmp/a" % s, "cp --targ=%s /tmp/a" % s,
                    "cp --t=%s /tmp/a" % s, "cp --target-dir %s /tmp/a" % s, "install -vt %s /tmp/a" % s, "cp /tmp/a -t %s" % s, "cp -t%s /tmp/a" % s,
                    "cp -aT /tmp/a %s/x.py" % s, "cp -r /tmp/empty %s" % self.d(""), "cp -a /tmp/empty/. %s/" % self.d(""),
                    "cp /tmp/a %s/*.py" % s, "install -m 755 /tmp/a %s/new.sh" % hk):
            self.denied(cmd)

    def test_reading_guarded_files_with_copy_options_stays_allowed(self):
        s, h = self.d("scripts"), self.home
        for cmd in ("cp -at /tmp/dest %s/x.py" % s, "cp -vt /tmp/dest %s/x.py" % self.d("hooks"), "cp --target /tmp/dest %s/x.py" % s,
                    "cp --targ=/tmp/dest %s/x.py" % s, "cp %s/x.py /tmp/dest/" % s, "cp %s /tmp/backup/" % self.d("settings.json"),
                    "cp /tmp/a %s/" % self.d("commands"), "cp -r %s /tmp/backup/" % self.d("projects"), "cp -a %s/.claude/skills /tmp/b/" % h,
                    "rsync -a %s/x.py /tmp/b/" % s):
            self.allowed(cmd)

    # MEDIUM: cp, install, rsync and ln no longer skip the folder check
    def test_dest_last_programs_may_not_replace_or_empty_the_guarded_folders(self):
        h = self.home
        for cmd in ("rsync -a --delete /tmp/empty/ %s/.claude/" % h, "rsync -a --delete /tmp/empty/ %s/" % self.d("scripts"), "rsync -a /tmp/empty/ %s/.claude/" % h,
                    "rsync -a --remove-source-files %s/x.py /tmp/b/" % self.d("hooks"), "rsync -a --delete-after /tmp/empty/ %s/" % self.d("hooks"),
                    "ln -sfn /tmp/evil %s" % self.d("hooks"), "ln -sfn /tmp/evil %s" % self.d("scripts"), "ln -sfn /tmp/evil %s" % self.d("settings.json"),
                    "cp --remove-destination /tmp/a %s" % self.d("settings.json"), "cp -T %s/x.py /tmp/y" % self.d("hooks"),
                    "cp -aT /tmp/empty %s" % self.d("scripts")):
            self.denied(cmd)

    def test_find_and_fd_that_delete_or_run_commands_in_the_guarded_places_are_writes(self):
        h = self.home
        for cmd in ("find %s/.claude/hooks -delete" % h, "find %s/.claude -name '*.py' -delete" % h, "find %s/.claude/scripts -exec rm {} +" % h,
                    "find %s -path '*/hooks/*' -delete" % self.d(""), "find %s/.claude/hooks -execdir sh -c 'echo > x' ;" % h,
                    "fd -x rm . %s/.claude/scripts" % h, "find %s/.claude -fprint %s/x.py" % (h, self.d("hooks"))):
            self.denied(cmd)
        for cmd in ("find %s/.claude/scripts -name '*.py'" % h, "find %s -delete" % self.p("work"), "find %s/.claude/commands -delete" % h,
                    "find %s/.claude/hooks -type f -print" % h, "fd py %s/.claude/scripts" % h):
            self.allowed(cmd)

    # MEDIUM: one normaliser for Windows drives
    def test_windows_drive_paths_ignore_case_in_every_check(self):
        os.environ["HOME"] = "/mnt/c/Users/Someone"
        for cmd in ("rm -rf /mnt/C/USERS/SOMEONE/.CLAUDE", "mv /MNT/c/Users/Someone/.claude/SCRIPTS /tmp/x", "chmod 000 /mnt/c/users/someone/.Claude/Hooks",
                    "ln -sfn /tmp/evil /mnt/c/USERS/someone/.claude/HOOKS", "rsync -a --delete /tmp/empty/ /mnt/C/Users/SOMEONE/.claude/"):
            self.denied(cmd)
        f = "/mnt/c/Users/Someone/.config/afa/no-ai-paths.txt"
        self.assertTrue(w._is_protected("/MNT/C/users/someone/.config/AFA/no-ai-paths.txt", "/", [f]))
        self.assertTrue(w._reaches("/mnt/C/USERS", "/", [f]))
        self.assertTrue(w._reaches("/mnt/c/users/SOMEONE/.config", "/", [f]))
        self.assertFalse(w._reaches("/mnt/d/USERS", "/", [f]))
        self.assertFalse(w._is_protected("/home/someone/.config/afa/no-ai-paths.txt", "/", [f]))
        self.assertFalse(w._is_protected("/Home/x/no-ai-paths.txt", "/", ["/home/x/no-ai-paths.txt"]))  # outside /mnt, case matters

    # issue 233: a lane launcher is not a search
    def test_lane_launcher_with_a_parent_of_a_listed_folder_is_allowed(self):
        parent = self.p("wall")
        for cmd in ("lane.sh --console --headless n %s sonnet /tmp/p.md" % parent, "~/.claude/scripts/lane.sh n %s sonnet p.md; ls -R %s" % (parent, self.p("work")),
                    "lane.sh n %s sonnet p.md && find %s -name x" % (parent, self.p("work")), "git -C %s grep x; lane.sh n %s s p.md" % (self.p("work"), parent),
                    "bash ~/.claude/scripts/lane.sh n %s sonnet p.md 2>&1 | tail -3; rg x %s" % (parent, self.p("work"))):
            self.allowed(cmd)

    def test_lane_launcher_still_refuses_a_listed_folder_and_hides_no_search(self):
        wall, parent = self.p("wall/clientA"), self.p("wall")
        self.denied("lane.sh n %s sonnet p.md" % wall, "entry 1")
        self.denied("lane.sh n %s/docs sonnet p.md" % wall, "entry 1")
        self.denied("lane.sh n %s sonnet p.md; find %s -name x" % (self.p("work"), parent), "entry 1")
        self.denied("lane.sh n %s sonnet $(find %s -name x)" % (self.p("work"), parent), "entry 1")
        self.denied("lane.sh n %s sonnet `rg x %s`" % (self.p("work"), parent), "entry 1")
        self.denied("find %s -name x; lane.sh n /tmp s p.md" % parent, "entry 1")
        self.denied("bash -c 'lane.sh n /tmp s p.md; find %s'" % parent, "entry 1")


if __name__ == "__main__":
    unittest.main()
