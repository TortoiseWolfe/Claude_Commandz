#!/usr/bin/env python3
"""lane_start_parts.py: what a lane's fresh session loaded before its first answer, and whether its own four files are the cause (read-only).
Use when: lane_watch.sh names a lane for a big start load and the hub needs to know what to shrink (issue 226); lane.sh --start-parts runs it.

  lane_start_parts.py TRANSCRIPT.jsonl LANE_FOLDER

Reads the transcript only as far as the first answer (its usage is the start load, input plus cache creation plus cache read tokens) and adds up
the size in characters of each kind of start-up entry before it: the skill listing, the tool names, the CLAUDE.md and rule files, the connector
instructions, the agent listing, the start-up hook text. It also sizes the lane's four own files (report.md, status.md, handoff.md, prompt.md).
When those four files are under a quarter of the load it says so and names the five biggest parts; when they are not, it names the biggest file.
Prints one line, counts and names only, never message text. Prints nothing when there is no reading.
"""
import json
import sys
from pathlib import Path

LANE_FILES = ("report.md", "status.md", "handoff.md", "prompt.md")
LABELS = {
    "skill_listing": "skill listing",
    "deferred_tools_delta": "tool names (connectors and other deferred tools)",
    "agent_listing_delta": "agent listing",
    "mcp_instructions_delta": "connector instructions",
    "instructions": "CLAUDE.md and rule files",
    "nested_memory": "CLAUDE.md and rule files",
    "hook_success": "start-up hook text",
    "hook_additional_context": "start-up hook text",
    "environment": "session context",
    "session_context": "session context",
}
SKIP_TYPES = {"user", "assistant", "system", "api-request", "api-request-shape", "api-request-blob", "attachment:prompt_snapshot"}
MIN_PART = 1000  # characters: a smaller part is not worth naming


def read_start(path):
    """(tokens at the first answer, {label: characters}) or None when the transcript has no first answer."""
    parts = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                t = d.get("type")
                if t == "assistant" and not d.get("isSidechain"):
                    u = (d.get("message") or {}).get("usage")
                    if isinstance(u, dict):
                        n = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                        if n > 0:
                            return n, parts
                    continue
                if t != "attachment":
                    continue
                kind = str((d.get("attachment") or {}).get("type"))
                if "attachment:" + kind in SKIP_TYPES:
                    continue
                label = LABELS.get(kind, kind.replace("_", " "))
                parts[label] = parts.get(label, 0) + len(line)
    except OSError:
        return None
    return None


def file_sizes(folder):
    out = {}
    for name in LANE_FILES:
        try:
            out[name] = (Path(folder) / name).stat().st_size
        except OSError:
            pass
    return out


def kb(n):
    return "%.1f KB" % (n / 1024)


def describe(tokens, parts, files):
    total = sum(files.values())
    tail = ""
    ranked = sorted(((n, label) for label, n in parts.items() if n >= MIN_PART), reverse=True)[:5]
    # bytes over 4 is about tokens, so "under a quarter of the load" is total bytes under the token count
    if files and total >= tokens:
        big = max(files, key=files.get)
        return ("its own files are the bulk: %s together (about %dK tokens of the %dK), %s the biggest at %s; archive or shrink that one"
                % (kb(total), round(total / 4000), round(tokens / 1000), big, kb(files[big])))
    if ranked:
        tail = ("; the load is the session's own setup, " if files else "; ") + "biggest parts in characters: " + "; ".join("%s %s" % (label, format(n, ",")) for n, label in ranked)
    head = "its own files (%s) total %s, too small to be the cause" % (", ".join(sorted(files)), kb(total)) if files else "its own files could not be read"
    return head + tail


def main(argv):
    if len(argv) != 3:
        print("usage: lane_start_parts.py TRANSCRIPT.jsonl LANE_FOLDER", file=sys.stderr)
        return 2
    got = read_start(argv[1])
    if not got:
        return 0
    tokens, parts = got
    print(describe(tokens, parts, file_sizes(argv[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
