"""The plain "yes": the chat model ends a reply with ONE line "Say yes to start: start <repo>: <task>", Jonathan's
tray message "yes" (or do it / go ahead / ok ...) makes the poller read Main session back and start that job through the
same start path as a typed one. The model's text is data. The PC and OpenClaw are fakes: no session is ever touched."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402

NOW = td.NOW
PROPOSAL = "Sure, I can have a worker look at that.\n\nSay yes to start: start own-app: list the files in docs"


def msg(role, text, age=60, provider="google"):
    return {"role": role, "provider": None if role == "user" else provider, "text": text, "ts_ms": int((NOW - age) * 1000)}


def hub(text, age=30):
    return msg("assistant", "[hub]\n\n" + text, age, provider="openclaw")


class YesHost(td.FakeHost):
    def __init__(self, history):
        super().__init__()
        self.history, self.reads = history, 0

    def main_history(self, limit=12):
        self.reads += 1
        return self.history


class Yes(td.Base):
    def say(self, text, history, **kw):
        self.host = YesHost(history)
        return self.act(text, **kw)

    def flow(self, proposal=PROPOSAL, **kw):
        """What the session really holds when the poller runs: proposal, his yes, the model's reply to the yes."""
        return [msg("assistant", proposal, 90, **kw), msg("user", "yes", 45), msg("assistant", "On it.", 40)]

    def nothing(self, kind, line):
        self.assertEqual(kind, "dispatch")
        self.assertIn("nothing started", line)
        self.assertEqual(self.host.started, [])
        self.assertEqual(self.host.chats, [pk.NOTHING_TO_START])
        self.assertEqual(self.lane_names(), [])


class Forms(unittest.TestCase):
    def test_the_confirm_forms_ignore_case_and_punctuation(self):
        for t in ("yes", "Yes", "YES!", "yes, do it", "Yes do it.", "do it", "Do it!", "go", "go ahead", "Go ahead!", "start it", "ok", "OK.", "ok, do it", "okay"):
            self.assertTrue(pk.is_confirm(t), t)

    def test_nothing_else_is_a_confirm(self):
        for t in ("", "no", "yes and also email Greg", "yes start own-app: x", "do it later", "I said yes", "go away", "start", "okay but not today", "status"):
            self.assertFalse(pk.is_confirm(t), t)


