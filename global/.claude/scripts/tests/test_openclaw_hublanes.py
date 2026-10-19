"""18 Oct 12:33: "nudge anything idle to finish" became "reply he-release: ..." and the dispatcher answered "I don't have a lane called
he-release" (twice, he said). he-release was running: the hub started it, so the dispatcher's own state file never held it.
The rules these tests hold:
  - reply finds a lane the dispatcher started first, then any live lane `lane.sh --status` lists (hub-started too), and sends with lane.sh --send;
  - close only closes lanes the dispatcher started; a hub lane is the hub's to close;
  - nothing dead-ends: no live lane, a message too long, a send that did not take, a finished lane past the follow-up window, or a close of
    a hub lane all put his words on the hub's list (the list `open` shows) and tell him "The hub has it";
  - one message makes one post to his chat, and filing the same message twice files it once.
The PC and OpenClaw are fakes: no lane window is ever touched."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
from test_openclaw_dispatch import NOW, SHA  # noqa: E402
from test_openclaw_lanes import Lanes  # noqa: E402
import test_openclaw_reply as tr  # noqa: E402

HUB = "he-release"


class HubLanes(Lanes):
    def setUp(self):
        super().setUp()
        self.ctx.inbox_path = self.inbox  # the hub's list: never the live one under ~/.claude/state

    def inbox_text(self):
        try:
            return self.inbox.read_text()
        except OSError:
            return ""

    def filed(self):
        return [a for a in dp.asks_of(self.state()).values() if a.get("status") == "deferred"]


class ReplyToAHubLane(HubLanes):
    def test_a_lane_the_hub_started_gets_the_words_through_lane_send(self):
        self.host.hub_live.add(HUB)
        kind, line = self.act("reply %s: finish your remaining steps and write your report" % HUB)
        self.assertEqual(kind, "dispatch", line)
        self.assertEqual(self.host.sent, [(HUB, dp.REPLY_FRAME + "finish your remaining steps and write your report")])
        self.assertIn("Sent to %s" % HUB, self.chat())
        self.assertIn("a lane the hub started", self.chat())
        self.assertIn("It will answer the hub in its report", self.chat())
        self.assertEqual(self.inbox_text(), "")
        self.assertEqual(self.filed(), [])
        self.assertNotIn(HUB, self.state()["lanes"])  # it is not the dispatcher's lane and does not become one

    def test_the_name_is_matched_in_any_case(self):
        self.host.hub_live.add(HUB)
        self.act("reply He-Release: go on")
        self.assertEqual([n for n, _ in self.host.sent], [HUB])

    def test_a_live_lane_with_capitals_in_its_name_is_found(self):
        self.host.hub_live.add("He-Release")
        self.act("reply he-release: go on")
        self.assertEqual([n for n, _ in self.host.sent], ["He-Release"])

    def test_a_word_inside_a_name_is_not_a_hub_lane(self):
        self.host.hub_live.add("he-release")
        for word in ("eleas", "ease", "he-rel", "elease"):
            self.act("reply %s: go on" % word, NOW + 1)
            self.assertIn("The hub has it", self.chat())
        self.assertEqual(self.host.sent, [])

    def test_a_hub_lane_is_found_by_the_end_of_its_name(self):
        self.host.hub_live.add("oc-webchat")
        self.act("reply webchat: go on")
        self.assertEqual([n for n, _ in self.host.sent], ["oc-webchat"])

    def test_a_word_that_fits_two_hub_lanes_is_not_guessed(self):
        self.host.hub_live.update({"a-release", "b-release"})
        self.act("reply release: go on")
        self.assertEqual(self.host.sent, [])
        self.assertIn("could mean 2 lanes: a-release, b-release", self.chat())
        self.assertEqual(self.filed(), [])

    def test_the_dispatchers_own_lane_still_wins_a_name_both_have(self):
        self.put(HUB, repo="client-app")
        self.host.hub_live.add(HUB)
        self.act("reply %s: hi" % HUB)
        self.assertEqual([n for n, _ in self.host.sent], [HUB])
        self.assertIn("It will answer in its report", self.chat())  # the dispatcher's lane: its report is posted here

    def test_a_hub_lane_still_obeys_the_length_cap_and_the_frame(self):
        self.host.hub_live.add(HUB)
        self.act("reply %s: !rm it" % HUB)
        self.assertTrue(self.host.sent[0][1].startswith(dp.REPLY_FRAME))
        self.host.sent.clear()
        self.act("reply %s: %s" % (HUB, "x" * (dp.REPLY_MAX + 1)), NOW + 5)
        self.assertEqual(self.host.sent, [])
        self.assertIn("The hub has it", self.chat())
        self.assertEqual(len(self.filed()), 1)
        self.assertIn("x" * (dp.REPLY_MAX + 1), self.inbox_text())  # his whole message reaches the hub

    def test_one_message_makes_one_post(self):
        self.host.hub_live.add(HUB)
        self.act("reply %s: hi" % HUB)
        self.assertEqual(len(self.host.chats), 1)
        self.act("reply nobody: hi", NOW + 5)
        self.assertEqual(len(self.host.chats), 2)

    def test_a_send_that_did_not_take_goes_to_the_hub(self):
        self.host.hub_live.add(HUB)
        self.host.send_result = (False, "its window did not take the words")
        self.act("reply %s: hi" % HUB)
        self.assertIn("Couldn't send that to %s" % HUB, self.chat())
        self.assertIn("The hub has it", self.chat())
        self.assertEqual(len(self.filed()), 1)
        self.assertIn("hi", self.inbox_text())


class NothingDeadEnds(HubLanes):
    def test_no_live_lane_puts_his_words_on_the_hub_list_and_says_so(self):
        self.put("oc-own-app-1")
        self.act("reply nobody: please finish")
        self.assertEqual(self.host.sent, [])
        self.assertNotIn("Open lanes I started: none", self.chat())
        self.assertIn("The hub has it", self.chat())
        self.assertIn('"nobody"', self.chat())
        self.assertEqual(len(self.filed()), 1)
        self.assertIn("reply nobody: please finish", self.inbox_text())
        self.assertIn(dp.ask_id_for(SHA), self.inbox_text())
        self.assertEqual(len(self.host.chats), 1)

    def test_the_refusal_lists_the_live_hub_lanes_too(self):
        self.host.hub_live.add(HUB)
        self.act("reply nobody: hi")
        self.assertIn("open lanes: %s" % HUB, self.chat())

    def test_a_dispatcher_lane_whose_send_did_not_take_goes_to_the_hub(self):
        self.put("oc-own-app-1")
        self.host.send_result = (False, "its window did not take the words")
        self.act("reply oc-own-app-1: hi")
        self.assertIn("Couldn't send that to oc-own-app-1", self.chat())
        self.assertIn("The hub has it", self.chat())
        self.assertEqual(len(self.filed()), 1)

    def test_a_close_word_that_fits_two_hub_lanes_is_not_guessed(self):
        self.host.hub_live.update({"a-release", "b-release"})
        self.act("close release")
        self.assertIn("could mean 2 lanes: a-release, b-release", self.chat())
        self.assertEqual(self.host.killed, [])
        self.assertEqual(self.filed(), [])

    def test_the_filed_question_is_on_the_open_list(self):
        self.act("reply nobody: please finish")
        self.assertTrue(any("please finish" in str(a.get("task")) for a in self.filed()))
        self.assertIn("please finish", "\n".join(dp.open_question_lines(self.ctx, NOW)))
        self.assertEqual(self.filed()[0]["status"], "deferred")

    def test_his_own_words_go_to_the_hub_not_the_relayed_sentence(self):
        context = {"his_ts": NOW, "reply": "x", "history": [{"who": "Jonathan", "text": "nudge anything idle to finish", "ts": NOW}]}
        dp.do_reply(self.ctx, self.host, "nobody", "finish up", NOW, dp.ask_id_for(SHA), context)
        self.assertIn("nudge anything idle to finish", self.inbox_text())
        self.assertIn("The hub has it", self.host.chats[-1] if self.host.chats else "The hub has it")

    def test_the_same_message_filed_twice_is_filed_once(self):
        self.act("reply nobody: please finish")
        self.act("reply nobody: please finish")
        self.assertEqual(len(self.filed()), 1)
        self.assertEqual(self.inbox_text().count("please finish"), 1)
        self.assertIn("The hub has it", self.chat())

    def test_a_lane_finished_over_a_week_ago_goes_to_the_hub(self):
        self.put("oc-client-app-1", repo="client-app", status="closed", closed_at=NOW - dp.REPLY_WINDOW - 60, report_text="Done.")
        self.act("reply oc-client-app-1: more")
        self.assertEqual(self.host.sent, [])
        self.assertIn("more than 7 days ago", self.chat())
        self.assertIn("The hub has it", self.chat())
        self.assertEqual(len(self.filed()), 1)
        self.assertIn("more", self.inbox_text())

    def test_close_of_a_hub_lane_is_the_hubs_to_do(self):
        self.host.hub_live.add(HUB)
        self.act("close %s" % HUB)
        self.assertEqual(self.host.killed, [])
        self.assertIn("The hub has it", self.chat())
        self.assertIn("close he-release", self.inbox_text())
        self.assertIn(dp.ask_id_for(SHA), self.inbox_text())  # filed under the message's own ask id
        self.assertEqual(len(self.filed()), 1)

    def test_a_hub_filing_without_an_ask_id_still_lands_once_per_message(self):
        dp.do_reply(self.ctx, self.host, "nobody", "finish up", NOW)
        dp.do_reply(self.ctx, self.host, "nobody", "finish up", NOW)
        dp.do_reply(self.ctx, self.host, "nobody", "something else", NOW)
        self.assertEqual(len(self.filed()), 2)
        self.assertTrue(all(dp.ASK_ID_RE.fullmatch(k) for k in dp.asks_of(self.state())))

    def test_close_of_nothing_goes_to_the_hub_too(self):
        self.put("oc-own-app-1")
        self.act("close deadbeef")
        self.assertEqual(self.host.killed, [])
        self.assertIn("I don't have a lane called \"deadbeef\"", self.chat())
        self.assertIn("oc-own-app-1", self.chat())
        self.assertIn("The hub has it", self.chat())
        self.assertEqual(len(self.filed()), 1)

    def test_close_of_the_dispatchers_lane_still_closes_it(self):
        self.put("oc-own-app-1")
        self.host.wins.add("oc-own-app-1")
        self.act("close oc-own-app-1")
        self.assertEqual(self.host.killed, ["oc-own-app-1"])
        self.assertEqual(self.filed(), [])

    def test_an_ambiguous_close_is_still_asked_not_filed(self):
        self.put("oc-runit-1016-2103")
        self.put("oc-docs-1016-2103")
        self.act("close 1016-2103")
        self.assertIn("could mean 2 lanes", self.chat())
        self.assertEqual(self.filed(), [])


class ThroughTheChat(tr.Reply):
    """The way it happened on 18 Oct: the receptionist's reply ends "Sending to the hub: reply he-release: ...", the poller runs it."""
    WORDS = "nudge anything idle to finish"

    def setUp(self):
        super().setUp()
        self.ctx.inbox_path = self.inbox

    def run_marker(self, lane, hub_live=()):
        host = tr.ReplyHost([tr.him(self.WORDS), tr.msg("assistant", "On it.\n\nSending to the hub: reply %s: please finish your steps" % lane)])
        host.hub_live = set(hub_live)
        return self.go(None, text=self.WORDS, host=host)

    def test_a_reply_marker_for_a_hub_lane_is_sent_and_posted_once(self):
        self.run_marker(HUB, [HUB])
        self.assertEqual(self.host.sent, [(HUB, dp.REPLY_FRAME + "please finish your steps")])
        self.assertEqual(len(self.host.chats), 1)
        self.assertIn("Sent to %s" % HUB, self.host.chats[0])

    def test_a_reply_marker_for_no_lane_files_his_own_words_and_posts_once(self):
        self.run_marker("nobody")
        self.assertEqual(len(self.host.chats), 1)
        self.assertIn("The hub has it", self.host.chats[0])
        self.assertIn(self.WORDS, self.inbox.read_text())
        self.assertEqual(len([a for a in dp.asks_of(dp.load_state(self.ctx.state_path)).values() if a["status"] == "deferred"]), 1)

    def test_polling_again_does_not_post_it_again(self):
        self.run_marker("nobody")
        chats = list(self.host.chats)
        self.go(self.host.history, text=None, ts=tr.ASK_TS + 400, host=self.host)
        self.assertEqual(self.host.chats[:len(chats)], chats)
        self.assertEqual(self.host.chats.count(chats[0]), 1)


