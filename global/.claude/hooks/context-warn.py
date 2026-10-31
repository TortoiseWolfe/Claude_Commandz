#!/usr/bin/env python3
"""Warn the hub before its context reaches the hand-off line (150K tokens by default; Jonathan 30 Oct: hand off at 150K).

The hand-off line, in thousands of tokens, is read from AFA_HANDOFF_K (default 150;
a value that is not a positive whole number falls back to 150). Both warning points
come from it, as absolute token counts rather than percentages of an auto-compaction window:
an early warning once at (hand-off - 20)K ("start wrapping up"), and a hand-off warning once at
the hand-off line ("write the hand-off, run /session-prime, and ask Jonathan for a fresh
terminal and /prep; never auto-compact"). Each is shown once per session.

A --size <transcript> mode prints the latest context size in tokens and exits
(exit 1, no traceback, when the file is missing, unreadable or has no usage).
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

READ_LIMIT = 512 * 1024
DEFAULT_HANDOFF_K = 150
EARLY_MARGIN_K = 20  # the early warning comes this many thousand tokens before the hand-off line


def handoff_k_setting():
    """The hand-off line in thousands of tokens; a bad AFA_HANDOFF_K falls back to the default."""
    try:
        value = int(os.environ.get("AFA_HANDOFF_K", str(DEFAULT_HANDOFF_K)))
    except ValueError:
        return DEFAULT_HANDOFF_K
    return value if value > 0 else DEFAULT_HANDOFF_K


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
    # Check for --size mode first (doesn't need stdin payload)
    if len(sys.argv) == 3 and sys.argv[1] == "--size":
        transcript_path = sys.argv[2]
        try:
            size = latest_context_size(transcript_path)
        except OSError:
            return 1
        if size is not None:
            print(size)
            return 0
        return 1

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

        handoff_k = handoff_k_setting()
        early_k = max(handoff_k - EARLY_MARGIN_K, 1)
        thresholds_k = (early_k, handoff_k)
        size = latest_context_size(transcript_path)
        if size is None:
            return 0

        state_dir = Path(os.path.expanduser(os.environ.get("AFA_STATE_DIR", "~/.claude/state")))
        stamp = state_dir / ("context-warn." + session_id)
        # Reset the stamp when below the early warning point
        if size < early_k * 1000:
            stamp.unlink(missing_ok=True)
            return 0

        # Find the highest threshold we've crossed
        threshold_k = max((value for value in thresholds_k if size >= value * 1000), default=0)
        if threshold_k == 0:
            return 0
        try:
            warned = int(stamp.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            warned = 0
        if threshold_k <= warned:
            return 0

        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(threshold_k), encoding="ascii")
        tokens_k = (size + 500) // 1000
        if threshold_k < handoff_k:
            message = (
                f"Context is at {tokens_k}K tokens. Hand-off line is {handoff_k}K: "
                "start wrapping up. Finish the step in hand, answer any open receptionist "
                "questions, run /session-prime, then sign off telling the operator to close "
                "this terminal, open a fresh one in ~/repos and type /prep."
            )
        else:  # threshold_k == handoff_k
            message = (
                f"Context is at {tokens_k}K tokens, past the {handoff_k}K hand-off line. "
                "Write the hand-off, run /session-prime, and ask Jonathan for a fresh terminal "
                "and /prep. Never auto-compact."
            )
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
