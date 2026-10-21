#!/usr/bin/env python3
"""Mail is execution, never judgment (his rule, 21 Oct 7:15 PM: "why are you burning Fable on emails!"; one day of mail lanes on Fable
and Opus was $85 of $303). Every way the dispatcher starts a lane keeps a mail lane on Sonnet: the chat start, the dispatch pass, a
restart and the waiting room. lane.sh refuses Opus and Fable for mail too (test_lane_model_guard.sh). The PC and OpenClaw are fakes."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_guard  # noqa: E402,F401  first: nothing here may write the live ~/.claude/state
import openclaw_dispatch as dp  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
import test_openclaw_pass as tp  # noqa: E402

NOW = td.NOW
ASK = tp.ASK
MAIL_TASKS = ("draft a reply to the client's email about the invoice", "proofread the email to the client", "answer the email thread from Example Brewing")
ABSORB = "# Absorb lane: Example Brewing wrote\n\nNew Gmail message IDs from this client: 18c2\n"


class TheRule(unittest.TestCase):
    def test_a_mail_lane_never_starts_on_opus_or_fable(self):
        for model in ("opus", "fable"):
            self.assertEqual(dp.mail_tier("acme-mail", "anything", "claude", model), "sonnet", model)
            self.assertEqual(dp.mail_tier("oc-x", ABSORB, "claude", model), "sonnet", model)
            for task in MAIL_TASKS:
                self.assertEqual(dp.mail_tier("oc-x", task, "claude", model), "sonnet", task)
            self.assertEqual(dp.mail_tier("oc-x", "look at the files", "claude", model, mail=True), "sonnet", model)

    def test_everything_else_is_left_alone(self):
        self.assertEqual(dp.mail_tier("oc-x", "fix the lint errors", "claude", "opus"), "opus")
        self.assertEqual(dp.mail_tier("oc-x", "add an email field to the signup form", "claude", "opus"), "opus")
        self.assertEqual(dp.mail_tier("acme-mail", "x", "claude", "sonnet"), "sonnet")
        self.assertEqual(dp.mail_tier("acme-mail", "x", "claude", "haiku"), "haiku")
        self.assertEqual(dp.mail_tier("acme-mail", "x", "codex", "opus"), "opus")  # not a Claude lane: lane.sh's tier does not apply

    def test_site_model_work_keeps_its_own_rule(self):
        self.assertEqual(dp.mail_tier("acme-site-mail", "Fit data/sites/example and email nobody", "claude", "opus"), "opus")
        self.assertEqual(dp.prompt_site_tier(self._file("Fit the Example site model, draft an email when done."), "claude", "fable", "x", True)[:2],
                         ("claude", "fable"))

    def _file(self, text):
        d = Path(td.fresh_dir(self))
        p = d / "prompt.md"
        p.write_text(text)
        return p

    def test_choose_engine_gives_client_email_sonnet_and_client_judgment_opus(self):
        for task in MAIL_TASKS:
            self.assertEqual(dp.choose_engine("/none", "Example Brewing", task, mail=True)[:2], ("claude", "sonnet"), task)
            self.assertEqual(dp.choose_engine("/none", "Example Brewing", task)[:2], ("claude", "sonnet"), task)
        self.assertEqual(dp.choose_engine("/none", "Example Brewing", "write the proposal for the new contract")[:2], ("claude", "opus"))


class ChatStart(td.Base):
    def test_an_email_ask_in_a_client_repo_starts_on_sonnet(self):
        for i, task in enumerate(MAIL_TASKS):
            self.start("client-app", task, NOW + i * 60)
            self.assertEqual(self.host.started[-1][2:4], ("claude", "sonnet"), task)

    def test_a_client_judgment_ask_still_gets_opus(self):
        self.start("client-app", "price the proposal and check the contract terms")
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))


class ThePass(tp.PassBase):
    def setUp(self):
        super().setUp()
        self.lane = "oc-client-app-aaa"

    def test_the_pass_is_told_email_is_sonnet_and_never_opus_or_fable(self):
        self.hand(repo="client-app", task=MAIL_TASKS[0])
        text = dp.pass_prompt(self.ctx, ASK, self.ask(), NOW)[2]
        self.assertIn("never opus or fable", text)
        self.assertNotIn("opus only for client judgment or email", text)

    def prompt(self):
        return self.ctx.lanes_dir / ("%s-%s" % (time.strftime("%F", time.localtime(NOW)), self.lane)) / "prompt.md"

    def go(self, task, model, **kw):
        self.hand(repo="client-app", task=task)
        dp.write_prompt(self.ctx, self.lane, "<<<TASK\n%s\nTASK>>>\n" % task, NOW)
        self.index_row(self.lane, repo="client-app", begin_for=False)
        a = dict(ask_id=ASK, lane_name=self.lane, dirpath=self.root / "client-app", model=model, prompt_path=self.prompt(), now=NOW + 5)
        a.update(kw)
        return dp.start_for_ask(self.ctx, self.host, **a)

    def test_the_pass_choosing_opus_or_fable_for_an_email_task_is_moved_to_sonnet(self):
        for model in ("opus", "fable"):
            self.setUp()
            ok, msg = self.go(MAIL_TASKS[0], model)
            self.assertTrue(ok, msg)
            self.assertEqual(self.host.started[-1][2:4], ("claude", "sonnet"), model)

    def test_the_pass_choosing_opus_for_judgment_is_left_alone(self):
        ok, msg = self.go("price the proposal and check the contract terms", "opus")
        self.assertTrue(ok, msg)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))


class Restart(tp.PassBase):
    def row(self, name, model, prompt):
        day = time.strftime("%F", time.localtime(NOW))
        old = self.tmp / "lanes" / ("%s-%s" % (day, name))
        old.mkdir(parents=True)
        (old / "prompt.md").write_text(prompt)
        (old / "report.md").write_text("Done so far. 3:20 PM\n")
        row = [day, name, str(self.root / "client-app"), model, "subscription", str(old / "prompt.md"), str(old / "report.md"), "sid", "", "", "on"]
        with open(self.ctx.index_path, "a") as f:
            f.write("\t".join(row) + "\n")

    def test_a_mail_lane_recorded_on_fable_restarts_on_sonnet(self):
        self.row("acme-mail", "fable", ABSORB)
        dp.restart_lane(self.ctx, self.host, "acme-mail", "go on", NOW + 60)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "sonnet"))

    def test_a_code_lane_recorded_on_opus_keeps_it(self):
        self.row("plain", "opus", "Fix the lint errors.\n")
        dp.restart_lane(self.ctx, self.host, "plain", "go on", NOW + 60)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))


class TheWaitingRoom(tp.PassBase):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.room = self.ctx.lanes_dir / "queue"
        self.room.mkdir(parents=True)
        self.host.proj = 50

    def entry(self, name, model, brief):
        (self.room / (name + ".md")).write_text("dir: %s\nmodel: %s\napproved: x\n\n# %s\n\n%s\n" % (self.root / "client-app", model, name, brief))

    def test_a_mail_entry_asking_for_opus_or_fable_starts_on_sonnet(self):
        self.entry("fd-mail", "fable", MAIL_TASKS[0])
        dp.room_due(self.ctx, self.host, NOW)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "sonnet"))

    def test_a_plain_entry_keeps_its_model(self):
        self.entry("fd-plain", "opus", "Count the open issues in runit.")
        dp.room_due(self.ctx, self.host, NOW)
        self.assertEqual(self.host.started[-1][2:4], ("claude", "opus"))


if __name__ == "__main__":
    unittest.main()