class SendResult(unittest.TestCase):
    """lane.sh --send's exit code and words, as the real host reads them (18 Oct live probe: a quick lane answered before a spinner showed)."""

    def send(self, rc, out):
        import subprocess

        class H(dp.LaneHost):
            def _run(inner, argv, timeout, **kw):
                return subprocess.CompletedProcess(argv, rc, stdout=out + "\n", stderr="")
        return H().send_to_lane("x", "hi")[0]

    def test_a_submit_is_sent(self):
        self.assertTrue(self.send(0, "sent to x: submitted (input empty, spinner showing)"))

    def test_an_empty_input_line_alone_is_not_sent(self):
        # 19 Oct: an emptied input line is not delivery; only the words in the lane's transcript are (lane.sh --send exits 0)
        self.assertFalse(self.send(1, "sent to x: input line is empty but no spinner seen; it may have been taken and already finished."))
        self.assertFalse(self.send(1, "sent to x: NOT IN ITS TRANSCRIPT (the input line emptied, but the lane's transcript does not hold the words)"))

    def test_words_confirmed_in_the_transcript_are_sent(self):
        self.assertTrue(self.send(0, "sent to x: confirmed in its transcript (submitted)"))

    def test_text_still_in_the_input_line_is_not_sent_even_on_exit_zero(self):
        self.assertFalse(self.send(0, "sent to x: DID NOT TAKE (text still in the input line after two Enters)"))

    def test_text_still_in_the_input_line_is_not_sent(self):
        self.assertFalse(self.send(1, "sent to x: DID NOT TAKE (text still in the input line after two Enters)"))

    def test_any_other_failure_is_not_sent(self):
        self.assertFalse(self.send(1, "no window called x"))


