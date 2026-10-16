#!/usr/bin/env bash
# openclaw_ask_poll.sh: carry out what Jonathan typed into OpenClaw's dashboard. Shell only, never a model.
#
# The hub-ask hook inside OpenClaw copies each dashboard message into the drop folder; this takes the items
# out (as data) and runs the fixed action table on tray items (openclaw_pickup.py --act). It prints nothing
# when the folder is empty and appends one block to openclaw-ask-poll.log when something happened.
#
# Scheduling: this distro has no systemd (PID 1 is init), so the poll is a Windows Task Scheduler task, like
# \AFA\OpenClawStatus: \AFA\OpenClawAsk runs every minute
#   conhost.exe --headless wsl.exe -d Debian -- bash -lc ~/.claude/scripts/openclaw_ask_poll.sh
# Remove: schtasks.exe /Delete /TN '\AFA\OpenClawAsk' /F
set -u
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
state="$HOME/.claude/state"
log="$state/openclaw-ask-poll.log"
mkdir -p "$state"
exec 9>"$state/openclaw-ask.lock"
flock -n 9 || exit 0
[ -d /mnt/wsl/ocstage ] || exit 0

out=$(timeout 300 python3 "$here/openclaw_pickup.py" --consume --act 2>&1)
rc=$?
case "$out" in
  "pickup: folder empty") [ "$rc" = 0 ] && exit 0 ;;
esac
{ date '+%F %T'; printf '%s\n' "$out"; echo "exit $rc"; } >> "$log"
# keep the log small
if [ "$(wc -l < "$log")" -gt 2000 ]; then tail -n 1000 "$log" > "$log.new" && mv "$log.new" "$log"; fi
exit "$rc"
