#!/usr/bin/env python3
"""The record that the meeting_transcribe smoke test passed on exactly this code.

The smoke test (smoke_meeting_transcribe.py) is heavy Docker and GPU work, so the default suite does not run it, and a skipped test
is not a passing one. Instead run_meeting_smoke.sh runs it and writes meeting_smoke.stamp: a hash of meeting_transcribe/ plus the
smoke test file, with the date and the git SHA. test_meeting_smoke_stamp.py recomputes the hash in every suite run and fails,
naming the command, when the code no longer matches the stamp.

    meeting_smoke_stamp.py check     exit 0 if the stamp matches the code, else print why and exit 1
    meeting_smoke_stamp.py write     record the current hash (run_meeting_smoke.sh does this, after a pass)
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STAMP = os.path.join(HERE, "meeting_smoke.stamp")
COMMAND = "global/.claude/scripts/tests/run_meeting_smoke.sh"
SMOKE_FILE = os.path.join("tests", "smoke_meeting_transcribe.py")


def covered(scripts):
    """Relative paths (under the scripts folder) of every file the stamp covers, sorted."""
    out = [SMOKE_FILE]
    base = os.path.join(scripts, "meeting_transcribe")
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        out += [os.path.relpath(os.path.join(dirpath, f), scripts) for f in files if not f.endswith(".pyc")]
    return sorted(out)


def digest(scripts=os.path.dirname(HERE)):
    h = hashlib.sha256()
    for rel in covered(scripts):
        with open(os.path.join(scripts, rel), "rb") as f:
            data = f.read()
        h.update(rel.encode() + b"\0" + str(len(data)).encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def read():
    try:
        with open(STAMP) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def problem(scripts=os.path.dirname(HERE), stamp=None):
    """None when the stamp matches the code; otherwise the sentence the suite fails with."""
    stamp = stamp if stamp is not None else read()
    if not stamp:
        return "no meeting_transcribe smoke stamp yet. Run: %s" % COMMAND
    if stamp.get("hash") != digest(scripts):
        return ("meeting_transcribe (or its smoke test) changed since the smoke test last passed (%s, %s). Run: %s"
                % (stamp.get("date", "?"), (stamp.get("sha") or "?")[:7], COMMAND))
    return None


def write():
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    stamp = {"hash": digest(), "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "sha": sha,
             "note": "sha is the commit the smoke ran on top of; the hash is what the guard checks"}
    with open(STAMP, "w") as f:
        json.dump(stamp, f, indent=1)
        f.write("\n")
    return stamp


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "write":
        print(json.dumps(write()))
    elif mode == "check":
        p = problem()
        print(p or "meeting_transcribe smoke stamp matches")
        sys.exit(1 if p else 0)
    else:
        sys.exit("usage: meeting_smoke_stamp.py check|write")
