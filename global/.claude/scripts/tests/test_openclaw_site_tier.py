#!/usr/bin/env python3
"""Site-model work runs on Fable or Opus, at effort high, on every way the dispatcher starts a lane (his rule, 19 Oct; the tier-3d lane's
report found openclaw_dispatch still sent Tigard asks to Sonnet). A start that lane.sh refuses on tier is reported once and never
started again on another tier. The PC and OpenClaw are fakes: no lane, window or session is touched."""
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_guard  # noqa: E402,F401  first: nothing here may write the live ~/.claude/state
import openclaw_dispatch as dp  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
import test_openclaw_pass as tp  # noqa: E402
import test_openclaw_reply as tr  # noqa: E402

NOW = td.NOW
ASK = tp.ASK
SITE_ASKS = ("fit the Tigard site model to the porch photos", "extend the site-model skill with a roof step",
             "compare data/sites/tigard/footprints.json with the survey", "export the house to SketchUp geometry")
REFUSAL = "refusing model sonnet: the prompt names data/sites/ or site-model, and 3D fitting, photo matching and site-model work runs only on fable or opus"


class TheRule(unittest.TestCase):
    def test_each_trigger_the_rules_name_moves_a_lane_to_claude_opus(self):
        for task in SITE_ASKS:
            self.assertEqual(dp.site_tier(task, "codex", "sonnet"), ("claude", "opus", True), task)
            self.assertEqual(dp.choose_engine("/none", None, task)[:2], ("claude", "opus"), task)

    def test_fable_and_opus_stay_as_they_are_and_everything_else_moves_up(self):
        t = SITE_ASKS[0]
        self.assertEqual(dp.site_tier(t, "claude", "fable"), ("claude", "fable", False))
        self.assertEqual(dp.site_tier(t, "claude", "opus"), ("claude", "opus", False))
        for engine, model in (("claude", "sonnet"), ("claude", "haiku"), ("copilot", "sonnet"), ("claude", "gpt"), ("codex", "opus")):
            self.assertEqual(dp.site_tier(t, engine, model)[:2], ("claude", "opus"), (engine, model))

    def test_other_asks_are_left_alone(self):
        for task in ("audit the photo-matched pairs for site-01", "build a website model of the tax tables", "fix the lint errors",
                     "count the open issues", "the site is down"):
            self.assertEqual(dp.site_tier(task, "codex", "sonnet"), ("codex", "sonnet", False), task)
            self.assertNotEqual(dp.choose_engine("/none", None, task)[1], "opus", task)

    def test_it_catches_whatever_lane_sh_would_refuse(self):
        """lane.sh greps the prompt file for 'data/sites/|site-model': the dispatcher's test must be a superset of that."""
        for text in ("data/sites/x", "site-model", "The Site-Model skill", "a SITE MODEL"):
            self.assertTrue(dp.SITE_RE.search(text), text)


class ChatStart(td.Base):
    def test_a_site_ask_in_his_own_repo_starts_on_claude_opus_not_codex_or_sonnet(self):
        for i, task in enumerate(SITE_ASKS):
            self.write_done()
            self.start("own-app", task, NOW + i * 60)
            name, _dir, engine, model, _p = self.host.started[-1]
            self.assertEqual((engine, model), ("claude", "opus"), task)
            self.assertIn("Claude Opus", self.host.chats[-1])

    def write_done(self):
        state = self.state()
        for lane in state["lanes"].values():  # a lane that reported is not running: three at once is the cap
            lane["status"] = "closed"
        dp.save_state(self.ctx.state_path, state)

    def test_a_docker_repo_and_a_client_repo_get_opus_for_it_too(self):
        self.start("own-docker", SITE_ASKS[0], NOW)
        self.start("client-app", SITE_ASKS[1], NOW + 60)
        self.assertEqual([s[2:4] for s in self.host.started], [("claude", "opus")] * 2)

    def test_the_week_running_hot_does_not_step_a_site_lane_down_to_sonnet(self):
        self.host.proj = 93.4
        self.start("client-app", SITE_ASKS[0])
        self.assertEqual(self.host.started[-1][3], "opus")
        self.assertNotIn("Sonnet only", self.host.chats[-1])

    def test_context_that_names_a_site_model_moves_the_lane_up_too(self):
        """The task is plain, but the earlier chat attached to the prompt names the model lane.sh would refuse Sonnet on."""
        ctx = {"history": [tr.msg("user", "keep going on the site-model fit", NOW)], "his_ts": NOW}
        dp.do_start(self.ctx, self.host, "own-docker", "do the next step", NOW, None, ctx)
        self.assertIn("site-model fit", Path(self.host.started[-1][4]).read_text())
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))

    def test_a_refused_start_is_reported_once_and_not_started_again_on_a_cheaper_tier(self):
        self.host.start_ok = (False, REFUSAL)
        kind, line = self.act("start own-app: " + SITE_ASKS[0])
        self.assertIn("refused: " + REFUSAL, line)
        self.assertIn("runs only on Fable or Opus", self.host.chats[-1])
        self.assertNotIn("refusing model", self.host.chats[-1])
        self.assertEqual(len(self.host.started), 1)
        self.assertEqual(self.host.started[0][3], "opus")
        dp.fallback_due(self.ctx, self.host, NOW + 3600)  # his words are on the hub's list; a lane for them, if any, is never a lower tier
        self.assertTrue(all(s[2:4] == ("claude", "opus") for s in self.host.started), self.host.started)
        self.assertEqual((self.lane_names(), self.state()["starts"]), ([], []))


