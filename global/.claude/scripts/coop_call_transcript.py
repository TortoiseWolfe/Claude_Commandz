#!/usr/bin/env python3
"""Turn one streamed co-op call into absorbed notes with one command: fetch the captions, clean them, absorb them.

Usage: coop_call_transcript.py --dir CALL_FOLDER [--video ID_OR_URL] [--label TEXT] [--date YYYY-MM-DD] [--force] [--refetch] [--reclean] [--timeout 600] [--tolerance 180]
Use when: turn one streamed co-op call into absorbed notes: fetch the captions, clean them, absorb them, counts only, one timing line.

Steps, each timed: fetch (fetch_captions.py fetch), clean (coop_call_actions.py --clean; skipped while transcript-clean.md is not older
than transcript.srt, unless --reclean), absorb (coop_call_actions.py). Without --video the id is read from the youtu.be line of the
folder's transcript.md header. A NOT-READY fetch stops the chain with exit 4; any other failure prints "BLOCKED <step> exit N".

Output is counts only: from each sibling tool only lines that start with a known prefix (DONE, BLOCKED, compare, dropped, model, page,
transcript., cues, fetch skipped, clean skipped, NOT-READY, gate skipped) are kept, cut to 250 characters, so only lines
that start with one of those words are printed. The wrapper opens no call file except that one header line, and the call text goes only to a model we run.
Sibling tools sit next to this file; COOP_CALL_ACTIONS and FETCH_CAPTIONS override their paths (test only).
Standard library only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_captions as fc  # noqa: E402

KEEP = ("DONE", "BLOCKED", "compare", "dropped", "model", "page", "transcript.", "cues", "fetch skipped",
        "clean skipped", "NOT-READY", "gate skipped")


def tool(env, name):
    return os.environ.get(env) or str(HERE / name)


def run(cmd, limit):
    """Runs one sibling tool. Prints only whitelisted lines. Returns (exit code, kept lines)."""
    try:
        p = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, errors="replace", timeout=limit)
        text, code = p.stdout + "\n" + p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        text, code = "BLOCKED timed out", 124
    kept = [ln[:250] for ln in text.splitlines() if ln.startswith(KEEP)]
    for ln in kept:
        print(ln)
    return code, kept


def fmt(sec):
    sec = int(round(sec))
    return "%dm %ds" % (sec // 60, sec % 60)


def stop(step, code):
    print("BLOCKED %s exit %d" % (step, code))
    return code or 1


def step_fetch(a, fetcher, limit):
    vid = a.video or fc.video_id_from_folder(a.dir)
    if not vid:
        print("BLOCKED fetch: no video id")
        return 1
    cmd = [fetcher, "fetch", "--video", vid, "--dir", a.dir, "--tolerance", str(a.tolerance)]
    for flag, val in (("--date", a.date), ("--label", a.label)):
        if val:
            cmd += [flag, val]
    cmd += [f for f, on in (("--force", a.force), ("--refetch", a.refetch)) if on]
    code, _ = run(cmd, limit)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch, clean and absorb one co-op call (counts only).")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--video")
    ap.add_argument("--label")
    ap.add_argument("--date")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--reclean", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--tolerance", type=int, default=180)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 1
    a.dir = os.path.expanduser(a.dir)
    actions = tool("COOP_CALL_ACTIONS", "coop_call_actions.py")
    fetcher = tool("FETCH_CAPTIONS", "fetch_captions.py")
    times = {"fetch": 0.0, "clean": 0.0, "absorb": 0.0}
    t0 = time.time()
    limit = a.timeout * 6 + 600

    print("## fetch")
    t = time.time()
    code = step_fetch(a, fetcher, limit)
    times["fetch"] = time.time() - t
    if code == 4:
        return 4
    if code:
        return stop("fetch", code)

    print("## clean")
    clean, srt = Path(a.dir) / "transcript-clean.md", Path(a.dir) / "transcript.srt"
    if not a.reclean and clean.exists() and srt.exists() and clean.stat().st_mtime >= srt.stat().st_mtime:
        print("clean skipped: transcript-clean.md is current")
    else:
        t = time.time()
        code, kept = run([actions, "--dir", a.dir, "--clean", "--timeout", str(a.timeout)], limit)
        times["clean"] = time.time() - t
        if code or any(k.startswith("BLOCKED") for k in kept):
            return stop("clean", code)

    print("## absorb")
    t = time.time()
    code, kept = run([actions, "--dir", a.dir, "--timeout", str(a.timeout)], limit)
    times["absorb"] = time.time() - t
    if code or any(k.startswith("BLOCKED") for k in kept):
        return stop("absorb", code)

    print("TIMING fetch %s clean %s absorb %s total %s" % (
        fmt(times["fetch"]), fmt(times["clean"]), fmt(times["absorb"]), fmt(time.time() - t0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
