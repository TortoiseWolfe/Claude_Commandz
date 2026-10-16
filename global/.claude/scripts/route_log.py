#!/usr/bin/env python3
"""Route log (spec 002, FR-062): one line per intake item, local only, never any item text.

  route_log.py add --pack-id ID --row K-01 --class public|own|client [--hops 1,2] [--escalated] [--claude-started]
                   [--channel NAME]
  route_log.py report [--weeks N]            weekly kept share, escalated share and Claude sessions avoided

The log is ~/.claude/state/route-log.jsonl (override with ROUTE_LOG), mode 600, appended in one write so
two writers never interleave. Every value is a short token: free text is refused, so the log cannot carry
an item, a name or a secret. Python 3 stdlib only."""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOG = os.environ.get("ROUTE_LOG") or os.path.expanduser("~/.claude/state/route-log.jsonl")
TOKEN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
CLASSES = ("public", "own", "client")
FIELDS = {"ts", "pack_id", "row", "class", "hops", "escalated", "claude_started", "channel"}


def make_record(pack_id, row, cls, hops=(), escalated=False, claude_started=False, channel="unknown", ts=None):
    for name, v in (("pack_id", pack_id), ("row", row), ("channel", channel)):
        if not isinstance(v, str) or not TOKEN.match(v):
            raise ValueError(f"{name} must be a short token")
    if cls not in CLASSES:
        raise ValueError("class must be public, own or client")
    hops = list(hops)
    if not all(isinstance(h, int) and 0 < h < 100 for h in hops):
        raise ValueError("hops are chain positions")
    return {"ts": int(ts if ts is not None else time.time()), "pack_id": pack_id, "row": row, "class": cls,
            "hops": hops, "escalated": bool(escalated), "claude_started": bool(claude_started), "channel": channel}


def append(record, path=None):
    path = path or LOG
    if set(record) - FIELDS:
        raise ValueError("unknown field")
    line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def read(path=None):
    out = []
    try:
        with open(path or LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if isinstance(r, dict) and r.get("class") in CLASSES:
                        out.append(r)
                except ValueError:
                    continue   # a torn line is skipped, not fatal
    except FileNotFoundError:
        pass
    return out


def window_start(ts):
    """The Wednesday 12:00 ET that opened the subscription week containing `ts`."""
    et = ZoneInfo("America/New_York")
    d = datetime.fromtimestamp(ts, et)
    start = (d - timedelta(days=(d.weekday() - 2) % 7)).replace(hour=12, minute=0, second=0, microsecond=0)
    if start > d:
        start -= timedelta(days=7)
    return start.strftime("%Y-%m-%d")


def report(records, weeks=4):
    by = defaultdict(lambda: {"items": 0, "kept": 0, "escalated": 0, "claude_started": 0})
    for r in records:
        w = by[window_start(r["ts"])]
        w["items"] += 1
        w["escalated"] += bool(r.get("escalated"))
        w["claude_started"] += bool(r.get("claude_started"))
        w["kept"] += not r.get("escalated")
    rows = []
    for k in sorted(by)[-weeks:]:
        w = by[k]
        rows.append({"window": k, **w, "kept_share": round(w["kept"] / w["items"], 3) if w["items"] else None,
                     "claude_avoided": w["items"] - w["claude_started"]})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--pack-id", required=True)
    a.add_argument("--row", required=True)
    a.add_argument("--class", dest="cls", required=True)
    a.add_argument("--hops", default="")
    a.add_argument("--escalated", action="store_true")
    a.add_argument("--claude-started", action="store_true")
    a.add_argument("--channel", default="unknown")
    r = sub.add_parser("report")
    r.add_argument("--weeks", type=int, default=4)
    args = ap.parse_args(argv)
    if args.cmd == "add":
        try:
            hops = [int(h) for h in args.hops.split(",") if h]
            append(make_record(args.pack_id, args.row, args.cls, hops, args.escalated, args.claude_started, args.channel))
        except ValueError as e:
            print(f"route_log: {e}", file=sys.stderr)
            return 2
        return 0
    print(json.dumps(report(read(), args.weeks), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
