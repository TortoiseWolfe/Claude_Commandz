#!/usr/bin/env python3
"""Check that every message Jonathan sent from the chat reached somebody, and print each one that did not.

  openclaw_reconcile.py [--since STAMP|today|all] [--file] [--quiet]

Every tray message of his (the intake folder) must end in one of three places:
  (a) delivered into a live lane (a "reply X | sent" line: the send is only called sent once his words are in the lane's transcript)
  (b) a lane started or restarted carrying his words (started, restarted as, a pass that a lane claimed)
  (c) on the hub's list: an ask the hub holds open, or one the hub answered itself, or a muse-inbox line
The receptionist answering a plain chat message itself, a menu verb it carried out (lanes, stop, close) and a question it asked him
back (which lane did he mean) also count as delivered. Anything else is undelivered: a refusal, "nothing run", a reply meant for a lane
that a question lane answered instead, an answer that says it could not forward him, a pass whose lanes all failed with no answer, or
a message with no trace at all once it is past the poller's age limit. (19 Oct 3:34-3:57 PM: six of his replies to a closed lane bounced
and three markers were refused, and no check noticed.)

It prints one "undelivered | ..." line per message (oldest first) and exits 1 when there are any, 0 when there are none. With --file it
puts each one on the hub's list (his words whole, waking the hub) and logs a "reconcile" line, so the next run counts it as (c).
--since: "today" (midnight), "all", or a unix time; the default is the stamp in openclaw-reconcile.json, which the first run seeds with
the time it ran, so messages handled by hand before the reconciler existed are not filed again.
lane_watch.sh runs it on every poll (no --file: the hub is woken with the list); openclaw_status_refresh.sh runs it with --file.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openclaw_dispatch as dispatch  # noqa: E402
import openclaw_pickup as pickup  # noqa: E402

GRACE = pickup.ACT_MAX_AGE + 2 * 60  # a message younger than this with no trace may still be waiting for the model's reply
PASS_GRACE = 10 * 60  # a pass past its deadline by this much, still unanswered, has dropped him
ID_TAIL_RE = re.compile(r" \| (oc-[0-9a-f]{8})\s*$")
ASK_IN_LINE_RE = re.compile(r"\(ask (oc-[0-9a-f]{8})")
NO_SUCH_LANE_RE = re.compile(r" \| reply \| no such lane\b")
STAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d) \| ")


def _clock(ts):
    """12-hour time for Jonathan: "3:35 PM"."""
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")


def messages(intake_dir, since, until):
    """His tray messages with since <= ts < until, oldest first: [{"sha", "id", "ts", "text"}]."""
    out = []
    for f in Path(intake_dir).glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("source") != pickup.ACT_SOURCE or not isinstance(d.get("text"), str):
            continue
        try:
            ts = int(float(d.get("ts")))
        except (TypeError, ValueError):
            continue
        sha = d.get("sha256_of_original") or ""
        if since <= ts < until and re.fullmatch(r"[0-9a-f]{8,}", sha):
            out.append({"sha": sha, "id": dispatch.ask_id_for(sha), "ts": ts, "text": d["text"]})
    out.sort(key=lambda m: m["ts"])
    return out


def ask_ids(m):
    """Every ask id his message can be filed under: its own, the unsettled one (sha and time), and hub_has_it's sibling of each."""
    own = m["id"]
    unsettled = dispatch.ask_id_for(hashlib.sha256(("%s %s" % (m["sha"], m["ts"])).encode("utf-8")).hexdigest())
    ids = [own, unsettled]
    return ids + [dispatch.ask_id_for(hashlib.sha256((i + ":hub").encode("utf-8")).hexdigest()) for i in ids]


