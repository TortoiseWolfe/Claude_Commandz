"""The no-AI wall's shared reader and its tool-call decisions. Every path here is a fake folder under a temp dir."""
import io
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "no-ai-wall")  # the wall's own folder in the repo (mirror of ~/.claude/no-ai-wall)
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
        for f in (self.pfile, os.path.join(h, ".claude/no-ai-wall/no-ai-wall-guard.py"), os.path.join(h, ".claude/no-ai-wall/no_ai_wall.py"), os.path.join(h, ".claude/no-ai-wall/no_ai_wall.sh")):
            for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
                r = self.call(tool, **{key: f})
                self.assertIsNotNone(r, (tool, f))
                self.assertEqual(r[1], "protected file")

    def test_protected_file_reached_through_a_symlink_or_dots(self):
        os.makedirs(os.path.join(self.home, ".claude/no-ai-wall"))
        os.symlink(self.pfile, self.p("work/shortcut.txt"))
        self.assertIsNotNone(self.call("Edit", file_path="shortcut.txt"))
        self.assertIsNotNone(self.call("Edit", file_path=self.p("work/../paths.txt")))

    def test_bash_that_writes_a_protected_file_is_denied(self):
        h = self.home
        for cmd in ("echo x >> " + self.pfile, "sed -i 's/a/b/' " + self.pfile, "rm " + self.pfile, "mv %s /tmp/x" % self.pfile,
                    "cp /tmp/x " + os.path.join(h, ".claude/no-ai-wall/no_ai_wall.py"), "tee " + os.path.join(h, ".claude/no-ai-wall/no-ai-wall-guard.py"),
                    "python3 -c \"open('%s','w')\"" % self.pfile, "cat %s > /dev/null" % self.pfile, "truncate -s0 " + self.pfile):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)

    def test_bash_may_check_the_list_exists_or_view_it_through_redact_view(self):
        rv = os.path.join(self.home, ".claude/scripts/redact_view.py")
        self.assertIsNone(self.call("Bash", command="ls -l " + self.pfile))
        self.assertIsNone(self.call("Bash", command="wc -l " + self.pfile))
        self.assertIsNone(self.call("Bash", command="python3 %s %s" % (rv, self.pfile)))
        self.assertIsNone(self.call("Bash", command="python3 -I %s status" % os.path.join(self.home, ".claude/no-ai-wall/no_ai_wall.py")))
        self.assertIsNotNone(self.call("Bash", command="python3 /tmp/other.py " + self.pfile))
        self.assertIsNotNone(self.call("Bash", command="cat " + self.pfile))  # the contents stay out of the transcript

    def test_read_tool_refuses_the_list_but_may_read_the_hook_and_module(self):
        self.assertIsNotNone(self.call("Read", file_path=self.pfile))
        self.assertIsNone(self.call("Read", file_path=os.path.join(self.home, ".claude/no-ai-wall/no_ai_wall.py")))

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
        """Everything in the wall's own folder (any name, new files too), the three places the wall lived before that folder existed, and settings."""
        h = self.home
        return [os.path.join(h, ".claude/no-ai-wall/other-hook.py"), os.path.join(h, ".claude/no-ai-wall/x.sh"),
                os.path.join(h, ".claude/no-ai-wall/notes.md"), os.path.join(h, ".claude/no-ai-wall/tests/test_x.py"),
                os.path.join(h, ".claude/settings.json"),
                os.path.join(h, ".claude/settings.local.json"), self.p("work/.claude/settings.json"),
                self.p("work/.claude/settings.local.json")]

    def ordinary_cases(self):
        """The narrow lock: other tooling under ~/.claude/scripts and ~/.claude/hooks is not guarded."""
        h = self.home
        return [os.path.join(h, ".claude/scripts/lane.sh"), os.path.join(h, ".claude/scripts/gmail_api.py"), os.path.join(h, ".claude/scripts/tests/test_x.py"),
                os.path.join(h, ".claude/scripts/install_global.py"), os.path.join(h, ".claude/hooks/coop-scrubber.py"), os.path.join(h, ".claude/hooks/other-hook.py"),
                os.path.join(h, ".claude/hooks/x.sh"), os.path.join(h, ".claude/no-ai-wall-notes/x.py")]

    def test_ordinary_scripts_and_hooks_stay_writable_with_the_list_full_or_empty(self):
        for entries in (None, "# nothing listed yet"):
            if entries:
                self.paths(entries)
            for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
                for f in self.ordinary_cases():
                    self.assertIsNone(self.call(tool, **{key: f}), (tool, f))

    def test_a_folder_that_only_starts_with_the_wall_name_is_not_the_wall(self):
        self.assertIsNone(self.call("Write", file_path=os.path.join(self.home, ".claude/no-ai-wall-extras/x.py")))
        self.assertIsNone(self.call("Bash", command="echo x > %s/.claude/no-ai-wall.bak" % self.home))

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
        for f in (os.path.join(h, ".claude/scripts/notes.md"), os.path.join(h, ".claude/hooks/data.json"), os.path.join(h, ".claude/commands/x.md"),
                  self.p("work/settings.json"), self.p("work/script.py"), self.p("work/.claude/commands/x.md"), os.path.join(h, ".claude/other.json")):
            self.assertIsNone(self.call("Write", file_path=f), f)

    def test_protected_script_reached_through_a_symlinked_folder(self):
        real = self.p("work/real-scripts")
        os.makedirs(real)
        os.makedirs(os.path.join(self.home, ".claude"))
        os.symlink(real, os.path.join(self.home, ".claude/no-ai-wall"))
        self.assertIsNotNone(self.call("Write", file_path=os.path.join(real, "x.py")))
        self.assertIsNotNone(self.call("Write", file_path=os.path.join(self.home, ".claude/no-ai-wall/x.py")))

    def test_bash_writes_to_hook_script_or_settings_files_are_denied(self):
        h = self.home
        s, py, sh = os.path.join(h, ".claude/settings.json"), os.path.join(h, ".claude/no-ai-wall/x.py"), os.path.join(h, ".claude/no-ai-wall/y.sh")
        proj = self.p("work/.claude/settings.local.json")
        for cmd in ("echo '{}' > " + s, "echo x >> " + py, "cat /tmp/a >| " + sh, "sed -i 's/a/b/' " + py, "tee " + sh + " < /tmp/a", "cp /tmp/a " + py,
                    "cp -f /tmp/a /tmp/b " + os.path.dirname(py) + "/", "mv /tmp/a " + s, "mv " + py + " /tmp/gone", "rm " + sh, "truncate -s0 " + proj,
                    "jq . /tmp/a > /tmp/b && mv /tmp/b " + s, "chmod +x " + py, "cd %s/.claude/no-ai-wall && echo x > x.py" % h, "python3 -c \"open('%s','w')\"" % s,
                    "install -m 755 /tmp/a " + sh, "ln -sf /tmp/evil " + py, "cp -t %s /tmp/a" % os.path.dirname(py), "perl -pi -e 's/a/b/' " + s):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)

    def test_bash_may_not_move_or_delete_the_folders_that_hold_the_guarded_files(self):
        h = self.home
        for cmd in ("mv %s/.claude/no-ai-wall /tmp/gone" % h, "rm -rf %s/.claude/no-ai-wall" % h, "rm -r %s/.claude" % h, "mv %s/.claude /tmp/x" % h,
                    "chmod 000 %s/.claude/no-ai-wall" % h, "rm -rf " + h, "cd %s/.claude && rm -rf no-ai-wall" % h, "mv ../home/.claude/no-ai-wall /tmp/y"):
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)
        for cmd in ("rm -rf %s/.claude/projects/x" % h, "mv %s/.claude/commands /tmp/c" % h, "rm -rf %s/.cache" % h, "rm /tmp/a"):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

    def test_cp_target_directory_forms_are_destinations(self):
        h = self.home
        d = os.path.join(h, ".claude/no-ai-wall")
        for cmd in ("cp --target-directory=%s /tmp/a" % d, "cp -t%s /tmp/a" % d, "cp --target-directory %s /tmp/a" % d, "install -t %s /tmp/a" % d):
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        self.assertIsNone(self.call("Bash", command="cp -t /tmp/dest %s/.claude/no-ai-wall/x.py" % h))

    def test_guarded_files_on_a_windows_drive_ignore_case(self):
        os.environ["HOME"] = "/mnt/c/Users/Someone"
        for f in ("/mnt/C/USERS/SOMEONE/.CLAUDE/Settings.JSON", "/mnt/c/users/someone/.claude/NO-AI-WALL/X.PY", "/mnt/c/Users/Someone/.Claude/no-ai-wall/y.SH"):
            r = self.call("Write", file_path=f)
            self.assertIsNotNone(r, f)
            self.assertEqual(r[1], "protected file", f)

    def test_bash_runs_and_reads_scripts_and_settings_freely(self):
        h = self.home
        s, py, sh = os.path.join(h, ".claude/settings.json"), os.path.join(h, ".claude/no-ai-wall/x.py"), os.path.join(h, ".claude/no-ai-wall/y.sh")
        for cmd in ("python3 " + py + " draft-create --to a", "bash " + sh, "cat " + s, "cp " + py + " /tmp/backup/", "cp -a %s %s /tmp/backup/" % (py, sh),
                    "diff " + py + " /tmp/x.py", "echo hi > /tmp/out.txt", "ls " + os.path.dirname(py), "grep -n def " + py, "cd %s/.claude/no-ai-wall && python3 x.py" % h):
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
        scripts = os.path.join(self.home, ".claude/no-ai-wall")
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
        sc = os.path.join(self.home, ".claude/no-ai-wall/x.sh")
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
        for d in (".claude/no-ai-wall", ".claude/scripts", ".claude/hooks", ".claude/commands", ".claude/projects", "empty"):
            os.makedirs(os.path.join(h, d))
        for f in (".claude/no-ai-wall/x.py", ".claude/no-ai-wall/no-ai-wall-guard.py", ".claude/no-ai-wall/no_ai_wall.py", ".claude/scripts/x.py", ".claude/hooks/y.sh",
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
        for cmd in ("rm -rf %s/.claude/n*" % h, "rm -rf %s/.cl*" % h, "rm -rf %s/.claude/no-ai-wall/*" % h, "chmod 000 %s/.c*/no-ai-wall" % h,
                    "mv %s/.claude/n* /tmp/x" % h, "rm %s/.claude/no-ai-wall/no-ai-*" % h, "rm -rf %s/.claude/{no-ai-wall,junk}" % h, "rm -rf %s/.claude/no-ai-w?ll" % h,
                    "rm -rf %s/.claude/[n]o-ai-wall" % h, "cd %s/.claude && rm -rf n*" % h, "rm -rf /*", "rm %s/.claude/no-ai-wall/*.py" % h,
                    "sed -i s/a/b/ %s/.claude/no-ai-wall/*.py" % h, "rm -rf %s/.claude/**" % h):
            self.denied(cmd)

    def test_wildcard_operands_elsewhere_stay_allowed(self):
        h = self.home
        for cmd in ("rm /tmp/a*", "rm -rf %s/.claude/commands/*" % h, "rm %s/.claude/projects/*" % h, "rm -rf %s/.claude/{commands,projects}" % h,
                    "chmod 644 %s/.claude/commands/*.md" % h, "rm -rf %s/work/*.txt" % self.tmp):
            self.allowed(cmd)

    # the narrow lock: the scripts and hooks folders hold ordinary tooling, so a tooling install runs without anyone's hands
    def test_shell_writes_to_ordinary_scripts_and_hooks_are_allowed(self):
        h = self.home
        for cmd in ("cp /tmp/a %s/.claude/scripts/lane.sh" % h, "cp -a /tmp/new/. %s/.claude/scripts/" % h, "cp -t %s/.claude/hooks /tmp/a" % h,
                    "install -m 755 /tmp/a %s/.claude/hooks/coop-scrubber.py" % h, "rsync -a /tmp/new/ %s/.claude/scripts/" % h,
                    "rsync -a --delete /tmp/new/ %s/.claude/scripts/" % h, "echo x > %s/.claude/scripts/y.sh" % h, "rm %s/.claude/scripts/x.py" % h,
                    "rm -rf %s/.claude/hooks" % h, "mv %s/.claude/scripts /tmp/old-scripts" % h, "chmod +x %s/.claude/scripts/x.py" % h,
                    "sed -i s/a/b/ %s/.claude/hooks/y.sh" % h, "cd %s/.claude/scripts && echo x > x.py" % h, "find %s/.claude/scripts -name '*.pyc' -delete" % h,
                    "python3 -c \"open('%s/.claude/scripts/x.py','w')\"" % h, "tee %s/.claude/scripts/z.py" % h):
            self.allowed(cmd)

    def test_shell_writes_to_the_wall_folder_and_settings_are_still_denied(self):
        h = self.home
        for cmd in ("cp /tmp/a %s/.claude/no-ai-wall/no_ai_wall.py" % h, "cp -a /tmp/new/. %s/.claude/no-ai-wall/" % h, "cp -t %s/.claude/no-ai-wall /tmp/a" % h,
                    "rsync -a --delete /tmp/new/ %s/.claude/no-ai-wall/" % h, "echo x > %s/.claude/no-ai-wall/new.txt" % h, "rm -rf %s/.claude/no-ai-wall" % h,
                    "mv %s/.claude/no-ai-wall /tmp/gone" % h, "rm -rf %s/.claude" % h, "chmod 000 %s/.claude/no-ai-wall" % h,
                    "cd %s/.claude/no-ai-wall && echo x > y" % h, "echo x > %s/.claude/settings.json" % h):
            self.denied(cmd)

    def test_a_machine_not_yet_moved_keeps_its_old_wall_files_protected(self):
        """Until the install moves the wall into its own folder, the hook, module and helper still sit in hooks and scripts: they stay locked,
        and nothing else in those folders is."""
        h = self.home
        old = [self.d("hooks/no-ai-wall-guard.py"), self.d("scripts/no_ai_wall.py"), self.d("scripts/no_ai_wall.sh")]
        for f in old:
            open(f, "w").write("x")
            for tool in ("Edit", "Write"):
                self.assertEqual(self.call(tool, file_path=f)[1], "protected file", (tool, f))
            self.denied("echo x > " + f)
            self.denied("rm " + f)
        self.denied("rm -rf %s/.claude/scripts" % h)  # a folder that holds one of them
        self.assertIsNone(self.call("Write", file_path=self.d("scripts/lane.sh")))
        self.allowed("cp /tmp/a %s/.claude/scripts/lane.sh" % h)
        for f in old:
            os.remove(f)
        self.allowed("rm -rf %s/.claude/scripts" % h)  # once they are gone the folder is ordinary tooling

    def test_the_wall_may_be_run_from_its_folder_and_looked_at(self):
        h = self.home
        self.allowed("python3 -I %s/.claude/no-ai-wall/no_ai_wall.py status" % h)
        self.allowed("ls -l %s/.claude/no-ai-wall" % h)
        self.assertIsNone(self.call("Read", file_path=os.path.join(h, ".claude/no-ai-wall/no_ai_wall.py")))

    # issue 233 (second comment): a subagent prompt is judged by what the subagent does, not by the words in it
    def test_a_subagent_prompt_naming_protected_files_is_allowed_but_not_listed_paths(self):
        h = self.home
        prompt = "Read %s/.claude/no-ai-wall/no_ai_wall.py, %s/.claude/hooks/x.sh and report the scope; do not edit anything." % (h, h)
        for tool in ("Agent", "Task"):
            self.assertIsNone(self.call(tool, prompt=prompt, description="read-only"), tool)
            r = self.call(tool, prompt="summarise %s/wall/clientA/docs/secret.txt" % self.tmp)
            self.assertIsNotNone(r, tool)
            self.assertNotEqual(r[1], "protected file")
        self.assertIsNotNone(self.call("Agent", prompt="x", cwd=self.p("wall/clientA")))  # a session inside a listed folder is refused for every tool
        self.assertIsNotNone(self.call("mcp__fs__write_file", path="%s/.claude/no-ai-wall/no_ai_wall.py" % h, content="x"))

    # HIGH: copy-target options in every form getopt accepts
    def test_copy_target_option_forms_are_destinations(self):
        s, hk = self.d("no-ai-wall"), self.d("no-ai-wall")
        for cmd in ("cp -at %s /tmp/a" % s, "cp -vt %s /tmp/a" % s, "cp -avt %s /tmp/a" % hk, "cp --target %s /tmp/a" % s, "cp --targ=%s /tmp/a" % s,
                    "cp --t=%s /tmp/a" % s, "cp --target-dir %s /tmp/a" % s, "install -vt %s /tmp/a" % s, "cp /tmp/a -t %s" % s, "cp -t%s /tmp/a" % s,
                    "cp -aT /tmp/a %s/x.py" % s, "cp -r /tmp/empty %s" % self.d(""), "cp -a /tmp/empty/. %s/" % self.d(""),
                    "cp /tmp/a %s/*.py" % s, "install -m 755 /tmp/a %s/new.sh" % hk):
            self.denied(cmd)

    def test_reading_guarded_files_with_copy_options_stays_allowed(self):
        s, h = self.d("no-ai-wall"), self.home
        for cmd in ("cp -at /tmp/dest %s/x.py" % s, "cp -vt /tmp/dest %s/x.py" % self.d("no-ai-wall"), "cp --target /tmp/dest %s/x.py" % s,
                    "cp --targ=/tmp/dest %s/x.py" % s, "cp %s/x.py /tmp/dest/" % s, "cp %s /tmp/backup/" % self.d("settings.json"),
                    "cp /tmp/a %s/" % self.d("commands"), "cp -r %s /tmp/backup/" % self.d("projects"), "cp -a %s/.claude/skills /tmp/b/" % h,
                    "rsync -a %s/x.py /tmp/b/" % s):
            self.allowed(cmd)

    # MEDIUM: cp, install, rsync and ln no longer skip the folder check
    def test_dest_last_programs_may_not_replace_or_empty_the_guarded_folders(self):
        h = self.home
        for cmd in ("rsync -a --delete /tmp/empty/ %s/.claude/" % h, "rsync -a --delete /tmp/empty/ %s/" % self.d("no-ai-wall"), "rsync -a /tmp/empty/ %s/.claude/" % h,
                    "rsync -a --remove-source-files %s/x.py /tmp/b/" % self.d("no-ai-wall"), "rsync -a --delete-after /tmp/empty/ %s/" % self.d("no-ai-wall"),
                    "ln -sfn /tmp/evil %s" % self.d("no-ai-wall"), "ln -sfn /tmp/evil %s" % self.d("no-ai-wall"), "ln -sfn /tmp/evil %s" % self.d("settings.json"),
                    "cp --remove-destination /tmp/a %s" % self.d("settings.json"), "cp -T %s/x.py /tmp/y" % self.d("no-ai-wall"),
                    "cp -aT /tmp/empty %s" % self.d("no-ai-wall")):
            self.denied(cmd)

    def test_find_and_fd_that_delete_or_run_commands_in_the_guarded_places_are_writes(self):
        h = self.home
        for cmd in ("find %s/.claude/no-ai-wall -delete" % h, "find %s/.claude -name '*.py' -delete" % h, "find %s/.claude/no-ai-wall -exec rm {} +" % h,
                    "find %s -path '*/hooks/*' -delete" % self.d(""), "find %s/.claude/no-ai-wall -execdir sh -c 'echo > x' ;" % h,
                    "fd -x rm . %s/.claude/no-ai-wall" % h, "find %s/.claude -fprint %s/x.py" % (h, self.d("no-ai-wall"))):
            self.denied(cmd)
        for cmd in ("find %s/.claude/no-ai-wall -name '*.py'" % h, "find %s -delete" % self.p("work"), "find %s/.claude/commands -delete" % h,
                    "find %s/.claude/no-ai-wall -type f -print" % h, "fd py %s/.claude/no-ai-wall" % h):
            self.allowed(cmd)

    # follow-ups from the plugin's security review of the first push
    def test_brace_sequences_and_oversize_braces_fail_closed(self):
        h = self.home
        for cmd in ("rm -rf %s/.claude/{a..z}o-ai-wall" % h, "rm %s/.claude/no-ai-wall/x{1..3}.py" % h, "rm -rf %s/.claude/no-ai-wall{,.bak}" % h,
                    "rm %s/.claude/no-ai-wall/x{1..999999}" % h, "rm %s/.claude/{a,b,c,d,e,f,g,h}{1..60}" % h, "rm -rf %s/.claude/{x,{no-ai-wall,y}}" % h):
            self.denied(cmd)
        self.assertEqual(w._brace_expand("a{1..3}")[0], ["a1", "a2", "a3"])
        self.assertEqual(w._brace_expand("{c..a}")[0], ["c", "b", "a"])
        self.assertEqual(w._brace_expand("x{1..9..4}"), (["x1", "x5", "x9"], True))
        self.assertFalse(w._brace_expand("x{1..9999999}")[1])
        for cmd in ("rm /tmp/f{1..3}.txt", "rm %s/.claude/projects/p{1..3}.txt" % h, "find %s -name x -exec echo {} +" % self.p("work")):
            self.allowed(cmd)

    def test_an_option_value_after_the_operands_does_not_hide_the_destination(self):
        s, hk = self.d("no-ai-wall"), self.d("no-ai-wall")
        for cmd in ("cp /tmp/a %s/x.py -S .bak" % s, "cp /tmp/a %s/x.py --suffix .bak" % s, "cp /tmp/a %s/x.py --suf .bak" % s, "install /tmp/a %s/y.sh -m 755" % hk,
                    "install /tmp/a %s/y.sh -o root -g root" % hk, "rsync -a /tmp/empty/ %s/ --exclude x" % hk, "rsync -a /tmp/empty/ %s/ -e ssh" % hk,
                    "ln -sf /tmp/evil %s/x.py -S .bak" % s, "cp /tmp/a %s/x.py -v -S .bak" % s):
            self.denied(cmd)
        for cmd in ("cp -S .bak /tmp/a /tmp/b", "install -m 755 /tmp/a /tmp/b", "cp /tmp/a /tmp/b --suffix .bak", "rsync -a /tmp/a/ /tmp/b/ --exclude x",
                    "cp -a /tmp/a /tmp/b -v"):
            self.allowed(cmd)

    def test_find_and_fd_with_no_root_start_in_the_working_folder(self):
        h = self.home
        for cmd in ("cd %s/.claude/no-ai-wall && find -delete" % h, "cd %s/.claude/no-ai-wall && find -name '*.py' -delete" % h,
                    "cd %s/.claude/no-ai-wall && fd -x rm" % h, "cd %s/.claude && find -L -delete" % h, "cd %s/.claude/no-ai-wall && find . -delete" % h):
            self.denied(cmd)
        for cmd in ("cd %s && find -delete" % self.p("work"), "cd %s/.claude/commands && find -delete" % h, "cd %s/.claude/no-ai-wall && find -name '*.py'" % h):
            self.allowed(cmd)
        self.assertIsNone(self.call("Bash", cwd=self.p("work"), command="find -name x -delete"))
        self.assertIsNotNone(self.call("Bash", cwd=self.d("no-ai-wall"), command="find -name x -delete"))

    # a directory change the text cannot resolve: the working folder is unknown, so relative targets fail closed
    def test_directory_changes_the_parser_cannot_resolve_fail_closed(self):
        h = self.home
        for cmd in ("cd - && rm x.py", "cd %s/.claude/no-ai-wall; cd - ; rm x.py" % h, "popd && find -delete", "cd $WHERE && rm x.py",
                    "cd $(pwd)/../.claude/no-ai-wall && rm x.py", "cd `echo hooks` && find -delete", "builtin cd - && rm no-ai-wall-guard.py",
                    "env -C %s/.claude/no-ai-wall rm x.py" % h, "env -C %s/.claude/no-ai-wall find -delete" % h, "cd %s/.claude && cd - && echo hi > x.py" % h,
                    "sudo -u root rm %s/.claude/no-ai-wall/x.py" % h):
            self.denied(cmd)
        # known folders: a bare cd or cd ~ goes to the home folder, pushd and builtin cd work like cd
        for cmd in ("cd && rm .claude/no-ai-wall/x.py", "cd ~ && rm .claude/no-ai-wall/x.py", "pushd %s/.claude/no-ai-wall && rm x.py" % h,
                    "builtin cd %s/.claude/no-ai-wall && find -delete" % h, "command cd %s/.claude/no-ai-wall && rm x.py" % h):
            self.denied(cmd)
        for cmd in ("cd %s && cd - && ls" % self.p("work"), "cd %s/.claude/commands && rm c.md" % h, "pushd %s && rm y.txt" % self.p("work"),
                    "env -C %s ls" % self.p("work"), "cd - && cat x.py"):
            self.allowed(cmd)

    def test_a_directory_change_behind_a_keyword_or_brace_fails_closed(self):
        h = self.home
        for cmd in ("if true; then cd %s/.claude/no-ai-wall; rm x.py; fi" % h, "{ cd %s/.claude/no-ai-wall; rm x.py; }" % h, "time cd %s/.claude/no-ai-wall && rm x.py" % h,
                    "FOO=1 cd %s/.claude/no-ai-wall && rm x.py" % h, "for d in a; do cd $d; find -delete; done", "(cd %s/.claude/no-ai-wall && rm x.py)" % h):
            self.denied(cmd)
        self.allowed("echo hi; rm %s/.claude/commands/c.md" % h)  # no directory change: an absolute operand is judged on its own

    def test_wrapper_options_do_not_hide_an_inline_interpreter_or_a_delay_value(self):
        h = self.home
        for cmd in ("env -C %s/.claude python3 -c 'open(\"hooks/x.py\",\"w\")'" % h, "sudo -u root bash -c 'rm %s/.claude/no-ai-wall/x.py'" % h,
                    "timeout 5 rm %s/.claude/no-ai-wall/x.py" % h, "nice -n 5 rm %s/.claude/no-ai-wall/x.py" % h, "sudo -E node -e 'x(\"%s/.claude/no-ai-wall/x.py\")'" % h):
            self.denied(cmd)
        self.allowed("sudo -u root ls %s/.claude/no-ai-wall" % h)

    def test_rsync_option_values_are_destinations(self):
        h = self.home
        for cmd in ("rsync --log-file=%s/.claude/no-ai-wall/x.py /tmp/a /tmp/b" % h, "rsync --log-file %s/.claude/no-ai-wall/x.py /tmp/a /tmp/b" % h,
                    "rsync --log=%s/.claude/no-ai-wall/x.py /tmp/a /tmp/b" % h, "rsync --backup-dir %s/.claude/no-ai-wall /tmp/a /tmp/b" % h,
                    "rsync -T %s/.claude/no-ai-wall /tmp/a /tmp/b" % h, "rsync -aT%s/.claude/no-ai-wall /tmp/a /tmp/b" % h,
                    "rsync --write-batch=%s/.claude/no-ai-wall/x.py /tmp/a /tmp/b" % h, "rsync --partial-dir=%s/.claude/no-ai-wall /tmp/a /tmp/b" % h):
            self.denied(cmd)
        for cmd in ("rsync -av --log-file=/tmp/log /tmp/a /tmp/b", "rsync -av %s/.claude/no-ai-wall/x.py /tmp/b" % h,
                    "rsync -av --exclude %s/.claude/no-ai-wall/x.py /tmp/a /tmp/b" % h):
            self.allowed(cmd)

    # MEDIUM: one normaliser for Windows drives
    def test_windows_drive_paths_ignore_case_in_every_check(self):
        os.environ["HOME"] = "/mnt/c/Users/Someone"
        for cmd in ("rm -rf /mnt/C/USERS/SOMEONE/.CLAUDE", "mv /MNT/c/Users/Someone/.claude/NO-AI-WALL /tmp/x", "chmod 000 /mnt/c/users/someone/.Claude/No-AI-Wall",
                    "ln -sfn /tmp/evil /mnt/c/USERS/someone/.claude/NO-AI-WALL", "rsync -a --delete /tmp/empty/ /mnt/C/Users/SOMEONE/.claude/"):
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
        for cmd in ("lane.sh --console --headless n %s sonnet /tmp/p.md" % parent, "~/.claude/no-ai-wall/lane.sh n %s sonnet p.md; ls -R %s" % (parent, self.p("work")),
                    "lane.sh n %s sonnet p.md && find %s -name x" % (parent, self.p("work")), "git -C %s grep x; lane.sh n %s s p.md" % (self.p("work"), parent),
                    "bash ~/.claude/no-ai-wall/lane.sh n %s sonnet p.md 2>&1 | tail -3; rg x %s" % (parent, self.p("work"))):
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
        # a wrapper option before lane.sh is not a plain launch, so no scan for a "known program" may make it one
        self.denied("env -S 'find %s' lane.sh n /tmp s p.md" % parent, "entry 1")
        self.denied("flock -c 'rg x %s' lane.sh n /tmp s p.md" % parent, "entry 1")
        self.denied("timeout 5 find %s -name x; lane.sh n /tmp s p.md" % parent, "entry 1")

    # issue 71: a `>` inside the text handed to a search or print program is text, not a redirect
    def test_quoted_text_of_a_search_or_print_program_is_not_a_write(self):
        s = "~/.claude/settings.json"
        for cmd in ("node x.js > /tmp/out.txt 2>&1; grep -E 'MISSING|Location|=>|READ-BACK' /tmp/out.txt",  # the three from the issue
                    "cat > /tmp/issue.md <<'EOF'\nexample: echo x > %s\nEOF" % s,
                    "grep -n '> %s' /tmp/notes.md" % s,
                    'grep -E "a|=>|b" /tmp/out.txt', "echo 'never run: echo x > %s'" % s, 'echo "a > $HOME/.claude/settings.json"',
                    "cd $NOWHERE && grep -E 'a|=>|b' out.txt", "cat > /tmp/issue.md << 'EOF'\nexample: echo x > %s\nEOF" % s,
                    "echo ok; cat <<EOF > /tmp/x\ntext > %s and $HOME\nEOF" % s, "cat <<'A'\nfoo > %s\nA\ngrep 'q>%s' /tmp/f" % (s, s),
                    "cat <<'EOF'\n EOF\nstill body > %s\nEOF" % s, "grep 'x > %s' /tmp/f | sort | uniq -c | head -3" % s,
                    "grep 'x > %s' /tmp/f |\nhead -3" % s, "echo 'x > %s' &" % s, "grep 'x > %s' /tmp/f 2>&1 | head" % s,
                    "echo 'x > %s' >&2" % s, "grep -c 'x > %s' /tmp/f 2>/dev/null" % s, "grep 'x > %s' /tmp/f | cat <&0 | head -3" % s,
                    "grep 'x > %s' /tmp/f | sort -k2,2n -u | uniq -c" % s, "grep 'x > %s' /tmp/f | sort -rn | tail -2" % s,
                    "node x.js > /tmp/out.txt 2>&1; grep -E 'a|=>|b' /tmp/out.txt | sort | uniq -c | head -3"):
            self.allowed(cmd)

    def test_a_real_write_beside_quoted_text_is_still_denied(self):
        s = "~/.claude/settings.json"
        for cmd in ("echo x > %s" % s, "echo x >> %s" % s, "echo x > '%s'" % s, 'echo x > "$HOME/.claude/settings.json"', "echo 'a'>%s" % s,
                    "echo x >| %s" % s, "echo 'a > b' > %s" % s, 'echo "a > b" > %s' % s, "echo 'a' 'b' > %s" % s, "echo 'a>b' >| %s" % s,
                    "echo 'a>b' &> %s" % s, "grep 'a>b' /tmp/f; rm %s" % s, "echo 'x' | tee %s" % s, "cat > %s <<'EOF'\nhello\nEOF" % s,
                    "cat <<'EOF'\nhello\nEOF\necho x > %s" % s, "cat <<-'EOF'\n\thello\n\tEOF\necho x > %s" % s,
                    "cat <<-EOF\n\ttext\n\tEOF\necho x > %s" % s, "cat <<'EOF'\nEOF2\nEOF\necho x > %s" % s,
                    "cat <<'A' <<'B'\nx\nA\ny\nB\necho z > %s" % s, "echo 'ok > fine' && bash -c \"echo y > %s\"" % s):
            self.denied(cmd)

    def test_quoted_text_that_a_shell_could_still_run_keeps_the_strict_reading(self):
        s = "~/.claude/settings.json"
        text = "echo x > %s" % s  # harmless as text; each command below hands it to something that runs it
        for cmd in ("echo '%s' | bash" % text, "echo '%s' 2>&1 | bash" % text, "echo '%s' |\nbash" % text, "echo '%s' | env bash" % text,
                    "echo '%s' | (bash)" % text, "cat <<'EOF' | sh\n%s\nEOF" % text, "cat <<'EOF' |\n%s\nEOF\nsh" % text,
                    "bash <<'EOF'\n%s\nEOF" % text, "sh -s <<'EOF'\n%s\nEOF" % text, "bash <<< '%s'" % text, "bash <(echo '%s')" % text,
                    "bash -c '%s'" % text, "eval '%s'" % text, 'echo "$(%s)"' % text, 'echo "`%s`"' % text, "cat <<EOF\n$(%s)\nEOF" % text,
                    "eval $(echo a; echo 'b > %s'; echo c)" % s, "echo x | xargs -I{} sh -c 'echo {} > %s'" % s,
                    "tmux send-keys '%s' Enter" % text,
                    # a block's output is piped as a whole
                    "{ :; echo '%s'; } | bash" % text, "if true; then :; echo '%s'; fi | bash" % text,
                    "for i in 1; do :; echo '%s'; done | bash" % text, "while true; do :; echo '%s'; break; done | sh" % text,
                    "if true; then echo 'x > %s'; fi" % s,
                    # the text lands in a file that the same command runs
                    "echo '%s' > /tmp/x.sh; bash /tmp/x.sh" % text, "cat > /tmp/x.sh <<'EOF'\n%s\nEOF\nbash /tmp/x.sh" % text,
                    "echo '%s' >> /tmp/x.sh && . /tmp/x.sh" % text, "echo '%s' | sort -o /tmp/x.sh; bash /tmp/x.sh" % text,
                    "echo '%s' | uniq - /tmp/x.sh; bash /tmp/x.sh" % text, "exec 3>/tmp/x.sh; echo '%s' >&3; bash /tmp/x.sh" % text,
                    # the program's name cannot be trusted
                    "grep() { eval \"$2\"; }; grep -e '%s'" % text, "hash -p /bin/bash grep; grep -c '%s'" % text, "alias echo=eval; echo '%s'" % text,
                    ">grep bash -c '%s'" % text, "E=1 echo 'x > %s'" % s, "\"grep\" 'x > %s' /tmp/f" % s, "\\grep 'x > %s' /tmp/f" % s,
                    # ... also when the renaming sits behind another first word, or is a variable or search-path change
                    "builtin hash -p /bin/bash grep; grep -c '%s'" % text, "command alias echo=eval; echo '%s'" % text,
                    "builtin enable -f /tmp/x.so grep; grep -c '%s'" % text, "eval \"alias echo=eval\"; echo '%s'" % text,
                    "source /tmp/defs.sh; grep -c '%s'" % text, ". /tmp/defs.sh; grep -c '%s'" % text,
                    "export PATH=.:$PATH; cat '%s'" % text, "PATH=.:$PATH; cat '%s'" % text, "BASH_ENV=/tmp/e; echo '%s'" % text,
                    "ENV=/tmp/e; echo '%s'" % text, "$X; grep -c '%s'" % text, "shopt -s expand_aliases; echo '%s'" % text,
                    # a redirect with a quote or backslash glued on is a longer word, and that word names a file
                    "echo '%s' >&2'.sh'; bash 2.sh" % text, "echo '%s' >&2\".sh\"; bash 2.sh" % text, "echo '%s' >&1\\.sh; bash 1.sh" % text,
                    "echo '%s' >/dev/null'.sh'; bash /dev/null.sh" % text,
                    # after `--` a word starting with "-" is a file
                    "echo '%s' | uniq -- - -x.sh; bash ./-x.sh" % text, "echo '%s' | sort -- -o; bash /tmp/x.sh" % text,
                    # a copy of an open file (`<&0`) is a redirect, not the end of the pipeline: the text still reaches the shell
                    "echo '%s' | cat <&0 | bash" % text, "echo '%s' | cat 0<&0 | sh" % text, "echo '%s' | sort <&0 | sh" % text,
                    "echo '%s' | cat <&0 <&0 | . /dev/stdin" % text, "echo '%s' | cat 2<&1 | xargs -I{} sh -c {}" % text,
                    # sort can run a program on its lines, and a variable, brace or wildcard word can hide that option or an output file
                    "echo '%s' | sort -S 1 --compress-program=sh" % text, "echo '%s' | sort -S 1 --compress-prog sh" % text,
                    "X=--compress-program=sh; echo '%s' | sort -S 1 $X" % text, "echo '%s' | sort -S 1 {--compress-program=sh,-u}" % text,
                    "echo '%s' | sort -S 1 '--compress-program=sh'" % text, "echo '%s' | sort $X; bash /tmp/x.sh" % text,
                    "echo '%s' | uniq $X; bash /tmp/x.sh" % text, "echo '%s' | sort {-o,/tmp/x.sh}; bash /tmp/x.sh" % text,
                    # only space, tab and newline end a word in bash: `echo` with a carriage return glued on is another program
                    "echo\r '%s'" % text, "echo\r '%s' | cat" % text, "cat <<'EOF'\r\n%s\nEOF\n" % text,
                    # the text cannot be read with certainty
                    "echo hi # don't\n%s # isn't it" % text, "cat <<EOF\ntext > %s\n" % s, "echo 'unterminated > %s" % s,
                    "echo $'a\\'; %s; echo \\'b'" % text, "echo ${X:-'} ; %s ; echo '}" % text):
            self.denied(cmd)

    def test_quoted_text_reader_returns_the_command_unchanged_when_unsure(self):
        for cmd in ("echo 'a > b' | bash", "echo `date` 'a > b'", "echo $(date) 'a > b'", "echo 'a > b' # note", "echo 'a > b", "cat <<EOF\na > b",
                    "alias grep=bash; grep 'a > b'", "{ echo 'a > b'; }", "python3 -c 'a > b'", "echo 'a > b' > /tmp/f; sh /tmp/f"):
            self.assertEqual(w._quoted_text_as_text(cmd), cmd, cmd)
        self.assertEqual(w._quoted_text_as_text("grep -E 'a|=>|b' f; rm x"), "grep -E 'a =  b' f; rm x")
        self.assertEqual(w._quoted_text_as_text("cat > /tmp/f <<'EOF'\na > b\nEOF\necho done"), "cat > /tmp/f <<'EOF'\necho done")
        self.assertEqual(w._quoted_text_as_text("ls > out.txt"), "ls > out.txt")  # nothing outside quotes is ever touched


class Issue130Tests(Fixture):
    """Issue 130: ordinary lane commands were refused as a search into a listed folder, or as a change to the wall's files. Each shape below
    is the shape of a real refused command with the names made up. The session starts in self.tmp, a folder that holds a listed folder (the
    real session starts in a folder above the client areas). The second half proves the wall still refuses the real thing."""

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        self.lane = self.p("home/.claude/state/lanes/2026-01-01-demo")
        self.proj = self.p("work/proj")
        os.makedirs(os.path.join(self.lane, "sweep", "pw"))
        os.makedirs(os.path.join(self.proj, "src"))
        os.makedirs(w.wall_dir(), exist_ok=True)  # the wall's own folder exists on a real machine; the fake home needs one too
        self.assertTrue(os.path.isdir(self.p("wall/clientA")))

    def run_bash(self, command, cwd=None):
        return w.evaluate({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or self.tmp}, w.load_paths())

    def allowed(self, command, cwd=None):
        r = self.run_bash(command, cwd)
        self.assertIsNone(r, "wrongly refused: %s -> %s" % (command, r))

    def refused(self, command, cwd=None):
        r = self.run_bash(command, cwd)
        self.assertIsNotNone(r, "wrongly allowed: %s" % command)

    # ---- the refusals logged on the issue ----

    def test_heredoc_prose_with_search_words_is_not_a_search(self):
        self.allowed("cat > %s/note.md <<'EOF'\nfind them and search open and closed issues; du and tar are fine words\nEOF\nbash ~/.claude/scripts/lane.sh start %s" % (self.lane, self.lane))
        self.allowed("cat <<'EOF' > %s/prompt.md\nsearch every folder, find the list, tree of the issues\nEOF" % self.lane)

    def test_python_heredoc_with_grep_count_on_one_file(self):
        self.allowed("python3 - <<'EOF'\nimport subprocess\nprint('find it')\nEOF\ngrep -c done %s/status.md" % self.lane)
        self.allowed("python3 - <<'EOF'\nprint('git grep -r x')\nEOF\ngrep -c x %s/status.md" % self.lane)

    def test_cp_r_out_of_a_lane_folder_into_a_project(self):
        self.allowed("cp -r %s/sweep %s/src" % (self.lane, self.proj))
        self.allowed("L=%s; P=%s; mkdir -p $P/src && cp -r $L/sweep/* $P/src/ && ls -R $P | head" % (self.lane, self.proj))

    def test_git_show_piped_to_grep_is_not_git_grep(self):
        self.allowed("git show origin/main:README.md | grep -n phone")
        self.allowed("git log --oneline | grep -rn x %s/src" % self.proj)

    def test_named_root_is_the_only_root_walked(self):
        self.allowed("grep -rn pattern %s --include=*.py" % self.proj)
        self.allowed("find %s -name '*.md' -newer %s/status.md" % (self.proj, self.lane))

    def test_docker_run_with_a_lane_folder_mounted(self):
        self.allowed("L=%s; cd $L/sweep && timeout 600 docker-heavy.sh docker run --rm -v $L/sweep/pw:/pw -w /pw img sh -c 'node sweep.mjs /pw/out'" % self.lane)

    def test_phone_layout_command_after_a_variable_cd(self):
        self.allowed("L=%s; W=%s; cd $W && mkdir -p .phone-sweep/pw && echo '.phone-sweep/' >> \"$(git rev-parse --git-path info/exclude)\" && cp $L/sweep/sweep.mjs .phone-sweep/sweep.mjs" % (self.lane, self.proj))

    def test_docker_prettier_after_a_variable_cd(self):
        self.allowed("W=%s; cd $W && docker run --rm -v $W:/app -w /app img sh -c 'npx prettier --write src/a.js'" % self.proj)

    # ---- the wall still holds ----

    def test_a_search_into_a_listed_folder_or_its_parent_is_still_refused(self):
        for cmd in ("grep -r secret %s" % self.p("wall/clientA"), "grep -rn secret %s" % self.p("wall"), "find %s -name '*.md'" % self.p("wall"),
                    "rg secret %s" % self.p("wall"), "cp -r %s %s" % (self.p("wall"), self.proj), "du -sh %s" % self.p("wall"),
                    "cd %s && grep -r secret ." % self.p("wall"), "grep -r secret %s %s" % (self.proj, self.p("wall/clientB"))):
            self.refused(cmd)

    def test_a_search_with_no_root_from_a_folder_holding_a_listed_folder_is_still_refused(self):
        for cmd in ("rg foo", "find -name x", "find . -name x", "grep -r foo", "grep -r foo .", "du -sh", "tree", "ls -R", "git grep foo", "rg -g '*.py' src"):
            self.refused(cmd)

    def test_the_search_words_in_a_heredoc_that_a_shell_runs_still_count(self):
        self.refused("bash <<'EOF'\ngrep -r secret %s\nEOF" % self.p("wall"))
        self.refused("sh -c 'find %s'" % self.p("wall"))
        self.refused("docker run --rm img sh -c 'du -sh %s'" % self.p("wall"))
        self.refused("eval 'grep -r x %s'" % self.p("wall"))

    def test_a_listed_path_is_still_refused_when_a_variable_names_it(self):
        self.refused("D=%s; cat $D/docs/secret.txt" % self.p("wall/clientA"))
        self.refused("D=%s; grep -r x $D" % self.p("wall"))
        self.refused("cat %s" % self.p("wall/clientA/docs/secret.txt"))

    def test_grep_and_glob_and_read_still_refuse_a_listed_root(self):
        self.assertIsNotNone(w.evaluate({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": self.p("wall")}, "cwd": self.tmp}, w.load_paths()))
        self.assertIsNotNone(w.evaluate({"tool_name": "Glob", "tool_input": {"pattern": "**/*.md", "path": self.p("wall/clientA")}, "cwd": self.tmp}, w.load_paths()))
        self.assertIsNotNone(w.evaluate({"tool_name": "Read", "tool_input": {"file_path": self.p("wall/clientA/docs/secret.txt")}, "cwd": self.tmp}, w.load_paths()))

    def test_a_change_to_the_wall_the_list_or_settings_is_still_refused(self):
        wall = w.wall_dir()
        settings = self.p("home/.claude/settings.json")
        for cmd in ("echo x > %s/no_ai_wall.py" % wall, "rm %s/no-ai-wall-guard.py" % wall, "echo x >> %s" % self.pfile, "sed -i s/a/b/ %s" % settings,
                    "cp /tmp/x %s/new.py" % wall, "W=%s; cd $W; echo x > no_ai_wall.py" % wall, "W=%s; cd $W && rm no_ai_wall.sh" % wall,
                    "W=%s; cd $W; cp /tmp/x ." % wall, "cd \"$(cat /tmp/where)\"; rm no_ai_wall.py", "cd %s && echo x > settings.json" % self.p("home/.claude"),
                    "L=%s; cd $L && cp /tmp/x %s/no_ai_wall.py" % (self.lane, wall)):
            self.refused(cmd)

    def test_a_variable_reassigned_before_use_is_not_trusted(self):
        wall = w.wall_dir()
        self.refused("W=%s; W=$(cat /tmp/x); cd $W; echo x > no_ai_wall.py" % self.proj)
        self.refused("W=%s; if true; then W=%s; fi; cd $W; echo x > no_ai_wall.py" % (self.proj, wall))

    def test_a_variable_changed_by_any_other_form_is_not_trusted(self):
        # the working folder is the wall's own folder after the second statement, whatever form changed the variable
        home_claude = self.p("home/.claude")
        for change in ("W+=/no-ai-wall", "W[0]=%s" % w.wall_dir(), "((W=1)); W=%s" % w.wall_dir(), "W+=x; W=%s" % w.wall_dir(),
                       "declare -n R=W; R=%s" % w.wall_dir(), "W=${W:+%s}" % w.wall_dir(), "export W=%s" % w.wall_dir(), "read W <<< %s" % w.wall_dir()):
            self.refused("W=%s; %s; cd $W; echo x > no_ai_wall.py" % (home_claude, change))
        self.refused("W=%s; : ${W:=%s}; W+=/no-ai-wall; cd $W; cp /tmp/x ." % (home_claude, home_claude))

    # senior review 1, blocking 1: a statement that can change a variable without naming it must end the trust in every known value
    def test_a_variable_is_not_trusted_past_a_statement_that_can_rebind_it_unseen(self):
        home_claude = self.p("home/.claude")
        for hidden in (". /tmp/set.sh", "source /tmp/set.sh", "source <(echo Vz= | base64 -d)", ". <(cat /tmp/set.sh)", "eval \"$(cat /tmp/set.sh)\"",
                       "eval $X", "read W < /tmp/w", "mapfile W < /tmp/w", "printf -v W %s /tmp/w", "getopts a W", "let W=1", "declare -n R=W"):
            self.refused("W=%s; %s; cd $W; echo x > no_ai_wall.py" % (home_claude, hidden))
            self.refused("W=%s; %s; cd $W; cp /tmp/x ." % (home_claude, hidden))
        self.allowed("W=%s; cd $W && cp /tmp/x ." % self.proj)  # the plain shape the lanes use is still allowed

    # senior review 1, blocking 2: an interpreter runs its heredoc, so a search written inside it still names its roots
    def test_a_search_inside_an_interpreter_heredoc_still_refuses_a_listed_root_or_its_parent(self):
        parent, wall = self.p("wall"), self.p("wall/clientA")
        bodies = {"perl": 'system("grep -r x %s");', "python3": "import os\nos.system('grep -r x %s')", "node": "require('child_process').execSync('find %s -name x')",
                  "ruby": 'system("rg x %s")', "php": "<?php system('du -sh %s');", "awk": 'BEGIN { system("grep -r x %s") }', "sed": "1e find %s"}
        for prog, body in bodies.items():
            for root in (parent, wall):
                self.refused("%s <<'EOF'\n%s\nEOF" % (prog, body % root))
                self.refused("%s - <<'EOF' | head -3\n%s\nEOF" % (prog, body % root))
        # ... while the same words with no root named, or a root that is no listed folder, are still just text
        self.allowed("python3 <<'EOF'\nimport os\nos.system('grep -c x /tmp/one.txt')  # find them, search open issues\nEOF", cwd=self.proj)
        self.allowed("perl <<'EOF'\nsystem(\"grep -r x %s\");\nEOF" % self.proj)

    # senior review 1, missing tests: the wall's own files reached by the roads the issue 130 tests never took
    def test_a_guarded_file_is_refused_by_every_road_to_it(self):
        wall, settings = w.wall_dir(), self.p("home/.claude/settings.json")
        link = self.p("work/wall-link")
        os.symlink(wall, link)
        slink = self.p("work/settings-link")
        os.symlink(settings, slink)
        for cmd in ("echo x > %s/no_ai_wall.py" % link, "echo x | tee %s/no_ai_wall.py" % wall, "echo x | tee -a %s" % slink, "mv /tmp/x %s/no_ai_wall.py" % wall,
                    "mv /tmp/x %s" % settings, "python3 -c \"open('%s/no_ai_wall.py','w').write('x')\"" % wall,
                    "python3 -c \"open('%s','w').write('x')\"" % settings, "echo x > $HOME/.claude/settings.json", "echo x > ~/.claude/no-ai-wall/no_ai_wall.py",
                    "echo x > ${HOME}/.claude/no-ai-wall/no_ai_wall.sh", "cp /tmp/x %s" % slink, "sed -i s/a/b/ %s/no_ai_wall.py" % link,
                    "ln -sf /tmp/x %s/no_ai_wall.py" % wall, "chmod 000 %s/no-ai-wall-guard.py" % wall, "truncate -s 0 %s" % settings):
            self.refused(cmd)

    def test_a_quoted_or_escaped_program_word_is_still_read_as_the_program(self):
        for cmd in ("'find' . -name x", '"find" . -name x', "\\find . -name x", "fi'nd' . -name x", "env 'grep' -r x", "sudo \"rg\" x",
                    "timeout 5 'find' ."):
            self.refused(cmd)
        self.allowed("grep 'find' src/a.txt", cwd=self.proj)
        self.allowed("echo 'find' | cat", cwd=self.proj)

    def test_a_quoted_program_after_wrapper_options_is_still_the_program(self):
        for cmd in ("sudo -u root 'find' . -name x", "env -i FOO=1 'grep' -r x", "timeout -k 2 5 'rg' x", "nice -n 5 \"find\" .", "stdbuf -oL 'find' ."):
            self.refused(cmd)
        self.allowed("sudo -u root ls 'find' ", cwd=self.proj)

    def test_a_file_written_then_run_is_read_widely_whatever_its_name_ends_in(self):
        for cmd in ("echo 'find .' > a.md; chmod +x a.md; ./a.md", "echo 'find .' > a.txt; python3 a.txt", "echo 'grep -r x .' > /tmp/a.json; bash /tmp/a.json",
                    "echo 'find .' > /tmp/a.md; /tmp/a.md", "printf 'rg x\\n' > a.log; ash a.log", "echo 'find .' | bash"):
            self.refused(cmd)
        self.allowed("echo 'find them' > notes.md; cat notes.md", cwd=self.proj)

    # third automated review of the fix: the rebinding forms that name the variable only in a glued or indirect way
    def test_a_variable_is_not_trusted_past_a_glued_quoted_or_indirect_rebind(self):
        home_claude = self.p("home/.claude")
        for hidden in ("printf -vW %s /tmp/w", "printf -v\"W\" %s /tmp/w", "builtin printf -vW %s /tmp/w", "'printf' -vW %s /tmp/w", "N=W; \\read $N < /tmp/w",
                       "N=W; 'read' $N < /tmp/w", "N=W; \"eval\" \"$N=/tmp\"", "N=W; builtin read $N < /tmp/w", "N=W; \\mapfile $N < /tmp/w",
                       "N=W; 'declare' -n R=$N"):
            self.refused("W=%s; %s; cd $W; echo x > no_ai_wall.py" % (home_claude, hidden))

    # ... a search written in the code an interpreter runs from a string, a pipe, a redirect or a file it was handed
    def test_a_search_run_by_an_interpreter_still_refuses_a_listed_parent(self):
        parent = self.p("wall")
        for cmd in ("python3 -c 'import os; os.system(\"grep -r x %s\")'", "perl -e 'system(\"find %s -name x\")'", "node -e \"require('child_process').execSync('du -sh %s')\"",
                    "ruby -e 'system(\"rg x %s\")'", "awk 'BEGIN { system(\"grep -r x %s\") }'", "php -r 'system(\"find %s\");'",
                    "printf 'import os; os.system(\"grep -r x %s\")' | python3", "echo 'system(\"find %s\");' | perl", "printf 'import os; os.system(\"find %s\")' | python3 -",
                    "printf 'import os; os.system(\"grep -r x %s\")' > a.md; python3 < a.md", "printf 'system(\"find %s\");' > a.txt; perl < a.txt"):
            self.refused(cmd % parent)
        self.allowed("python3 -c \"import os; os.system('grep -c x %s/one.txt')\"" % self.proj, cwd=self.proj)
        self.allowed("echo 'print(1)' | python3", cwd=self.proj)
        self.allowed("python3 -c 'print(\"find them\")'", cwd=self.proj)

    # ... a file written by something other than a redirect, then run by a program no list knows
    def test_a_file_written_by_tee_then_run_by_any_program_is_read_widely(self):
        parent = self.p("wall")
        for cmd in ("printf 'import os; os.system(\"grep -r x %s\")' | tee a.md >/dev/null; python3 a.md", "printf 'os.execute(\"find %s\")' | tee a.md; lua a.md",
                    "printf 'all:\\n\\t grep -r x %s \\n' | tee Makefile; make", "printf 'find %s\\n' | tee a.md; busybox sh a.md",
                    "cat > a.md <<'EOF'\nos.execute(\"find %s\")\nEOF\nlua a.md", "printf 'find %s\\n' | tee a.md; pwsh a.md",
                    "printf 'find %s\\n' | tee a.md; env sh a.md"):
            self.refused(cmd % parent)
        self.allowed("echo 'find them' | tee notes.md; cat notes.md", cwd=self.proj)
        self.allowed("cat > notes.md <<'EOF'\nfind them\nEOF\ngit add notes.md && git status", cwd=self.proj)

    # fourth automated review: the heredoc exemption was one `<<` anywhere in the text, and an interpreter's option value counted as its program
    def test_an_interpreter_fed_by_a_pipe_or_here_string_is_not_exempted_by_a_heredoc_elsewhere(self):
        parent = self.p("wall")
        code = "printf 'import os; os.system(\"grep -r x %s\")' | " % parent
        for cmd in (code + "python3 -W ignore", code + "python3 -X dev", code + "python3 -W ignore -", "python3 <<< 'import os; os.system(\"find %s\")'" % parent,
                    "cat <<'EOF' >/dev/null\nhi\nEOF\n" + code + "python3", "echo 'system(\"find %s\");' | perl -I /tmp" % parent,
                    "echo 'system(\"find %s\");' | perl -M strict" % parent):
            self.refused(cmd)
        # a heredoc the guard did not recognise (no terminator line) excuses nothing; an unknown option's value is no program either
        for cmd in (code + "python3 - <<'EOF'\nprint(1)", code + "python3 -W ignore -X dev", code + "python3 --check-hash-based-pycs never",
                    "echo 'system(\"find %s\");' | node --max-old-space-size 64" % parent):
            self.refused(cmd)
        # a heredoc's credit belongs to the command holding the marker, and only when that command has no program of its own: a python that runs
        # a.py and gets a heredoc as its stdin data gives no credit to a later pipe-fed python
        for cmd in ("python3 a.py <<'EOF'\nx\nEOF\n" + code + "python3", "python3 -c 'print(1)' <<'EOF'\nx\nEOF\n" + code + "python3"):
            self.refused(cmd)
        self.allowed("python3 -I %s/src/tool.py" % self.proj, cwd=self.proj)
        self.allowed("python3 - <<'EOF'\nprint('find them')\nEOF", cwd=self.proj)
        self.allowed("echo '{}' | python3 -m json.tool", cwd=self.proj)

    # ... and git, gh, tar, sort, rsync can run a program through an option or an alias, so they are data tools only without one
    def test_a_tool_that_can_run_code_is_no_data_tool_when_given_an_option_that_runs_it(self):
        parent = self.p("wall")
        write = "printf 'find %s' > a.md; " % parent
        for cmd in ("git -c 'core.pager=sh a.md' log", "git -c 'alias.x=!sh a.md' x", "git --config-env=alias.x=A x", "gh alias set x '!sh a.md'",
                    "tar -xf a.tar --to-command='sh a.md'", "tar -cf a.tar --checkpoint=1 --checkpoint-action=exec='sh a.md' a.md", "sort --compress-program=sh a.md",
                    "rsync -e 'sh a.md' a.md host:/x", "GIT_PAGER='sh a.md' git log", "env GIT_SSH_COMMAND='sh a.md' git fetch", "PAGER='sh a.md' gh pr view 1",
                    "git bisect run sh a.md", "git submodule foreach 'sh a.md'", "tar -cf a.tar --new-volume-script=a.md a.md", "tar -cf a.tar --info-script=a.md a.md",
                    "git difftool --extcmd='sh a.md'", "git -c 'filter.x.clean=sh a.md' add a.md",
                    "git config core.pager 'sh a.md' && git log", "git config core.sshCommand 'sh a.md'", "git -C . config core.editor 'sh a.md'"):
            self.refused(write + cmd)
        self.allowed("printf 'find them' > notes.md; git add notes.md && git status", cwd=self.proj)
        self.allowed("printf 'find them' > notes.md; tar -czf out.tgz notes.md; sort notes.md > out.md", cwd=self.proj)

    def test_data_that_a_shell_is_fed_is_not_dropped_as_a_message(self):
        for cmd in ("echo find . | sh", "echo 'find .' | bash", "printf 'grep -r x .\\n' | sh -s", "echo find . | xargs -I{} sh -c '{}'",
                    "echo 'find .' > a.sh; sh a.sh", "echo 'find .' > a.sh; ./a.sh", "printf 'rg x\\n' > /tmp/a; source /tmp/a", "echo 'find .' > /tmp/a; . /tmp/a",
                    "echo 'find .' | tee /tmp/a.sh; bash /tmp/a.sh", "cat <<'EOF' > a.sh\nfind .\nEOF\nbash a.sh"):
            self.refused(cmd)
        self.allowed("echo 'find them' | head -1", cwd=self.proj)

    # ---- senior review 2 (these refusals are stronger than main's: main allows some of them) ----

    # blocking 1: a trap or an alias runs before the variable expands, so a value written ahead of it is not what the shell uses
    def test_a_variable_is_not_trusted_past_a_trap_alias_or_shell_option_statement(self):
        wall = w.wall_dir()
        for hidden in ("trap 'W=%s' DEBUG" % wall, "alias go='W=%s'; go" % wall, "shopt -s extdebug", "enable -f /tmp/x.so xx", "trap 'W=%s' DEBUG; true" % wall):
            self.refused("%s; W=/tmp; cd $W; echo x > no_ai_wall.py" % hidden)
            self.refused("W=/tmp; %s; cd $W; echo x > no_ai_wall.py" % hidden)
        # fail-closed: a statement that is no plain assignment, cd or data tool ends the collecting, whatever it is
        self.refused("W=/tmp; somecmd --flag; cd $W; echo x > no_ai_wall.py", cwd=wall)
        self.refused("f() { W=%s; }; W=/tmp; f; cd $W; echo x > no_ai_wall.py" % wall, cwd=wall)
        self.allowed("W=%s; cd $W && cp /tmp/x ." % self.proj)

    # blocking 2: a heredoc's child shell never sees an unexported variable, so its `cd $W` is not the cd the check read
    def test_a_heredoc_child_shell_does_not_see_the_variable_the_parent_set(self):
        self.refused("W=/tmp\nbash <<'EOF'\ncd $W\necho x > no_ai_wall.py\nEOF", cwd=w.wall_dir())
        self.refused("W=/tmp\nbash <<'EOF'\ncd $W\necho x > no_ai_wall.py\nEOF", cwd=self.p("home/.claude"))
        self.refused("W=/tmp\nsh <<EOF\ncd $W\ncp /tmp/x no_ai_wall.py\nEOF", cwd=self.p("home/.claude"))

    # blocking 3: run from a folder above a listed one, a string of code that runs a recursive search walks every listed folder below it
    def test_code_text_that_runs_a_recursive_search_walks_the_working_folder_too(self):
        for cmd in ("python3 -c \"import os; os.system('grep -r secret .')\"", "python3 -c 'import os; os.system(\"grep -r secret\")'",
                    "perl -e 'system(\"grep -r secret .\")'", "python3 <<'EOF'\nimport os\nos.system('grep -r secret .')\nEOF",
                    "perl <<'EOF'\nsystem(\"find . -name x\");\nEOF", "node -e \"require('child_process').execSync('rg secret')\"",
                    "python3 -c \"import subprocess; subprocess.run(['grep', '-r', 'secret'])\"",
                    "python3 <<'EOF'\nimport subprocess\nsubprocess.check_output(['rg', 'secret'])\nEOF"):
            self.refused(cmd)  # the session starts in self.tmp, which holds the listed folders
            self.allowed(cmd, cwd=self.proj)  # the same code in a folder with no listed folder below it is not a search into the wall
        self.allowed("python3 -c \"print('grep -c x a.txt; find them')\"", cwd=self.proj)  # a string that goes to print is prose, not a search

    # senior review 2, missing test (d): code that names the wall's folder as text beside a copy that goes elsewhere
    def test_code_that_only_names_the_wall_folder_as_text_beside_a_copy_elsewhere(self):
        wall = w.wall_dir()
        self.allowed("python3 -c \"print('the guard lives in %s'); import shutil; shutil.copy('/tmp/a', '%s/out.txt')\"" % (wall, self.proj))
        self.refused("python3 -c \"import shutil; shutil.copy('/tmp/a', '%s/no_ai_wall.py')\"" % wall)
        self.refused("python3 -c \"import shutil; shutil.copy('%s/no_ai_wall.py', '/tmp/b'); print(1)\"" % wall)

    # senior review 2, missing test: a guarded file reached through the home folder written out as /home/<user> (and not as ~ or $HOME)
    def test_a_guarded_file_is_refused_through_a_written_out_home_path(self):
        written_out = self.p("home/.claude")  # the fake home, spelled in full: the road ~ and $HOME take, without either of them
        for cmd in ("echo x > %s/settings.json" % written_out, "echo x > %s/no-ai-wall/no_ai_wall.py" % written_out, "rm %s/no-ai-wall/no_ai_wall.sh" % written_out,
                    "cd %s/no-ai-wall && echo x > no-ai-wall-guard.py" % written_out, "python3 -c \"open('%s/settings.json','w').write('x')\"" % written_out):
            self.refused(cmd)
        self.allowed("echo x > %s/state/lanes/2026-01-01-demo/note.md" % written_out)

    # the hub's refusal: a heredoc that only NAMES the wall's folder and the settings files, written into a lane folder, is an ordinary write
    def test_a_heredoc_that_names_the_wall_and_settings_but_writes_a_lane_file_is_allowed(self):
        wall, settings = w.wall_dir(), self.p("home/.claude/settings.json")
        body = "the no-AI list, %s and %s cannot be changed from a session" % (wall, settings)
        self.allowed("cat > %s/review-2.md <<'EOF'\n%s\nEOF" % (self.lane, body))
        self.allowed("cat <<'EOF' > %s/review-2.md\n# review\n%s\nEOF" % (self.lane, body))
        self.allowed("python3 - <<'EOF'\nopen('%s/review-2.md','w').write('%s')\nEOF" % (self.lane, body))
        self.refused("cat > %s/no_ai_wall.py <<'EOF'\n%s\nEOF" % (wall, body))  # the same text, written into the wall itself, is still refused


class Issue146Tests(Fixture):
    """Issue 146: a lane's read-back check was refused as "a search that would read the no-AI list". The cause: the last check read the whole
    text as ONE word list, so `-R` (gh's --repo) in `gh pr view -R owner/name` counted as `grep -R` (recursive) from a `grep` earlier in the
    text, and the quoted pattern given to that grep named a folder above the list file. Each command is now read on its own. The list file sits
    in its own folder (tmp/cfg) so only the list-file check can refuse here, as on a real machine, where the list is in a folder of its own."""

    run_bash, allowed, refused = Issue130Tests.run_bash, Issue130Tests.allowed, Issue130Tests.refused

    def setUp(self):
        super().setUp()
        os.makedirs(self.p("cfg"))
        self.pfile = self.p("cfg/paths.txt")
        os.environ["NO_AI_PATHS_FILE"] = self.pfile
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        self.lane = self.p("home/.claude/state/lanes/2026-01-01-demo")
        os.makedirs(self.lane)
        os.makedirs(w.wall_dir(), exist_ok=True)

    def test_the_exact_command_that_was_refused_is_allowed(self):
        # the refused call, with the names made up: the pattern's start (a folder above the list file) is the tmp folder, as /home was
        cmd = ("cd %s && head -1 report.md | cut -c1-80; sed -n '/^## Proof/,/^$/p' report.md | grep -oE \"%s/[^ ,;)]+\" | sort -u | "
               "while read p; do [ -e \"$p\" ] && echo \"exists $p\" || echo \"MISSING $p\"; done; "
               "gh api \"repos/owner/name/contents/hub/a.txt?ref=main\" --jq '.content|@base64d' | grep -c r-92081; "
               "gh pr view 144 -R owner/name --json state,mergeCommit --jq '.state+\" \"+.mergeCommit.oid[:7]'") % (self.lane, self.tmp)
        self.allowed(cmd, self.lane)
        self.allowed(cmd, self.tmp)

    def test_a_grep_without_r_and_a_gh_repo_flag_in_the_same_text_is_allowed(self):
        for cmd in ("grep -oE \"%s/[^ ,;)]+\" report.md; gh pr view 144 -R owner/name" % self.tmp,
                    "grep -c x report.md && gh pr list --repo owner/name -R owner/name",
                    "grep -n x report.md | head -3\ngh pr checks 144 -R owner/name",
                    "gh pr view 144 -R owner/name && grep -c x report.md",
                    "echo %s/ ; grep x report.md ; gh issue view 5 -R owner/name" % self.tmp):
            self.allowed(cmd, self.lane)

    # ---- the wall still holds: a recursive read of the list's folder, or one above it, is refused however the command is built ----

    def test_a_recursive_read_of_the_list_folder_is_still_refused(self):
        cfg, t = self.p("cfg"), self.tmp
        for cmd in ("grep -r secret %s" % cfg, "grep -rn x %s" % t, "grep -R x %s" % cfg, "grep -ri x %s" % cfg, "grep --recursive x %s" % cfg,
                    "rg secret %s" % cfg, "tar cf - %s" % cfg, "cp -r %s /tmp/x" % cfg, "find %s -type f -exec cat {} +" % cfg, "git grep x %s" % cfg):
            self.refused(cmd, self.lane)

    def test_a_recursive_grep_in_any_command_of_a_longer_text_is_still_refused(self):
        cfg = self.p("cfg")
        for cmd in ("echo hi; grep -r x %s" % cfg, "grep -c x report.md; grep -r x %s" % cfg, "grep -c x report.md && grep -R x %s" % cfg,
                    "gh pr view 144 -R owner/name; grep -r x %s" % cfg, "grep -oE y report.md | sort\ngrep -rn x %s" % cfg,
                    "gh pr view 144 -R owner/name | grep -r x %s" % cfg, "echo %s | xargs grep -r x" % cfg, "ls %s | xargs grep -rl x" % cfg,
                    "grep \\\n  -r x %s" % cfg, "(grep -r x %s)" % cfg, "x=$(grep -r x %s)" % cfg):
            self.refused(cmd, self.lane)

    def test_a_recursive_grep_inside_a_quoted_script_is_still_refused(self):
        cfg = self.p("cfg")
        for cmd in ("bash -c 'grep -r x %s'" % cfg, "sh -c \"cd /tmp; grep -R x %s\"" % cfg, "gh pr view 144 -R owner/name; bash -c 'grep -r x %s'" % cfg,
                    "docker run --rm img sh -c 'grep -r x %s'" % cfg):
            self.refused(cmd, self.lane)

    def test_a_recursive_grep_in_a_text_that_cannot_be_read_is_still_refused(self):
        cfg = self.p("cfg")
        self.refused("echo 'unclosed; grep -r x %s" % cfg, self.lane)  # the wide reading, as before

    def test_the_refusal_is_the_list_file_one(self):
        r = self.run_bash("grep -r secret %s" % self.p("cfg"), self.lane)
        self.assertIsNotNone(r)
        self.assertIn("no-AI list is private config", r[2])


if __name__ == "__main__":
    unittest.main()
