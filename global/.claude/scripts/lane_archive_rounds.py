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
import stat
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
    arch = path.with_name(path.stem + "-archive.md")
    tmp = path.with_name("." + path.name + ".tmp")
    # This runs as the hub on a folder a lane can write to: a lane that swapped one of these names for a link would make the hub
    # write through it. So the notes file, the archive and the temp name must each be absent or a plain file of ours, never a link.
    if not (_ours_or_absent(path, must_exist=True) and _ours_or_absent(arch) and _ours_or_absent(tmp)):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as f:
            st = os.fstat(f.fileno())
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    if st.st_size < min_bytes:
        return None
    head, sections = split_rounds(text)
    if len(sections) < 2:
        return None
    old = "".join(sections[:-1])
    try:
        fd = os.open(arch, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        if os.path.lexists(tmp):
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(head + sections[-1])
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except OSError:
        return None
    return len(sections) - 1, len(old.encode("utf-8"))


def _ours_or_absent(p, must_exist=False):
    """True when p is a regular file owned by this user (not a link), or does not exist and need not."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return not must_exist
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()


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
