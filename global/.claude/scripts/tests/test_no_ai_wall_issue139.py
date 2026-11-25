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
