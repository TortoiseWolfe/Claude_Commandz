#!/usr/bin/env python3
"""The Done / Next first lines of a lane's handoff.md, in one place so hub_digest.py (the hub's digest) and lane.sh --status read a hand-off the same way.

Usage: lane_handoff_summary.py <handoff.md>   prints "Done: ... | Next: ..." or nothing."""

import re
import sys
from pathlib import Path


def handoff_summary(path):
    """-> "Done: ... | Next: ..." from the first non-empty lines under those headings (or on the heading line)."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    done, nxt = "", ""
    in_done, in_next = False, False
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        d_m = re.match(r"^(?:[-*]\s+)?#*\s*Done[:\s]*(.*)", s, re.I)
        if d_m:
            in_done, in_next = True, False
            if d_m.group(1).strip():
                done, in_done = d_m.group(1).strip().lstrip("- ").strip(), False
            continue
        n_m = re.match(r"^(?:[-*]\s+)?#*\s*Next[:\s]*(.*)", s, re.I)
        if n_m:
            in_next, in_done = True, False
            if n_m.group(1).strip():
                nxt, in_next = n_m.group(1).strip().lstrip("- ").strip(), False
            continue
        if in_done and not done:
            done, in_done = s.lstrip("- ").strip(), False
        elif in_next and not nxt:
            nxt, in_next = s.lstrip("- ").strip(), False
        if done and nxt:
            break
    parts = []
    if done:
        parts.append(f"Done: {done}")
    if nxt:
        parts.append(f"Next: {nxt}")
    return " | ".join(parts)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        out = handoff_summary(sys.argv[1])
        if out:
            print(out)
