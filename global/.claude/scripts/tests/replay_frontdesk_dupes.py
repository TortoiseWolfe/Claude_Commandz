#!/usr/bin/env python3
"""Replay of the two 17 Oct evening receptionist-chat bugs through the current code. Everything runs on a temp state with the gateway call
replaced by a recorder (LaneHost._run), so every chat.inject the code would make is counted and none is sent: nothing here touches the live
state, the hub's list or Jonathan's chat.

  python3 replay_frontdesk_dupes.py

1  one hub post (the CLI, as the hub ran it for "## Tigard porch ...") makes exactly one chat.inject call.
2  one deferred question makes one short chat line, and one hub-list line that carries his whole message.
3  a failed lane's report is posted to the chat but leaves its ask open, on the hub's list (the oc-bb085874 case).
4  a later report posted to an already-answered ask is recorded as a follow-up on it and delivered once.

What this cannot show: the doubling Jonathan saw is the Control UI pairing a live chat.inject event with the stored copy, which is in the
gateway's web client; this replay shows the dispatcher sends once. See the lane report."""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
from test_openclaw_pass import PassBase  # noqa: E402

ASK = "oc-bb085874"
HIS = "Tigard porch: what is the status of the first pass and when does the fix land?"


class Recorder:
    """Stands in for LaneHost._run: records the gateway call (the params the real code would send) and answers ok."""
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout, cwd=None, stdin=None, env=None, timeout_result=False):
        self.calls.append(json.loads(stdin) if stdin else argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")


class Replay(PassBase):
    def runTest(self):
        rec = Recorder()
        env = {dp.TEST_ENV: "1", dp.TEST_STATE_ENV: str(self.tmp / "replay-state.json"), dp.TEST_SESSION_ENV: "agent:replay:dupes"}
        with mock.patch.dict(os.environ, env), mock.patch.object(dp.LaneHost, "_run", lambda host, *a, **k: rec(*a, **k)), \
                mock.patch.object(dp.LaneHost, "windows", lambda self: []), mock.patch.object(dp.LaneHost, "kill_window", lambda self, n: True):
            # 1. one hub post -> one chat.inject
            text = "## Tigard porch: first pass done, fix still running\n\nThe fix lane is running; I'll post when it lands."
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = dp.main(["post", text])
            sent = [c for c in rec.calls if isinstance(c, dict)]
            print("1 one hub post          `post` printed %r, exit %d; chat.inject calls: %d, to %s, label %s (before the fix the code also sent once: the doubling is not here)" % (
                out.getvalue().strip(), code, len(sent), sent[0]["sessionKey"], sent[0]["label"]))
            assert len(sent) == 1 and sent[0]["message"] == text

            # 2. one deferred question -> one short line, one whole-message hub-list line
            self.ctx.hand_off_first = False
            host = dp.LaneHost()
            line, chat = dp.ask_question(self.ctx, host, ASK + "0", HIS, time.time())
            inbox = self.ctx.inbox_path.read_text().splitlines()
            print("2 deferred question     chat line %r (%d chars, his words in it: %s); hub-list lines: %d, carrying his whole message: %s" % (
                chat, len(chat), HIS in chat, len(inbox), json.dumps(HIS) in "\n".join(inbox)))
            assert len(inbox) == 1 and json.dumps(HIS) in inbox[0] and HIS not in chat and len(chat) < 80

            # 3. a failed lane's report leaves its ask open
            state = {"lanes": {}, "starts": [], "asks": {}}
            now = time.time()
            state["asks"][ASK] = {"kind": "job", "status": "claimed", "task": "backlog sweep", "task_key": dp.task_key("backlog sweep"),
                                  "created": now - 900, "lanes": ["oc-scripthammer-bb085874"], "repo": "own-app"}
            report = self.tmp / "lanes" / "2026-10-17-oc-scripthammer-bb085874" / "report.md"
            report.parent.mkdir(parents=True)
            report.write_text("Permission denied: Docker was blocked. No changes were made.")
            state["lanes"]["oc-scripthammer-bb085874"] = {"repo": "own-app", "status": "started", "engine": "copilot", "ask": ASK,
                                                          "report": str(report), "started": now - 800, "task_key": "x"}
            dp.save_state(self.ctx.state_path, state)
            before = len(rec.calls)
            posted = dp.sweep(self.ctx, host, now)
            ask = self.state()["asks"][ASK]
            listed = [l for l in dp.open_question_lines(self.ctx, now) if l.startswith(ASK)]
            print("3 failed lane report    report posted to the chat %d time(s); ask status %r (before the fix: 'answered'), answer recorded: %s; on the hub's list: %s; open shows: %r" % (
                len(rec.calls) - before, ask["status"], "answer" in ask, ASK in self.ctx.inbox_path.read_text(), listed[0][:70] if listed else None))
            assert ask["status"] == "claimed" and "answer" not in ask and len(posted) == 1 and listed

            # 4. a later report to the answered ask is a follow-up, delivered once
            ok, msg = dp.post_ask(self.ctx, host, ASK, "sh-backlog-2 finished: 14 issues triaged.", now + 600)
            ask = self.state()["asks"][ASK]
            print("4 follow-up             first hub post on the failed ask: %s (status now %r)" % (msg, ask["status"]))
            before = len(rec.calls)
            ok, msg = dp.post_ask(self.ctx, host, ASK, "The second lane's own report, after that.", now + 900)
            ask = self.state()["asks"][ASK]
            print("                        later post on the answered ask: %r, %s; chat.inject calls: %d; follow-ups recorded: %s; ask still %r, answer kept: %s" % (
                msg, "accepted" if ok else "REFUSED", len(rec.calls) - before, [f["text"][:30] for f in ask.get("followups", [])], ask["status"],
                ask["answer"].startswith("sh-backlog-2")))
            assert ok and len(rec.calls) - before == 1 and len(ask["followups"]) == 1 and ask["status"] == "answered"


if __name__ == "__main__":
    sys.exit(0 if unittest.TextTestRunner(verbosity=0).run(Replay()).wasSuccessful() else 1)
