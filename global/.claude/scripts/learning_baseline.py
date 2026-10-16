#!/usr/bin/env python3
"""Learning-loop baselines (spec 002, Phase 1): one record per subscription week, written once the week is over.

  learning_baseline.py observe [--now ISO] [--week YYYY-MM-DD]   write the last finished week's record if it is missing
  learning_baseline.py show                                      print the records

Weeks run Wednesday 12:00 ET to the next Wednesday 12:00 ET, the same windows route_log.py reports on.
Each record holds three numbers, so a later change can be judged against the week before it:
  m1  tokens in one main-thread turn (median, p90) and the count of turns over 200K, from the usage ledger
  m2  compactions (the ledger's context report, when the hub report script is present, else null)
  m3  first-pass review rate on director items (hub report script, date-granular, else null)
Records go to ~/.claude/state/learning/baselines.jsonl (mode 600, override LEARNING_BASELINES). A week that is
already recorded is never rewritten. No text from any session is read: only token counts and counters.
Python 3 stdlib only."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OUT = os.environ.get("LEARNING_BASELINES") or os.path.expanduser("~/.claude/state/learning/baselines.jsonl")
DB = os.environ.get("LEDGER_DB") or os.path.expanduser("~/.local/share/ledger/ledger.db")
REPORT = os.environ.get("MODEL_QUALITY_REPORT") or os.path.expanduser("~/repos/hub/scripts/model_quality_report.py")
BIG = 200_000


def week_bounds(ts):
    """(start, end) datetimes of the Wednesday-12:00-ET week containing epoch `ts`."""
    d = datetime.fromtimestamp(ts, ET)
    start = (d - timedelta(days=(d.weekday() - 2) % 7)).replace(hour=12, minute=0, second=0, microsecond=0)
    if start > d:
        start -= timedelta(days=7)
    return start, start + timedelta(days=7)


def last_finished_week(now_ts):
    start, _ = week_bounds(now_ts)
    return start - timedelta(days=7), start


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, round(p * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def m1(db, start, end):
    """Main-thread turn sizes in [start, end): input + cache writes + cache reads, sidechains left out."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = con.execute(
            "SELECT input + cache_5m + cache_1h + cache_read FROM tokens WHERE sidechain = 0 AND ts >= ? AND ts < ?",
            (int(start.timestamp()), int(end.timestamp())),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    vals = sorted(r[0] or 0 for r in rows)
    return {
        "turns": len(vals),
        "median": _pct(vals, 0.5),
        "p90": _pct(vals, 0.9),
        "over_200k": sum(1 for v in vals if v > BIG),
    }


def _hub_report(report, start, end):
    if not report or not os.path.isfile(report):
        return None
    cmd = [sys.executable, report, "--json",
           "--since", start.strftime("%Y-%m-%d"), "--until", end.strftime("%Y-%m-%d"),
           "--context-since", start.isoformat(), "--context-until", end.isoformat()]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
        return json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def m2_m3(report, start, end):
    d = _hub_report(report, start, end)
    if not d:
        return None, None
    try:
        c = d["context_load"]["report_window"]["compact"]
        m2 = {"count": c["count"], "auto": c["auto"], "manual": c["manual"]}
    except (KeyError, TypeError):
        m2 = None
    try:
        pairs = d["director"]["by_tier_reviewer"].values()
        n, ok = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
        m3 = {"reviewed": n, "first_pass_ok": ok, "rate": round(ok / n, 3) if n else None}
    except (KeyError, TypeError, IndexError):
        m3 = None
    return m2, m3


def recorded(path):
    out = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.add(json.loads(line)["week"])
                except (ValueError, KeyError, TypeError):
                    pass
    except OSError:
        pass
    return out


def observe(now_ts=None, week=None, db=None, out=None, report=None):
    """Write the record for the last finished week (or the named one). Returns the record, or None when that
    week is already recorded."""
    db, out = db or DB, out or OUT
    now_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    if week:
        start = datetime.strptime(week, "%Y-%m-%d").replace(hour=12, tzinfo=ET)
        end = start + timedelta(days=7)
    else:
        start, end = last_finished_week(now_ts)
    label = start.strftime("%Y-%m-%d")
    if label in recorded(out):
        return None
    rep = REPORT if report is None else report
    mm2, mm3 = m2_m3(rep, start, end)
    rec = {"week": label, "end": end.strftime("%Y-%m-%d"), "m1": m1(db, start, end), "m2": mm2, "m3": mm3,
           "written": int(now_ts)}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("observe")
    o.add_argument("--now", help="pretend it is this moment (ISO with offset)")
    o.add_argument("--week", help="record this week (its Wednesday, YYYY-MM-DD) instead of the last finished one")
    sub.add_parser("show")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        try:
            print(open(OUT, encoding="utf-8").read(), end="")
        except OSError:
            pass
        return 0
    now = datetime.fromisoformat(a.now).timestamp() if a.now else None
    rec = observe(now_ts=now, week=a.week)
    print(json.dumps(rec) if rec else "already recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
