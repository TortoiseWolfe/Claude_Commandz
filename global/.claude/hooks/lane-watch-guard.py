#!/usr/bin/env python3
"""Stop the hub from going quiet while lanes run with no watcher.

The hub starts work in lanes (windows of the tmux session "lanes") and keeps
~/.claude/scripts/lane_watch.sh running in the background so a lane's report
wakes it. The script exits after each wake and the hub must start it again. On
24 Oct about 11:33 AM the hub read a wake and did not, and a lane's request sat
unseen until the owner asked "looks like you're stuck". When the hub tries to
end a turn while lanes are open and no watcher runs, this Stop hook blocks the
stop and says to re-arm the watcher.

One exemption: once the hub has written its hand-off (a ~/.claude/state/hub-handoff-*.md
changed in the last 30 minutes) and touched ~/.claude/state/hub-handed-off.<session id>,
it may stop, so it stops answering wakes and growing. AFA_STATE_DIR moves the state folder.
A running watcher writes ~/.claude/state/lanes/.lane_watch.pid, which this hook checks before any
process listing, so a watcher started before a /clear (a different session id) still counts.
The marker must carry the id of the session that is stopping (a marker for another session does not count). When a fresh hand-off
file exists but this session's marker does not, the block message names the exact marker path to touch.

A watcher only wakes the hub when it is a child of the hub's own Claude Code process: that process is told when the background job
ends. A watcher started detached (nohup, setsid, a trailing &, another terminal or tmux window) keeps running and keeps its pid file
fresh, but its exit reaches nobody. That happened on 16 Oct (a report unseen for 7 hours), 18 Oct and 11 Nov 2026. So a live watcher
counts only when this hook's own Claude Code process is among the watcher's parents; otherwise the stop is blocked and the message
names the detached watcher's pid. The parent chain is read from /proc/<pid>/stat and the process name from /proc/<pid>/comm, never a
command line. When this hook cannot find its own Claude Code process (no parent named claude or node), the check is skipped and a
live watcher counts as before. LANE_WATCH_GUARD_HUB_PID stands in for that process in tests; set but empty means "not found".
"""
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

WATCHER_PATTERN = r"^(/usr/bin/|/bin/)?bash [^ ]*/\.claude/scripts/lane_watch\.sh( |$)"
RECYCLE_PATTERN = r"^(/usr/bin/|/bin/)?bash [^ ]*/\.claude/scripts/lane_watch\.sh --recycle-once( |$)"
# Claude Code shows as "claude" (the installed program) or "node" (run from its package) in /proc/<pid>/comm.
HUB_NAMES = ("claude", "node")
MAX_PARENTS = 64


def open_lanes():
    override = os.environ.get("LANE_WATCH_GUARD_LANES")
    if override is not None:
        return [name.strip() for name in override.split(",") if name.strip()]
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", "lanes", "-F", "#{window_name}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def state_root():
    state_dir = os.environ.get("AFA_STATE_DIR") or "~/.claude/state"
    return Path(os.path.expanduser(state_dir))


def process_start_time(pid):
    """Field 22 of /proc/<pid>/stat, or None. Never reads the process command line."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            stat = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    rest = stat.rsplit(") ", 1)[-1].split()
    return rest[19] if len(rest) >= 20 else None


def parent_pid(pid):
    """Field 4 of /proc/<pid>/stat, or None. Never reads the process command line."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            stat = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    rest = stat.rsplit(") ", 1)[-1].split()
    return int(rest[1]) if len(rest) >= 2 and rest[1].isdigit() else None


def parents(pid):
    """The pids above pid, nearest first, up to but not including pid 1."""
    chain = []
    seen = {pid}
    while len(chain) < MAX_PARENTS:
        pid = parent_pid(pid)
        if pid is None or pid <= 1 or pid in seen:
            break
        chain.append(pid)
        seen.add(pid)
    return chain


def process_name(pid):
    """/proc/<pid>/comm: the short process name, never the command line."""
    try:
        with open(f"/proc/{pid}/comm", "rb") as fh:
            return fh.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def hub_pids():
    """The Claude Code process(es) this hook runs under, or an empty set when none can be found."""
    override = os.environ.get("LANE_WATCH_GUARD_HUB_PID")
    if override is not None:
        return {int(word) for word in override.split() if word.isdigit()}
    # The nearest one is this session. A farther one is whatever started the session (an editor, a terminal, another session).
    for pid in parents(os.getpid()):
        if process_name(pid) in HUB_NAMES:
            return {pid}
    return set()


