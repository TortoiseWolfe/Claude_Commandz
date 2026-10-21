#!/usr/bin/env python3
"""Replay of the 17 Oct question asks (the live ledger plus the saved chat context of each) through the current ask_question, on a
temp copy of the state and a fake host: nothing here touches the live state, the chat or the hub.

  python3 replay_frontdesk_17oct.py [ledger.json] [context_dir]

Prints one line per change, with the old count from the ledger beside the new count from the replay."""
import json
import re
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
LEDGER = Path(sys.argv[1]) if len(sys.argv) > 1 else STATE / "openclaw-dispatch.json"
CONTEXTS = Path(sys.argv[2]) if len(sys.argv) > 2 else STATE / "lanes/context"
MAX_BURST = 5
QUIET, CAP, POLL = 60, 180, 30  # lane_watch.sh: LANE_WATCH_BATCH_QUIET, LANE_WATCH_BATCH_MAX, LANE_WATCH_POLL


def bursts(times, quiet=QUIET, cap=CAP):
    """Wakes after the hold: one per group of touches that keep arriving inside `quiet` seconds, a group ends at `cap` seconds."""
    out, start, last = [], None, None
    for t in sorted(times):
        if start is not None and (t - last >= quiet or t - start >= cap):
            out.append(start)
            start = None
        if start is None:
            start = t
        last = t
    if start is not None:
        out.append(start)
    return len(out)


