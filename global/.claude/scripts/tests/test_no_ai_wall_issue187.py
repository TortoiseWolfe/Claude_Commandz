"""Issue 187: shell reads of the private list or of a walled path that slipped past the wall. Fake folders only.
1. The list read by a relative name after a `cd` (or `pushd`): the check joined every word with the folder the session started in.
2. A walled path or the list written with brace expansion: the shell makes two paths of `{a,b}`, the wall read one.
3. A walled path written with an operator on the home variable (`${HOME:0}`, `${HOME%/}`): only `$HOME` and `${HOME}` were understood.
Each shape is refused (red first on the old code), also through the hook itself with the keys a subagent's call carries; the controls stay allowed."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_no_ai_wall as base  # noqa: E402

w = base.w
HOOK = os.path.join(os.path.dirname(os.path.abspath(w.__file__)), "no-ai-wall-guard.py")


class ReadGapTests(base.Fixture):
    run_bash, allowed, refused = base.Issue130Tests.run_bash, base.Issue130Tests.allowed, base.Issue130Tests.refused

    def setUp(self):
        super().setUp()
        # the list lives in a folder of its own, as the real one does (~/.config/afa), under the fake home folder
        self.cfg = os.path.join(self.home, ".config", "afa")
        os.makedirs(self.cfg)
        self.pfile = os.path.join(self.cfg, "no-ai-paths.txt")
        os.environ["NO_AI_PATHS_FILE"] = self.pfile
        self.meet = self.p("home/meetings/coop/2026-11-25")
        os.makedirs(self.meet)
        with open(os.path.join(self.meet, "actions.md"), "w") as fh:
            fh.write("x")
        self.paths(self.p("home/meetings/coop"))
        self.work = self.p("work")

    # ---- 1: the list, read by a relative name after a cd ----

    def test_the_list_read_by_a_relative_name_after_a_cd_is_refused(self):
        c = self.cfg
        for cmd in ("cd %s && cat no-ai-paths.txt" % c, "cd %s; head -3 no-ai-paths.txt" % c, "cd %s && grep . no-ai-paths.txt" % c,
                    "cd ~/.config/afa && sed -n 1p no-ai-paths.txt", "cd $HOME/.config/afa && awk 1 no-ai-paths.txt", "cd %s && tail no-ai-paths.txt" % c,
                    "cd %s && cat ./no-ai-paths.txt" % c, "cd %s && cat no-ai-*" % c, "cd %s && cat *" % c, "cd %s && cat < no-ai-paths.txt" % c,
                    "cd %s && while read l; do echo $l; done < no-ai-paths.txt" % c, "pushd %s; cat no-ai-paths.txt" % c,
                    "cd %s && python3 -c \"print(open('no-ai-paths.txt').read())\"" % c, "cd %s && for f in *; do cat $f; done" % c,
                    "cd %s && grep -r . ." % c, "cd %s && grep . *" % c,
                    # from a folder above: the relative name carries the rest of the way
                    "cd ~ && cat .config/afa/no-ai-paths.txt", "cd && cat .config/afa/no-ai-paths.txt", "cd ~/.config && cat afa/no-ai-paths.txt",
                    "cd %s && cat .config/afa/no-ai-paths.txt" % self.home, "cd %s/.. && cat afa/no-ai-paths.txt" % c,
                    "cd ~/.config; cd afa; cat no-ai-paths.txt"):
            self.refused(cmd, self.work)

    def test_the_list_read_by_a_relative_name_from_its_own_folder_is_refused(self):
        for cmd in ("cat no-ai-paths.txt", "head no-ai-paths.txt", "grep . no-ai-paths.txt", "cat *", "cat no-ai-*"):
            self.refused(cmd, self.cfg)

    def test_other_reads_after_a_cd_stay_allowed(self):
        c = self.cfg
        self.allowed("cd %s && ls" % c, self.work)  # a listing that only looks at names
        self.allowed("cd %s && ls -la no-ai-paths.txt" % c, self.work)
        self.allowed("cd %s && cat a.txt" % self.work, self.work)
        self.allowed("cd ~ && cat other.txt", self.work)
        self.allowed("cd ~/.config && cat other.txt", self.work)
        self.allowed("cd %s && cat other.txt" % self.work, self.cfg)  # a session that starts in the list's folder but works elsewhere

    # ---- 2: brace expansion ----

    def test_a_walled_path_or_the_list_written_with_braces_is_refused(self):
        h = self.home
        for cmd in ("cat %s/{meetings,x}/coop/2026-11-25/actions.md" % h, "cat %s/meetings/{coop,x}/2026-11-25/actions.md" % h,
                    "cat {%s/meetings/coop/2026-11-25/actions.md,/dev/null}" % h, "cat %s/.config/afa/{no-ai-paths.txt,x}" % h,
                    "cat %s/.config/{afa,x}/no-ai-paths.txt" % h, "head ~/{meetings,x}/coop/2026-11-25/actions.md",
                    "grep x %s/meetings/coop/2026-11-25/action{s,x}.md" % h, "sed -n 1p %s/{meetings,x}/coop/*/actions.md" % h,
                    "cat %s/meetings/coop/{2026-11-25,x}/{actions,y}.md" % h, "cat %s/{a,{meetings,b}}/coop/2026-11-25/actions.md" % h,
                    # a sequence that writes one letter hides a name too
                    "cat %s/.config/afa/no-ai-paths.tx{t..t}" % h, "cat %s/{m..m}eetings/coop/2026-11-25/actions.md" % h):
            self.refused(cmd, self.work)
        self.allowed("cat %s/{a.txt,b.txt}" % self.work, self.work)
        self.allowed("cat {a,b}.txt", self.work)
        self.allowed("echo {1..3}", self.work)

    # ---- 3: operators on the home variable ----

    def test_an_operator_on_the_home_variable_is_read_as_the_home_folder(self):
        for cmd in ("cat ${HOME:0}/meetings/coop/2026-11-25/actions.md", "cat ${HOME%/}/meetings/coop/2026-11-25/actions.md",
                    "cat ${HOME/x/y}/meetings/coop/2026-11-25/actions.md", "cat ${HOME#/zz}/meetings/coop/2026-11-25/actions.md",
                    "cat ${HOME:-/x}/meetings/coop/2026-11-25/actions.md", "cat ${HOME}/.config/afa/no-ai-paths.txt",
                    "cat ${HOME%/}/.config/afa/no-ai-paths.txt", "cd ${HOME:0}/.config/afa && cat no-ai-paths.txt"):
            self.refused(cmd, self.work)
        # another variable that only starts with HOME is not the home folder
        self.allowed("cat ${HOMEX:0}/meetings/a.md", self.work)

    # ---- the plain shapes the hub named stay refused, through the hook itself ----

    def hook(self, command, **extra):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.work}
        payload.update(extra)
        env = dict(os.environ)
        r = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload), capture_output=True, text=True, timeout=30, env=env)
        return r.returncode, r.stderr

    def test_reads_through_the_hook_are_refused_with_or_without_a_subagents_keys(self):
        agent = {"agent_id": "a1b2c3", "agent_type": "general-purpose", "session_id": "s", "transcript_path": "/x", "hook_event_name": "PreToolUse"}
        a = os.path.join(self.meet, "actions.md")
        for cmd in ("cat " + a, "head " + a, "head -n 5 " + a, "grep -n x " + a, "sed -n 1,3p " + a, "cat " + self.pfile, "head " + self.pfile,
                    "grep . " + self.pfile, "sed -n p " + self.pfile, "cd %s && cat no-ai-paths.txt" % self.cfg,
                    "cat %s/{meetings,x}/coop/2026-11-25/actions.md" % self.home):
            for extra in ({}, agent):
                rc, err = self.hook(cmd, **extra)
                self.assertEqual(rc, 2, "not refused (%s): %s" % (sorted(extra), cmd))
                self.assertIn("no-ai wall", err)
                self.assertNotIn(self.home, err)  # the refusal names an entry number, never a path
        rc, err = self.hook("cat %s/a.txt" % self.work, **agent)
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
