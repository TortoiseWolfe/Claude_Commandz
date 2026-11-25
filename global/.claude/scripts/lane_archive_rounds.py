#!/usr/bin/env python3
"""Move a lane's finished rounds out of report.md and status.md into report-archive.md and status-archive.md (issue 128).

A lane adds one "## " section to those two files for every round and nothing took the old ones out, so a lane's notes (30 KB and 27 KB on
19 Nov) outgrew its recycle line and it was recycled in a loop. lane.sh --recycle runs this on the lane's hand-off folder once the lane has
written handoff.md and before it is cleared, so the fresh session re-reads line 1, the lines above the first round and the open round only.

Rule, the same for both files:
  - the lines before the first "## " heading stay (line 1 is always among them: it is the word the hub reads);
  - the last "## " section is the open round and stays;
  - every earlier section is appended, unchanged, to <name>-archive.md in the same folder, and leaves the file;
  - a "## " line inside a code fence is not a heading;
  - a file under --min-bytes (default 4096) or with fewer than two sections is not touched;
  - the file's modified time is kept, so lane_watch.sh does not read the rewrite as a new report.
The archive is written first and the notes file is replaced after, so a crash can repeat a section in the archive but never lose one.

Use when: a lane's report.md or status.md has grown past what a fresh session should re-read (lane.sh --recycle calls it; run it by hand on a hand-off folder to trim one).

Usage: lane_archive_rounds.py [--min-bytes N] <hand-off folder>
Prints one line for each file it shortened and always exits 0 (a recycle never fails on this)."""

import os
import sys
from pathlib import Path

NAMES = ("report.md", "status.md")
DEFAULT_MIN_BYTES = 4096


def split_rounds(text):
    """-> (head, [section, ...]); every piece keeps its own newlines, so head + sections joined is the text."""
    lines = text.splitlines(keepends=True)
    heads, sections, fenced = [], [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if i > 0 and not fenced and line.startswith("## "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            heads.append(line)
    return "".join(heads), ["".join(s) for s in sections]


def archive_file(path, min_bytes=DEFAULT_MIN_BYTES):
    """-> (rounds moved, bytes moved) for one file, or None when it was left alone."""
    path = Path(path)
    try:
        st = path.stat()
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if st.st_size < min_bytes:
        return None
    head, sections = split_rounds(text)
    if len(sections) < 2:
        return None
    old = "".join(sections[:-1])
    arch = path.with_name(path.stem + "-archive.md")
    try:
        with open(arch, "a", encoding="utf-8") as f:
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        tmp = path.with_name("." + path.name + ".tmp")
        tmp.write_text(head + sections[-1], encoding="utf-8")
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except OSError:
        return None
    return len(sections) - 1, len(old.encode("utf-8"))


def archive_folder(folder, min_bytes=DEFAULT_MIN_BYTES):
    """-> one line for each file shortened."""
    out = []
    for name in NAMES:
        got = archive_file(Path(folder) / name, min_bytes)
        if got:
            n, size = got
            out.append("%s: %d round%s (%d bytes) moved to %s-archive.md" % (name, n, "" if n == 1 else "s", size, name[:-3]))
    return out


def main(argv):
    min_bytes = DEFAULT_MIN_BYTES
    args = list(argv)
    if len(args) >= 2 and args[0] == "--min-bytes" and args[1].isdigit():
        min_bytes, args = int(args[1]), args[2:]
    if len(args) != 1:
        print("usage: lane_archive_rounds.py [--min-bytes N] <hand-off folder>", file=sys.stderr)
        return 0
    for line in archive_folder(args[0], min_bytes):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
