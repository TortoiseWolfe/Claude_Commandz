#!/usr/bin/env python3
"""big_output.py: a long answer goes to a file; the session sees a short summary and the path.

Why: tool output is about 68 percent of the text in a hub or lane conversation, and every word of it is re-read on every
later turn. A status list or a mail board of 150,000 characters read once costs far more than the two lines that were needed
from it. Output over LIMIT (8,000) characters is written whole to a file; the terminal gets a summary of at most 500
characters and the path. `--full` prints everything, as before.

Latest mode (`latest=True`, or `--latest` on the command line): the file has one fixed name, out-<name>-latest.txt, and each call
replaces it (written to a temporary file beside it, then swapped in), so a command that is asked for again and again leaves one
file, not one per call. Without it the name carries the time, out-<name>-HHMMSS.txt, and every call makes a new file.

Pruning: each time a file is written (either mode) this tool deletes its own old files from that same folder: a regular file
(never a symlink, never a folder) named exactly out-<name>-<six digits>.txt whose last change is over 24 hours ago
(PRUNE_AFTER_SECONDS). The latest file and every file of any other name are left alone. A delete that fails is ignored.

When it applies: only for a session that asked for it, with AFA_BIG_OUT=1 in its environment (lane.sh sets that for every new
Claude lane, and hub_lean.py for the hub session). Everything else, the scheduled jobs and every script that parses another
script's output, gets the full text exactly as before. `--full` or AFA_BIG_OUT=0 always wins, so a script that parses an
answer says `--full`.

As a module:   from big_output import emit;  emit(text, "lane-status")            (prints text or the summary)
As a filter:   some_command | big_output.py --name board [--group-by REGEX] [--full] [--folder DIR]
The file goes in the caller's lane folder when known (AFA_LANE_FOLDER, set by lane.sh), else ~/.claude/state/out/.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

LIMIT = 8000
SUMMARY_MAX = 500
PRUNE_AFTER_SECONDS = 86400
TIMED_NAME = re.compile(r"out-[A-Za-z0-9_.-]{1,40}-[0-9]{6}\.txt")  # exactly what a time-named file is called
PATH_NOTE = "full output ({chars:,} chars, {lines:,} lines): {path}  (add --full to print it all)"


def active(full=False):
    return (not full) and os.environ.get("AFA_BIG_OUT") == "1"


def out_folder(folder=None):
    if folder:
        return Path(folder)
    lf = os.environ.get("AFA_LANE_FOLDER")
    if lf and Path(lf).is_dir():
        return Path(lf)
    return Path.home() / ".claude/state/out"


def default_summary(text, group_by=None):
    lines = text.splitlines()
    head = "%d lines." % len(lines)
    if group_by:
        counts = {}
        rx = re.compile(group_by)
        for l in lines:
            m = rx.search(l)
            if m:
                k = (m.group(1) if m.groups() else m.group(0)).strip()
                counts[k] = counts.get(k, 0) + 1
        if counts:
            head += " By kind: " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8]) + "."
    room = SUMMARY_MAX - len(head) - 12
    first = []
    used = 0
    for l in lines:
        l = l.strip()
        if not l:
            continue
        l = l[:110]
        if used + len(l) + 3 > room:
            break
        first.append(l)
        used += len(l) + 3
        if len(first) >= 3:
            break
    s = head + ("\nFirst: " + " / ".join(first) if first else "")
    return s[:SUMMARY_MAX]


def prune_old(d, now=None):
    """Delete this tool's own time-named files in folder d that are over PRUNE_AFTER_SECONDS old. Never raises."""
    try:
        cutoff = (time.time() if now is None else now) - PRUNE_AFTER_SECONDS
        with os.scandir(d) as it:
            entries = list(it)
        for e in entries:
            try:
                if not TIMED_NAME.fullmatch(e.name) or e.is_symlink() or not e.is_file(follow_symlinks=False):
                    continue
                if e.stat(follow_symlinks=False).st_mtime < cutoff:
                    os.unlink(e.path)
            except Exception:
                pass
    except Exception:
        pass


def emit(text, name, *, latest=False, full=False, limit=LIMIT, folder=None, summary=None, group_by=None, stream=None, newline=True):
    """Print text, or (when the answer is long and this session asked for it) a summary and the path. Returns the path or None."""
    stream = stream or sys.stdout
    if not active(full) or len(text) <= limit:
        stream.write(text)
        if newline and text and not text.endswith("\n"):
            stream.write("\n")
        return None
    d = out_folder(folder)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)  # a folder made here is owner-only; a lane folder that exists keeps its mode
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", name)[:40] or "out"
    path = d / ("out-%s-%s.txt" % (safe, "latest" if latest else time.strftime("%H%M%S")))
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the text can hold mail and board lines: owner only
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)
    prune_old(d)
    s = (summary if summary is not None else default_summary(text, group_by))[:SUMMARY_MAX]
    stream.write(s.rstrip("\n") + "\n")
    stream.write(PATH_NOTE.format(chars=len(text), lines=text.count("\n") + 1, path=path) + "\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offload long output to a file (filter form).")
    ap.add_argument("--name", default="out")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--latest", action="store_true", help="one fixed file out-<name>-latest.txt, replaced on each call")
    ap.add_argument("--folder")
    ap.add_argument("--group-by", help="regex; its first group (or whole match) is counted per line in the summary")
    a = ap.parse_args(argv)
    emit(sys.stdin.read(), a.name, latest=a.latest, full=a.full, limit=a.limit, folder=a.folder, group_by=a.group_by, newline=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
