#!/usr/bin/env python3
"""The outbox: everything the hub sends to his chat goes out one post at a time (19 Oct 2026, 4:59 PM: "that was a lot of cards at
once ... you need a que to give them back to me one at a time"), and a post that does not land is kept and tried again (5:03-5:24 PM
the bridge to the gateway failed on and off and four answers had to be held by hand)."""
import contextlib
import fcntl
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_pickup as pk  # noqa: E402
import test_openclaw_dispatch as td  # noqa: E402
import test_openclaw_reply as tr  # noqa: E402

NOW = td.NOW


class QHost(tr.ReplyHost):
    """The fake host with the real queued inject in front of a fake chat."""
    inject = dp.LaneHost.inject

    def __init__(self, path):
        super().__init__([])
        self.queue_path, self.shown, self.up, self.t = path, [], True, NOW

    def clock(self):
        return self.t

    def inject_now(self, text):
        if not self.up:
            return False
        self.shown.append(text)
        return True


class Outbox(td.Base):
    def setUp(self):
        super().setUp()
        self.box = self.tmp / "chat-queue.json"
        self.host = QHost(self.box)
        self.n = 0

    def him(self, text, ts):
        self.n += 1
        sha = ("%08x" % self.n) + "f" * 56
        return {"id": "tray-%d" % self.n, "source": "tray", "route": "own", "text": text, "ts": ts, "sha256_of_original": sha, "untrusted": True}

    def poll(self, item, now):
        res = [{"verdict": "accept", "stored": item}] if item else []
        with mock.patch.object(pk, "site_models", return_value={}):
            return pk.run_act(res, self.host, self.tmp / "skp", self.tmp / "aliases.json", self.inbox, self.wake, self.ctx.results_log,
                              now=now, ctx=self.ctx)

    def items(self):
        return dp.queue_load(self.box)["items"]

    def test_a_burst_of_five_posts_shows_one_then_one_per_next(self):
        posts = ["Post %d: the porch columns are traced.\n\nSecond paragraph %d." % (i, i) for i in range(1, 6)]
        for p in posts:
            self.assertTrue(self.host.inject(p))
        self.assertEqual(self.host.shown, [posts[0] + "\n\n(Nothing else waiting.)", "(1 more waiting. Say next for the next one.)"],
                         "one post shows; the rest wait, and one short line (once) says something is waiting")
        self.host.shown.pop()
        for k in range(1, 5):
            done = self.poll(self.him("next", NOW + 60 * k), NOW + 60 * k + 5)
            self.assertEqual(len(self.host.shown), k + 1)
            self.assertEqual(done[-1][0], "queue")
            self.assertIn("| next | showed the next post |", done[-1][1])
            self.assertEqual(dp.delivery_of(done[-1][1]), "frontdesk", "a next message counts as delivered, not as a drop")
        self.assertEqual(self.host.shown[1:], [posts[1] + "\n\n(3 more waiting. Say next for the next one.)",
                                               posts[2] + "\n\n(2 more waiting. Say next for the next one.)",
                                               posts[3] + "\n\n(1 more waiting. Say next for the next one.)",
                                               posts[4] + "\n\n(Nothing else waiting.)"])
        self.poll(self.him("Next please", NOW + 400), NOW + 405)
        self.assertEqual(self.host.shown[-1], "Nothing else waiting.")
        q = dp.queue_load(self.box)
        self.assertEqual(q["items"], [])
        self.assertEqual([d["text"] for d in q["done"]], posts, "every post shown whole, in order, none dropped")

    def test_his_answer_moves_on_and_his_message_still_runs(self):
        self.host.inject("Which porch color, white or grey?")
        self.host.inject("The 3:05 AM run is armed.")
        done = self.poll(self.him("white, thanks", NOW + 30), NOW + 35)
        self.assertEqual(len(self.host.shown), 3)  # the question, the line saying one more waits, then the next post
        self.assertEqual(self.host.shown[2], "The 3:05 AM run is armed.\n\n(Nothing else waiting.)")
        self.assertNotIn("queue", [k for k, _ in done], "an answer is not swallowed: it runs as usual")
        self.assertEqual([e["text"] for e in pk.load_pending(self.ctx.pending_path)], ["white, thanks"])

    def test_nothing_shows_while_one_is_up(self):
        self.host.inject("one")
        self.poll(None, NOW + 120)
        self.host.inject("two")
        self.host.inject("three")
        self.poll(None, NOW + 180)
        self.assertEqual(self.host.shown, ["one\n\n(Nothing else waiting.)", "(1 more waiting. Say next for the next one.)"])
        self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, NOW + 999), "showing")

    def test_a_post_that_does_not_land_stays_first_and_is_tried_again(self):
        self.host.up = False
        self.assertTrue(self.host.inject("Nate's run is armed."), "saved counts as posted: it can no longer be lost")
        self.host.t = NOW + 2
        self.assertTrue(self.host.inject("Second answer."))
        self.assertEqual(self.host.shown, [])
        head = self.items()[0]
        self.assertEqual((head["attempts"], head["failing_since"], head["next_try"]), (1, NOW, NOW + 30))
        self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, NOW + 10), "later")
        self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, NOW + 30), "failed")
        self.assertEqual(self.items()[0]["next_try"], NOW + 90)
        t = NOW + 90
        for _ in range(10):  # backoff grows to ten minutes and stays there, for as long as it takes
            self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, t), "failed")
            t = self.items()[0]["next_try"]
        self.assertEqual(self.items()[0]["next_try"] - self.items()[0]["failing_since"] > 3600, True)
        self.assertEqual(len(self.items()), 2)
        self.host.up = True
        self.poll(None, t)  # the every-minute poller tries it again
        self.assertEqual(self.host.shown, ["Nate's run is armed.\n\n(1 more waiting. Say next for the next one.)"])
        self.assertIsNone(self.items()[0]["failing_since"])

    def test_a_send_that_raises_keeps_the_post(self):
        def boom(text):
            raise RuntimeError("vsock")
        dp.queue_add(self.box, "kept", NOW)
        self.assertEqual(dp.queue_pump(self.box, boom, NOW), "failed")
        self.assertEqual(self.items()[0]["text"], "kept")

    def test_next_while_the_first_has_not_landed_tries_it_now(self):
        self.host.up = False
        self.host.inject("stuck one")
        self.host.up = True
        self.poll(self.him("next", NOW + 5), NOW + 6)
        self.assertEqual(self.host.shown, ["stuck one\n\n(Nothing else waiting.)"])
        self.assertEqual(len(self.items()), 1, "he has not seen it yet, so it is still the one showing, not done")

    def test_failing_fifteen_minutes_wakes_the_hub_once_then_hourly(self):
        self.host.up = False
        self.host.inject("stuck")
        self.assertIsNone(dp.queue_check(self.box, NOW + 14 * 60))
        line = dp.queue_check(self.box, NOW + 15 * 60)
        self.assertTrue(line.startswith("outbox: 1 post(s) for Jonathan have not reached his chat since "), line)
        self.assertIsNone(dp.queue_check(self.box, NOW + 16 * 60), "not on every poll")
        self.assertIsNotNone(dp.queue_check(self.box, NOW + 76 * 60))
        self.host.up = True
        dp.queue_pump(self.box, self.host.inject_now, NOW + 80 * 60)
        self.assertIsNone(dp.queue_check(self.box, NOW + 200 * 60), "shown: nothing to wake about")

    def test_a_second_sender_does_not_send_twice(self):
        dp.queue_add(self.box, "one", NOW)
        with open(str(self.box) + ".pump", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, NOW), "busy")
        self.assertEqual(self.host.shown, [])
        self.assertEqual(dp.queue_pump(self.box, self.host.inject_now, NOW), "shown")

    def test_an_unreadable_queue_is_kept_aside_not_written_over(self):
        self.box.write_text('{"items": [{"text": "held')
        with mock.patch.object(dp, "STATE", self.tmp):
            dp.queue_add(self.box, "new", NOW)
        aside = [p for p in self.tmp.iterdir() if p.name.startswith("chat-queue.json.unreadable-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_text(), '{"items": [{"text": "held')
        self.assertEqual([i["text"] for i in self.items()], ["new"])

    def test_a_bare_host_posts_straight_away(self):
        self.assertIsNone(dp.LaneHost().queue_path)
        self.assertEqual(list(vars(dp.LaneHost())), [])


class LiveEntryPoints(td.Base):
    """The CLI and the poller post through the outbox; in a live test that outbox is beside the test's state file, never his."""

    def env(self):
        return {dp.TEST_ENV: "1", dp.TEST_STATE_ENV: str(self.tmp / "t-state.json"), dp.TEST_SESSION_ENV: "agent:replay:queue"}

    def test_post_goes_through_the_test_outbox(self):
        sent = []

        def run(host, argv, timeout, cwd=None, stdin=None, env=None, timeout_result=False):
            if stdin:
                sent.append(json.loads(stdin))
            return type("R", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""})()
        live = dp.STATE / "openclaw-chat-queue.json"
        before = live.stat().st_mtime if live.exists() else None
        with mock.patch.dict(os.environ, self.env()), mock.patch.object(dp.LaneHost, "_run", run), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dp.main(["post", "first"]), 0)
            self.assertEqual(dp.main(["post", "second"]), 0)
            self.assertEqual(dp.chat_queue_path(), self.tmp / "t-state.chat-queue.json")
            injects = [s for s in sent if "message" in s]
            self.assertEqual([s["message"] for s in injects], ["first\n\n(Nothing else waiting.)", "(1 more waiting. Say next for the next one.)"])
            self.assertEqual({s["sessionKey"] for s in injects}, {"agent:replay:queue"})
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(dp.main(["queue"]), 0)
            self.assertIn("| showing since", out.getvalue())
            self.assertIn("| waiting | ", out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(dp.main(["queue", "next"]), 0)
            self.assertEqual([s["message"] for s in sent if "message" in s][-1], "second\n\n(Nothing else waiting.)")
        self.assertEqual(live.stat().st_mtime if live.exists() else None, before, "his outbox untouched")

    def test_a_pass_may_not_post_through_the_queue(self):
        with mock.patch.dict(os.environ, dict(self.env(), AFA_OC_PASS="oc-0000beef")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(dp.main(["queue", "add"]), 1)
            self.assertEqual(dp.main(["queue", "next"]), 1)

    def test_the_poller_uses_a_queued_host(self):
        seen = {}

        def fake_run_act(results, host, *a, **k):
            seen["path"] = host.queue_path
            return []
        with mock.patch.dict(os.environ, self.env()), mock.patch.object(pk, "run_act", fake_run_act), \
                mock.patch.object(pk, "pickup", return_value=[]), mock.patch.object(dp, "load_repos", return_value={}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pk.main(["--act"]), 0)
        self.assertEqual(seen["path"], self.tmp / "t-state.chat-queue.json")


if __name__ == "__main__":
    unittest.main()
