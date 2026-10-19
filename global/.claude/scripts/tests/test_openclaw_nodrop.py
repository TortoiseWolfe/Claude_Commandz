#!/usr/bin/env python3
"""No message of Jonathan's is dropped (19 Oct 2026, 4:15 PM: "I can't have you dropping messages ... fix that bullshit").

The reconciler (openclaw_reconcile.py) checks every tray message against the three places it may end: a live lane, a lane started or
restarted with his words, or the hub's list. Today's drops are replayed twice: as the old code left them (the reconciler must find all
six), and through the fixed code (the reconciler must find none). Words and lane names are stand-ins; the shapes are the real ones.
"""
import hashlib
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import openclaw_reconcile as rc  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
import test_openclaw_reply as tr  # noqa: E402

D19 = 1792440000  # 19 Oct 2026 4:00 PM EDT


def sha_of(n):
    return hashlib.sha256(("message %d" % n).encode()).hexdigest()


def oc(n):
    return dp.ask_id_for(sha_of(n))


class Fixture(td.Base):
    def setUp(self):
        super().setUp()
        self.intake = self.tmp / "intake"
        self.intake.mkdir()
        self.ctx.inbox_path = self.inbox

    def message(self, n, text, ts):
        item = {"id": "tray-%d" % n, "source": "tray", "route": "own", "text": text, "ts": ts, "sha256_of_original": sha_of(n), "untrusted": True}
        (self.intake / (sha_of(n) + ".json")).write_text(json.dumps(item))
        return item

    def write_log(self, *lines):
        with open(self.ctx.results_log, "a") as f:
            f.write("".join(l + "\n" for l in lines))

    def found(self, since, now, history=None):
        return rc.reconcile(self.ctx, self.intake, since, now, history)


class TodayBeforeTheFix(Fixture):
    """19 Oct 3:34 PM to 4:20 PM as the old code left it."""

    def setUp(self):
        super().setUp()
        t = D19 - 26 * 60  # 3:34 PM
        for n, words in enumerate(["is this an improvement", "it does show the porch columns", "why no time stamps",
                                   "are there three steps", "I never said open another file", "yes, go ahead with the cleanup",
                                   "the hub should have the whole case", "next"], 1):
            self.message(n, words, t + n * 60)
        uns = dp.ask_id_for(hashlib.sha256(("%s %s" % (sha_of(7), t + 7 * 60)).encode()).hexdigest())
        self.write_log("2026-10-19 15:35 | reply | no such lane: on the hub's list (chat) | %s" % oc(1),
                 "2026-10-19 15:36 | reply | no such lane: on the hub's list (chat) | %s" % oc(2),
                 "2026-10-19 15:38 | chat | nothing run: the marker was not the last line of the reply | %s" % oc(3),
                 "2026-10-19 15:51 | chat | nothing run: the marker was not the last line of the reply | %s" % oc(4),
                 "2026-10-19 15:57 | chat | nothing run: the marker was not the last line of the reply | %s" % oc(5),
                 "2026-10-19 15:57 | reply oc-workspace-1019-1550: follow-up | workspace | handed to Claude (ask %s, 5 min to take it) (chat) | %s" % (oc(6), oc(6)),
                 "2026-10-19 14:52 | unsettled | no reply to it could be found in Main session (a hub line, not the model, came right after his message): put on the hub's list (ask %s)" % uns)
        s = dp.load_state(self.ctx.state_path)
        q = {"kind": "question", "route": "hub, the front desk could not do it", "status": "answered", "fallback_started_at": D19 - 1200}
        dp.asks_of(s)[oc(1)] = dict(q, lanes=["oc-workspace-1019-1550"], answer="It was not an improvement, you are right.", task="is this an improvement")
        s["asks"][oc(2)] = dict(q, lanes=["oc-workspace-1019-1551"], answer="The front desk could not forward your reply to it.", task="porch")
        s["asks"][oc(6)] = {"status": "claimed", "lanes": ["oc-workspace-%s" % oc(6)[3:]], "created": D19 - 180, "claimed_at": D19 - 150,
                            "deadline": D19 + 120, "task": "yes, go ahead with the cleanup"}
        s["lanes"]["oc-workspace-%s" % oc(6)[3:]] = {"repo": "workspace", "started": D19 - 150, "status": "closed", "failed": {"why": "denied"}}
        s["asks"][uns] = {"kind": "question", "route": "hub, front desk could not settle it", "status": "deferred", "task": "the hub should have the whole case"}
        dp.save_state(self.ctx.state_path, s)
        self.now = D19 + 20 * 60  # 4:20 PM

    def test_it_finds_all_six_drops_and_nothing_else(self):
        out, counts = self.found(0, self.now)
        self.assertEqual([m["id"] for m, _ in out], [oc(n) for n in range(1, 7)])
        whys = dict((m["id"], w) for m, w in out)
        self.assertIn("a question lane answered it instead", whys[oc(1)])
        self.assertIn("a question lane answered it instead", whys[oc(2)])
        self.assertIn("not the last line", whys[oc(3)])
        self.assertIn("every lane it went to failed", whys[oc(6)])
        self.assertEqual(counts["delivered"], 1)  # drop 3's message: it did reach the hub's list
        self.assertEqual(counts["no trace (before the fix)"], 1)  # "next", answered in chat: only Main session could tell

    def test_main_session_tells_a_plain_answer_from_a_marker_that_never_ran(self):
        t = D19 - 26 * 60 + 8 * 60
        history = [tr.msg("user", "next", t + 1), tr.msg("assistant", "Here is the next card.", t + 3)]
        _, counts = self.found(0, self.now, history)
        self.assertEqual(counts["no trace (before the fix)"], 0)
        self.assertEqual(counts["delivered"], 2)
        history[-1] = tr.msg("assistant", "On it.\n\nSending to the hub: start own-app: next card", t + 3)
        out, _ = self.found(0, self.now, history)
        self.assertIn("a marker that never ran", dict((m["id"], w) for m, w in out)[oc(8)])

    def test_filing_puts_each_on_the_hubs_list_and_the_next_run_finds_none(self):
        out, _ = self.found(0, self.now)
        filed = rc.file_all(self.ctx, out, self.now)
        self.assertEqual(len(filed), 6)
        asks = dp.asks_of(dp.load_state(self.ctx.state_path))
        self.assertTrue(all(asks[i]["status"] in dp.OPEN_ASK_STATUSES for i in filed), [asks[i]["status"] for i in filed])
        self.assertEqual(self.found(0, self.now + 60)[0], [])
        self.assertTrue(Path(self.ctx.wake_path).exists())

    def test_a_pass_past_its_deadline_with_no_answer_is_a_drop(self):
        s = dp.load_state(self.ctx.state_path)
        s["asks"][oc(6)] = {"status": "pending", "created": D19 - 180, "deadline": D19 - 30 * 60, "task": "x"}
        dp.save_state(self.ctx.state_path, s)
        out, _ = self.found(0, self.now)
        self.assertIn("the pass never answered", dict((m["id"], w) for m, w in out)[oc(6)])

    def test_since_skips_messages_handled_before_the_reconciler_existed(self):
        self.assertEqual(self.found(D19 + 60, self.now)[0], [])


