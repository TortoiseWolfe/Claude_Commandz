#!/usr/bin/env python3
"""Check a lane's handoff.md: under 4 KB and with the three headings a fresh session needs. No model.

A fresh session reads prompt.md and handoff.md before any work, so a long hand-off is paid for on every start
(2 Nov: an 11 KB hand-off with five jobs in it was read twice per start and the lane was recycled three times in 18 minutes).

  handoff_check.py FILE             check; print one line saying what is wrong, or "ok"
  handoff_check.py FILE --rotate    if FILE is too big, move its text to handoff-prevN.md beside it
                                    (N = the next free number) and leave a skeleton with the headings
  handoff_check.py FILE --max N     limit in bytes (default 4096)

Headings it wants (any markdown level, any capitalisation): one starting "Done", one starting "Next", one starting "Re-arm".
Exit codes: 0 = ok (or rotated); 1 = too big; 2 = a heading missing; 3 = both; 4 = file unreadable.
Rotation never deletes: the whole old text is kept in the handoff-prev file, and it changes nothing when the size is fine.
"""
import argparse
import re
import sys
from pathlib import Path

MAX_BYTES = 4096
HEADINGS = [("Done", r"^#+\s*done\b"), ("Next", r"^#+\s*next\b"), ("Re-arm", r"^#+\s*re-?arm\b")]

SKELETON = """# Hand-off (rotated; the previous text is in {prev})

Keep this file under 4 KB and about one job. History goes to handoff-prevN.md.

## Done

## In flight

## Next step

## Re-arm
"""


def problems(text, limit):
    size = len(text.encode("utf-8"))
    out = []
    code = 0
    if size > limit:
        out.append("%d bytes, over the %d-byte limit" % (size, limit))
        code |= 1
    missing = [name for name, pat in HEADINGS if not re.search(pat, text, re.I | re.M)]
    if missing:
        out.append("missing heading: " + ", ".join(missing))
        code |= 2
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
    ap.add_argument("--max", type=int, default=MAX_BYTES, dest="limit")
    a = ap.parse_args(argv)
    path = Path(a.file)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.stderr.write("handoff_check: cannot read %s: %s\n" % (path, e))
        return 4
    code, found = problems(text, a.limit)
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
