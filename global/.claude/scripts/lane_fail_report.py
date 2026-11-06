#!/usr/bin/env python3
"""What a headless lane leaves behind when its engine process exits with a failure (issue #234).

lane.sh runs this after "exit <code>" is written to out.log, only when the code is not 0:

  lane_fail_report.py <report.md> <out.log> <exit code> <engine> [<state dir>]

1. If report.md is missing or empty, line 1 becomes "FAILED exit <code> | <last error line>", so lane_watch wakes the hub.
   A report the lane wrote itself is never touched.
2. If out.log shows the engine is out of quota with a reset time ("RESOURCE_EXHAUSTED ... Resets in 91h56m"),
   <state dir>/engine-out/<engine>.until holds the epoch second when it resets, and `lane.sh` refuses that engine until then.

Reads out.log only (it is the engine's own output); prints nothing. Exit 0 always: a failed note must not mask the lane's own exit code.
"""
import os
import re
import sys
import time

ERR_RE = re.compile(r"(error|exception|exhausted|denied|refused|failed|not found|unauthor|forbidden|quota|limit|\b4\d\d\b|\b5\d\d\b)", re.I)
RESET_RE = re.compile(r"Resets? in\s+(?:(\d+)\s*d(?:ays?)?\s*)?(?:(\d+)\s*h(?:ours?)?\s*)?(?:(\d+)\s*m(?:in(?:utes?)?)?)?", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")


def last_error_line(text):
    lines = [ANSI_RE.sub("", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not re.fullmatch(r"exit \d+", l)]
    for l in reversed(lines):
        if ERR_RE.search(l):
            return l[:200]
    return lines[-1][:200] if lines else "no output"


def reset_seconds(text):
    if "RESOURCE_EXHAUSTED" not in text and "quota" not in text.lower():
        return None
    m = None
    for m in RESET_RE.finditer(text):
        pass
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(g) if g else 0 for g in m.groups())
    secs = d * 86400 + h * 3600 + mi * 60
    return secs if secs > 0 else None


def main(argv):
    if len(argv) < 5:
        return 0
    report, outlog, code, engine = argv[1:5]
    state = argv[5] if len(argv) > 5 else os.path.expanduser("~/.claude/state")
    try:
        text = open(outlog, errors="replace").read()
    except OSError:
        text = ""
    try:
        if not os.path.exists(report) or os.path.getsize(report) == 0:
            with open(report, "w") as f:
                f.write("FAILED exit %s | %s\n" % (code, last_error_line(text)))
    except OSError:
        pass
    secs = reset_seconds(text)
    if secs and re.fullmatch(r"[a-z]+", engine):
        try:
            d = os.path.join(state, "engine-out")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, engine + ".until"), "w") as f:
                f.write("%d\n" % (time.time() + secs))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
