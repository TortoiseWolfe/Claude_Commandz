#!/usr/bin/env python3
"""A message of his that did not get through reaches his chat in plain words, never as tool output.

20 Oct 2026, 12:02 AM, 12:23 AM and 12:25 AM: his chat got "Couldn't send that to nc35-upgrade: sent to nc35-upgrade: NOT IN ITS
TRANSCRIPT (the input line emptied and it was submitted, but the lane's transcript does not hold the words). The hub has it."
His words: "that's confusing and sounds contradictory, what do I care if it's in it's transcript?"

The rule these tests hold: a failed send, a failed restart or a failed start tells him what did not arrive and what happens next, in
plain words; lane.sh's own text stays in the log line and on the hub's list. Lane names are stand-ins; the lane.sh lines are the real
shapes. The PC and OpenClaw are fakes.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
from test_openclaw_hublanes import HubLanes  # noqa: E402

LANE = "site-move"
# lane.sh --send's last line, verbatim in shape, as do_reply received it at 12:02 AM
NOT_IN = ("sent to %s: NOT IN ITS TRANSCRIPT (the input line emptied and it was submitted, but the lane's transcript does not hold "
          "the words)" % LANE)
DID_NOT = "sent to %s: DID NOT TAKE (text still in the input line after two Enters)" % LANE
TOOL_WORDS = ("NOT IN ITS TRANSCRIPT", "DID NOT TAKE", "transcript", "input line", "sent to", "lane.sh", "Enters", "did not answer",
              "refusing engine", "tmux")
NEXT = "The hub has your words and will pass them on."


class Plain(HubLanes):
    def assertPlain(self, chat):
        for w in TOOL_WORDS:
            self.assertNotIn(w, chat)
        self.assertNotIn("Couldn't send that to", chat)  # "couldn't send" next to "the hub has it" read as a contradiction
        self.assertIn(NEXT, chat)  # what happens next


class TonightsCase(Plain):
    def test_the_1202_am_reply_tells_him_plainly_and_files_his_words(self):
        self.host.hub_live.add(LANE)
        self.host.send_result = (False, NOT_IN)
        kind, line = self.act("reply %s: research the nuance and elaborate" % LANE)
        chat = self.chat()
        self.assertPlain(chat)
        self.assertIn("I couldn't confirm that your message reached %s" % LANE, chat)  # what did not arrive, and to where
        self.assertEqual(len(self.host.chats), 1)
        self.assertIn("NOT IN ITS TRANSCRIPT", line)  # the tool text stays in the log line for the hub
        self.assertEqual(len(self.filed()), 1)
        self.assertIn("NOT IN ITS TRANSCRIPT", self.inbox_text())  # and on the hub's list, with the why
        self.assertIn("research the nuance and elaborate", self.inbox_text())

    def test_a_send_that_did_not_take_says_it_did_not_reach_the_lane(self):
        self.host.hub_live.add(LANE)
        self.host.send_result = (False, DID_NOT)
        self.act("reply %s: hold the run" % LANE)
        self.assertPlain(self.chat())
        self.assertIn("Your message didn't reach %s." % LANE, self.chat())

    def test_a_dispatcher_lane_says_the_same(self):
        self.put("oc-own-app-1")
        for out in (NOT_IN.replace(LANE, "oc-own-app-1"), DID_NOT.replace(LANE, "oc-own-app-1"), "lane.sh did not answer"):
            self.host.chats.clear()
            self.host.send_result = (False, out)
            self.act("reply oc-own-app-1: go on", td.NOW + len(out))
            self.assertPlain(self.chat())
            self.assertIn("oc-own-app-1", self.chat())

    def test_no_lane_sh_output_shape_reaches_his_chat(self):
        for out in (NOT_IN, DID_NOT, "lane.sh did not answer", "", None, "anything else lane.sh prints"):
            chat = dp.not_sent_text(LANE, out)
            self.assertPlain(chat)
            self.assertIn(LANE, chat)
            if out:
                self.assertNotIn(out, chat)

    def test_only_not_in_its_transcript_says_it_may_have_arrived(self):
        self.assertIn("couldn't confirm", dp.not_sent_text(LANE, NOT_IN))
        self.assertIn("didn't reach", dp.not_sent_text(LANE, DID_NOT))
        self.assertNotIn("couldn't confirm", dp.not_sent_text(LANE, DID_NOT))


class FailedStart(Plain):
    def test_a_start_lane_sh_refused_is_plain(self):
        self.ctx.hand_off_first = False  # start it here, not through the pass
        self.host.start_ok = (False, "refusing engine codex: blocked until 31 Oct")
        kind, line = self.act("start own-app: tidy the docs")
        self.assertPlain(self.host.chats[-1])
        self.assertIn("I couldn't start a lane in own-app", self.host.chats[-1])
        self.assertIn("refusing engine codex", line)

    def test_a_closed_lane_that_will_not_restart_is_plain(self):
        day = time.strftime("%F", time.localtime(td.NOW))
        old = self.tmp / "lanes" / ("%s-porch-finish" % day)
        old.mkdir(parents=True)
        (old / "prompt.md").write_text("Trace the porch columns.\n")
        (old / "report.md").write_text("Columns traced.\n")
        row = [day, "porch-finish", str(self.root / "own-app"), "sonnet", "claude", str(old / "prompt.md"), str(old / "report.md"), "sid", "", "", "on"]
        Path(self.ctx.index_path).write_text("\t".join(row) + "\n")
        self.host.start_ok = (False, "tmux is down")
        kind, line = self.act("reply porch-finish: and the steps too")
        chat = self.chat()
        self.assertPlain(chat)
        self.assertIn("porch-finish had closed and I couldn't start it again, so your message didn't reach it.", chat)
        self.assertIn("tmux is down", line)
        self.assertEqual(len(self.filed()), 1)


if __name__ == "__main__":
    unittest.main()
