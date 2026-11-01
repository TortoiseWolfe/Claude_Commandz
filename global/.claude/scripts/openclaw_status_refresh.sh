#!/usr/bin/env bash
# Rebuild the OpenClaw status digest and put it where OpenClaw loads it at session start:
# a marked block at the top of MEMORY.md in its main workspace (OpenClaw injects MEMORY.md into every
# main session, 20,000 chars per file). Data flows hub -> OpenClaw: the remote side prints one
# "ok <bytes>" line, and the only other thing read back is the receptionist's model chain (the digest's client guard).
#
#   openclaw_status_refresh.sh            build and deliver (what the scheduled task and lane_watch.sh call)
#   openclaw_status_refresh.sh --build    build only, print the digest path
#
# On any machine other than NX-01 only the keeper steps run (bridge probe, lane recycle, inbox archive, hub digest); see "One machine switch".
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

# First, the WSL-to-Windows bridge: every wsl.exe call below needs it. The watchdog checks /proc/buddyinfo for free 512 KB blocks (what each
# Windows command's Hyper-V channel allocates), heals when they run out, and runs the 10 s cmd.exe probe. It is silent when all is well.
# 19 Oct: the bridge went quiet at 5:21 PM and 8:15 PM; see wsl_bridge_watchdog.py.
timeout 200 python3 "$here/wsl_bridge_watchdog.py" --probe >/dev/null 2>&1 || echo "openclaw_status_refresh: wsl bridge watchdog reports a problem (see ~/.claude/state/wsl-bridge/heal.log)" >&2

# Recovery record and restarts (fleet-up.sh): when the heartbeat shows the machine was down, write the record and bring back the lanes and long
# jobs. Idempotent and silent when nothing happened; it reads the machine name itself, so it runs on both machines before the machine switch.
[ -x "$here/fleet-up.sh" ] && { timeout 150 "$here/fleet-up.sh" >/dev/null 2>&1 || echo "openclaw_status_refresh: fleet-up reports a problem" >&2; }

# Recycle any lane past its own line, so recycling never depends on the hub having a watcher armed (26 Oct: between two hub sessions
# nothing was armed and scout-run-2 went 128K to 163K and compacted on its own). The same decision as lane_watch.sh's poll loop, the same
# thrash and queued-input guards, silent: it wakes nobody. The recycle itself runs detached (it takes minutes).
timeout 120 "$here/lane_watch.sh" --recycle-once >/dev/null 2>&1 || echo "openclaw_status_refresh: lane recycle pass reports a problem" >&2

# Done lines older than 7 days leave the hub's inbox for hub-inbox-archive/YYYY-MM.md (no model; takes the inbox lock; open lines are never touched).
timeout 30 python3 "$here/hub_inbox_archive.py" >/dev/null 2>&1 || echo "openclaw_status_refresh: inbox archive failed" >&2

# One machine switch (28 Oct 2026, fleet review finding 3): NX-01 owns the OpenClaw gateway, the Gmail sweeps, the CI runners and the hours
# jobs, so it runs everything below. Any other machine (the tower, NCC-74656) is a keeper only: the steps above, plus its own hub digest, and
# stops here. The name comes from ~/.config/afa/machine; a missing file counts as another machine, because doing less is the safe mistake.
build_digest() { timeout 60 python3 "$here/hub_digest.py" "$@" --out "$state/hub-digest.md" || echo "openclaw_status_refresh: hub-digest build failed" >&2; }
machine=$(cat "$HOME/.config/afa/machine" 2>/dev/null || echo unknown)
if [ "$machine" != NX-01 ]; then
  # Merged fixes reach this machine unasked, the way NX-01 gets them from its own folder (tower_claude_refresh.sh runs at most hourly).
  timeout 600 "$here/tower_claude_refresh.sh" >/dev/null 2>&1 || echo "openclaw_status_refresh: tower refresh reports a problem (see ~/.claude/state/tower-refresh.log)" >&2
  build_digest
  exit 0
