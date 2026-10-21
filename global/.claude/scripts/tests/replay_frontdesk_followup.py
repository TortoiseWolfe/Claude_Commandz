#!/usr/bin/env python3
"""Replay of the 17 Oct lane asks that were left "claimed" after their reports reached the chat, and of ask oc-a486228e (a note to Hatch),
through the current code. Works on a temp copy of the live ledger and a fake host: nothing here touches the live state, the chat or the hub.

  python3 replay_frontdesk_followup.py [ledger.json]            the replay
  python3 replay_frontdesk_followup.py --backfill [ledger.json]  settle the seven from their lane reports (use a copy; see backfill())

backfill() sets status answered, answer (the lane's report, marked as backfilled), answered_at (when the sweep closed the lane, else the
log line's minute) and "backfilled": true on each ask, and saves the old file beside the new one as <name>.before-backfill. To undo: move that file back."""
import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
from test_openclaw_pass import PassBase  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

STATE = Path.home() / ".claude/state"
SEVEN = ("oc-16cd92d0", "oc-d296ae6c", "oc-ab70e557", "oc-33876df9", "oc-11c7e8f1", "oc-bb085874", "oc-ef23efba")
HATCH = "oc-a486228e"
LOG = STATE / "openclaw-ask-results.log"


def logged_at(lane, log=LOG):
    """When the log says this lane's report was posted (to the minute), or None."""
    for line in log.read_text().splitlines()[::-1]:
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d) \| report \| %s .*report (?:posted to chat|handed to the hub)" % re.escape(lane), line)
        if m:
            return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M"))
    return None


def backfill(ctx, ask_ids, log=LOG):
    """-> list of ask ids settled. Only an ask that is still "claimed", whose lanes are all closed and delivered, with a report file, and
    a "report posted to chat" line (or closed_at) to date it. Anything short of that is left alone."""
    done = []
    with ctx.locked():
        state = dp.load_state(ctx.state_path)
        shutil.copy2(ctx.state_path, str(ctx.state_path) + ".before-backfill")
        for k in ask_ids:
            ask = dp.asks_of(state).get(k)
            lanes = [state["lanes"].get(n) for n in (ask or {}).get("lanes", [])]
            if not ask or ask.get("status") != "claimed" or not lanes or not all(l and l.get("delivered") and l.get("status") == "closed" for l in lanes):
                continue
            last = ask["lanes"][-1]
            report = Path(lanes[-1]["report"])
            at = lanes[-1].get("closed_at") or logged_at(last, log)
            if not report.is_file() or at is None:
                continue
            ask["answer"] = "(backfilled from the lane report) " + dp._report_text(str(report))
            ask["status"], ask["answered_at"], ask["backfilled"] = "answered", at, True
            dp._finish(ask, at, "lane report, backfilled")
            done.append(k)
        dp.save_state(ctx.state_path, state)
    return done


class Replay(PassBase):
    def runTest(self):
        ledger = Path(sys.argv[-1]) if sys.argv[-1].endswith(".json") else STATE / "openclaw-dispatch.json"
        old = json.loads(ledger.read_text())
        # the state as it was before the new sweep: the seven asks claimed, their lanes' notes not yet posted
        state = {"lanes": {}, "starts": [], "asks": {}}
        for k in SEVEN + (HATCH,):
            state["asks"][k] = dict(old["asks"][k])
            for n in old["asks"][k]["lanes"]:
                state["lanes"][n] = dict(old["lanes"][n], status="reported", delivered=False)
                state["lanes"][n].pop("closed_at", None)
        dp.save_state(self.ctx.state_path, state)
        n_chats = len(self.host.chats)
        posted = dp.sweep(self.ctx, self.host, time.time())
        after = self.state()["asks"]
        answered = [k for k in SEVEN if after[k]["status"] == "answered"]
        with_text = [k for k in SEVEN if after[k].get("answer") and after[k].get("answered_at")]
        print("1 posted lane reports   %d of 8 lane notes posted; of the 7 asks, %d now answered, %d carry the posted text and a time (before: 0 of 7)" % (len(posted), len(answered), len(with_text)))
        # backfill on a copy of the real ledger
        copy = Path(fresh_dir(self)) / "dispatch.json"
        shutil.copy2(ledger, copy)
        ctx2 = dp.Ctx(self.ctx.repos, copy, self.tmp / "lanes", self.senders, self.repos_txt, self.root, **self.private_paths())
        done = backfill(ctx2, SEVEN)
        b = dp.load_state(copy)["asks"]
        undone = json.loads(Path(str(copy) + ".before-backfill").read_text())["asks"]
        print("  backfill (on a copy)  %d of 7 settled; %d still 'claimed' in the saved original (reversible); again: %d more" % (
            len(done), sum(undone[k]["status"] == "claimed" for k in SEVEN), len(backfill(ctx2, SEVEN))))
        print("  e.g. %s answered_at %s: %s" % (done[0], time.strftime("%H:%M", time.localtime(b[done[0]]["answered_at"])), b[done[0]]["answer"][:90].replace("\n", " ")))
        # re-claim: the hub's lane.sh --ask on an already-claimed ask (sh-backlog-2 on oc-bb085874)
        st = dp.load_state(copy)
        st["asks"]["oc-bb085874"]["status"] = "claimed"
        dp.save_state(copy, st)
        with open(self.index, "a") as f:
            f.write("\t".join(["2026-10-17", "sh-backlog-2", str(self.root / "own-app"), "sonnet", "copilot", "-", str(self.tmp / "r.md"), "-", "1000"]) + "\n")
        ok, msg = dp.claim(ctx2, "oc-bb085874", "sh-backlog-2", 1060)
        got = dp.load_state(copy)["asks"]["oc-bb085874"]
        print("  re-claim sh-backlog-2 %s; lanes now %s (before: refused, lane never joined)" % ("accepted" if ok else "REFUSED: " + msg, got["lanes"]))
        # fix 2: the Hatch ask
        task = old["asks"][HATCH]["task"]
        self.host.spawned.clear()
        self.host.started.clear()
        ctx3 = dp.Ctx(self.ctx.repos, Path(fresh_dir(self)) / "d.json", self.tmp / "lanes", self.senders, self.repos_txt, self.root, hand_off_first=True, **self.private_paths())
        line, chat = dp.hand_off(ctx3, self.host, HATCH, "own-app", task, time.time())
        row = dp.load_state(ctx3.state_path)["asks"][HATCH]
        print("2 note to Hatch         ask %s (%r): passes started %d, lanes started %d; ask %s, on the hub's list: %s" % (
            HATCH, task[:60], len(self.host.spawned), len(self.host.started), row["status"], HATCH in ctx3.inbox_path.read_text()))
        print("  chat: %s" % chat[:120])


if __name__ == "__main__":
    sys.exit(0 if unittest.TextTestRunner(verbosity=0).run(Replay()).wasSuccessful() else 1)
