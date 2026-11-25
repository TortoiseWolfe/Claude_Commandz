"""Issues 157, 160, 161 and 164: four gaps in how the wall reads a search whose words or flags arrive from somewhere else than the typed command.
157: an xargs fed by anything but the plain words of an echo or printf can carry a search root. 160: a substitution before a search's root can
hand the search a flag. 161: GNU parallel passes its input on like xargs. 164: the widening for a substitution after an xargs reaches only the
commands of the same pipeline or statement. Fake folders only."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_no_ai_wall as base  # noqa: E402


class XargsFamilyTests(base.Fixture):
    run_bash, allowed, refused = base.Issue130Tests.run_bash, base.Issue130Tests.allowed, base.Issue130Tests.refused

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        self.above = self.p("wall")  # a folder above the listed ones: only the recursion decides
        self.safe = self.p("work")  # a folder with no listed folder under it

    # ---- 157: the input of an xargs can carry a search root ----

    def test_an_xargs_input_that_is_not_plain_words_can_carry_a_root(self):
        """`cat roots.txt | xargs grep -r x /safe`: the recursive flag and the root typed here are harmless, and the file can list a folder on the
        no-AI list. Run from a folder that holds a listed one, the search is read as one that may walk it."""
        for cmd in ("cat roots.txt | xargs grep -r x %s" % self.safe, "ls | xargs grep -rn x %s" % self.safe,
                    "cat roots.txt | xargs grep -rl x %s" % self.safe, "git ls-files | xargs grep -n x %s" % self.safe,
                    "xargs grep -r x %s < roots.txt" % self.safe, "xargs grep -r x %s <<< \"$ROOTS\"" % self.safe,
                    "echo $ROOTS | xargs grep -r x %s" % self.safe, "cat roots.txt | xargs ls -R %s" % self.safe,
                    "cat roots.txt | xargs du %s" % self.safe, "cat roots.txt | xargs find %s -name x" % self.safe,
                    "cat roots.txt | xargs rg x %s" % self.safe):
            self.refused(cmd, self.tmp)

    def test_an_xargs_input_of_plain_words_is_still_allowed(self):
        for cmd in ("echo hi | xargs grep -r x %s" % self.safe, "echo hi there | xargs grep -rn x %s" % self.safe,
                    "printf '%%s\\n' a b | xargs grep -r x %s" % self.safe, "echo hi | xargs ls -R %s" % self.safe):
            self.allowed(cmd, self.tmp)
        # no listed folder under the session folder: the input's roots are not a risk the wall can see there
        self.allowed("cat roots.txt | xargs grep -r x %s" % self.safe, self.safe)
        self.allowed("ls | xargs grep x", self.safe)
        self.allowed("git ls-files | xargs grep -n x", self.safe)

    # ---- 160: a substitution before the root can hand the search a flag ----

    def test_a_substitution_that_can_hand_a_search_its_flag_is_refused(self):
        a = self.above
        self.refused("grep -rn x %s" % a, self.tmp)  # the typed search, the reference
        for cmd in ("grep $(echo -r) x %s" % a, "grep `echo -r` x %s" % a, "grep $(echo -R) x %s" % a, "grep \"$(echo -r)\" x %s" % a,
                    "grep x $(echo -r) %s" % a, "egrep $(echo -r) x %s" % a, "ls $(echo -R) %s" % a, "rg $(echo -r) x %s" % a,
                    "grep $(echo -r) x %s; ls" % a, "echo hi && grep $(echo -r) x %s" % a):
            self.refused(cmd, self.tmp)

    def test_a_search_with_no_substitution_before_its_root_stays_allowed(self):
        a = self.above
        for cmd in ("grep x %s" % a, "grep -n x %s" % a, "ls %s" % a, "echo $(pwd); grep x %s" % a, "grep x %s; echo $(pwd)" % a,
                    "grep x %s | wc -l" % a, "grep x $(pwd) | wc -l", "ls $(pwd)"):
            self.allowed(cmd, self.tmp)

    # ---- 161: GNU parallel passes its input on like xargs ----

    def test_a_search_given_its_flag_by_parallel_is_refused_like_xargs(self):
        a = self.above
        self.refused("echo -r | xargs grep x %s" % a, self.tmp)  # the reference
        for cmd in ("echo -r | parallel grep x %s" % a, "echo -R | parallel grep x %s" % a, "cat flags.txt | parallel grep x %s" % a,
                    "ls | parallel grep x %s" % a, "parallel grep x %s < flags.txt" % a, "parallel grep x %s <<< '-r'" % a,
                    "echo -r | \\parallel grep x %s" % a, "echo -r | parallel egrep x %s" % a, "echo -r | parallel ls %s" % a,
                    "parallel grep x %s ::: -r" % a, "parallel grep x %s :::: flags.txt" % a, "echo r | parallel grep -{} x %s" % a,
                    "echo r | parallel grep -{1} x %s" % a, "echo -r | parallel grep {} x %s" % a):
            self.refused(cmd, self.tmp)
        # the options that change how the input is read take the wide reading, spelled out or shortened
        for cmd in ("echo hi | parallel -a flags.txt grep x %s" % a, "echo hi | parallel --arg-file flags.txt grep x %s" % a,
                    "echo hi | parallel --arg-file=flags.txt grep x %s" % a, "echo a-r | parallel -d a grep x %s" % a,
                    "echo a-r | parallel --delimiter a grep x %s" % a, "echo r | parallel -I@ grep -@ x %s" % a,
                    "echo r | parallel --replace=@ grep -@ x %s" % a, "echo r | parallel --repl @ grep -@ x %s" % a,
                    "echo hi | parallel --arg grep x %s" % a, "echo hi | parallel --a=flags.txt grep x %s" % a,
                    "echo hi | parallel -j4 -a flags.txt grep x %s" % a):
            self.refused(cmd, self.tmp)
        self.refused("echo -r | parallel grep x", self.tmp)  # no root named: it walks the session folder

    def test_a_search_with_plain_input_over_parallel_stays_allowed(self):
        a = self.above
        for cmd in ("echo hi | parallel grep x %s" % a, "echo hi there | parallel grep -n x %s" % a,
                    "printf '%%s\\n' a b | parallel grep x %s" % a, "echo hi | parallel ls %s" % a):
            self.allowed(cmd, self.tmp)

    # ---- 164: a substitution widens only the pipeline or statement of the xargs ----

    def test_a_command_in_a_later_statement_is_read_on_its_own(self):
        a, s = self.above, self.safe  # the find names a safe root: a bare `find .` would itself walk the session folder, which holds a listed one
        for cmd in ("find %s -name x | xargs rm; ls $(pwd)" % s, "find %s -name x | xargs rm && ls $(pwd)" % s, "find %s -name x | xargs rm\nls $(pwd)" % s,
                    "find %s -name x | xargs rm; grep x %s $(pwd)" % (s, s), "echo hi | xargs grep x %s; grep y %s $(pwd)" % (a, a),
                    "find %s -name x | xargs rm | wc -l; ls $(pwd)" % s, "find %s -name x | xargs rm; ls `pwd`" % s):
            self.allowed(cmd, self.tmp)

    def test_a_command_the_substitution_can_split_from_xargs_stays_refused(self):
        a = self.above
        for cmd in ("echo -r | xargs $(true) grep x %s" % a, "echo -r | xargs `true` grep x %s" % a,
                    "echo -r | xargs -E <(true) grep x %s" % a, "xargs -a <(echo -r) grep x %s" % a,
                    "echo -r | xargs -n1 $(true) find %s -name x" % a, "echo -r | xargs $(true; true) grep x %s" % a,
                    "echo -r | xargs $(echo a | tr a b) grep x %s" % a, "echo -r | xargs -I{} $(true) grep {} x %s" % a,
                    "echo -r | xargs $(true) $(true) grep x %s" % a, "echo -r | parallel $(true) grep x %s" % a):
            self.refused(cmd, self.tmp)
        # a substitution that is the last word hands over a flag as well; one that holds only `pwd` never does
        for cmd in ("grep x %s $(echo -r)" % a, "ls %s $(echo -R)" % a, "grep x %s `echo -r`" % a, "echo hi | xargs true; grep x %s $(echo -r)" % a):
            self.refused(cmd, self.tmp)
        # a feeder run by a feeder takes input of its own; so does a parallel that has options set in its environment
        for cmd in ("echo hi | xargs xargs grep x %s" % a, "echo hi | PARALLEL=-a parallel grep x %s" % a, "echo hi | env PARALLEL=-a parallel grep x %s" % a):
            self.refused(cmd, self.tmp)
        with mock.patch.dict(os.environ, {"PARALLEL": "-a flags.txt"}):
            self.refused("echo hi | parallel grep x %s" % a, self.tmp)
        # a PARALLEL variable set earlier in the text reaches parallel too
        for cmd in ("export PARALLEL=-a; echo hi | parallel grep x %s" % a, "declare -x PARALLEL=-a; echo hi | parallel grep x %s" % a):
            self.refused(cmd, self.tmp)
        # `pwd` is exempt only while nothing can change what it prints: a function or alias of that name, a changed IFS or PATH, a second pwd, or
        # a folder built at run time all turn the exemption off
        for cmd in ("pwd() { echo -R; }; ls %s $(pwd)" % a, "function pwd { echo -R; }; ls %s $(pwd)" % a, "alias pwd='echo -R'; ls %s $(pwd)" % a,
                    "IFS=-; ls %s $(pwd)" % a, "ls %s $(pwd; echo -R)" % a, "cd $X; ls %s $(pwd)" % a, "cd 'a b'; ls %s $(pwd)" % a,
                    # any change of folder, however it is spelled, and any word that can rebind a name
                    "cd -; ls %s $(pwd)" % a, "cd /; ls %s $(pwd)" % a, "sh -c 'cd /; ls %s $(pwd)'" % a, "pushd /; ls %s $(pwd)" % a,
                    "eval 'pwd() { echo -R; }'; ls %s $(pwd)" % a, "command alias pwd=x; ls %s $(pwd)" % a, "builtin cd /; ls %s $(pwd)" % a,
                    "bash -c 'pwd() { echo -R; }; ls %s $(pwd)'" % a, "export -f pwd; ls %s $(pwd)" % a):
            self.refused(cmd, self.tmp)
        # a session folder with a space in its name splits in two where it is printed, so the exemption is off there
        spaced = self.p("a -R b")
        os.makedirs(spaced, exist_ok=True)
        self.refused("ls %s $(pwd)" % a, spaced)
        # separators that do not balance (a case pattern's closing parenthesis) read every later command as one the feeder may run
        self.refused("echo -r | xargs true; case x in a) grep y %s $(true);; esac" % a, self.tmp)
        # an xargs inside a substitution widens only what the substitution holds
        self.refused("echo $(echo -r | xargs $(true) grep x %s)" % a, self.tmp)
        self.allowed("echo $(echo hi | xargs echo); ls $(pwd)", self.tmp)


if __name__ == "__main__":
    unittest.main()
