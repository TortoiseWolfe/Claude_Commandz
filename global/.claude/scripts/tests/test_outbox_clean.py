#!/usr/bin/env python3
"""19 Oct 11:10 PM, replayed. His outbox held 20 posts: 10 were "On the hub's list: the hub has your message.", 6 were repeats (a lane
report saying again what the lane had just posted, and a rewritten report posted whole a second time, in parts), and his own question
about the queue waited 37 minutes behind the 12-starts-a-day cap. "I don't need 5 part reports, or needless repeats ... clean it up and
just tell me what I actually need to know"; "clear the duplicates". The rules:
  1. the front desk never posts that acknowledgement (the receptionist already said it went to the hub);
  2. a lane report reaches his chat once, as one item of at most 150 words, never in parts; a rewritten report replaces its item when
     he has not seen it yet, and after he has seen it only what is new is posted (nothing, when nothing is new);
  3. his questions about the chat itself are not held by the day's cap (they get CHAT_EXTRA_PER_DAY more starts; one that needs
     mail gets none);
  4. "N more waiting" counts real posts only.
Names here are invented."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_dispatch as dp  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
from test_openclaw_queue import QHost  # noqa: E402

NOW = td.NOW
ACK = "On the hub's list: the hub has your message."

# the lane report of 8:52 PM, and its rewrite of 10:43 PM (one line reworded, one paragraph added)
PLAN = ("Plan posted on #344.\n\n"
        "- **16 pages move, not 13.** The FAQ, the therapy page and the contact page proved unique too. Everything else 301s.\n"
        "- **Dates:** backup Wed 21 Oct, drafts Wed to Thu, staging proof Fri 23. Sat 24 Oct, 6:00 to 9:59 AM: publish and switch DNS.\n\n"
        "**Jonathan's one step:** open WP Admin from the host's site menu and add yourself as Administrator.")
PLAN_2 = PLAN.replace("from the host's site menu", "from Manage All, then the site menu,") + (
    "\n\n**Update, for the hub:** the edge has served 20 blog images as cached 429s since Sun 18 Oct. A URL purge clears it.")


class Clean(td.Base):
    def setUp(self):
        super().setUp()
        self.box = self.tmp / "chat-queue.json"
        self.host = QHost(self.box)

    def items(self):
        return dp.queue_load(self.box)["items"]

    # 1 ----------------------------------------------------------------------------------------------------------------
    def test_a_question_put_on_the_hubs_list_posts_nothing_to_his_chat(self):
        self.ctx.hand_off_first = False
        line, chat = dp.ask_question(self.ctx, self.host, "oc-0be4a771", "clear the duplicates and give me what's left queued", NOW,
                                     {"history": [{"who": "Jonathan", "text": "clear the duplicates and give me what's left queued"}]})
        self.assertIn("put on the hub's list", line)
        self.assertIsNone(chat)
        self.assertEqual(self.host.shown, [])
        self.assertEqual(self.items(), [])
        self.assertIn("oc-0be4a771", self.ctx.inbox_path.read_text(), "his words still reach the hub")

    def test_the_acknowledgement_is_never_queued_and_an_old_queue_full_of_them_counts_only_real_posts(self):
        self.assertIsNone(dp.queue_add(self.box, ACK, NOW))
        self.assertIsNone(dp.queue_add(self.box, "[hub] " + ACK, NOW))
        self.assertEqual(self.items(), [])
        # tonight's queue, as the hub found it at 11:06 PM: the acknowledgements were queued before this fix
        q = {"items": [], "done": [], "seq": 0}
        real = ["**Why it was denied:** the safety check refused the merge.", "**Your decision:** merge #74 first, then #75.",
                "Sent to drafts-check, a lane the hub started."]
        for n, text in enumerate([ACK, ACK, real[0], ACK, real[1], ACK, ACK, real[1], ACK, real[2], ACK]):
            q["seq"] += 1
            q["items"].append({"id": q["seq"], "text": text, "source": "hub", "added": NOW + n, "shown": None, "attempts": 0,
                               "next_try": NOW, "failing_since": None, "woke": None})
        dp.queue_save(self.box, q)
        self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, NOW + 60), "shown")
        self.assertEqual(self.host.shown, [real[0] + dp.queue_tail(2)], "2 more waiting: the real posts, not 10")
        self.assertEqual([i["text"] for i in self.items()], real)
        dropped = [i["why"] for i in dp.queue_load(self.box)["done"]]
        self.assertEqual(sorted(set(dropped)), ["dropped: acknowledgement", "dropped: repeat"])
        self.assertEqual(len(dropped), 8)

    def test_the_same_words_already_waiting_are_not_queued_twice(self):
        dp.queue_add(self.box, "one", NOW)
        dp.queue_add(self.box, "Stopped: a new job, started by mistake.", NOW)
        self.assertIsNone(dp.queue_add(self.box, "Stopped: a new job,  started by mistake.", NOW + 1))
        self.assertEqual(len(self.items()), 2)

    # 2 ----------------------------------------------------------------------------------------------------------------
    def report(self, lane, text, mtime):
        p = self.tmp / "lanes" / ("2026-10-19-%s" % lane) / "report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        os.utime(p, (mtime, mtime))
        return p

    def posts(self, now):
        return dp.post_new_reports(self.ctx, self.host, now)

    def test_a_long_lane_report_is_one_item_of_150_words_with_its_path(self):
        dp.post_new_reports(self.ctx, self.host, NOW, seed=True)
        body = "\n\n".join("## Part %d\n\nThe plan for this page is set and the redirect is in place for the move. " % k * 3 for k in range(80))
        p = self.report("site-mail", body, NOW - 60)
        self.assertEqual(self.posts(NOW), ["site-mail"])
        self.assertEqual(len(self.items()), 1, "one item, never parts")
        text = self.items()[0]["text"]
        self.assertLessEqual(len(text.split()), dp.REPORT_WORDS)
        self.assertNotRegex(text, r"\(\d+ of \d+\)|\(part \d+")
        self.assertTrue(text.startswith("### site-mail\n"))
        self.assertIn("(Shortened. Full report: %s)" % p, text)

    def test_a_rewritten_report_replaces_its_unshown_item_and_after_he_saw_it_only_the_news_is_posted(self):
        dp.post_new_reports(self.ctx, self.host, NOW, seed=True)
        dp.queue_add(self.box, "something he is reading", NOW)  # one post showing, so the report waits behind it
        p = self.report("site-move", PLAN, NOW - 60)
        self.posts(NOW)
        self.assertEqual(len(self.items()), 2)
        os.utime(p, (NOW + 30, NOW + 30))
        p.write_text(PLAN_2)
        os.utime(p, (NOW + 30, NOW + 30))
        self.posts(NOW + 60)
        self.assertEqual(len(self.items()), 2, "replaced where it stands, not a second post")
        self.assertIn("Update, for the hub", self.items()[1]["text"])
        self.assertIn("16 pages move", self.items()[1]["text"], "he never saw the first version: the whole report")
        # he reads it
        self.host.t = NOW + 100
        dp.queue_next(self.box, self.host.inject_now, NOW + 100, why="hub")
        dp.queue_next(self.box, self.host.inject_now, NOW + 200, why="hub")
        self.assertEqual(self.items(), [])
        # the lane writes the same report again: nothing new, nothing posted
        os.utime(p, (NOW + 300, NOW + 300))
        self.assertEqual(self.posts(NOW + 400), [])
        self.assertEqual(self.items(), [])
        # and then adds one line: only that line comes
        p.write_text(PLAN_2 + "\n\nPurge done at 11:20 PM.")
        os.utime(p, (NOW + 500, NOW + 500))
        self.assertEqual(self.posts(NOW + 600), ["site-move"])
        new = self.host.shown[-1]
        self.assertIn("Purge done at 11:20 PM.", new)
        self.assertIn("Report updated", new)
        self.assertNotIn("16 pages move", new)

    def test_a_record_from_before_texts_were_kept_still_posts_nothing_for_an_unchanged_report(self):
        # the live record held 411 report times and texts for 4 when this shipped: an old entry has its mtime only
        p = self.report("old-lane", PLAN, NOW - 600)
        self.ctx.reports_posted_path.write_text(json.dumps({"posted": {str(p): NOW - 600}}))
        self.assertEqual(self.posts(NOW), [])
        self.assertEqual(self.items(), [])

    def test_a_lane_that_already_answered_in_chat_does_not_post_its_report_again(self):
        # 10:26 PM the answer lane posted three items with `post`; 10:27 PM the sweep posted its report saying the same, in three parts
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        state = self.state()
        state.setdefault("asks", {})["oc-f69d36a1"] = {"kind": "question", "task": "why was the merge denied?", "created": NOW - 3600,
                                                       "deadline": NOW, "status": "deferred", "deferred_at": NOW - 1800, "lanes": []}
        dp.save_state(self.ctx.state_path, state)
        dp.fallback_due(self.ctx, self.host, NOW)
        lane = next(iter(self.state()["lanes"]))
        ok, _msg = dp.post_ask(self.ctx, self.host, "oc-f69d36a1", "**Why it was denied:** the safety check refused it.", NOW + 60)
        self.assertTrue(ok, _msg)
        Path(self.state()["lanes"][lane]["report"]).write_text("Answered and posted to oc-f69d36a1. I merged nothing.")
        before = len(self.items()) + len(dp.queue_load(self.box)["done"])
        out = dp.sweep(self.ctx, self.host, NOW + 120)
        self.assertEqual(len(out), 1)
        self.assertIn("not posted: it already answered ask oc-f69d36a1 in chat", out[0][0])
        self.assertEqual(len(self.items()) + len(dp.queue_load(self.box)["done"]), before, "no second post")
        self.assertTrue(self.state()["lanes"][lane]["delivered"])

    # 3 ----------------------------------------------------------------------------------------------------------------
    def record_question(self, ask_id, task):
        state = self.state()
        state.setdefault("asks", {})[ask_id] = {"kind": "question", "task": task, "created": NOW - 1800, "deadline": NOW - 1500,
                                                "status": "deferred", "deferred_at": NOW - 20 * 60, "lanes": []}
        state["starts"] = [NOW - 10] * dp.MAX_PER_DAY
        dp.save_state(self.ctx.state_path, state)

    def test_his_question_about_the_chat_is_not_held_by_the_days_cap(self):
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.record_question("oc-0be4a771", "then why the fuck is the que still say 30odd items if you're just repeationg a bunch of "
                                            "duplicates, clear the duplicates and give me what's actaully left qued")
        out = dp.fallback_due(self.ctx, self.host, NOW)
        self.assertEqual(len(out), 1)
        self.assertIn(" | started ", out[0][0])
        self.assertEqual(dp.asks_of(self.state())["oc-0be4a771"]["status"], "fallback-lane")

    def test_a_question_about_the_chat_gets_a_few_extra_starts_not_an_open_door(self):
        # a word match ("chat", "stuck") is loose, so it buys CHAT_EXTRA_PER_DAY starts past the cap, never an unlimited number
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.record_question("oc-0be4a771", "clear the duplicates in the chat")
        state = self.state()
        state["starts"] = [NOW - 10] * (dp.MAX_PER_DAY + dp.CHAT_EXTRA_PER_DAY)
        dp.save_state(self.ctx.state_path, state)
        out = dp.fallback_due(self.ctx, self.host, NOW)
        self.assertIn("held: %d starts today" % (dp.MAX_PER_DAY + dp.CHAT_EXTRA_PER_DAY), out[0][0])

    def test_a_chat_question_that_needs_mail_still_waits_for_the_days_cap(self):
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.record_question("oc-33334444", "the chat says the Gmail draft is stuck, fix it")
        self.assertTrue(dp.mail_ask("the chat says the Gmail draft is stuck, fix it"))
        out = dp.fallback_due(self.ctx, self.host, NOW)
        self.assertIn("held: %d starts today" % dp.MAX_PER_DAY, out[0][0])

    def test_any_other_question_still_waits_for_the_days_cap(self):
        (self.root / ".git").mkdir()
        self.ctx.repos = dp.load_repos(self.root / "hub/repos.tsv", self.root)
        self.record_question("oc-11112222", "which sample status is current?")
        out = dp.fallback_due(self.ctx, self.host, NOW)
        self.assertIn("held: %d starts today" % dp.MAX_PER_DAY, out[0][0])

    def test_what_counts_as_a_question_about_the_chat(self):
        for t in ("clear the duplicates", "why is the queue frozen", "what's actually left qued", "the outbox says 30 more waiting",
                  "open claw chat still looks frozen too?", "stop repeating posts"):
            self.assertTrue(dp.about_the_chat(t), t)
        for t in ("which sample status is current?", "merge #74", "what's next week's plan", "Chattanooga drafts"):
            self.assertFalse(dp.about_the_chat(t), t)


if __name__ == "__main__":
    unittest.main()
