#!/usr/bin/env python3
"""Is a lane's report.md still the last word? Prints one word:
  fresh  the report is newer than everything the lane was told, and no background job is running
  input  the lane got input after the report was written (lane.sh --send, a forwarded chat message, any typed turn)
  job    a background command or agent the lane started has not reported back
  ?      no transcript to read (a headless lane, or the session is not known): the caller falls back to "the file exists"

19 Oct: the hub closed tigard-trace because lane.sh --status said "report:yes idle". The report was from 12:20 PM; at 12:40 PM Jonathan's
complaint was forwarded into the lane, and by 1:05 PM it was running the test suite in the background. Closing it killed that work.

usage: lane_report_state.py <report.md> <transcript.jsonl>"""

import json
import os
import re
import sys
from datetime import datetime

LOST_JOB_HOURS = 12  # a background job with no word back after this long is lost, not pending: it must not hold a report stale forever
NOT_INPUT = ("This session is being continued", "<task-notification>", "<system-reminder>", "[SYSTEM NOTIFICATION")
TOOL_USE_ID = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")


def epoch(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_input(text):
    text = text.strip()
    return bool(text) and not text.startswith(NOT_INPUT)


def report_state(report, transcript, now=None):
    try:
        rep_ts = os.stat(report).st_mtime
        with open(transcript, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return "?"
    now = now if now is not None else datetime.now().timestamp()
    last_input, started, done = 0.0, {}, set()
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        ts, kind = epoch(o.get("timestamp")), o.get("type")
        if ts is None:
            continue
        if kind == "user" and not o.get("isMeta") and not o.get("isSidechain"):
            if is_input(text_of(o.get("message", {}).get("content"))):
                last_input = max(last_input, ts)
        elif kind == "queue-operation" and o.get("operation") == "enqueue":
            body = text_of(o.get("content"))
            if body.lstrip().startswith("<task-notification>"):
                done.update(TOOL_USE_ID.findall(body))
            elif is_input(body):
                last_input = max(last_input, ts)  # typed while the lane was mid-turn: queued, then delivered
        elif kind == "assistant" and isinstance(o.get("message", {}).get("content"), list):
            for b in o["message"]["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use" and (b.get("input") or {}).get("run_in_background"):
                    started[b.get("id")] = ts
    if last_input > rep_ts:
        return "input"
    if any(i not in done and now - ts < LOST_JOB_HOURS * 3600 for i, ts in started.items()):
        return "job"
    return "fresh"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: lane_report_state.py <report.md> <transcript.jsonl>")
    print(report_state(sys.argv[1], sys.argv[2]))
