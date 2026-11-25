#!/usr/bin/env python3
"""first_prompt_tokens.py: how many tokens a session held at its first answer (read-only, from the transcripts).
Use when: measuring what a lane or hub window loads before it does any work (C18 before and after numbers).

The first assistant message of a transcript carries the usage of the very first request: input tokens plus the cache
creation and cache read tokens is everything the session had loaded (rules, CLAUDE.md files, command and tool lists).

  first_prompt_tokens.py FILE.jsonl                one session
  first_prompt_tokens.py --folder DIR [--last N] [--since YYYY-MM-DD]
                                                   the newest N (default 20) sessions in a Claude project folder
                                                   (~/.claude/projects/<folder>), with the median and the range
Prints counts only, never message text.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path


def first_prompt_tokens(path):
    """Total input-side tokens of the first assistant message in a transcript, or None when there is none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "assistant":
                    continue
                u = (d.get("message") or {}).get("usage")
                if isinstance(u, dict):
                    return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    except OSError:
        return None
    return None


def folder_numbers(folder, last=20, since=None):
    """[(file name, tokens)] for the newest `last` transcripts in folder (those with an answer), newest first."""
    files = sorted(Path(folder).glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if since:
        cut = datetime.strptime(since, "%Y-%m-%d").timestamp()
        files = [f for f in files if f.stat().st_mtime >= cut]
    out = []
    for f in files:
        if len(out) >= last:
            break
        n = first_prompt_tokens(f)
        if n is not None:
            out.append((f.name, n))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--folder")
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--since")
    a = ap.parse_args(argv)
    if a.folder:
        rows = folder_numbers(a.folder, a.last, a.since)
        if not rows:
            print("no sessions with an answer")
            return 1
        vals = [n for _, n in rows]
        print("sessions %d  median %d  lowest %d  highest %d tokens at the first answer" % (len(vals), statistics.median(vals), min(vals), max(vals)))
        return 0
    if a.file:
        n = first_prompt_tokens(a.file)
        if n is None:
            print("no answer in that file")
            return 1
        print(n)
        return 0
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
