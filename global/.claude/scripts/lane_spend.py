#!/usr/bin/env python3
"""lane_spend.py: what each lane has cost today, in API-equivalent dollars, from the usage ledger.

  lane_spend.py [--day YYYY-MM-DD] [--top N] [--over USD] [--no-ingest] [--json]

Why: 21 Oct the lanes ran up $303 of API-equivalent spend in one day ($85 on one mail lane redrafting a reply six times on Fable and
Opus) and nothing showed it until the end of the day. hub_digest.py prints the top lanes from here; lane_watch.sh wakes the hub once per
lane per day when one passes $25 (--over).

How a lane's sessions are found: lanes/index.tsv holds the session id the lane started with (column 8). A recycle (/clear) or a resume
starts a new transcript, so a session the index does not claim is matched to its lane by the lane's hand-off folder path
(~/.claude/state/lanes/<date>-<name>/), which the re-prime message quotes in the transcript's first lines. Sessions that belong to no lane
(the hub's own, a subagent-only run) are left out of the lane list and counted in "other".

Prices come from usage_ledger.py (its PRICES table: a yardstick, not money billed; the subscription is flat).
Output: one "<usd>\t<lane>\t<model families>" line per lane, most expensive first, then "total\t<usd>\t<usd outside lanes>".
$LANE_SPEND_HOME overrides the home folder (tests).
"""
import argparse
import json
import os
import re
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


def lane_spend(day=None, ingest=True, h=None):
    """-> (lanes, other): lanes = [(name, usd, {family: usd})] most expensive first; other = usd outside any lane."""
    h = h or home()
    env = ul.Env(h)
    if ingest:
        try:
            ul.ingest(env)
        except Exception as exc:  # a stale ledger beats no alarm
            sys.stderr.write("lane_spend: ingest failed (%s); using the ledger as it is\n" % type(exc).__name__)
    if not os.path.exists(env.db_path):
        return [], 0.0
    day = day or time.strftime("%Y-%m-%d")
    lo, hi = ul.day_bounds(day, day)
    conn = ul.connect(env)
    try:
        by_sid = spend_by_session(conn, lo, hi)
    finally:
        conn.close()
    rows = read_index(os.path.join(h, ".claude", "state", "lanes", "index.tsv"))
    owner = {sid: name for name, _d, sid in rows if sid and sid not in ("-", "?")}
    dirs = {d for _n, d, _s in rows}
    names = {n for n, _d, _s in rows}
    proj = os.path.join(h, ".claude", "projects")
    for sid in by_sid:
        if sid in owner:
            continue
        for d in dirs:
            p = os.path.join(proj, ul.slugify(d), sid + ".jsonl")
            if os.path.exists(p):
                n = lane_of_transcript(p)
                if n in names:
                    owner[sid] = n
                break
    lanes, other = {}, 0.0
    for sid, fams in by_sid.items():
        n = owner.get(sid)
        if n is None:
            other += sum(fams.values())
            continue
        d = lanes.setdefault(n, {})
        for f, v in fams.items():
            d[f] = d.get(f, 0.0) + v
    out = sorted(((n, sum(f.values()), f) for n, f in lanes.items()), key=lambda x: -x[1])
    return out, other


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", help="local YYYY-MM-DD (default today)")
    ap.add_argument("--top", type=int, default=0, help="only the N most expensive lanes")
    ap.add_argument("--over", type=float, help="print only lanes at or over this many dollars")
    ap.add_argument("--no-ingest", action="store_true", help="read the ledger as it is")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.day):
        print("lane_spend: --day must be YYYY-MM-DD", file=sys.stderr)
        return 2
    lanes, other = lane_spend(a.day, ingest=not a.no_ingest)
    total = sum(u for _n, u, _f in lanes) + other
    if a.over is not None:
        lanes = [x for x in lanes if x[1] >= a.over]
    if a.top:
        lanes = lanes[:a.top]
    if a.json:
        print(json.dumps({"lanes": [{"name": n, "usd": round(u, 2), "families": {k: round(v, 2) for k, v in f.items()}}
                                    for n, u, f in lanes], "other": round(other, 2), "total": round(total, 2)}))
        return 0
    for n, u, f in lanes:
        fam = "+".join(k for k, _v in sorted(f.items(), key=lambda kv: -kv[1]))
        print("%.2f\t%s\t%s" % (u, n, fam))
    print("total\t%.2f\t%.2f" % (total, other))
    return 0


if __name__ == "__main__":
    sys.exit(main())