fi

# Harmonic Egg CI runs on this machine while its repo variable RUNNER_LABEL=workstation (harmonic-egg#351): start the runner if WSL, Docker
# or sleep took it down. A no-op otherwise. See he_runner_watch.sh.
timeout 120 "$here/he_runner_watch.sh" >/dev/null 2>&1 || echo "openclaw_status_refresh: he runner watch reports a problem (see ~/.claude/state/he-runner/)" >&2


# Handled fleet-note drafts leave the owner's Drafts folder (26 Oct: the real drafts were buried under Hatch and tower notes). notes_check.py --sweep
# --apply deletes only drafts with a fleet tag in the subject once they are handled (rules at the top of its sweep section), saving each one's raw
# text to ~/.claude/state/draft-sweep/ first. No model. Gmail errors leave the folder as it was.
timeout 150 python3 "$here/notes_check.py" --sweep --apply >/dev/null 2>&1 || echo "openclaw_status_refresh: draft sweep failed (see ~/.claude/state/notes-check.log)" >&2

# Every run: pull the calendar events that belong to a client into hours/calendar-events.tsv (hours.py calendar; read-only, its own
# calendar-token.json, so the Gmail token is untouched). Until 26 Oct that file was a one-time copy and never refreshed. A failure
# keeps the old file and says so in ~/.claude/state/hours-calendar.txt. Detached: it never holds this task.
( timeout 120 python3 "$here/hours.py" calendar </dev/null >/dev/null 2>&1 & )

# Monday morning, once: last week's co-op and internship hours (hours.py week --if-due; Monday after 6:00 AM Eastern, one run a day, its own
# lock, ~1 minute, so it runs detached and never holds this task). It writes ~/.claude/state/hours-week.txt, which the hub digest shows.
( timeout 1500 python3 "$here/hours.py" week --if-due </dev/null >/dev/null 2>&1 & )

# The hub knowledge graph (~/repos/graphify-out, private, untracked): restage the docs it is built from and, if any changed, re-extract only
# those files with one headless Sonnet pass, at most once a day (an unchanged corpus costs about a second and no model call). Detached, own lock.
[ -x "$HOME/repos/hub/scripts/hub-graph-refresh.sh" ] && ( timeout 1500 "$HOME/repos/hub/scripts/hub-graph-refresh.sh" </dev/null >/dev/null 2>&1 & )

# The hub's own status page (private, read by /prep): built here so it rides the same 15-minute task and
# lane_watch.sh trigger. It is a different file from the OpenClaw digest and is never delivered anywhere.
# A live state that no longer parses (the roadmap text changed shape) makes the digest builder exit 3 with the reason on stderr (kept, not
# discarded) and put an alert on the hub's list, which wakes the hub; the previous page stays.
# --publish-budget also sets the FLEET_BUDGET variable on the hub repo (one line, numbers and times; the tower reads it with
# gh variable get FLEET_BUDGET -R TortoiseWolfe/workspace because it can't read this disk). A failed publish never fails the digest.
build_digest --publish-budget

# Every lane's new or changed report.md goes into his chat here, so a result reaches him with no hub session open (this task is
# the safety net; lane_watch.sh does the same the moment a report lands). One post per report version; the first run only records.
timeout 120 python3 "$here/openclaw_dispatch.py" post-reports 2>/dev/null || echo "openclaw_status_refresh: post-reports failed" >&2

# Every message of Jonathan's must have reached a lane or the hub's list (19 Oct: six replies bounced and nobody noticed). Any that
# did not are put on the hub's list now, which wakes the hub. lane_watch.sh runs the same check on every poll.
timeout 60 python3 "$here/openclaw_reconcile.py" --file --quiet >/dev/null 2>&1 || echo "openclaw_status_refresh: reconcile failed" >&2

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