def judge_ask(ask, now, reply_for_lane=False):
    """-> None when this ask is delivered (or still on its way), else why it is not."""
    status = ask.get("status")
    answer = ask.get("answer") or ""
    if reply_for_lane and ask.get("fallback_started_at"):
        return "his reply was meant for a lane, and a question lane answered it instead (%s)" % ", ".join(ask.get("lanes") or ["no lane"])
    if status in dispatch.OPEN_ASK_STATUSES:
        if status == "fallback-failed":
            return "the question lane for it failed and nobody answered"
        return None  # (c): the hub holds it open
    if status == "pending":
        if now > float(ask.get("deadline") or 0) + PASS_GRACE:
            return "the pass never answered it"
        return None
    if status in ("claimed", "fallback"):
        lanes = ask.get("lanes") or []
        return None if lanes or now <= float(ask.get("claimed_at") or ask.get("created") or 0) + PASS_GRACE else "claimed, but no lane started"
    if status == "answered" or answer:
        if dispatch.NOT_FORWARDED_RE.search(answer):
            return "the answer said it could not forward him"
        return None
    return None if status else "no record of it on the hub's list"


def judge_line(line, m, asks, state, now, inbox_text):
    """-> None when this result line shows the message delivered, else why not."""
    kind = dispatch.delivery_of(line)
    ids = ASK_IN_LINE_RE.findall(line) + ask_ids(m)
    found = [asks[i] for i in dict.fromkeys(ids) if i in asks]
    if NO_SUCH_LANE_RE.search(line):  # meant for a lane that could not be reached: it is delivered only if the hub (not a question lane) has it
        if not found:
            return "his reply was meant for a lane that could not be reached, and nothing holds it"
        whys = [judge_ask(a, now, reply_for_lane=True) for a in found]
        return None if None in whys else whys[0]
    if kind in ("lane", "held", "frontdesk"):
        return None
    if kind in ("pass", "hub"):
        if found:
            whys = [_with_lanes(judge_ask(a, now), a, state) for a in found]
            return None if None in whys else whys[0]
        if kind == "hub" and m["id"] in inbox_text:
            return None  # a muse-inbox line (a "hub:" message or a stale typed ask)
        return "the line says the hub has it, but no ask or inbox line holds it"
    parts = line.split(" | ")
    return (" | ".join(parts[1:3]) if len(parts) > 2 else line)[:160]


def _with_lanes(why, ask, state):
    """A claimed ask whose every lane failed, with no answer, is undelivered (19 Oct 3:57 PM: oc-9af92153 sat lane-failed 15 minutes)."""
    if why is None and ask.get("status") in ("claimed", "fallback") and not ask.get("answer") and dispatch._ask_failed(state, ask):
        return "every lane it went to failed, and nobody answered"
    return why


