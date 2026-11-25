#!/usr/bin/env python3
"""One heartbeat line per machine, and the hub-page lines that say whether each machine's line is fresh.

Use when: you need to know whether a machine (the tower, NX-01, the Mac mini) is up and busy or idle.

Fleet review C8: nobody could say whether the tower or the Mac mini was idle. Each machine writes one line a day
(numbers and times only, no names but the machine's own); the hub digest shows one line per machine.

  machine_heartbeat.py line [--machine NAME] [--now EPOCH]
      prints ONE line: 2026-11-25T11:18:02-05:00 NX-01 up_hours=42 lanes=8 mem_free_mb=5120
      Machine name: HEARTBEAT_MACHINE, else the host name. lanes = windows of the tmux session "lanes" (0 when missing).
  machine_heartbeat.py write FILE [--machine NAME] [--now EPOCH]
      writes that line to FILE (temp file, then rename); prints nothing; leaves a line under 20 hours old alone,
      so a 15-minute caller writes about once a day.
  machine_heartbeat.py report FILE [FILE ...] [--now EPOCH]
      one hub-page line per file: fresh (under 26 hours), LAST SEEN (older), or no heartbeat (missing or unreadable).

Sources can be swapped for test files: HEARTBEAT_UPTIME_FILE, HEARTBEAT_MEMINFO_FILE,
HEARTBEAT_TMUX_OUT (the text "tmux list-windows" would print). Standard library only; safe under python3 -I.
Tests: tests/test_machine_heartbeat.py (invented data only).
"""
import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime

WRITE_SKIP_HOURS = 20   # a line younger than this is not rewritten
FRESH_HOURS = 26        # a line older than this is reported as LAST SEEN
LINE_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d) ([A-Za-z0-9_.-]+) up_hours=(\d+) lanes=(\d+) mem_free_mb=(\d+)")


def _read(env_name, default):
    path = os.environ.get(env_name) or default
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def up_hours():
    try:
        return int(float(_read("HEARTBEAT_UPTIME_FILE", "/proc/uptime").split()[0]) // 3600)
    except (IndexError, ValueError):
        return 0


def mem_free_mb():
    for row in _read("HEARTBEAT_MEMINFO_FILE", "/proc/meminfo").splitlines():
        m = re.match(r"MemAvailable:\s+(\d+)\s*kB", row)
        if m:
            return int(m.group(1)) // 1024
    return 0


def lanes():
    if os.environ.get("HEARTBEAT_TMUX_OUT"):
        out = _read("HEARTBEAT_TMUX_OUT", "")
    else:
        try:
            r = subprocess.run(["tmux", "list-windows", "-t", "lanes", "-F", "#{window_id}"], capture_output=True,
                               text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return 0
        out = r.stdout if r.returncode == 0 else ""
    return len([row for row in out.splitlines() if row.strip()])


def make_line(machine=None, now=None):
    now = time.time() if now is None else now
    machine = machine or os.environ.get("HEARTBEAT_MACHINE") or socket.gethostname()
    machine = re.sub(r"[^A-Za-z0-9_.-]", "-", machine) or "unknown"
    stamp = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    return f"{stamp} {machine} up_hours={up_hours()} lanes={lanes()} mem_free_mb={mem_free_mb()}"


def parse(text):
    """-> (datetime, name, up, lanes, mem) from a file's text, or None when it is not exactly one heartbeat line."""
    rows = text.splitlines()
    if len(rows) != 1:
        return None
    m = LINE_RE.fullmatch(rows[0])
    if not m:
        return None
    try:
        when = datetime.fromisoformat(m.group(1))
    except ValueError:
        return None
    return when, m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))


def write_file(path, machine=None, now=None):
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as f:
            old = parse(f.read())
        if old and 0 <= now - old[0].timestamp() < WRITE_SKIP_HOURS * 3600:
            return False
    except OSError:
        pass
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".heartbeat.", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(make_line(machine, now) + "\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def _clock(when):
    return when.strftime("%-I:%M %p")


def report_line(path, now=None):
    now = time.time() if now is None else now
    base = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, encoding="utf-8") as f:
            rec = parse(f.read())
    except (OSError, UnicodeDecodeError):
        rec = None
    if rec is None:
        return f"- {base}: no heartbeat (nothing installed or never written)"
    when, name, up, nlanes, mem = rec
    age = now - when.timestamp()
    if age < FRESH_HOURS * 3600:
        return f"- {name}: up {up} h, {nlanes} lanes, {mem} MB free (seen {_clock(when)})"
    days = int(age // 86400)
    if days >= 1:
        ago = f"{days} day{'s' if days != 1 else ''} ago"
    else:
        hours = int(age // 3600)
        ago = f"{hours} hours ago"
    return f"- {name}: LAST SEEN {ago} ({when.strftime('%a %-d %b')} {_clock(when)}), nothing since"


def main(argv=None):
    ap = argparse.ArgumentParser(description="One heartbeat line per machine.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("line")
    p.add_argument("--machine")
    p.add_argument("--now", type=float)
    p = sub.add_parser("write")
    p.add_argument("file")
    p.add_argument("--machine")
    p.add_argument("--now", type=float)
    p = sub.add_parser("report")
    p.add_argument("files", nargs="+")
    p.add_argument("--now", type=float)
    a = ap.parse_args(argv)
    if a.cmd == "line":
        print(make_line(a.machine, a.now))
    elif a.cmd == "write":
        try:
            write_file(a.file, a.machine, a.now)
        except OSError as e:
            print(f"machine_heartbeat: cannot write: {e.__class__.__name__}", file=sys.stderr)
            return 1
    else:
        for f in a.files:
            print(report_line(f, a.now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
