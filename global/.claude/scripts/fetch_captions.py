#!/usr/bin/env python3
"""Fetch the YouTube captions of one video from the transcript server and write them into a call folder as transcript.md, transcript.srt and transcript.json.

Usage: fetch_captions.py probe | info --video ID_OR_URL | fetch --video ID_OR_URL --dir FOLDER [--date YYYY-MM-DD] [--label TEXT] [--tolerance 180] [--force] [--refetch]
Use when: a streamed co-op call has a YouTube copy and its captions must land in the call folder without any caption text reaching a terminal or a vendor model (coop_call_transcript.py runs this as step one).

Rules:
  - Captions come only from the transcript server: the Docker image mcp/youtube-transcript, spoken to over stdin/stdout through
    ~/.claude/scripts/docker-heavy.sh --light. No yt-dlp, no browser scraper.
  - It prints counts and timestamps only, never caption text, never the video title or description.
  - A get_video_info call with no reply exits 2 and writes nothing.
  - Readiness gate: the video length is read first (get_video_info). If the last caption ends more than --tolerance seconds before the
    end of the video, nothing is written, one NOT-READY line is printed and the exit code is 4. --force skips the comparison.
    An unreadable length prints "gate skipped" and carries on.
  - Files are mode 600, the folder is mode 700. An existing transcript.srt, .md or .json is never overwritten unless --refetch is given.
  - Call text goes only to a model we run.
  - FETCH_CAPTIONS_SERVER_CMD (a shell-split command line that replaces the Docker command) is a test hook only.

Exit codes: 0 ok or skipped, 1 bad arguments, 2 no reply, 3 bad reply, 4 not ready.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

HEAVY = os.path.expanduser("~/.claude/scripts/docker-heavy.sh")
DEFAULT_CMD = [HEAVY, "--light", "--timeout", "240", "--tag", "coop-yt-transcript", "--",
               "docker", "run", "-i", "--rm", "mcp/youtube-transcript"]
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def server_cmd():
    env = os.environ.get("FETCH_CAPTIONS_SERVER_CMD")
    return shlex.split(env) if env else list(DEFAULT_CMD)


def session(calls):
    """calls: JSON-RPC messages sent after initialize. Returns (dict id -> reply, return code, stderr bytes)."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "lane", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ] + calls
    payload = "".join(json.dumps(m) + "\n" for m in msgs)
    try:
        p = subprocess.run(server_cmd(), input=payload.encode(), capture_output=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return {}, -1, 0
    out = {}
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and "id" in r:
            out[r["id"]] = r
    return out, p.returncode, len(p.stderr)


def call_tool(name, args):
    """One tools/call. Returns (structuredContent dict or None, reply seen, return code)."""
    out, rc, _ = session([{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": name, "arguments": args}}])
    r = out.get(2)
    if not r or not isinstance(r.get("result"), dict):
        return None, False, rc
    res = r["result"]
    sc = res.get("structuredContent")
    if res.get("isError") or not isinstance(sc, dict):
        return None, True, rc
    return sc, True, rc


def video_id(text):
    """A bare 11-character id, or a youtube.com/watch?v= or youtu.be/ address. None when it is neither."""
    text = (text or "").strip()
    if ID_RE.match(text):
        return text
    m = re.search(r"(?:youtube\.com/watch\?(?:[^#\s]*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})", text)
    return m.group(1) if m else None


def parse_duration(text):
    """Seconds from "19 seconds", "1 hour, 2 minutes, 3 seconds", "2 hours", "1:02:03" or "12:30"; None when unreadable."""
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if not t:
        return None
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", t)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    units = {"hour": 3600, "minute": 60, "second": 1}
    rest = t
    total = 0
    found = False
    pat = re.compile(r"^\s*(\d+)\s*(hour|minute|second)s?\s*(?:,|and)?\s*")
    while rest.strip():
        m = pat.match(rest)
        if not m:
            return None
        total += int(m.group(1)) * units[m.group(2)]
        found = True
        rest = rest[m.end():]
    return total if found else None


def ts(x, ms=False):
    secs, milli = divmod(round(float(x) * 1000), 1000)
    h, r = divmod(secs, 3600)
    m, sec = divmod(r, 60)
    base = "%02d:%02d:%02d" % (h, m, sec)
    return base + (",%03d" % milli if ms else "")


def video_length(vid):
    """(seconds or None, exit code). No reply at all is exit 2; a reply with unreadable text is (None, 0)."""
    sc, seen, rc = call_tool("get_video_info", {"url": "https://www.youtube.com/watch?v=" + vid})
    if not seen:
        print("no reply from get_video_info, rc", rc)
        return None, 2
    if not sc:
        return None, 0
    return parse_duration(sc.get("duration")), 0


def get_segments(vid):
    """All caption segments over every page. Returns (segments, exit code); prints page lines."""
    segs = []
    cursor = None
    for page in range(60):
        args = {"url": "https://www.youtube.com/watch?v=" + vid, "lang": "en"}
        if cursor:
            args["next_cursor"] = cursor
        sc, seen, rc = call_tool("get_timed_transcript", args)
        if not seen:
            print("no reply, rc", rc)
            return None, 2
        if sc is None:
            print("page", page, "bad reply")
            return None, 3
        got = None
        for v in sc.values():
            if isinstance(v, list):
                got = v
        if got is None:
            print("page", page, "no list; keys", sorted(sc))
            return None, 3
        cursor = sc.get("next_cursor")
        segs.extend(g for g in got if isinstance(g, dict))
        print("page", page, "items", len(got), "more", bool(cursor))
        if not cursor:
            break
    if cursor:
        print("page cap reached")
        return None, 3
    if not segs:
        print("no segments")
        return None, 3
    return segs, 0


def normalise(segs):
    """Cues with start, text and end. Same millisecond detection and end rule as the first helper."""
    norm = []
    for s in segs:
        start = s.get("start", s.get("offset", 0))
        norm.append({"start": float(start), "text": (s.get("text") or "").replace("\n", " ").strip(),
                     "dur": s.get("duration")})
    norm.sort(key=lambda s: s["start"])
    if norm[-1]["start"] > 20000:
        for s in norm:
            s["start"] /= 1000.0
            if s["dur"] is not None:
                s["dur"] = float(s["dur"]) / 1000.0
    for i, s in enumerate(norm):
        if s["dur"] is not None:
            s["end"] = s["start"] + float(s["dur"])
        else:
            s["end"] = norm[i + 1]["start"] if i + 1 < len(norm) else s["start"] + 3.0
        if i + 1 < len(norm) and s["end"] > norm[i + 1]["start"]:
            s["end"] = norm[i + 1]["start"]
    return norm


def build_files(norm, vid, date, label, length):
    words = sum(len(s["text"].split()) for s in norm)
    src = "YouTube auto-generated English captions (transcript server), https://youtu.be/%s" % vid
    if label:
        src += ", " + label
    lines = ["# Transcript", "", "- Source: " + src]
    if date:
        lines.append("- Date: " + date)
    lines += [
        "- Duration: %s; speech %s to %s" % (ts(length) if length is not None else "unknown",
                                           ts(norm[0]["start"]), ts(norm[-1]["start"])),
        "- Speakers: not labelled",
        "- Not corrected: auto-captions mishear names and numbers",
        "- Quality: %d words in %d caption segments" % (words, len(norm)),
        "- Sensitive: call data. Local model only. Keep this file out of any repo, issue or email.",
        "", ""]
    hdr = "\n".join(lines)
    return {
        "transcript.md": hdr + "\n".join("[%s] %s" % (ts(s["start"]), s["text"]) for s in norm) + "\n",
        "transcript.srt": "\n".join("%d\n%s --> %s\n%s\n" % (i + 1, ts(s["start"], True), ts(s["end"], True), s["text"])
                                    for i, s in enumerate(norm)),
        "transcript.json": json.dumps([{"start": s["start"], "text": s["text"], "end": s["end"]} for s in norm], indent=1),
    }, words


def video_id_from_folder(folder):
    """The id from the "https://youtu.be/ID" line of FOLDER/transcript.md, or None. Reads only the header lines."""
    try:
        with open(os.path.join(folder, "transcript.md"), encoding="utf-8", errors="replace") as f:
            for _ in range(12):
                line = f.readline()
                if not line:
                    break
                if line.startswith("- Source:"):
                    m = re.search(r"https://youtu\.be/([A-Za-z0-9_-]{11})", line)
                    return m.group(1) if m else None
    except OSError:
        return None
    return None


def gate(norm, length, tolerance, force):
    """Returns (ready, line). A line is printed in every case but a plain pass."""
    if length is None:
        return True, "gate skipped: video length unreadable"
    last_end = norm[-1]["end"]
    if force or last_end >= length - tolerance:
        return True, None
    return False, ("NOT-READY last_caption %s video_length %s short_by %ds "
                   "(check the Studio or Twitch page, then rerun with --force)"
                   % (ts(last_end), ts(length), int(length - last_end)))


def write_files(folder, files):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for name, body in files.items():
        path = os.path.join(folder, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, 0o600)
        print(name, os.path.getsize(path), "bytes")


def cmd_probe(_a):
    out, rc, _ = session([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    tools = (out.get(2) or {}).get("result", {}).get("tools")
    if tools is None:
        print("no reply, rc", rc)
        return 2
    for t in tools:
        print(t.get("name"))
    return 0


def cmd_info(a):
    vid = video_id(a.video)
    if not vid:
        print("bad video id")
        return 1
    n, code = video_length(vid)
    if code:
        return code
    print("duration_seconds %d" % n if n is not None else "duration unreadable")
    return 0


def cmd_fetch(a):
    vid = video_id(a.video)
    if not vid:
        print("bad video id")
        return 1
    if not a.refetch:
        for name in ("transcript.srt", "transcript.md", "transcript.json"):
            path = os.path.join(a.dir, name)
            if os.path.exists(path):
                n = 0
                if name == "transcript.srt":
                    with open(path, encoding="utf-8", errors="replace") as f:
                        n = sum(1 for line in f if "-->" in line)
                print("fetch skipped: %s exists%s" % (name, ", %d cues" % n if name == "transcript.srt" else ""))
                return 0
    length, code = video_length(vid)
    if code:
        return code
    segs, code = get_segments(vid)
    if code:
        return code
    norm = normalise(segs)
    ready, line = gate(norm, length, a.tolerance, a.force)
    if line:
        print(line)
    if not ready:
        return 4
    files, words = build_files(norm, vid, a.date, a.label, length)
    write_files(a.dir, files)
    print("cues", len(norm), "words", words, "first", ts(norm[0]["start"]), "last", ts(norm[-1]["start"]),
          "last_end", ts(norm[-1]["end"]), "video_length", ts(length) if length is not None else "unknown")
    return 0


def parser():
    p = argparse.ArgumentParser(description="Fetch YouTube captions from the transcript server (counts only).")
    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("probe")
    i = sub.add_parser("info")
    i.add_argument("--video", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--video", required=True)
    f.add_argument("--dir", required=True)
    f.add_argument("--date")
    f.add_argument("--label")
    f.add_argument("--tolerance", type=int, default=180)
    f.add_argument("--force", action="store_true")
    f.add_argument("--refetch", action="store_true")
    return p


def main(argv=None):
    try:
        a = parser().parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 1
    return {"probe": cmd_probe, "info": cmd_info, "fetch": cmd_fetch}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
