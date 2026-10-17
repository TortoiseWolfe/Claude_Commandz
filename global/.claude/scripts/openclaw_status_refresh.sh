#!/usr/bin/env bash
# Rebuild the OpenClaw status digest and put it where OpenClaw loads it at session start:
# a marked block at the top of MEMORY.md in its main workspace (OpenClaw injects MEMORY.md into every
# main session, 20,000 chars per file). Data flows hub -> OpenClaw: the remote side prints one
# "ok <bytes>" line, and the only other thing read back is the receptionist's model chain (the digest's client guard).
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

# The receptionist's live model chain (primary, then fallbacks), model names and nothing else: client repos get PR titles in the
# digest only when every one is a claude/ model, since a fallback reads the same digest. The digest checks the shape and treats
# anything else (empty, an error, another provider, a line cut short) as not Claude: counts only.
model=$(timeout 30 wsl.exe -d OpenClawGateway -u openclaw -e python3 -c '
import json
c = json.load(open("/home/openclaw/.openclaw/openclaw.json"))
a = c.get("agents") or {}
d = (a.get("defaults") or {}).get("model")
m = next((x.get("model") for x in a.get("list") or [] if isinstance(x, dict) and x.get("id") == "main" and x.get("model")), None) or d
chain = [m.get("primary", "") if isinstance(m, dict) else m or ""]
fb = m.get("fallbacks") if isinstance(m, dict) and "fallbacks" in m else (d or {}).get("fallbacks") if isinstance(d, dict) else None
chain += [f if isinstance(f, str) else "?" for f in fb or []]  # anything odd fails the digest check
print(",".join(chain))
' 2>/dev/null | tr -d '\r\0' | head -c 4000 | head -1)
[ "${#model}" -le 1000 ] || model=""  # never cut a chain short: a dropped fallback would read as all-Claude

python3 "$here/openclaw_status_digest.py" --receptionist-model "$model" --out "$out.new" || { rm -f "$out.new"; exit 1; }
first=$(head -1 "$out.new"); last=$(tail -1 "$out.new"); size=$(wc -c < "$out.new")
if [ "$first" != "<!-- openclaw-status:begin -->" ] || [ "$last" != "<!-- openclaw-status:end -->" ] || [ "$size" -gt 12000 ]; then
  echo "openclaw_status_refresh: digest failed its shape check (size $size); not delivering" >&2
  rm -f "$out.new"; exit 1
fi
mv "$out.new" "$out"
[ "${1:-}" = "--build" ] && { echo "$out"; exit 0; }

res=$({
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
} | timeout 60 wsl.exe -d OpenClawGateway -u openclaw -- bash -s 2>&1 | tr -d '\r\0' | tail -1)

# After the status block is in, keep the receptionist's hand-off block current while its chat is quiet. The 4 AM
# daily reset only runs when the next message arrives, hours after the last turn, so this is what the new chat
# starts from. handoff.js makes no model call unless the chat changed and has been quiet for 2 minutes. It runs
# detached (the pickup caller waits 90 s on this script) and under this script's lock, so runs never overlap.
node=/home/openclaw/.openclaw/tools/node-v24.19.0/bin/node
hook=/home/openclaw/.openclaw/hooks/session-handoff/handoff.js
( timeout 150 wsl.exe -d OpenClawGateway -u openclaw -e "$node" "$hook" --agent main --session-key agent:main:main --reason sweep \
    2>&1 | tr -d '\r\0' | tail -1 > "$state/openclaw-handoff-sweep.log" ) </dev/null >/dev/null 2>&1 &
echo "$res"
