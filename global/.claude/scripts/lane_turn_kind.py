#!/usr/bin/env python3
"""What did a lane's last finished turn do? Prints one word:
  monitor  the turn was started by a Monitor event (a progress line from a command the lane is watching) and the lane asked nothing
  real     anything else: a turn Jonathan or the hub started, a finished background job, or any turn that asks a question
  ?        no transcript to read: the caller treats it as real

19 Oct 11:59 PM: he-lift-move watched a 35-minute download with Monitor. Each progress line started a 2-second turn ("Baked for 2s"), and
lane_watch.sh woke the hub for every one ("ended a turn without a new report"): two wakes in two minutes, nothing to do.

A Monitor event reaches the transcript as a user entry whose text begins with <task-notification> and whose summary says "Monitor event".
A finished background command or agent sends the same wrapper without those words, so it stays real. The words after the last real input
are the lane's answer: a "?" in them, or an AskUserQuestion call, means it is asking and the hub must hear.

28 Oct: the tower lane bandwagon-live used a scheduled job (CronCreate) to check Google Drive every 10 minutes, and each check woke the hub
for nothing. A scheduled prompt is a user entry marked "isMeta" with plain text. It starts a new turn only when the last turn has ended (a
"turn_duration" or "stop_hook_summary" entry); a meta entry in the middle of a turn, such as skill text, is ignored.

usage: lane_turn_kind.py <transcript.jsonl>"""

import json
import sys

TAIL_LINES = 600  # a turn is a few dozen entries; a monitor tick far fewer


def blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def turn_kind(lines):
    trigger, answer, asks = None, [], False
    scheduled, ended = False, False
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        kind, msg = o.get("type"), o.get("message")
        if kind == "system" and o.get("subtype") in ("turn_duration", "stop_hook_summary") and not o.get("isSidechain"):
            ended = True
            continue
        if not isinstance(msg, dict) or o.get("isSidechain"):
            continue
        parts = blocks(msg.get("content"))
        if kind == "user":
            text = "\n".join(b.get("text", "") for b in parts if b.get("type") == "text").strip()
            if text and not any(b.get("type") == "tool_result" for b in parts):
                meta = bool(o.get("isMeta"))
                if meta and trigger is not None and not ended:
                    continue  # meta text inside a turn (skill text and the like) is not a new input
                # a new input: everything before it is a finished turn
                trigger, answer, asks, scheduled, ended = text, [], False, meta, False
        elif kind == "assistant":
            for b in parts:
                if b.get("type") == "text":
                    answer.append(b.get("text", ""))
                elif b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                    asks = True
    if trigger is None:
        return "?"
    # the wrapper's own <summary> tag, not the words anywhere in the text: a finished job whose output mentions "Monitor event" is real
    from_monitor = trigger.startswith("<task-notification>") and "<summary>Monitor event" in trigger.split("</summary>")[0]
    if (from_monitor or scheduled) and not asks and "?" not in "\n".join(answer):
        return "monitor"
    return "real"


def main(argv):
    if len(argv) != 2:
        print("usage: lane_turn_kind.py <transcript.jsonl>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-TAIL_LINES:]
    except OSError:
        print("?")
        return 0
    print(turn_kind(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
