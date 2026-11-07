#!/usr/bin/env python3
"""Write or check line 1 of a lane's report.md. No model.

The hub reads only line 1 of report.md. A lane that types it by hand gets the time wrong or the state word wrong, and then
lane_watch.sh complains ("stamp ahead") or misses the line. This stamps the time from the clock itself and refuses a state
word the watchers do not know.

  report_line.py set FILE --state STATE --text "..."   rewrite ONLY line 1 as "STATE h:mm AM | text"; the rest of the file is kept byte for byte
  report_line.py check FILE                            check line 1 and the top section; print "ok" or one line per problem
  report_line.py now                                   print the clock as the watchers read it (12-hour, no leading zero)

States: WORKING, DONE, BLOCKED, EMPTY, QUEUE EMPTY, RECYCLE, DRAFTED, MORNING FILE READY, STEP-FOR-JONATHAN, TOWER-STEP, FAILED,
ENDED WHILE WORKING, HOURLY, STATUS, READY.
STEP-FOR-JONATHAN needs a full https:// link in the text (lane_watch.sh step_url_check wakes the hub when it is missing).
The top section (line 1 up to the first heading) is meant to stay under 150 words; set warns on stderr, check fails.
A missing FILE is created holding just line 1.

Exit codes: 0 = ok; 1 = check found a problem; 2 = refused (bad state, no link, multi-line text, unreadable file).
Test hook: the environment variable REPORT_LINE_NOW ("9:05 AM") replaces the clock, so tests can pin the stamp.
"""
import argparse
import datetime
import os
import re
import sys
import tempfile
from pathlib import Path

# Longest first, so "QUEUE EMPTY" is not read as "EMPTY" and "ENDED WHILE WORKING" is not read as "ENDED".
STATES = [
    "WORKING", "DONE", "BLOCKED", "EMPTY", "QUEUE EMPTY", "RECYCLE", "DRAFTED", "MORNING FILE READY",
    "STEP-FOR-JONATHAN", "TOWER-STEP", "FAILED", "ENDED WHILE WORKING", "HOURLY", "STATUS", "READY",
]
BY_LENGTH = sorted(STATES, key=len, reverse=True)
MAX_WORDS = 150
TIME_RE = re.compile(r"(1[0-2]|0?[1-9]):[0-5][0-9] ?[AaPp][Mm]")
# Same reading as lane_watch.sh stamp_ahead_check: a time after by/until/till/deadline/due/before is a target, not the stamp.
TARGET_RE = re.compile(r"\b(?:by|until|till|deadline|due|before)[: ]+$", re.I)
STAMP_SLACK_MIN = 2


def clock():
    forced = os.environ.get("REPORT_LINE_NOW")
    if forced:
        return forced
    return datetime.datetime.now().strftime("%I:%M %p").lstrip("0")


def split_state(line):
    """(state, rest) when the line starts with a known state word followed by a space or the end; else (None, line)."""
    for s in BY_LENGTH:
        if line.startswith(s) and (len(line) == len(s) or line[len(s)] in " |:"):
            return s, line[len(s):]
    return None, line


def top_section(text):
    """Line 1 and everything up to (not including) the first markdown heading line."""
    lines = text.split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.lstrip().startswith("#"):
            break
        out.append(ln)
    return "\n".join(out)


def words(text):
    return len(text.split())


def status_time(line):
    """The first time on the line that is not a target (deadline 7:00 PM), as minutes after midnight; None if there is none."""
    for m in TIME_RE.finditer(line):
        if TARGET_RE.search(line[:m.start()]):
            continue
        tok = m.group(0).replace(" ", "").upper()
        h, rest = tok.split(":")
        h, mi, ap = int(h), int(rest[:2]), rest[2:]
        if ap == "PM" and h != 12:
            h += 12
        if ap == "AM" and h == 12:
            h = 0
        return h * 60 + mi
    return None


def read(path):
    try:
        return Path(path).read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        print("cannot read %s: %s" % (path, e), file=sys.stderr)
        sys.exit(2)


def cmd_set(args):
    state = args.state.strip().upper()
    if state not in STATES:
        print("unknown state %r; use one of: %s" % (args.state, ", ".join(STATES)), file=sys.stderr)
        return 2
    text = args.text.strip()
    if "\n" in text or "\r" in text:
        print("--text must be one line", file=sys.stderr)
        return 2
    if state == "STEP-FOR-JONATHAN" and not re.search(r"https://\S+", text):
        print("STEP-FOR-JONATHAN needs the full https:// link of the page in --text", file=sys.stderr)
        return 2
    line = "%s %s" % (state, clock())
    if text:
        line += " | " + text
    old = read(args.file)
    rest = "" if old is None else (old.split("\n", 1)[1] if "\n" in old else "")
    new = line + "\n" + rest if (old is None or "\n" in old) else line + "\n"
    target = Path(args.file)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".report-line-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        if target.exists():
            os.chmod(tmp, target.stat().st_mode & 0o7777)
        os.replace(tmp, target)
    except OSError as e:
        print("cannot write %s: %s" % (args.file, e), file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return 2
    n = words(top_section(new))
    if n > MAX_WORDS:
        print("warning: the top section is %d words; keep it under %d (move detail below a heading)" % (n, MAX_WORDS), file=sys.stderr)
    print(line)
    return 0


def cmd_check(args):
    text = read(args.file)
    if text is None:
        print("no such file: %s" % args.file)
        return 1
    first = text.split("\n", 1)[0]
    out = []
    state, _ = split_state(first)
    if state is None:
        out.append("line 1 does not start with a known state word")
    if not TIME_RE.search(first):
        out.append("line 1 has no h:mm AM/PM time")
    else:
        st = status_time(first)
        if st is not None:
            mtime = datetime.datetime.fromtimestamp(Path(args.file).stat().st_mtime)
            if st - (mtime.hour * 60 + mtime.minute) > STAMP_SLACK_MIN:
                out.append("line 1 time is more than %d minutes ahead of the file's last change" % STAMP_SLACK_MIN)
    if state == "STEP-FOR-JONATHAN" and not re.search(r"https://\S+", first):
        out.append("STEP-FOR-JONATHAN line 1 has no https:// link")
    n = words(top_section(text))
    if n > MAX_WORDS:
        out.append("top section is %d words, over %d" % (n, MAX_WORDS))
    if out:
        print("\n".join(out))
        return 1
    print("ok")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("file")
    s.add_argument("--state", required=True)
    s.add_argument("--text", default="")
    c = sub.add_parser("check")
    c.add_argument("file")
    sub.add_parser("now")
    args = ap.parse_args(argv)
    if args.cmd == "now":
        print(clock())
        return 0
    return cmd_set(args) if args.cmd == "set" else cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