def wakes_this_session(pid, hubs):
    """True when the watcher's exit reaches this session. With no hub process found, nothing can be said, so it counts."""
    return not hubs or bool(hubs.intersection(parents(pid)))


def watcher_from_pid_file():
    """The live watcher's pid from the state folder, so /clear cannot hide it; None when there is none."""
    pid_file = state_root() / "lanes" / ".lane_watch.pid"
    try:
        fields = pid_file.read_text(encoding="utf-8").split()
    except OSError:
        return None
    if not fields or not fields[0].isdigit():
        return None
    pid = int(fields[0])
    start = fields[1] if len(fields) > 1 else ""
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    now = process_start_time(pid)
    # An older pid file has no start time; accept the live pid. A recycled pid does not match the stored start time.
    return pid if (not start or not now or start == now) else None


def pids_matching(pattern):
    result = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode > 1:
        raise RuntimeError("pgrep could not list processes")
    return set(result.stdout.split()) if result.returncode == 0 else set()


def watcher_state():
    """(running, detached): running is True when a watcher that can wake this session runs; detached lists the
    pids of live watchers that cannot (sorted), for the block message."""
    hubs = hub_pids()
    detached = set()
    pid = watcher_from_pid_file()
    if pid is not None:
        if wakes_this_session(pid, hubs):
            return True, []
        detached.add(pid)
    override = os.environ.get("LANE_WATCH_GUARD_RUNNING")
    if override is not None:
        return override == "1", sorted(detached)
    try:
        watching = pids_matching(WATCHER_PATTERN)
        if not watching:
            return False, sorted(detached)
        recycled = pids_matching(RECYCLE_PATTERN)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return True, []  # unknown: do not block on a failed listing
    for word in sorted(watching - recycled):
        if not word.isdigit():
            continue
        if wakes_this_session(int(word), hubs):
            return True, []
        detached.add(int(word))
    return False, sorted(detached)


def main():
    try:
        if os.environ.get("AFA_LANE") == "1":
            return 0
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("stop_hook_active"):
            return 0
        hub = os.environ.get("LANE_WATCH_GUARD_HUB") or "~/repos"
        cwd = payload.get("cwd")
        if not isinstance(cwd, str):
            return 0
        if os.path.realpath(cwd) != os.path.realpath(os.path.expanduser(hub)):
            return 0
        lanes = open_lanes()
        if not lanes:
            return 0
        running, detached = watcher_state()
        if running:
            return 0

        # Hand-off exemption: let it stop if a hand-off file was recently modified
        # and the session's handed-off marker exists.
        state_path = state_root()
        session_id = payload.get("session_id")
        marker = None
        if isinstance(session_id, str) and re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
            marker = state_path / f"hub-handed-off.{session_id}"

        if marker is not None and marker.exists():
            handoff_files = glob.glob(str(state_path / "hub-handoff-*.md"))
            now = time.time()
            if any(now - os.path.getmtime(f) < 30 * 60 for f in handoff_files):
                return 0

        if detached:
            pids = " ".join(str(pid) for pid in detached)
            reason = (
                f"{len(lanes)} lane(s) open ({', '.join(lanes[:6])}) and the lane_watch.sh that is running "
                f"(pid {pids}) was started detached from this session (nohup, setsid, a trailing & or another "
                "window), so its exit will not wake this session and lane reports will go unseen. "
                f"Stop it with kill {pids}, then run ~/.claude/scripts/lane_watch.sh with the Bash tool's "
                "run_in_background and nothing else on the line, then finish the turn."
            )
        else:
            reason = (
                f"{len(lanes)} lane(s) open ({', '.join(lanes[:6])}) and no lane_watch.sh "
                "is running, so lane reports will not wake this session. Re-arm it now: "
                "run ~/.claude/scripts/lane_watch.sh with run_in_background, then finish the turn."
            )
        if marker is not None:
            # 6 Nov 2026: the hub wrote its hand-off and touched hub-handed-off.<an id of another session>, so this guard kept blocking.
            # The marker has to carry the id of the session that is stopping; say exactly which one, so it is never a guess.
            fresh = any(
                time.time() - os.path.getmtime(f) < 30 * 60
                for f in glob.glob(str(state_path / "hub-handoff-*.md"))
            )
            if fresh and not marker.exists():
                reason += (
                    " If this session has already handed off (a hand-off file from the last 30 minutes exists), "
                    f"its marker is missing: this session's id is {session_id}, so run "
                    f"touch {marker} instead of re-arming, and finish the turn."
                )
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