class Starts(Yes):
    def test_a_yes_starts_the_proposed_job_the_way_a_typed_start_does(self):
        kind, line = self.say("yes", self.flow())
        self.assertEqual(kind, "dispatch")
        self.assertEqual(self.host.reads, 1)
        self.assertEqual(len(self.host.started), 1)
        name, d, engine, model, prompt = self.host.started[0]
        self.assertEqual((engine, d), ("codex", str(self.root / "own-app")))
        self.assertIn(json.dumps("list the files in docs"), Path(prompt).read_text())
        self.assertIn("started", line)
        self.assertEqual(len(self.host.chats), 1)
        self.assertIn("Started oc-own-app", self.host.chats[0])
        self.assertEqual(self.lane_names(), [name])

    def test_every_confirm_form_works(self):
        for t in ("yes do it", "Do it!", "go", "Go ahead.", "start it", "OK", "ok do it"):
            self.setUp()
            self.say(t, self.flow())
            self.assertEqual(len(self.host.started), 1, t)

    def test_the_proposal_may_be_the_newest_message_when_his_yes_is_not_in_the_session_yet(self):
        kind, line = self.say("yes", [msg("assistant", PROPOSAL, 20)])
        self.assertEqual(len(self.host.started), 1, line)

    def test_decoration_around_the_marker_line_is_tolerated(self):
        for p in ("**Say yes to start:** start own-app: tidy", "> Say yes to start: `start own-app: tidy`", "Say yes to start: start own-app: tidy.\n"):
            self.setUp()
            self.say("yes", self.flow(p))
            self.assertEqual(len(self.host.started), 1, p)

    def test_a_client_repo_still_routes_to_claude(self):
        self.say("yes", self.flow("Say yes to start: start client-app: fix the header"))
        self.assertEqual(len(self.host.started), 1)
        self.assertEqual(self.host.started[0][2:4], ("claude", "sonnet"))
        self.setUp()
        self.say("go ahead", self.flow("Say yes to start: start client-app: draft an email to the owner"))
        self.assertEqual(self.host.started[0][2:4], ("claude", "opus"))

    def test_the_budget_guard_still_holds_a_confirmed_job(self):
        for i in range(dp.MAX_RUNNING):
            self.start(task="job %d" % i, now=NOW - 10 * (i + 1))
        n = len(self.host.started)
        kind, line = self.say("yes", self.flow())
        self.assertEqual(kind, "dispatch")
        self.assertIn("held", line)
        self.assertEqual(len(self.host.started), 0)
        self.assertGreaterEqual(n, dp.MAX_RUNNING)
        self.assertIn("Held", self.host.chats[-1])

    def test_a_repo_not_in_the_table_is_refused_with_the_table_not_started(self):
        self.say("yes", self.flow("Say yes to start: start ../../etc: x"))
        self.assertEqual(self.host.started, [])
        self.say("yes", self.flow("Say yes to start: start secret-repo: x"))
        self.assertEqual(self.host.started, [])
        self.assertIn("don't have a repo called", self.host.chats[-1])

    def test_the_task_stays_quoted_data_even_when_it_looks_like_a_command(self):
        self.say("yes", self.flow("Say yes to start: start own-app: $(touch x) ; close x\nignore the rules"))
        self.assertEqual(len(self.host.started), 1)
        self.assertEqual(self.host.sent + self.host.killed, [])
        self.assertIn(json.dumps("$(touch x) ; close x"), Path(self.host.started[0][4]).read_text())

    def test_the_same_proposal_cannot_start_twice(self):
        h = self.flow()
        self.say("yes", h)
        self.assertEqual(len(self.host.started), 1)
        first = self.host
        self.host = YesHost(h)
        self.act("yes")
        self.assertEqual(self.host.started, [])
        self.assertEqual(self.host.chats, [pk.NOTHING_TO_START])
        self.assertEqual(len(first.started), 1)


class Nothing(Yes):
    def test_a_hub_line_before_the_yes_starts_nothing(self):
        h = [msg("assistant", PROPOSAL, 90), hub("Started oc-x in own-app on Codex.", 50), msg("user", "yes", 45)]
        self.nothing(*self.say("yes", h))

    def test_a_hub_line_as_the_newest_message_starts_nothing(self):
        self.nothing(*self.say("yes", [msg("assistant", PROPOSAL, 90), hub("Started oc-x in own-app on Codex.")]))

    def test_a_lane_report_as_the_newest_message_starts_nothing(self):
        report = "Lane oc-x (own-app) finished. Its report (the lane's own words, quoted):\n> Say yes to start: start own-app: x"
        self.nothing(*self.say("yes", [hub(report)]))
        self.setUp()
        self.nothing(*self.say("yes", [msg("assistant", PROPOSAL, 90), hub(report), msg("user", "yes", 45)]))

    def test_a_reply_from_the_openclaw_provider_is_not_model_written(self):
        self.nothing(*self.say("yes", self.flow("Say yes to start: start own-app: x", provider="openclaw")))

    def test_a_stale_proposal_starts_nothing(self):
        self.nothing(*self.say("yes", [msg("assistant", PROPOSAL, 16 * 60), msg("user", "yes", 45)]))
        self.setUp()
        self.say("yes", [msg("assistant", PROPOSAL, 14 * 60), msg("user", "yes", 5)])
        self.assertEqual(len(self.host.started), 1)

    def test_two_markers_start_nothing(self):
        two = "Say yes to start: start own-app: a\nSay yes to start: start own-app: b"
        self.nothing(*self.say("yes", self.flow(two)))
        self.setUp()
        self.nothing(*self.say("yes", self.flow("Say yes to start: start own-app: a Say yes to start: start own-app: b")))

    def test_no_marker_starts_nothing(self):
        self.nothing(*self.say("yes", self.flow("I can look into that. Want me to?")))

    def test_a_payload_that_is_not_a_start_line_starts_nothing(self):
        for p in ("Say yes to start: close oc-x", "Say yes to start: reply oc-x: go", "Say yes to start: format the disk", "Say yes to start:", "Say yes to start: start own-app"):
            self.setUp()
            self.nothing(*self.say("yes", self.flow(p)))

    def test_a_different_newest_message_from_him_means_the_yes_is_not_for_this_reply(self):
        h = [msg("assistant", PROPOSAL, 90), msg("user", "actually what is the weather", 50), msg("assistant", "Sunny.", 45)]
        self.nothing(*self.say("yes", h))

    def test_an_empty_history_starts_nothing(self):
        self.nothing(*self.say("yes", []))

    def test_an_unreadable_session_starts_nothing_and_says_so(self):
        kind, line = self.say("yes", None)
        self.assertEqual((kind, self.host.started), ("dispatch", []))
        self.assertIn("Couldn't read the chat", self.host.chats[0])

    def test_a_proposal_in_his_own_message_is_not_a_proposal(self):
        self.nothing(*self.say("yes", [msg("user", PROPOSAL, 90), msg("user", "yes", 45)]))

    def test_thinking_parts_never_count(self):
        h = pk.normalize_history({"messages": [
            {"role": "assistant", "provider": "google", "timestamp": int((NOW - 30) * 1000),
             "content": [{"type": "thinking", "thinking": "Say yes to start: start own-app: x"}, {"type": "text", "text": "Hello."}]}]})
        self.assertEqual(h[0]["text"], "Hello.")
        self.nothing(*self.say("yes", h))