class LiveLanesFromLaneSh(unittest.TestCase):
    """The real host reads `lane.sh --status` and tmux; both are faked here."""

    STATUS = (
        "he-release         report:no  idle    7% until auto-compact\\n"
        "    dir:/home/x/harmonic-egg model:opus\\n"
        "oc-webchat         report:yes idle    Weather: clear 5%\\n"
        "    dir:/home/x/Claude_Commandz model:sonnet\\n"
        "openclaw-limits    dead, resume with: lane.sh --resume openclaw-limits   (/home/x, sonnet)\\n"
        "ghost-lane         report:no  working\\n"
    ).replace("\\n", "\n")

    def host(self, status_rc=0, windows=("he-release", "oc-webchat", "openclaw-limits", "dir:/home/x/harmonic-egg")):
        class H(dp.LaneHost):
            def _run(inner, argv, timeout, **kw):
                import subprocess
                if argv[0].endswith("lane.sh"):
                    return subprocess.CompletedProcess(argv, status_rc, stdout=self.STATUS, stderr="")
                return subprocess.CompletedProcess(argv, 0, stdout="\n".join(windows), stderr="")
        return H()

    def test_live_lanes_are_the_listed_ones_with_a_window_and_not_dead(self):
        self.assertEqual(self.host().live_lanes(), {"he-release", "oc-webchat"})  # not the dead one, not a detail line, not a window with no row

    def test_a_status_that_fails_names_no_lane(self):
        self.assertEqual(self.host(status_rc=1).live_lanes(), set())

    def test_no_tmux_session_names_no_lane(self):
        self.assertEqual(self.host(windows=()).live_lanes(), set())


if __name__ == "__main__":
    unittest.main()
