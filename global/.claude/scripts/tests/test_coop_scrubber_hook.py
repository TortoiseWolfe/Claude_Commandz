import json
import os
import subprocess
import sys
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coop_fixtures as fx  # noqa: E402

HOOK = os.path.join(HERE, "..", "..", "hooks", "coop-scrubber.py")


class Hook(unittest.TestCase):
    # test_output_scrubber_hook.py runs every test in this class a second time through output-scrubber.py,
    # the hook that takes this one's place in settings.json, by overriding the three names below.
    hook = HOOK
    drop_env = ("COOP_",)

    def more_env(self):
        return {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.coop = os.path.join(self.home, "repos", "Acme-Co-stack")
        self.other = os.path.join(self.home, "repos", "Elsewhere")
        os.makedirs(self.coop)
        os.makedirs(self.other)
        self.cfg = os.path.join(self.tmp, "cfg.json")
        self.roster = os.path.join(self.tmp, "roster.json")
        self.log = os.path.join(self.tmp, "log.tsv")
        fx.write_json(self.cfg, {"folders": ["~/repos/Acme-Co*"], "ner": False})
        fx.write_json(self.roster, fx.ROSTER)

    def run_hook(self, cwd, response, tool="Bash", raw=None, **env_extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(self.drop_env)}
        env.update(HOME=self.home, COOP_SCRUBBER_CONFIG=self.cfg, COOP_ROSTER_FILE=self.roster,
                   COOP_SCRUBBER_LOG=self.log)
        env.update(self.more_env())
        env.update(env_extra)
        stdin = raw if raw is not None else json.dumps({"tool_name": tool, "cwd": cwd, "tool_response": response})
        r = subprocess.run([sys.executable, self.hook], input=stdin, env=env, capture_output=True, text=True, timeout=60)
        return r

    def bash_response(self, text):
        return {"stdout": text, "stderr": "", "interrupted": False, "isImage": False}

    def test_non_coop_folder_prints_nothing(self):
        r = self.run_hook(self.other, self.bash_response(fx.ZELDA + " " + fx.ZELDA_EMAIL))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertFalse(os.path.exists(self.log))

    def test_coop_folder_rewrites_bash_output_in_its_own_shape(self):
        r = self.run_hook(self.coop, self.bash_response("%s wrote from %s" % (fx.ZELDA, fx.ZELDA_EMAIL)))
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertEqual(out["updatedToolOutput"], self.bash_response("[PERSON_1] wrote from [EMAIL_1]"))
        self.assertNotIn(fx.ZELDA, r.stdout)

    def test_subfolder_of_a_coop_folder_counts(self):
        deep = os.path.join(self.coop, "a", "b")
        os.makedirs(deep)
        r = self.run_hook(deep, self.bash_response(fx.BARNABY))
        self.assertIn("[PERSON_2]", r.stdout)

    def test_read_tool_shape_is_kept(self):
        resp = {"type": "text", "file": {"filePath": "/x", "content": "1\t%s" % fx.BARNABY_EMAIL, "numLines": 1}}
        r = self.run_hook(self.coop, resp, tool="Read")
        new = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(new["file"], {"filePath": "/x", "content": "1\t[EMAIL_2]", "numLines": 1})
        self.assertEqual(new["type"], "text")

    def test_clean_text_prints_nothing(self):
        r = self.run_hook(self.coop, self.bash_response("docker compose up -d"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_log_has_counts_and_no_text(self):
        self.run_hook(self.coop, self.bash_response(fx.ZELDA + " " + fx.ZELDA_EMAIL))
        line = open(self.log).read()
        self.assertIn("person=1\temail=1\tphone=0", line)
        self.assertNotIn("Zelda", line)
        self.assertNotIn("example", line)

    def test_no_config_means_off_everywhere(self):
        os.remove(self.cfg)
        r = self.run_hook(self.coop, self.bash_response(fx.ZELDA))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_unreadable_config_leaves_output_alone_and_says_why(self):
        open(self.cfg, "w").write("{broken")
        r = self.run_hook(self.coop, self.bash_response(fx.ZELDA))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("skipped", r.stderr)

    def test_unreadable_roster_in_a_coop_folder_withholds_the_text(self):
        open(self.roster, "w").write("{broken")
        r = self.run_hook(self.coop, self.bash_response(fx.ZELDA))
        self.assertEqual(r.returncode, 0)
        new = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("withheld", new["stdout"])
        self.assertEqual(new["stderr"], "")
        self.assertIs(new["interrupted"], False)
        self.assertNotIn(fx.ZELDA, r.stdout)

    def test_bad_input_never_fails(self):
        for raw in ("", "not json", "[]", json.dumps({"cwd": self.coop})):
            r = self.run_hook(self.coop, None, raw=raw)
            self.assertEqual((r.returncode, r.stdout), (0, ""), raw)

    def test_presidio_stand_in_is_used_when_ner_is_on(self):
        fx.write_json(self.cfg, {"folders": ["~/repos/Acme-Co*"]})
        py, worker = fx.write_fake_presidio(self.tmp)
        r = self.run_hook(self.coop, self.bash_response("Wendell Fogbottom"),
                          COOP_SCRUBBER_PRESIDIO_PYTHON=py, COOP_SCRUBBER_PRESIDIO_WORKER=worker)
        self.assertRegex(json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]["stdout"], r"^\[PERSON_x[0-9a-f]{4}\]$")

    def test_no_presidio_still_scrubs_and_warns(self):
        fx.write_json(self.cfg, {"folders": ["~/repos/Acme-Co*"]})
        r = self.run_hook(self.coop, self.bash_response(fx.ZELDA), COOP_SCRUBBER_PRESIDIO_PYTHON=os.path.join(self.tmp, "no"))
        self.assertIn("[PERSON_1]", r.stdout)
        self.assertIn("Presidio is not available", r.stderr)


if __name__ == "__main__":
    unittest.main()