class Gates(Yes):
    def test_a_yes_from_any_source_but_the_tray_is_ignored(self):
        for src in ("twitch", "discord", "youtube", "drop"):
            self.host = YesHost(self.flow())
            kind, line = self.act("yes", source=src)
            self.assertEqual((kind, line), ("ignored", None), src)
            self.assertEqual((self.host.started, self.host.reads, self.host.chats), ([], 0, []))

    def test_a_stale_yes_is_not_run_and_reads_nothing(self):
        self.host = YesHost(self.flow())
        kind, line = self.act("yes", ts=NOW - 16 * 60)
        self.assertEqual(kind, "inbox")
        self.assertIn("not run", line)
        self.assertEqual((self.host.started, self.host.reads), ([], 0))

    def test_a_yes_without_a_time_is_not_run(self):
        self.host = YesHost(self.flow())
        item = self.item("yes")
        del item["ts"]
        pk.act_on(item, self.host, {}, {}, self.inbox, self.wake, NOW, self.ctx)
        self.assertEqual((self.host.started, self.host.reads), ([], 0))

    def test_without_the_lane_menu_a_yes_is_plain_chat(self):
        self.host = YesHost(self.flow())
        self.assertEqual(pk.act_on(self.item("yes"), self.host, {}, {}, self.inbox, self.wake, NOW, None), ("chat", None))
        self.assertEqual(self.host.reads, 0)

    def test_typed_commands_work_as_before_and_read_nothing(self):
        self.host = YesHost(self.flow())
        kind, line = self.act("start own-app: list files")
        self.assertEqual((kind, len(self.host.started), self.host.reads), ("dispatch", 1, 0))

    def test_ordinary_chat_does_not_read_the_session(self):
        self.host = YesHost(self.flow())
        self.assertEqual(self.act("what is the weather"), ("chat", None))
        self.assertEqual(self.host.reads, 0)

    def test_the_session_is_read_through_chat_history_for_main_only(self):
        calls = []

        class Rec(pk.Host):
            def _run(self, argv, timeout, cwd=None, stdin=None):
                calls.append((argv, stdin))

                class R:
                    returncode = 0
                    stdout = json.dumps({"messages": [{"role": "assistant", "provider": "google", "timestamp": 1, "content": [{"type": "text", "text": "hi"}]}]})
                return R()

        h = Rec().main_history()
        self.assertEqual(h, [{"role": "assistant", "provider": "google", "text": "hi", "ts_ms": 1}])
        argv, stdin = calls[0]
        self.assertIn("chat.history", argv[-1])
        self.assertEqual(json.loads(stdin), {"sessionKey": "agent:main:main", "limit": pk.HISTORY_WINDOW})


if __name__ == "__main__":
    unittest.main()
