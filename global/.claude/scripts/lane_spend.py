#!/usr/bin/env python3
"""lane_spend.py: what each lane has cost today, in API-equivalent dollars, from the usage ledger.

  lane_spend.py [--day YYYY-MM-DD] [--top N] [--over USD] [--no-ingest] [--json] [--week-start EPOCH]

Why: 21 Oct the lanes ran up $303 of API-equivalent spend in one day ($85 on one mail lane redrafting a reply six times on Fable and
Opus) and nothing showed it until the end of the day. hub_digest.py prints the top lanes from here; lane_watch.sh wakes the hub once per
lane per day when one passes $25 (--over).

How a lane's sessions are found: first, ~/.claude/state/lanes/sessions/<lane name> (written by lane_session_hook.sh)
is checked for the session id. If not found, lanes/index.tsv holds the session id the lane started with (column 8).
A recycle (/clear) or a resume starts a new transcript, so a session the index does not claim is matched to its lane
by the lane's hand-off folder path (~/.claude/state/lanes/<date>-<name>/), which the re-prime message quotes in the
transcript's first lines. Sessions that belong to no lane (the hub's own, a subagent-only run) are left out of the
lane list and counted in "other"; the hub's own sessions (in the ~/repos project folder) are counted as "hub".

The hub's own use over a longer window (--week-start EPOCH, the weekly reset; --json only) is added under "hub" as "week": the
hub's dollars since then, the dollars of everything in the ledger since then (so the share is of all use), and, for the sessions a
person typed in (the ledger's 'human' prompts: not the muse poller or a dispatch pass), how many there were, the median context a turn
carried (input + cache writes + cache reads, main chain only: a subagent's turns are left out) and the median context of a session's
first turn. 29 Oct 2026: the hub was 40% of the week's use and nothing on the digest showed it.

Prices come from usage_ledger.py (its PRICES table: a yardstick, not money billed; the subscription is flat).
Output: one "<usd>\t<lane>\t<model families>" line per lane, most expensive first, then "total\t<usd>\t<usd outside lanes>".
$LANE_SPEND_HOME overrides the home folder (tests).
"""
import argparse
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_ledger as ul  # noqa: E402

FOLDER_RE = re.compile(r"/state/lanes/\d{4}-\d{2}-\d{2}-([A-Za-z0-9._-]+)/")
HEAD_BYTES = 200_000


def home():
    return os.environ.get("LANE_SPEND_HOME") or os.path.expanduser("~")


def read_index(path):
    """[(name, dir, session id)] from lanes/index.tsv, oldest first; short or blank rows skipped."""
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) >= 8 and c[1]:
                    rows.append((c[1], c[2], c[7]))
    except OSError:
        pass
    return rows


