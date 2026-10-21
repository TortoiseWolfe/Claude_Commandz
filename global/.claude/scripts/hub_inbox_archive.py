#!/usr/bin/env python3
"""Move old done lines off the hub's inbox into a monthly archive. No model, no network.

  hub_inbox_archive.py [--days 7] [--inbox F] [--archive-dir D] [--today YYYY-MM-DD] [--dry-run]

A block is a "- [x] ..." line plus the indented notes under it ("  - done ...", "  - 2026-10-08 14:10: checked ...").
It moves when it was last touched more than --days ago: the latest date on its first line or on the head of
any of its notes. Open lines never move and nothing is reordered; a done line with no readable date stays.
The archive keeps the same line format (hub-inbox-archive/YYYY-MM.md, by the month of the last touch), so
session_prime_facts.py reads it as a second input. A block already in the archive is not written twice, so a run
that dies half-way is safe to repeat.

It takes hub-inbox.lock (flock, beside the inbox), the same lock muse_poll_record.sh and the OpenClaw scripts take
for every write, so an archive run and an append never interleave.
"""
import argparse
import fcntl
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path(os.environ.get("HOME", str(Path.home())))
HEAD = re.compile(r"^- \[(?P<box>[ x])\] (?P<day>\d{4}-\d\d-\d\d)\b")
ANY_HEAD = re.compile(r"^- \[[ x]\] ")
NOTE_DATE = re.compile(r"\d{4}-\d\d-\d\d")


def split_blocks(lines):
    """-> (preamble lines, [[head, *continuation lines]]). Any line that does not begin "- [" belongs to the block above it."""
    pre, blocks = [], []
    for ln in lines:
        if ANY_HEAD.match(ln):
            blocks.append([ln])
        elif blocks:
            blocks[-1].append(ln)
        else:
            pre.append(ln)
    return pre, blocks


def last_touch(block):
    """The latest date on a done block's head or on the first 30 characters of its notes; None when the head has none."""
    m = HEAD.match(block[0])
    if not m or m.group("box") != "x":
        return None
    days = [m.group("day")]
    for ln in block[1:]:
        d = NOTE_DATE.search(ln[:30])
        if d:
            days.append(d.group(0))
    best = None
    for d in days:
        try:
            v = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        best = v if best is None or v > best else best
    return best


def plan(lines, today, days):
    """-> (kept lines, {"YYYY-MM": [lines]}, blocks moved)."""
    cutoff = today - timedelta(days=days)
    pre, blocks = split_blocks(lines)
    kept, moved, n = list(pre), {}, 0
    for b in blocks:
        t = last_touch(b)
        if t is not None and t < cutoff:
            moved.setdefault(t.strftime("%Y-%m"), []).extend(b)
            n += 1
        else:
            kept.extend(b)
    return kept, moved, n


def add_to_archive(path, new_lines):
    """Append blocks to the month file, skipping any whose head line is already there. fsynced before the inbox is rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    have = set(path.read_text(errors="replace").splitlines()) if path.exists() else set()
    _, blocks = split_blocks(new_lines)
    out = []
    for b in blocks:
        if b[0].rstrip("\n") not in have:
            out.extend(b)
    if not out:
        return
    with open(path, "a", encoding="utf-8") as f:
        text = "".join(x if x.endswith("\n") else x + "\n" for x in out)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def run(inbox, archive_dir, today, days, dry_run=False):
    inbox = Path(os.path.realpath(inbox))  # the old muse-inbox.md name is a symlink: replace the file it points at
    if not inbox.exists():
        return 0, 0
    lock_path = inbox.with_name("hub-inbox.lock")
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            lines = inbox.read_text(errors="replace").splitlines(keepends=True)
            open_before = sum(1 for ln in lines if ln.startswith("- [ ] "))
            kept, moved, n = plan(lines, today, days)
            if n == 0 or dry_run:
                return n, open_before
            for month, blk in sorted(moved.items()):
                add_to_archive(Path(archive_dir) / (month + ".md"), blk)
            open_after = sum(1 for ln in kept if ln.startswith("- [ ] "))
            if open_after != open_before:
                raise RuntimeError("open lines changed (%d -> %d): refusing to rewrite the inbox" % (open_before, open_after))
            fd, tmp = tempfile.mkstemp(prefix=".hub-inbox.", dir=str(inbox.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write("".join(kept))
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, inbox.stat().st_mode & 0o777)
                os.replace(tmp, inbox)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            return n, open_before
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--inbox", default=os.environ.get("HUB_INBOX") or os.environ.get("MUSE_INBOX") or str(HOME / ".claude/state/hub-inbox.md"))
    ap.add_argument("--archive-dir", default=None)
    ap.add_argument("--today", help="YYYY-MM-DD (tests)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()
    adir = Path(a.archive_dir) if a.archive_dir else Path(os.path.realpath(a.inbox)).with_name("hub-inbox-archive")
    n, opened = run(a.inbox, adir, today, a.days, a.dry_run)
    print("%s %d done block(s) older than %d days; %d open line(s) untouched" % ("would archive" if a.dry_run else "archived", n, a.days, opened))
    return 0


if __name__ == "__main__":
    sys.exit(main())
