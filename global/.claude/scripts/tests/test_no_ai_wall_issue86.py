"""Issue 86: the wall crashed (RecursionError, so a refusal that named an error and not the cause) on a harmless command that held an unusual
space: a non-breaking space, a form feed and the like. The reader splits a word again when it holds white space, and str.isspace() counts
characters that shlex.split does not split on, so the split gave the same word back and called itself forever. Every path here is a fake folder."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_no_ai_wall as base  # noqa: E402  (the module, not its names: a test class imported by name would be collected twice)

w = base.w

ODD_SPACES = {
    "no-break space U+00A0": "\u00a0",
    "narrow no-break space U+202F": "\u202f",
    "ideographic space U+3000": "\u3000",
    "next line U+0085": "\u0085",
    "form feed": "\f",
    "vertical tab": "\v",
}


class Issue86Tests(base.Fixture):
    run_bash, allowed, refused = base.Issue130Tests.run_bash, base.Issue130Tests.allowed, base.Issue130Tests.refused

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))

    def test_a_harmless_command_with_an_odd_space_gets_a_verdict_not_a_crash(self):
        for name, sp in ODD_SPACES.items():
            for cmd in ("git commit -m 'it is 20%skm away'" % sp, "echo 20%skm" % sp, "echo 'a%sb' c" % sp, "ls work/a.txt%s" % sp):
                with self.subTest(space=name, command=cmd):
                    self.allowed(cmd, self.p("work"))

    def test_words_returns_for_every_odd_space(self):
        for name, sp in ODD_SPACES.items():
            with self.subTest(space=name):
                got = w._words("echo 'a%sb'" % sp)
                self.assertIn("a%sb" % sp, got)

    def test_a_quoted_inner_command_is_still_split_as_before(self):
        self.assertEqual(w._words("bash -c 'find /x'"), ["bash", "-c", "find /x", "find", "/x"])
        self.assertEqual(w._words("echo 'a\tb'"), ["echo", "a\tb", "a", "b"])
        # the real thing stays refused: a search into a folder above a listed one, typed inside a quoted command
        self.refused("bash -c 'grep -r x %s'" % self.p("wall"), self.tmp)
        self.refused("bash -c 'grep -r x%s%s'" % (" ", self.p("wall")), self.tmp)

    def test_the_command_word_groups_do_not_loop_either(self):
        for sp in ODD_SPACES.values():
            self.allowed("echo 'one%stwo'" % sp, self.tmp)


if __name__ == "__main__":
    unittest.main()