class Replay(PassBase):
    def runTest(self):
        old = json.loads(LEDGER.read_text())["asks"]
        rows = sorted(((k, a) for k, a in old.items() if a.get("kind") == "question"), key=lambda r: r[1]["created"])
        self.ctx.context_dir = Path(fresh_dir(self))
        self.host.behave = None
        chats, line_of, long_words, exact_words, sent = {}, {}, 0, 0, []
        repeats = []  # (ask id, reply) for an exact re-send of a question that is still open
        for k, a in rows:
            c = json.loads((CONTEXTS / (k + ".json")).read_text()) if (CONTEXTS / (k + ".json")).exists() else None
            # the hub answers (in the old ledger) before this moment: settle what the new code would still be holding
            state = self.state()
            for ok_, oa in dp.asks_of(state).items():
                oldans = old[ok_].get("answered_at")
                if oa["status"] in ("deferred", "fallback", "fallback-lane") and oldans and oldans <= a["created"]:
                    state["last_hub_post_at"] = oldans
                    dp.save_state(self.ctx.state_path, state)
                    dp.settle_asks(self.ctx, ok_, "replay: the hub answered", oldans)
                    state = self.state()
            before = len(self.host.spawned)
            marker = a["task"]
            sent.append((k, a["created"]))
            try:
                line, chat = dp.ask_question(self.ctx, self.host, k, marker, a["created"], c)
            except Exception as e:  # a failure of the new code is a finding, not a skipped row
                self.fail("ask %s: %r" % (k, e))
            new = dp.asks_of(self.state()).get(k)
            words = dp.his_words(c, " ".join(marker.split()))
            chats[k] = chat
            line_of[k] = (line, new)
            if len(words) > 300:
                long_words += 1
                exact_words += bool(new and new.get("task") == words)
            if len(sent) % 13 == 5 and new is not None and new["status"] in dp.UNANSWERED_STATUSES:  # he sends the same words again
                again = dp.ask_question(self.ctx, self.host, "oc-%08x" % (len(sent) + 4096), marker, a["created"] + 90, c)
                repeats.append((k, again))
            if len(self.host.spawned) > before:  # a pass started: let it end with no answer, the way 80 of 98 did
                self.host.result = (0, "")
                dp.run_pass(self.ctx, self.host, k, a["created"] + 20)
                for ob in dp.fallback_due(self.ctx, self.host, a["created"] + dp.ASK_WAIT + 1):
                    pass
        state = self.state()
        mine = {k: v for k, v in dp.asks_of(state).items() if v.get("kind") == "question"}
        # --- change 1: passes
        old_passes = sum(1 for a in old.values() if a.get("kind") == "question" and a.get("pass"))
        new_passes = len(self.host.spawned)
        # --- change 2: words verbatim
        verb = sum(1 for k, v in mine.items() if v["task"] == dp.his_words(json.loads((CONTEXTS / (k + ".json")).read_text()), v["task"]) and v["task"] != v["marker"])
        hist_ok = sum(1 for k in mine if (CONTEXTS / (k + ".json")).exists())
        # --- change 3: repeats
        said = [r for r in repeats if r[1][1] and "Already with" in r[1][1]]
        filed = sum(1 for v in dp.asks_of(state).values() if v.get("kind") == "question") - len(rows)
        # --- change 4: wakes
        wake_times = [v["deferred_at"] for v in mine.values() if v.get("deferred_at")]
        old_wakes = len({int(t // POLL) for t in wake_times})
        new_wakes = bursts(wake_times)
        # --- change 5: cut
        # --- change 6: passes ended without answer
        ended = [k for k, v in mine.items() if v.get("pass") is not None or v.get("hub_list_at")]
        told = [m for m in self.host.chats if "your question is with the hub now" in m]
        # --- change 7: cap
        base = ([v["task"] for v in mine.values() if dp.pass_kind(v["task"]) == "facts"] or ["how many open issues does own-app have?"])[0]
        t0 = rows[-1][1]["created"] + 3600
        replies = []
        for i in range(MAX_BURST):  # a burst: more facts questions in ten seconds than the pass takes at once (the 17 Oct data holds only 3 facts questions)
            replies.append(dp.ask_question(self.ctx, self.host, "oc-%08x" % (9000 + i), "%s (take %d)" % (base, i), t0 + 2 * i, None)[1] or "")
        cap_msgs = sum(1 for c in replies if "already has %d questions" % dp.MAX_QUESTIONS in c)
        capped = [v for v in dp.asks_of(self.state()).values() if v.get("route") == "hub, pass full"]
        # --- change 8: finish
        answered = [k for k, v in mine.items() if v["status"] == "answered"]
        finished = [k for k in answered if mine[k].get("finished_at")]
        print("replayed %d question asks from 17 Oct, plus %d exact re-sends and a burst of %d" % (len(rows), len(repeats), MAX_BURST))
        print("1 passes started   old %d  new %d (of %d questions)" % (old_passes, new_passes, len(rows)))
        print("2 words verbatim   %d of %d asks hold his whole message (%d had saved chat context)" % (verb, len(mine), hist_ok))
        print("3 repeats          %d exact re-sends refused, %d told 'Already with the hub since', %d dropped silently; reworded copies filed: %d" % (len(repeats), len(said), len(repeats) - len(said), filed))
        inside = sum(1 for a_, b_ in zip(sorted(wake_times), sorted(wake_times)[1:]) if b_ - a_ < QUIET)
        print("4 hub wakes        %d hub-list touches that day: old %d wakes (one per 30 s poll that saw a touch), new %d (quiet %d s, cap %d s); %d touches landed inside a burst; a burst of 5 touches 40 s apart: old %d wakes, new %d" % (len(wake_times), old_wakes, new_wakes, QUIET, CAP, inside, len({int(t // POLL) for t in (0, 40, 80, 120, 160)}), bursts([0, 40, 80, 120, 160])))
        print("5 long messages    %d over 300 characters, %d stored whole" % (long_words, exact_words))
        print("6 passes ended with no answer: %d, chat lines sent: %d" % (len(ended), len(told)))
        print("7 cap message      %d times in a burst of %d facts questions, %d filed on the hub's list" % (cap_msgs, MAX_BURST, len(capped)))
        print("8 finish times     %d of %d answered asks carry one" % (len(finished), len(answered)))
        for k, again in repeats[:2]:
            print("   repeat reply:", (again[1] or "")[:150])


if __name__ == "__main__":
    r = unittest.TextTestRunner(verbosity=0).run(Replay())
    sys.exit(0 if r.wasSuccessful() else 1)