class Restart(td.Base):
    def row(self, name, model, billing, prompt):
        day = time.strftime("%F", time.localtime(NOW))
        old = self.tmp / "lanes" / ("%s-%s" % (day, name))
        old.mkdir(parents=True)
        (old / "prompt.md").write_text(prompt)
        (old / "report.md").write_text("Done so far. 3:20 PM\n")
        row = [day, name, str(self.root / "own-app"), model, billing, str(old / "prompt.md"), str(old / "report.md"), "sid", "", "", "on"]
        with open(self.ctx.index_path, "a") as f:
            f.write("\t".join(row) + "\n")

    def test_a_fable_lane_restarts_on_fable_not_sonnet(self):
        self.row("tigard-fit", "fable", "subscription", "Fit the Tigard site model.\n")
        dp.restart_lane(self.ctx, self.host, "tigard-fit", "go on", NOW + 60)
        self.assertEqual((self.host.started[-1][0],) + self.host.started[-1][2:4], ("tigard-fit-2", "claude", "fable"))

    def test_a_site_lane_whose_row_says_sonnet_or_codex_restarts_on_opus(self):
        self.row("old-sonnet", "sonnet", "subscription", "Work in data/sites/tigard.\n")
        self.row("old-codex", "sonnet", "codex", "Extend the site-model skill.\n")
        dp.restart_lane(self.ctx, self.host, "old-sonnet", "go on", NOW + 60)
        dp.restart_lane(self.ctx, self.host, "old-codex", "go on", NOW + 61)
        self.assertEqual([s[2:4] for s in self.host.started], [("claude", "opus")] * 2)

    def test_a_plain_lane_keeps_its_row(self):
        self.row("plain", "sonnet", "subscription", "Count the issues.\n")
        dp.restart_lane(self.ctx, self.host, "plain", "go on", NOW + 60)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "sonnet"))


class ThePass(tp.PassBase):
    def setUp(self):
        super().setUp()
        self.lane = "oc-own-app-aaa"

    def prompt(self):
        return self.ctx.lanes_dir / ("%s-%s" % (time.strftime("%F", time.localtime(NOW)), self.lane)) / "prompt.md"

    def go(self, task, **kw):
        self.hand(task=task)
        dp.write_prompt(self.ctx, self.lane, "<<<TASK\n%s\nTASK>>>\n" % task, NOW)
        self.index_row(self.lane, begin_for=False)  # what lane.sh writes: one row in the lane index
        a = dict(ask_id=ASK, lane_name=self.lane, dirpath=self.root / "own-app", model="sonnet", prompt_path=self.prompt(), now=NOW + 5)
        a.update(kw)
        return dp.start_for_ask(self.ctx, self.host, **a)

    def test_the_pass_choosing_sonnet_or_copilot_for_site_work_is_moved_up(self):
        ok, msg = self.go(SITE_ASKS[0], engine="copilot")
        self.assertTrue(ok, msg)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))

    def test_a_plain_task_is_untouched(self):
        ok, msg = self.go("count the open issues", engine="copilot")
        self.assertTrue(ok, msg)
        self.assertEqual(self.host.started[-1][2:4], ("copilot", "sonnet"))

    def test_the_pass_is_told_the_rule(self):
        self.hand(task=SITE_ASKS[0])
        text = dp.pass_prompt(self.ctx, ASK, self.ask(), NOW)[2]
        self.assertIn("never sonnet, haiku, codex or copilot", text)

    def test_a_refusal_files_his_words_with_the_hub_and_the_ask_does_not_go_back_to_pending(self):
        self.host.start_ok = (False, REFUSAL)
        ok, msg = self.go(SITE_ASKS[0])
        self.assertFalse(ok)
        self.assertIn("refused", msg)
        self.assertEqual(self.ask()["status"], "fallback-failed")
        self.assertIn(ASK, self.ctx.inbox_path.read_text())
        self.assertIn("runs only on Fable or Opus", self.host.chats[-1])
        n = len(self.host.started)
        self.assertEqual(dp.fallback_due(self.ctx, self.host, NOW + 3600), [])
        self.assertEqual(len(self.host.started), n)  # not started again by the script fallback

    def test_any_other_failure_still_goes_back_to_pending(self):
        self.host.start_ok = (False, "tmux is down")
        ok, _ = self.go(SITE_ASKS[0])
        self.assertFalse(ok)
        self.assertEqual(self.ask()["status"], "pending")


