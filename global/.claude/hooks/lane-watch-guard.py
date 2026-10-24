#!/usr/bin/env python3
"""Stop the hub from going quiet while lanes run with no watcher.

The hub starts work in lanes (windows of the tmux session "lanes") and keeps
~/.claude/scripts/lane_watch.sh running in the background so a lane's report
wakes it. The script exits after each wake and the hub must start it again. On
24 Oct about 11:33 AM the hub read a wake and did not, and a lane's request sat
unseen until the owner asked "looks like you're stuck". When the hub tries to
end a turn while lanes are open and no watcher runs, this Stop hook blocks the
stop and says to re-arm the watcher.
"""
import json
import os
import subprocess
import sys

WATCHER_PATTERN = r"^(/usr/bin/|/bin/)?bash [^ ]*/\.claude/scripts/lane_watch\.sh( |$)"


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


def watcher_running():
    override = os.environ.get("LANE_WATCH_GUARD_RUNNING")
    if override is not None:
        return override == "1"
    try:
        result = subprocess.run(
            ["pgrep", "-f", WATCHER_PATTERN],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode == 0


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
        if not lanes or watcher_running():
            return 0
        reason = (
            f"{len(lanes)} lane(s) open ({', '.join(lanes[:6])}) and no lane_watch.sh "
            "is running, so lane reports will not wake this session. Re-arm it now: "
            "run ~/.claude/scripts/lane_watch.sh with run_in_background, then finish the turn."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
