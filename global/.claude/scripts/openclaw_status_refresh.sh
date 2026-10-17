#!/usr/bin/env bash
# Rebuild the OpenClaw status digest and put it where OpenClaw loads it at session start:
# a marked block at the top of MEMORY.md in its main workspace (OpenClaw injects MEMORY.md into every
# main session, 20,000 chars per file). Data flows hub -> OpenClaw only: the remote side prints one
# "ok <bytes>" line and nothing is read back.
#
#   openclaw_status_refresh.sh            build and deliver (what the scheduled task and lane_watch.sh call)
#   openclaw_status_refresh.sh --build    build only, print the digest path
#
# Scheduling: this distro has no systemd (PID 1 is init), so the 15-minute timer is a Windows Task
# Scheduler task, like \AFA\MuseNotesPoll: \AFA\OpenClawStatus runs
#   conhost.exe --headless wsl.exe -d Debian -- bash -lc ~/.claude/scripts/openclaw_status_refresh.sh
set -u
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
state="$HOME/.claude/state"
out="$state/openclaw-status-digest.md"
mkdir -p "$state"
exec 9>"$state/openclaw-status.lock"
flock -n 9 || exit 0

# The hub's own status page (private, read by /prep): built here so it rides the same 15-minute task and
# lane_watch.sh trigger. It is a different file from the OpenClaw digest and is never delivered anywhere.
timeout 60 python3 "$here/hub_digest.py" --out "$state/hub-digest.md" 2>/dev/null || echo "openclaw_status_refresh: hub-digest build failed" >&2

python3 "$here/openclaw_status_digest.py" --out "$out.new" || { rm -f "$out.new"; exit 1; }
first=$(head -1 "$out.new"); last=$(tail -1 "$out.new"); size=$(wc -c < "$out.new")
if [ "$first" != "<!-- openclaw-status:begin -->" ] || [ "$last" != "<!-- openclaw-status:end -->" ] || [ "$size" -gt 12000 ]; then
  echo "openclaw_status_refresh: digest failed its shape check (size $size); not delivering" >&2
  rm -f "$out.new"; exit 1
fi
mv "$out.new" "$out"
[ "${1:-}" = "--build" ] && { echo "$out"; exit 0; }

{
  cat <<'REMOTE'
set -eu
W=/home/openclaw/.openclaw/workspace
f=$W/MEMORY.md
blk=$(mktemp "$W/.status-block.XXXXXX"); tmp=$(mktemp "$W/.status-new.XXXXXX")
trap 'rm -f "$blk" "$tmp"' EXIT
cat > "$blk" <<'__OCSTATUS_EOF__'
REMOTE
  cat "$out"
  cat <<'REMOTE'
__OCSTATUS_EOF__
[ -f "$f" ] || : > "$f"
{ cat "$blk"; echo; sed '/<!-- openclaw-status:begin -->/,/<!-- openclaw-status:end -->/d' "$f" | sed '/./,$!d'; } > "$tmp"
chmod --reference="$f" "$tmp"
mv "$tmp" "$f"
echo "ok $(wc -c < "$f")"
REMOTE
} | timeout 60 wsl.exe -d OpenClawGateway -u openclaw -- bash -s 2>&1 | tr -d '\r\0' | tail -1
