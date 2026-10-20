#!/usr/bin/env python3
"""20 Oct 12:02-12:08 AM, replayed. He had read the 11:39 PM lane report (three posts). At 12:02 AM he asked a question that quoted
it. The front desk echoed his words back ("asked Claude: ..."), so the whole report came back into his outbox as three new posts,
then a "went on the hub's list" note, then the hub's answer, last. The receptionist, who could not see the queue, told him "last
item" while the post showing ended "(2 more waiting. Say next ...)". His words: "is it the last item or you need me to say next,
that confusing as fuck"; "I'm frustrated with it telling me to say next n many times just to say it's clueless about what's
really in the ques". The rules:
  1. nothing echoes his own message, and a post that is mostly what he was already shown is not queued;
  2. the hub's answer to his ask goes ahead of the older posts still waiting;
  3. the receptionist reads OUTBOX.md, written from the queue, before it says "say next", a count or "last item"; with nothing
     waiting it never asks for next; the hub's own words never state a count.
Names here are invented."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_dispatch as dp  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
from test_openclaw_queue import QHost  # noqa: E402

NOW = td.NOW
HOOK = Path(__file__).resolve().parent.parent / "openclaw_ask_hook"

# the 11:39 PM report, long enough to go out as more than one post
REPORT = ("### site-move\n\n"
          "The old server's app store was switched off for the move: the calendar, the forms and the photo viewer apps stay off "
          "until the new server has them installed.\n\n"
          "- **Why off:** the upgrade refuses to run while apps that are not ready for the new version are switched on.\n"
          "- **Which ones:** calendar, forms, photo viewer, the mail app and two themes nobody has opened since spring.\n"
          "- **What comes back:** each app is switched on again once its new version is out, checked one at a time on staging.\n\n"
          "---\n\n"
          "**Staging proof:** the upgrade ran on staging at 10:52 PM and every page loaded, the logins worked and the files synced "
          "both ways. The two themes were removed rather than kept, since nobody uses them and they block the next version too.\n\n"
          "---\n\n"
          "**Production:** waits for the Tuesday evening window. Nothing on production has changed tonight; the backup from 9:00 PM "
          "is the one the move restores from if anything goes wrong.")
QUESTION = ("who asked for these to be switched off? > ### site-move > The old server's app store was switched off for the move: "
            "the calendar, the forms and the photo viewer apps stay off until the new server has them installed.")
ANSWER = "Nobody asked: the upgrade itself needs them off. They come back one at a time once their new versions are out."
OTHER = "### lane-b\n\nThe porch model's roof now matches the photo within two inches at every corner."


class Replay(td.Base):
    def setUp(self):
        super().setUp()
        self.box = self.tmp / "chat-queue.json"
        self.host = QHost(self.box)

    def items(self):
        return dp.queue_load(self.box)["items"]

    record_question = td.ReceptionistQueue.record_question

    def asked(self, now=NOW):
        """His 12:02 AM question, handed to Claude's quick check as it was that night."""
        self.ctx.hand_off_first = True
        with mock.patch.object(dp, "pass_kind", lambda text: "facts"):
            return dp.ask_question(self.ctx, self.host, "oc-ce1095ad", QUESTION, now, {"history": [{"who": "Jonathan", "text": QUESTION}]})

    def he_read_the_report(self):
        """The report's posts, each shown and moved past; then one hub post showing (the 11:58 PM one)."""
        dp.queue_add(self.box, REPORT, NOW - 1500)
        dp.queue_pump(self.box, self.host.inject_now, NOW - 1500)
        pieces = len(dp.queue_pieces(REPORT))
        self.assertGreater(pieces, 1)
        for n in range(pieces):
            dp.queue_next(self.box, self.host.inject_now, NOW - 1400 + 120 * n, "next")
        self.assertEqual(len(dp.queue_load(self.box)["done"]), pieces)
        dp.queue_add(self.box, "The calendar sync lane is merged and its tests pass.", NOW - 300)
        dp.queue_pump(self.box, self.host.inject_now, NOW - 300)
        self.assertEqual([bool(i["shown"]) for i in self.items()], [True])

    # 1 ----------------------------------------------------------------------------------------------------------------
    def test_his_question_quoting_the_report_puts_nothing_in_his_outbox(self):
        self.he_read_the_report()
        line, chat = self.asked()
        self.assertIn("handed to Claude", line)
        self.assertIsNone(chat)
        self.assertEqual(len(self.items()), 1, "only the post that was already showing")

    def test_the_old_echo_of_his_words_and_the_report_he_read_are_not_queued_again(self):
        self.he_read_the_report()
        echo = "asked Claude: " + " ".join(("[hub] " + REPORT).split())
        self.assertIsNone(dp.queue_add(self.box, echo, NOW))
        self.assertIsNone(dp.queue_add(self.box, "[hub] " + REPORT, NOW))
        for piece in dp.queue_pieces(REPORT):  # piece by piece, with the old "(k of n)" marks and tails
            self.assertIsNone(dp.queue_add(self.box, "(1 of 3) " + piece + "\n\n(2 more waiting. Say next for the next one.)", NOW))
        self.assertEqual(len(self.items()), 1)

    def test_a_new_post_that_quotes_one_line_of_the_report_is_still_queued(self):
        self.he_read_the_report()
        new = ("The mail app is back on staging; the other apps wait for their new versions. Quoted from the report: "
               "**Why off:** the upgrade refuses to run while apps that are not ready for the new version are switched on. "
               "The calendar is next, on Wednesday, and the forms after it once the forms app ships its new version.")
        self.assertIsNotNone(dp.queue_add(self.box, new, NOW))

    def test_the_hub_list_note_is_never_queued(self):
        self.assertIsNone(dp.queue_add(self.box, "1 of your messages went on the hub's list instead of the usual way (no reply "
                                                  "to it could be found). The hub has your words.", NOW))
        self.assertEqual(self.items(), [])

    # 2 ----------------------------------------------------------------------------------------------------------------
    def test_the_hubs_answer_goes_ahead_of_older_waiting_posts(self):
        self.he_read_the_report()
        dp.queue_add(self.box, OTHER, NOW + 10)
        self.record_question("oc-ce1095ad", "pending", NOW)
        ok, msg = dp.post_ask(self.ctx, self.host, "oc-ce1095ad", ANSWER, NOW + 60)
        self.assertTrue(ok, msg)
        texts = [i["text"] for i in self.items()]
        self.assertEqual(texts[1:], [ANSWER, OTHER], "the answer is next, the older lane report after it")
        dp.queue_next(self.box, self.host.inject_now, NOW + 120, "next")
        self.assertEqual(self.host.shown[-1], ANSWER + dp.queue_tail(1))

    def test_the_report_as_he_copied_it_from_the_chat_is_not_queued_again(self):
        # the chat shows markdown rendered: what he copies has no ** or ` marks, and no list dashes
        self.he_read_the_report()
        copied = " ".join(REPORT.replace("**", "").replace("- ", "").replace("---", "").split())
        self.assertIsNone(dp.queue_add(self.box, "asked Claude: " + copied, NOW))

    def test_say_puts_the_answer_ahead_of_older_waiting_posts(self):
        self.he_read_the_report()
        dp.queue_add(self.box, OTHER, NOW + 10)
        self.record_question("oc-ce1095ad", "pending", NOW)
        ok, msg = dp.say(self.ctx, self.host, "oc-ce1095ad", ANSWER, NOW + 60)
        self.assertTrue(ok, msg)
        self.assertEqual([i["text"] for i in self.items()][1:], [ANSWER, OTHER])

    def test_two_answers_keep_their_order_and_never_jump_the_post_showing(self):
        self.he_read_the_report()
        dp.queue_add(self.box, OTHER, NOW + 10)
        dp.post_answer(self.host, "First answer.")
        dp.post_answer(self.host, "Second answer.")
        texts = [i["text"] for i in self.items()]
        self.assertEqual(texts, ["The calendar sync lane is merged and its tests pass.", "First answer.", "Second answer.", OTHER])

    def test_an_answer_into_an_empty_outbox_shows_at_once(self):
        dp.post_answer(self.host, ANSWER)
        self.assertEqual(self.host.shown, [ANSWER + dp.queue_tail(0)])

    def test_an_answer_he_asked_for_is_never_dropped_even_when_it_repeats_the_report(self):
        self.he_read_the_report()
        dp.post_answer(self.host, REPORT.split("---")[0])
        self.assertEqual(len(self.items()), 2)

    # 3 ----------------------------------------------------------------------------------------------------------------
    def test_outbox_md_tells_the_receptionist_the_true_count(self):
        self.he_read_the_report()
        dp.queue_add(self.box, OTHER, NOW + 10)
        dp.post_answer(self.host, ANSWER)
        view = dp.outbox_view(self.box, NOW + 70)
        self.assertIn("Showing now: The calendar sync lane is merged", view)
        self.assertIn("Waiting, not shown yet: 2", view)
        self.assertIn("1. Nobody asked", view)
        self.assertIn("2. lane-b", view)
        self.assertIn("What you may say: 2 posts wait.", view)
        self.assertNotIn("Never ask him to say next", view)

    def test_with_nothing_waiting_outbox_md_forbids_asking_for_next(self):
        self.he_read_the_report()
        view = dp.outbox_view(self.box, NOW)
        self.assertIn("Waiting, not shown yet: 0", view)
        self.assertIn("nothing else is waiting (the post showing is the last one). Never ask him to say next.", view)
        dp.queue_next(self.box, self.host.inject_now, NOW + 5, "next")
        view = dp.outbox_view(self.box, NOW + 6)
        self.assertIn("Showing now: nothing", view)
        self.assertIn("What you may say: nothing else is waiting. Never ask him to say next.", view)

    def test_tonights_case_from_start_to_end_the_count_always_matches_the_queue(self):
        self.he_read_the_report()
        self.assertIn("handed to Claude", self.asked()[0])
        self.assertTrue(dp.post_ask(self.ctx, self.host, "oc-ce1095ad", ANSWER, NOW + 120)[0])
        self.assertIn("Waiting, not shown yet: 1", dp.outbox_view(self.box, NOW + 121))
        dp.queue_next(self.box, self.host.inject_now, NOW + 240, "next")  # he says next: the answer, not the report again
        self.assertEqual(self.host.shown[-1], ANSWER + "\n\n(Nothing else waiting.)")
        view = dp.outbox_view(self.box, NOW + 241)
        self.assertIn("Waiting, not shown yet: 0", view)
        self.assertIn("Never ask him to say next", view)

    def test_the_view_lists_ten_and_counts_the_rest(self):
        for n in range(13):
            dp.queue_add(self.box, "Post number %d about lane %d." % (n, n), NOW + n)
        view = dp.outbox_view(self.box, NOW + 20)
        self.assertIn("Waiting, not shown yet: 13", view)
        self.assertIn("10. Post number 9", view)
        self.assertNotIn("11. ", view)
        self.assertIn("... and 3 more", view)

    def test_the_mirror_writes_only_when_the_queue_changed_and_only_for_the_live_queue(self):
        pushed = []
        push = lambda text: pushed.append(text) or True  # noqa: E731
        self.assertEqual(dp.outbox_mirror(self.box, push, NOW), "not live")
        self.assertEqual(dp.outbox_mirror(None, push, NOW), "not live")
        self.assertEqual(pushed, [])
        live = self.tmp / "openclaw-chat-queue.json"
        with mock.patch.object(dp, "STATE", self.tmp):
            dp.queue_add(live, OTHER, NOW)
            self.assertEqual(dp.outbox_mirror(live, push, NOW), "pushed")
            self.assertEqual(dp.outbox_mirror(live, push, NOW + 600), "same", "a new time alone is no change")
            dp.queue_add(live, "Another post.", NOW + 700)
            self.assertEqual(dp.outbox_mirror(live, push, NOW + 700), "pushed")
            self.assertEqual(dp.outbox_mirror(live, lambda t: False, NOW + 701), "same")
            dp.queue_add(live, "A third post.", NOW + 800)
            self.assertEqual(dp.outbox_mirror(live, lambda t: False, NOW + 800), "failed")
            self.assertEqual(dp.outbox_mirror(live, push, NOW + 801), "pushed", "a failed write is tried again")
        self.assertEqual(len(pushed), 3)
        self.assertIn("Waiting, not shown yet: 3", pushed[-1])

    def test_push_outbox_writes_the_file_whole(self):
        target = self.tmp / "ws" / "OUTBOX.md"
        target.parent.mkdir()
        real = subprocess.run

        def local(cmd, **kw):
            self.assertEqual(cmd[:6], ["wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "--"])
            return real(["bash", "-s"], **kw)
        dp.queue_add(self.box, "It's `done`; $HOME and \"quotes\" stay as typed.", NOW)
        text = dp.outbox_view(self.box, NOW)
        with mock.patch.object(dp, "OUTBOX_FILE", str(target)), mock.patch.object(dp.subprocess, "run", local):
            self.assertTrue(dp.LaneHost().push_outbox(text))
        self.assertEqual(target.read_text(), text)
        self.assertEqual([p.name for p in target.parent.iterdir()], ["OUTBOX.md"], "no temp file left behind")

    def test_the_hubs_own_words_never_state_a_count(self):
        for bad in ("That was the last item.", "Nothing else waiting.", "2 more waiting.", "Say next for the rest."):
            self.assertIsNotNone(dp.item_problem("post", [bad]), bad)
        self.assertIsNone(dp.item_problem("post", ["Three lanes are waiting for a review; the last one merged at 11:50 PM."]))
        self.record_question("oc-ce1095ad", "pending", NOW)
        ok, msg = dp.say(self.ctx, self.host, "oc-ce1095ad", "This is the last item tonight.", NOW + 5)
        self.assertFalse(ok)
        self.assertIn("talks about the outbox", msg)

    def test_the_receptionist_is_told_to_read_outbox_md_first(self):
        text = (HOOK / "agents_live.md").read_text()
        rule = next((p for p in text.split("\n\n") if p.startswith("**The outbox.**")), "")
        self.assertIn("read `OUTBOX.md` with your read tool", rule)
        for words in ('"say next"', '"last item"', '"nothing else waiting"', "any count of posts"):
            self.assertIn(words, rule)
        self.assertIn("When it says nothing is waiting, never ask him to say next.", rule)
        self.assertIn("Never count posts from older messages in this chat", rule)

    def test_the_live_hosts_mirror_after_every_post(self):
        calls = []
        host = QHost(self.box)
        host.mirror_outbox = lambda: calls.append(1)
        host.inject("A post.")
        dp.post_answer(host, "An answer.")
        self.assertEqual(len(calls), 2)



class EachPostLeavesOnce(td.Base):
    """20 Oct 12:06-12:17 AM: the "last item" post and the "Next up" line each showed as two identical [hub] blocks in his chat.
    Main session's history holds each once, so the hub sent each once; the second block is the chat page's own (its history merge
    keeps the live copy of an injected post beside the saved one: chat.inject's live frame has run id inject-<id> and no seq, the
    saved row has neither run id nor matching seq, and chat.inject takes no id the page could match). This holds the hub's half:
    across a night of polls, each post and each nudge goes to chat.inject exactly once."""

    def test_the_1206_to_1217_am_run_sends_every_post_once(self):
        box = self.tmp / "chat-queue.json"
        host = QHost(box)
        last = "**This is the last item. Nothing waits after it.**"
        dp.queue_add(box, last, NOW)
        dp.queue_pump(box, host.inject_now, NOW)  # 12:06 AM: shown
        for m in range(1, 6):
            dp.queue_pump(box, host.inject_now, NOW + 60 * m)  # the poller runs every minute; nothing new to send
        dp.queue_add(box, OTHER, NOW + 360)  # 12:12 AM: a lane report arrives while the last item is still up
        for m in range(6, 12):
            dp.queue_pump(box, host.inject_now, NOW + 60 * m)  # one "Next up" nudge, then nothing again
        dp.queue_next(box, host.inject_now, NOW + 720, "next")  # 12:15 AM: he says next
        for m in range(13, 15):
            dp.queue_pump(box, host.inject_now, NOW + 60 * m)
        dp.queue_next(box, host.inject_now, NOW + 900, "next")  # 12:17 AM: next again, with nothing waiting
        sent = host.shown
        self.assertEqual(sum(last in t for t in sent), 1, sent)
        self.assertEqual(sum(OTHER.splitlines()[-1] in t for t in sent), 1, sent)
        self.assertEqual(sum(t.lstrip().startswith("Next up") or "\nNext up" in t for t in sent), 1, sent)
        self.assertEqual(len(sent), len(set(sent)), sent)  # no text went out twice


if __name__ == "__main__":
    unittest.main()
