"""deck_write.py pacing: the wait between writes, the queue, drain, queue-status.

A fake clock and a fake network (call/find_card/read_env are replaced), a private state folder per test,
made-up card numbers and text. Nothing here reads a credentials file or touches the live Deck or ~/.claude/state.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deck_write

REAL_PICK_GAP = deck_write.pick_gap   # setUp replaces it with a fixed 12 minutes
ET = ZoneInfo("America/New_York")
TEXT = "hey, did the link open? i found it in the notes, the page looked fine on my end."
TEXT2 = "no rush on this one. i added the picture to the notes so it is easier to find."


def at(h, m=0, day=10):
    """Tue 10 Nov 2026 at h:m Eastern, as the fake 'now'."""
    return datetime(2026, 11, day, h, m, tzinfo=ET)


class Pace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"DECK_STATE_DIR": self.tmp.name}),
                        mock.patch.object(deck_write, "pick_gap", return_value=12 * 60)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = at(10)
        patcher = mock.patch.object(deck_write, "now", side_effect=lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.posts = []   # every fake request that would have reached the Deck

        def fake_call(c, method, path, body=None, api=deck_write.API):
            self.posts.append((method, path, body))
            return None
        for patcher in (mock.patch.object(deck_write, "read_env", return_value={"URL": "https://example.test"}),
                        mock.patch.object(deck_write, "find_card", return_value=({"id": 6, "title": "Done"}, {"id": 9, "title": "A card"})),
                        mock.patch.object(deck_write, "find_stack", return_value={"id": 6, "title": "Done"}),
                        mock.patch.object(deck_write, "call", side_effect=fake_call)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                result = deck_write.main(list(argv))
                code = result or 0
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 1
                if isinstance(stop.code, str):
                    err.write(stop.code)
        return code, out.getvalue(), err.getvalue()

    def comment(self, text=TEXT, card="9"):
        return self.run_cli("--env", "unused.cfg", "--board", "4", "comment", "--card", card, "--text", text)

    def queue_file(self):
        return Path(self.tmp.name) / "deck-write-queue.jsonl"


class TheGap(Pace):
    def test_a_second_write_inside_the_gap_is_refused_with_75(self):
        self.assertEqual(self.comment()[0], 0)
        self.clock = at(10, 5)
        code, _, err = self.comment(TEXT2)
        self.assertEqual(code, 75)
        self.assertEqual(len(self.posts), 1)
        self.assertIn("allowed after 10:12 AM Eastern", err)   # 10:00 write + the 12 minute wait

    def test_a_write_after_the_gap_passes(self):
        self.comment()
        self.clock = at(10, 12) + timedelta(seconds=1)
        self.assertEqual(self.comment(TEXT2)[0], 0)
        self.assertEqual(len(self.posts), 2)

    def test_the_wait_is_drawn_between_6_and_18_minutes(self):
        self.assertEqual((deck_write.GAP_MIN_S, deck_write.GAP_MAX_S), (360, 1080))
        picks = {REAL_PICK_GAP() for _ in range(300)}
        self.assertTrue(all(360 <= p <= 1080 for p in picks))
        self.assertGreater(len(picks), 50)   # random, not one fixed number

    def test_reading_a_card_is_never_paced(self):
        self.comment()
        code, out, _ = self.run_cli("--env", "unused.cfg", "--board", "4", "show", "--card", "9")
        self.assertEqual(code, 0)
        self.assertIn("#9 A card", out)

    def test_a_comment_and_a_move_on_one_card_are_one_write(self):
        code, out, _ = self.run_cli("--env", "unused.cfg", "--board", "4", "comment", "--card", "9", "--text", TEXT,
                                    "+", "move", "--card", "9", "--stack", "Done")
        self.assertEqual(code, 0)
        self.assertEqual([p[1] for p in self.posts], ["/cards/9/comments", "/cards/9/reorder"])
        self.clock = at(10, 3)
        self.assertEqual(self.comment(TEXT2)[0], 75)   # the pair set a single wait

    def test_joined_actions_must_share_a_card(self):
        code, _, err = self.run_cli("--env", "unused.cfg", "--board", "4", "comment", "--card", "9", "--text", TEXT,
                                    "+", "move", "--card", "10", "--stack", "Done")
        self.assertEqual(code, 1)
        self.assertIn("same --card", err)
        self.assertEqual(self.posts, [])

    def test_dry_run_makes_no_request_and_records_nothing(self):
        code, out, _ = self.run_cli("--env", "unused.cfg", "--board", "4", "--dry-run", "comment", "--card", "9", "--text", TEXT)
        self.assertEqual(code, 0)
        self.assertIn("dry run: would run comment on card 9", out)
        self.assertEqual(self.posts, [])
        self.assertFalse((Path(self.tmp.name) / "deck-last-write").exists())


class TheQueue(Pace):
    def queue(self, text=TEXT, card="9", board="4"):
        return self.run_cli("queue", board, "comment", "--card", card, "--text", text)

    def test_the_queue_keeps_the_exact_text(self):
        odd = "hey, it's \"ok\" now: the page loads fine for me, even with  two spaces and a tab\there."
        with mock.patch.object(deck_write, "require_chat_voice"):
            code, out, _ = self.queue(odd)
        self.assertEqual(code, 0)
        entry = json.loads(self.queue_file().read_text().splitlines()[0])
        self.assertEqual(entry["argv"], ["comment", "--card", "9", "--text", odd])
        self.assertEqual((entry["board"], entry["status"]), (4, "queued"))
        self.assertIn("1 waiting", out)

    def test_a_comment_that_fails_the_voice_check_is_not_queued(self):
        code, _, err = self.queue("**made up text**")
        self.assertNotEqual(code, 0)
        self.assertFalse(self.queue_file().exists())
        self.assertIn("no request was made", err)

    def test_drain_posts_one_queued_write_and_leaves_the_rest(self):
        self.queue(TEXT, "9")
        self.queue(TEXT2, "10")
        code, out, _ = self.run_cli("drain")
        self.assertEqual(code, 0)
        self.assertEqual([p[1] for p in self.posts], ["/cards/9/comments"])   # oldest first, one only
        left = [json.loads(ln) for ln in self.queue_file().read_text().splitlines()]
        self.assertEqual([e["argv"][2] for e in left], ["10"])
        self.assertIn("1 still waiting", out)

    def test_drain_waits_for_the_gap_before_the_next_one(self):
        self.queue(TEXT, "9")
        self.queue(TEXT2, "10")
        self.run_cli("drain")
        self.clock = at(10, 4)
        self.run_cli("drain")
        self.assertEqual(len(self.posts), 1)
        self.clock = at(10, 13)
        self.run_cli("drain")
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.queue_file().read_text(), "")

    def test_drain_does_nothing_outside_9am_to_9pm(self):
        self.queue()
        for hour, minute in ((8, 59), (21, 0), (23, 30), (3, 0)):
            self.clock = at(hour, minute)
            self.assertEqual(self.run_cli("drain")[0], 0)
            self.assertEqual(self.posts, [], f"posted at {hour}:{minute:02d}")
        self.clock = at(9, 0)
        self.run_cli("drain")
        self.assertEqual(len(self.posts), 1)

    def test_drain_with_an_empty_queue_is_silent(self):
        code, out, err = self.run_cli("drain")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_drain_dry_run_changes_nothing(self):
        self.queue()
        before = self.queue_file().read_text()
        code, out, _ = self.run_cli("drain", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would post", out)
        self.assertEqual(self.queue_file().read_text(), before)
        self.assertEqual(self.posts, [])
        self.assertFalse((Path(self.tmp.name) / "deck-last-write").exists())

    def test_a_failed_write_waits_ten_minutes_then_parks_after_three_tries(self):
        self.queue()
        with mock.patch.object(deck_write, "call", side_effect=SystemExit("deck_write: POST /cards/9/comments: HTTP 500")):
            self.assertEqual(self.run_cli("drain")[0], 1)
            entry = json.loads(self.queue_file().read_text())
            self.assertEqual((entry["attempts"], entry["status"]), (1, "queued"))
            self.clock = at(10, 5)
            self.run_cli("drain")   # inside both its own retry wait and the gap: nothing happens
            self.assertEqual(json.loads(self.queue_file().read_text())["attempts"], 1)
            self.clock = at(11)
            self.run_cli("drain")
            self.clock = at(12)
            self.run_cli("drain")
        entry = json.loads(self.queue_file().read_text())
        self.assertEqual((entry["attempts"], entry["status"]), (3, "failed"))
        self.clock = at(13)
        self.run_cli("drain")   # parked: never tried again
        self.assertEqual(json.loads(self.queue_file().read_text())["attempts"], 3)

    def test_queued_pairs_post_together(self):
        self.run_cli("queue", "4", "comment", "--card", "9", "--text", TEXT, "+", "move", "--card", "9", "--stack", "Done")
        self.run_cli("drain")
        self.assertEqual([p[1] for p in self.posts], ["/cards/9/comments", "/cards/9/reorder"])


class TheStatus(Pace):
    def test_empty_queue_and_no_last_write(self):
        self.assertEqual(self.run_cli("queue-status")[1].strip(), "0 queued; last write never")

    def test_counts_next_due_and_last_write(self):
        self.comment()   # 10:00, wait 12 min
        self.clock = at(10, 3)
        self.run_cli("queue", "4", "comment", "--card", "9", "--text", TEXT2)
        out = self.run_cli("queue-status")[1].strip()
        self.assertIn("1 queued", out)
        self.assertIn("next due 10:12 AM Eastern (comment on card 9 (board 4))", out)
        self.assertIn("last write 10:00 AM, the next write is allowed after 10:12 AM", out)

    def test_next_due_slides_to_9am_when_the_window_is_closed(self):
        self.clock = at(22, 30)
        self.run_cli("queue", "4", "comment", "--card", "9", "--text", TEXT)
        out = self.run_cli("queue-status")[1]
        self.assertIn("next due Wed 11 Nov 9:00 AM", out)


if __name__ == "__main__":
    unittest.main()
