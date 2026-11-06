#!/usr/bin/env python3
"""Warn the hub before its context reaches the hand-off line (330K tokens by default, the last tenth before auto-compact at 367K; Jonathan 31 Oct at 157K: "still too soon, fifty seven percent left").

The hub (cwd = ~/repos) gets a short "Hub context: NNNK of 367K" line once per prompt, plus three tiers.
The line comes with the UserPromptSubmit event; a PostToolUse call prints it only when no prompt event has shown it in this session
(a resumed session's first tool call), because every print lands in the hub's own context (issue 224, 5 Nov 2026: it printed after
every tool call). A tier warning still arrives on whichever event first sees the crossing, with the line above it.
All come from absolute token counts, not percentages of an auto-compaction window:
  - AFA_HANDOFF_START_K (default 250): "start the running hand-off file now", once per session;
  - hand-off line minus 20K (310K by default): "start wrapping up", once per session;
  - AFA_HANDOFF_K (default 330): "hand off and end, no new work" with the marker to touch,
    repeated on every call past the line (lane-watch-guard.py lets the session stop once
    ~/.claude/state/hub-handed-off.<session id> exists and a fresh hub-handoff-*.md does).
A setting that is not a positive whole number falls back to its default. Lanes (any other cwd) see nothing.

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
DEFAULT_HANDOFF_K = 330
DEFAULT_HANDOFF_START_K = 250
EARLY_MARGIN_K = 20  # the early warning comes this many thousand tokens before the hand-off line


def get_setting(env_var, default):
    """Read a setting from environment; a bad value falls back to the default."""
    try:
        value = int(os.environ.get(env_var, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


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

        handoff_k = get_setting("AFA_HANDOFF_K", DEFAULT_HANDOFF_K)
        handoff_start_k = get_setting("AFA_HANDOFF_START_K", DEFAULT_HANDOFF_START_K)
        early_k = max(handoff_k - EARLY_MARGIN_K, 1)
        thresholds_k = sorted({handoff_start_k, early_k, handoff_k})
        size = latest_context_size(transcript_path)
        if size is None:
            return 0

        tokens_k = (size + 500) // 1000
        message = f"Hub context: {tokens_k}K of 367K"

        state_dir = Path(os.path.expanduser(os.environ.get("AFA_STATE_DIR", "~/.claude/state")))
        stamp = state_dir / ("context-warn." + session_id)
        # Once per prompt: UserPromptSubmit always shows the line and leaves a mark; PostToolUse shows it only when there is no mark yet.
        line_mark = state_dir / ("context-line." + session_id)
        is_prompt = payload.get("hook_event_name") == "UserPromptSubmit"
        show_line = is_prompt or not line_mark.exists()
        if show_line:
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                line_mark.write_text("1", encoding="ascii")
            except OSError:
                pass
        # Reset the stamp when below the lowest warning point
        if size < min(thresholds_k) * 1000:
            stamp.unlink(missing_ok=True)
            if not show_line:
                return 0
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": payload.get("hook_event_name"),
                    "additionalContext": message,
                }
            }))
            return 0

        # Find the highest threshold we've crossed
        threshold_k = max((value for value in thresholds_k if size >= value * 1000), default=0)
        try:
            warned = int(stamp.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            warned = 0

        extra = None
        if threshold_k > warned and threshold_k < handoff_k:
            if threshold_k == handoff_start_k:
                extra = (
                    f"Hub context {tokens_k}K: start the running hand-off file now "
                    f"(~/.claude/state/hub-handoff-<date>-<HHMM>.md)."
                )
            elif threshold_k == early_k:
                extra = (
                    f"Context is at {tokens_k}K tokens. Hand-off line is {handoff_k}K: "
                    "start wrapping up. Finish the step in hand, answer any open receptionist "
                    "questions, run /session-prime, then sign off telling the operator to close "
                    "this terminal, open a fresh one in ~/repos and type /prep."
                )
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(str(threshold_k), encoding="ascii")

        # The hand-off warning repeats every turn past 330K
        if threshold_k >= handoff_k:
            extra = (
                f"Hub context {tokens_k}K: hand off and end, no new work. "
                f"When the hand-off file is written: touch ~/.claude/state/hub-handed-off.{session_id}"
            )

        if extra:
            message += "\n" + extra
        elif not show_line:
            return 0

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