def reconcile(ctx, intake_dir, since, now, history=None):
    """-> (undelivered, counts): undelivered is [(message, why)], oldest first. history: Main session's messages (tests, or a read of the
    chat), used only for a message with no trace, to tell a plain chat answer from a drop."""
    state = dispatch.load_state(ctx.state_path)
    asks = dispatch.asks_of(state)
    try:
        log_lines = Path(ctx.results_log).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        log_lines = []
    try:
        inbox_text = Path(ctx.inbox_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        inbox_text = ""
    pending = {e["sha"] for e in pickup.load_pending(ctx.pending_path)}
    by_id = {}
    for ln in log_lines:
        t = ID_TAIL_RE.search(ln)
        if t:
            by_id.setdefault(t.group(1), []).append(ln)
    unsettled = {}
    for ln in log_lines:  # unsettled lines from before 19 Oct carried no oc- tail: they name the ask instead
        for i in ASK_IN_LINE_RE.findall(ln):
            unsettled.setdefault(i, []).append(ln)
    out, counts = [], {"delivered": 0, "in flight": 0, "undelivered": 0, "no trace (before the fix)": 0}
    for m in messages(intake_dir, since, now):
        traces = by_id.get(m["id"], []) + [l for i in ask_ids(m)[1:] for l in unsettled.get(i, [])]
        if not traces:
            if m["sha"] in pending or now - m["ts"] < GRACE:
                counts["in flight"] += 1
                continue
            why = _no_trace(m, history, now)
            if why is None:
                counts["delivered"] += 1
                continue
            if why == "unchecked":
                counts["no trace (before the fix)"] += 1
                continue
            out.append((m, why))
            counts["undelivered"] += 1
            continue
        why = judge_line(traces[-1], m, asks, state, now, inbox_text)  # the newest trace is where the message ended
        if why is None:
            counts["delivered"] += 1
        else:
            out.append((m, why))
            counts["undelivered"] += 1
    return out, counts


def _no_trace(m, history, now):
    """A message the poller left no line for. Since 19 Oct every message it settles leaves one, so none is a drop; before that a plain
    chat answer left none, and only Main session tells the two apart. -> None (answered), "unchecked", or why it is a drop."""
    if history is not None:
        status, got = pickup.find_reply(history, {"sha": m["sha"], "ts": m["ts"], "text": m["text"]}, now)
        if status == "reply":
            job, bad = pickup.marker_job(got)
            text = " ".join(x["text"] for x in got)
            if job or bad:
                return "the receptionist's reply had a marker that never ran"
            if dispatch.NOT_FORWARDED_RE.search(text) or pickup.PROMISED_RE.search(text):
                return "the receptionist promised a hand-off it never made"
            return None
        if status == "none" and "not in Main session" not in got:
            return "no reply to it in Main session (%s)" % got
    if m["ts"] < FIXED_AT:
        return "unchecked"
    return "no trace: the front desk never settled it"


FIXED_AT = 1792447200  # 19 Oct 2026 6:00 PM EDT: from here on every message the poller settles leaves a result line


def file_all(ctx, found, now):
    """Put each undelivered message on the hub's list and log a reconcile line for it. -> the ask ids."""
    filed = []
    for m, why in found:
        ask_id = dispatch.hub_has_it(ctx, m["id"], m["text"], now, ("reconcile: " + why)[:200])
        line = "%s | reconcile | undelivered (%s): on the hub's list (ask %s) | %s" % (dispatch._stamp(now), why[:120], ask_id, m["id"])
        dispatch._append(ctx.results_log, line)
        filed.append(ask_id)
    return filed


def stamp_path(ctx):
    return Path(ctx.state_path).with_name("openclaw-reconcile.json")


def default_since(ctx, now):
    p = stamp_path(ctx)
    try:
        return float(json.loads(p.read_text())["since"])
    except (OSError, ValueError, KeyError, TypeError):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"since": now, "seeded": dispatch._stamp(now)}) + "\n")
        return now


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", default=None)
    ap.add_argument("--file", action="store_true", help="put each undelivered message on the hub's list")
    ap.add_argument("--quiet", action="store_true", help="print only undelivered lines")
    ap.add_argument("--intake", default=pickup.DEFAULT_DEST)
    ap.add_argument("--read-chat", type=int, default=0, metavar="N",
                    help="read the newest N messages of Main session (a read, it creates nothing) to check messages that left no trace")
    a = ap.parse_args(argv)
    now = time.time()
    ctx = dispatch.Ctx({}, state_path=dispatch.state_file())
    if a.since == "all":
        since = 0
    elif a.since == "today":
        since = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    elif a.since:
        since = float(a.since)
    else:
        since = default_since(ctx, now)
    history = pickup.Host().main_history(a.read_chat) if a.read_chat else None
    found, counts = reconcile(ctx, a.intake, since, now, history)
    for m, why in found:
        print("undelivered | %s | %s | %s | %s" % (_clock(m["ts"]), m["id"], why, json.dumps(" ".join(m["text"].split())[:80])))
    if a.file and found:
        print("filed with the hub: %s" % ", ".join(file_all(ctx, found, now)))
    if not a.quiet:
        print("reconcile since %s: %s" % (_clock(since) if since else "the start",
                                          ", ".join("%d %s" % (n, k) for k, n in counts.items())))
    return 1 if found and not a.file else 0


if __name__ == "__main__":
    sys.exit(main())