class TheWaitingRoom(tp.PassBase):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.room = self.ctx.lanes_dir / "queue"
        self.room.mkdir(parents=True)
        self.host.proj = 50  # the week is on pace

    def entry(self, name, brief):
        (self.room / (name + ".md")).write_text("dir: %s\nmodel: sonnet\napproved: x\n\n# %s\n\n%s\n" % (self.root / "own-app", name, brief))

    def test_a_site_entry_asking_for_sonnet_starts_on_opus(self):
        self.entry("fd-site", "Fit the Tigard site model to the porch.")
        dp.room_due(self.ctx, self.host, NOW)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))

    def test_a_plain_entry_keeps_its_model(self):
        self.entry("fd-plain", "Count the open issues in runit.")
        dp.room_due(self.ctx, self.host, NOW)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "sonnet"))


class TheHost(unittest.TestCase):
    def host(self, result):
        seen = {}

        class H(dp.LaneHost):
            def _run(self, argv, timeout, cwd=None, stdin=None, env=None):
                seen["argv"] = argv
                return result
        return H(), seen

    def prompt(self, text):
        p = Path(td.fresh_dir(self)) / "prompt.md"
        p.write_text(text)
        return p

    def ok(self):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_a_site_lane_is_started_at_effort_high(self):
        host, seen = self.host(self.ok())
        host.start_lane("oc-x", "/tmp", "claude", "opus", self.prompt("Fit the Tigard site model."))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertEqual(argv[-2], "opus")

    def test_a_plain_lane_gets_no_effort_flag(self):
        host, seen = self.host(self.ok())
        host.start_lane("oc-x", "/tmp", "claude", "sonnet", self.prompt("Count the issues."))
        self.assertNotIn("--effort", seen["argv"])

    def test_a_claude_start_says_why_it_is_not_on_agy(self):
        # lane.sh sends his own-repo code lanes to agy unless --claude-because is given (22 Oct); the dispatcher has already routed here
        host, seen = self.host(self.ok())
        host.start_lane("oc-x", "/tmp", "claude", "sonnet", self.prompt("Count the issues."))
        argv = seen["argv"]
        self.assertIn("agy out of usage", argv[argv.index("--claude-because") + 1])
        self.assertEqual(argv[-4:], ["oc-x", "/tmp", "sonnet", argv[-1]])

    def test_a_docker_prompt_gives_docker_as_the_reason(self):
        host, seen = self.host(self.ok())
        host.start_lane("oc-x", "/tmp", "claude", "sonnet", self.prompt("Run docker compose up and check the logs."))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--claude-because") + 1], "needs Docker or wsl.exe")

    def test_the_reason_is_not_passed_to_other_engines(self):
        for engine in ("agy", "codex", "copilot"):
            host, seen = self.host(self.ok())
            host.start_lane("oc-x", "/tmp", engine, "gemini-3.8-flash-high", self.prompt("Count the issues."))
            self.assertNotIn("--claude-because", seen["argv"], engine)

    def test_lane_sh_refusing_the_tier_comes_back_as_that_line_not_the_last_stderr_line(self):
        err = types.SimpleNamespace(returncode=1, stdout="", stderr=REFUSAL + "\nsome later line\n")
        host, _ = self.host(err)
        ok, msg = host.start_lane("oc-x", "/tmp", "claude", "sonnet", self.prompt("site-model"))
        self.assertFalse(ok)
        self.assertTrue(msg.startswith(dp.TIER_REFUSED), msg)


if __name__ == "__main__":
    unittest.main()
