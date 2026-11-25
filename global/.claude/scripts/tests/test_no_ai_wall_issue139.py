"""Issue 139: three ways the wall worked out the wrong working folder, so a command could reach a folder on the no-AI list.
1. CDPATH: a relative `cd` that CDPATH resolves into a listed folder was read as a folder under the current one.
2. After a `cd` the wall cannot work out (`cd $X`, a backtick), a later relative `cd` set a guessed folder again instead of staying unknown.
3. `bash -c 'cd; ...'`: the child shell's bare `cd` goes home, and a relative path after it was not checked against home.
Each shape is refused (red first on the old code); the controls with a known folder stay allowed. Every path is a fake folder."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_no_ai_wall as base  # noqa: E402  (the module, not its names: a test class imported by name would be collected twice)

w = base.w


class Issue139Tests(base.Fixture):
    run_bash, allowed, refused = base.Issue130Tests.run_bash, base.Issue130Tests.allowed, base.Issue130Tests.refused

    def setUp(self):
        super().setUp()
        self.paths(self.p("wall/clientA"), self.p("wall/clientB"))
        os.makedirs(self.p("home/clientA"), exist_ok=True)
        os.makedirs(w.wall_dir(), exist_ok=True)
        self.addCleanup(lambda: os.environ.pop("CDPATH", None))
        self.work = self.p("work")

    # ---- 1: CDPATH ----

    def test_cdpath_in_the_text_makes_a_plain_relative_cd_unknown(self):
        wall = self.p("wall")
        for cmd in ("CDPATH=%s cd clientA && cat docs/secret.txt" % wall, "export CDPATH=%s; cd clientA; cat docs/secret.txt" % wall,
                    "CDPATH=%s; cd clientA; rm docs/secret.txt" % wall, "export CDPATH=%s; cd clientA && rm -f docs/secret.txt" % wall,
                    "export CDPATH=%s; cd clientA; grep -r x ." % wall):
            self.refused(cmd, self.work)

    def test_cdpath_in_the_environment_makes_a_plain_relative_cd_unknown(self):
        os.environ["CDPATH"] = self.p("wall")
        for cmd in ("cd clientA; cat docs/secret.txt", "cd clientA && rm docs/secret.txt", "cd clientA; grep -r x ."):
            self.refused(cmd, self.work)

    def test_other_ways_to_change_where_a_cd_lands_are_unknown_too(self):
        wall = self.p("wall")
        for cmd in ("cdpath=(%s); cd clientA; rm docs/secret.txt" % wall, "setopt cdable_vars; cd clientA; rm docs/secret.txt",
                    "shopt -s cdable_vars; cd clientA; rm docs/secret.txt", "export CDPATH; cd clientA; rm docs/secret.txt",
                    # zsh reads option names in any case and with or without underscores; a named folder is not the working folder
                    "setopt CDABLEVARS; cd clientA; rm docs/secret.txt", "setopt CDABLE_VARS; cd clientA; rm docs/secret.txt",
                    "setopt AUTO_CD; cd clientA; rm docs/secret.txt", "setopt c_d_a_b_l_e_v_a_r_s; cd clientA; rm docs/secret.txt",
                    "setopt _cdable__vars_; cd clientA; rm docs/secret.txt", "hash -r -d w=%s; cd ~w; rm clientA/docs/secret.txt" % wall, "hash -d w=%s; cd ~w; rm clientA/docs/secret.txt" % wall,
                    "cd ~someone; rm docs/secret.txt", "cd ~+; rm docs/secret.txt"):
            self.refused(cmd, self.work)
        self.allowed("cd ~/proj; cat a.txt", self.work)  # a folder under the home folder is still read as one
        self.allowed("cd ~; cat a.txt", self.work)

    def test_quoting_escaping_and_short_flags_do_not_hide_a_cd_changing_option(self):
        # the shell removes quotes and backslashes before it runs a word, so `ha\sh -d` is `hash -d`; zsh's short flags are option names too
        # (-J is AUTO_CD, -T is CDABLE_VARS), and setopt, unsetopt and emulate can change how a cd lands in ways the text does not show
        wall = self.p("wall")
        for cmd in ("ha\\sh -d w=%s; cd ~w; rm clientA/docs/secret.txt" % wall, "'hash' -d w=%s; cd ~w; rm clientA/docs/secret.txt" % wall,
                    "ha\"\"sh -d w=%s; cd ~w; rm clientA/docs/secret.txt" % wall, "hash -f w=%s; cd clientA; rm docs/secret.txt" % wall,
                    "set -J; cd clientA; rm docs/secret.txt", "set -T; cd clientA; rm docs/secret.txt", "set -eJ; cd clientA; rm docs/secret.txt", "set -e -J; cd clientA; rm docs/secret.txt",
                    "se\\t -J; cd clientA; rm docs/secret.txt", "setopt autocd; cd clientA; rm docs/secret.txt", "set\\opt c\"\"dablevars; cd clientA; rm docs/secret.txt",
                    "unsetopt cdablevars; cd clientA; rm docs/secret.txt", "emulate -L zsh; cd clientA; rm docs/secret.txt",
                    "\\emulate zsh; cd clientA; rm docs/secret.txt"):
            self.refused(cmd, self.work)
        self.allowed("set -e; cd ~/proj; cat a.txt", self.work)  # -e is not a cd option

    def test_ansi_c_quotes_locale_quotes_and_brace_expansion_do_not_hide_a_cd_changing_option(self):
        # the shell writes out $'..' (with escapes), $".." and {a,b} before it runs a word: `$'ha\x73h' -d` is `hash -d`, `{hash,x} -d` is `hash -d`
        wall = self.p("wall")
        # (the option name is written so the quote-stripped text does not show setopt, hash or cdablevars either)
        for cmd in ("$'set\\157pt' $'cdable\\x76ars'; cd clientA; rm docs/secret.txt", "$\"setopt\" $\"cdable\"vars; cd clientA; rm docs/secret.txt",
                    "{setopt,x} cdable{vars,x}; cd clientA; rm docs/secret.txt", "$'ha\\x73h' -d w=%s; cd clientA; rm docs/secret.txt" % wall,
                    "{hash,x} -d w=%s; cd clientA; rm docs/secret.txt" % wall,
                    # a sequence writes one letter too: `{s..s}etopt` is `setopt`
                    "{s..s}etopt cdable{v..v}ars; cd clientA; rm docs/secret.txt", "{h..h}ash -d w=%s; cd clientA; rm docs/secret.txt" % wall):
            self.refused(cmd, self.work)
        self.allowed("cd ~/proj; cat a.txt", self.work)

    def test_a_variable_that_only_starts_with_home_is_not_the_home_folder(self):
        # `$HOMEX` is another variable: it must never be read as the home folder followed by an X
        self.refused("cd $HOMEX; rm docs/secret.txt", self.work)
        self.refused("cd $HOMEX; cd clientA; rm docs/secret.txt", self.work)
        self.assertNotEqual(w.expand("$HOMEX/a", "/base"), w.home() + "X/a")
        self.assertEqual(w.expand("$HOME/a", "/base"), w.home() + "/a")
        self.assertEqual(w.expand("${HOME}/a", "/base"), w.home() + "/a")

    def test_too_many_possible_folders_is_refused_not_cut_short(self):
        # the folders a cd may reach are never cut to a cap: over it, the answer is None and the callers refuse
        entries = ["/e%d" % n for n in range(1, 8)]
        self.assertIsNone(w._cd_bases("d", [self.work + "/x%d" % n for n in range(450)], entries))
        self.assertEqual(len(w._cd_bases("d", [self.work + "/x%d" % n for n in range(90)], entries)), 90 + 7 + 1)
        wall = self.p("wall")
        cmd = "cd " + " ; cd ".join("a%d" % n for n in range(500)) + "; cat clientA/x.txt"
        self.assertEqual(len(w._cd_bases("d", [self.work], entries)), 1 + 7 + 1)
        self.refused("export CDPATH=%s; " % wall + cmd, self.work)

    def test_without_cdpath_the_same_cd_is_a_known_folder(self):
        self.allowed("cd proj; cat a.txt", self.work)
        self.allowed("cd ./proj && cat a.txt", self.work)
        os.environ["CDPATH"] = self.p("wall")
        self.allowed("cd ./proj && cat a.txt", self.work)  # a name that starts with ./ is never looked up in CDPATH
        self.allowed("cd %s/proj && cat a.txt" % self.work, self.work)

    # ---- 2: a guess after an unknown cd ----

    def test_a_relative_cd_after_an_unknown_cd_stays_unknown(self):
        for cmd in ("cd $NOWHERE; cd clientA; rm docs/secret.txt", "cd `echo x`; cd clientA; rm docs/secret.txt", "cd $(echo x); cd docs; rm secret.txt",
                    "cd -; cd docs; rm secret.txt", "cd $NOWHERE && cd ./docs && rm secret.txt"):
            self.refused(cmd, self.p("wall/clientA"))
        # the search side: an unknown cd then a recursive search naming no root, from a folder above a listed one
        for cmd in ("cd $NOWHERE; cd sub; grep -r x", "cd `echo x`; cd sub; grep -r x .", "cd $NOWHERE; cd sub; find . -name x"):
            self.refused(cmd, self.p("wall"))

    def test_a_relative_root_after_an_unknown_cd_takes_the_wide_reading(self):
        # the search runs inside the folder the text cannot name, so ./sub and sub are not under the working folder; only roots that are
        # absolute, under ~ or from a variable are judged as written (security review of the first fix for issue 139)
        for cmd in ("cd $X; grep -r x ./clientA", "cd $X; grep -r x clientA", "cd $X; find ./clientA -name x", "cd $X; ls -R ./clientA", "cd $X; du ./clientA",
                    "cd $X; rg x ./clientA", "cd $X; rsync -a ./clientA /tmp/out", "cd $X; grep -r x %s ./clientA" % self.work, "cd $X; tar cf /tmp/o.tar clientA"):
            self.refused(cmd, self.p("wall"))
            self.refused(cmd, self.tmp)  # a folder above the listed ones: the unknown folder could be the one below it
        self.allowed("cd $X; grep -r x %s/proj" % self.work, self.work)  # an absolute root is judged as written
        self.allowed("cd $X; find %s/proj -name x" % self.work, self.work)

    def test_a_known_cd_then_a_relative_cd_is_still_followed(self):
        self.allowed("cd %s/proj; cd .; cat a.txt" % self.work, self.tmp)
        self.allowed("cd %s; cd proj; rm a.txt" % self.work, self.tmp)

    # ---- 3: bash -c with a bare cd ----

    def test_a_child_shell_cd_with_no_argument_goes_home(self):
        # the child shell's cd goes to the home folder; a relative path after it names something under home, not under the working folder
        os.makedirs(self.p("home/clientA/docs"), exist_ok=True)
        self.paths(self.p("home/clientA"))
        for cmd in ("bash -c 'cd; cat clientA/docs/x.txt'", "bash -c 'cd && rm -rf clientA'", "sh -c 'cd; grep -r x clientA'", "bash -c 'cd; grep -r x .'",
                    "bash -lc 'cd ; rm clientA/docs/x.txt'", "bash -c \"cd; cat clientA/notes.md\""):
            self.refused(cmd, self.work)

    def test_a_child_shell_with_a_known_cd_stays_allowed(self):
        self.paths(self.p("home/clientA"))
        self.allowed("bash -c 'cd %s/proj; cat a.txt'" % self.work, self.work)
        self.allowed("bash -c 'cat a.txt'", self.work)


if __name__ == "__main__":
    unittest.main()
