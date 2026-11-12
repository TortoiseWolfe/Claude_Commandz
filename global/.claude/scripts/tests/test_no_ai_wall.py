"""The no-AI wall's shared reader and its tool-call decisions. Every path here is a fake folder under a temp dir."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_python_may_run_a_listed_file_only_as_itself(self):
        """The read-only rule is the only gate for `python <wall file>`. Every form where Python would run something other than the
        file (and the file is just an argument handed to it) must be refused, joined to its value or not."""
        wall = os.path.join(self.home, ".claude/no-ai-wall")
        mod, clean = os.path.join(wall, "no_ai_wall.py"), os.path.join(wall, "wall_cleanup.py")
        rv = os.path.join(self.home, ".claude/scripts/redact_view.py")
        for cmd in ("python3 %s status" % mod, "python3 -I -B %s status" % mod, "python3 -I %s check-path /tmp/x" % mod, "/usr/bin/python3.12 -s -S -u %s status" % mod,
                    "python3 %s" % clean, "python3 -I %s --apply" % clean, "python3 -I %s --keys-only %s" % (rv, self.pfile)):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)
        for target in (mod, clean, rv + " " + self.pfile):
            for before in ("-m somemodule", "-msomemodule", "-I -msomemodule", "-Imsomemodule", "-c pass", "-cpass", "-Bcpass", "-", "-i", "-X dev", "-Xdev", "-W error",
                           "-Werror", "--version", "-IB", "-E", "/tmp/other.py"):
                cmd = "python3 %s %s" % (before, target)
                r = self.call("Bash", command=cmd)
                self.assertIsNotNone(r, cmd)
                self.assertEqual(r[1], "protected file", cmd)
        for cmd in ("python3 %s /tmp/somewhere" % clean, "python3 %s --apply /tmp/somewhere" % clean, "python3 %s --force" % clean, "python3 -I %s -- --apply" % clean,
                    "python3-config %s" % mod, "pythonw %s status" % mod):
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        # A variable set in front of the program is not the program, even when its value ends in a trusted name.
        for target in (mod, clean, rv + " " + self.pfile):
            for first in ("A=/ls", "A=/usr/bin/wc", "A=/stat", "A=/x/python3", "A=1 B=/ls", "A=ls", "A=1"):
                for run in ("python3 -m somemodule", "python3 -cpass", "python3 -I", "python3"):
                    cmd = "%s %s %s" % (first, run, target)
                    r = self.call("Bash", command=cmd)
                    self.assertIsNotNone(r, cmd)
                    self.assertEqual(r[1], "protected file", cmd)
        for cmd in ("A=/ls cat %s" % mod, "ls %s; A=/wc python3 -m somemodule %s" % (mod, mod), "A=/ls ls %s" % clean):
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        # Parentheses and braces start a command where the rule sees no new part: a function body, a group, a subshell. The program inside must not go unread.
        for target in (mod, clean, rv + " " + self.pfile):
            for shape in ("ls() ( python3 -m somemodule %s ); ls", "ls () ( python3 -m somemodule %s ); ls", "ls() ( python3 -m somemodule %s )",
                          "wc() ( python3 -cpass %s ) ; wc", "ls() { python3 -m somemodule %s; }; ls", "ls() {\npython3 -m somemodule %s\n}\nls",
                          "function ls { python3 -m somemodule %s; }; ls", "(python3 -m somemodule %s)", "{ python3 -m somemodule %s; }",
                          "ls; (python3 -m somemodule %s)", "stat() ( python3 -I -m somemodule %s ); stat /tmp"):
                cmd = shape % target
                r = self.call("Bash", command=cmd)
                self.assertIsNotNone(r, cmd)
                self.assertEqual(r[1], "protected file", cmd)
        # A first word the shell would expand is not a plain program name, so it is not read as one.
        for cmd in ("$P -m somemodule %s" % mod, '"$P" -m somemodule %s' % mod, "ls %s; $P %s" % (mod, mod), "l[s] %s; python[3] -m somemodule %s" % (mod, mod),
                    "ls* %s" % clean, "ls {a,b} %s" % mod):
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        # The honest forms still pass: a lone look, two looks, a look and then the module's own check.
        for cmd in ("ls -la %s" % mod, "sha256sum %s" % clean, "ls %s; wc -l %s" % (mod, clean), "ls %s && python3 -I %s status" % (wall, mod), "/usr/bin/stat %s" % mod):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)
        # The path the rule compares must be the path the machine opens: quotes, white space, links, relative paths and other variables are refused.
        os.makedirs(wall, exist_ok=True)
        os.makedirs(os.path.dirname(rv), exist_ok=True)
        for f in (mod, clean, rv):
            if not os.path.exists(f):
                open(f, "w").close()
        x = tempfile.mkdtemp(dir=self.tmp)
        self.addCleanup(shutil.rmtree, x, ignore_errors=True)
        os.makedirs(os.path.join(x, "far", "sub"))
        os.symlink(os.path.join(x, "far", "sub"), os.path.join(x, "l"))
        os.symlink(mod, os.path.join(x, "no_ai_wall.py"))
        os.symlink(clean, os.path.join(x, "wall_cleanup.py"))
        os.symlink(mod, os.path.join(x, "w[x]"))
        refused = ('python3 " %s" status' % mod, 'python3 "%s " status' % mod, 'python3 " %s" --keys-only %s' % (rv, self.pfile),
                   "python3 '~/.claude/no-ai-wall/no_ai_wall.py' status", 'python3 "~/.claude/scripts/redact_view.py" --keys-only %s' % self.pfile,
                   "python3 '$HOME/.claude/no-ai-wall/no_ai_wall.py' status", "python3 \\~/.claude/no-ai-wall/no_ai_wall.py status",
                   "python3 \r%s status" % mod, "python3 %s check-path %s" % (mod.lstrip("/"), mod),
                   "python3 -I %s/l/../wall_cleanup.py --apply" % x, "python3 %s/l/../no_ai_wall.py status" % x,
                   "python3 %s/no_ai_wall.py status" % x, "python3 %s/w[x] status" % x)
        for cmd in refused:
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)
        with mock.patch.dict(os.environ, {"WALLX": wall}):
            cmd = "python3 $WALLX/no_ai_wall.py status"
            r = self.call("Bash", command=cmd)
            self.assertIsNotNone(r, cmd)
            self.assertEqual(r[1], "protected file", cmd)
        for cmd in ("python3 ~/.claude/no-ai-wall/no_ai_wall.py status", "python3 $HOME/.claude/scripts/redact_view.py --keys-only %s" % self.pfile,
                    "python3 -I $HOME/.claude/no-ai-wall/wall_cleanup.py --apply", 'python3 %s --keys-only "%s"' % (rv, self.pfile),
                    "python3\t-I\t%s\tstatus" % mod):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)
        # No white space of any kind may end the file's word: strip() in expand() would drop it, but the shell passes it on, and Python opens a sibling file.
        # The rule function is called directly with text only (the full check stops earlier on these texts).
        forms = [("module", mod, ["status"]), ("module (~/ form)", "~/.claude/no-ai-wall/no_ai_wall.py", ["status"]), ("cleanup command", clean, ["--apply"]),
                 ("redact_view.py", rv, ["--keys-only", self.pfile])]
        for label, path, rest in forms:
            for ch in ("\v", "\f", "\xa0", "\x1c", "\x1f", "\u2003", "\u2028"):
                words = ["python3", path + ch] + rest
                self.assertFalse(w._runs_listed_python(words, " ".join(words)), "%r on the end of the word for the %s" % (ch, label))
        words = ["python3", mod, "status"]
        self.assertTrue(w._runs_listed_python(words, " ".join(words)))

    def test_python_rule_refuses_dotdot_after_a_link_and_any_other_variable(self):
        """A ".." after a link means the folder above the link's target, not above the link, and a variable other than the leading $HOME may hold anything:
        the rule compares text, so it must refuse both. Each case would be allowed if its clause were removed."""
        claude = os.path.join(self.home, ".claude")
        os.makedirs(claude, exist_ok=True)
        far = tempfile.mkdtemp(dir=self.tmp)
        self.addCleanup(shutil.rmtree, far, ignore_errors=True)
        os.symlink(far, os.path.join(claude, "lnk"))
        cases = [["python3", "%s/lnk/../no-ai-wall/no_ai_wall.py" % claude, "status"],
                 ["python3", "~/.claude/lnk/../no-ai-wall/no_ai_wall.py", "status"],
                 ["python3", "-I", "$HOME/.claude/lnk/../no-ai-wall/wall_cleanup.py", "--apply"],
                 ["python3", "%s/lnk/../scripts/redact_view.py" % claude, "--keys-only", self.pfile]]
        for words in cases:
            cmd = " ".join(words)
            self.assertFalse(w._runs_listed_python(words, cmd), cmd)
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        with mock.patch.dict(os.environ, {"EMPTYV": "", "FOLDERV": "no-ai-wall"}):
            for word in ("$HOME/.claude/no-ai-wall/no_ai_wall.py$EMPTYV", "$HOME/$EMPTYV.claude/no-ai-wall/no_ai_wall.py",
                         "$HOME/.claude/$FOLDERV/no_ai_wall.py", "~/.claude/$FOLDERV/no_ai_wall.py"):
                words = ["python3", word, "status"]
                self.assertFalse(w._runs_listed_python(words, " ".join(words)), word)

    def test_a_home_folder_with_a_space_refuses_the_home_form(self):
        """$HOME/ in a command is split by the shell when the home folder's name holds white space, so the rule refuses it."""
        os.environ["HOME"] = os.path.join(self.tmp, "my home")
        words = ["python3", "$HOME/.claude/no-ai-wall/no_ai_wall.py", "status"]
        self.assertFalse(w._runs_listed_python(words, " ".join(words)))

    def test_a_listing_program_is_a_bare_name_or_a_system_path(self):
        """A listing program is a bare name or sits directly under /usr/bin or /bin; a path to any other folder is refused."""
        wall = os.path.join(self.home, ".claude/no-ai-wall")
        W, C = os.path.join(wall, "no_ai_wall.py"), os.path.join(wall, "wall_cleanup.py")
        for cmd in ("/tmp/x/python3 %s status" % W, "./python3 -I %s" % C, "/tmp/x/ls %s" % W, "~/bin/stat %s" % C, "/usr/bin/../../tmp/x/ls %s" % W,
                    "/usr/local/bin/ls %s" % W):
            self.assertIsNotNone(self.call("Bash", command=cmd), cmd)
        for cmd in ("ls %s" % W, "/usr/bin/stat %s" % W, "/bin/ls %s" % W, "/usr/bin/python3 %s status" % W, "python3 -I %s" % C):
            self.assertIsNone(self.call("Bash", command=cmd), cmd)

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


if __name__ == "__main__":
    unittest.main()
