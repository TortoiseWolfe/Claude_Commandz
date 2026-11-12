"""Tests for hooks/output-scrubber.py, run as a subprocess the way test_coop_scrubber_hook.py runs its hook."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coop_fixtures as fx  # noqa: E402

HOOK = os.path.join(HERE, "..", "..", "hooks", "output-scrubber.py")
# Made-up values are put together at run time from pieces, so a leak scanner reading this public file finds
# nothing shaped like a real one.
FAKE_JWT = ".".join(["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJmYWtlIjoidGVzdC1vbmx5Iiwicm9sZSI6Im5vYm9keSJ9",
                     "ZmFrZXNpZ25hdHVyZS1mb3ItdGVzdGluZy1vbmx5"])
PLAIN_A = "abcd1234" + "efgh5678"
PLAIN_B = "hunter2" + "hunter2"
PLAIN_C = "abcdef" + "123456"
FAKE_SECRET = "Zq7fakeJWTsigningValue0123456789abcdefQwErTy"
FAKE_KNOWN = "Kx9!fake-known-value-for-tests-only-77Q"


class Hook(unittest.TestCase):
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
        fx.write_json(self.cfg, {"folders": ["~/repos/Acme-Co*"], "ner": False})
        fx.write_json(self.roster, fx.ROSTER)
        self.secrets = os.path.join(self.tmp, "secrets")      # empty unless a test fills it
        os.makedirs(self.secrets)
        self.base_env = {k: v for k, v in os.environ.items()
                         if not k.startswith(("COOP_", "SECRET_SCRUB", "KNOWN_", "SHELL_SCRUB", "CLAUDE_CODE_SHELL_PREFIX"))}
        self.alerts = os.path.join(self.tmp, "alerts.log")
        self.heart = os.path.join(self.tmp, "heartbeat")
        self.off = os.path.join(self.tmp, "off")
        self.base_env.update(SECRET_SCRUB_ALERTS=self.alerts, SHELL_SCRUB_HEARTBEAT=self.heart, SHELL_SCRUB_OFF_FILE=self.off)
        self.base_env.update(HOME=self.home, COOP_SCRUBBER_CONFIG=self.cfg, COOP_ROSTER_FILE=self.roster,
                             COOP_SCRUBBER_LOG=os.path.join(self.tmp, "coop.log"),
                             OUTPUT_SCRUBBER_LOG=os.path.join(self.tmp, "out.log"),
                             KNOWN_SECRETS_REPOS=os.path.join(self.home, "repos"), KNOWN_SECRETS_DIR=self.secrets,
                             KNOWN_SECRETS_IGNORE=os.path.join(self.tmp, "none"),
                             KNOWN_SECRETS_CACHE=os.path.join(self.tmp, "cache.json"))

    def run_hook(self, response, tool="Bash", cwd=None, raw=None, **env_extra):
        env = dict(self.base_env, **env_extra)
        stdin = raw if raw is not None else json.dumps({"tool_name": tool, "cwd": cwd or self.other,
                                                         "tool_response": response})
        return subprocess.run([sys.executable, HOOK], input=stdin, env=env, capture_output=True, text=True, timeout=60)

    def bash(self, text, stderr=""):
        return {"stdout": text, "stderr": stderr, "interrupted": False, "isImage": False, "noOutputExpected": False}

    def updated(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        return out["updatedToolOutput"]

    def silent(self, r):
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    # ---- masking
    def test_postgrest_config_reply(self):
        reply = json.dumps({"db_schemas": "public", "db_anon_role": "anon", "jwt_secret": FAKE_SECRET,
                            "max_rows": 1000, "server_port": 3000})
        r = self.run_hook(self.bash(reply))
        new = self.updated(r)
        self.assertEqual(sorted(new), sorted(self.bash("")))
        got = json.loads(new["stdout"])
        self.assertEqual(got["jwt_secret"], "<redacted:field jwt_secret>")
        self.assertEqual((got["db_schemas"], got["db_anon_role"], got["max_rows"], got["server_port"]),
                         ("public", "anon", 1000, 3000))
        self.assertEqual((new["stderr"], new["interrupted"], new["isImage"]), ("", False, False))
        self.assertNotIn(FAKE_SECRET, r.stdout)

    def test_bare_jwt(self):
        new = self.updated(self.run_hook(self.bash("got %s ok" % FAKE_JWT, stderr="err %s" % FAKE_JWT)))
        self.assertEqual(new["stdout"], "got <redacted:jwt> ok")
        self.assertEqual(new["stderr"], "err <redacted:jwt>")

    def test_mcp_tool_gets_tier_two_and_structured_fields(self):
        resp = [{"type": "text", "text": "API_KEY=" + PLAIN_A}]
        new = self.updated(self.run_hook(resp, tool="mcp__x__y"))
        self.assertEqual(new, [{"type": "text", "text": "API_KEY=<redacted:field API_KEY>"}])

    def test_known_value_in_every_form(self):
        with open(os.path.join(self.secrets, "fake_api_token.txt"), "w") as fh:
            fh.write(FAKE_KNOWN + "\n")
        sys.path.insert(0, os.path.join(HERE, ".."))
        import known_secrets_scan as ks
        forms = sorted(f.decode() for f in ks.forms(FAKE_KNOWN))
        self.assertGreaterEqual(len(forms), 5)
        text = "\n".join("form %d: [%s]" % (i, f) for i, f in enumerate(forms))
        r = self.run_hook(self.bash(text))
        new = self.updated(r)
        for f in forms:
            self.assertNotIn(f, new["stdout"])
        self.assertEqual(new["stdout"].count("<redacted:known-secret>"), len(forms))
        # the file-name cache holds paths only
        self.assertNotIn(FAKE_KNOWN, open(self.base_env["KNOWN_SECRETS_CACHE"]).read())

    def test_known_value_masked_for_read_too(self):
        with open(os.path.join(self.secrets, "fake_api_token.txt"), "w") as fh:
            fh.write(FAKE_KNOWN + "\n")
        resp = {"type": "text", "file": {"filePath": "/x", "content": "1\tnote: " + FAKE_KNOWN, "numLines": 1}}
        new = self.updated(self.run_hook(resp, tool="Read"))
        self.assertEqual(new["file"]["content"], "1\tnote: <redacted:known-secret>")
        self.assertEqual(new["file"]["numLines"], 1)

    def test_read_gets_tier_one_only(self):
        resp = {"type": "text", "file": {"filePath": "/x", "numLines": 2,
                                         "content": '1\tpassword = "%s"\n2\tDB_SECRET=%s' % (PLAIN_B, PLAIN_C)}}
        self.silent(self.run_hook(resp, tool="Read"))
        for tool in ("Edit", "Write", "NotebookEdit"):
            self.silent(self.run_hook(resp, tool=tool))
        resp["file"]["content"] += "\n3\ttoken " + FAKE_JWT
        new = self.updated(self.run_hook(resp, tool="Read"))
        self.assertIn("3\ttoken <redacted:jwt>", new["file"]["content"])
        self.assertIn('password = "hunter2hunter2"', new["file"]["content"])

    # ---- ordinary output passes
    def test_ordinary_output_prints_nothing(self):
        git_log = "\n".join("%07x Fix the token refresh path in the login flow" % (i * 7919) for i in range(30))
        cases = [git_log,
                 json.dumps({"items": [{"id": 1, "name": "a", "status": "ok"}], "count": 1, "total_tokens": 42}),
                 'token = get_token()\npassword = os.environ["X"]\nsecret = os.environ.get("S")\n',
                 "next_page_token=CAESBggBEAEYAg token_count=12 token_type: Bearer"]
        for text in cases:
            self.silent(self.run_hook(self.bash(text)))
        self.silent(self.run_hook(self.bash(""), tool="Bash"))

    def test_bad_input_never_fails(self):
        for raw in ("", "not json", "[]", json.dumps({"cwd": self.other})):
            self.silent(self.run_hook(None, raw=raw))

    def test_image_and_non_string_values_survive(self):
        resp = {"stdout": "", "stderr": "", "interrupted": False, "isImage": True, "n": 5, "x": None}
        self.silent(self.run_hook(resp))

    # ---- fail closed
    def test_crash_withholds_every_string(self):
        r = self.run_hook(self.bash("secret stuff " + FAKE_JWT, stderr="more"), SECRET_SCRUBBER_FAIL="1")
        new = self.updated(r)
        self.assertIn("withheld", new["stdout"])
        self.assertIn("withheld", new["stderr"])
        self.assertIs(new["interrupted"], False)
        self.assertIs(new["isImage"], False)
        self.assertNotIn("eyJ", r.stdout)
        self.assertNotIn("secret stuff", r.stdout)

    def test_crash_withholds_nested_shapes(self):
        resp = {"file": {"content": FAKE_JWT, "numLines": 3}, "list": [FAKE_JWT, 4]}
        new = self.updated(self.run_hook(resp, tool="Read", SECRET_SCRUBBER_FAIL="1"))
        self.assertNotIn("eyJ", json.dumps(new))
        self.assertEqual(new["file"]["numLines"], 3)
        self.assertEqual(new["list"][1], 4)

    # ---- co-op step
    def test_coop_step_runs_after_secret_step_in_coop_folder(self):
        text = "%s wrote %s with %s" % (fx.ZELDA, fx.ZELDA_EMAIL, FAKE_JWT)
        r = self.run_hook(self.bash(text), cwd=self.coop)
        new = self.updated(r)
        self.assertEqual(new["stdout"], "[PERSON_1] wrote [EMAIL_1] with <redacted:jwt>")
        self.assertNotIn(fx.ZELDA, r.stdout)

    def test_coop_step_alone_still_rewrites(self):
        new = self.updated(self.run_hook(self.bash("%s wrote from %s" % (fx.ZELDA, fx.ZELDA_EMAIL)), cwd=self.coop))
        self.assertEqual(new, self.bash("[PERSON_1] wrote from [EMAIL_1]"))

    def test_coop_step_does_not_run_elsewhere(self):
        new = self.updated(self.run_hook(self.bash("%s %s %s" % (fx.ZELDA, fx.ZELDA_EMAIL, FAKE_JWT)), cwd=self.other))
        self.assertEqual(new["stdout"], "%s %s <redacted:jwt>" % (fx.ZELDA, fx.ZELDA_EMAIL))
        self.silent(self.run_hook(self.bash(fx.ZELDA), cwd=self.other))

    def test_coop_failure_in_coop_folder_still_withholds(self):
        open(self.roster, "w").write("{broken")
        new = self.updated(self.run_hook(self.bash(fx.ZELDA + " " + FAKE_JWT), cwd=self.coop))
        self.assertIn("withheld", new["stdout"])
        self.assertNotIn(fx.ZELDA, new["stdout"])

    # ---- PostToolUseFailure
    def failure(self, error="", tool="Bash", **extra):
        payload = dict({"hook_event_name": "PostToolUseFailure", "session_id": "sess-1", "tool_name": tool, "cwd": self.other,
                        "tool_input": {"command": "echo " + FAKE_JWT}, "error": error}, **extra)
        return self.run_hook(None, raw=json.dumps(payload))

    def alert_lines(self):
        return open(self.alerts).read().splitlines() if os.path.exists(self.alerts) else []

    def test_failed_call_with_secret_alerts_and_tells_the_model(self):
        r = self.failure("Exit code 3\njwt_secret=%s" % FAKE_SECRET)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUseFailure")
        self.assertNotIn("updatedToolOutput", out)
        self.assertIn("SECRET-EXPOSED Bash", out["additionalContext"])
        self.assertIn("NOT masked", out["additionalContext"])
        self.assertNotIn(FAKE_SECRET, r.stdout)
        lines = self.alert_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("sess-1", lines[0])
        self.assertIn("Bash", lines[0])
        self.assertIn("count=1", lines[0])
        self.assertIn("failed-call", lines[0])
        self.assertNotIn(FAKE_SECRET, lines[0])
        self.assertNotIn("eyJ", lines[0])

    def test_failed_call_scans_other_strings_but_not_tool_input(self):
        self.silent(self.failure("Exit code 1\nnothing here"))          # the secret is only in tool_input
        self.assertEqual(self.alert_lines(), [])
        r = self.failure("x", tool_response={"stderr": "got " + FAKE_JWT})
        self.assertIn("additionalContext", r.stdout)

    def test_failed_call_without_secret_is_silent(self):
        self.silent(self.failure("Exit code 2\ncommand not found: foo"))
        self.assertEqual(self.alert_lines(), [])

    # ---- shell wrapper checks
    def bash_call(self, text="plain", background=False, **env):
        payload = {"hook_event_name": "PostToolUse", "session_id": "sess-2", "tool_name": "Bash", "cwd": self.other,
                   "tool_input": {"command": "x", "run_in_background": background}, "tool_response": self.bash(text)}
        return self.run_hook(None, raw=json.dumps(payload), **env)

    def fresh_heartbeat(self):
        open(self.heart, "w").close()

    def test_nothing_fires_when_the_prefix_variable_is_unset(self):
        self.silent(self.bash_call())                                    # no heartbeat file at all
        r = self.bash_call("got " + FAKE_JWT)
        self.updated(r)
        self.assertNotIn("additionalContext", r.stdout)
        self.assertEqual(self.alert_lines(), [])

    def test_wrapper_missed_when_the_hook_had_to_mask(self):
        self.fresh_heartbeat()
        r = self.bash_call("got " + FAKE_JWT, CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh")
        self.assertEqual(self.updated(r)["stdout"], "got <redacted:jwt>")
        lines = self.alert_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("wrapper-missed", lines[0])

    def test_healthy_wrapper_is_silent(self):
        self.fresh_heartbeat()
        self.silent(self.bash_call(CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh"))
        self.assertEqual(self.alert_lines(), [])

    def test_stale_or_missing_heartbeat_warns_once_per_30_minutes(self):
        env = dict(CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh")
        r = self.bash_call(**env)                                        # missing heartbeat
        ctx = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUse")
        self.assertIn("not running", ctx["additionalContext"])
        self.assertNotIn("updatedToolOutput", ctx)
        self.assertIn("heartbeat-stale", self.alert_lines()[0])
        r2 = self.bash_call(**env)                                       # inside 30 minutes: logged, not repeated
        self.silent(r2)
        self.assertEqual(len(self.alert_lines()), 2)
        old = time.time() - 1900
        os.utime(self.alerts + ".stamp", (old, old))
        self.assertIn("not running", self.bash_call(**env).stdout)       # after 30 minutes: again
        open(self.heart, "w").close()
        os.utime(self.heart, (time.time() - 1000, time.time() - 1000))   # older than 900 s
        os.utime(self.alerts + ".stamp", (old, old))
        self.assertIn("not running", self.bash_call(**env).stdout)

    def test_background_call_and_off_switch_skip_the_heartbeat_check(self):
        env = dict(CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh")
        self.silent(self.bash_call(background=True, **env))
        open(self.off, "w").close()
        # Changed: the off switch used to be silent. It is now loud (a notice and a "filter-off" line), but still
        # never logs "heartbeat-stale" because the heartbeat check is skipped while the filter is off.
        r = self.bash_call(**env)
        self.assertIn("switched off", r.stdout)
        self.assertEqual(len(self.alert_lines()), 1)
        self.assertNotIn("heartbeat-stale", self.alert_lines()[0])

    def test_off_switch_is_loud_once_per_30_minutes(self):
        env = dict(CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh")
        open(self.off, "w").close()
        r = self.bash_call(**env)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUse")
        self.assertIn("switched off", ctx["additionalContext"])
        self.assertIn("report.md", ctx["additionalContext"])
        self.assertNotIn("updatedToolOutput", ctx)
        self.assertEqual(len(self.alert_lines()), 1)
        self.assertIn("filter-off", self.alert_lines()[0])
        self.silent(self.bash_call(**env))                               # inside the window: no notice, no second line
        self.assertEqual(len(self.alert_lines()), 1)
        old = time.time() - 1900
        os.utime(self.alerts + ".off.stamp", (old, old))
        self.assertIn("switched off", self.bash_call(**env).stdout)      # after 30 minutes: again
        self.assertEqual(len(self.alert_lines()), 2)

    def test_off_switch_notice_does_not_use_the_heartbeat_stamp(self):
        env = dict(CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh")
        self.assertIn("not running", self.bash_call(**env).stdout)       # heartbeat warning stamps its own file
        open(self.off, "w").close()
        self.assertIn("switched off", self.bash_call(**env).stdout)

    def test_masked_call_while_off_logs_filter_off_masked(self):
        env = dict(CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh")
        open(self.off, "w").close()
        r = self.bash_call("got " + FAKE_JWT, **env)
        self.assertEqual(self.updated(r)["stdout"], "got <redacted:jwt>")
        self.assertIn("switched off", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        r2 = self.bash_call("again " + FAKE_JWT, **env)                  # window still open: masked line again, no notice
        self.updated(r2)
        self.assertNotIn("additionalContext", r2.stdout)
        words = [ln.split("\t")[-1] for ln in self.alert_lines()]
        self.assertEqual(words, ["filter-off-masked", "filter-off", "filter-off-masked"])
        self.assertTrue(all("count=1" in ln for ln in self.alert_lines() if ln.endswith("masked")))
        self.assertNotIn("eyJ", open(self.alerts).read())

    def test_only_bash_is_checked(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Grep", "cwd": self.other, "tool_input": {},
                   "tool_response": "fine"}
        self.silent(self.run_hook(None, raw=json.dumps(payload), CLAUDE_CODE_SHELL_PREFIX="/x/prefix.sh"))

    def test_log_has_counts_and_no_text(self):
        self.run_hook(self.bash("x " + FAKE_JWT))
        line = open(self.base_env["OUTPUT_SCRUBBER_LOG"]).read()
        self.assertIn("Bash\tmasked=1", line)
        self.assertNotIn("eyJ", line)


if __name__ == "__main__":
    unittest.main()
