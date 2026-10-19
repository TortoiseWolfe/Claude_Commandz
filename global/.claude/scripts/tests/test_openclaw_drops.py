"""18 Oct 09:10: the poller could not read Main session (wsl.exe failed), so none of his chat messages could be matched to the
receptionist's "Sending to the hub: ..." reply. They sat in openclaw-pending.json, then aged out of it after 15 minutes. Nothing
dispatched, nothing was logged, and the hub only learned of it when Jonathan said so.

The rule these tests hold: a message of his that waits more than REPLY_WAIT without being settled, or that the poller crashes on,
or that ages out, goes on the hub's list with his words and wakes the hub. It is never dropped silently, and never twice."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import test_openclaw_reply as tr  # noqa: E402

NOW, ASK_TS = tr.NOW, tr.ASK_TS


class Drops(tr.Reply):
    def poll(self, history, now, items=()):
        """One poller run at `now`; history None is a Main session that cannot be read."""
        self.host = tr.ReplyHost(history)
        res = [{"verdict": "accept", "stored": i} for i in items]
        with mock.patch.object(pk, "site_models", return_value={}):
            return pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=now, ctx=self.ctx)

    def pending(self):
        return json.loads(self.ctx.pending_path.read_text())

    def inbox_lines(self):
        p = self.ctx.inbox_path
        return p.read_text().splitlines() if p.exists() else []

    def open_asks(self):
        return [l for l in dp.open_question_lines(self.ctx) if l.startswith("oc-")]  # one row per ask; his words sit under it


class Alarm(Drops):
    def test_an_unreadable_session_for_more_than_the_reply_wait_puts_his_words_on_the_hub_list(self):
        self.go(None)  # arrives while the session cannot be read: waits
        self.assertEqual(len(self.pending()), 1)
        self.poll(None, ASK_TS + pk.REPLY_WAIT + 30)
        lines = self.inbox_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(json.dumps(self.TEXT), lines[0])
        self.assertIn("Jonathan via OpenClaw (question)", lines[0])
        self.assertIn("could not be read", lines[0])
        self.assertTrue(self.ctx.wake_path.exists())
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(self.open_asks()), 1)
        self.nothing()

    def test_the_hub_is_told_in_the_chat_once_for_a_burst(self):
        self.go(None)
        later = ASK_TS + pk.REPLY_WAIT + 30
        self.poll(None, later)
        self.assertEqual(len(self.host.chats), 1)
        self.assertIn("hub's list", self.host.chats[0])
        self.assertNotIn(self.TEXT, self.host.chats[0])  # his words are not echoed back

    def test_a_young_unreadable_message_waits_and_files_nothing(self):
        self.go(None)
        self.poll(None, ASK_TS + pk.REPLY_WAIT - 30)
        self.assertEqual(self.inbox_lines(), [])
        self.assertEqual(len(self.pending()), 1)
        self.assertFalse(self.ctx.wake_path.exists())

    def test_a_message_is_filed_once_however_many_polls_follow(self):
        self.go(None)
        for k in range(4):
            self.poll(None, ASK_TS + pk.REPLY_WAIT + 30 + 60 * k)
        self.assertEqual(len(self.inbox_lines()), 1)
        self.assertEqual(len(self.open_asks()), 1)

    def test_a_message_older_than_the_giving_up_age_is_filed_not_dropped(self):
        self.go(None)
        self.poll(self.flow(), ASK_TS + pk.ACT_MAX_AGE + 5)  # the poller was down until now
        self.assertEqual(len(self.inbox_lines()), 1)
        self.assertIn(json.dumps(self.TEXT), self.inbox_lines()[0])
        self.assertEqual(self.pending(), [])
        self.nothing()  # a stale marker is still never run

    def test_a_readable_session_with_an_ordinary_answer_files_nothing(self):
        self.go(None)
        self.poll([tr.him(), tr.msg("assistant", "Sure, the docs folder has three files.")], NOW)
        self.assertEqual(self.inbox_lines(), [])
        self.assertEqual(self.pending(), [])

    def test_a_readable_session_that_never_shows_his_message_files_it_once_it_is_old_enough(self):
        self.go([tr.msg("assistant", "unrelated")], now=NOW)
        self.assertEqual(len(self.pending()), 1)  # young: it may still arrive
        self.poll([tr.msg("assistant", "unrelated")], ASK_TS + pk.REPLY_WAIT + 30)
        self.assertEqual(len(self.inbox_lines()), 1)  # 18 Oct: a "none" was a silent drop; now it is filed with his words
        self.assertIn(json.dumps(self.TEXT), self.inbox_lines()[0])
        self.assertIn("no reply to it could be found", self.inbox_lines()[0])
        self.assertEqual(self.pending(), [])

    def test_the_alarm_runs_when_the_folder_was_empty_too(self):
        self.go(None)
        self.poll(None, ASK_TS + pk.REPLY_WAIT + 30, items=())  # no new items: the poller still checks the waiting list
        self.assertEqual(len(self.inbox_lines()), 1)

    def test_the_filed_message_is_on_the_open_list_with_his_words(self):
        self.go(None)
        self.poll(None, ASK_TS + pk.REPLY_WAIT + 30)
        self.assertIn(self.TEXT, "\n".join(self.open_asks()))

    def test_a_retry_after_a_crash_files_the_same_message_once(self):
        a = dp.unsettled_to_hub(self.ctx, "a" * 64, ASK_TS, self.TEXT, "test", NOW)
        b = dp.unsettled_to_hub(self.ctx, "a" * 64, ASK_TS, self.TEXT, "test", NOW + 5)
        self.assertIsNotNone(a[1])
        self.assertIsNone(b[1])
        self.assertEqual(len(self.inbox_lines()), 1)

    def test_a_long_message_reaches_the_hub_list_whole(self):
        long = "the whole point is to get what we can before sending Kai in person, " * 8
        dp.unsettled_to_hub(self.ctx, "c" * 64, ASK_TS, long, "test", NOW)
        self.assertIn(json.dumps(" ".join(long.split())), self.inbox_lines()[0])

    def test_two_identical_messages_a_minute_apart_are_two_asks(self):
        a = self.item(self.TEXT, ts=ASK_TS)
        b = self.item(self.TEXT, ts=ASK_TS + 60)
        self.poll(None, ASK_TS + 90, items=[a, b])
        self.poll(None, ASK_TS + 60 + pk.REPLY_WAIT + 30)
        self.assertEqual(len(self.inbox_lines()), 2)


class Crash(Drops):
    def test_a_marker_that_blows_up_goes_to_the_hub_list_and_the_others_are_still_settled(self):
        other = self.item("a second question", ts=ASK_TS + 5)
        other["sha256_of_original"] = "b" * 64
        history = [tr.him(), tr.msg("assistant", tr.REPLY), tr.him("a second question", ASK_TS + 6),
                   tr.msg("assistant", "Sure.", ts=ASK_TS + 8)]
        self.host = tr.ReplyHost(history)
        real = pk.execute

        def boom(*a, **k):
            raise RuntimeError("gateway fell over")
        res = [{"verdict": "accept", "stored": self.item(self.TEXT, ts=ASK_TS)}, {"verdict": "accept", "stored": other}]
        with mock.patch.object(pk, "site_models", return_value={}), mock.patch.object(pk, "execute", boom):
            pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=NOW, ctx=self.ctx)
        self.assertIs(pk.execute, real)
        lines = self.inbox_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(json.dumps(self.TEXT), lines[0])
        self.assertIn("gateway fell over", lines[0])
        self.assertEqual(self.pending(), [])

    def test_an_unexpected_error_in_the_history_read_is_the_same_as_an_unreadable_session(self):
        self.go(None)

        class Broken(tr.ReplyHost):
            def main_history(self, limit=40):
                raise OSError("wsl.exe not found")
        self.host = Broken(None)
        with mock.patch.object(pk, "site_models", return_value={}):
            pk.run_act([], self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.log, now=ASK_TS + pk.REPLY_WAIT + 30, ctx=self.ctx)
        self.assertEqual(len(self.inbox_lines()), 1)


if __name__ == "__main__":
    unittest.main()


class DroppedNone18Oct(Drops):
    """18 Oct 20:58 and 20:00: two of his messages were lost with no ask, no log line and no confirmation. find_reply said "none"
    and settle_pending dropped the entry. Replayed with the chat's shape (role, order, stamps, marker line; neutral words)."""

    def test_a_compaction_before_the_reply_still_runs_the_marker(self):
        h = [tr.him(self.TEXT, ASK_TS + 1), tr.msg("system", "Compaction", ASK_TS + 4.7, provider=None),
             tr.msg("assistant", tr.REPLY, ASK_TS + 4.99)]
        self.go(h)
        self.assertEqual(len(self.host.started), 1)
        self.assertEqual(self.pending(), [])

    def test_a_turn_that_ended_before_a_reply_is_filed_when_the_wait_is_over(self):
        h = [tr.him(self.TEXT, ASK_TS + 1), tr.msg("custom", "turn ended before a reply", ASK_TS + 2, provider=None)]
        self.go(h)
        self.assertEqual((len(self.pending()), self.inbox_lines()), (1, []))  # young: may still be answered
        self.poll(h, ASK_TS + pk.REPLY_WAIT + 30)
        self.assertEqual(len(self.inbox_lines()), 1)
        self.assertIn(json.dumps(self.TEXT), self.inbox_lines()[0])
        self.assertEqual(self.pending(), [])
        self.assertTrue(self.ctx.wake_path.exists())
        self.assertEqual(len(self.open_asks()), 1)
        self.nothing()

    def test_a_none_result_files_the_message_instead_of_dropping_it(self):
        with mock.patch.object(pk, "find_reply", return_value=("none", "a stand-in reason")):
            self.go(self.flow())
        lines = self.inbox_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(json.dumps(self.TEXT), lines[0])
        self.assertIn("a stand-in reason", lines[0])
        self.assertEqual(self.pending(), [])
        self.nothing()

    def test_a_none_result_is_filed_once_however_many_polls_follow(self):
        with mock.patch.object(pk, "find_reply", return_value=("none", "x")):
            self.go(self.flow())
            self.poll(self.flow(), NOW + 60)
            self.poll(self.flow(), NOW + 120)
        self.assertEqual(len(self.inbox_lines()), 1)