class TodayAfterTheFix(Fixture):
    """The same kinds of message, through the fixed code, then reconciled: none is undelivered."""

    def setUp(self):
        super().setUp()
        day = time.strftime("%F", time.localtime(td.NOW))
        old = self.tmp / "lanes" / ("%s-porch-finish" % day)
        old.mkdir(parents=True)
        (old / "prompt.md").write_text("Trace the porch columns.\n")
        (old / "report.md").write_text("Columns traced. 3:20 PM\n")
        row = [day, "porch-finish", str(self.root / "own-app"), "sonnet", "claude", str(old / "prompt.md"), str(old / "report.md"), "sid", "", "", "on"]
        Path(self.ctx.index_path).write_text("\t".join(row) + "\n")
        self.host = tr.ReplyHost([])

    def poll(self, item, now, history=None):
        if history is not None:
            self.host.history = history
        res = [{"verdict": "accept", "stored": item}] if item else []
        with mock.patch.object(pk, "site_models", return_value={}):
            return pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.ctx.results_log, now=now, ctx=self.ctx)

    def test_drop_1_a_reply_to_a_closed_lane_restarts_it_with_his_words(self):
        t = td.NOW
        self.poll(self.message(1, "reply porch-finish: it does show the porch columns!", t), t + 5)
        self.assertEqual([s[0] for s in self.host.started], ["porch-finish-2"])
        prompt = Path(self.host.started[0][4]).read_text()
        self.assertIn("Trace the porch columns.", prompt)
        self.assertIn("Columns traced.", prompt)
        self.assertIn(json.dumps("it does show the porch columns!"), prompt)
        self.assertIn("restarted it as porch-finish-2", self.host.chats[-1])
        self.poll(self.message(2, "reply porch-finish: and the steps too", t + 60), t + 65)
        self.assertEqual([n for n, _ in self.host.sent], ["porch-finish-2"])  # the second reply goes to the lane that carries on
        self.assertEqual(self.found(0, t + rc.GRACE + 120)[0], [])

    def test_drop_1_a_fallback_never_answers_a_reply_meant_for_a_lane(self):
        t = td.NOW
        self.poll(self.message(1, "reply nowhere-lane: are you there", t), t + 5)
        asks = dp.asks_of(dp.load_state(self.ctx.state_path))
        self.assertEqual(asks[oc(1)]["target_lane"], "nowhere-lane")
        self.assertEqual(dp.fallback_due(self.ctx, self.host, t + 3600), [])
        self.assertEqual(self.found(0, t + rc.GRACE + 120)[0], [])

    def test_drop_1_an_answer_that_says_it_could_not_forward_him_is_refused(self):
        t = td.NOW
        dp.hub_has_it(self.ctx, oc(1), "porch", t, "front desk: test")
        ok, msg = dp.post_ask(self.ctx, self.host, oc(1), "The front desk could not forward your reply to it.")
        self.assertFalse(ok, msg)
        self.assertIn(dp.asks_of(dp.load_state(self.ctx.state_path))[oc(1)]["status"], dp.OPEN_ASK_STATUSES)

    def test_drop_2_a_marker_followed_by_more_text_runs(self):
        t = td.NOW
        words = "list the files in docs"
        item = self.message(3, words, t)
        self.poll(item, t + 5, [tr.msg("user", words, t + 1), tr.msg("assistant", "Sending to the hub: start own-app: list the docs\n\nThat should do it.", t + 3)])
        self.assertEqual(len(self.lane_names()), 1)
        self.assertEqual(self.found(0, t + rc.GRACE + 120)[0], [])

    def test_drop_3_a_reply_after_a_hub_line_goes_to_the_hub_with_the_receptionists_reply(self):
        t = td.NOW
        words = "the hub should have the whole case"
        item = self.message(4, words, t)
        history = [tr.msg("user", words, t + 1), tr.hub("a card about something else", t + 2),
                   tr.msg("assistant", "Sending to the hub: the hub should have the whole case", t + 4)]
        self.poll(item, t + 5, history)
        self.poll(None, t + pk.REPLY_WAIT + 10)
        lines = dp.open_question_lines(self.ctx, t + pk.REPLY_WAIT + 20)
        self.assertTrue(any("receptionist's reply" in l for l in lines), lines)
        self.assertIn("hub's list", self.host.chats[-1])
        self.assertEqual(self.found(0, t + rc.GRACE + 120)[0], [])

    def test_a_plain_chat_answer_leaves_a_trace_and_counts_as_delivered(self):
        t = td.NOW
        self.poll(self.message(5, "thanks", t), t + 5, [tr.msg("user", "thanks", t + 1), tr.msg("assistant", "You're welcome.", t + 3)])
        out, counts = self.found(0, t + rc.GRACE + 120)
        self.assertEqual(out, [])
        self.assertEqual(counts["delivered"], 1)

    def test_a_message_with_no_trace_after_the_fix_is_a_drop(self):
        self.message(6, "lost", td.NOW)
        out, _ = self.found(0, td.NOW + rc.GRACE + 120)
        self.assertEqual([w for _, w in out], ["no trace: the front desk never settled it"])


class Decode(unittest.TestCase):
    def test_bytes_that_are_not_utf8_never_crash_a_post(self):
        # 19 Oct 5:09 PM: wsl.exe printed its failure in another encoding and two of his messages never ran (UnicodeDecodeError)
        r = dp.LaneHost()._run([sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'x\\xe9\\x28y'); sys.stdout.buffer.write(b'\\xff')"], 30)
        self.assertIsInstance(r, subprocess.CompletedProcess)
        self.assertIn("x", r.stderr)


class DedupWindow(td.Base):
    """The same words asked again are only a duplicate inside DEDUP_WINDOW: after it, his repeat is a new request and runs."""

    def test_the_same_task_after_the_window_is_not_a_duplicate(self):
        task = "trace the porch columns in own-app"
        key = dp.task_key(task)
        for age, dup in ((60, True), (dp.DEDUP_WINDOW + 1, False)):
            dp.save_state(self.ctx.state_path, {"lanes": {}, "starts": [], "asks": {"oc-11111111": {
                "task_key": key, "created": td.NOW - age, "status": "answered", "kind": "task"}}})
            self.assertEqual(dp.duplicate_start(self.ctx, task, td.NOW), dup, age)


if __name__ == "__main__":
    unittest.main()
