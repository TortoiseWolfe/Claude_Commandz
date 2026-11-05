#!/usr/bin/env python3
"""Claude Code PostToolUse hook: the co-op scrubber. In a session whose working folder is a co-op folder (patterns in the
private ~/.config/afa/coop-scrubber.json), rewrites the tool's result so every person's name, email address and phone
number is a placeholder before the model reads it. Anywhere else it does nothing at all.

stdin:  {"tool_name": ..., "tool_response": ..., "cwd": ...}
stdout: {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": <same shape, strings scrubbed>}}
        printed only when something was replaced. The hook always exits 0 and never blocks (the no-AI wall is the layer
        that blocks). A failure inside a co-op folder withholds every string in the result, so an unreadable roster never
        lets raw text through; a failure before the folder is known (an unreadable config) leaves the output alone.
        One line per scrubbed call goes to ~/.claude/state/coop-scrubber.log: time, tool, counts, never text.
The work lives in coop_scrubber.py (scripts/), found beside this file in the repo and in ~/.claude/scripts once installed.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "scripts"))


WITHHELD = "[withheld: the co-op scrubber failed on this text]"


def withhold(obj):
    if isinstance(obj, str):
        return WITHHELD if obj else obj
    if isinstance(obj, dict):
        return {k: withhold(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [withhold(v) for v in obj]
    return obj


def emit(new):
    json.dump({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new}}, sys.stdout)


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or "tool_response" not in payload:
            return 0
        import coop_scrubber as cs
        cfg = cs.load_config()
        if not cs.is_coop_folder(payload.get("cwd") or os.getcwd(), cfg["folders"]):
            return 0
    except Exception as exc:  # the folder is not known, so nothing says this text is private
        sys.stderr.write("coop scrubber: skipped (%s)\n" % type(exc).__name__)
        return 0
    response = payload["tool_response"]
    try:
        scrubber = cs.Scrubber(cs.Roster.load(), cfg["allow"], cfg["ner"])
        new = scrubber.scrub(response)
        if scrubber.ner_state == "unavailable":
            sys.stderr.write("coop scrubber: Presidio is not available, so only the roster and patterns ran\n")
        if new != response:
            cs.log_counts(payload.get("tool_name") or "?", scrubber)
            emit(new)
    except Exception as exc:  # a co-op folder: stay closed
        sys.stderr.write("coop scrubber: failed (%s), so this result is withheld\n" % type(exc).__name__)
        emit(withhold(response))
    return 0


if __name__ == "__main__":
    sys.exit(main())
