#!/usr/bin/env python3
"""Check a lane's handoff.md: under 4 KB and with the three headings a fresh session needs. No model.

A fresh session reads prompt.md and handoff.md before any work, so a long hand-off is paid for on every start
(2 Nov: an 11 KB hand-off with five jobs in it was read twice per start and the lane was recycled three times in 18 minutes).

  handoff_check.py FILE             check; print one line saying what is wrong, or "ok"
  handoff_check.py FILE --rotate    if FILE is too big, move its text to handoff-prevN.md beside it
                                    (N = the next free number) and leave a skeleton with the headings
  handoff_check.py FILE --max N     limit in bytes (default 4096)
  handoff_check.py FILE --hub       check a HUB hand-off (~/.claude/state/hub-handoff-*.md) instead: under 6 KB, with headings
                                    starting "Done", "Waiting" and "Lanes", and no "Lessons" heading (workspace ticket 96: the
                                    111 November hub hand-offs averaged 10 KB, and a lesson written in one reaches a single
                                    later session; a lesson becomes a hub-list job line the same day). --rotate is not offered
                                    for a hub file, because each hub hand-off is a new dated file; cut it and check again.

Headings it wants (any markdown level, any capitalisation): one starting "Done", one starting "Next", one starting "Re-arm".
Exit codes: 0 = ok (or rotated); 1 = too big; 2 = a heading missing; 3 = both; 4 = file unreadable; 5 = --hub with --rotate;
bit 8 is added (so 8, 9, 10 or 11) when a hub hand-off has a Lessons heading.
Rotation never deletes: the whole old text is kept in the handoff-prev file, and it changes nothing when the size is fine.
"""
import argparse
import re
import sys
from pathlib import Path

MAX_BYTES = 4096
HEADINGS = [("Done", r"^#+\s*done\b"), ("Next", r"^#+\s*next\b"), ("Re-arm", r"^#+\s*re-?arm\b")]
HUB_MAX_BYTES = 6144
HUB_HEADINGS = [("Done", r"^(#+\s*|\*\*)done\b"), ("Waiting", r"^(#+\s*|\*\*)waiting\b"), ("Lanes", r"^(#+\s*|\*\*)lanes\b")]
LESSONS = r"^(#+\s*|\*\*)lessons?\b"

SKELETON = """# Hand-off (rotated; the previous text is in {prev})

Keep this file under 4 KB and about one job. History goes to handoff-prevN.md.

## Done

## In flight

## Next step

## Re-arm
"""


def problems(text, limit, hub=False):
    size = len(text.encode("utf-8"))
    out = []
    code = 0
    if size > limit:
        out.append("%d bytes, over the %d-byte limit" % (size, limit))
        code |= 1
    missing = [name for name, pat in (HUB_HEADINGS if hub else HEADINGS) if not re.search(pat, text, re.I | re.M)]
    if missing:
        out.append("missing heading: " + ", ".join(missing))
        code |= 2
    if hub and re.search(LESSONS, text, re.I | re.M):
        out.append('a Lessons heading: turn each lesson into a hub-list job line (muse_poll_record.sh inbox "Job: ...") and cut the section')
        code |= 8
    return code, out


def next_prev(path):
    n = 1
    while path.with_name("handoff-prev%d.md" % n).exists():
        n += 1
    return path.with_name("handoff-prev%d.md" % n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--rotate", action="store_true")
    ap.add_argument("--max", type=int, default=None, dest="limit")
    ap.add_argument("--hub", action="store_true")
    a = ap.parse_args(argv)
    if a.hub and a.rotate:
        sys.stderr.write("handoff_check: --rotate is for a lane's handoff.md; cut the hub hand-off and check it again\n")
        return 5
    limit = a.limit if a.limit is not None else (HUB_MAX_BYTES if a.hub else MAX_BYTES)
    path = Path(a.file)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.stderr.write("handoff_check: cannot read %s: %s\n" % (path, e))
        return 4
    code, found = problems(text, limit, a.hub)
    if a.rotate and code & 1:
        prev = next_prev(path)
        prev.write_text(text, encoding="utf-8")
        path.write_text(SKELETON.format(prev=prev.name), encoding="utf-8")
        print("rotated: %d bytes moved to %s; %s now holds a skeleton to fill in" % (len(text.encode("utf-8")), prev.name, path.name))
        return 0
    print("; ".join(found) if found else "ok")
    return code


if __name__ == "__main__":
    sys.exit(main())