def lane_of_transcript(path):
    """The lane name whose hand-off folder the transcript's first lines quote, else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    m = FOLDER_RE.search(head.replace("\\/", "/"))
    return m.group(1) if m else None


def spend_by_session(conn, lo, hi):
    """{session: {family: usd}} for tokens stamped in [lo, hi)."""
    out = {}
    q = ("SELECT session, model, SUM(input), SUM(output), SUM(cache_5m), SUM(cache_1h), SUM(cache_read) "
         "FROM tokens WHERE ts >= ? AND ts < ? GROUP BY session, model")
    for sid, model, i, o, w5, w1, r in conn.execute(q, (lo, hi)):
        usd = ul.usd_of(model, i or 0, o or 0, w5 or 0, w1 or 0, r or 0)
        fam = ul.model_family(model)
        d = out.setdefault(sid, {})
        d[fam] = d.get(fam, 0.0) + usd
    return out


def read_sessions(h):
    """{session id: lane name} from ~/.claude/state/lanes/sessions/*."""
    out = {}
    dir_path = os.path.join(h, ".claude", "state", "lanes", "sessions")
    if not os.path.isdir(dir_path):
        return out
    for name in os.listdir(dir_path):
        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            continue
        p = os.path.join(dir_path, name)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    c = line.rstrip("\n").split("\t")
                    if c and c[0]:
                        out[c[0]] = name
        except OSError:
            pass
    return out


def lane_owners(h):
    """-> (owner {session id: lane name}, hand-off folders, lane names): the index's first session per lane, the session map over it."""
    rows = read_index(os.path.join(h, ".claude", "state", "lanes", "index.tsv"))
    owner = {sid: name for name, _d, sid in rows if sid and sid not in ("-", "?")}
    owner.update(read_sessions(h))  # session map wins
    return owner, {d for _n, d, _s in rows}, {n for n, _d, _s in rows}


def hub_transcript(h, sid):
    """Where a session started in ~/repos (the hub's own project folder) keeps its transcript."""
    return os.path.join(h, ".claude", "projects", ul.slugify(os.path.join(h, "repos")), sid + ".jsonl")


def lane_spend(day=None, ingest=True, h=None):
    """-> (lanes, other, hub): lanes = [(name, usd, {family: usd})] most expensive first;
    other = usd outside any lane or hub; hub = {family: usd}."""
    h = h or home()
    env = ul.Env(h)
    if ingest:
        try:
            ul.ingest(env)
        except Exception as exc:  # a stale ledger beats no alarm
            sys.stderr.write("lane_spend: ingest failed (%s); using the ledger as it is\n" % type(exc).__name__)
    if not os.path.exists(env.db_path):
        return [], 0.0, {}
    day = day or time.strftime("%Y-%m-%d")
    lo, hi = ul.day_bounds(day, day)
    conn = ul.connect(env)
    try:
        by_sid = spend_by_session(conn, lo, hi)
    finally:
        conn.close()
    owner, dirs, names = lane_owners(h)
    proj = os.path.join(h, ".claude", "projects")
    lanes, other, hub = {}, 0.0, {}
    for sid, fams in by_sid.items():
        n = owner.get(sid)
        if n is None:
            # check if it is the hub first
            if os.path.exists(hub_transcript(h, sid)):
                for f, v in fams.items():
                    hub[f] = hub.get(f, 0.0) + v
                continue
            # folder-path guess for others
            for d in dirs:
                p = os.path.join(proj, ul.slugify(d), sid + ".jsonl")
                if os.path.exists(p):
                    n = lane_of_transcript(p)
                    if n in names:
                        owner[sid] = n
                    break
        n = owner.get(sid)
        if n is None:
            other += sum(fams.values())
            continue
        d = lanes.setdefault(n, {})
        for f, v in fams.items():
            d[f] = d.get(f, 0.0) + v
    out = sorted(((n, sum(f.values()), f) for n, f in lanes.items()), key=lambda x: -x[1])
    return out, other, hub


def hub_window(conn, h, lo, hi):
    """The hub's own use in [lo, hi), as lane_spend() counts the hub: a session whose transcript is in the ~/repos project folder and
    that no lane claims (a lane started in ~/repos lands in the same ledger repo, 'repos (hub)', and is left out here).
    -> {since, usd, total, share, sessions, median_context, first_context}; the last two are None when no typed-in session ran.
    "total" is everything in the ledger in the window, so "share" is of all use. A session counts as typed in when the ledger saw a
    human prompt in the window; a session begun before `lo` has no first turn here."""
    by_sid = spend_by_session(conn, lo, hi)
    owner, _dirs, _names = lane_owners(h)
    hub = {sid for sid in by_sid if sid not in owner and os.path.exists(hub_transcript(h, sid))}
    usd = sum(sum(by_sid[sid].values()) for sid in hub)
    total = sum(sum(f.values()) for f in by_sid.values())
    typed = hub & {r[0] for r in conn.execute(
        "SELECT DISTINCT session FROM activity WHERE kind = 'human' AND ts >= ? AND ts < ?", (lo, hi))}
    ctx, first = [], {}
    q = ("SELECT session, COALESCE(input, 0) + COALESCE(cache_5m, 0) + COALESCE(cache_1h, 0) + COALESCE(cache_read, 0) "
         "FROM tokens WHERE sidechain = 0 AND ts >= ? AND ts < ? ORDER BY ts")
    for sid, c in conn.execute(q, (lo, hi)):
        if sid in typed:
            ctx.append(c)
            first.setdefault(sid, c)
    for sid in list(first):
        if conn.execute("SELECT 1 FROM tokens WHERE session = ? AND sidechain = 0 AND ts < ? LIMIT 1", (sid, lo)).fetchone():
            del first[sid]
    return {"since": int(lo), "usd": round(usd, 2), "total": round(total, 2), "share": round(usd / total, 4) if total > 0 else 0.0,
            "sessions": len(typed),
            "median_context": int(round(statistics.median(ctx))) if ctx else None,
            "first_context": int(round(statistics.median(first.values()))) if first else None}


def hub_week(since, until=None, h=None):
    """hub_window() from `since` (epoch seconds: the weekly reset) to `until` (default now); None when there is no ledger."""
    h = h or home()
    env = ul.Env(h)
    if not os.path.exists(env.db_path):
        return None
    conn = ul.connect(env)
    try:
        return hub_window(conn, h, int(since), int(until if until is not None else time.time()) + 1)
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", help="local YYYY-MM-DD (default today)")
    ap.add_argument("--top", type=int, default=0, help="only the N most expensive lanes")
    ap.add_argument("--over", type=float, help="print only lanes at or over this many dollars")
    ap.add_argument("--no-ingest", action="store_true", help="read the ledger as it is")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--week-start", type=float, metavar="EPOCH",
                    help="with --json, add the hub's use since this moment (the weekly reset) under hub.week")
    a = ap.parse_args(argv)
    if a.day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.day):
        print("lane_spend: --day must be YYYY-MM-DD", file=sys.stderr)
        return 2
    lanes, other, hub = lane_spend(a.day, ingest=not a.no_ingest)
    hub_total = sum(hub.values())
    total = sum(u for _n, u, _f in lanes) + other + hub_total
    if a.over is not None:
        lanes = [x for x in lanes if x[1] >= a.over]
    if a.top:
        lanes = lanes[:a.top]
    if a.json:
        hub_json = {"usd": round(hub_total, 2), "families": {k: round(v, 2) for k, v in hub.items()}}
        if a.week_start is not None:
            hub_json["week"] = hub_week(a.week_start)
        print(json.dumps({"lanes": [{"name": n, "usd": round(u, 2), "families": {k: round(v, 2) for k, v in f.items()}}
                                    for n, u, f in lanes],
                          "hub": hub_json, "other": round(other, 2), "total": round(total, 2)}))
        return 0
    for n, u, f in lanes:
        fam = "+".join(k for k, _v in sorted(f.items(), key=lambda kv: -kv[1]))
        print("%.2f\t%s\t%s" % (u, n, fam))
    if hub_total > 0:
        fam = "+".join(k for k, _v in sorted(hub.items(), key=lambda kv: -kv[1]))
        print("hub\t%.2f\t%s" % (hub_total, fam))
    print("total\t%.2f\t%.2f" % (total, other))
    return 0


if __name__ == "__main__":
    sys.exit(main())
