#!/usr/bin/env python3
"""Warn the hub before its context reaches the auto-compaction window."""
import json
import os
from pathlib import Path
import re
import sys

READ_LIMIT = 512 * 1024
THRESHOLDS = (50, 60, 70)


def latest_context_size(transcript_path):
    with open(transcript_path, "rb") as transcript:
        transcript.seek(0, os.SEEK_END)
        end = transcript.tell()
        transcript.seek(max(0, end - READ_LIMIT))
        data = transcript.read(READ_LIMIT)

    for line in reversed(data.splitlines()):
        try:
            entry = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if isinstance(message, dict) and message.get("model") == "<synthetic>":
            continue
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            continue
        fields = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        values = [usage.get(field, 0) for field in fields]
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            continue
        return sum(values)
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        hub_dir = Path(os.path.expanduser(os.environ.get("AFA_HUB_DIR", "~/repos"))).resolve()
        cwd = Path(payload["cwd"]).resolve()
        if cwd != hub_dir:
            return 0

        session_id = payload["session_id"]
        transcript_path = payload["transcript_path"]
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
            return 0
        if not isinstance(transcript_path, str):
            return 0

        window = int(os.environ.get("AFA_COMPACT_WINDOW", "400000"))
        if window <= 0:
            return 0
        size = latest_context_size(transcript_path)
        if size is None:
            return 0

        state_dir = Path(os.path.expanduser(os.environ.get("AFA_STATE_DIR", "~/.claude/state")))
        stamp = state_dir / ("context-warn." + session_id)
        if size * 100 < window * 40:
            stamp.unlink(missing_ok=True)
            return 0

        percent = size * 100 // window
        threshold = max((value for value in THRESHOLDS if size * 100 >= window * value), default=0)
        if threshold == 0:
            return 0
        try:
            warned = int(stamp.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            warned = 0
        if threshold <= warned:
            return 0

        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(threshold), encoding="ascii")
        tokens_k = (size + 500) // 1000
        window_k = (window + 500) // 1000
        message = (
            f"Context is at {percent}% of the auto-compaction point "
            f"({tokens_k}K of {window_k}K tokens). Hand off before it compacts: "
            "finish the step in hand, answer any open receptionist questions, run "
            "/session-prime, then sign off telling the operator to close this terminal, "
            "open a fresh one in ~/repos and type /prep."
        )
        if threshold == 70:
            message = "Overdue: " + message
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": payload.get("hook_event_name"),
                "additionalContext": message,
            }
        }))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
